import assert from 'node:assert/strict';
import test from 'node:test';
import { Routes, Route, useLocation } from 'react-router-dom';
import { C, api, el, act, dom, state, render, response, find, text, click, setInput, deferred, settle } from './bh-harness.mjs';

const ME = { id: 'admin-A', email: 'a@example.invalid', name: 'Admin A', role: 'platform_admin', tenant_id: null };
const signedIn = { access_token: 'access-A', user_id: ME.id, email: ME.email, role: ME.role, tenant_id: null };
const challenge = { access_token: '', mfa_required: true, mfa_challenge_token: 'one-use-challenge-A' };
const denied = () => response({ detail: 'Authentication required' }, 401);
let currentAuth;
function Probe() {
    currentAuth = C.useAuth();
    const where = useLocation();
    return el('div', null, el('p', { 'data-principal': true }, currentAuth.user?.id ?? 'signed out'),
        el('p', { 'data-location': true }, where.pathname), el('p', { 'data-auth-error': true }, currentAuth.error),
        currentAuth.isAuthenticated ? el(C.Header) : null, el(C.Sidebar));
}
const principal = () => find('[data-principal]')?.textContent;
const draw = async (path = '/login', returnTo) => {
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: [{ pathname: path, state: returnTo ? { from: { pathname: returnTo } } : undefined }] },
        el(Probe), el(Routes, null, el(Route, { path: '/login', element: el(C.LoginPage) }), el(Route, { path: '*', element: el('p', null, 'Available section') })))));
};
const signIn = async () => {
    await setInput(find('#email'), ME.email); await setInput(find('#password'), 'synthetic-password'); await click('Sign In');
};
const publicRoute = (request) => {
    if (request.path.endsWith('/auth/login')) return response(challenge);
    if (request.path.endsWith('/auth/me')) return request.authorization === 'Bearer access-A' ? response(ME) : denied();
    if (request.path.endsWith('/auth/refresh')) return denied();
    return response({}, 500);
};
test.beforeEach(() => { api.setToken(null); state.route = publicRoute; });

for (const variant of ['stored', 'cookie-only', 'rejected']) {
    test(`bootstrap verifies ${variant} authority using the canonical endpoint`, async () => {
        if (variant !== 'cookie-only') localStorage.setItem('admin_token', 'saved-A');
        state.route = () => variant === 'rejected' ? denied() : response(ME);
        await draw();
        assert.equal(principal(), variant === 'rejected' ? 'signed out' : ME.id);
        assert.equal(api.getToken(), variant === 'stored' ? 'saved-A' : null);
        const reads = state.requests.filter((request) => request.path.endsWith('/auth/me'));
        assert.ok(reads.length > 0);
        assert.ok(reads.every((request) => request.credentials === 'include'));
        assert.ok(reads.every((request) => request.authorization === (variant === 'cookie-only' ? undefined : 'Bearer saved-A')));
    });
}

for (const recovery of [false, true]) {
    test(`${recovery ? 'alphanumeric recovery code' : 'authenticator code'} completes MFA only after canonical identity verification`, async () => {
        state.route = (request) => request.path.endsWith('/auth/mfa/verify') ? response(signedIn) : publicRoute(request);
        await draw('/login', '/users'); await signIn();
        assert.equal(principal(), 'signed out'); assert.equal(api.getToken(), null);
        assert.equal(find('#password'), null);
        if (recovery) await click('Use a recovery code');
        const value = recovery ? 'AbCdEfGh-IjKlMnOp' : '123456';
        await setInput(find('#factor'), value); await click('Verify');
        assert.equal(principal(), ME.id); assert.equal(find('[data-location]').textContent, '/users');
        const factors = state.requests.filter((request) => request.path.endsWith('/auth/mfa/verify'));
        assert.equal(factors.length, 1);
        assert.deepEqual(factors[0].body, { challenge_token: challenge.mfa_challenge_token,
            ...(recovery ? { recovery_code: value } : { code: value }) });
        assert.equal(factors[0].authorization, undefined); assert.equal(factors[0].credentials, 'include');
        const verified = state.requests.filter((request) => request.authorization === 'Bearer access-A' && request.path.endsWith('/auth/me'));
        assert.ok(verified.length > 0); assert.equal(verified[0].headers['X-Talky-Expected-User'], ME.id);
        assert.equal(localStorage.getItem('mfa_challenge_token'), null);
        assert.equal(localStorage.getItem('refresh_token'), null);
    });
}

