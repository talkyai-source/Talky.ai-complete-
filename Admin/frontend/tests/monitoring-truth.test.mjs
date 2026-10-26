import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, state, render, response, text, find, settle, deferred } from './bh-harness.mjs';

const timers = new Map(); let nextTimer = 0;
const realSet = globalThis.setInterval, realClear = globalThis.clearInterval;
test.beforeEach(() => {
    timers.clear();
    globalThis.setInterval = (fn) => { timers.set(++nextTimer, fn); return nextTimer; };
    globalThis.clearInterval = (id) => timers.delete(id);
});
test.afterEach(() => { globalThis.setInterval = realSet; globalThis.clearInterval = realClear; });
const tick = async () => { await act(async () => { for (const fn of [...timers.values()]) fn(); }); await settle(); };
const time = '2026-10-21T00:00:00Z';
const health = { providers: [{ name: 'Database', status: 'operational', latency_ms: 12,
    latency_display: '12ms', last_check: time, scope: 'this API process', detail: 'SELECT 1 succeeded' }] };

test('unmeasured providers have neutral status, absent latency and explicit scope', async () => {
    state.route = () => response({ providers: [{ name: 'STT', status: 'unknown', latency_ms: null,
        latency_display: 'Unavailable', last_check: null, scope: 'no account probe', detail: 'No current measurement' }] });
    await render(el(C.SystemHealth)); await settle();
    assert.match(text(), /Not measured/); assert.match(text(), /Latency unavailable/);
    assert.match(text(), /no account probe/); assert.match(text(), /No observation/);
    assert.equal(find('.health-status-icon.green'), null);
});

test('failed later health reads remove the healthy colour while retaining dated evidence', async () => {
    let fail = false; state.route = () => fail ? response({ detail: 'database timeout' }, 503) : response(health);
    await render(el(C.SystemHealth)); await settle();
    assert.ok(find('.health-status-icon.green'));
    fail = true; await tick();
    assert.match(text(), /Last observed: Operational/);
    assert.match(text(), /database timeout/); assert.match(text(), /SELECT 1 succeeded/);
    assert.equal(find('.health-status-icon.green'), null);
    assert.equal(find('.operational'), null);
});

test('actual provider observations distinguish failed primary and successful fallback without claiming fleet health', async () => {
    const observation = { latency_ms: 85, latency_display: '85ms to first output', last_check: time,
        scope: 'tenant credential row-one; observed revision abcd1234', detail: 'Recent observed scope only.' };
    state.route = () => response({ providers: [
        { ...observation, name: 'LLM / primary', status: 'observed_failure' },
        { ...observation, name: 'LLM / fallback', status: 'observed_success' },
    ] });
    await render(el(C.SystemHealth)); await settle();
    assert.match(text(), /Last observed request failed/);
    assert.match(text(), /Last observed request succeeded/);
    assert.match(text(), /do not represent every configured account/);
    assert.match(text(), /85ms to first output/);
    assert.doesNotMatch(text(), /Operational|100%/);
    assert.ok(find('.health-status-icon.orange'));
    assert.ok(find('.health-status-icon.green'));
});

test('later observation read failure removes success colouring and preserves dated scope', async () => {
    let failed = false;
    state.route = () => failed ? response({ detail: 'observation endpoint unavailable' }, 503) : response({ providers: [{
        name: 'STT / flux', status: 'observed_success', latency_ms: null, latency_display: 'Not measured',
        last_check: time, scope: 'observed credential only', detail: 'Recognition accuracy is not measured here.',
    }] });
    await render(el(C.SystemHealth)); await settle();
    assert.match(text(), /Latency unavailable/);
    failed = true; await tick();
    assert.equal(find('.health-status-icon.green'), null);
    assert.match(text(), /Last observed: Last observed request succeeded/);
    assert.match(text(), /observed credential only/);
});

test('worker heartbeat does not invent processed jobs, success rate or task occupancy', async () => {
    state.route = () => response({ workers: [{ id: 'dialer', name: 'Dialer', status: 'healthy',
        current_task: null, processed_count: null, failed_count: null, success_rate: null, uptime_seconds: null,
        last_heartbeat: time, age_seconds: 2, detail: 'Heartbeat only; no job counters' }],
        total_workers: 1, active_workers: 1, busy_workers: null, checked_at: time, scope: 'role heartbeats; not instance inventory' });
    await render(el(C.WorkerStatusTable)); await settle();
    assert.match(text(), /1 roles with fresh heartbeats/); assert.match(text(), /not instance inventory/);
    assert.match(text(), /Unavailable/); assert.doesNotMatch(text(), /100%|Idle|Busy/);
});

