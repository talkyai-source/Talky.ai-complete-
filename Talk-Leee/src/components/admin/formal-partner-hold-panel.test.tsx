import React, { StrictMode } from "react";
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, cleanup, fireEvent, render, screen, waitFor, type RenderResult } from "@testing-library/react";
import { FormalPartnerHoldPanel } from "@/components/admin/formal-partner-hold-panel";
import { getRequestIdentityGeneration, invalidateRequestIdentity, setRequestIdentity, setTokenProvider } from "@/lib/http-client";

const PARTNER="10000000-0000-4000-8000-000000000001",OTHER="10000000-0000-4000-8000-000000000002";
const HOLD="20000000-0000-4000-8000-000000000001",NEXT_HOLD="20000000-0000-4000-8000-000000000002";
const AUDIT="30000000-0000-4000-8000-000000000001",ADMIN="40000000-0000-4000-8000-000000000001";
const event=(extra:Record<string,unknown>={})=>({suspension_id:HOLD,target_type:"partner",target_id:PARTNER,suspension_type:"ADMIN",reason_category:"administrative_review",reason_description:"Original hold",
    suspended_at:"2026-10-21T00:00:00Z",suspended_until:null,is_active:true,appeal_reason:null,appeal_decision:null,appeal_response:null,audit_log_id:AUDIT,restored_at:null,restore_reason:null,suspended_by:ADMIN,evidence:null,...extra});
const status=(events:ReturnType<typeof event>[]=[],partner=PARTNER)=>({target_type:"partner",target_id:partner,status:"active",is_suspended:events.some(e=>e.is_active),
    active_suspension:events.find(e=>e.is_active)??null,suspension_history:events,effective_status:events.some(e=>e.is_active)?"suspended":"active",remaining_hold_ids:events.filter(e=>e.is_active).map(e=>e.suspension_id),history_complete:true,history_limit:200});
const proof=(hold=HOLD,partner=PARTNER)=>({version:1,suspension_id:hold,target_type:"partner",target_id:partner,event_state:"restored",policy_cutoff:"2026-10-21T00:00:00Z",observed_at_db:"2026-10-21T00:01:00Z",
    current_authority:{status:"restricted",remaining_hold_ids:[NEXT_HOLD]},propagation_status:"unverified",dimensions:["credentials","assistant_runtimes","telephony"].map(key=>({key,status:key==="telephony"?"exempt_by_policy":"unverified",coverage:key==="telephony"?"complete":"unknown",counts:{captured:key==="telephony"?1:2,observed:1,confirmed:0,pending:0,exempt:key==="telephony"?1:0,unknown:key==="telephony"?0:1},reasons:["Historical coverage missing"]})),reconciliation_reasons:["Full runtime closure unverified"]});
const receipt=(extra:Record<string,unknown>={})=>({suspension_id:HOLD,target_type:"partner",target_id:PARTNER,status:"suspended",audit_event_id:AUDIT,...extra});
const json=(data:unknown,code=200)=>new Response(JSON.stringify(data),{status:code,headers:{"content-type":"application/json"}});
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done;});return{promise,resolve};}
type Request={path:string;method:string;body:Record<string,unknown>|undefined};
let current:unknown,currentProof:unknown,requests:Request[],view:RenderResult;
let respond:(request:Request)=>Response|Promise<Response>;
const originalFetch=globalThis.fetch;
const route=(request:Request)=>request.method==="POST"?json(receipt()):request.path.includes("/propagation-status/")?json(currentProof??proof(request.path.split("/").at(-1))):json(current);
const posts=()=>requests.filter(request=>request.method==="POST");
beforeEach(()=>{
    process.env.NEXT_PUBLIC_API_BASE_URL="http://localhost:8000/api/v1";
    invalidateRequestIdentity();setRequestIdentity({userId:ADMIN});setTokenProvider(()=>"synthetic-operator-token");
    current=status();currentProof=undefined;requests=[];respond=route;
    globalThis.fetch=(async(url:RequestInfo|URL,init?:RequestInit)=>{
        const request={path:new URL(String(url),"http://localhost").pathname,method:init?.method??"GET",body:init?.body?JSON.parse(String(init.body)):undefined};
        requests.push(request);return respond(request);
    }) as typeof fetch;
});
afterEach(()=>{cleanup();globalThis.fetch=originalFetch;setTokenProvider(null);});
const panel=(partner=PARTNER)=><StrictMode><FormalPartnerHoldPanel partnerId={partner} partnerName={partner===PARTNER?"Alpha partner":"Beta partner"} operatorId={ADMIN} identityGeneration={getRequestIdentityGeneration()} onClose={()=>view.rerender(<p>Closed panel</p>)}/></StrictMode>;
const loaded=async()=>{await waitFor(()=>assert.equal((screen.getByRole("button",{name:"Refresh status and evidence"}) as HTMLButtonElement).disabled,false));};
const draw=async(partner=PARTNER)=>{view=render(panel(partner));await loaded();};
const click=(name:string)=>fireEvent.click(screen.getByRole("button",{name}));
const text=()=>document.body.textContent??"";
async function draft(action="Create formal hold",reason="Reviewed reason"){
    click(action);fireEvent.change(screen.getByLabelText(action.includes("appeal")&&action!=="Record operator appeal"?"Review response":"Reason",{exact:true}),{target:{value:reason}});
    fireEvent.click(screen.getByRole("checkbox",{name:/I reviewed this partner/}));
}
const submit=()=>fireEvent.submit(document.querySelector("form")!);
const retainedSubmit=()=>{
    const node=document.querySelector("form")!;
    const props=(node as unknown as Record<string,unknown>)[Object.keys(node).find(key=>key.startsWith("__reactProps"))!] as {onSubmit:(event:{preventDefault:()=>void})=>void};
    return ()=>props.onSubmit({preventDefault(){}});
};

