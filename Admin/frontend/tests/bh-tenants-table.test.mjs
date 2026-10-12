import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, state, render, response, text, find, button, click, setInput } from './bh-harness.mjs';

const tenant = (extra = {}) => ({
    id: '10000000-0000-4000-8000-000000000001', business_name: 'Acme Ltd', plan_id: 'p1', plan_name: 'Pro',
    minutes_used: 40, minutes_allocated: 500, status: 'active', user_count: 2, campaign_count: 1,
    max_concurrent_calls: 5, created_at: null, ...extra,
});

const draw = async (tenants, onRefresh) => {
    await render(el(C.TenantsTable, { tenants, loading: false, onRefresh, searchTerm: '', onSearchChange() {},
        statusFilter: '', onStatusFilterChange() {} }));
};
const openMenu = async () => { await act(async () => find('.action-menu-btn').click()); };
const mutations = () => state.requests.filter((r) => r.method !== 'GET');

// admin-frontend-gateway-1 ---------------------------------------------------

test('failed suspend keeps the confirm modal open, shows the error and does not refresh', async () => {
    state.route = () => response({ detail: 'Not allowed to suspend' }, 403);
    let refreshed = 0;
    await draw([tenant()], () => { refreshed += 1; });
    await openMenu(); await click('Suspend Tenant');
    await click('Suspend');
    assert.equal(mutations().length, 1);
    assert.match(text(), /Suspend Tenant\?/, 'modal must stay open on failure');
    const alert = find('[role="alert"]');
    assert.ok(alert, 'an error must be rendered for the operator');
    assert.match(alert.textContent, /Not allowed to suspend/);
    assert.equal(refreshed, 0);
});

test('network failure on resume is surfaced, not swallowed', async () => {
    state.route = () => { throw new Error('socket hang up'); };
    let refreshed = 0;
    await draw([tenant({ status: 'suspended' })], () => { refreshed += 1; });
    await openMenu(); await click('Resume Tenant');
    await click('Resume');
    assert.match(text(), /Resume Tenant\?/);
    assert.match(find('[role="alert"]').textContent, /socket hang up/);
    assert.equal(refreshed, 0);
});

test('successful suspend refreshes once and closes the modal', async () => {
    state.route = () => response({ detail: 'ok', status: 'suspended' });
    let refreshed = 0;
    await draw([tenant()], () => { refreshed += 1; });
    await openMenu(); await click('Suspend Tenant');
    await click('Suspend');
    assert.equal(refreshed, 1);
    assert.doesNotMatch(text(), /Suspend Tenant\?/);
    assert.equal(find('[role="alert"]'), null);
});

test('failed quota save keeps the modal open, shows the error and does not refresh', async () => {
    state.route = () => response({ detail: 'Quota backend unavailable' }, 500);
    let refreshed = 0;
    await draw([tenant()], () => { refreshed += 1; });
    await openMenu(); await click('Edit Quota');
    await setInput(find('#concurrent'), '9');
    await click('Save Changes');
    assert.equal(mutations().length, 1);
    assert.match(text(), /Edit Quota - Acme Ltd/, 'modal must stay open on failure');
    assert.match(find('[role="alert"]').textContent, /Quota backend unavailable/);
    assert.equal(refreshed, 0);
});

// admin-frontend-gateway-8 ---------------------------------------------------

test('editing only concurrency sends only max_concurrent_calls (no stale minutes overwrite)', async () => {
    state.route = () => response({ detail: 'ok' });
    await draw([tenant()], () => {});
    await openMenu(); await click('Edit Quota');
    await setInput(find('#concurrent'), '9');
    await click('Save Changes');
    assert.deepEqual(mutations()[0].body, { max_concurrent_calls: 9 });
});

test('editing only minutes sends only minutes_allocated', async () => {
    state.route = () => response({ detail: 'ok' });
    await draw([tenant()], () => {});
    await openMenu(); await click('Edit Quota');
    await setInput(find('#minutes'), '750');
    await click('Save Changes');
    assert.deepEqual(mutations()[0].body, { minutes_allocated: 750 });
});

test('clearing the minutes box cannot save 0: Save is disabled and nothing is sent', async () => {
    state.route = () => response({ detail: 'ok' });
    await draw([tenant()], () => {});
    await openMenu(); await click('Edit Quota');
    await setInput(find('#minutes'), '');
    assert.equal(button('Save Changes').disabled, true);
    await act(async () => find('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })));
    assert.equal(mutations().length, 0, 'an empty field must never be coerced to 0 and sent');
});

test('saving with no change sends nothing', async () => {
    state.route = () => response({ detail: 'ok' });
    await draw([tenant()], () => {});
    await openMenu(); await click('Edit Quota');
    assert.equal(button('Save Changes').disabled, true);
    assert.equal(mutations().length, 0);
});
