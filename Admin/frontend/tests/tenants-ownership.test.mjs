import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, dom, state, render, response, text, find, button, click, setInput, deferred, settle } from './bh-harness.mjs';
const stamp = '2026-10-21T00:00:00Z';
const receipt = (fields = {}) => ({ id: 'one', changed: true, status: 'active', updated_at: '2026-10-21T00:01:00Z', audit_event_id: 'audit-fixture', minutes_allocated: 500, max_concurrent_calls: 5, ...fields });
const tenant = (extra = {}) => ({ id: 'one', business_name: 'Acme', plan_id: null, plan_name: null, minutes_used: 0,
    minutes_allocated: 500, status: 'active', user_count: 1, campaign_count: 0, max_concurrent_calls: 5, created_at: null, updated_at: stamp, ...extra });
const draw = async (row = tenant(), onRefresh = () => {}) => render(el(C.TenantsTable, { tenants: [row], loading: false, onRefresh,
    searchTerm: '', onSearchChange() {}, statusFilter: '', onStatusFilterChange() {} }));
const menu = async () => act(async () => find('[aria-label="Actions for Acme"]').click());
const mutations = () => state.requests.filter((r) => r.method !== 'GET');

test('a planless zero-allowance tenant is displayed honestly and keeps explicit quota editing', async () => {
    state.route = () => response(receipt({ minutes_allocated: 80 }));
    await draw(tenant({ minutes_allocated: 0 }));
    assert.equal(find('.plan-badge').textContent, 'No Plan');
    assert.equal(find('.usage-text').textContent, '0 / 0 min');
    assert.equal(find('.usage-fill').style.width, '0%');
    assert.equal(mutations().length, 0);
    await menu(); await click('Edit Quota');
    assert.equal(find('#minutes').value, '0');
    await setInput(find('#minutes'), '80'); await click('Save Changes');
    assert.equal(mutations().length, 1);
    assert.deepEqual(mutations()[0].body, { expected_updated_at: stamp, minutes_allocated: 80 });
    assert.match(find('[aria-label="Saved tenant change"]').textContent, /Minutes allocated: 80/);
});

test('status writes carry the displayed version and reject double submission', async () => {
    const pending = deferred(); state.route = () => pending.promise;
    await draw(); await menu(); await click('Suspend subscription');
    await act(async () => { button('Suspend').click(); button('Suspend').click(); });
    assert.equal(mutations().length, 1); assert.deepEqual(mutations()[0].body, { expected_updated_at: stamp });
    await act(async () => pending.resolve(response(receipt({ status: 'suspended', detail: 'saved' }))));
});

test('an unversioned tenant cannot be mutated', async () => {
    await draw(tenant({ updated_at: undefined })); await menu(); await click('Suspend subscription'); await click('Suspend');
    assert.equal(mutations().length, 0); assert.match(find('[role="alert"]').textContent, /version is missing/);
});

test('quota conflict preserves the operator draft and original reviewed version', async () => {
    state.route = () => response({ detail: 'Tenant changed; refresh and review before saving' }, 409);
    await draw(); await menu(); await click('Edit Quota'); await setInput(find('#concurrent'), '9'); await click('Save Changes');
    assert.equal(find('#concurrent').value, '9'); assert.match(text(), /Tenant changed/);
    assert.deepEqual(mutations()[0].body, { expected_updated_at: stamp, max_concurrent_calls: 9 });
    assert.equal(find('[role="dialog"]') !== null, true);
});

test('a mismatching quota receipt does not close the draft or report success', async () => {
    let refreshed = 0; state.route = () => response({ detail: 'saved', max_concurrent_calls: 7 });
    await draw(tenant(), () => { refreshed += 1; }); await menu(); await click('Edit Quota');
    await setInput(find('#concurrent'), '9'); await click('Save Changes');
    assert.equal(refreshed, 0); assert.match(find('[role="alert"]').textContent, /could not be confirmed/);
    assert.equal(find('[aria-label="Saved tenant change"]'), null);
});

test('pending quota cannot be closed by backdrop or Escape', async () => {
    const pending = deferred(); state.route = () => pending.promise;
    await draw(); await menu(); await click('Edit Quota'); await setInput(find('#minutes'), '600'); await click('Save Changes');
    await act(async () => { find('.modal-overlay').click(); document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); });
    assert.ok(find('[role="dialog"]'));
    await act(async () => pending.resolve(response(receipt({ detail: 'saved', minutes_allocated: 600 }))));
    assert.equal(Boolean(find('[role="dialog"]')), false);
});

test('old-session mutation cannot refresh or close a new session view', async () => {
    let refreshed = 0; const pending = deferred(); state.route = () => pending.promise;
    await draw(tenant(), () => { refreshed += 1; }); await menu(); await click('Suspend subscription'); await click('Suspend');
    await act(async () => api.setToken('new-tenant-operator')); await settle();
    await act(async () => pending.resolve(response(receipt({ status: 'suspended', detail: 'saved' }))));
    assert.equal(refreshed, 0); assert.equal(find('[role="dialog"]'), null);
});

test('tenant list failure does not render no tenants found', async () => {
    state.route = (r) => r.path.endsWith('/auth/me') ? response({ id: 'admin', email: 'admin@example.invalid', role: 'platform_admin' }) : response({ detail: 'Database unavailable' }, 503);
    await render(el(C.MemoryRouter, null, el(C.AuthProvider, null, el(C.TenantsPage)))); await settle();
    assert.match(text(), /Tenant list unavailable/); assert.doesNotMatch(text(), /No tenants found|0 total/);
});

