import {describe,it,expect} from "vitest";
import {applyDepositTransfers,buildDepositStatus,parseDepositCommand,type Deposit} from "../src/deposits";

const deposit=(payer:string,receiver:string,amount:number,currency="THB"):Deposit=>({expense_id:206,trip_id:1,payer_name:payer,receiver_name:receiver,amount_minor:amount,currency});
describe("deposit accounting",()=>{
  it("keeps the bill funded exactly once, including the collector's own contribution",()=>{
    const paid={A:300};
    applyDepositTransfers(paid,[deposit("A","A",10000),deposit("B","A",5000),deposit("C","A",2500)],"THB",1);
    expect(paid).toEqual({A:225,B:50,C:25});
    expect(Object.values(paid).reduce((a,b)=>a+b,0)).toBe(300);
  });
  it("uses the expense rate and normalizes a legacy currency alias",()=>{
    const paid={A:200};
    applyDepositTransfers(paid,[deposit("B","A",50000,"JYP")],"JPY",0.2);
    expect(paid).toEqual({A:100,B:100});
  });
  it("refuses to display stale deposits after the expense currency changed",()=>{
    expect(()=>buildDepositStatus({id:206,amount:100,currency:"USD",participants:["A","B"]},[deposit("B","A",1000)])).toThrow();
  });
  it("rejects a fractional cent or mismatched currency before accounting",()=>{
    expect(()=>applyDepositTransfers({A:100},[deposit("B","A",1.5)],"THB",1)).toThrow();
    expect(()=>applyDepositTransfers({A:100},[deposit("B","A",1000,"JPY")],"THB",1)).toThrow();
  });
  it("parses exact cents and rejects ambiguous or oversized amounts",()=>{
    expect(parseDepositCommand("มัดจำ 0206 1,120.05 A ให้ B")).toMatchObject({amountMinor:112005});
    expect(parseDepositCommand("มัดจำ 0206 1,12 A ให้ B")).toBeNull();
    expect(parseDepositCommand("มัดจำ 0206 1000001 A ให้ B")).toBeNull();
  });
});
