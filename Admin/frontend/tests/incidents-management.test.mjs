import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, dom, state, render, response, find, text, click, settle, deferred, button } from './bh-harness.mjs';
const stamp = '2026-10-21T00:00:00Z';
const receiptActor = '00000000-0000-4000-8000-000000000002';
const item = (id = 'one', status = 'open') => ({ id, title: `Incident ${id}`, severity: 'critical', status,
    description: 'An actual recorded condition', triggered_at: stamp, acknowledged_at: status === 'acknowledged' ? stamp : null,
    acknowledged_by: status === 'acknowledged' ? 'admin' : null, resolved_at: status === 'resolved' ? stamp : null, resolved_by: status === 'resolved' ? 'admin' : null });
const list = (items = [item()], extra = {}) => ({ items, total: items.length, page: 1, page_size: 20, open_count: 1, critical_count: 1, ...extra });
const normal = (r) => {
    if (r.path.endsWith('/auth/me')) return response({ id: 'admin', email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null });
    if (r.path.includes('/admin/incidents?')) return response(list());
    if (r.path.endsWith('/admin/incidents/one')) return response(item());
    return response({ detail: 'unrouted synthetic request' }, 500);
};
const draw = async () => { await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.IncidentsPage)))); await settle(); };
const open = async () => { await act(async () => find('[aria-label="View incident Incident one"]').click()); await settle(); };
const select = async (label, value) => { await act(async () => { const field = find(`[aria-label="${label}"]`); field.value = value; field.dispatchEvent(new dom.window.Event('change', { bubbles: true })); }); await settle(); };
test.beforeEach(() => { state.route = normal; });

test('source recovery is shown separately from an open operator incident', async () => {
    state.route = r => r.path.endsWith('/admin/incidents/one') ? response({ ...item(),
        source_system: 'approved-monitor', source_status: 'recovered',
        source_last_received_at: stamp, source_recovered_at: stamp }) : normal(r);
    await draw(); await open();
    const detail = find('[role="dialog"]').textContent;
    assert.match(detail, /Recovery reported/);
    assert.match(detail, /Operator statusopen/);
    assert.match(detail, /recorded separately/);
    assert.equal(button('Acknowledge').disabled, false);
});

test('missing incident store is an error rather than an empty successful list', async () => {
    state.route = (r) => r.path.includes('/admin/incidents') ? response({ detail: 'Incident records could not be read' }, 503) : normal(r);
    await draw(); assert.match(find('[role="alert"]').textContent, /could not be read/);
    assert.doesNotMatch(text(), /No incidents match|Coming Soon/);
});

test('valid empty incident response remains an empty list with accurate zero counts', async () => {
    state.route = r => r.path.includes('/admin/incidents?')
        ? response(list([], { open_count: 0, critical_count: 0 })) : normal(r);
    await draw();
    assert.match(text(), /0 open across all severities; 0 critical and open. 0 matching records/);
    assert.match(text(), /No incidents match these filters/);
    assert.equal(find('[role="alert"]'), null);
});

for (const [name, body] of [
    ['null', null], ['missing fields', {}], ['non-array rows', { ...list(), items: null }],
    ['invalid page size', list([], { page_size: 0 })],
    ['invalid counts', list([], { open_count: '0' })],
    ['object title', list([{ ...item(), title: { unexpected: 'object' } }])],
    ['null item', list([null])], ['unsupported state', list([{ ...item(), status: 'unknown' }])],
]) {
    test(`malformed successful incident list (${name}) is unavailable without empty claims or actions`, async () => {
        state.route = r => r.path.includes('/admin/incidents?') ? response(body) : normal(r);
        await draw();
        assert.match(find('[role="alert"]').textContent, /unexpected response/);
        assert.doesNotMatch(text(), /No incidents match these filters|0 matching records/);
        assert.equal(find('[aria-label^="View incident"]'), null);
    });
}

for (const [name, changes] of [
    ['object title', { title: {} }], ['object description', { description: [] }],
    ['unknown state', { status: 'unknown' }], ['unknown severity', { severity: 'unknown' }],
    ['object source', { source_system: {} }], ['object timestamp', { acknowledged_at: {} }],
    ['invalid source state', { source_system: 'monitor', source_status: 'resolved' }],
]) {
    test(`malformed matching incident detail (${name}) cannot enable actions`, async () => {
        state.route = r => r.path.endsWith('/admin/incidents/one') ? response({ ...item(), ...changes }) : normal(r);
        await draw(); await open();
        const dialog = find('[role="dialog"]');
        assert.match(dialog.textContent, /unexpected incident record/);
        assert.equal([...dialog.querySelectorAll('button')].some(b => ['Acknowledge', 'Resolve'].includes(b.textContent)), false);
        assert.equal(state.requests.filter(r => r.method === 'POST').length, 0);
    });
}

test('malformed detail refresh preserves the prior record but disables mutation', async () => {
    await draw(); await open();
    state.route = r => r.path.endsWith('/admin/incidents/one') ? response({ id: 'one' }) : normal(r);
    await click('Refresh record'); await settle();
    const dialog = find('[role="dialog"]');
    assert.match(dialog.textContent, /Incident one/);
    assert.match(dialog.textContent, /last confirmed record/);
    assert.equal(button('Acknowledge').disabled, true);
    assert.equal(button('Resolve').disabled, true);
    assert.equal(state.requests.filter(r => r.method === 'POST').length, 0);
});

