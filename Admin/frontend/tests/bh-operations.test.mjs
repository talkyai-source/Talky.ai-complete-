// Real ReactDOM/event regression checks. Synthetic fetch only; the shared
// harness forbids sockets. No production identity, provider or DB is exercised.
import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { el, act, dom, state, render, response, text, find, button, click, settle, setInput, deferred } from './bh-harness.mjs';

const built = await build({
    stdin: { contents: `export { CallTerminationAction } from './src/components/CallTerminationAction';
        export { InboundControlPage } from './src/pages/InboundControlPage';
        export { AuthProvider } from './src/lib/auth'; export { api } from './src/lib/api';
        export { MemoryRouter } from 'react-router-dom';`,
        resolveDir: fileURLToPath(new URL('..', import.meta.url)), loader: 'ts' },
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
    define: { 'import.meta.env': '{}' }, jsx: 'automatic', loader: { '.css': 'empty' },
});
const loaded = { exports: {} };
new Function('require', 'module', 'exports', built.outputFiles[0].text)(createRequire(import.meta.url), loaded, loaded.exports);
const O = loaded.exports;
test.beforeEach(() => O.api.setToken('synthetic-operations-only'));
test.afterEach(() => O.api.setToken(null));

const call = { id: 'call-A', tenant_id: 'tenant-A', status: 'in_call', termination_status: 'none' };
const pending = { call_id: call.id, call_status: 'termination_pending', termination_status: 'requested',
    provider_hangup_requested: true, provider_hangup_confirmed: false, error: 'termination_unconfirmed', reason: 'confirmation_timeout' };
async function mountTermination(props = {}) {
    const patches = [], confirmed = [];
    await render(el(O.CallTerminationAction, { call, onPatch: (...args) => patches.push(args), onConfirmed: (...args) => confirmed.push(args), ...props }));
    return { patches, confirmed };
}
for (const status of [503, 504]) {
    test(`HTTP ${status} requested receipt remains pending and reconciliation never resends hangup`, async () => {
        state.route = (request) => request.method === 'POST' ? response({ detail: pending }, status)
            : response({ id: call.id, status: 'termination_pending' });
        const result = await mountTermination();
        await click('End'); await click('Yes');
        assert.match(text(), /Awaiting provider confirmation/);
        assert.doesNotMatch(text(), /Retry end/);
        assert.equal(result.patches[0][1].termination_status, 'requested');
        await click('Check status');
        assert.equal(state.requests.filter((r) => r.method === 'POST').length, 1);
        assert.equal(result.confirmed.length, 0);
    });
}
test('lost transport response stays unknown; terminal DB label alone is not provider proof', async () => {
    state.route = (request) => {
        if (request.method === 'POST') throw new Error('synthetic connection lost');
        return response({ id: call.id, status: 'completed' });
    };
    const result = await mountTermination();
    await click('End'); await click('Yes'); await click('Check status');
    assert.match(text(), /Outcome unknown/);
    assert.match(text(), /Provider confirmation is still unavailable/);
    assert.doesNotMatch(text(), /Retry end/);
    assert.equal(result.confirmed.length, 0);
    assert.equal(state.requests.filter((r) => r.method === 'POST').length, 1);
});
test('mismatched termination receipt cannot remove the selected call', async () => {
    state.route = () => response({ status: 'confirmed', call_id: 'call-B', termination_status: 'confirmed', provider_hangup_confirmed: true });
    const result = await mountTermination();
    await click('End'); await click('Yes');
    assert.match(text(), /Outcome unknown/);
    assert.equal(result.confirmed.length, 0);
});
test('explicit saved confirmation reconciles an unknown request without another hangup', async () => {
    state.route = (request) => request.method === 'POST' ? response({ detail: 'synthetic gateway timeout' }, 504)
        : response({ id: call.id, status: 'termination_pending', termination_status: 'confirmed', provider_hangup_confirmed: true });
    const result = await mountTermination();
    await click('End'); await click('Yes'); await click('Check status');
    assert.equal(result.confirmed.length, 1);
    assert.equal(state.requests.filter((r) => r.method === 'POST').length, 1);
});
test('confirmed hangup with receipt persistence gap is visible and never retryable failure', async () => {
    state.route = () => response({ status: 'confirmed', call_id: call.id, termination_status: 'confirmed', provider_hangup_confirmed: true,
        termination_receipt_saved: false, termination_reconciliation_error: 'Confirmed provider hangup; durable confirmation could not be saved.' });
    const result = await mountTermination();
    await click('End'); await click('Yes');
    assert.match(text(), /Ended/); assert.match(text(), /durable confirmation could not be saved/);
    assert.doesNotMatch(text(), /Retry end|Outcome unknown/);
    assert.equal(result.confirmed.length, 0);
    assert.equal(result.patches[0][1].provider_hangup_confirmed, true);
});
test('late termination result is retired when selection changes', async () => {
    const request = deferred(); state.route = () => request.promise;
    const result = await mountTermination();
    await click('End'); await click('Yes');
    await render(el(O.CallTerminationAction, { call: { ...call, id: 'call-B' }, onPatch: () => assert.fail('old patch'), onConfirmed: () => assert.fail('old confirmation') }));
    await act(async () => request.resolve(response({ status: 'confirmed', call_id: call.id, termination_status: 'confirmed', provider_hangup_confirmed: true })));
    assert.equal(result.confirmed.length, 0);
    assert.equal(button('End').disabled, false);
});

