import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, dom, state, render, response, text, find, settle } from './bh-harness.mjs';

// StatsGrid/SystemHealth/Incidents refresh on timers; capture the ticks so a test can fire them.
const ticks = [];
const realSetInterval = globalThis.setInterval, realClearInterval = globalThis.clearInterval;
const stubIntervals = () => {
    globalThis.setInterval = (fn) => { ticks.push(fn); return ticks.length; };
    dom.window.setInterval = globalThis.setInterval;
    globalThis.clearInterval = () => {};
    dom.window.clearInterval = globalThis.clearInterval;
};
test.beforeEach(() => { ticks.length = 0; stubIntervals(); });
test.afterEach(() => {
    globalThis.setInterval = realSetInterval; globalThis.clearInterval = realClearInterval;
});
const tick = async () => { await act(async () => { for (const fn of [...ticks]) await fn(); }); };

const STATS = { active_calls: 7, error_rate_24h: '3.2%', active_tenants: 12, api_errors_24h: 41 };

// admin-frontend-gateway-4: StatsGrid ----------------------------------------

test('stats API failure shows unavailable, not zeros', async () => {
    state.route = () => response({ detail: 'stats backend down' }, 500);
    await render(el(C.StatsGrid));
    await settle();
    assert.doesNotMatch(text(), /0%/, 'a failed read must not render an error rate of 0%');
    const values = [...state.host.querySelectorAll('.stat-value')].map((n) => n.textContent.trim());
    assert.equal(values.length, 4);
    for (const value of values) assert.notEqual(value, '0', 'a failed read must not render zero counts');
    assert.match(find('[role="alert"]').textContent, /stats backend down/);
});

test('stats success renders the real values with no alert', async () => {
    state.route = () => response(STATS);
    await render(el(C.StatsGrid));
    await settle();
    assert.match(text(), /7/); assert.match(text(), /3\.2%/); assert.match(text(), /12/); assert.match(text(), /41/);
    assert.equal(find('[role="alert"]'), null);
});

test('a later stats failure flags the previous numbers as stale', async () => {
    let ok = true;
    state.route = () => (ok ? response(STATS) : response({ detail: 'unauthorised' }, 401));
    await render(el(C.StatsGrid));
    await settle();
    ok = false;
    await tick();
    assert.match(find('[role="alert"]').textContent, /last known|stale/i);
});

// admin-frontend-gateway-4: Incidents ----------------------------------------

test('incidents API failure says unavailable, never "No open incidents."', async () => {
    state.route = () => response({ detail: 'incidents backend down' }, 500);
    await render(el(C.Incidents));
    await settle();
    assert.doesNotMatch(text(), /No open incidents/);
    assert.match(find('[role="alert"]').textContent, /incidents backend down/);
});

test('a genuinely empty incident list still says "No open incidents."', async () => {
    state.route = () => response({ items: [], total: 0, page: 1, page_size: 5 });
    await render(el(C.Incidents));
    await settle();
    assert.match(text(), /No open incidents/);
    assert.equal(find('[role="alert"]'), null);
});

// admin-frontend-gateway-4: SystemHealth -------------------------------------

const health = (status) => ({ providers: [{ name: 'Groq', status, latency_ms: 120, latency_display: '120ms' }] });

test('health API failure on first load shows unavailable, not an empty card', async () => {
    state.route = () => response({ detail: 'health backend down' }, 500);
    await render(el(C.SystemHealth));
    await settle();
    assert.match(find('[role="alert"]').textContent, /health backend down/);
});

test('a later health failure keeps rows but flags them stale instead of a silent Operational', async () => {
    let ok = true;
    state.route = () => (ok ? response(health('operational')) : response({ detail: 'timeout' }, 504));
    await render(el(C.SystemHealth));
    await settle();
    assert.match(text(), /Operational/);
    ok = false;
    await tick();
    assert.match(find('[role="alert"]').textContent, /last known|stale/i);
});

test('an unknown provider status is neutral, not green', async () => {
    state.route = () => response(health('maintenance'));
    await render(el(C.SystemHealth));
    await settle();
    const icon = find('.health-status-icon');
    assert.ok(icon);
    assert.ok(!icon.classList.contains('green'), `unknown status rendered green: ${icon.className}`);
    assert.ok(icon.classList.contains('gray'));
});