for(const phase of ["inflight","unknown"] as const)test(`closing and reopening a ${phase} mutation cannot resend it`,async()=>{
    const pending=deferred<Response>();respond=request=>request.method==="POST"?pending.promise:route(request);
    await draw();await draft();submit();assert.equal(posts().length,1);
    if(phase==="unknown")await act(async()=>pending.resolve(json({detail:"lost outcome"},503)));
    click("Close");view.rerender(panel());await loaded();assert.match(text(),/request outcome is unverified/);
    assert.equal((screen.getByRole("button",{name:"Create formal hold"}) as HTMLButtonElement).disabled,true);
    click("Refresh status and evidence");await loaded();assert.equal(posts().length,1);
    if(phase==="inflight")await act(async()=>pending.resolve(json(receipt())));
    assert.doesNotMatch(text(),/Saved decision confirmed/);assert.equal(posts().length,1);
});
test("a saved receipt survives failed reload and reopening without allowing duplicate restore",async()=>{
    current=status([event()]);let fail=false;
    respond=request=>request.method==="POST"?(fail=true,json(receipt({restored:true,status:"restored"}))):fail&&request.path.endsWith("/status")?json({detail:"refresh unavailable"},503):route(request);
    await draw();await draft("Restore selected hold");submit();await screen.findByText("Saved decision confirmed");
    assert.match(text(),/Audit receipt:/);assert.doesNotMatch(text(),/Saved event audit reference|Original request receipt unavailable/);
    await screen.findByText(/Status unavailable: refresh unavailable/);click("Close");view.rerender(panel());await loaded();
    assert.match(text(),/Saved decision confirmed/);assert.equal(posts().length,1);
});
test("saved restoration overrides a stale active read and retained submit cannot repeat it",async()=>{
    current=status([event()]);respond=request=>request.method==="POST"?json(receipt({restored:true,status:"restored"})):route(request);
    await draw();await draft("Restore selected hold");const old=retainedSubmit();submit();await screen.findByText("Saved decision confirmed");await loaded();
    assert.match(text(),/Restoration of this hold is already confirmed/);assert.equal((screen.getByRole("button",{name:"Restore selected hold"}) as HTMLButtonElement).disabled,true);
    act(()=>old());assert.equal(posts().length,1);
});
test("a proved restored created hold permits a new distinct hold with a new reviewed nonce",async()=>{
    let saves=0;respond=request=>request.method==="POST"?json(receipt({suspension_id:++saves===1?HOLD:NEXT_HOLD})):route(request);
    await draw();await draft();submit();await screen.findByText("Saved decision confirmed");await loaded();
    assert.equal((screen.getByRole("button",{name:"Create formal hold"}) as HTMLButtonElement).disabled,true);
    const first=(posts()[0].body?.evidence as {operator_request_id:string}).operator_request_id;
    current=status([event({is_active:false,restored_at:"2026-10-21T01:00:00Z",restore_reason:"Reviewed restore"})]);
    click("Refresh status and evidence");await loaded();await draft("Create formal hold","A new reviewed restriction");submit();
    await waitFor(()=>assert.equal(posts().length,2));assert.notEqual((posts()[1].body?.evidence as {operator_request_id:string}).operator_request_id,first);
});
test("unknown create requires exact nonce, actor, target and content before observation",async()=>{
    respond=request=>request.method==="POST"?json({detail:"lost outcome"},503):route(request);
    await draw();await draft();submit();await screen.findByText(/request outcome is unverified/);
    const evidence=posts()[0].body?.evidence;
    current=status([event({reason_description:"Reviewed reason"})]);click("Refresh status and evidence");await loaded();click("Review matching saved decision");
    assert.match(text(),/No matching saved decision/);
    current=status([event({reason_description:"Reviewed reason",evidence})]);click("Refresh status and evidence");await loaded();click("Review matching saved decision");
    assert.match(text(),/Matching saved decision reviewed/);assert.doesNotMatch(text(),/Saved decision confirmed/);assert.equal(posts().length,1);
});
for(const anotherActiveHold of [false,true])test(`historical create is observed after one refresh and review; another active hold ${anotherActiveHold}`,async()=>{
    respond=request=>request.method==="POST"?json({detail:"Historical create response lost"},503):route(request);
    await draw();await draft();submit();await screen.findByText(/request outcome is unverified/);
    const historical=event({reason_description:"Reviewed reason",evidence:posts()[0].body?.evidence,
        is_active:false,restored_at:"2026-10-21T01:00:00Z",restore_reason:"Independent later restoration"});
    // The same event may occur in more than one returned history position.
    current=status([historical,historical,...(anotherActiveHold?[event({suspension_id:NEXT_HOLD})]:[])]);
    const readsBefore=requests.filter(request=>request.path.endsWith("/status")).length;
    click("Refresh status and evidence");await loaded();
    assert.match(text(),/request outcome is unverified/,"A read alone preserves the explicit review step");
    click("Review matching saved decision");
    assert.match(text(),/Matching saved decision reviewed/);
    assert.match(text(),/Saved event audit reference/);assert.match(text(),/Original request receipt unavailable/);
    assert.doesNotMatch(text(),/Audit receipt:/);
    assert.doesNotMatch(text(),/request outcome is unverified|Historical create response lost|Saved decision confirmed/);
    assert.equal(posts().length,1,"Historical observation never resends the mutation");
    assert.equal(requests.filter(request=>request.path.endsWith("/status")).length,readsBefore+1,"No second status read is needed to retire the created hold");
    if(anotherActiveHold){
        assert.match(text(),/Formal hold: Active/);
        assert.equal(screen.queryByRole("button",{name:"Create formal hold"}),null);
    }else{
        assert.match(text(),/Formal hold: None active/);
        assert.equal((screen.getByRole("button",{name:"Create formal hold"}) as HTMLButtonElement).disabled,false);
    }
});
for(const mismatch of ["nonce","actor","duration","ambiguous","missing-restoration","invalid-restoration"] as const)test(`historical create keeps uncertainty for ${mismatch}`,async()=>{
    respond=request=>request.method==="POST"?json({detail:"Historical create response lost"},503):route(request);
    await draw();await draft();submit();await screen.findByText(/request outcome is unverified/);
    const changed:Record<string,unknown>={};
    if(mismatch==="nonce")changed.evidence={operator_request_id:NEXT_HOLD};
    if(mismatch==="actor")changed.suspended_by=OTHER;
    if(mismatch==="duration")changed.suspended_until="2026-10-21T06:00:00Z";
    if(mismatch==="missing-restoration")changed.restored_at=null;
    if(mismatch==="invalid-restoration")changed.restored_at="not-a-date";
    const historical=event({reason_description:"Reviewed reason",evidence:posts()[0].body?.evidence,
        is_active:false,restored_at:"2026-10-21T01:00:00Z",restore_reason:"Independent later restoration",...changed});
    current=status([historical,...(mismatch==="ambiguous"?[{...historical,suspension_id:NEXT_HOLD}]:[])]);
    click("Refresh status and evidence");await loaded();
    if(mismatch!=="invalid-restoration")click("Review matching saved decision");
    else assert.match(text(),/Status unavailable/);
    assert.match(text(),/request outcome is unverified/);
    assert.doesNotMatch(text(),/Matching saved decision reviewed|Saved decision confirmed/);
    assert.equal((screen.getByRole("button",{name:"Create formal hold"}) as HTMLButtonElement).disabled,true);
    assert.equal(posts().length,1);
});
for(const decision of ["granted","denied"] as const)test(`historical appeal already ${decision} is observed without replacing its current decision`,async()=>{
    current=status([event()]);
    respond=request=>request.method==="POST"?json({detail:"Historical appeal response lost"},503):route(request);
    await draw();await draft("Record operator appeal");submit();await screen.findByText(/request outcome is unverified/);
    current=status([event({appeal_reason:"Reviewed reason",appeal_submitted_at:"2026-10-21T00:10:00Z",
        appeal_decision:decision,appeal_response:"Later independent review",is_active:decision!=="granted",
        restored_at:decision==="granted"?"2026-10-21T01:00:00Z":null})]);
    const readsBefore=requests.filter(request=>request.path.endsWith("/status")).length;
    click("Refresh status and evidence");await loaded();
    assert.match(text(),/request outcome is unverified/);click("Review matching saved decision");
    assert.match(text(),/Matching saved decision reviewed/);
    assert.match(text(),new RegExp(`Appeal: ${decision}`));
    assert.match(text(),/Saved event audit reference/);assert.match(text(),/Original request receipt unavailable/);
    assert.doesNotMatch(text(),/Audit receipt:/);
    assert.match(text(),/Later independent review/);
    assert.doesNotMatch(text(),/request outcome is unverified|Historical appeal response lost|Saved decision confirmed/);
    assert.equal(posts().length,1);
    assert.equal(requests.filter(request=>request.path.endsWith("/status")).length,readsBefore+1);
});
for(const mismatch of ["reason","missing-time","invalid-time"] as const)test(`historical appeal keeps uncertainty for ${mismatch}`,async()=>{
    current=status([event()]);
    respond=request=>request.method==="POST"?json({detail:"Historical appeal response lost"},503):route(request);
    await draw();await draft("Record operator appeal");submit();await screen.findByText(/request outcome is unverified/);
    current=status([event({appeal_reason:mismatch==="reason"?"A different appeal":"Reviewed reason",
        appeal_submitted_at:mismatch==="missing-time"?null:mismatch==="invalid-time"?"invalid":"2026-10-21T00:10:00Z",
        appeal_decision:"denied",appeal_response:"Later independent review"})]);
    click("Refresh status and evidence");await loaded();
    if(!(screen.getByRole("button",{name:"Review matching saved decision"}) as HTMLButtonElement).disabled)click("Review matching saved decision");
    assert.match(text(),/request outcome is unverified/);
    assert.doesNotMatch(text(),/Matching saved decision reviewed|Saved decision confirmed/);
    assert.equal(posts().length,1);
});
for(const legacyTime of [undefined,null])test(`pending appeal preserves legacy ${legacyTime===null?"null":"absent"} submitted time observation`,async()=>{
    current=status([event()]);
    respond=request=>request.method==="POST"?json({detail:"Pending appeal response lost"},503):route(request);
    await draw();await draft("Record operator appeal");submit();await screen.findByText(/request outcome is unverified/);
    current=status([event({appeal_reason:"Reviewed reason",appeal_decision:"pending",appeal_submitted_at:legacyTime})]);
    click("Refresh status and evidence");await loaded();click("Review matching saved decision");
    assert.match(text(),/Matching saved decision reviewed/);assert.match(text(),/Appeal: pending/);
    assert.doesNotMatch(text(),/request outcome is unverified|Pending appeal response lost|Saved decision confirmed/);
    assert.equal(posts().length,1);
});
for(const hours of [undefined,6])test(`unknown create requires exact ${hours??"indefinite"} duration before observation`,async()=>{
    respond=request=>request.method==="POST"?json({detail:"lost outcome"},503):route(request);
    await draw();click("Create formal hold");
    if(hours!==undefined){fireEvent.change(screen.getByLabelText("Hold type"),{target:{value:"TEMPORARY"}});fireEvent.change(screen.getByLabelText("Duration in hours"),{target:{value:String(hours)}});}
    fireEvent.change(screen.getByLabelText("Reason",{exact:true}),{target:{value:"Reviewed duration"}});
    fireEvent.click(screen.getByRole("checkbox",{name:/I reviewed this partner/}));submit();await screen.findByText(/request outcome is unverified/);
    const recorded={reason_description:"Reviewed duration",suspension_type:hours===undefined?"ADMIN":"TEMPORARY",evidence:posts()[0].body?.evidence};
    current=status([event({...recorded,suspended_until:"2026-10-21T07:00:00Z"})]);click("Refresh status and evidence");await loaded();click("Review matching saved decision");
    assert.match(text(),/No matching saved decision/);assert.doesNotMatch(text(),/Matching saved decision reviewed/);
    current=status([event({...recorded,suspended_until:hours===undefined?null:"2026-10-21T06:00:00Z"})]);click("Refresh status and evidence");await loaded();click("Review matching saved decision");
    assert.match(text(),/Matching saved decision reviewed/);assert.equal(posts().length,1);
});
test("refresh retires a reviewed callback before its read completes or fails",async()=>{
    current=status([event()]);await draw();await draft("Restore selected hold");const old=retainedSubmit();const pending=deferred<Response>();
    respond=request=>request.path.endsWith("/status")?pending.promise:route(request);
    click("Refresh status and evidence");act(()=>old());assert.equal(posts().length,0);
    await act(async()=>pending.resolve(json({detail:"read unavailable"},503)));act(()=>old());assert.equal(posts().length,0);
});
test("selected partner switch retires pending reads and mutations",async()=>{
    const pending=deferred<Response>();respond=request=>request.method==="POST"?pending.promise:route(request);
    await draw();await draft();submit();current=status([],OTHER);view.rerender(panel(OTHER));await loaded();
    await act(async()=>pending.resolve(json(receipt())));assert.match(text(),/Beta partner/);assert.doesNotMatch(text(),/Saved decision confirmed/);
    assert.equal(posts().length,1);
});
test("identity replacement retires mutation and resets session-owned memory",async()=>{
    const pending=deferred<Response>();respond=request=>request.method==="POST"?pending.promise:route(request);
    await draw();await draft();submit();act(()=>{invalidateRequestIdentity();setRequestIdentity({userId:ADMIN});view.rerender(panel());});await loaded();
    await act(async()=>pending.resolve(json(receipt())));assert.doesNotMatch(text(),/Saved decision confirmed|request outcome is unverified/);
    assert.equal(posts().length,1);
});
for(const change of ["unmount","target","generation"] as const)test(`retained reviewed submit dispatches nothing after ${change} changes`,async()=>{
    current=status([event()]);await draw();await draft("Restore selected hold");const old=retainedSubmit();
    if(change==="unmount")view.rerender(<p>Different surface</p>);
    else if(change==="target"){current=status([],OTHER);view.rerender(panel(OTHER));await loaded();}
    else act(()=>invalidateRequestIdentity());
    act(()=>old());await act(async()=>{});assert.equal(posts().length,0);
});
test("old evidence cannot replace the newly selected event",async()=>{
    current=status([event(),event({suspension_id:NEXT_HOLD,is_active:false,restored_at:"2026-10-21T01:00:00Z"})]);
    const pending=deferred<Response>();respond=request=>request.path.endsWith(`/propagation-status/${HOLD}`)?pending.promise:route(request);
    await draw();fireEvent.change(screen.getByLabelText("Reviewed hold"),{target:{value:NEXT_HOLD}});await screen.findByText(/Full runtime closure unverified/);
    await act(async()=>pending.resolve(json({...proof(),reconciliation_reasons:["Obsolete Alpha proof"]})));
    assert.doesNotMatch(text(),/Obsolete Alpha proof/);
});
test("UI shows restored event, observed restriction, time, all counts and coverage separately",async()=>{
    current=status([event({is_active:false,restored_at:"2026-10-21T01:00:00Z"})]);await draw();await screen.findByText(/Access at observation: restricted/);
    assert.match(text(),/Selected hold at observation: restored/);assert.match(text(),/Policy cutoff: 2026-10-21/);
    assert.match(text(),/Captured 2 · Observed 1 · Confirmed 0/);assert.match(text(),/telephony: exempt by policy/);
});
test("malformed proof is unavailable rather than a false confirmed state",async()=>{
    current=status([event()]);currentProof={...proof(),propagation_status:"confirmed",dimensions:[]};await draw();
    await screen.findByText(/Evidence unavailable/);assert.doesNotMatch(text(),/confirmed · observed/);
});
test("partner dialog traps keyboard focus and Escape returns it to the invoker",async()=>{
    const opener=document.createElement("button");opener.textContent="Existing partner control";document.body.append(opener);opener.focus();
    await draw();const dialog=screen.getByRole("dialog");await waitFor(()=>assert.ok(dialog.contains(document.activeElement)));
    const buttons=[...dialog.querySelectorAll<HTMLButtonElement>("button:not(:disabled)")];buttons.at(-1)!.focus();
    fireEvent.keyDown(buttons.at(-1)!,{key:"Tab"});assert.equal(document.activeElement,buttons[0]);
    fireEvent.keyDown(window,{key:"Escape"});await screen.findByText("Closed panel");assert.equal(document.activeElement,opener);opener.remove();
});

