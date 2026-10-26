import assert from 'node:assert/strict';
import test from 'node:test';
import { useState } from 'react';
import { C, api, el, act, dom, state, render, response, find, text, click, settle, deferred, button } from './bh-harness.mjs';

const A = '10000000-0000-4000-8000-000000000001', B = '10000000-0000-4000-8000-000000000002';
const E = '20000000-0000-4000-8000-000000000001', F = '20000000-0000-4000-8000-000000000002';
const AUDIT = '30000000-0000-4000-8000-000000000001', ADMIN = '40000000-0000-4000-8000-000000000001';
const target = (id = A, type = 'tenant') => ({ id, type, label: id === A ? 'Reviewed Alpha' : 'Reviewed Beta' });
const event = (extra = {}) => ({ suspension_id: E, target_type: 'tenant', target_id: A, suspension_type: 'ADMIN',
    reason_category: 'administrative', reason_description: 'Original restriction', suspended_at: '2026-10-21T00:00:00Z',
    suspended_until: null, suspended_by: ADMIN, restored_at: null, is_active: true, audit_log_id: AUDIT,
    appeal_submitted_at: null, appeal_reason: null, appeal_decision: null, appeal_response: null, restore_reason: null, evidence: null, ...extra });
const status = (events = [], t = target(), extra = {}) => ({ target_type: t.type, target_id: t.id, status: 'active', is_suspended: events.some((e) => e.is_active),
    active_suspension: events.find((e) => e.is_active) ?? null, suspension_history: events, effective_status: events.some((e) => e.is_active) ? 'suspended' : 'active',
    remaining_hold_ids: events.filter((e) => e.is_active).map((e) => e.suspension_id), history_complete: true, history_limit: 200, ...extra });
const proof = (id = E, t = target()) => ({ version: 1, suspension_id: id, target_type: t.type, target_id: t.id, event_state: 'active',
    policy_cutoff: '2026-10-21T00:00:00Z', observed_at_db: '2026-10-21T00:01:00Z', original_audit_event_id: AUDIT,
    current_authority: { status: 'restricted', remaining_hold_ids: [id] }, propagation_status: 'unverified',
    dimensions: ['credentials', 'assistant_runtimes', 'telephony'].map((key) => ({ key, status: key === 'telephony' ? 'exempt_by_policy' : 'unverified', coverage: key === 'telephony' ? 'complete' : 'unknown',
        counts: { captured: 1, observed: 1, confirmed: 0, pending: 0, exempt: key === 'telephony' ? 1 : 0, unknown: key === 'telephony' ? 0 : 1 }, reasons: ['Historical coverage is incomplete'] })), reconciliation_reasons: ['No full runtime inventory'] });
const saved = (id = E, t = target(), extra = {}) => ({ suspension_id: id, target_type: t.type, target_id: t.id,
    audit_event_id: AUDIT, status: 'suspended', changed: true, ...extra });