test('rejected or expired MFA challenge never authenticates and can be restarted', async () => {
    state.route = (request) => request.path.endsWith('/auth/mfa/verify') ? denied() : publicRoute(request);
    await draw(); await signIn(); await setInput(find('#factor'), '123456'); await click('Verify');
    assert.equal(principal(), 'signed out'); assert.ok(find('#factor')); assert.equal(api.getToken(), null);
    await click('Back to sign in'); assert.ok(find('#password'));
    assert.equal(state.requests.filter((request) => request.path.endsWith('/auth/mfa/verify')).length, 1);
});

test('lost MFA success is unconfirmed and does not automatically consume the factor again', async () => {
    state.route = (request) => request.path.endsWith('/auth/mfa/verify')
        ? response({ detail: 'Proxy response lost' }, 502) : publicRoute(request);
    await draw(); await signIn(); await click('Use a recovery code');
    await setInput(find('#factor'), 'AbCdEfGh-IjKlMnOp'); await click('Verify');
    assert.equal(principal(), 'signed out'); assert.equal(find('#factor'), null);
    assert.match(text(), /may already have been used/);
    assert.equal(state.requests.filter((request) => request.path.endsWith('/auth/mfa/verify')).length, 1);
});

test('login uses the current server role rather than the optimistic credential response', async () => {
    state.route = (request) => request.path.endsWith('/auth/login') ? response(signedIn)
        : request.authorization === 'Bearer access-A' ? response({ ...ME, role: 'tenant_admin' }) : denied();
    await draw('/login', '/users'); await signIn();
    assert.equal(principal(), ME.id); assert.equal(find('[data-location]').textContent, '/actions');
    assert.equal(find('a[href="/system-health"]'), null); assert.equal(find('a[href="/incidents"]'), null);
    assert.ok(find('a[href="/actions"]'));
});

test('login remains unverified on a canonical identity outage and a deliberate check recovers', async () => {
    let available = false;
    state.route = (request) => request.path.endsWith('/auth/login') ? response(signedIn)
        : request.authorization === 'Bearer access-A' ? available ? response(ME) : response({ detail: 'Identity service unavailable' }, 503) : denied();
    await draw(); await signIn();
    assert.equal(principal(), 'signed out'); assert.equal(api.getToken(), 'access-A');
    available = true; await click('Retry session check'); assert.equal(principal(), ME.id);
});

test('logout clears sensitive UI immediately and distinguishes unconfirmed server revocation', async () => {
    const pending = deferred();
    api.setToken('access-A');
    state.route = (request) => request.path.endsWith('/auth/logout') ? pending.promise : response(ME);
    await draw('/actions'); await click('Sign out');
    assert.equal(principal(), 'signed out'); assert.equal(api.getToken(), null);
    await act(async () => pending.resolve(response({ detail: 'Gateway timed out' }, 504)));
    assert.match(text(), /Server sign-out could not be confirmed/);
    const request = state.requests.find((value) => value.path.endsWith('/auth/logout'));
    assert.equal(request.credentials, 'include'); assert.equal(request.authorization, 'Bearer access-A');
    assert.equal(request.headers['X-Talky-Expected-User'], ME.id);
    const before = state.requests.length;
    await act(async () => currentAuth.checkAuth());
    assert.equal(state.requests.length, before, 'an uncertain logout must not rediscover its still-live cookie automatically');
});

test('cross-tab token change masks old identity until the new account is verified without rewriting shared storage', async () => {
    api.setToken('access-A');
    const pending = deferred();
    state.route = (request) => request.authorization === 'Bearer access-B' ? pending.promise.then((value) => response(value)) : response(ME);
    await draw('/actions');
    await act(async () => {
        localStorage.setItem('admin_token', 'access-B');
        window.dispatchEvent(new dom.window.StorageEvent('storage', { key: 'admin_token', newValue: 'access-B' }));
    });
    assert.equal(principal(), 'signed out'); assert.equal(localStorage.getItem('admin_token'), 'access-B');
    await act(async () => pending.resolve({ ...ME, id: 'admin-B', email: 'b@example.invalid' }));
    assert.equal(principal(), 'admin-B');
});

