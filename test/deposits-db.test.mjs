import { before, beforeEach, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

// Run real PostgreSQL SQL in an isolated PGlite database, never production.
const moduleUrl = process.env.PGLITE_MODULE ? pathToFileURL(process.env.PGLITE_MODULE).href : '@electric-sql/pglite';
const { PGlite } = await import(moduleUrl);
const db = new PGlite();
let migration;
before(async () => {
  await db.exec(`
    CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS;
    CREATE TABLE public.trips (id int PRIMARY KEY, title text, status text, line_group_id text, creator_id text);
    CREATE TABLE public.expenses (id int PRIMARY KEY, trip_id int REFERENCES trips, payer_name text,
      amount numeric, currency text, participants jsonb, item_name text, tag text);
    CREATE TABLE public.bot_states (user_id text PRIMARY KEY);
    CREATE TABLE public.export_jobs (id text PRIMARY KEY);
    GRANT ALL ON trips,expenses,bot_states,export_jobs TO anon,authenticated;
    GRANT SELECT,UPDATE ON trips,expenses TO service_role;
  `);
  try { migration = await readFile(new URL('../db/2026-09-06-expense-deposits.sql', import.meta.url), 'utf8'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (migration) await db.exec(migration);
});
beforeEach(async () => {
  await db.exec(`RESET ROLE; TRUNCATE trips,expenses CASCADE;
    INSERT INTO trips VALUES (1,'Trip','active','group-1','user-1'),(2,'Other','active','group-2','user-2');
    INSERT INTO expenses VALUES (206,1,'collector',4900,'THB','["Ball","Pak","Min"]','Hotel','#Hotel'),
      (207,2,'other',100,'THB','["Other"]','Other','#Other');`);
});
after(() => db.close());

async function record(overrides = {}) {
  const p = { id:206, group:'group-1', user:'user-1', names:['Ball','Pak'], receiver:'collector', amount:56000, event:'event-1', time:1000, ...overrides };
  try {
    const result = await db.query(`SELECT public.set_expense_deposits($1,$2,$3,$4::text[],$5,$6,$7,$8) AS result`,
      [p.id,p.group,p.user,p.names,p.receiver,p.amount,p.event,p.time]);
    return result.rows[0].result;
  } catch(error) { return { error: error.message, code: error.code }; }
}
async function payments() { return (await db.query('SELECT payer_name,receiver_name,amount_minor FROM expense_deposits ORDER BY payer_name')).rows; }

test('records a batch atomically without changing the bill', async () => {
  const result = await record();
  assert.equal(result.applied_count, 2, JSON.stringify(result));
  assert.deepEqual(await payments(), [
    {payer_name:'Ball',receiver_name:'collector',amount_minor:56000},
    {payer_name:'Pak',receiver_name:'collector',amount_minor:56000},
  ]);
  assert.equal((await db.query('SELECT amount FROM expenses WHERE id=206')).rows[0].amount, '4900');
});
test('duplicate deliveries and older corrections cannot overwrite newer totals', async () => {
  assert.equal((await record()).applied_count,2);
  assert.equal((await record()).applied_count,0);
  assert.equal((await record({names:['Ball'],amount:80000,event:'event-3',time:3000})).applied_count,1);
  assert.equal((await record({names:['Ball'],amount:60000,event:'event-2',time:2000})).applied_count,0);
  assert.equal((await payments())[0].amount_minor,80000);
});
test('corrections preserve other people; zero is a versioned reset', async () => {
  assert.equal((await record()).applied_count,2);
  assert.equal((await record({names:['Ball'],amount:0,event:'event-2',time:2000})).applied_count,1);
  await record();
  assert.deepEqual((await payments()).map(x=>x.amount_minor),[0,56000]);
});
test('independent writers preserve every participant', async () => {
  const results = await Promise.all([
    record({names:['Ball']}), record({names:['Pak'],event:'event-2'}), record({names:['Min'],event:'event-3'}),
  ]);
  assert.deepEqual(results.map(x=>x.applied_count),[1,1,1]);
  assert.equal((await payments()).length,3);
});
test('equal timestamps use event IDs for deterministic order', async () => {
  assert.equal((await record({names:['Ball'],event:'event-B',amount:70000})).applied_count,1);
  assert.equal((await record({names:['Ball'],event:'event-A',amount:10000})).applied_count,0);
  assert.equal((await payments())[0].amount_minor,70000);
});
test('invalid participant rejects the whole batch', async () => {
  assert.equal((await record({names:['Ball','Unknown']})).code,'22023');
  assert.deepEqual(await payments(),[]);
});
test('unknown collector is rejected and self contribution is allowed', async () => {
  assert.equal((await record({receiver:'Unknown'})).code,'22023');
  assert.equal((await record({names:['Ball'],receiver:'Ball'})).applied_count,1);
});
test('another group cannot write, including a creator with a personal trip', async () => {
  assert.equal((await record({group:'group-other'})).code,'42501');
  assert.equal((await record({id:207})).code,'42501');
  assert.deepEqual(await payments(),[]);
});
test('direct conversation must belong to the trip creator', async () => {
  assert.equal((await record({group:null,user:'user-other'})).code,'42501');
  assert.equal((await record({group:null})).applied_count,2);
});
test('closed trip rejects writes', async () => {
  await db.exec("UPDATE trips SET status='closed' WHERE id=1");
  assert.equal((await record()).code,'42501');
});
test('rejects invalid financial and event inputs before writing', async () => {
  for (const p of [{amount:-1},{amount:100000001},{names:[]},{names:['Ball','Ball']},{event:''},{time:0}]) {
    assert.equal((await record(p)).code,'22023',JSON.stringify(p));
  }
  assert.deepEqual(await payments(),[]);
});
test('anon and authenticated cannot read or mutate deposit records or invoke RPC', async () => {
  assert.equal((await record()).applied_count,2);
  for (const role of ['anon','authenticated']) {
    await db.exec(`SET ROLE ${role}`);
    await assert.rejects(db.query('SELECT * FROM expense_deposits'), e=>e.code==='42501');
    await assert.rejects(db.query('UPDATE expense_deposits SET amount_minor=0'), e=>e.code==='42501');
    await assert.rejects(db.query('SELECT public.get_expense_deposits(1,null)'), e=>e.code==='42501');
    assert.equal((await record()).code,'42501');
    await db.exec('RESET ROLE');
  }
  await db.exec('SET ROLE service_role');
  assert.equal((await record({event:'event-2',time:2000})).applied_count,2);
});
test('migration is repeatable without losing deposit data', async () => {
  assert.equal((await record()).applied_count,2);
  await db.exec(migration);
  assert.equal((await payments()).length,2);
});
test('an expense without any valid participant cannot accept arbitrary names', async () => {
  await db.exec("UPDATE expenses SET participants='[]',payer_name=null WHERE id=206");
  assert.equal((await record({names:['Unknown'],receiver:'Unknown'})).code,'22023');
});
test('reads one complete trip snapshot, excluding another trip', async () => {
  assert.equal((await record()).applied_count,2);
  assert.equal((await record({id:207,group:'group-2',user:'user-2',names:['Other'],receiver:'other'})).applied_count,1);
  let result;
  try { result=(await db.query('SELECT public.get_expense_deposits(1,null) AS rows')).rows[0].rows; }
  catch(error) { result={error:error.message}; }
  assert.equal(result.length,2,JSON.stringify(result));
  assert.ok(result.every(row=>row.trip_id===1 && row.expense_id===206));
});
test('public roles cannot forge trip ownership, bot state, or export jobs', async () => {
  for (const role of ['anon','authenticated']) {
    await db.exec(`SET ROLE ${role}`);
    try {
      for (const query of ["UPDATE trips SET creator_id='attacker'", "UPDATE expenses SET amount=1", "INSERT INTO bot_states VALUES ('attacker')", "INSERT INTO export_jobs VALUES ('forged')"]) {
        await assert.rejects(db.query(query), error=>error.code==='42501');
      }
    } finally { await db.exec('RESET ROLE'); }
  }
});
