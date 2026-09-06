import { createHmac } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import worker from "../src/worker";

const people = ["บอล","ปาค","มิน","เอ้","ไท","จอม","ใหญ่","ต้น1","ต้น2","ต้น3","ต้น4"];
function setup(options: { withDeposits?: boolean; noTrip?: boolean; otherGroup?: boolean; failRpc?: boolean; fullPage?: boolean; state?:string } = {}) {
  const expense = { id:206,trip_id:1,payer_name:"ผู้รับ",amount:4900,currency:"THB",amount_thb:4900,participants:people,item_name:"ค่าที่พัก",tag:"#ค่าที่พัก",created_at:new Date().toISOString() };
  const trip = { id:1,title:"ทริป",status:"active",creator_id:"user-1",line_group_id:"group-1",base_currency:"THB" };
  let deposits = options.withDeposits ? people.slice(0,7).map(payer_name=>({expense_id:206,trip_id:1,payer_name,receiver_name:"ผู้รับ",amount_minor:56000,currency:"THB",event_time:1000,event_id:"old"})) : [];
  if(options.fullPage) deposits.unshift(...Array.from({length:1000},(_,i)=>({expense_id:1,trip_id:1,payer_name:`person-${i}`,receiver_name:"ผู้รับ",amount_minor:0,currency:"THB",event_time:1000,event_id:"old"})));
  const replies: unknown[] = [];
  const calls: Array<Record<string, unknown>> = [];
  vi.stubGlobal("fetch", vi.fn(async(input: string | URL | Request,init?:RequestInit)=> {
    const url = new URL(String(input));
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    if(url.hostname==="api.line.me") {
      if(url.pathname.endsWith("/reply")) { replies.push(body); return Response.json({}); }
      return Response.json({displayName:"บอล"});
    }
    if(url.pathname.endsWith("/rpc/get_expense_deposits")) return Response.json(deposits.filter(d=>d.trip_id===body.p_trip_id && (body.p_expense_id===null || d.expense_id===body.p_expense_id)));
    if(url.pathname.endsWith("/rpc/set_expense_deposits")) {
      calls.push(body);
      if(options.failRpc) return Response.json({message:"บันทึกไม่สำเร็จ"},{status:503});
      // Database-side locking, validation and replay behavior have separate real SQL tests.
      deposits = deposits.filter(row=>!body.p_names.includes(row.payer_name));
      deposits.push(...body.p_names.map((payer_name:string)=>({expense_id:206,trip_id:1,payer_name,receiver_name:body.p_receiver,amount_minor:body.p_amount_minor,currency:"THB",event_time:body.p_event_time,event_id:body.p_event_id})));
      return Response.json({applied_count:body.p_names.length,expense,deposits});
    }
    if(url.pathname.endsWith("/bot_states")) return Response.json(options.state ? [{user_id:"user-1",group_id:"group-1",action:options.state,payload:{trip_id:1,trips:[trip],target_id:"group-1"}}] : []);
    if(url.pathname.endsWith("/trips")) return Response.json(options.noTrip || (url.searchParams.has("line_group_id") && url.searchParams.get("line_group_id")!=="eq.group-1") ? [] : [trip]);
    if(url.pathname.endsWith("/expenses")) return Response.json(url.searchParams.get("id")==="eq.999" ? [] : [expense]);
    if(url.pathname.endsWith("/expense_deposits")) { const offset=Number(url.searchParams.get("offset") || 0); return Response.json(deposits.slice(offset,offset+1000)); }
    throw new Error(`Unexpected HTTP request: ${url}`);
  }));
  async function send(text:string, provenance=true) {
    const event = {type:"message",replyToken:"reply-1",source:{type:"group",userId:"user-1",groupId:options.otherGroup?"group-other":"group-1"},message:{type:"text",text},...(provenance?{webhookEventId:"01K4FZ00000000000000000001",timestamp:2000}:{})};
    const body=JSON.stringify({events:[event]});
    const res=await worker.fetch(new Request("https://bot.example/callback",{method:"POST",body,headers:{"X-Line-Signature":createHmac("sha256","secret").update(body).digest("base64")}}),{LINE_CHANNEL_SECRET:"secret",LINE_CHANNEL_ACCESS_TOKEN:"token",SUPABASE_URL:"https://db.example",SUPABASE_SERVICE_ROLE_KEY:"service"},{waitUntil:vi.fn()} as unknown as ExecutionContext);
    expect(res.status).toBe(200);
    return JSON.stringify(replies);
  }
  return {send,calls,replies,expense,getDeposits:()=>deposits};
}
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks();});

