import assert from 'node:assert/strict';
import test, { beforeEach, after } from 'node:test';
import { C, api, el, act, dom, state, render, response, text, click, deferred } from './bh-harness.mjs';

// Actual React DOM and API client; only time and API responses are synthetic.
const timers = new Map(); let nextTimer = 1;
const oldWindowSet = dom.window.setInterval, oldWindowClear = dom.window.clearInterval;
const oldSet = globalThis.setInterval, oldClear = globalThis.clearInterval;
const set = (fn) => { const id = nextTimer++; timers.set(id, fn); return id; };
const clear = id => timers.delete(id);
dom.window.setInterval = globalThis.setInterval = set;
dom.window.clearInterval = globalThis.clearInterval = clear;
beforeEach(() => timers.clear());
after(() => {
    dom.window.setInterval = oldWindowSet; dom.window.clearInterval = oldWindowClear;
    globalThis.setInterval = oldSet; globalThis.clearInterval = oldClear;
});
const tick = async () => act(async () => { for (const fn of [...timers.values()]) fn(); });
const row = name => ({ id: `call-${name}`, tenant_id: 'tenant', tenant_name: name,
    phone_number: '+15555550100', status: 'in_call', campaign_name: 'Owned campaign', duration_seconds: 12 });
const pause = (paused, reason = null) => ({ paused, reason, paused_at: null, paused_by: null });
// StrictMode can start two requests; each HTTP request owns a distinct body.
const reply = pending => pending.promise.then(result => result.clone());
const route = (calls, status = () => response(pause(false)), put = () => response(pause(true))) => request => {
    if (request.path.endsWith('/calls/live')) return calls(request);
    if (request.path.endsWith('/calls/pause-status')) return status(request);
    if (request.method === 'PUT') return put(request);
    throw new Error(`Unexpected synthetic request ${request.path}`);
};

for (const component of ['LiveCalls', 'LiveCallsTable']) {
    test(`${component}: slow polls complete without overlapping interval requests or starving rows`, async () => {
        const old = deferred();
        state.route = route(() => reply(old));
        await render(el(C[component]));
        const reads = () => state.requests.filter(request => request.path.endsWith('/calls/live')).length;
        const before = reads();
        state.route = route(() => response([row('Current tenant')]));
        await tick(); await tick(); await tick();
        assert.equal(reads(), before);
        await act(async () => old.resolve(response([row('Stale tenant')])));
        assert.match(text(), /Stale tenant/);
        await tick();
        assert.match(text(), /Current tenant/);
        assert.doesNotMatch(text(), /Stale tenant/);
    });

    test(`${component}: account change clears old rows and ignores late old-session reads`, async () => {
        state.route = route(() => response([row('Private previous tenant')]));
        await render(el(C[component]));
        assert.match(text(), /Private previous tenant/);
        const old = deferred(); state.route = route(() => reply(old)); await tick();
        state.route = route(() => response({ detail: 'Replacement account denied' }, 403));
        await act(async () => api.setToken('replacement-session'));
        await act(async () => old.resolve(response([row('Private stale tenant')])));
        assert.doesNotMatch(text(), /Private previous tenant|Private stale tenant/);
        assert.match(text(), /Replacement account denied/);
    });

    test(`${component}: unmount retires polling and a late response cannot repopulate the view`, async () => {
        const pending = deferred();
        state.route = route(() => reply(pending));
        await render(el(C[component]));
        await render(null);
        const requestCount = state.requests.length;
        await act(async () => pending.resolve(response([row('Retired view')])));
        await tick();
        assert.equal(text(), ''); assert.equal(timers.size, 0);
        assert.equal(state.requests.length, requestCount);
    });

    test(`${component}: a poll started before confirmed termination cannot resurrect the ended call`, async () => {
        const call = row('Ending tenant'), pending = deferred();
        state.route = route(() => response([call]));
        await render(el(C[component]));
        let ended = false;
        state.route = request => {
            if (request.method === 'POST') {
                ended = true;
                return response({ status: 'confirmed', call_id: call.id, termination_status: 'confirmed',
                    provider_hangup_confirmed: true, provider_hangup_requested: true, termination_receipt_saved: true });
            }
            return route(() => ended ? response([]) : reply(pending))(request);
        };
        await tick(); await click('End'); await click('Yes');
        assert.doesNotMatch(text(), /Ending tenant/);
        await act(async () => pending.resolve(response([call])));
        assert.doesNotMatch(text(), /Ending tenant/);
    });
}