let current, currentProof, role, post;
const route = (r) => {
    if (r.path.endsWith('/auth/me')) return response({ id: ADMIN, email: 'admin@example.invalid', role, name: 'Administrator' });
    if (r.method === 'POST') return post(r);
    if (r.path.includes('/propagation-status/')) return response(currentProof ?? proof(r.path.split('/').at(-1)));
    if (r.path.endsWith('/status')) return response(current);
    return response({ detail: 'unexpected synthetic request' }, 500);
};
const draw = async (t = target(), extra = {}) => {
    await render(el(C.AuthProvider, null, el(C.FormalHoldPanel, { target: t, onClose() {}, ...extra }))); await settle();
};
const edit = async (label, value) => {
    const node = find(`[aria-label="${label}"]`); assert.ok(node, label);
    await act(async () => {
        const prototype = node.tagName === 'TEXTAREA' ? dom.window.HTMLTextAreaElement.prototype : node.tagName === 'SELECT' ? dom.window.HTMLSelectElement.prototype : dom.window.HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(prototype, 'value').set.call(node, value);
        node.dispatchEvent(new dom.window.Event(node.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
    });
};
const draft = async (action, reason = 'Reviewed reason') => { await click(action); await edit('Formal action reason', reason); await click('Review action'); };
const posts = () => state.requests.filter((r) => r.method === 'POST');
const callback = (node) => node[Object.keys(node).find((k) => k.startsWith('__reactProps'))].onClick;
test.beforeEach(() => { current = status(); currentProof = null; role = 'platform_admin'; post = () => response(saved()); state.route = route; });

test('formal controls fail closed for a non-platform role without requesting protected data', async () => {
    role = 'partner_admin'; await draw(); assert.match(text(), /current platform administrator is required/);
    assert.equal(state.requests.filter((r) => r.path.includes('/admin/suspensions/')).length, 0);
});
test('failed and mismatched reads never look like an empty successful history', async () => {
    state.route = (r) => r.path.endsWith('/status') ? response({ detail: 'database offline' }, 503) : route(r);
    await draw(); assert.match(text(), /Status unavailable: database offline/); assert.doesNotMatch(text(), /No formal hold history/);
    state.route = route; current = status([], target(B)); await click('Refresh hold status');
    assert.match(text(), /does not match this account/); assert.equal(find('form'), null);
});
test('empty and truncated history are explicit and ordinary controls remain separate', async () => {
    current = status([], target(), { history_complete: false }); await draw();
    assert.match(text(), /No direct active formal hold/); assert.match(text(), /History is limited to 200/);
    assert.match(text(), /Ordinary user blocks and tenant subscription controls are separate/);
});
test('malformed effective status is rejected before rendering privileged data', async () => {
    current = status([], target(), { effective_status: {} }); await draw();
    assert.match(text(), /Status unavailable/); assert.doesNotMatch(text(), /No formal hold history/);
});
test('a new status read retires the previously reviewed confirmation callback', async () => {
    current = status([event()]); await draw(); await draft('Restore selected hold'); const retained = callback(button('Confirm formal action'));
    await click('Refresh hold status'); await act(async () => retained());
    assert.equal(posts().length, 0); assert.equal(find('[aria-label="Review formal action"]'), null);
    assert.match(text(), /Review the action again/);
});
test('two-step tenant create submits reviewed policy exactly and dispatches once', async () => {
    const pending = deferred(); post = () => pending.promise; await draw(); await click('Create formal hold');
    await edit('Hold type', 'TEMPORARY'); await edit('Duration in hours', '6'); await edit('Formal action reason', 'Security review');
    await act(async () => find('input[type="checkbox"]').click()); await click('Review action');
    assert.match(text(), /Established-call policy exception requested/);
    await act(async () => { button('Confirm formal action').click(); button('Confirm formal action').click(); });
    assert.equal(posts().length, 1); assert.match(posts()[0].path, /tenants\/.*\/suspend\?immediate=false$/);
    assert.match(posts()[0].body.evidence.operator_request_id, /^[0-9a-f-]{36}$/);
    assert.deepEqual(posts()[0].body, { suspension_type: 'TEMPORARY', reason_category: 'administrative', reason_description: 'Security review', duration_hours: 6, notify_user: false, evidence: posts()[0].body.evidence });
    await act(async () => pending.resolve(response(saved()))); assert.match(text(), /Action saved: Create formal hold/);
});
for (const kind of ['user', 'tenant', 'partner']) test(`${kind} restore sends the exact reviewed event, with receipt retained after failed reload`, async () => {
    const t = target(A, kind); current = status([event({ target_type: kind })], t); let fail = false;
    post = () => { fail = true; return response(saved(E, t, { restored: true, status: 'restored' })); };
    state.route = (r) => fail && r.method === 'GET' && r.path.endsWith('/status') ? response({ detail: 'reload unavailable' }, 503) : route(r);
    await draw(t); await draft('Restore selected hold'); await click('Confirm formal action');
    assert.deepEqual(posts()[0].body, { expected_suspension_id: E, reason: 'Reviewed reason' });
    assert.match(posts()[0].path, new RegExp(`/${kind}s/${A}/restore$`));
    assert.match(text(), /Action saved: Restore selected hold/); assert.match(text(), /reload unavailable/);
    assert.equal(button('Restore selected hold').disabled, true);
});
test('a mismatched saved receipt stays unknown and is not automatically resent', async () => {
    current = status([event()]); post = () => response(saved(F, target(), { restored: true })); await draw();
    await draft('Restore selected hold'); await click('Confirm formal action'); await click('Refresh hold status');
    assert.match(text(), /Action outcome unconfirmed/); assert.doesNotMatch(text(), /Action saved:/); assert.equal(posts().length, 1);
    assert.equal(button('Restore selected hold').disabled, true);
});
test('a committed receipt wins over stale active status and a retained confirm callback cannot repeat it', async () => {
    current = status([event()]); post = () => response(saved(E, target(), { restored: true })); await draw();
    await draft('Restore selected hold'); const retained = callback(button('Confirm formal action')); await click('Confirm formal action');
    assert.equal(button('Restore selected hold').disabled, true); await act(async () => retained());
    await click('Refresh hold status'); assert.equal(button('Restore selected hold').disabled, true);
    assert.equal(posts().length, 1); assert.match(text(), /Action saved: Restore selected hold/);
});
test('a definite server conflict clears the draft but requires current state before a fresh action', async () => {
    current = status([event()]); post = () => { current = status([event({ suspension_id: F })]); return response({ detail: 'Reviewed hold replaced' }, 409); };
    await draw(); await draft('Restore selected hold'); await click('Confirm formal action');
    assert.match(text(), /Reviewed hold replaced/); assert.doesNotMatch(text(), /Action saved:/); assert.equal(find('[aria-label="Review formal action"]'), null);
    assert.equal(button('Restore selected hold').disabled, true); assert.equal(posts().length, 1);
});
test('unknown restore remains blocked across reopen until the exact event is observed restored', async () => {
    current = status([event()]); post = () => response({ detail: 'lost response' }, 503); await draw();
    await draft('Restore selected hold'); await click('Confirm formal action');
    await render(el(C.AuthProvider, null, el('p', null, 'Closed'))); await draw();
    assert.match(text(), /Action outcome unconfirmed/); await click('Refresh hold status'); assert.equal(posts().length, 1);
    current = status([event({ is_active: false, restored_at: '2026-10-21T01:00:00Z' })]); await click('Refresh hold status');
    assert.match(text(), /Matching saved state observed/); assert.doesNotMatch(text(), /Action saved:/);
    assert.equal(find('[aria-label="Review formal action"]'), null); assert.equal(button('Restore selected hold').disabled, true);
});
test('unknown create is not cleared by merely successful empty status', async () => {
    post = () => response({ detail: 'timeout' }, 503); await draw(); await draft('Create formal hold'); await click('Confirm formal action');
    await click('Refresh hold status'); assert.match(text(), /Action outcome unconfirmed/); assert.equal(button('Create formal hold').disabled, true);
    current = status([event({ reason_description: 'Different operation' })]); await click('Refresh hold status');
    assert.match(text(), /Action outcome unconfirmed/); assert.equal(posts().length, 1);
});
test('unknown create requires exact correlation as well as actor/target/reason and does not invent original receipt', async () => {
    post = () => response({ detail: 'timeout' }, 503); await draw(); await draft('Create formal hold'); await click('Confirm formal action');
    current = status([event({ reason_description: 'Reviewed reason' })]); await click('Refresh hold status');
    assert.match(text(), /Action outcome unconfirmed/);
    current = status([event({ reason_description: 'Reviewed reason', evidence: { ...posts()[0].body.evidence, _formal_account_hold: { version: 1, immediate: true } } })]); await click('Refresh hold status');
    assert.match(text(), /Matching saved state observed/); assert.doesNotMatch(text(), /Action saved:/); assert.equal(posts().length, 1);
    assert.equal(button('Create formal hold').disabled, true);
});
for (const hours of [undefined, 6]) test(`unknown create checks the exact ${hours ?? 'indefinite'} duration before reconciliation`, async () => {
    post = () => response({ detail: 'timeout' }, 503); await draw(); await click('Create formal hold');
    if (hours !== undefined) { await edit('Hold type', 'TEMPORARY'); await edit('Duration in hours', String(hours)); }
    await edit('Formal action reason', 'Reviewed duration'); await click('Review action'); await click('Confirm formal action');
    const recorded = { reason_description: 'Reviewed duration', suspension_type: hours === undefined ? 'ADMIN' : 'TEMPORARY', evidence: { ...posts()[0].body.evidence, _formal_account_hold: { version: 1, immediate: true } } };
    current = status([event({ ...recorded, suspended_until: '2026-10-21T07:00:00Z' })]); await click('Refresh hold status');
    assert.match(text(), /Action outcome unconfirmed/); assert.doesNotMatch(text(), /Matching saved state observed/);
    current = status([event({ ...recorded, suspended_until: hours === undefined ? null : '2026-10-21T06:00:00Z' })]); await click('Refresh hold status');
    assert.match(text(), /Matching saved state observed/); assert.equal(posts().length, 1);
});

for (const mismatch of ['policy missing', 'policy version', 'immediate', 'supporting note']) test(`unknown tenant create rejects a changed ${mismatch}`, async () => {
    post = () => response({ detail: 'timeout' }, 503); await draw(); await click('Create formal hold');
    await edit('Formal action reason', 'Reviewed policy'); await edit('Supporting note', 'Reviewed supporting details');
    await act(async () => find('input[type="checkbox"]').click()); await click('Review action'); await click('Confirm formal action');
    const evidence = { ...posts()[0].body.evidence, _formal_account_hold: { version: 1, immediate: false } };
    const mismatched = { ...evidence, ...(mismatch === 'supporting note' ? { operator_note: 'Different details' } : {
        _formal_account_hold: mismatch === 'policy missing' ? undefined : { version: mismatch === 'policy version' ? 0 : 1, immediate: mismatch === 'immediate' },
    }) };
    current = status([event({ reason_description: 'Reviewed policy', evidence: mismatched })]); await click('Refresh hold status');
    assert.match(text(), /Action outcome unconfirmed/); assert.doesNotMatch(text(), /Matching saved state observed/);
    current = status([event({ reason_description: 'Reviewed policy', evidence })]); await click('Refresh hold status');
    assert.match(text(), /Matching saved state observed/); assert.equal(posts().length, 1);
});

test('operator appeal is bound to the selected hold and displays publication separately', async () => {
    current = status([event()]); post = () => response(saved(E, target(), { appeal_submitted: true, status: 'pending_review', hint_status: 'failed', notification_delivery_status: 'unverified' }));
    await draw(); await draft('Submit operator appeal', 'Reviewed customer appeal'); await click('Confirm formal action');
    assert.equal(posts()[0].path, `/api/v1/admin/suspensions/${E}/appeal`); assert.deepEqual(posts()[0].body, { appeal_reason: 'Reviewed customer appeal' });
    assert.match(text(), /Action saved: Submit operator appeal/); assert.match(text(), /notification delivery/);
});
for (const decision of ['granted', 'denied']) test(`appeal ${decision} requires review and matched decision receipt`, async () => {
    current = status([event({ appeal_decision: 'pending', appeal_reason: 'Original appeal', appeal_submitted_at: '2026-10-21T00:01:00Z' })]);
    post = () => response(saved(E, target(), { decision, restored: decision === 'granted' })); await draw();
    await click('Review appeal'); await edit('Appeal decision', decision); await edit('Formal action reason', 'Reviewed decision'); await click('Review action'); await click('Confirm formal action');
    assert.deepEqual(posts()[0].body, { decision, response: 'Reviewed decision' }); assert.match(text(), /Action saved: Review appeal/);
});
test('a restored event with a restricted current account renders both facts and dimension counts', async () => {
    current = status([event({ is_active: false, restored_at: '2026-10-21T00:30:00Z' })]); currentProof = { ...proof(), event_state: 'restored' };
    await draw(); assert.match(text(), /Selected event: restored · Current authority: restricted/);
    assert.match(text(), /Coverage: unknown\. Captured 1; observed 1; confirmed 0/);
    assert.match(text(), /telephony: exempt by policy/); assert.match(text(), /No full runtime inventory/);
});
test('mismatched and malformed evidence is unavailable rather than green', async () => {
    current = status([event()]); currentProof = proof(F); await draw(); assert.match(text(), /evidence does not match/);
    currentProof = { ...proof(), dimensions: [] }; await click('Refresh hold status'); assert.match(text(), /evidence does not match/);
    currentProof = { ...proof(), propagation_status: 'confirmed' }; await click('Refresh hold status'); assert.match(text(), /evidence does not match/);
    assert.equal(find('[aria-label="credentials evidence"]'), null);
});
for (const mismatch of ['aggregate pending', 'confirmed', 'exempt_by_policy', 'not_applicable']) test(`evidence summary rejects ${mismatch}`, async () => {
    current = status([event()]);
    currentProof = mismatch === 'aggregate pending' ? { ...proof(), propagation_status: 'pending' }
        : { ...proof(), propagation_status: 'confirmed', dimensions: proof().dimensions.map((d) => ({ ...d, status: mismatch, coverage: 'complete' })) };
    await draw(); assert.match(text(), /Evidence unavailable/);
    assert.equal(find('[aria-label="credentials evidence"]'), null);
});
test('late A read and old confirmation callback cannot modify selected B', async () => {
    current = status([event()]); await draw(); await draft('Restore selected hold'); const retained = callback(button('Confirm formal action'));
    const late = deferred(); state.route = (r) => r.path.endsWith(`/tenants/${A}/status`) ? late.promise.then((v) => v.clone()) : route(r);
    await click('Refresh hold status'); current = status([], target(B)); await draw(target(B));
    await act(async () => retained()); assert.equal(posts().length, 0);
    await act(async () => late.resolve(response(status([event({ reason_description: 'Old private details' })]))));
    assert.match(text(), /Reviewed Beta/); assert.doesNotMatch(text(), /Old private details|Original restriction/);
});
test('late selected event evidence cannot replace newer selection', async () => {
    current = status([event(), event({ suspension_id: F, is_active: false, restored_at: '2026-10-21T01:00:00Z' })]);
    const late = deferred(); state.route = (r) => r.path.endsWith(`/propagation-status/${E}`) ? late.promise.then((v) => v.clone()) : route(r);
    await draw(); await edit('Reviewed hold', F);
    await act(async () => late.resolve(response({ ...proof(), reconciliation_reasons: ['obsolete private evidence'] })));
    assert.doesNotMatch(text(), /obsolete private evidence/); assert.equal(find('[aria-label="Reviewed hold"]').value, F);
});
for (const exit of ['unmount', 'session']) test(`late mutation continuation is retired on ${exit}`, async () => {
    const late = deferred(); let changed = 0; post = () => late.promise; await draw(target(), { onChanged() { changed += 1; } });
    await draft('Create formal hold'); await click('Confirm formal action');
    if (exit === 'unmount') await render(el('p', null, 'Different page'));
    else { role = 'readonly'; await act(async () => api.setToken('different-principal')); }
    await act(async () => late.resolve(response(saved()))); assert.equal(changed, 0); assert.doesNotMatch(text(), /Action saved:/);
});
test('dialog contains keyboard focus, closes with Escape and restores the invoking control', async () => {
    function Screen() {
        const [open, setOpen] = useState(false);
        return el(C.AuthProvider, null, el('button', { onClick: () => setOpen(true) }, 'Open holds'), open && el(C.FormalHoldPanel, { target: target(), onClose: () => setOpen(false) }));
    }
    await render(el(Screen)); await settle(); const opener = button('Open holds'); opener.focus(); await click('Open holds');
    const close = find('[aria-label="Close formal holds"]'); assert.equal(document.activeElement, close);
    const last = button('Review appeal'); const enabled = [...find('[role="dialog"]').querySelectorAll('button:not(:disabled)')].at(-1); enabled.focus();
    document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }));
    assert.equal(document.activeElement, close); assert.equal(last.disabled, true);
    await act(async () => document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    assert.equal(find('[role="dialog"]'), null); assert.equal(document.activeElement, opener);
});