// Pass the future optional callback at runtime without changing production
// Props for the preserved before-fix baseline.
type ChangedPanelProps=React.ComponentProps<typeof FormalPartnerHoldPanel>&{onChanged?:()=>void};
function changedPanel(onChanged?:()=>void,options:{partner?:string;operator?:string;generation?:number}={}){
    const partner=options.partner??PARTNER;
    const props:ChangedPanelProps={partnerId:partner,partnerName:partner===PARTNER?"Alpha partner":"Beta partner",
        operatorId:options.operator??ADMIN,identityGeneration:options.generation??getRequestIdentityGeneration(),
        onClose:()=>view.rerender(<p>Closed panel</p>),onChanged};
    return <StrictMode><FormalPartnerHoldPanel {...props}/></StrictMode>;
}
async function drawChanged(onChanged?:()=>void){view=render(changedPanel(onChanged));await loaded();}
const statusReads=()=>requests.filter(request=>request.path.endsWith("/status"));
function retainedReview(){
    const node=screen.getByRole("button",{name:"Review matching saved decision"});
    const props=(node as unknown as Record<string,unknown>)[Object.keys(node).find(key=>key.startsWith("__reactProps"))!] as {onClick:()=>void};
    return props.onClick;
}

for(const kind of ["suspend","restore","appeal","granted","denied"] as const)test(`parent notification follows accepted ${kind} receipt exactly once despite failed reload`,async()=>{
    const labels={suspend:"Create formal hold",restore:"Restore selected hold",appeal:"Record operator appeal",granted:"Grant appeal",denied:"Deny appeal"};
    if(kind!=="suspend")current=status([event({appeal_decision:kind==="granted"||kind==="denied"?"pending":null})]);
    const saved=receipt(kind==="restore"?{restored:true,status:"restored"}:kind==="appeal"?{appeal_submitted:true,status:"pending_review"}
        :kind==="granted"?{restored:true,decision:"granted"}:kind==="denied"?{decision:"denied"}:{});
    let failed=false;const notices:number[]=[];
    respond=request=>request.method==="POST"?(failed=true,json(saved)):failed&&request.path.endsWith("/status")?json({detail:"Followup unavailable"},503):route(request);
    const changed=()=>{notices.push(statusReads().length);};
    await drawChanged(changed);const before=statusReads().length;await draft(labels[kind]);submit();
    await screen.findByText("Saved decision confirmed");await screen.findByText(/Status unavailable: Followup unavailable/);
    assert.deepEqual(notices,[before],"Notify after adopting the receipt and before the followup read");
    assert.doesNotMatch(text(),/request outcome is unverified/);assert.equal(posts().length,1);
    click("Close");view.rerender(changedPanel(changed));await loaded();
    assert.match(text(),/Saved decision confirmed/);assert.equal(notices.length,1,"Reopening a saved receipt does not notify again");
});

