import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, state, render, response, find, text, click, deferred } from './bh-harness.mjs';

const tenant = { id: 'top', business_name: 'Highest tenant', minutes_used: 9000 };
const connector = (extra = {}) => ({ id: 'connector', name: 'Calendar account', status: 'active',
    token_status: 'valid', refresh_state: 'idle', type: 'calendar', provider: 'google_calendar', ...extra });
const page = (items = [connector()]) => ({ items, total: 123, page: 1, page_size: 5 });

test('top tenants asks the server for a global ranking before limiting and exposes working navigation', async () => {
    state.route = () => response([tenant]);
    await render(el(C.TopTenantsList));
    const query = new URL(state.requests[0].path, 'https://fixture.invalid').searchParams;
    assert.equal(query.get('sort_by'), 'minutes_used');
    assert.equal(query.get('limit'), '5');
    assert.match(text(), /Highest tenant/);
    assert.equal(find('a').getAttribute('href'), '/tenants');
    assert.equal([...state.host.querySelectorAll('button')].some(node => node.textContent === 'Alert'), false);
});

for (const [component, empty, good] of [
    ['TopTenantsList', /No tenants yet/, [tenant]],
    ['TopTenantsPanel', /No connectors configured/, page()],
]) {
    test(component + ': read failure is not empty; retry recovers', async () => {
        state.route = () => response({ detail: 'database is unavailable' }, 503);
        await render(el(C[component]));
        assert.match(find('[role="alert"]').textContent, /database is unavailable/);
        assert.doesNotMatch(text(), empty);
        state.route = () => response(good);
        await click(component === 'TopTenantsList' ? 'Retry tenants' : 'Retry connectors');
        assert.equal(find('[role="alert"]'), null);
    });
    test(component + ': late old-session response cannot disclose old data', async () => {
        const old = deferred();
        state.route = () => old.promise;
        await render(el(C[component]));
        state.route = () => response({ detail: 'New session denied' }, 403);
        await act(async () => api.setToken('new-synthetic-session'));
        await act(async () => old.resolve(response(good)));
        assert.doesNotMatch(text(), /Highest tenant|Calendar account/);
        assert.match(text(), /New session denied/);
    });
}

test('connector summary uses actual total and prioritizes uncertain refresh and expired tokens over active status', async () => {
    state.route = () => response(page([connector({ id: 'expired', token_status: 'expired' }),
        connector({ id: 'uncertain', refresh_state: 'uncertain' })]));
    await render(el(C.TopTenantsPanel));
    assert.match(text(), /Showing 2 of 123/);
    assert.match(text(), /Token expired/);
    assert.match(text(), /Refresh outcome unconfirmed/);
    assert.doesNotMatch(text(), /Connected/);
    assert.equal(find('a').getAttribute('href'), '/connectors');
});

test('malformed successful summary stays unavailable', async () => {
    state.route = () => response({ items: {}, total: 3 });
    await render(el(C.TopTenantsPanel));
    assert.match(find('[role="alert"]').textContent, /unexpected response/);
    assert.doesNotMatch(text(), /No connectors configured/);
});

test('object-valued connector labels cannot crash the dashboard', async () => {
    state.route = () => response(page([connector({ name: { unexpected: 'object' } })]));
    await render(el(C.TopTenantsPanel));
    assert.match(find('[role="alert"]').textContent, /unexpected response/);
});

test('standalone usage summary retires prior account data even with unchanged dates', async () => {
    const old = deferred();
    state.route = () => old.promise;
    await render(el(C.UsageBreakdownCard, { fromDate: '2026-10-01', toDate: '2026-10-21' }));
    state.route = () => response({ detail: 'New session denied' }, 403);
    await act(async () => api.setToken('replacement-account'));
    await act(async () => old.resolve(response({ period_start: 'Private prior account', total_call_minutes: 987 })));
    assert.doesNotMatch(text(), /Private prior account|987/);
    assert.match(text(), /Usage data is unavailable/);
});
