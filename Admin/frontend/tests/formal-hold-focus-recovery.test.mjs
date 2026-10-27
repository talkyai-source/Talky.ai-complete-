// Actual React DOM with synthetic HTTP; native baseline is recorded separately.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, dom, state, render, response, find, text, click, settle, deferred, button } from './bh-harness.mjs';

const TARGET='10000000-0000-4000-8000-000000000001',OTHER='10000000-0000-4000-8000-000000000002';
const EVENT='20000000-0000-4000-8000-000000000001',AUDIT='30000000-0000-4000-8000-000000000001',ADMIN='40000000-0000-4000-8000-000000000001';
const reason='Reviewed formal focus recovery',failure='Synthetic lost formal response';
const target=(type='user',id=TARGET)=>({type,id,label:'Reviewed account'});
const event=(extra={})=>({suspension_id:EVENT,target_type:'user',target_id:TARGET,suspension_type:'ADMIN',reason_category:'administrative',
  reason_description:'Original hold',suspended_at:'2026-10-21T00:00:00Z',suspended_until:null,suspended_by:ADMIN,restored_at:null,is_active:true,
  audit_log_id:AUDIT,appeal_submitted_at:null,appeal_reason:null,appeal_decision:null,appeal_response:null,restore_reason:null,evidence:null,...extra});
const status=(events=[],type='user',id=TARGET)=>({target_type:type,target_id:id,status:events.some(e=>e.is_active)?'suspended':'active',
  is_suspended:events.some(e=>e.is_active),effective_status:events.some(e=>e.is_active)?'suspended':'active',remaining_hold_ids:events.filter(e=>e.is_active).map(e=>e.suspension_id),
  active_suspension:events.find(e=>e.is_active)??null,suspension_history:events,history_complete:true,history_limit:200});
const tenant={id:TARGET,business_name:'Reviewed Alpha',status:'active',plan_id:null,plan_name:null,minutes_used:0,minutes_allocated:100,
  user_count:1,campaign_count:0,max_concurrent_calls:1,created_at:null,updated_at:'2026-10-21T00:00:00Z'};
let current,post,sent,role;
const route=request=>{
  if(request.path.endsWith('/auth/me'))return response({id:ADMIN,email:'admin@example.invalid',role,name:'Admin'});
  if(request.method==='POST'){sent=request;return post(request);}
  if(request.path.endsWith('/status'))return response(current);
  return response({detail:'Synthetic downstream evidence unavailable'},503);
};
test.beforeEach(()=>{current=status();sent=null;role='platform_admin';post=()=>response({detail:failure},503);state.route=route;});
const draw=async(extra={},t=target())=>{await render(el(C.AuthProvider,null,el(C.FormalHoldPanel,{target:t,onClose(){},...extra})));await settle();};
const enterReason=async()=>{const node=find('[aria-label="Formal action reason"]');await act(async()=>{
  Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(node,reason);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});};
const draft=async(label='Create formal hold')=>{await click(label);await enterReason();await click('Review action');};
const usableFocus=()=>{const active=document.activeElement,dialog=find('[role="dialog"]');return Boolean(active?.isConnected&&dialog?.contains(active)&&!active.matches(':disabled'));};
const posts=()=>state.requests.filter(r=>r.method==='POST');
const handler=node=>node[Object.keys(node).find(k=>k.startsWith('__reactProps'))].onClick;
const escape=async()=>act(async()=>document.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})));
const recovered=(kind,type='user')=>kind==='hold'?event({target_type:type,reason_description:reason,evidence:{...sent.body.evidence,...(type==='tenant'?{_formal_account_hold:{version:1,immediate:true}}:{})}})
  :kind==='restore'?event({is_active:false,restored_at:'2026-10-21T01:00:00Z',restore_reason:reason})
  :kind==='appeal'?event({appeal_reason:reason,appeal_submitted_at:'2026-10-21T01:00:00Z',appeal_decision:'pending'})
  :event({appeal_reason:'Existing appeal',appeal_submitted_at:'2026-10-21T00:01:00Z',appeal_decision:'denied',appeal_response:reason});