test("parent notification uses latest committed callback during mutation without restarting reads or draft",async()=>{
    const pending=deferred<Response>();respond=request=>request.method==="POST"?pending.promise:route(request);
    let previous=0,latest=0;await drawChanged(()=>{previous+=1;});await draft();submit();
    const before=statusReads().length;view.rerender(changedPanel(()=>{latest+=1;}));
    await act(async()=>{});assert.equal(statusReads().length,before,"Callback identity is not a status-effect dependency");
    assert.equal(posts().length,1);await act(async()=>pending.resolve(json(receipt())));
    await screen.findByText("Saved decision confirmed");await loaded();
    assert.equal(previous,0);assert.equal(latest,1);
});

test("parent callback replacement during pending initial read neither discards nor restarts it",async()=>{
    const pending=deferred<Response>();respond=request=>request.path.endsWith("/status")?pending.promise:route(request);
    let previous=0,latest=0;view=render(changedPanel(()=>{previous+=1;}));
    await waitFor(()=>assert.equal(statusReads().length,1));view.rerender(changedPanel(()=>{latest+=1;}));
    await act(async()=>pending.resolve(json(status())));await loaded();assert.equal(statusReads().length,1);
    respond=route;await draft();submit();await screen.findByText("Saved decision confirmed");await loaded();
    assert.equal(previous,0);assert.equal(latest,1);
});

