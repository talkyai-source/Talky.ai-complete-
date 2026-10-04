import React, {useSyncExternalStore} from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {notificationsStore, notificationStorageKeys, defaultNotificationsSettings} from '@/lib/notifications';
import {NotificationCenter} from '@/components/notifications/notification-center';
import {QualifiedLeadAlerts} from '@/components/notifications/qualified-lead-alerts';

const identities = {
  A:{tenantId:'00000000-0000-4000-8000-00000000000a',userId:'00000000-0000-4000-8000-00000000001a'},
  B:{tenantId:'00000000-0000-4000-8000-00000000000b',userId:'00000000-0000-4000-8000-00000000001b'},
};
const marker='cp07.synthetic.verified-identity';
const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
let late: ReturnType<typeof notificationsStore.capture> | null = null;
let lateResult: unknown = 'not captured';
let delayStorage = false;
const delayedStorage: StorageEvent[] = [];
window.addEventListener('storage', event => {
  if (delayStorage) { event.stopImmediatePropagation(); delayedStorage.push(event); }
}, true);
const fixture = {identity:'none',identities,events:{A:[],B:[]} as Record<string,any[]>,eventReads:0,networkAttempts:0,responseOwner:'current',holdNextRead:false,pendingRead:null as null|(()=>void),queryFetching:()=>client.isFetching({queryKey:['events']})};
(window as any).__cp07 = fixture;
window.fetch = async () => { fixture.networkAttempts++; renderStatus(); throw new Error('Harness blocks outbound fetch'); };
function applyIdentity(value:string) {
  fixture.identity = value === 'A' || value === 'B' ? value : 'none';
  notificationsStore.setIdentity(fixture.identity === 'none' ? null : identities[fixture.identity as 'A'|'B']);
  renderStatus();
}
function bind(value:string) { localStorage.setItem(marker,value); applyIdentity(value); }
window.addEventListener('storage',event=>{
  if(event.key===marker) { notificationsStore.suspendIdentity(); applyIdentity(event.newValue || 'none'); }
});
function createAlert() {
  notificationsStore.capture().create({type:'success',title:`Synthetic account ${fixture.identity} alert`,message:`Private fixture for ${fixture.identity}`,data:{fixture:true}});
}
function freshLead() {
  if(fixture.identity==='none') return;
  const id=crypto.randomUUID();
  fixture.events[fixture.identity] = [...fixture.events[fixture.identity],{id,category:'alert',title:`Qualified synthetic lead ${fixture.identity}`,description:'Synthetic follow-up request',severity:'info',related_campaign_id:null,related_call_id:null,actor_user_id:null,metadata:{kind:'qualified_lead',phone_number:'+15550001111'},created_at:new Date().toISOString()}];
  void client.invalidateQueries({queryKey:['events']});
}
function legacy() {
  const settings=defaultNotificationsSettings();
  settings.category.success.routing='both';settings.integrations.webhook={enabled:true,url:'https://never-send.example.invalid/hook'};settings.privacy.consentThirdParty=true;
  localStorage.setItem('talklee.notifications.v1',JSON.stringify([{id:'unowned',type:'success',priority:'normal',title:'UNOWNED LEGACY SECRET',createdAt:Date.now()}]));
  localStorage.setItem('talklee.notifications.settings.v1',JSON.stringify(settings));
  if(fixture.identity!=='none') {
    localStorage.setItem(notificationStorageKeys(identities[fixture.identity as 'A'|'B']).settings,JSON.stringify(settings));
    notificationsStore.hydrateIfNeeded();
    notificationsStore.setSettings(settings);
    createAlert();
  }
  renderStatus();
}
function renderStatus(){window.dispatchEvent(new Event('cp07-status'));}
function Status(){
  const snapshot=useSyncExternalStore(listener=>notificationsStore.subscribe(listener),()=>notificationsStore.getSnapshot());
  const text=JSON.stringify({syntheticIdentity:fixture.identity,scopeKey:snapshot.scopeKey,generation:snapshot.generation,hydrated:snapshot.hydrated,persistence:snapshot.persistence,storeHistory:snapshot.settings.privacy.storeHistory,titles:snapshot.notifications.map(n=>n.title),toastTitles:snapshot.toasts.map(n=>n.title),externalDestination:snapshot.settings.integrations.webhook,thirdPartyConsent:snapshot.settings.privacy.consentThirdParty,networkAttempts:fixture.networkAttempts,eventReads:fixture.eventReads,lateResult,delayStorage},null,2);
  return <pre id="status">{text}</pre>;
}
function App(){
  const [,refresh]=React.useReducer(x=>x+1,0);
  React.useEffect(()=>{window.addEventListener('cp07-status',refresh);return()=>window.removeEventListener('cp07-status',refresh);},[]);
  return <main><h1>CP07 browser isolation check</h1><p>Actual notification store, center, event query and qualified-lead observer. Synthetic verified identities and event source. No deployed login or external delivery.</p>
    <section aria-label="Synthetic controls"><button onClick={()=>bind('A')}>Bind verified A</button><button onClick={()=>bind('B')}>Bind verified B</button><button onClick={()=>bind('none')}>Logout all tabs</button><button onClick={createAlert}>Create current alert</button><button onClick={()=>{late=notificationsStore.capture();lateResult='captured '+fixture.identity;renderStatus();}}>Capture delayed action</button><button onClick={()=>{lateResult=late?.create({type:'error',title:'LATE A PRIVATE RESPONSE'});renderStatus();}}>Release delayed action</button><button onClick={freshLead}>Add qualified lead</button><button onClick={legacy}>Try legacy webhook settings</button></section>
    <section aria-label="Storage race controls"><button onClick={()=>{delayStorage=true;renderStatus();}}>Delay storage events</button><button onClick={()=>{delayStorage=false;for(const event of delayedStorage.splice(0))window.dispatchEvent(new StorageEvent('storage',{key:event.key,newValue:event.newValue,oldValue:event.oldValue,storageArea:event.storageArea,url:event.url}));renderStatus();}}>Release storage events</button><button onClick={()=>notificationsStore.setPrivacy({storeHistory:false})}>Disable persisted history</button></section>
    <section aria-label="Event response controls"><button onClick={()=>{fixture.responseOwner='other';}}>Return foreign event owner</button><button onClick={()=>{fixture.responseOwner='missing';}}>Return missing event owner</button><button onClick={()=>{fixture.responseOwner='current';}}>Return current event owner</button><button onClick={()=>{fixture.holdNextRead=true;}}>Hold next event read</button><button onClick={()=>fixture.pendingRead?.()}>Release held event read</button></section>
    <section aria-label="Actual notification center"><NotificationCenter maxHeightClassName="" /></section><section aria-label="Fixture state"><Status /></section><QualifiedLeadAlerts />
  </main>;
}
applyIdentity(localStorage.getItem(marker)||'none');
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={client}><App/></QueryClientProvider>);
