import assert from 'node:assert/strict';
import test from 'node:test';
import { Routes, Route } from 'react-router-dom';
import { C, api, el, act, state, render, response, find, text, click, settle, deferred } from './bh-harness.mjs';

const ME = { id: 'current-member', email: 'member@example.invalid', name: 'Current member', role: 'tenant_admin', tenant_id: 'current-tenant' };
const action = { id: 'owned-action', tenant_id: ME.tenant_id, tenant_name: 'Private tenant receipt',
    type: 'send_email', status: 'cancelled', created_at: '2026-10-21T05:00:00Z', duration_ms: null,
    triggered_by: 'manual', is_retryable: false, is_cancellable: false };
const denied = () => response({ detail: 'Admin access required' }, 403);
let currentAuth;
function Probe() { currentAuth = C.useAuth(); return null; }
const draw = async () => {
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: ['/actions'] }, el(Probe),
        el(Routes, null, el(Route, { path: '/actions', element: el(C.AdminRouteGuard, null, el(C.ActionsPage)) }),
            el(Route, { path: '/login', element: el(C.LoginPage) }))))); await settle();
};
const identityReads = () => state.requests.filter(request => request.path.endsWith('/auth/me'));

test('a current protected403 discovers role removal and retires the previous header and route', async () => {
    let demoted = false;
    state.route = request => request.path.endsWith('/auth/me') ? response({ ...ME, role: demoted ? 'readonly' : ME.role })
        : request.path.startsWith('/api/v1/admin/actions') ? demoted ? denied() : response({ items: [action], total: 1 })
        : response({}, 500);
    await draw(); assert.match(text(), /Private tenant receipt/); assert.match(find('.header').textContent, /tenant admin/);
    demoted = true; await click('Refresh Log'); await settle();
    assert.match(text(), /Access Denied/);
    assert.equal(find('.header'), null); assert.doesNotMatch(text(), /Private tenant receipt/);
    assert.equal(currentAuth.user.role, 'readonly');
    assert.equal(state.requests.filter(request => request.path.startsWith('/api/v1/admin/actions')).length, 4,
        'StrictMode performs two mount reads per table; no extra forbidden operation is replayed');
});

const list = () => response({ items: [action], total: 1 });
const baseRoute = request => request.path.endsWith('/auth/me') ? response(ME)
    : request.path.endsWith(`/actions/${action.id}`) ? response(action)
    : request.path.startsWith('/api/v1/admin/actions') ? list() : denied();

test('a concurrent owned protected403 retires an already-rendered action drawer when the canonical role changed', async () => {
    state.route = baseRoute; await draw();
    await act(async () => find('tbody tr').click()); await settle();
    assert.ok(find('[role="dialog"]')); assert.match(find('[role="dialog"]').textContent, /Private tenant receipt/);
    const generation = api.getAuthGeneration();
    state.route = request => request.path.endsWith('/auth/me') ? response({ ...ME, role: 'readonly' }) : denied();
    // Models an existing concurrent protected request, not a click through the
    // modal's inert background. No invented production UI control is involved.
    await act(async () => { await api.getDashboardStats(); }); await settle();
    assert.ok(api.getAuthGeneration() > generation); assert.equal(find('[role="dialog"]'), null);
    assert.equal(find('.header'), null); assert.doesNotMatch(text(), /Private tenant receipt/); assert.match(text(), /Access Denied/);
});

test('ordinary unchanged-role403 retains identity and returns the original error without replaying a mutation or looping', async () => {
    state.route = baseRoute; await draw(); const generation = api.getAuthGeneration(), token = api.getToken(), reads = identityReads().length;
    state.route = request => request.path.endsWith('/auth/me') ? response(ME) : denied();
    let result; await act(async () => { result = await api.updateUser('owned-other-user', { role: 'readonly' }); }); await settle();
    assert.equal(result.error.status, 403); assert.equal(result.error.message, 'Admin access required');
    assert.equal(api.getAuthGeneration(), generation); assert.equal(api.getToken(), token);
    assert.equal(currentAuth.user.role, 'tenant_admin'); assert.match(find('.header').textContent, /tenant admin/);
    assert.equal(identityReads().length, reads + 1);
    assert.equal(state.requests.filter(request => request.method === 'PATCH').length, 1);
    assert.equal(state.requests.filter(request => request.path.endsWith('/auth/refresh')).length, 0);
});