test("parent callback replacement preserves an already reviewed unsent draft",async()=>{
    let previous=0,latest=0;await drawChanged(()=>{previous+=1;});await draft();const before=statusReads().length;
    view.rerender(changedPanel(()=>{latest+=1;}));await act(async()=>{});
    assert.equal(statusReads().length,before);assert.equal((screen.getByRole("checkbox",{name:/I reviewed this partner/}) as HTMLInputElement).checked,true);
    submit();await screen.findByText("Saved decision confirmed");assert.equal(posts().length,1);assert.equal(previous,0);assert.equal(latest,1);
});

for(const failure of ["throw","reject"] as const)test(`parent callback ${failure} cannot erase a committed receipt or revive uncertainty`,async()=>{
    let notices=0;
    const changed=()=>{notices+=1;if(failure==="throw")throw new Error("Directory refresh failed");return Promise.reject(new Error("Directory refresh failed"));};
    await drawChanged(changed);await draft();submit();await screen.findByText("Saved decision confirmed");await loaded();
    assert.equal(notices,1);assert.doesNotMatch(text(),/request outcome is unverified/);
    assert.match(text(),/Related account views could not be refreshed/);
    click("Refresh status and evidence");await loaded();assert.equal(notices,1);assert.equal(posts().length,1);
    assert.match(text(),/Saved decision confirmed/);
});