test('LiveCalls: a slow pause-status read is not starved by interval ticks', async () => {
    const old = deferred();
    state.route = route(() => response([]), () => reply(old));
    await render(el(C.LiveCalls));
    const count = () => state.requests.filter(request => request.path.endsWith('/calls/pause-status')).length;
    const before = count();
    await tick(); await tick(); await tick(); assert.equal(count(), before);
    await act(async () => { old.resolve(response(pause(true, 'Current operator decision'))); });
    assert.match(text(), /New outbound calls are paused/);
    assert.match(text(), /Current operator decision/);
});

test('LiveCallsTable: manual refresh retires an older read without letting it clear the newest state', async () => {
    state.route = route(() => response([row('Initial tenant')]));
    await render(el(C.LiveCallsTable));
    const old = deferred(); state.route = route(() => reply(old)); await click('Refresh');
    state.route = route(() => response([row('Current tenant')])); await click('Refresh');
    await act(async () => old.resolve(response([row('Retired tenant')])));
    assert.match(text(), /Current tenant/); assert.doesNotMatch(text(), /Retired tenant/);
});

for (const component of ['LiveCalls', 'LiveCallsTable']) {
    for (const malformed of [null, {}, [null], [{ id: 'broken-row' }]]) {
        test(`${component}: malformed successful payload ${JSON.stringify(malformed)} is unavailable, never empty`, async () => {
            state.route = route(() => response(malformed));
            await render(el(C[component]));
            assert.match(text(), /incomplete or invalid/i);
            assert.doesNotMatch(text(), /No active calls/i);
        });
    }
}

test('LiveCalls: a lost pause response cannot claim the emergency pause was not applied', async () => {
    state.route = route(() => response([]), () => response(pause(false)),
        () => response({ detail: 'Commit acknowledgement unavailable' }, 503));
    await render(el(C.LiveCalls)); await click('Pause All Calls'); await click('Yes');
    assert.match(text(), /Commit acknowledgement unavailable/);
    assert.doesNotMatch(text(), /Calls were NOT paused|Calls were NOT resumed/);
    assert.match(text(), /unconfirmed|unknown|not confirmed/i);
});

test('LiveCalls: double confirmation dispatches once and old-session pause success is discarded', async () => {
    const pending = deferred();
    state.route = route(() => response([]), () => response(pause(false)), () => reply(pending));
    await render(el(C.LiveCalls)); await click('Pause All Calls');
    await act(async () => {
        const confirm = [...state.host.querySelectorAll('button')].find(button => button.textContent === 'Yes');
        confirm.click(); confirm.click();
    });
    assert.equal(state.requests.filter(request => request.method === 'PUT').length, 1);
    state.route = route(() => response([]), () => response(pause(false)));
    await act(async () => api.setToken('replacement-session'));
    await act(async () => pending.resolve(response(pause(true, 'Previous account action'))));
    assert.doesNotMatch(text(), /Previous account action|New outbound calls are paused|Pause all calls\?/);
});

test('LiveCalls: a pause confirmation cannot become a resume after another operator pauses', async () => {
    state.route = route(() => response([]));
    await render(el(C.LiveCalls)); await click('Pause All Calls');
    state.route = route(() => response([]), () => response(pause(true, 'Other operator')));
    await tick(); await click('Yes');
    assert.equal(state.requests.filter(request => request.method === 'PUT').length, 0);
    assert.match(text(), /already paused/);
});
