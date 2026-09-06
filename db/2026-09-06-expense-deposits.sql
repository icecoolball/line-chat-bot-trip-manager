-- Apply before deploying the deposit-aware Worker and Excel exporter.
-- Deposits are transfers/earmarked contributions, never additional expenses.
-- Public clients must not forge the parent context or export dispatch records.
-- Existing bot/export runtimes must use service_role before this is applied.
revoke all on public.trips,public.expenses,public.bot_states,public.export_jobs from public,anon,authenticated;
grant select,insert,update,delete on public.trips,public.expenses,public.bot_states,public.export_jobs to service_role;

create table if not exists public.expense_deposits (
  expense_id integer not null references public.expenses(id) on delete restrict,
  trip_id integer not null references public.trips(id) on delete restrict,
  payer_name text not null check (btrim(payer_name) <> ''),
  receiver_name text not null check (btrim(receiver_name) <> ''),
  amount_minor bigint not null check (amount_minor between 0 and 100000000),
  currency text not null,
  event_id text not null,
  event_time bigint not null check (event_time > 0),
  updated_by text not null,
  updated_at timestamptz not null default now(),
  primary key (expense_id, payer_name)
);
create index if not exists expense_deposits_trip_idx on public.expense_deposits(trip_id);
alter table public.expense_deposits enable row level security;
revoke all on public.expense_deposits from public, anon, authenticated;
grant select, insert, update on public.expense_deposits to service_role;

-- One JSON aggregate is one consistent SQL snapshot and bypasses row-page caps.
create or replace function public.get_expense_deposits(p_trip_id integer, p_expense_id integer default null)
returns jsonb language sql stable security invoker set search_path = '' as $$
  select coalesce(jsonb_agg(to_jsonb(d) order by d.expense_id,d.payer_name),'[]'::jsonb)
  from public.expense_deposits d
  where d.trip_id=p_trip_id and (p_expense_id is null or d.expense_id=p_expense_id);
$$;
revoke all on function public.get_expense_deposits(integer,integer) from public,anon,authenticated;
grant execute on function public.get_expense_deposits(integer,integer) to service_role;

create or replace function public.set_expense_deposits(
  p_expense_id integer, p_group_id text, p_user_id text, p_names text[],
  p_receiver text, p_amount_minor bigint, p_event_id text, p_event_time bigint
) returns jsonb
language plpgsql security invoker set search_path = '' as $$
declare
  exp public.expenses%rowtype;
  names text[];
  person text;
  changed integer;
  applied integer := 0;