describe("deposit commands",()=>{
  it("explains cumulative amounts and an explicit collector",async()=>{
    const bot=setup();
    const output=await bot.send("มัดจำ");
    expect(output).toContain("ยอดสะสม");expect(output).toContain("ให้");expect(bot.calls).toEqual([]);
  });
  it("sets the seven contributions without recording another expense",async()=>{
    const bot=setup();
    const output=await bot.send("มัดจำ 0206 560 บอล ปาค มิน เอ้ ไท จอม ใหญ่ ให้ ผู้รับ");
    expect(bot.calls).toEqual([{p_expense_id:206,p_group_id:"group-1",p_user_id:"user-1",p_names:people.slice(0,7),p_receiver:"ผู้รับ",p_amount_minor:56000,p_event_id:"01K4FZ00000000000000000001",p_event_time:2000}]);
    expect(bot.getDeposits()).toHaveLength(7);
    expect(bot.expense.amount).toBe(4900);
    expect(output).toContain("3,920.00");expect(output).toContain("980.00");expect(output).toContain("ต้น4");
  });
  it("shows who has not paid and who overpaid relative to the full equal share",async()=>{
    const output=await setup({withDeposits:true}).send("มัดจำ 0206");
    expect(output).toContain("ยังไม่จ่าย");expect(output).toContain("เกิน");
    expect(output).toContain("114.54");expect(output).toContain("445.45");
  });
  it("accepts an explicit zero correction and English alias",async()=>{
    const bot=setup({withDeposits:true});
    await bot.send("deposit 0206 0 บอล ให้ ผู้รับ");
    expect(bot.calls[0]).toMatchObject({p_amount_minor:0,p_names:["บอล"]});
    expect(bot.getDeposits().find(x=>x.payer_name==="บอล")?.amount_minor).toBe(0);
  });
  it.each(["มัดจำ 0206 -1 บอล ให้ ผู้รับ","มัดจำ 0206 1.001 บอล ให้ ผู้รับ","มัดจำ 0206 560 บอล","มัดจำ 0206 560 ไม่มีชื่อนี้ ให้ ผู้รับ","มัดจำ 0206 560 บอล ให้ คนอื่น","มัดจำ 0206 560 บอล บอล ให้ ผู้รับ"])("rejects invalid input without writing: %s",async text=>{
    const bot=setup(); const output=await bot.send(text); expect(bot.calls).toEqual([]);expect(output).toContain("⚠️");
  });
  it.each([{noTrip:true},{otherGroup:true}])("rejects inaccessible trip %j",async options=>{
    const bot=setup(options);expect(await bot.send("มัดจำ 0206 560 บอล ให้ ผู้รับ")).toContain("ไม่มีทริป");expect(bot.calls).toEqual([]);
  });
  it("rejects an expense outside the current trip",async()=>{
    const bot=setup();expect(await bot.send("มัดจำ 999 560 บอล ให้ ผู้รับ")).toContain("ไม่พบรายการ");expect(bot.calls).toEqual([]);
  });
  it("requires event identity before changing financial metadata",async()=>{
    const bot=setup();expect(await bot.send("มัดจำ 0206 560 บอล ให้ ผู้รับ",false)).toContain("⚠️");expect(bot.calls).toEqual([]);
  });
  it("does not announce success when persistence fails",async()=>{
    vi.spyOn(console,"error").mockImplementation(()=>{});
    const bot=setup({failRpc:true});const output=await bot.send("มัดจำ 0206 560 บอล ให้ ผู้รับ");
    expect(output).toContain("ไม่สำเร็จ");expect(bot.getDeposits()).toEqual([]);
  });
  it("keeps trip cost unchanged but deducts deposits in end-trip settlement",async()=>{
    const output=await setup({withDeposits:true}).send("end trip");
    expect(output).toContain("4,900");expect(output).toContain("980.00");
    expect(output).toContain("หลังหักมัดจำ");
  });
  it("exposes deposit help on the edit card",async()=>{
    expect(await setup().send("edit")).toContain("มัดจำ [ID]");
  });
  it("loads deposits beyond the database's first page before settling",async()=>{
    expect(await setup({withDeposits:true,fullPage:true}).send("end trip")).toContain("980.00");
  });
  it.each(["end trip","excel","history"])("does not expose the creator's other trip in another group: %s",async command=>{
    vi.spyOn(console,"error").mockImplementation(()=>{});
    const bot=setup({otherGroup:true,withDeposits:true});
    const output=await bot.send(command);
    expect(output).not.toContain("980.00");expect(output).not.toContain("Excel: ทริป");
    expect(output).toMatch(/ไม่มีทริป|ยังไม่มีประวัติ/);
  });
  it("does not continue an end-trip confirmation from another group",async()=>{
    const output=await setup({otherGroup:true,withDeposits:true,state:"wait_end_trip_confirm"}).send("ยืนยัน");
    expect(output).not.toContain("980.00");
  });
});

describe("HTTP export boundary",()=>{
  it.each([undefined,"wrong"])("rejects unauthenticated export before reading data: %s",async secret=>{
    const fetch=vi.fn(()=>{throw new Error("No database access expected");});vi.stubGlobal("fetch",fetch);
    const response=await worker.fetch(new Request("https://bot.example/api/export-trip",{method:"POST",body:JSON.stringify({tripId:1,targetId:"attacker"})}),{
      LINE_CHANNEL_SECRET:"secret",LINE_CHANNEL_ACCESS_TOKEN:"token",SUPABASE_URL:"https://db.example",CRON_SECRET:secret,
    },{waitUntil:vi.fn()} as unknown as ExecutionContext);
    expect(response.status).toBe(401);expect(fetch).not.toHaveBeenCalled();
  });
});