test('cookie-only identity marker retires the previous generation without clearing a sibling token', async () => {
    api.setToken('access-A'); state.route = () => response(ME); await draw('/actions');
    const before = api.getAuthGeneration();
    await act(async () => window.dispatchEvent(new dom.window.StorageEvent('storage', {
        key: 'talky.admin.identity.v1', newValue: JSON.stringify({ kind: 'logout', nonce: 'other-tab-logout' }),
    })));
    assert.equal(principal(), 'signed out'); assert.ok(api.getAuthGeneration() > before);
    assert.equal(localStorage.getItem('admin_token'), 'access-A', 'receipt consumption does not write another tab’s storage');
});

test('blocked storage still permits in-memory sign-in and verified cookie requests', async () => {
    const prototype = dom.window.Storage.prototype, originalGet = prototype.getItem, originalSet = prototype.setItem, originalRemove = prototype.removeItem;
    prototype.getItem = prototype.setItem = prototype.removeItem = () => { throw new Error('Storage blocked'); };
    state.route = (request) => request.path.endsWith('/auth/login') ? response(signedIn)
        : request.authorization === 'Bearer access-A' ? response(ME) : denied();
    try { await draw(); await signIn(); assert.equal(principal(), ME.id); }
    finally { prototype.getItem = originalGet; prototype.setItem = originalSet; prototype.removeItem = originalRemove; }
});

test('all supported roles and aliases share sidebar, landing, and direct-route policy', async () => {
    for (const role of ['platform_admin', 'super_admin', 'partner_admin', 'tenant_admin', 'admin', 'owner', 'readonly', 'user', 'unknown']) {
        const platform = ['platform_admin', 'super_admin'].includes(role), scoped = ['partner_admin', 'tenant_admin', 'admin', 'owner'].includes(role);
        for (const rule of C.ADMIN_ROUTES) {
            const permitted = platform || (scoped && rule.path === '/actions');
            assert.equal(C.canAccessAdminRoute(rule.path, role), permitted, `${role} ${rule.path}`);
            assert.equal(C.safeAdminReturn(rule.path, role), permitted ? rule.path : C.adminLanding(role));
        }
        assert.equal(C.safeAdminReturn('https://untrusted.invalid/', role), C.adminLanding(role));
    }
    api.setToken('access-A'); state.route = () => response({ ...ME, role: 'tenant_admin' });
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: ['/system-health'] },
        el(C.AdminRouteGuard, null, el('p', null, 'Private platform health')))));
    assert.match(text(), /Access Denied/); assert.doesNotMatch(text(), /Private platform health/);
    assert.ok(find('a[href="/actions"]')); assert.ok([...state.host.querySelectorAll('button')].some((node) => node.textContent === 'Sign out'));
});

test('concurrent expired reads share one refresh, retain identity ownership, and retry only once', async () => {
    api.setToken('expired-A'); api.expectIdentity({ userId: ME.id, tenantId: null, role: ME.role });
    const generation = api.getAuthGeneration(), pending = deferred();
    state.route = (request) => request.path.endsWith('/auth/refresh') ? pending.promise
        : request.authorization === 'Bearer access-A' ? response({ ok: true }) : denied();
    const reads = [api.getTenants(), api.getUsersList()]; await settle();
    assert.equal(state.requests.filter((request) => request.path.endsWith('/auth/refresh')).length, 1);
    await act(async () => pending.resolve(response(signedIn)));
    assert.ok((await Promise.all(reads)).every((result) => result.data?.ok));
    assert.equal(api.getAuthGeneration(), generation);
    const refresh = state.requests.find((request) => request.path.endsWith('/auth/refresh'));
    assert.equal(refresh.authorization, undefined); assert.equal(refresh.credentials, 'include');
    assert.equal(refresh.headers['X-Talky-Expected-User'], ME.id); assert.equal(refresh.headers['X-Talky-Expected-Tenant'], '');
    assert.equal(state.requests.length, 5);
});