test('concurrent current403 responses coalesce into one canonical authority read', async () => {
    state.route = baseRoute; await draw(); const gate = deferred(), reads = identityReads().length;
    state.route = request => request.path.endsWith('/auth/me') ? gate.promise : denied();
    let first, second;
    await act(async () => { first = api.getDashboardStats(); second = api.getSystemHealth(); }); await settle();
    assert.equal(identityReads().length, reads + 1);
    await act(async () => { gate.resolve(response(ME)); await Promise.all([first, second]); });
    assert.equal((await first).error.status, 403); assert.equal((await second).error.status, 403);
    assert.equal(identityReads().length, reads + 1);
});

for (const kind of ['outage', 'malformed']) {
    test(`canonical identity ${kind} after403 masks sensitive views without falsely claiming role removal`, async () => {
        state.route = baseRoute; await draw(); const token = api.getToken(), generation = api.getAuthGeneration();
        state.route = request => request.path.endsWith('/auth/me')
            ? kind === 'outage' ? response({ detail: 'Authority read unavailable' }, 503) : response({ id: ME.id, role: {} }) : denied();
        await act(async () => { await api.getDashboardStats(); }); await settle();
        assert.equal(find('.header'), null); assert.doesNotMatch(text(), /Private tenant receipt/);
        assert.match(text(), /Could not verify your current permissions/); assert.doesNotMatch(text(), /Access Denied|revoked/);
        assert.equal(currentAuth.user, null); assert.equal(api.getToken(), token); assert.equal(api.getAuthGeneration(), generation);
        state.route = baseRoute; await click('Retry session check'); await settle();
        assert.equal(currentAuth.user.role, 'tenant_admin'); assert.match(text(), /Private tenant receipt/);
    });
}

for (const late of ['role', 'outage']) {
    test(`late ${late} authority result cannot retire or mask a replacement identity`, async () => {
        state.route = baseRoute; await draw(); const gate = deferred();
        const replacement = { ...ME, id: 'replacement-member', email: 'replacement@example.invalid' };
        state.route = request => request.path.endsWith('/auth/me')
            ? request.authorization === 'Bearer replacement-token' ? response(replacement) : gate.promise
            : request.path.startsWith('/api/v1/admin/actions') ? list() : denied();
        let old; await act(async () => { old = api.getDashboardStats(); }); await settle();
        await act(async () => { api.setToken('replacement-token'); await currentAuth.checkAuth(); }); await settle();
        await act(async () => { gate.resolve(late === 'role' ? response({ ...ME, role: 'readonly' }) : response({}, 503)); await old; }); await settle();
        assert.equal(api.getToken(), 'replacement-token'); assert.equal(currentAuth.user.id, replacement.id);
        assert.equal(currentAuth.error, null); assert.match(find('.header').textContent, /replacement@example.invalid/);
    });
}

test('foreign canonical identity after403 is rejected instead of being adopted', async () => {
    state.route = baseRoute; await draw();
    state.route = request => request.path.endsWith('/auth/me') ? response({ ...ME, id: 'foreign-member' }) : denied();
    await act(async () => { await api.getDashboardStats(); }); await settle();
    assert.equal(api.getToken(), null); assert.equal(currentAuth.user, null); assert.equal(find('.header'), null);
    assert.doesNotMatch(text(), /Private tenant receipt/);
});

test('a protected blob403 uses the same bounded canonical role check without downloading or replaying media', async () => {
    state.route = baseRoute; await draw(); const reads = identityReads().length;
    state.route = request => request.path.endsWith('/auth/me') ? response({ ...ME, role: 'readonly' }) : denied();
    await act(async () => { await assert.rejects(() => api.getAdminRecordingAudio('owned-recording'), /Admin access required/); }); await settle();
    assert.equal(currentAuth.user.role, 'readonly'); assert.match(text(), /Access Denied/);
    assert.equal(state.requests.filter(request => request.path.endsWith('/audio')).length, 1);
    assert.equal(identityReads().length, reads + 2, 'one authority read plus existing AuthProvider verification after role retirement');
});

test('an unverified standalone view reports403 without a recursive identity-check/remount loop', async () => {
    api.setToken('unverified-token'); const generation = api.getAuthGeneration(); state.route = denied;
    await render(el(C.ActionsTable, { onActionSelect() {} })); await settle();
    assert.match(text(), /Admin access required/); assert.equal(identityReads().length, 0);
    assert.equal(api.getAuthGeneration(), generation);
    assert.equal(state.requests.length, 2, 'only the two StrictMode initial reads occur');
});