test("parent notification may synchronously unmount after acceptance without a retired followup read",async()=>{
    let notices=0;
    await drawChanged(()=>{notices+=1;view.rerender(<p>Parent retired the panel</p>);});
    const before=statusReads().length;await draft();submit();await screen.findByText("Parent retired the panel");
    await act(async()=>{});assert.equal(notices,1);assert.equal(statusReads().length,before);assert.equal(posts().length,1);
    view.rerender(changedPanel(()=>{notices+=1;}));await loaded();assert.match(text(),/Saved decision confirmed/);assert.equal(notices,1);
});

test("parent notification follows exact observed create only once, including a retained Review handler",async()=>{
    respond=request=>request.method==="POST"?json({detail:"Lost create response"},503):route(request);
    let notices=0;const changed=()=>{notices+=1;};await drawChanged(changed);await draft();submit();
    await screen.findByText(/request outcome is unverified/);assert.equal(notices,0);
    current=status([event({reason_description:"Reviewed reason"})]);click("Refresh status and evidence");await loaded();click("Review matching saved decision");
    assert.match(text(),/No matching saved decision/);assert.equal(notices,0);
    current=status([event({reason_description:"Reviewed reason",evidence:posts()[0].body?.evidence})]);
    click("Refresh status and evidence");await loaded();assert.equal(notices,0,"A plain read is not acceptance");
    const old=retainedReview();click("Review matching saved decision");assert.match(text(),/Matching saved decision reviewed/);assert.equal(notices,1);
    act(()=>{old();old();});assert.equal(notices,1,"Retained handlers cannot repeat notification");assert.equal(posts().length,1);
    click("Refresh status and evidence");await loaded();click("Close");view.rerender(changedPanel(changed));await loaded();assert.equal(notices,1);
});

for(const failure of ["unavailable","wrong-target"] as const)test(`parent notification is absent for unknown mutation plus ${failure} observation`,async()=>{
    let notices=0;respond=request=>request.method==="POST"?json({detail:"Lost create response"},503):route(request);
    await drawChanged(()=>{notices+=1;});await draft();submit();await screen.findByText(/request outcome is unverified/);
    respond=request=>request.path.endsWith("/status")?(failure==="unavailable"?json({detail:"Read failed"},503):json(status([],OTHER))):route(request);
    click("Refresh status and evidence");await loaded();assert.match(text(),/Status unavailable/);assert.equal(notices,0);
    assert.equal((screen.getByRole("button",{name:"Review matching saved decision"}) as HTMLButtonElement).disabled,true);
});

for(const change of ["operator","target","generation","unmount"] as const)test(`parent notification rejects pending mutation after ${change} retirement`,async()=>{
    const pending=deferred<Response>();respond=request=>request.method==="POST"?pending.promise:route(request);
    let oldNotices=0,newNotices=0;await drawChanged(()=>{oldNotices+=1;});await draft();submit();
    if(change==="unmount")view.rerender(<p>Other surface</p>);
    else if(change==="operator")view.rerender(changedPanel(()=>{newNotices+=1;},{operator:OTHER}));
    else if(change==="target"){current=status([],OTHER);view.rerender(changedPanel(()=>{newNotices+=1;},{partner:OTHER}));}
    else act(()=>{invalidateRequestIdentity();setRequestIdentity({userId:ADMIN});view.rerender(changedPanel(()=>{newNotices+=1;}));});
    if(change!=="unmount")await loaded();
    await act(async()=>pending.resolve(json(receipt())));assert.equal(oldNotices,0);assert.equal(newNotices,0);
    assert.doesNotMatch(text(),/Saved decision confirmed/);assert.equal(posts().length,1);
});

for(const change of ["operator","target","generation","unmount"] as const)test(`parent notification rejects retained reconciliation after ${change} retirement`,async()=>{
    let notices=0;respond=request=>request.method==="POST"?json({detail:"Lost create response"},503):route(request);
    await drawChanged(()=>{notices+=1;});await draft();submit();await screen.findByText(/request outcome is unverified/);
    current=status([event({reason_description:"Reviewed reason",evidence:posts()[0].body?.evidence})]);click("Refresh status and evidence");await loaded();
    const old=retainedReview();
    if(change==="unmount")view.rerender(<p>Other surface</p>);
    else if(change==="operator")view.rerender(changedPanel(()=>{notices+=1;},{operator:OTHER}));
    else if(change==="target"){current=status([],OTHER);view.rerender(changedPanel(()=>{notices+=1;},{partner:OTHER}));}
    else act(()=>{invalidateRequestIdentity();setRequestIdentity({userId:ADMIN});view.rerender(changedPanel(()=>{notices+=1;}));});
    if(change!=="unmount")await loaded();act(()=>old());assert.equal(notices,0);
    assert.doesNotMatch(text(),/Matching saved decision reviewed|Saved decision confirmed/);assert.equal(posts().length,1);
});

