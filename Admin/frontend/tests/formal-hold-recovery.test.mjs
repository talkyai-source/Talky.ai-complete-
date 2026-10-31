// Actual React DOM, synthetic HTTP only; no account enforcement or provider effects.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, dom, state, render, response, find, text, click, settle } from './bh-harness.mjs';

const TARGET = '10000000-0000-4000-8000-000000000001';
const EVENT = '20000000-0000-4000-8000-000000000001';
const AUDIT = '30000000-0000-4000-8000-000000000001';
const ADMIN = '40000000-0000-4000-8000-000000000001';
const target = {type:'user',id:TARGET,label:'Owned recovery user'};
const failed = 'Synthetic lost formal action response';
const reason = 'Reviewed recovery reason';
const event = (extra = {}) => ({suspension_id:EVENT,target_type:'user',target_id:TARGET,suspension_type:'ADMIN',
    reason_category:'administrative',reason_description:'Original hold',suspended_at:'2026-10-21T00:00:00Z',
    suspended_until:null,suspended_by:ADMIN,restored_at:null,is_active:true,audit_log_id:AUDIT,
    appeal_submitted_at:null,appeal_reason:null,appeal_decision:null,appeal_response:null,restore_reason:null,evidence:null,...extra});
const status = events => ({target_type:'user',target_id:TARGET,status:events.some(e=>e.is_active)?'suspended':'active',
    is_suspended:events.some(e=>e.is_active),effective_status:events.some(e=>e.is_active)?'suspended':'active',
    remaining_hold_ids:events.filter(e=>e.is_active).map(e=>e.suspension_id),active_suspension:events.find(e=>e.is_active)??null,
    suspension_history:events,history_complete:true,history_limit:200});
const labels = {hold:'Create formal hold',restore:'Restore selected hold',appeal:'Submit operator appeal',review:'Review appeal'};
const editReason = async () => {
    const node=find('[aria-label="Formal action reason"]'); assert.ok(node);
    await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(node,reason);
        node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});
};

for (const kind of ['hold','restore','appeal','review']) {
    test(`${kind}: matching saved state retires the obsolete transport error without inventing a receipt`,async()=>{
        let events=kind==='hold'?[]:[event(kind==='review'?{appeal_decision:'pending',appeal_reason:'Existing appeal',appeal_submitted_at:'2026-10-21T00:01:00Z'}:{})];
        let sent;
        state.route=request=>{
            if(request.path.endsWith('/auth/me')) return response({id:ADMIN,email:'admin@example.invalid',role:'platform_admin',name:'Admin'});
            if(request.method==='POST') {
                sent=request;
                return response({detail:failed},503);
            }
            if(request.path.endsWith('/status')) return response(status(events));
            return response({detail:'Synthetic enforcement evidence unavailable'},503);
        };
        await render(el(C.AuthProvider,null,el(C.FormalHoldPanel,{target,onClose(){}}))); await settle();
        await click(labels[kind]); await editReason(); await click('Review action'); await click('Confirm formal action');
        assert.match(text(),/Action outcome unconfirmed/); assert.ok(text().includes(failed));
        if(kind==='hold') events=[event({reason_description:reason,evidence:sent.body.evidence})];
        if(kind==='restore') events=[event({is_active:false,restored_at:'2026-10-21T01:00:00Z',restore_reason:reason})];
        if(kind==='appeal') events=[event({appeal_reason:reason,appeal_submitted_at:'2026-10-21T01:00:00Z',appeal_decision:'pending'})];
        if(kind==='review') events=[event({appeal_reason:'Existing appeal',appeal_submitted_at:'2026-10-21T00:01:00Z',appeal_decision:'denied',appeal_response:reason})];
        await click('Refresh hold status'); await settle();
        assert.match(text(),/Matching saved state observed/);
        assert.doesNotMatch(text(),/Action outcome unconfirmed|Action saved:/);
        assert.equal(state.requests.filter(request=>request.method==='POST').length,1);
        assert.equal(text().includes(failed),false,'An exact observed outcome must replace the obsolete transport failure');
    });
}