test('an old-token 401 after rotation reuses the validated successor without a second rotation', async () => {
    api.setToken('expired-A'); api.expectIdentity({ userId: ME.id, tenantId: null, role: ME.role });
    const late = deferred();
    state.route = (request) => {
        if (request.path.endsWith('/auth/refresh')) return response(signedIn);
        if (request.authorization === 'Bearer access-A') return response({ ok: true });
        return request.path.includes('/admin/users') ? late.promise : denied();
    };
    const old = api.getUsersList(); await api.getTenants();
    late.resolve(denied()); assert.ok((await old).data?.ok);
    assert.equal(state.requests.filter((request) => request.path.endsWith('/auth/refresh')).length, 1);
    assert.equal(api.getToken(), 'access-A');
});

test('a mutation rejected for auth is never replayed automatically after renewal', async () => {
    api.setToken('expired-A'); api.expectIdentity({ userId: ME.id, tenantId: null, role: ME.role });
    state.route = (request) => request.path.endsWith('/auth/refresh') ? response(signedIn) : denied();
    const result = await api.deleteAdminRecording('recording-A', 'Reviewed deletion', 'exact-original-key');
    assert.equal(result.error.code, 'AUTH_RETRY_REQUIRED');
    const writes = state.requests.filter((request) => request.method === 'DELETE');
    assert.equal(writes.length, 1); assert.equal(writes[0].headers['Idempotency-Key'], 'exact-original-key');
    assert.equal(api.getToken(), 'access-A');
});

test('network or proxy failures never replay a consequential request', async () => {
    api.setToken('access-A');
    state.route = () => { throw new Error('Connection lost after send'); };
    const result = await api.deleteAdminRecording('recording-A', 'Reviewed deletion', 'exact-original-key');
    assert.equal(result.error.code, 'NETWORK_ERROR'); assert.equal(state.requests.length, 1);
});

test('refresh outage retains the current session and foreign refresh cannot adopt another identity', async () => {
    api.setToken('expired-A'); api.expectIdentity({ userId: ME.id, tenantId: null, role: ME.role });
    state.route = (request) => request.path.endsWith('/auth/refresh') ? response({ detail: 'Temporarily unavailable' }, 503) : denied();
    assert.equal((await api.getTenants()).error.status, 503); assert.equal(api.getToken(), 'expired-A');
    state.route = (request) => request.path.endsWith('/auth/refresh') ? response({ ...signedIn, user_id: 'foreign-B' }) : denied();
    assert.equal((await api.getTenants()).error.code, 'IDENTITY_CHANGED'); assert.equal(api.getToken(), null);
});

test('a late refresh cannot restore the previous session after logout or account change', async () => {
    api.setToken('expired-A'); api.expectIdentity({ userId: ME.id, tenantId: null, role: ME.role });
    const pending = deferred();
    state.route = (request) => request.path.endsWith('/auth/refresh') ? pending.promise : denied();
    const read = api.getTenants(); await settle(); api.setToken('new-account-B');
    pending.resolve(response(signedIn)); assert.equal((await read).error.code, 'IDENTITY_CHANGED');
    assert.equal(api.getToken(), 'new-account-B');
});

test('read retry is bounded and a second 401 retires the refreshed session', async () => {
    api.setToken('expired-A'); api.expectIdentity({ userId: ME.id, tenantId: null, role: ME.role });
    state.route = (request) => request.path.endsWith('/auth/refresh') ? response(signedIn) : denied();
    assert.equal((await api.getTenants()).error.status, 401);
    assert.equal(state.requests.length, 3); assert.equal(api.getToken(), null);
});

test('refresh Origin rejection retains the real 403 without falsely revoking the session', async () => {
    api.setToken('expired-A'); api.expectIdentity({ userId: ME.id, tenantId: null, role: ME.role });
    state.route = (request) => request.path.endsWith('/auth/refresh') ? response({ detail: 'Origin not permitted.' }, 403) : denied();
    const result = await api.getTenants();
    assert.equal(result.error.status, 403); assert.equal(result.error.code, 'SESSION_RENEWAL_UNAVAILABLE');
    assert.equal(api.getToken(), 'expired-A');
});