const controls = { inbound_enabled: true, recording_enabled: false, transfer_enabled: false, settlement_enabled: true, version: 1 };
const approval = { id: '00000000-0000-4000-8000-000000006000', assignment_id: '00000000-0000-4000-8000-000000006001', source_tenant_id: '00000000-0000-4000-8000-000000006002',
    target_tenant_id: '00000000-0000-4000-8000-000000006003', target_campaign_id: '00000000-0000-4000-8000-000000006004', status: 'pending', requested_by: '00000000-0000-4000-8000-000000006005',
    reason: 'Customer authorized ownership move', requested_at: '2026-10-21T00:00:00Z' };
function inboundRoute(overrides = {}) {
    return (request) => {
        if (overrides.route) { const custom = overrides.route(request); if (custom !== undefined) return custom; }
        if (request.path.endsWith('/auth/me')) return response({ id: '00000000-0000-4000-8000-000000006006', email: 'admin@example.invalid', role: 'platform_admin' });
        if (request.path.endsWith('/admin/inbound/overview')) return response({ controls: overrides.controls?.() ?? controls,
            active_assignments: 0, paused_assignments: 0, quarantined_assignments: 0, denied_last_24h: 0 });
        if (request.path.includes('/admin/inbound/assignments')) return response({ items: [], total: 0 });
        if (request.path.split('?')[0].endsWith('/admin/inbound/reassignments')) return response({ items: overrides.requests ?? [], has_more: false, next_cursor: null });
        if (request.path.includes('/admin/inbound/reassignments/')) return response((overrides.requests ?? []).find(row => row.id === request.path.split('/').at(-1)) ?? null);
        if (request.path.split('?')[0].endsWith('/admin/tenants')) return response([]);
        for (const id of [approval.source_tenant_id, approval.target_tenant_id]) {
            if (request.path.endsWith(`/admin/tenants/${id}`)) return response({ id, business_name: `Business ${id}` });
        }
        return response({ detail: 'unrouted synthetic request' }, 500);
    };
}
async function mountInbound() {
    await render(el(O.AuthProvider, null, el(O.MemoryRouter, null, el(O.InboundControlPage)))); await settle();
}
async function editAdmission() {
    await act(async () => find('.inbound-switch input').click());
    await setInput(find('#control-reason'), 'Synthetic incident review');
}
test('rejected control mutation remains visible after refresh and retains its dirty draft', async () => {
    let version = 1, reads = 0;
    state.route = inboundRoute({ controls: () => { reads += 1; return { ...controls, version }; }, route: (request) => {
        if (request.method !== 'PATCH') return;
        version = 2;
        return response({ detail: { code: 'version_conflict', message: 'Synthetic mutation rejected: version changed' } }, 409);
    } });
    await mountInbound(); const before = reads; await editAdmission(); await click('Apply version 2'); await settle();
    assert.ok(reads > before);
    assert.match(text(), /Synthetic mutation rejected/);
    assert.match(text(), /draft is based on version 1; the server is now version 2/);
    assert.equal(find('.inbound-switch input').checked, false);
    assert.equal(button('Apply version 2').disabled, true);
    assert.equal(find('#control-reason').value, 'Synthetic incident review');
    await click('Discard draft and use latest values');
    assert.equal(find('.inbound-switch input').checked, true);
    assert.match(text(), /Synthetic mutation rejected/);
});
test('ordinary refresh preserves an unsaved draft without silently rebasing its version', async () => {
    let version = 1;
    state.route = inboundRoute({ controls: () => ({ ...controls, version }) });
    await mountInbound(); await editAdmission(); version = 3; await click('Refresh'); await settle();
    assert.equal(find('.inbound-switch input').checked, false);
    assert.match(text(), /draft is based on version 1; the server is now version 3/);
    assert.equal(state.requests.filter((r) => r.method !== 'GET').length, 0);
});
test('late old-filter response cannot replace the current inbound inventory', async () => {
    const stale = deferred(); let deferReads = false;
    state.route = inboundRoute({ route: (request) => {
        if (!request.path.includes('/admin/inbound/assignments')) return;
        if (request.path.includes('status=paused')) return response({ items: [], total: 0 });
        if (deferReads) return stale.promise;
    } });
    await mountInbound(); deferReads = true; await click('Refresh');
    await act(async () => {
        const select = find('select[aria-label="Filter inbound assignments by status"]');
        select.value = 'paused'; select.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
    });
    await settle();
    const old = { id: 'old', tenant_name: 'WRONG OLD FILTER', masked_did: 'old DID', campaign_id: 'campaign', campaign_name: 'old', status: 'active', version: 1, readiness: { ready: true, blockers: [] } };
    await act(async () => stale.resolve(response({ items: [old], total: 1 })));
    assert.doesNotMatch(text(), /WRONG OLD FILTER/);
    assert.match(text(), /No assignments found/);
});
test('approval exposes full identifiers, requires entered reason, and sends exactly that reason once', async () => {
    const receipt = deferred();
    state.route = inboundRoute({ requests: [approval], route: (request) => request.path.endsWith('/approve') ? receipt.promise : undefined });
    await mountInbound(); await click('Review reassignment');
    for (const value of [approval.id, approval.assignment_id, approval.source_tenant_id, approval.target_tenant_id, approval.target_campaign_id, approval.requested_by, approval.reason]) assert.ok(text().includes(value));
    assert.equal(button('Approve reassignment').disabled, true);
    const reason = 'Verified customer ownership and paused target campaign';
    await act(async () => {
        const area = find('#reassignment-review-reason');
        Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(area, reason);
        area.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    });
    await act(async () => { button('Approve reassignment').click(); button('Approve reassignment').click(); });
    const posts = state.requests.filter((r) => r.method === 'POST');
    assert.equal(posts.length, 1); assert.equal(posts[0].body.reason, reason);
    await act(async () => receipt.resolve(response({ ...approval, status: 'approved', approved_by: '00000000-0000-4000-8000-000000006006', decision_reason: reason, approved_assignment_id: '00000000-0000-4000-8000-000000006007', decided_at: '2026-10-21T01:00:00Z' })));
    assert.match(text(), /Reassignment approved and applied atomically/);
});
test('requester can review but cannot approve their own request', async () => {
    state.route = inboundRoute({ requests: [{ ...approval, requested_by: '00000000-0000-4000-8000-000000006006' }] });
    await mountInbound(); assert.equal(button('Review reassignment').disabled, false);
    await click('Review reassignment'); await settle();
    assert.equal(button('Approve reassignment').disabled, true);
    assert.equal(button('Cancel saved request').disabled, false);
    assert.equal(state.requests.filter((r) => r.method === 'POST').length, 0);
});
test('unquarantine accepts the actual partial receipt and restored active status', async () => {
    const assignment = { id: 'assignment-A', tenant_id: 'tenant-A', tenant_name: 'Tenant A', campaign_id: 'campaign-A', campaign_name: 'Campaign A',
        masked_did: 'masked DID', status: 'quarantined', version: 4, readiness: { ready: false, blockers: [] } };
    state.route = inboundRoute({ route: (request) => {
        if (request.path.endsWith('/unquarantine')) return response({ id: assignment.id, status: 'active', version: 5, masked_did: assignment.masked_did, quarantine_reason: null });
        if (request.path.includes('/assignments')) return response({ items: [assignment], total: 1 });
    } });
    await mountInbound(); await click('Unquarantine');
    await act(async () => {
        const area = find('#assignment-action-reason');
        Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(area, 'Carrier routing reviewed');
        area.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    });
    await click('Confirm unquarantine'); await settle();
    assert.match(text(), /masked DID was released from quarantine/);
    assert.doesNotMatch(text(), /result could not be verified/);
});
test('retry after unknown inbound result preserves the exact idempotency key', async () => {
    let attempts = 0;
    state.route = inboundRoute({ route: (request) => {
        if (request.method !== 'PATCH') return;
        attempts += 1;
        if (attempts === 1) throw new Error('synthetic response lost');
        return response({ ...controls, ...request.body, version: 2 });
    } });
    await mountInbound(); await editAdmission(); await click('Apply version 2'); await settle();
    assert.match(text(), /synthetic response lost/);
    await click('Apply version 2'); await settle();
    const writes = state.requests.filter((request) => request.method === 'PATCH');
    assert.equal(writes.length, 2);
    assert.equal(writes[0].headers['Idempotency-Key'], writes[1].headers['Idempotency-Key']);
    assert.deepEqual(writes[0].body, writes[1].body);
    assert.match(text(), /Current version 2/, 'an older refresh cannot undo the acknowledged version');
});