test('tenant list can page beyond the first fifty without claiming a global total', async () => {
    state.route = (r) => r.path.endsWith('/auth/me') ? response({ id: 'admin', email: 'admin@example.invalid', role: 'platform_admin' })
        : r.path.includes('offset=50') ? response([tenant({ id: 'last', business_name: 'Next tenant' })])
        : response(Array.from({ length: 51 }, (_, i) => tenant({ id: `tenant-${i}`, business_name: `Tenant ${i}` })));
    await render(el(C.MemoryRouter, null, el(C.AuthProvider, null, el(C.TenantsPage)))); await settle();
    assert.match(text(), /50 shown/); await click('Next'); await settle();
    assert.match(text(), /Next tenant/); assert.equal(button('Next').disabled, true);
    assert.ok(state.requests.some((r) => r.path.includes('limit=51') && r.path.includes('offset=50')));
});


test('an existing tenant above 100 concurrent calls can edit minutes without reducing capacity', async () => {
    state.route = () => response(receipt({ detail: 'saved', minutes_allocated: 600, max_concurrent_calls: 500 }));
    await draw(tenant({ max_concurrent_calls: 500 })); await menu(); await click('Edit Quota');
    await setInput(find('#minutes'), '600'); assert.equal(button('Save Changes').disabled, false); await click('Save Changes');
    assert.deepEqual(mutations()[0].body, { expected_updated_at: stamp, minutes_allocated: 600 });
});

test('minutes above the persisted integer range cannot be submitted', async () => {
    await draw(); await menu(); await click('Edit Quota'); await setInput(find('#minutes'), '2147483648');
    assert.equal(button('Save Changes').disabled, true);
    await act(async () => find('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })));
    assert.equal(mutations().length, 0);
});


test('a changed tenant receipt without a matching durable audit cannot announce success', async () => {
    let refreshed = 0; state.route = () => response(receipt({ status: 'suspended', audit_event_id: null }));
    await draw(tenant(), () => { refreshed += 1; }); await menu(); await click('Suspend subscription'); await click('Suspend');
    assert.equal(refreshed, 0); assert.match(find('[role="alert"]').textContent, /could not be confirmed/);
});

test('same-target saved state is acknowledged without requiring a second mutation audit', async () => {
    let refreshed = 0; state.route = () => response(receipt({ status: 'suspended', changed: false, audit_event_id: null }));
    await draw(tenant(), () => { refreshed += 1; }); await menu(); await click('Suspend subscription'); await click('Suspend');
    assert.equal(refreshed, 1); assert.equal(find('[role="dialog"]'), null);
    assert.match(find('[aria-label="Saved tenant change"]').textContent, /requested state already existed; no new mutation audit/);
});

for (const operation of ['subscription', 'quota']) test(`confirmed ${operation} receipt survives a failed directory reload and filter change`, async () => {
    let saved = false;
    state.route = (r) => {
        if (r.path.endsWith('/auth/me')) return response({ id: 'admin', email: 'admin@example.invalid', role: 'platform_admin' });
        if (r.method !== 'GET') {
            saved = true;
            return response(receipt(operation === 'subscription' ? { status: 'suspended' } : { minutes_allocated: 600 }));
        }
        return saved ? response({ detail: 'Directory unavailable after commit' }, 503) : response([tenant()]);
    };
    await render(el(C.MemoryRouter, null, el(C.AuthProvider, null, el(C.TenantsPage)))); await settle();
    await menu();
    if (operation === 'subscription') { await click('Suspend subscription'); await click('Suspend'); }
    else { await click('Edit Quota'); await setInput(find('#minutes'), '600'); await click('Save Changes'); }
    await settle();
    assert.equal(Boolean(find('[role="dialog"]')), false);
    assert.match(text(), /Directory unavailable after commit/);
    const savedNotice = find('[aria-label="Saved tenant change"]');
    assert.ok(savedNotice, 'a confirmed saved receipt must survive a failing follow-up read');
    assert.match(savedNotice.textContent, /Acme.*one/);
    assert.match(savedNotice.textContent, /audit-fixture/);
    assert.match(savedNotice.textContent, operation === 'subscription' ? /suspended/ : /600/);
    assert.match(savedNotice.textContent, /not a current-state check/);
    await setInput(find('[aria-label="Search tenants"]'), 'Different tenant'); await settle(350);
    assert.ok(find('[aria-label="Saved tenant change"]'));
    assert.equal(mutations().length, 1, 'read failure/filter changes must not replay the saved write');
    await click('Dismiss saved tenant change');
    assert.equal(find('[aria-label="Saved tenant change"]'), null);
});

for (const asynchronous of [false, true]) test(`a ${asynchronous ? 'rejected' : 'throwing'} refresh callback cannot reclassify a confirmed save`, async () => {
    state.route = () => response(receipt({ status: 'suspended' }));
    await draw(tenant(), () => {
        if (asynchronous) return Promise.reject(new Error('Refresh rejected'));
        throw new Error('Refresh threw');
    });
    await menu(); await click('Suspend subscription'); await click('Suspend'); await settle();
    assert.equal(Boolean(find('[role="dialog"]')), false);
    assert.match(find('[aria-label="Saved tenant change"]').textContent, /suspended.*audit-fixture/);
    assert.match(text(), /Saved change confirmed.*reload failed/i);
    assert.doesNotMatch(text(), /saved tenant status could not be confirmed/);
    assert.equal(mutations().length, 1);
});

test('saved tenant acknowledgement is private to its authentication generation', async () => {
    state.route = () => response(receipt({ status: 'suspended' }));
    await draw(); await menu(); await click('Suspend subscription'); await click('Suspend');
    assert.ok(find('[aria-label="Saved tenant change"]'));
    await act(async () => api.setToken('replacement-tenant-session')); await settle();
    assert.equal(find('[aria-label="Saved tenant change"]'), null);
});