test('same-origin refresh locking serializes independent API owners using the shared cookie family', async () => {
    const original = Object.getOwnPropertyDescriptor(dom.window.navigator, 'locks');
    let queue = Promise.resolve(), active = 0, maximum = 0;
    const names = [];
    Object.defineProperty(dom.window.navigator, 'locks', { configurable: true, value: {
        request(name, _options, callback) {
            names.push(name); const pending = queue.then(callback);
            queue = pending.catch(() => {}); return pending;
        },
    } });
    const second = new api.constructor('http://localhost:8000/api/v1');
    api.setToken('expired-A'); second.setToken('expired-A');
    for (const owner of [api, second]) owner.expectIdentity({ userId: ME.id, tenantId: null, role: ME.role });
    state.route = async (request) => {
        if (!request.path.endsWith('/auth/refresh')) return request.authorization === 'Bearer access-A' ? response({ ok: true }) : denied();
        active += 1; maximum = Math.max(maximum, active);
        await new Promise((resolve) => setTimeout(resolve, 10)); active -= 1;
        return response(signedIn);
    };
    try {
        const results = await Promise.all([api.getTenants(), second.getTenants()]);
        assert.ok(results.every((result) => result.data?.ok)); assert.equal(maximum, 1);
        assert.equal(names.length, 2); assert.equal(names[0], names[1]);
    } finally {
        if (original) Object.defineProperty(dom.window.navigator, 'locks', original); else delete dom.window.navigator.locks;
    }
});

test('a server-verified role removal retires privileged views during refresh', async () => {
    api.setToken('expired-A');
    state.route = (request) => request.path.endsWith('/auth/me') ? response(request.authorization === 'Bearer access-A' ? { ...ME, role: 'tenant_admin' } : ME)
        : request.path.endsWith('/auth/refresh') ? response({ ...signedIn, role: 'tenant_admin' })
            : request.authorization === 'Bearer access-A' ? response({}, 403) : denied();
    await draw('/actions'); const generation = api.getAuthGeneration();
    await act(async () => api.getTenants());
    assert.equal(currentAuth.user.role, 'tenant_admin'); assert.ok(api.getAuthGeneration() > generation);
    assert.equal(find('a[href="/system-health"]'), null); assert.ok(find('a[href="/actions"]'));
});

test('identity change during a factor attempt discards its late success and verifies the new session', async () => {
    const pending = deferred();
    state.route = (request) => request.path.endsWith('/auth/mfa/verify') ? pending.promise
        : request.authorization === 'Bearer access-B' ? response({ ...ME, id: 'admin-B', email: 'b@example.invalid' }) : publicRoute(request);
    await draw(); await signIn(); await setInput(find('#factor'), '123456'); await click('Verify');
    await act(async () => {
        localStorage.setItem('admin_token', 'access-B');
        window.dispatchEvent(new dom.window.StorageEvent('storage', { key: 'admin_token', newValue: 'access-B' }));
    });
    assert.equal(principal(), 'signed out'); assert.equal(find('#factor'), null);
    await act(async () => pending.resolve(response(signedIn)));
    assert.equal(principal(), 'admin-B'); assert.equal(api.getToken(), 'access-B');
});

for (const transport of ['request', 'blob', 'refresh']) {
    test(`actual normalized identity_changed 409 from ${transport} retires current authority`, async () => {
        api.setToken('access-A'); state.route = () => response(ME); await draw('/actions');
        const changed = () => response({ detail: { code: 'identity_changed', message: 'Your signed-in account changed.' } }, 409);
        state.route = (request) => transport === 'refresh' && !request.path.endsWith('/auth/refresh') ? denied() : changed();
        await act(async () => transport === 'blob'
            ? api.getAdminRecordingAudio('recording-A').catch(() => {}) : api.getTenants());
        assert.equal(principal(), 'signed out'); assert.equal(api.getToken(), null);
        assert.match(text(), /session.*changed/i);
    });
}

test('explicit session check completes after refresh discovers a changed role', async () => {
    api.setToken('expired-A'); let expired = false;
    state.route = (request) => {
        if (request.path.endsWith('/auth/refresh')) return response({ ...signedIn, role: 'tenant_admin' });
        if (request.authorization === 'Bearer access-A') return response({ ...ME, role: 'tenant_admin' });
        return expired ? denied() : response(ME);
    };
    await draw('/actions'); assert.equal(currentAuth.user.role, 'platform_admin');
    expired = true; await act(async () => currentAuth.checkAuth());
    assert.equal(currentAuth.isLoading, false); assert.equal(currentAuth.user.role, 'tenant_admin');
    assert.equal(find('a[href="/system-health"]'), null);
});