const initial=kind=>kind==='hold'?status():status([event(kind==='review'?{appeal_decision:'pending',appeal_reason:'Existing appeal',appeal_submitted_at:'2026-10-21T00:01:00Z'}:{})]);
const labels={hold:'Create formal hold',restore:'Restore selected hold',appeal:'Submit operator appeal',review:'Review appeal'};

for(const action of ['Review action','Edit draft','Cancel draft'])test(`${action} removal preserves usable focus inside the formal dialog`,async()=>{
  await draw();await click('Create formal hold');await enterReason();if(action==='Edit draft')await click('Review action');
  button(action).focus();await click(action);assert.equal(usableFocus(),true,'Removed form controls must hand focus to a connected enabled dialog target');
});

for(const result of ['saved','unknown'])test(`focused Confirm stays usable through deferred ${result} response`,async()=>{
  const late=deferred();post=()=>late.promise;await draw();await draft();button('Confirm formal action').focus();await click('Confirm formal action');
  const during=usableFocus();
  current=status([recovered('hold')]);
  await act(async()=>late.resolve(result==='saved'?response({suspension_id:EVENT,target_type:'user',target_id:TARGET,status:'suspended',audit_event_id:AUDIT}):response({detail:failure},503)));
  await settle();assert.equal(during,true,'Focus must move before the pending confirmation is disabled');assert.equal(usableFocus(),true);
  assert.match(text(),result==='saved'?/Action saved:/:/Action outcome unconfirmed/);assert.equal(posts().length,1);
});

for(const result of ['success','failure'])test(`manual Refresh hands focus off before loading and preserves a later Close focus after ${result}`,async()=>{
  await draw();const late=deferred();state.route=r=>r.path.endsWith('/status')?late.promise:route(r);
  button('Refresh hold status').focus();await click('Refresh hold status');const during=usableFocus();
  const close=find('[aria-label="Close formal holds"]');close.focus();
  await act(async()=>late.resolve(result==='success'?response(current):response({detail:'Synthetic read failure'},503)));await settle();
  assert.equal(during,true,'Refresh may not leave its now-disabled button focused');assert.equal(document.activeElement===close,true,'Async completion must not steal deliberate focus');
  assert.equal(posts().length,0);
});

test('retained Edit callback does not move deliberate focus outside the removed review region',async()=>{
  await draw();await draft();const edit=handler(button('Edit draft')),close=find('[aria-label="Close formal holds"]');close.focus();
  await act(async()=>edit());assert.equal(document.activeElement===close,true);assert.equal(posts().length,0);
});

for(const kind of ['hold','restore','appeal','review'])test(`${kind}: exact observed recovery notifies parent once across repeated reads and reopen`,async()=>{
  current=initial(kind);let changed=0;const onChanged=()=>changed++;await draw({onChanged});await draft(labels[kind]);await click('Confirm formal action');
  assert.equal(changed,0);current=status([recovered(kind)]);await click('Refresh hold status');await settle();
  const afterFirst=changed;await click('Refresh hold status');await render(el('p',null,'Closed'));await draw({onChanged});
  assert.equal(afterFirst,1,'Exact recovered outcome must refresh the owning directory');assert.equal(changed,1,'Observed cache must prevent repeated notifications');
  assert.match(text(),/Matching saved state observed/);assert.doesNotMatch(text(),/Action saved:/);assert.equal(posts().length,1);
});

for(const read of ['failed','mismatched'])test(`${read} recovery never notifies parent or clears uncertainty`,async()=>{
  let changed=0;await draw({onChanged:()=>changed++});await draft();await click('Confirm formal action');
  if(read==='failed')state.route=r=>r.path.endsWith('/status')?response({detail:'Read failed'},503):route(r);
  else current=status([event({reason_description:reason,evidence:{operator_request_id:OTHER}})]);
  await click('Refresh hold status');assert.equal(changed,0);assert.match(text(),/Action outcome unconfirmed/);assert.ok(text().includes(failure));
});

