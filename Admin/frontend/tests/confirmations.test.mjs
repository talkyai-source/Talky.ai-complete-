// A7: five actions ask for confirmation through the shared ConfirmationModal.
// Cancel leaves everything unchanged (no request, same screen state); Confirm
// runs the existing action exactly once.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, dom, state, render, response, text, find, click, setInput, settle } from './bh-harness.mjs';

const writes = () => state.requests.filter((request) => request.method !== 'GET');
const modal = () => find('.modal-backdrop');
const noModal = () => assert.equal(modal() === null, true, 'no confirmation is open');
// The confirmation's own Cancel: a form underneath may have a Cancel button too.
const cancelModal = async () => { await act(async () => [...modal().querySelectorAll('button')].find((node) => node.textContent.trim() === 'Cancel').click()); };

// LiveCalls polls on window.setInterval; neutralise it so the test is deterministic.
const realSetInterval = dom.window.setInterval, realClearInterval = dom.window.clearInterval;
test.before(() => { dom.window.setInterval = () => 1; dom.window.clearInterval = () => {}; });
test.after(() => { dom.window.setInterval = realSetInterval; dom.window.clearInterval = realClearInterval; });

test('resume all calls: cancel keeps the pause, confirm sends one resume', async () => {
    const pauseBody = (paused) => ({ paused, reason: paused ? 'Provider outage' : null, paused_at: null, paused_by: null });
    let paused = true;
    state.route = (call) => {
        if (call.path.endsWith('/admin/calls/live')) return response([]);
        if (call.path.endsWith('/admin/calls/pause-status')) return response(pauseBody(paused));
        if (call.path.endsWith('/admin/calls/pause') && call.method === 'PUT') { paused = call.body.paused; return response(pauseBody(paused)); }
        return response({ detail: 'unrouted' }, 500);
    };
    await render(el(C.LiveCalls)); await settle();
    assert.match(text(), /New outbound calls are paused/);
    await click('Resume Calls');
    assert.ok(modal()); assert.match(modal().textContent, /Resume Calls/);
    await cancelModal();
    noModal(); assert.equal(writes().length, 0); assert.match(text(), /New outbound calls are paused/);
    await click('Resume Calls'); await click('Confirm'); await settle();
    noModal();
    assert.equal(writes().length, 1); assert.deepEqual(writes()[0].body, { paused: false });
    assert.doesNotMatch(text(), /New outbound calls are paused/);
});

const connector = (extra = {}) => ({
    id: 'c0000000-0000-4000-8000-000000000001', tenant_id: 't1', tenant_name: 'Acme Ltd', type: 'email',
    provider: 'gmail', name: 'Acme Gmail', status: 'active', account_email: 'ops@acme.example',
    token_expires_at: null, token_status: 'expired', last_refreshed_at: null, created_at: '2026-10-01T00:00:00Z',
    refresh_state: 'idle', provider_revocation_status: 'not_requested', ...extra,
});
const reconnectRoute = (call) => {
    if (call.path.endsWith('/reconnect')) return response({ success: true, connector_id: connector().id });
    if (call.path.includes('/admin/connectors/c0000000')) return response({ ...connector(), scopes: [], error_message: null });
    return response({ items: [connector()], total: 1, page: 1, page_size: 20 });
};

test('refresh token (table): cancel sends nothing, confirm sends one reconnect', async () => {
    state.route = reconnectRoute;
    await render(el(C.ConnectorsTable, {})); await settle();
    const open = async () => { await act(async () => find('button[title="Refresh token"]').click()); };
    await open(); assert.ok(modal()); assert.match(modal().textContent, /Refresh token/);
    await cancelModal(); noModal(); assert.equal(writes().length, 0);
    await open(); await click('Confirm'); await settle();
    noModal(); assert.equal(writes().length, 1); assert.match(writes()[0].path, /\/reconnect$/);
    assert.match(text(), /Token refresh confirmed/);
});

test('refresh token (drawer): cancel sends nothing, confirm sends one reconnect', async () => {
    state.route = reconnectRoute;
    let refreshed = 0;
    await render(el(C.ConnectorDetailDrawer, { connector: connector(), onClose() {}, onRefresh: () => { refreshed += 1; } })); await settle();
    await click('Refresh token'); assert.ok(modal());
    await cancelModal(); noModal(); assert.equal(writes().length, 0); assert.equal(refreshed, 0);
    await click('Refresh token'); await click('Confirm'); await settle();
    noModal(); assert.equal(writes().length, 1); assert.match(writes()[0].path, /\/reconnect$/); assert.equal(refreshed, 1);
});

test('edit quota: cancel keeps the draft open and unchanged, confirm sends one PATCH', async () => {
    const tenant = { id: '10000000-0000-4000-8000-000000000001', business_name: 'Acme Ltd', plan_id: 'p1', plan_name: 'Pro',
        minutes_used: 40, minutes_allocated: 500, status: 'active', user_count: 2, campaign_count: 1,
        max_concurrent_calls: 5, created_at: null, updated_at: '2026-10-21T00:00:00Z' };
    state.route = (call) => call.method === 'PATCH'
        ? response({ id: tenant.id, changed: true, updated_at: '2026-10-22T00:00:00Z', audit_event_id: 'audit-1', minutes_allocated: 750, max_concurrent_calls: 5 })
        : response([tenant]);
    let refreshed = 0;
    await render(el(C.TenantsTable, { tenants: [tenant], loading: false, onRefresh: () => { refreshed += 1; }, searchTerm: '', onSearchChange() {},
        statusFilter: '', onStatusFilterChange() {} }));
    await act(async () => find('.action-menu-btn').click()); await click('Edit Quota');
    await setInput(find('#minutes'), '750'); await click('Save Changes');
    assert.ok(modal()); assert.match(modal().textContent, /Save the quota changes for "Acme Ltd"\?/);
    await cancelModal();
    noModal(); assert.equal(writes().length, 0);
    assert.ok(find('#minutes'), 'the quota form stays open'); assert.equal(find('#minutes').value, '750');
    await click('Save Changes'); await click('Confirm'); await settle();
    assert.equal(writes().length, 1); assert.equal(writes()[0].method, 'PATCH');
    assert.deepEqual(writes()[0].body, { expected_updated_at: '2026-10-21T00:00:00Z', minutes_allocated: 750 });
    assert.equal(find('#minutes') === null, true, 'the saved quota closes the form'); assert.equal(refreshed, 1);
});