for (const readCase of ['unavailable','not-matching']) {
    test(`restore: ${readCase} refresh retains the uncertain action and original error`,async()=>{
        let sent=false;
        state.route=request=>{
            if(request.path.endsWith('/auth/me')) return response({id:ADMIN,email:'admin@example.invalid',role:'platform_admin',name:'Admin'});
            if(request.method==='POST') {sent=true;return response({detail:failed},503);}
            if(request.path.endsWith('/status')) return sent&&readCase==='unavailable'?response({detail:'Status read unavailable'},503):response(status([event()]));
            return response({detail:'Synthetic enforcement evidence unavailable'},503);
        };
        await render(el(C.AuthProvider,null,el(C.FormalHoldPanel,{target,onClose(){}}))); await settle();
        await click('Restore selected hold'); await editReason(); await click('Review action'); await click('Confirm formal action');
        await click('Refresh hold status'); await settle();
        assert.match(text(),/Action outcome unconfirmed/); assert.ok(text().includes(failed));
        assert.doesNotMatch(text(),/Matching saved state observed|Action saved:/);
        assert.equal(state.requests.filter(request=>request.method==='POST').length,1);
    });
}

// Creation can be committed and later restored before its lost response is reconciled.
// Recovery observes the exact historical operation, independently of current access.
for (const targetType of ['user','tenant','partner']) {
    test(`${targetType}: a correlated creation already restored is observed without replay`,async()=>{
        const ownedTarget={...target,type:targetType};
        let events=[];
        const ownedStatus=()=>({...status(events),target_type:targetType});
        state.route=request=>{
            if(request.path.endsWith('/auth/me')) return response({id:ADMIN,email:'admin@example.invalid',role:'platform_admin',name:'Admin'});
            if(request.method==='POST') {
                events=[event({target_type:targetType,reason_description:reason,is_active:false,
                    restored_at:'2026-10-21T01:00:00Z',restore_reason:'Later independent restoration',
                    evidence:{...request.body.evidence,_formal_account_hold:{version:1,immediate:true}}})];
                return response({detail:failed},503);
            }
            if(request.path.endsWith('/status')) return response(ownedStatus());
            return response({detail:'Synthetic enforcement evidence unavailable'},503);
        };
        await render(el(C.AuthProvider,null,el(C.FormalHoldPanel,{target:ownedTarget,onClose(){}}))); await settle();
        await click('Create formal hold'); await editReason(); await click('Review action'); await click('Confirm formal action');
        await click('Refresh hold status'); await settle();
        assert.match(text(),/Matching saved state observed/);
        assert.match(text(),/Current effective status: active/);
        assert.match(text(),/Restored hold/);
        assert.match(text(),/Synthetic enforcement evidence unavailable/,'Unverified downstream evidence remains explicit');
        assert.doesNotMatch(text(),/Action outcome unconfirmed|Action saved:/);
        assert.equal(text().includes(failed),false);
        assert.equal(state.requests.filter(request=>request.method==='POST').length,1);
    });
}

for (const mismatch of ['nonce','actor','policy','duration','missing-restoration','invalid-restoration']) {
    test(`historical create: ${mismatch} cannot resolve the uncertain action`,async()=>{
        const ownedTarget={...target,type:'tenant'};
        let events=[];
        state.route=request=>{
            if(request.path.endsWith('/auth/me')) return response({id:ADMIN,email:'admin@example.invalid',role:'platform_admin',name:'Admin'});
            if(request.method==='POST') {
                const historical=event({target_type:'tenant',reason_description:reason,is_active:false,
                    restored_at:'2026-10-21T01:00:00Z',
                    evidence:{...request.body.evidence,_formal_account_hold:{version:1,immediate:true}}});
                if(mismatch==='nonce') historical.evidence.operator_request_id=EVENT;
                if(mismatch==='actor') historical.suspended_by=TARGET;
                if(mismatch==='policy') historical.evidence._formal_account_hold.immediate=false;
                if(mismatch==='duration') historical.suspended_until='2026-10-21T01:00:00Z';
                if(mismatch==='missing-restoration') historical.restored_at=null;
                if(mismatch==='invalid-restoration') historical.restored_at='invalid';
                events=[historical];
                return response({detail:failed},503);
            }
            if(request.path.endsWith('/status')) return response({...status(events),target_type:'tenant'});
            return response({detail:'Synthetic enforcement evidence unavailable'},503);
        };
        await render(el(C.AuthProvider,null,el(C.FormalHoldPanel,{target:ownedTarget,onClose(){}}))); await settle();
        await click('Create formal hold'); await editReason(); await click('Review action'); await click('Confirm formal action');
        await click('Refresh hold status'); await settle();
        assert.match(text(),/Action outcome unconfirmed/); assert.ok(text().includes(failed));
        assert.doesNotMatch(text(),/Matching saved state observed|Action saved:/);
        assert.equal(state.requests.filter(request=>request.method==='POST').length,1);
    });
}