test('reconciliation uses the latest committed callback without notifying the replaced callback',async()=>{
  let old=0,latest=0;const owned=target();await draw({onChanged:()=>old++},owned);await draft();await click('Confirm formal action');
  const late=deferred();state.route=r=>r.path.endsWith('/status')?late.promise.then(v=>v.clone()):route(r);await click('Refresh hold status');
  const before=state.requests.filter(r=>r.path.endsWith('/status')).length;
  await draw({onChanged:()=>latest++},owned);assert.equal(state.requests.filter(r=>r.path.endsWith('/status')).length,before,'Callback replacement must not restart status reads');
  current=status([recovered('hold')]);await act(async()=>late.resolve(response(current)));await settle();
  assert.equal(old,0);assert.equal(latest,1);assert.match(text(),/Matching saved state observed/);
});

for(const retire of ['unmount','session'])test(`late matching recovery after ${retire} cannot notify or change the new focus`,async()=>{
  let changed=0;await draw({onChanged:()=>changed++});await draft();await click('Confirm formal action');
  const late=deferred();state.route=r=>r.path.endsWith('/status')?late.promise.then(v=>v.clone()):route(r);await click('Refresh hold status');
  if(retire==='session'){role='readonly';await act(async()=>api.setToken('replacement-identity'));}
  await render(el('button',{id:'replacement-focus'},'Replacement view'));const replacement=find('#replacement-focus');replacement.focus();
  current=status([recovered('hold')]);await act(async()=>late.resolve(response(current)));await settle();
  assert.equal(changed,0);assert.equal(document.activeElement===replacement,true);assert.doesNotMatch(text(),/Matching saved state observed/);
});

for(const type of ['user','tenant'])for(const delivery of ['unknown','saved'])test(`${type} real page refreshes after ${delivery} hold and returns Escape from removed opener to stable search`,async()=>{
  current=status([],type);let directoryReads=0,held=false;
  const directoryWait=deferred();
  const user=()=>({id:TARGET,name:'Reviewed user',email:'person@example.invalid',role:'user',is_active:!held});
  if(delivery==='saved')post=()=>{held=true;current=status([recovered('hold',type)],type);return response({suspension_id:EVENT,target_type:type,target_id:TARGET,status:'suspended',audit_event_id:AUDIT});};
  state.route=r=>{
    if(r.path.includes('/admin/users/operations?'))return response({items:[],next_cursor:null});
    if(r.path.startsWith('/api/v1/admin/users?')){directoryReads++;return held?directoryWait.promise.then(()=>response([user()])):response([user()]);}
    if(r.path.startsWith('/api/v1/admin/tenants')){if(type==='tenant')directoryReads++;return held&&type==='tenant'?directoryWait.promise.then(()=>response([tenant])):response([tenant]);}
    if(r.path.endsWith('/rbac/roles'))return response([{id:'user',name:'user',level:1}]);
    if(r.path.endsWith('/rbac/permissions'))return response([]);
    return route(r);
  };
  await render(el(C.AuthProvider,null,el(C.MemoryRouter,null,el(type==='user'?C.UsersPage:C.TenantsPage))));await settle(350);
  const opener=find(type==='user'?'[aria-label="Formal holds for person@example.invalid"]':'[aria-label="Actions for Reviewed Alpha"]');assert.ok(opener);opener.focus();
  await act(async()=>opener.click());if(type==='tenant')await click('Formal holds');await settle();
  await draft();const before=directoryReads;await click('Confirm formal action');
  if(delivery==='unknown'){held=true;current=status([recovered('hold',type)],type);await click('Refresh hold status');}
  const refreshed=directoryReads>before,openerRemoved=!opener.isConnected;
  await act(async()=>directoryWait.resolve());await settle(350);
  const currentRow=type==='user'?Boolean(find('[aria-label="Unblock person@example.invalid"]')):find('tbody tr .status-badge')?.textContent==='Active';
  await escape();const active=document.activeElement;
  assert.equal(refreshed,true,'Exact recovery must trigger the real page directory GET');assert.equal(currentRow,true,'Current user block reflects hold; tenant subscription stays separately Active');
  assert.equal(openerRemoved,true,'The actual pending directory read removes its invoking row');
  assert.equal(active?.getAttribute('aria-label'),type==='user'?'Search users':'Search tenants','Removed opener must return to a stable labelled search');
  assert.equal(posts().length,1);
});
