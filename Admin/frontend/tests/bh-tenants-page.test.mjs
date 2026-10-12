import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, state, response, text, find, setInput, sleep, deferred } from './bh-harness.mjs';

const ME = { id: 'admin-1', email: 'admin@example.invalid', role: 'platform_admin' };
const row = (name, id) => ({
    id, business_name: name, plan_id: null, plan_name: 'Pro', minutes_used: 1, minutes_allocated: 10,
    status: 'active', user_count: 1, campaign_count: 0, max_concurrent_calls: 2, created_at: null,
});
const tenantReads = () => state.requests.filter((r) => r.method === 'GET' && r.path.startsWith('/api/v1/admin/tenants'));
// Production has no StrictMode double-effect, so count requests without it.
const draw = async () => {
    await act(async () => state.root.render(
        el(C.MemoryRouter, null, el(C.AuthProvider, null, el(C.TenantsPage)))));
    await act(async () => { await sleep(30); });
};
const type = async (value) => { await setInput(find('.search-input'), value); };
const wait = async (ms) => { await act(async () => { await sleep(ms); }); };

// admin-frontend-gateway-9 ---------------------------------------------------

test('the initial load fetches once, not twice', async () => {
    state.route = (call) => (call.path.endsWith('/auth/me') ? response(ME) : response([row('Alpha', 't1')]));
    await draw();
    await wait(400);
    assert.equal(tenantReads().length, 1, tenantReads().map((r) => r.path).join(' | '));
});

test('typing fires one debounced request for the final term, not one per keystroke', async () => {
    state.route = (call) => (call.path.endsWith('/auth/me') ? response(ME) : response([row('Alpha', 't1')]));
    await draw();
    await wait(400);
    const before = tenantReads().length;
    await type('a'); await wait(40);
    await type('ab'); await wait(40);
    await type('abc');
    assert.equal(tenantReads().length, before, 'no request may fire while the admin is still typing');
    await wait(450);
    const fresh = tenantReads().slice(before);
    assert.equal(fresh.length, 1, fresh.map((r) => r.path).join(' | '));
    assert.match(fresh[0].path, /search=abc/);
});

test('an out-of-order reply for an earlier term cannot overwrite the current results', async () => {
    const slowA = deferred();
    state.route = (call) => {
        if (call.path.endsWith('/auth/me')) return response(ME);
        if (call.path.includes('search=a') && !call.path.includes('search=ab')) return slowA.promise.then(() => response([row('Stale result for A', 't1')]));
        if (call.path.includes('search=ab')) return response([row('Result for AB', 't2')]);
        return response([row('Everything', 't0')]);
    };
    await draw();
    await wait(400);
    await type('a'); await wait(450);            // request for "a" is now in flight and slow
    await type('ab'); await wait(450);           // request for "ab" answers first
    assert.match(text(), /Result for AB/);
    await act(async () => slowA.resolve());
    assert.match(text(), /Result for AB/);
    assert.doesNotMatch(text(), /Stale result for A/);
});