test('unavailable queue measurements render neither zero nor a chart', async () => {
    state.route = () => response({ queues: [{ name: 'Dialer', pending: null, processing: null, failed: null,
        success_rate_24h: null, avg_processing_time_ms: null, status: 'unavailable', scope: 'Redis', detail: 'Redis unavailable' }],
        total_pending: null, total_processing: null, checked_at: time });
    await render(el(C.QueueDepthChart)); await settle();
    assert.match(text(), /Unavailable total pending/); assert.match(text(), /Redis unavailable/);
    assert.equal(find('.queue-bar'), null); assert.doesNotMatch(text(), /100%|0 pending|0ms/);
});

test('measured empty queue retains real zeros but leaves unmeasured history unavailable', async () => {
    state.route = () => response({ queues: [{ name: 'Dialer', pending: 0, processing: 0, failed: null,
        success_rate_24h: null, avg_processing_time_ms: null, status: 'measured', scope: 'Redis pending lists', detail: '' }],
        total_pending: 0, total_processing: 0, checked_at: time });
    await render(el(C.QueueDepthChart)); await settle();
    assert.match(text(), /0 total pending/); assert.ok(find('.queue-bar'));
    assert.match(text(), /Success \(24h\): Unavailable/); assert.doesNotMatch(text(), /100%/);
});

test('no traffic means no failure rate, with a displayed denominator', async () => {
    state.route = () => response({ active_calls: 0, active_tenants: 2, failed_calls_24h: 0,
        failed_call_rate_24h: null, total_calls_24h: 0, observed_at: time });
    await render(el(C.StatsGrid)); await settle();
    assert.match(text(), /No calls/); assert.match(text(), /0 calls created/);
    assert.match(text(), /No-answer calls are excluded/); assert.doesNotMatch(text(), /0%|API Errors/);
});

test('database disconnect does not fabricate pool size, table count or size', async () => {
    state.route = () => response({ connected: false, latency_ms: null, pool_size: null, active_connections: null,
        available_connections: null, table_count: null, database_size_mb: null, last_check: time,
        error: 'Pool unavailable', scope: 'this API database pool' });
    await render(el(C.DatabaseHealthCard)); await settle();
    assert.match(text(), /Disconnected/); assert.match(text(), /Pool unavailable/);
    assert.match(text(), /Unavailable active \/ Unavailable allocated/);
    assert.equal(find('.badge-success'), null);
});

test('missing failure rate cannot turn nonzero traffic into no calls', async () => {
    state.route = () => response({ active_calls: 1, active_tenants: 2, failed_calls_24h: 1,
        failed_call_rate_24h: null, total_calls_24h: 50, observed_at: time });
    await render(el(C.StatsGrid)); await settle();
    assert.match(text(), /Unavailable/); assert.match(text(), /50 calls created/);
    assert.doesNotMatch(text(), /No calls/);
});

test('polling coalesces while a read is pending and retires old-session results', async () => {
    const pending = deferred(); state.route = () => pending.promise.then((r) => r.clone());
    await render(el(C.SystemHealth)); await settle();
    const initialCount = state.requests.length;
    await tick(); await tick(); assert.equal(state.requests.length, initialCount);
    state.route = () => response({ providers: [{ ...health.providers[0], name: 'New session database' }] });
    await act(async () => api.setToken('new-monitoring-session')); await settle();
    assert.match(text(), /New session database/);
    await act(async () => pending.resolve(response({ providers: [{ ...health.providers[0], name: 'Previous session secret' }] })));
    assert.doesNotMatch(text(), /Previous session secret/);
});


for (const component of ['SystemHealth', 'StatsGrid', 'WorkerStatusTable', 'QueueDepthChart', 'HealthOverviewCards', 'DatabaseHealthCard']) {
    test(`${component} treats malformed 200 JSON as unavailable without crashing`, async () => {
        state.route = () => response({});
        await render(el(C[component])); await settle();
        assert.match(find('[role="alert"]').textContent, /incomplete or unexpected response/);
        assert.doesNotMatch(text(), /No calls|100%/);
    });
}
