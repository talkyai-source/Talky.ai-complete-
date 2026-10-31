// Real ReactDOM; synthetic HTTP only. No storage/provider/database or native decoder.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, dom, state, render, response, find, text, click, settle, deferred } from './bh-harness.mjs';
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const callId=id(1), tenantId=id(2), stamp='2026-10-21T00:00:00Z';
const call={id:callId,tenant_id:tenantId,tenant_name:'Owned tenant',phone_number:'Owned call',status:'completed',timeline:[],created_at:stamp};
const common={call_id:callId,tenant_id:tenantId,tenant_name:'Owned tenant',created_at:stamp,legal_hold:false};
const recording={...common,id:id(3),status:'uploaded',mime_type:'audio/wav',storage:'local',playable:false,duration_seconds:3};
const feedback={...common,id:id(4),audio_mime_type:'audio/wav',audio_size_bytes:44,transcript:null,transcript_status:'failed',transcript_error:'Prior failure',transcription_attempts:1,retryable:true};
const page=items=>({items,total:items.length,page:1,page_size:20});
const history=items=>({items,has_more:false,next_cursor:null});
const surfaces=[
 {name:'recording table',component:'RecordingsTable',kind:'recording',item:recording,confirm:'Delete recording'},
 {name:'feedback table',component:'FeedbackTable',kind:'call_feedback',item:feedback,confirm:'Delete feedback'},
 {name:'recording drawer',drawer:true,tab:'Recordings',kind:'recording',item:recording,confirm:'Delete permanently'},
 {name:'feedback drawer',drawer:true,tab:'Feedback',kind:'call_feedback',item:feedback,confirm:'Delete permanently'},
];
const receipt=(s,extra={})=>({detail:'Saved permanent deletion',resource_type:s.kind,resource_id:s.item.id,outcome:'completed',deletion_intent_id:id(5),...extra});
const saved=(s,extra={})=>({id:id(5),resource_type:s.kind,resource_id:s.item.id,tenant_id:tenantId,call_id:callId,original_actor_id:id(6),reason:'Original reviewed reason',status:'completed',attempt_count:1,created_at:stamp,updated_at:stamp,object_deleted_at:stamp,completed_at:stamp,...extra});
let override, removed, readFailure;
const modal=()=>find('[aria-labelledby="modal-title"]');
const deletes=()=>state.requests.filter(r=>r.method==='DELETE');
test.beforeEach(()=>{
 override=null;removed=false;readFailure=false;
 state.route=r=>{
  const custom=override?.(r);if(custom!==undefined)return custom;
  const path=new URL(r.path,'https://synthetic.invalid').pathname;
  if(path.endsWith(`/admin/calls/${callId}`))return response(call);
  if(path.endsWith('/admin/recordings'))return readFailure?response({detail:'Inventory read unavailable'},503):response(page(removed?[]:[recording]));
  if(path.endsWith('/admin/feedback'))return readFailure?response({detail:'Inventory read unavailable'},503):response(page(removed?[]:[feedback]));
  if(path.endsWith('/admin/tenants'))return response([]);
  return response({detail:'Unrouted synthetic recovery request'},500);
 };
});
async function mount(s){
 await render(s.drawer?el(C.CallDetailDrawer,{callId,onClose(){}}):el(C[s.component],{onCallSelect(){}}));await settle();
 if(s.drawer){const tab=[...state.host.querySelectorAll('[role="tab"]')].find(n=>n.textContent.includes(s.tab));await act(async()=>tab.click());}
}
async function submit(s){
 await click('Delete');const input=modal().querySelector('textarea');
 await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(input,'Original reviewed reason');input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});
 await click(s.confirm);await settle();
}
async function changeStatus(value){const select=find('.filter-group select');await act(async()=>{select.value=value;select.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});await settle();}
for(const s of surfaces){
 test(`${s.name}: completed deletion survives unavailable inventory refresh without false empty`,async()=>{
  override=r=>{if(r.method==='DELETE'){readFailure=true;return response(receipt(s));}};
  await mount(s);await submit(s);
  assert.equal(Boolean(modal()),false);assert.match(text(),/Deletion confirmed/);assert.match(text(),/Inventory read unavailable/);
  assert.doesNotMatch(text(),/No recordings found|No feedback notes found|No recording is available|No reviewer feedback/);
  assert.equal(deletes().length,1);
 });
 test(`${s.name}: lost result remains recoverable after closing review and failed exact read`,async()=>{
  let fail=true;
  override=r=>r.method==='DELETE'?Promise.reject(new Error('Synthetic delivery loss')):r.path.includes('/admin/media-deletions')?fail?response({detail:'Saved read unavailable'},503):response(history([saved(s)])):undefined;
  await mount(s);await submit(s);assert.equal(Boolean(modal()),true);await click('Cancel');
  await click('Check saved deletion');await settle();assert.match(text(),/Deletion result unconfirmed/);assert.match(text(),/Saved read unavailable/);
  fail=false;await click('Check saved deletion');await settle();assert.match(text(),/Deletion confirmed/);assert.match(text(),/Server-saved deletion/);assert.match(text(),/Original reviewed reason/);
  assert.equal(deletes().length,1);
 });
 test(`${s.name}: remount with cleared storage discovers removed media from saved history only`,async()=>{
  removed=true;localStorage.clear();
  override=r=>r.path.endsWith(`/admin/media-deletions/${id(5)}`)?response(saved(s)):r.path.includes('/admin/media-deletions?')?response(history([saved(s)])):undefined;
  await mount(s);assert.equal(state.requests.some(r=>r.path.includes('/media-deletions')),false,'Discovery is deliberate read-only');
  await click('Load deletion history');await click('View saved deletion');await settle();
  assert.match(text(),/Server-saved deletion/);assert.match(text(),/Original operator/);assert.equal(deletes().length,0);
 });
}
for(const extra of [{resource_id:id(9)},{resource_type:'call_feedback'},{outcome:'completed',deletion_intent_id:null},{outcome:'absent',deletion_intent_id:id(5)}]){
 test(`recording receipt rejects wrong target/outcome ${JSON.stringify(extra)}`,async()=>{
  override=r=>r.method==='DELETE'?response(receipt(surfaces[0],extra)):undefined;
  await mount(surfaces[0]);await submit(surfaces[0]);assert.equal(Boolean(modal()),true);assert.match(text(),/Deletion result unconfirmed/);assert.equal(deletes().length,1);
 });
}
test('absent receipt is truthful absence, not confirmation of the original deletion',async()=>{
 override=r=>r.method==='DELETE'?response(receipt(surfaces[0],{outcome:'absent',deletion_intent_id:null})):undefined;
 await mount(surfaces[0]);await submit(surfaces[0]);assert.match(text(),/Media already absent/);assert.doesNotMatch(text(),/Deletion confirmed/);assert.match(text(),/does not confirm an earlier deletion/);
});
for(const change of [{attempt_count:0},{id:'bad-id'},{created_at:'2026-10-21'},{completed_at:{}},{resource_id:id(9)}]){
 test(`saved exact record rejects malformed or foreign result ${JSON.stringify(change)}`,async()=>{
  override=r=>r.method==='DELETE'?response({detail:'Unknown'},503):r.path.includes('/admin/media-deletions')?response(history([saved(surfaces[0],change)])):undefined;
  await mount(surfaces[0]);await submit(surfaces[0]);await click('Cancel');await click('Check saved deletion');await settle();
  assert.match(text(),/Deletion result unconfirmed/);assert.equal(Boolean(find('[aria-label="Server-saved deletion"]')),false);assert.equal(deletes().length,1);
 });
}
for(const stage of ['intent_committed','object_deleted','failed']){
 test(`saved ${stage} never certifies full completion`,async()=>{
  override=r=>r.method==='DELETE'?response({detail:'Unknown'},503):r.path.includes('/admin/media-deletions')?response(history([saved(surfaces[0],{status:stage,completed_at:null})])):undefined;
  await mount(surfaces[0]);await submit(surfaces[0]);await click('Cancel');await click('Check saved deletion');await settle();
  assert.match(text(),/Deletion result unconfirmed/);assert.doesNotMatch(text(),/Deletion confirmed/);assert.equal(deletes().length,1);
  if(stage==='failed')assert.match(text(),/a storage effect may already have occurred/);
 });
}
test('legacy saved completed status accepts nullable timestamps without inventing a time',async()=>{
 override=r=>r.path.endsWith(`/admin/media-deletions/${id(5)}`)?response(saved(surfaces[0],{object_deleted_at:null,completed_at:null})):r.path.includes('/admin/media-deletions?')?response(history([saved(surfaces[0])])):undefined;
 await mount(surfaces[0]);await click('Load deletion history');await click('View saved deletion');assert.match(text(),/original completion time is unavailable/);assert.match(text(),/Deletion completed/);
});
test('empty history cannot turn an unknown request into known no-effect',async()=>{
 override=r=>r.method==='DELETE'?response({detail:'Unknown'},503):r.path.includes('/admin/media-deletions')?response(history([])):undefined;
 await mount(surfaces[0]);await submit(surfaces[0]);await click('Cancel');await click('Check saved deletion');assert.match(text(),/does not prove the earlier request had no effect/);assert.match(text(),/Deletion result unconfirmed/);
});
test('completed known intent rejects a different intent and retains original receipt on failed reread',async()=>{
 override=r=>r.method==='DELETE'?response(receipt(surfaces[0])):r.path.includes('/admin/media-deletions')?response(saved(surfaces[0],{id:id(8)})):undefined;
 await mount(surfaces[0]);await submit(surfaces[0]);await click('Check saved deletion');assert.match(text(),/did not match/);assert.match(text(),/Deletion confirmed/);assert.equal(Boolean(find('[aria-label="Server-saved deletion"]')),false);
});
test('query change during saved read updates the same session notice without replaying deletion',async()=>{
 const pending=deferred();override=r=>r.method==='DELETE'?response({detail:'Unknown'},503):r.path.includes('/admin/media-deletions')?pending.promise:undefined;
 await mount(surfaces[0]);await submit(surfaces[0]);await click('Cancel');await click('Check saved deletion');await changeStatus('deleted');
 await act(async()=>pending.resolve(response(history([saved(surfaces[0])]))));await settle();assert.match(text(),/Deletion confirmed/);assert.doesNotMatch(text(),/Deletion result unconfirmed/);assert.equal(deletes().length,1);
});
test('session replacement retires pending saved read and private completed result',async()=>{
 const pending=deferred();override=r=>r.path.includes('/admin/media-deletions')?pending.promise:undefined;
 await mount(surfaces[0]);await click('Load deletion history');await act(async()=>api.setToken('replacement-session'));
 await act(async()=>pending.resolve(response(history([saved(surfaces[0])]))));assert.doesNotMatch(text(),/Original reviewed reason|View saved deletion/);
});
test('pagination failure preserves previous page and selected saved completion',async()=>{
 override=r=>r.path.endsWith(`/admin/media-deletions/${id(5)}`)?response(saved(surfaces[0])):r.path.includes('/admin/media-deletions?')?r.path.includes('cursor=')?response({detail:'Next history page unavailable'},503):response({items:[saved(surfaces[0])],has_more:true,next_cursor:'owned-next'}):undefined;
 await mount(surfaces[0]);await click('Load deletion history');await click('View saved deletion');await click('Next deletions');assert.match(text(),/Next history page unavailable/);assert.match(text(),/Server-saved deletion/);assert.match(text(),/Original reviewed reason/);assert.equal(deletes().length,0);
});
test('exact read started on old history page cannot replace selection after page change',async()=>{
 const pending=deferred();override=r=>r.path.endsWith(`/admin/media-deletions/${id(5)}`)?pending.promise:r.path.includes('/admin/media-deletions?')?r.path.includes('cursor=')?response(history([])):response({items:[saved(surfaces[0])],has_more:true,next_cursor:'owned-next'}):undefined;
 await mount(surfaces[0]);await click('Load deletion history');await click('View saved deletion');await click('Next deletions');await act(async()=>pending.resolve(response(saved(surfaces[0]))));
 assert.equal(Boolean(find('[aria-label="Server-saved deletion"]')),false);
});
test('validated retry outcome survives failed current-filter refresh',async()=>{
 override=r=>{if(r.method==='POST'){readFailure=true;return response({...feedback,transcript_status:'done',transcript:'Saved transcript',transcript_error:null,transcription_attempts:2,retryable:false});}};
 await mount(surfaces[1]);await changeStatus('failed');await click('Retry transcript');assert.match(text(),/Saved transcription result/);assert.match(text(),/done; attempt 2/);assert.match(text(),/Inventory read unavailable/);assert.doesNotMatch(text(),/No feedback notes found/);
 const reads=state.requests.filter(r=>r.path.includes('/admin/feedback?'));assert.equal(new URL(reads.at(-1).path,'https://synthetic.invalid').searchParams.get('transcript_status'),'failed');assert.equal(state.requests.filter(r=>r.method==='POST').length,1);
});
test('drawer prevents deletion while transcription retry is in flight, including retained callback',async()=>{
 const retry=deferred();override=r=>r.method==='POST'?retry.promise:r.method==='DELETE'?response(receipt(surfaces[3])):undefined;
 await mount(surfaces[3]);const originalDelete=[...state.host.querySelectorAll('button')].find(n=>n.textContent.trim()==='Delete');
 const propsKey=Object.keys(originalDelete).find(k=>k.startsWith('__reactProps$'));const retained=originalDelete[propsKey].onClick;
 await click('Retry');await act(async()=>retained());assert.equal(Boolean(modal()),false,'Retry must own media mutation while pending');
 await act(async()=>retry.resolve(response({...feedback,transcript_status:'done',transcript:'Done',transcript_error:null,transcription_attempts:2})));
 assert.equal(deletes().length,0);
});
test('drawer retires older media read before adopting a confirmed transcript retry',async()=>{
 const old=deferred();let reads=0;
 override=r=>{
  if(r.path.includes('/admin/recordings?'))return response({detail:'Recording unavailable'},503);
  if(r.path.includes('/admin/feedback?')){reads++;return reads===1?response(page([feedback])):old.promise;}
  if(r.method==='POST')return response({...feedback,transcript_status:'done',transcript:'New saved transcript',transcript_error:null,transcription_attempts:2,retryable:false});
 };
 await mount(surfaces[3]);await click('Retry media');await click('Retry');await act(async()=>old.resolve(response(page([feedback]))));
 assert.match(text(),/New saved transcript/);assert.doesNotMatch(text(),/Prior failure/);
});