test('incident acknowledge: cancel sends nothing, confirm sends one acknowledgement', async () => {
    const stamp = '2026-10-21T00:00:00Z';
    const item = (status = 'open') => ({ id: 'one', title: 'Incident one', severity: 'critical', status, description: 'Recorded condition',
        triggered_at: stamp, acknowledged_at: status === 'acknowledged' ? stamp : null, acknowledged_by: status === 'acknowledged' ? 'admin' : null,
        resolved_at: null, resolved_by: null });
    let saved = false;
    state.route = (request) => {
        if (request.path.endsWith('/auth/me')) return response({ id: 'admin', email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null });
        if (request.method === 'POST') { saved = true; return response({ success: true, incident_id: 'one', status: 'acknowledged', message: 'Incident acknowledged', acknowledged_at: stamp, acknowledged_by: '00000000-0000-4000-8000-000000000002', resolved_at: null, resolved_by: null }); }
        if (request.path.includes('/admin/incidents?')) return response({ items: [item(saved ? 'acknowledged' : 'open')], total: 1, page: 1, page_size: 20, open_count: 1, critical_count: 1 });
        if (request.path.endsWith('/admin/incidents/one')) return response(item(saved ? 'acknowledged' : 'open'));
        return response({ detail: 'unrouted' }, 500);
    };
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.IncidentsPage)))); await settle();
    await act(async () => find('[aria-label="View incident Incident one"]').click()); await settle();
    await click('Acknowledge'); assert.ok(modal()); assert.match(modal().textContent, /Acknowledge “Incident one”\?/);
    await cancelModal(); noModal(); assert.equal(writes().length, 0);
    assert.match(find('.incidents-dialog').textContent, /Operator statusopen/);
    await click('Acknowledge'); await click('Confirm'); await settle();
    noModal(); assert.equal(writes().length, 1); assert.match(writes()[0].path, /\/admin\/incidents\/one\/acknowledge$/);
    assert.match(find('[role="status"]').textContent, /server confirmed/);
});

test('supplier statement decision: cancel sends nothing, confirm records one review', async () => {
    const id = '00000000-0000-4000-8000-000000000001';
    const capture = { id, request_id: id, supplier: 'deepgram', source_kind: 'deepgram_billing_breakdown_v1',
        account_id: id, reference: 'Owned supplier export', period_start: '2026-09-01', period_end: '2026-09-30',
        currency: 'USD', reported_charges: '12.34000000', source_sha256: 'a'.repeat(64), imported_by: id,
        imported_at: '2026-10-01T00:00:00Z', stage: 'imported', superseded_by: null, reviews: [] };
    const page = (items) => ({ items, has_more: false, net_spend: null, coverage: 'listed_account_periods_only' });
    const summary = { period_start: '2026-10-01', period_end: '2026-10-27', monetary_note: 'No supplier allocation',
        total_call_minutes: 1, total_action_records: 2, legacy_outbound_estimate: null };
    state.route = (request) => {
        if (request.path.endsWith('/auth/me')) return response({ id, email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null });
        if (request.path.endsWith('/reviews')) return response({ ...capture, stage: 'reviewed', reviews: [{ id, request_id: request.body.request_id, stage: 'reviewed',
            review_reference: request.body.review_reference, notes: request.body.notes, reviewer_id: id, recorded_at: '2026-10-02T00:00:00Z' }] });
        if (request.path.includes('/usage/statements')) return response(page([capture]));
        if (request.path.includes('/usage/summary')) return response(summary);
        return response({ monetary_note: 'No allocation', breakdown: [] });
    };
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.UsageCostPage)))); await settle();
    await click('Owned supplier export'); await settle();
    const section = find('section[aria-label="Selected supplier evidence"]');
    await setInput(section.querySelector('input'), 'Finance source check');
    await act(async () => {
        const textarea = section.querySelector('textarea');
        Object.getOwnPropertyDescriptor(Object.getPrototypeOf(textarea), 'value').set.call(textarea, 'Matched to original owned account export.');
        textarea.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    });
    await act(async () => section.querySelector('input[type="checkbox"]').click());
    await click('Record source review');
    assert.ok(modal()); assert.match(modal().textContent, /Record this decision for "Owned supplier export"\?/);
    assert.match(modal().textContent, /operator attestation, not automatic supplier verification or finance approval/);
    await cancelModal();
    noModal(); assert.equal(writes().length, 0);
    assert.equal(section.querySelector('input').value, 'Finance source check', 'the draft is unchanged');
    assert.equal(section.querySelector('textarea').value, 'Matched to original owned account export.');
    await click('Record source review'); await click('Confirm'); await settle();
    noModal(); assert.equal(writes().length, 1); assert.match(writes()[0].path, /\/reviews$/);
    assert.equal(writes()[0].body.review_reference, 'Finance source check');
    assert.match(text(), /Review receipt saved/);
});