test("partner history inactive without restoration evidence is not labelled Restored",async()=>{
    current=status([event({is_active:false,restored_at:null,restore_reason:null})]);await draw();
    const option=screen.getByRole("option",{name:new RegExp(HOLD)});assert.match(option.textContent??"",/Inactive.*restoration unverified/i);
    assert.doesNotMatch(option.textContent??"",/^Restored/);
});

test("partner history recorded restoration retains its Restored label",async()=>{
    current=status([event({is_active:false,restored_at:"2026-10-21T01:00:00Z",restore_reason:"Recorded restoration"})]);await draw();
    assert.match(screen.getByRole("option",{name:new RegExp(HOLD)}).textContent??"",/^Restored/);
});

test("saved partner receipt reports decision-time restriction separately from a later status read",async()=>{
    respond=request=>request.method==="POST"?json(receipt({effective_status:"suspended",remaining_hold_ids:[NEXT_HOLD]})):route(request);
    await draw();await draft();submit();await screen.findByText("Saved decision confirmed");await loaded();
    current=status([event({is_active:false,restored_at:"2026-10-21T01:00:00Z",restore_reason:"Later independent restoration"})]);
    click("Refresh status and evidence");await loaded();assert.match(text(),/Account state at that read: active/);
    const saved=screen.getByText("Saved decision confirmed").closest("section")!.textContent??"";
    assert.match(saved,/Account restriction reported with this decision: suspended/);
    assert.match(saved,/Other holds reported with this decision: 1/);
    assert.doesNotMatch(saved,/Current account restriction|still apply/);
});

for(const kind of ["restore","granted"] as const)for(const recordedTime of [false,true])test(`parent reconciliation ${kind} requires recorded restoration time: ${recordedTime}`,async()=>{
    current=status([event({appeal_decision:kind==="granted"?"pending":null})]);
    respond=request=>request.method==="POST"?json({detail:"Lost restoration response"},503):route(request);
    let notices=0;await drawChanged(()=>{notices+=1;});await draft(kind==="restore"?"Restore selected hold":"Grant appeal");submit();
    await screen.findByText(/request outcome is unverified/);
    current=status([event({is_active:false,restored_at:recordedTime?"2026-10-21T01:00:00Z":null,
        restore_reason:"Reviewed reason",appeal_decision:kind==="granted"?"granted":null,
        appeal_response:kind==="granted"?"Reviewed reason":null})]);
    click("Refresh status and evidence");await loaded();assert.equal(notices,0);
    click("Review matching saved decision");
    if(recordedTime){
        assert.match(text(),/Matching saved decision reviewed/);assert.doesNotMatch(text(),/request outcome is unverified/);assert.equal(notices,1);
    }else{
        assert.match(text(),/request outcome is unverified/);assert.doesNotMatch(text(),/Matching saved decision reviewed|Saved decision confirmed/);assert.equal(notices,0);
    }
    assert.equal(posts().length,1);
});

test("accepted partner receipt keeps connected focus inside the dialog after Confirm is removed",async()=>{
    const pending=deferred<Response>();respond=request=>request.method==="POST"?pending.promise:route(request);
    await draw();await draft();const confirm=screen.getByRole("button",{name:"Confirm: Create formal hold"});
    confirm.focus();assert.equal(document.activeElement,confirm);submit();
    assert.ok(document.activeElement?.isConnected&&screen.getByRole("dialog").contains(document.activeElement));
    assert.equal(document.activeElement?.matches(":disabled"),false,"Move focus before disabling the submitted control, not after its response");
    await act(async()=>pending.resolve(json(receipt())));await screen.findByText("Saved decision confirmed");await loaded();
    assert.equal(confirm.isConnected,false);
    assert.ok(document.activeElement?.isConnected&&screen.getByRole("dialog").contains(document.activeElement),
        "Removing the focused confirmation must leave a connected focus target in this dialog");
});

test("accepted partner receipt does not steal focus deliberately moved to Close while pending",async()=>{
    const pending=deferred<Response>();respond=request=>request.method==="POST"?pending.promise:route(request);
    await draw();await draft();screen.getByRole("button",{name:"Confirm: Create formal hold"}).focus();submit();
    const close=screen.getByRole("button",{name:"Close"});close.focus();assert.equal(document.activeElement,close);
    await act(async()=>pending.resolve(json(receipt())));await screen.findByText("Saved decision confirmed");await loaded();
    assert.equal(document.activeElement,close);assert.ok(close.isConnected);
});

test("observed partner reconciliation keeps connected focus after its Review control is removed",async()=>{
    respond=request=>request.method==="POST"?json({detail:"Lost create response"},503):route(request);
    await draw();await draft();submit();await screen.findByText(/request outcome is unverified/);
    current=status([event({reason_description:"Reviewed reason",evidence:posts()[0].body?.evidence})]);
    click("Refresh status and evidence");await loaded();const review=screen.getByRole("button",{name:"Review matching saved decision"});
    review.focus();assert.equal(document.activeElement,review);fireEvent.click(review);
    await screen.findByText("Matching saved decision reviewed");assert.equal(review.isConnected,false);
    assert.ok(document.activeElement?.isConnected&&screen.getByRole("dialog").contains(document.activeElement),
        "Removing the focused saved-decision review must leave focus in this dialog");
});

