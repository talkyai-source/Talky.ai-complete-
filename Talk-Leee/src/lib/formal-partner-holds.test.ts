import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { formalPartnerHolds, type PartnerHoldCommand } from "@/lib/formal-partner-holds";
import { invalidateRequestIdentity, setRequestIdentity, setTokenProvider } from "@/lib/http-client";

const PARTNER = "10000000-0000-4000-8000-000000000001", OTHER = "10000000-0000-4000-8000-000000000002";
const HOLD = "20000000-0000-4000-8000-000000000001", AUDIT = "30000000-0000-4000-8000-000000000001";
const ADMIN = "40000000-0000-4000-8000-000000000001", NONCE = "50000000-0000-4000-8000-000000000001";
const event = (extra: Record<string, unknown> = {}) => ({ suspension_id:HOLD,target_type:"partner",target_id:PARTNER,suspension_type:"ADMIN",
    reason_description:"Reviewed",reason_category:"security",suspended_at:"2026-10-21T00:00:00Z",suspended_until:null,is_active:true,
    appeal_reason:null,appeal_decision:null,appeal_response:null,audit_log_id:AUDIT,restored_at:null,restore_reason:null,suspended_by:ADMIN,evidence:null,...extra });
const status = () => ({target_type:"partner",target_id:PARTNER,status:"active",is_suspended:true,active_suspension:event(),suspension_history:[event()],
    effective_status:"suspended",remaining_hold_ids:[HOLD],history_complete:false,history_limit:200});
const proof = () => ({ version:1,suspension_id:HOLD,target_type:"partner",target_id:PARTNER,event_state:"restored",policy_cutoff:"2026-10-21T00:00:00Z",observed_at_db:"2026-10-21T00:01:00Z",
    current_authority:{status:"restricted",remaining_hold_ids:[OTHER]},propagation_status:"unverified",
    dimensions:["credentials","assistant_runtimes","telephony"].map(key=>({key,status:"unverified",coverage:"unknown",counts:{captured:1,observed:1,confirmed:0,pending:0,exempt:0,unknown:1},reasons:["Original coverage unknown"]})),reconciliation_reasons:["Historical runtime coverage unknown"]});
const receipt = (extra:Record<string,unknown>={}) => ({suspension_id:HOLD,audit_event_id:AUDIT,target_type:"partner",target_id:PARTNER,status:"suspended",...extra});
const suspend:PartnerHoldCommand={kind:"suspend",reason:" Reviewed ",category:" security ",holdType:"TEMPORARY",hours:6,operatorRequestId:NONCE};
const originalFetch=globalThis.fetch;
let result:unknown, statusCode:number, requests:Array<{url:string;init?:RequestInit}>;
beforeEach(()=>{
    process.env.NEXT_PUBLIC_API_BASE_URL="http://localhost:8000/api/v1";
    invalidateRequestIdentity();setRequestIdentity({userId:ADMIN});setTokenProvider(()=>"synthetic-admin-token");
    requests=[];result=receipt();statusCode=200;
    globalThis.fetch=(async(url:RequestInfo|URL,init?:RequestInit)=>{
        requests.push({url:String(url),init});return new Response(JSON.stringify(result),{status:statusCode,headers:{"content-type":"application/json"}});
    }) as typeof fetch;
});
afterEach(()=>{globalThis.fetch=originalFetch;setTokenProvider(null);});