const tenantRow = { id: A, business_name: 'Reviewed Alpha', plan_id: null, plan_name: null, status: 'active', minutes_used: 0, minutes_allocated: 100,
    user_count: 1, campaign_count: 0, max_concurrent_calls: 1, created_at: null, updated_at: '2026-10-21T00:00:00Z' };
test('Users page exposes formal holds while keeping ordinary block controls explicit', async () => {
    current = status([], target(A, 'user'));
    state.route = (r) => r.path === '/api/v1/admin/users/operations?state=pending&limit=25' ? response({ items: [], next_cursor: null })
        : r.path.includes('/admin/users') ? response([{ id: A, name: 'Reviewed user', email: 'person@example.invalid', role: 'user', is_active: true }])
        : r.path.endsWith('/rbac/roles') ? response([{ id: 'user', name: 'user', level: 1 }])
            : r.path.endsWith('/rbac/permissions') ? response([])
                : r.path.endsWith('/admin/tenants') ? response([tenantRow]) : route(r);
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.UsersPage)))); await settle(350);
    assert.ok(find('[aria-label="Block person@example.invalid"]'));
    assert.ok(state.requests.some(r => r.path === '/api/v1/admin/users/operations?state=pending&limit=25'));
    await act(async () => find('[aria-label="Formal holds for person@example.invalid"]').click());
    assert.match(text(), /Formal holds · person@example.invalid/);
    assert.ok(state.requests.some((r) => r.path.endsWith(`/suspensions/users/${A}/status`)));
});
test('an active subscription remains explicitly separate from an active formal hold and restricted effective access', async () => {
    // Real-browser baseline: subscription_status stays active while a saved formal hold restricts access.
    current = status([event()]);
    state.route = (r) => r.path.startsWith('/api/v1/admin/tenants?') ? response([tenantRow]) : route(r);
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.TenantsPage)))); await settle();
    assert.ok(button('Subscription status'));
    assert.equal(find('tbody tr .status-badge').textContent, 'Active');
    const filter = find('[aria-label="Tenant subscription status"]');
    assert.ok(filter);
    assert.match(document.getElementById(filter.getAttribute('aria-describedby')).textContent,
        /Subscription status does not include formal holds/);
    await act(async () => find('[aria-label="Actions for Reviewed Alpha"]').click());
    assert.ok(button('Suspend subscription')); await click('Formal holds');
    assert.match(text(), /Formal holds · Reviewed Alpha/); assert.ok(state.requests.some((r) => r.path.endsWith(`/suspensions/tenants/${A}/status`)));
    assert.match(text(), /Hold workflow state: active\. Current effective status: suspended\./);
    assert.equal(button('Create formal hold').disabled, true);
    assert.equal(button('Restore selected hold').disabled, false);
});