for(const result of ["success","failure"] as const)test(`partner status refresh keeps enabled dialog focus while pending and after ${result}`,async()=>{
    await draw();const pending=deferred<Response>();respond=request=>request.path.endsWith("/status")?pending.promise:route(request);
    const refresh=screen.getByRole("button",{name:"Refresh status and evidence"});refresh.focus();fireEvent.click(refresh);
    assert.ok(document.activeElement?.isConnected&&screen.getByRole("dialog").contains(document.activeElement));
    assert.equal(document.activeElement?.matches(":disabled"),false,"A focused Refresh must not be stranded while disabled");
    const close=screen.getByRole("button",{name:"Close"});close.focus();
    await act(async()=>pending.resolve(result==="success"?json(status()):json({detail:"Deferred status unavailable"},503)));await loaded();
    if(result==="failure")assert.match(text(),/Status unavailable: Deferred status unavailable/);
    assert.equal(document.activeElement,close,"Settling the read must not steal deliberate Close focus");
});

for(const complete of [true,false])test(`empty partner history describes only the returned read; complete ${complete}`,async()=>{
    current={...status(),history_complete:complete,history_limit:200};await draw();
    assert.match(text(),/Last successful status read/);
    assert.match(text(),/Account state at that read: active/);
    assert.match(text(),/No formal holds were returned by this read/);
    assert.doesNotMatch(text(),/Current account state|No formal holds are recorded/);
    if(!complete)assert.match(text(),/Only the first 200 history records are loaded\. This is not a complete account history/);
});

test("pending and lost partner create never presents its earlier empty read as current truth",async()=>{
    const pending=deferred<Response>();respond=request=>request.method==="POST"?pending.promise:route(request);
    await draw();await draft();submit();const pendingText=text();
    await act(async()=>pending.resolve(json({detail:"Committed response not received"},503)));
    await screen.findByText(/request outcome is unverified/);
    respond=request=>request.path.endsWith("/status")?json({detail:"Recovery read unavailable"},503):route(request);
    click("Refresh status and evidence");await loaded();const failedReadText=text();
    current=status([event({reason_description:"Reviewed reason",evidence:posts()[0].body?.evidence})]);respond=route;
    click("Refresh status and evidence");await loaded();
    for(const snapshot of [pendingText,failedReadText]){
        assert.match(snapshot,/Last successful status read/);
        assert.match(snapshot,/Account state at that read: active/);
        assert.doesNotMatch(snapshot,/Current account state|No formal holds are recorded/);
    }
    assert.match(failedReadText,/Current account status could not be verified/);
    assert.match(text(),/Account state at that read: suspended/);
    assert.match(text(),/request outcome is unverified/,"A later GET cannot confirm the lost request");
    assert.equal((screen.getByRole("button",{name:"Restore selected hold"}) as HTMLButtonElement).disabled,true);
    assert.equal(posts().length,1);
});

test("confirmed partner restoration plus failed status read keeps prior enforcement as an observation",async()=>{
    current=status([event()]);currentProof={...proof(),event_state:"active",current_authority:{status:"restricted",remaining_hold_ids:[HOLD,NEXT_HOLD]}};
    let saved=false;respond=request=>request.method==="POST"?(saved=true,json(receipt({restored:true,status:"restored",effective_status:"active",remaining_hold_ids:[]}))):saved&&request.path.endsWith("/status")?json({detail:"Follow-up read unavailable"},503):route(request);
    await draw();await screen.findByText(/Full runtime closure unverified/);await draft("Restore selected hold");submit();
    await screen.findByText(/Status unavailable: Follow-up read unavailable/);await loaded();
    assert.match(text(),/Saved decision confirmed/);assert.match(text(),/Account restriction reported with this decision: active/);
    assert.match(text(),/Account state at that read: suspended/);assert.match(text(),/Current account status could not be verified/);
    assert.match(text(),/Enforcement observation/);assert.match(text(),/observed 2026-10-21T00:01:00Z/);
    assert.match(text(),/Access at observation: restricted · 2 active hold/);
    assert.doesNotMatch(text(),/Current account state|Current access|request outcome is unverified/);
    assert.equal(posts().length,1);
});

test("initial pending status cannot invent a previous successful observation",async()=>{
    const pending=deferred<Response>();respond=request=>request.path.endsWith("/status")?pending.promise:route(request);
    view=render(panel());await waitFor(()=>assert.ok(requests.some(request=>request.path.endsWith("/status"))));
    const before=text();await act(async()=>pending.resolve(json(status())));await loaded();
    assert.doesNotMatch(before,/Last successful status read|Account state at that read|No formal holds were returned/);
});

test("successful partner status and unavailable enforcement remain separate observations",async()=>{
    current=status([event({is_active:false,restored_at:"2026-10-21T01:00:00Z"})]);
    respond=request=>request.path.includes("/propagation-status/")?json({detail:"Evidence read unavailable"},503):route(request);
    await draw();await screen.findByText(/Evidence unavailable: Evidence read unavailable/);
    assert.match(text(),/Account state at that read: active/);
    assert.doesNotMatch(text(),/Access at observation:|Current access:|confirmed · observed|Current account status could not be verified/);
});
