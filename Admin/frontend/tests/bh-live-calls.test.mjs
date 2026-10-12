import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, dom, state, render, response, text, find, click, settle } from './bh-harness.mjs';

// LiveCalls polls on window.setInterval(5s); capture the tick so a test can fire it deterministically.
const ticks = [];
const realSetInterval = dom.window.setInterval.bind(dom.window);
const realClearInterval = dom.window.clearInterval.bind(dom.window);
dom.window.setInterval = (fn, ms) => { ticks.push(fn); return ticks.length; };
dom.window.clearInterval = () => {};
test.after(() => { dom.window.setInterval = realSetInterval; dom.window.clearInterval = realClearInterval; });
const tick = async () => { await act(async () => { for (const fn of [...ticks]) await fn(); }); };

const pauseBody = (paused, reason = null) => ({ paused, reason, paused_at: null, paused_by: null });
const route = ({ status, put }) => (call) => {
    if (call.path.endsWith('/admin/calls/live')) return response([]);
    if (call.path.endsWith('/admin/calls/pause-status')) return status(call);
    if (call.path.endsWith('/admin/calls/pause') && call.method === 'PUT') return put(call);
    return response({ detail: 'unrouted' }, 500);
};
const puts = () => state.requests.filter((r) => r.method === 'PUT');

// admin-frontend-gateway-3 ---------------------------------------------------

test('a failed pause PUT is surfaced: alert shown, confirm stays open, no paused banner', async () => {
    ticks.length = 0;
    state.route = route({ status: () => response(pauseBody(false)), put: () => response({ detail: 'Forbidden for this role' }, 403) });
    await render(el(C.LiveCalls));
    await settle();
    await click('Pause All Calls');
    assert.match(text(), /Pause all calls\?/);
    await click('Yes');
    assert.equal(puts().length, 1);
    const alert = find('[role="alert"]');
    assert.ok(alert, 'the operator must be told the emergency stop was not applied');
    assert.match(alert.textContent, /Forbidden for this role/);
    assert.match(text(), /Pause all calls\?/, 'confirm must stay open so the operator can retry');
    assert.doesNotMatch(text(), /New outbound calls are paused/);
});

test('a successful pause PUT shows the paused banner and closes the confirm', async () => {
    ticks.length = 0;
    state.route = route({ status: () => response(pauseBody(false)), put: () => response(pauseBody(true, 'Paused from Admin Command Center')) });
    await render(el(C.LiveCalls));
    await settle();
    await click('Pause All Calls');
    await click('Yes');
    assert.match(text(), /New outbound calls are paused/);
    assert.doesNotMatch(text(), /Pause all calls\?/);
    assert.equal(find('[role="alert"]'), null);
});

test('a failed initial pause-status read shows "unknown", never an implied not-paused state', async () => {
    ticks.length = 0;
    state.route = route({ status: () => response({ detail: 'status backend down' }, 500), put: () => response(pauseBody(true)) });
    await render(el(C.LiveCalls));
    await settle();
    const alert = find('[role="alert"]');
    assert.ok(alert, 'unknown pause state must be visible');
    assert.match(alert.textContent, /unknown/i);
});

test('the pause status is re-read on the live-call poll and recovers from unknown', async () => {
    ticks.length = 0;
    let healthy = false;
    state.route = route({
        status: () => (healthy ? response(pauseBody(true, 'Provider outage')) : response({ detail: 'status backend down' }, 500)),
        put: () => response(pauseBody(true)),
    });
    await render(el(C.LiveCalls));
    await settle();
    assert.match(find('[role="alert"]').textContent, /unknown/i);
    healthy = true;
    await tick();
    assert.match(text(), /New outbound calls are paused/);
    assert.match(text(), /Provider outage/);
    assert.doesNotMatch(text(), /unknown/i);
});