begin
  if p_amount_minor is null or p_amount_minor not between 0 and 100000000
     or p_event_time is null or p_event_time <= 0
     or coalesce(btrim(p_event_id), '') = '' or coalesce(btrim(p_user_id), '') = ''
     or coalesce(cardinality(p_names), 0) = 0
     or cardinality(p_names) <> (select count(distinct n) from unnest(p_names) as n)
     or coalesce(btrim(p_receiver), '') = '' then
    raise exception 'ข้อมูลมัดจำไม่ถูกต้อง' using errcode = '22023';
  end if;

  -- Lock both records: trip closure and another deposit writer must serialize.
  select e.* into exp from public.expenses e join public.trips t on t.id = e.trip_id
  where e.id = p_expense_id and t.status = 'active'
    and ((p_group_id is not null and t.line_group_id = p_group_id)
      or (p_group_id is null and t.creator_id = p_user_id))
  for update of e,t;
  if not found then
    raise exception 'ไม่พบรายการในทริปที่เข้าถึงได้ หรือทริปปิดแล้ว' using errcode = '42501';
  end if;

  -- Match the existing participant representation without modifying it.
  if jsonb_typeof(exp.participants) = 'array' then
    select array_agg(distinct btrim(n)) into names
      from jsonb_array_elements_text(exp.participants) n where btrim(n) <> '';
  elsif jsonb_typeof(exp.participants) = 'string' then
    names := regexp_split_to_array(btrim(exp.participants #>> '{}'), '\s+');
  end if;
  if coalesce(cardinality(names),0) = 0 then names := array[exp.payer_name]; end if;
  if names[1] is null or btrim(names[1]) = '' then
    raise exception 'รายการนี้ไม่มีคนหารที่ถูกต้อง' using errcode = '22023';
  end if;
  if not (p_receiver = any(names)) and p_receiver is distinct from exp.payer_name then
    raise exception 'ไม่พบชื่อผู้รับในรายการ' using errcode = '22023';
  end if;
  foreach person in array p_names loop
    if person is null or person <> btrim(person) or not (person = any(names)) then
      raise exception 'ไม่พบชื่อผู้จ่ายในคนหาร: %', person using errcode = '22023';
    end if;
  end loop;

  foreach person in array p_names loop
    insert into public.expense_deposits as d
      (expense_id,trip_id,payer_name,receiver_name,amount_minor,currency,event_id,event_time,updated_by)
    values (exp.id,exp.trip_id,person,p_receiver,p_amount_minor,upper(coalesce(nullif(btrim(exp.currency),''),'THB')),p_event_id,p_event_time,p_user_id)
    on conflict (expense_id,payer_name) do update set
      receiver_name = excluded.receiver_name, amount_minor = excluded.amount_minor,
      currency = excluded.currency, event_id = excluded.event_id, event_time = excluded.event_time,
      updated_by = excluded.updated_by, updated_at = now()
    -- Zero rows retain provenance, so an old delivery cannot resurrect a reset.
    where (d.event_time,d.event_id) < (excluded.event_time,excluded.event_id);
    get diagnostics changed = row_count;
    applied := applied + changed;
  end loop;
  return jsonb_build_object('applied_count',applied,'expense',to_jsonb(exp),'deposits',
    coalesce((select jsonb_agg(to_jsonb(d) order by d.payer_name)
      from public.expense_deposits d where d.expense_id=exp.id),'[]'::jsonb));
end;
$$;
revoke all on function public.set_expense_deposits(integer,text,text,text[],text,bigint,text,bigint) from public,anon,authenticated;
grant execute on function public.set_expense_deposits(integer,text,text,text[],text,bigint,text,bigint) to service_role;

-- Participant replacement is versioned so redelivered LINE commands cannot undo a correction.
alter table public.expenses add column if not exists participants_event_time bigint not null default 0;
alter table public.expenses add column if not exists participants_event_id text not null default '';
create or replace function public.set_expense_participants(
  p_expense_id integer, p_trip_id integer, p_group_id text, p_user_id text,
  p_names text[], p_event_id text, p_event_time bigint
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
  exp public.expenses%rowtype;
begin
  if coalesce(btrim(p_user_id),'') = '' or coalesce(btrim(p_event_id),'') = ''
     or p_event_time is null or p_event_time <= 0
     or coalesce(cardinality(p_names),0) = 0
     or cardinality(p_names) <> (select count(distinct n) from unnest(p_names) n)
     or exists(select 1 from unnest(p_names) n where n is null or n = '' or n <> btrim(n) or n ~ '\s') then
    raise exception 'คนหารหรือข้อมูลคำสั่งไม่ถูกต้อง' using errcode = '22023';
  end if;

  -- Share the expense/trip lock with deposits; closure rejects further edits.
  select e.* into exp from public.expenses e join public.trips t on t.id=e.trip_id
  where e.id=p_expense_id and e.trip_id=p_trip_id and t.status='active'
    and ((p_group_id is not null and t.line_group_id=p_group_id)
      or (p_group_id is null and t.creator_id=p_user_id))
  for update of e,t;
  if not found then
    raise exception 'ไม่พบรายการในทริปที่เข้าถึงได้ หรือทริปปิดแล้ว' using errcode = '42501';
  end if;
  if (exp.participants_event_time,exp.participants_event_id) >= (p_event_time,p_event_id) then
    return jsonb_build_object('applied',false,'expense',to_jsonb(exp));
  end if;

  -- Never orphan a positive contribution or its collector. Zero tombstones stay for replay safety.
  if exists(select 1 from public.expense_deposits d where d.expense_id=exp.id and d.amount_minor>0
    and (not (d.payer_name=any(p_names))
      or (not (d.receiver_name=any(p_names)) and d.receiver_name is distinct from exp.payer_name))) then
    raise exception 'แก้มัดจำของชื่อที่จะเอาออกให้เป็น 0 ก่อน' using errcode = '22023';
  end if;
  update public.expenses set participants=to_jsonb(p_names),participants_event_time=p_event_time,
    participants_event_id=p_event_id where id=exp.id returning * into exp;
  return jsonb_build_object('applied',true,'expense',to_jsonb(exp));
end;
$$;
revoke all on function public.set_expense_participants(integer,integer,text,text,text[],text,bigint) from public,anon,authenticated;
grant execute on function public.set_expense_participants(integer,integer,text,text,text[],text,bigint) to service_role;