test("formal partner create sends exact target, reviewed nonce, duration and trimmed content through canonical HTTP",async()=>{
    await formalPartnerHolds.change(PARTNER,suspend);
    assert.equal(requests.length,1);assert.match(requests[0].url,new RegExp(`/admin/suspensions/partners/${PARTNER}/suspend$`));
    assert.equal(requests[0].init?.method,"POST");
    assert.deepEqual(JSON.parse(String(requests[0].init?.body)),{suspension_type:"TEMPORARY",reason_category:"security",reason_description:"Reviewed",evidence:{operator_request_id:NONCE},duration_hours:6});
    assert.equal(new Headers(requests[0].init?.headers).get("Authorization"),"Bearer synthetic-admin-token");
});
for(const kind of ["restore","appeal","granted","denied"] as const)test(`${kind} sends exact hold and accepts only its matching receipt`,async()=>{
    result=receipt({restored:kind==="restore"||kind==="granted",decision:kind==="granted"||kind==="denied"?kind:undefined,appeal_submitted:kind==="appeal",status:kind==="appeal"?"pending_review":"restored"});
    await formalPartnerHolds.change(PARTNER,{kind,eventId:HOLD,reason:" Reviewed "});
    const body=JSON.parse(String(requests[0].init?.body));
    if(kind==="restore")assert.deepEqual(body,{reason:"Reviewed",expected_suspension_id:HOLD});
    else if(kind==="appeal")assert.deepEqual(body,{appeal_reason:"Reviewed"});
    else assert.deepEqual(body,{decision:kind,response:"Reviewed"});
    assert.equal(requests.length,1);
});
for(const [name,patch] of Object.entries({wrong_target:{target_id:OTHER},missing_target:{target_id:undefined},wrong_state:{status:"active"},missing_audit:{audit_event_id:null}}))test(`create rejects ${name} receipt`,async()=>{
    result=receipt(patch);await assert.rejects(()=>formalPartnerHolds.change(PARTNER,suspend));assert.equal(requests.length,1);
});
for(const [name,kind,patch] of [
    ["different hold","restore",{suspension_id:OTHER,restored:true}],
    ["unconfirmed restoration","restore",{restored:false}],
    ["unconfirmed grant","granted",{decision:"granted",restored:false}],
    ["different decision","denied",{decision:"granted",restored:true}],
    ["unconfirmed appeal","appeal",{appeal_submitted:false,status:"pending_review"}],
    ["wrong appeal state","appeal",{appeal_submitted:true,status:"denied"}],
] as const)test(`mutation rejects ${name}`,async()=>{
    result=receipt(patch);await assert.rejects(()=>formalPartnerHolds.change(PARTNER,{kind,eventId:HOLD,reason:"Reviewed"}));assert.equal(requests.length,1);
});
test("unknown HTTP outcome is not retried by the request helper",async()=>{
    statusCode=503;result={detail:"response unavailable"};await assert.rejects(()=>formalPartnerHolds.change(PARTNER,suspend));assert.equal(requests.length,1);
});
test("invalid local identity and duration send no mutation",async()=>{
    await assert.rejects(()=>formalPartnerHolds.change("not-a-uuid",suspend));
    await assert.rejects(()=>formalPartnerHolds.change(PARTNER,{...suspend,hours:0}));assert.equal(requests.length,0);
});
test("status preserves bounded-history metadata and validates every child target",async()=>{
    result=status();const parsed=await formalPartnerHolds.status(PARTNER);assert.equal(parsed.history_complete,false);assert.equal(parsed.history_limit,200);
    result={...status(),suspension_history:[event({target_id:OTHER})]};await assert.rejects(()=>formalPartnerHolds.status(PARTNER));
    result={...status(),effective_status:{}};await assert.rejects(()=>formalPartnerHolds.status(PARTNER));
});
test("status retains valid submitted time and preserves absent/null legacy reads without accepting malformed time",async()=>{
    const submitted="2026-10-21T00:10:00Z";
    result={...status(),suspension_history:[event({appeal_submitted_at:submitted})]};
    assert.equal((await formalPartnerHolds.status(PARTNER)).suspension_history[0].appeal_submitted_at,submitted);
    for(const legacy of [undefined,null]){
        result={...status(),suspension_history:[event({appeal_submitted_at:legacy})]};
        assert.equal((await formalPartnerHolds.status(PARTNER)).suspension_history[0].appeal_submitted_at,legacy);
    }
    result={...status(),suspension_history:[event({appeal_submitted_at:"invalid"})]};
    await assert.rejects(()=>formalPartnerHolds.status(PARTNER));
});
test("evidence retains restored event and currently restricted authority separately",async()=>{
    result=proof();const parsed=await formalPartnerHolds.evidence(PARTNER,HOLD);assert.equal(parsed.event_state,"restored");assert.equal(parsed.current_authority.status,"restricted");assert.equal(parsed.dimensions.length,3);
});
const malformed:Record<string,(p:ReturnType<typeof proof>)=>unknown>={
    missing_dimension:p=>({...p,dimensions:p.dimensions.slice(1)}),
    duplicate_dimension:p=>({...p,dimensions:[p.dimensions[0],p.dimensions[0],p.dimensions[2]]}),
    wrong_target:p=>({...p,target_id:OTHER}),wrong_event:p=>({...p,suspension_id:OTHER}),
    invalid_event_state:p=>({...p,event_state:"unknown_fake_state"}),invalid_observation:p=>({...p,observed_at_db:"not-a-date"}),
    false_confirmed:p=>({...p,propagation_status:"confirmed"}),
    pending_mismatch:p=>({...p,propagation_status:"pending"}),
    invalid_count:p=>({...p,dimensions:p.dimensions.map(d=>({...d,counts:{...d.counts,unknown:-1}}))}),
    fractional_count:p=>({...p,dimensions:p.dimensions.map(d=>({...d,counts:{...d.counts,unknown:0.5}}))}),
    unresolved_confirmed:p=>({...p,propagation_status:"confirmed",dimensions:p.dimensions.map(d=>({...d,status:"confirmed",coverage:"complete"}))}),
    unresolved_exemption:p=>({...p,propagation_status:"confirmed",dimensions:p.dimensions.map(d=>({...d,status:"exempt_by_policy",coverage:"complete"}))}),
    unresolved_not_applicable:p=>({...p,propagation_status:"confirmed",dimensions:p.dimensions.map(d=>({...d,status:"not_applicable",coverage:"complete"}))}),
};
for(const [name,change]of Object.entries(malformed))test(`evidence rejects ${name}`,async()=>{result=change(proof());await assert.rejects(()=>formalPartnerHolds.evidence(PARTNER,HOLD));});
test("a complete coherent empty cohort is accepted without inventing observed effects",async()=>{
    result={...proof(),propagation_status:"confirmed",dimensions:proof().dimensions.map(d=>({...d,status:"not_applicable",coverage:"complete",counts:{captured:0,observed:0,confirmed:0,pending:0,exempt:0,unknown:0}}))};
    const parsed=await formalPartnerHolds.evidence(PARTNER,HOLD);assert.equal(parsed.propagation_status,"confirmed");assert.equal(parsed.dimensions[0].counts.observed,0);
});