test('old filter response cannot replace a newly selected status', async () => {
    const old = deferred();
    state.route = (r) => r.path.includes('status=open') ? old.promise.then((v) => v.clone()) : r.path.includes('status=resolved') ? response(list([item('resolved-record', 'resolved')])) : normal(r);
    await draw(); await select('Incident status', 'resolved');
    await act(async () => old.resolve(response(list([item('obsolete')]))));
    assert.match(text(), /resolved-record/); assert.doesNotMatch(text(), /obsolete/);
});

test('pagination uses server total and requests the next page', async () => {
    state.route = (r) => r.path.includes('/admin/incidents?') ? response(list([item(r.path.includes('page=2') ? 'second' : 'one')], { total: 21, page: r.path.includes('page=2') ? 2 : 1 })) : normal(r);
    await draw(); assert.match(text(), /Page 1 of 2/); await click('Next'); await settle();
    assert.match(text(), /Incident second/); assert.equal(button('Next').disabled, true);
    assert.ok(state.requests.some((r) => r.path.includes('page=2')));
});

test('mismatching detail identity blocks incident actions', async () => {
    state.route = (r) => r.path.endsWith('/admin/incidents/one') ? response(item('other')) : normal(r);
    await draw(); await open(); assert.match(find('[role="dialog"]').textContent, /No matching incident/);
    assert.equal([...state.host.querySelectorAll('button')].some((b) => b.textContent === 'Acknowledge'), false);
});

test('acknowledgement dispatch is single and only a matching saved receipt reports success', async () => {
    const pending = deferred(); let saved = false;
    state.route = (r) => r.method === 'POST' ? pending.promise : r.path.endsWith('/admin/incidents/one') ? response(item('one', saved ? 'acknowledged' : 'open')) : normal(r);
    await draw(); await open();
    await act(async () => { button('Acknowledge').click(); button('Acknowledge').click(); });
    assert.equal(state.requests.filter((r) => r.method === 'POST').length, 1);
    saved = true; await act(async () => pending.resolve(response({ success: true, incident_id: 'one', status: 'acknowledged', message: 'Incident acknowledged', acknowledged_at: stamp, acknowledged_by: receiptActor, resolved_at: null, resolved_by: null })));
    assert.match(find('[role="status"]').textContent, /server confirmed/);
    assert.match(find('[role="dialog"]').textContent, /acknowledged/);
});

test('unconfirmed action requires reading the record and does not erase the error on list refresh', async () => {
    let saved = false;
    state.route = (r) => r.method === 'POST' ? response({ detail: 'Transition could not be confirmed' }, 503)
        : r.path.endsWith('/admin/incidents/one') ? response(item('one', saved ? 'resolved' : 'open')) : normal(r);
    await draw(); await open(); await click('Acknowledge');
    assert.equal(button('Acknowledge').disabled, true); assert.match(text(), /could not be confirmed/);
    await click('Refresh list'); assert.match(text(), /could not be confirmed/);
    saved = true; await click('Refresh record'); await settle();
    assert.match(find('[role="dialog"]').textContent, /resolved/);
    assert.equal([...state.host.querySelectorAll('button')].some((b) => b.textContent === 'Resolve'), false);
    assert.equal(state.requests.filter((r) => r.method === 'POST').length, 1);
});

test('resolution asks for deliberate confirmation before sending its request', async () => {
    await draw(); await open(); await click('Resolve');
    assert.match(text(), /does not repair the system/);
    assert.equal(state.requests.filter((r) => r.method === 'POST').length, 0);
    await click('Cancel resolution'); assert.equal(button('Resolve').disabled, false);
});

test('late mutation after session replacement cannot announce success or reopen details', async () => {
    const pending = deferred(); state.route = (r) => r.method === 'POST' ? pending.promise : normal(r);
    await draw(); await open(); await click('Acknowledge');
    await act(async () => api.setToken('new-incident-session')); await settle();
    await act(async () => pending.resolve(response({ success: true, incident_id: 'one', status: 'acknowledged', message: 'Incident acknowledged', acknowledged_at: stamp, acknowledged_by: receiptActor, resolved_at: null, resolved_by: null })));
    assert.equal(find('[role="dialog"]'), null); assert.doesNotMatch(text(), /server confirmed the saved state/);
});


test('confirmed resolution stays resolved if its follow-up read fails', async () => {
    let committed = false;
    state.route = (r) => {
        if (r.method === 'POST') { committed = true; return response({ success: true, incident_id: 'one', status: 'resolved',
            message: 'Incident resolved', acknowledged_at: null, acknowledged_by: null, resolved_at: stamp, resolved_by: receiptActor }); }
        if (committed && r.path.endsWith('/admin/incidents/one')) return response({ detail: 'Database temporarily unavailable' }, 503);
        return normal(r);
    };
    await draw(); await open(); await click('Resolve'); await click('Confirm resolution'); await settle();
    const dialog = find('[role="dialog"]');
    assert.match(dialog.textContent, /resolved/); assert.match(dialog.textContent, /last confirmed record/);
    assert.ok(dialog.textContent.includes(`by ${receiptActor}`));
    assert.equal([...dialog.querySelectorAll('button')].some((b) => ['Acknowledge', 'Resolve', 'Confirm resolution'].includes(b.textContent)), false);
});
