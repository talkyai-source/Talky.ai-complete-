import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, dom, state, render, response, find, click, setInput, deferred, settle, installSessionLocks } from './bh-harness.mjs';

const A = { id: 'cookie-A', email: 'a@example.invalid', role: 'platform_admin', tenant_id: null };
const B = { id: 'cookie-B', email: 'b@example.invalid', role: 'tenant_admin', tenant_id: 'tenant-B' };
const signed = (user, token) => ({ access_token: token, user_id: user.id, tenant_id: user.tenant_id, role: user.role });
// Synthetic Web Locks scheduler shared by independent API owners. The DOM is
// real React; the cookie label below models response-header ordering only.
// Actual browser cookie behavior is verified separately against normal ASGI.
const installLocks = installSessionLocks;

const independent = (token = 'access-A', identity = A) => {
    const client = new api.constructor('http://localhost:8000/api/v1');
    client.setToken(token, false); client.expectIdentity({ userId: identity.id, tenantId: identity.tenant_id, role: identity.role });
    return client;
};
const holdFirstLogin = async () => {
    const gate = deferred();
    state.route = request => request.body?.email === 'holding@example.invalid' ? gate.promise : response({});
    const first = independent().login('holding@example.invalid', 'synthetic-password'); await settle();
    return { first, release: async () => { gate.resolve(response(signed(A, 'other-A'))); await first; await settle(); } };
};

for (const kind of ['login', 'MFA']) {
    test(`queued ${kind} is discarded if the tab adopts another identity before transport`, async () => {
        installLocks(); const blocker = await holdFirstLogin();
        const next = kind === 'login' ? api.login(B.email, 'synthetic-password')
            : api.verifyMfaChallenge('one-use-challenge', { code: '123456' });
        api.setToken('replacement');
        await blocker.release();
        assert.equal((await next).error.code, 'IDENTITY_CHANGED');
        assert.equal(state.requests.length, 1, 'no stale credentials or one-use factor may reach the server');
    });
}

test('queued MFA also checks its operation owner when the challenge is retired without a token change', async () => {
    installLocks(); const blocker = await holdFirstLogin(); let owned = true;
    const next = api.verifyMfaChallenge('one-use-challenge', { recovery_code: 'synthetic-one-use-code' }, () => owned);
    owned = false; await blocker.release();
    assert.equal((await next).error.code, 'IDENTITY_CHANGED'); assert.equal(state.requests.length, 1);
});

test('queued logout preserves captured bearer and expected identity after its UI masks local credentials', async () => {
    installLocks(); const blocker = await holdFirstLogin();
    api.setToken('reviewed-A'); api.expectIdentity({ userId: A.id, tenantId: null, role: A.role });
    const logout = api.logout(); api.invalidateIdentity();
    state.route = request => request.path.endsWith('/auth/logout')
        ? response({ detail: { code: 'identity_changed', message: 'Cookies changed' } }, 409) : response({});
    await blocker.release();
    assert.equal((await logout).error.code, 'IDENTITY_CHANGED');
    const request = state.requests.find(row => row.path.endsWith('/auth/logout'));
    assert.equal(request.authorization, 'Bearer reviewed-A');
    assert.equal(request.headers['X-Talky-Expected-User'], A.id);
    assert.equal(request.headers['X-Talky-Expected-Tenant'], '');
    assert.equal(request.credentials, 'include');
    assert.equal(state.requests.filter(row => row.path.endsWith('/auth/logout')).length, 1);
    assert.equal(state.requests.filter(row => row.path.endsWith('/auth/refresh')).length, 0);
});

test('queued logout cannot target a replacement identity even when its original credentials were cleared', async () => {
    installLocks(); const blocker = await holdFirstLogin(); let owned = true;
    const logout = api.logout(() => owned); api.invalidateIdentity(); owned = false;
    await blocker.release();
    assert.equal((await logout).error.code, 'IDENTITY_CHANGED'); assert.equal(state.requests.length, 1);
});

test('queued refresh is discarded after a newer principal replaces its owner', async () => {
    installLocks(); const blocker = await holdFirstLogin();
    state.route = () => response({ detail: 'Expired access token' }, 401);
    const read = api.getDashboardStats(); await settle(); api.setToken('replacement-B');
    await blocker.release();
    assert.equal((await read).error.code, 'IDENTITY_CHANGED');
    assert.equal(api.getToken(), 'replacement-B');
    assert.equal(state.requests.filter(row => row.path.endsWith('/auth/refresh')).length, 0);
});

test('MFA and logout wait for an admitted refresh response, without repeating a one-use factor', async () => {
    const names = installLocks(); const refreshGate = deferred();
    api.setToken('expired-A'); api.expectIdentity({ userId: A.id, tenantId: null, role: A.role });
    state.route = request => {
        if (request.path.endsWith('/auth/refresh')) return refreshGate.promise;
        if (request.path.endsWith('/admin/dashboard/stats') && request.authorization === 'Bearer expired-A') return response({}, 401);
        if (request.path.endsWith('/auth/mfa/verify')) return response(signed(B, 'access-B'));
        return response({});
    };
    const read = api.getDashboardStats(); await settle();
    const second = independent('unrelated-B', B);
    const mfa = second.verifyMfaChallenge('challenge-B', { recovery_code: 'one-use-B' });
    const logout = independent('reviewed-C').logout(); await settle();
    assert.equal(state.requests.filter(row => /mfa\/verify|logout/.test(row.path)).length, 0);
    refreshGate.resolve(response(signed(A, 'renewed-A')));
    await Promise.all([read, mfa, logout]);
    assert.equal(state.requests.filter(row => row.path.endsWith('/auth/mfa/verify')).length, 1);
    assert.equal(state.requests.filter(row => row.path.endsWith('/auth/logout')).length, 1);
    assert.equal(new Set(names).size, 1);
});

test('a failed transport releases the shared lock without replaying logout', async () => {
    installLocks(); const gate = deferred();
    state.route = request => request.path.endsWith('/auth/logout') ? gate.promise : response(signed(B, 'access-B'));
    const logout = independent().logout(); await settle();
    const login = independent(null, B).login(B.email, 'synthetic-password'); await settle();
    assert.equal(state.requests.length, 1);
    gate.resolve(response({ detail: 'Gateway unavailable' }, 503));
    assert.equal((await logout).error.status, 503); assert.ok((await login).data);
    assert.equal(state.requests.filter(row => row.path.endsWith('/auth/logout')).length, 1);
});

test('a queued lock timeout dispatches nothing and does not poison later sign-in', async () => {
    installLocks(); const blocker = await holdFirstLogin();
    const original = globalThis.setTimeout;
    globalThis.setTimeout = (callback, ms, ...args) => original(callback, ms === 20000 ? 5 : ms, ...args);
    let result;
    try { result = await api.login(B.email, 'synthetic-password'); }
    finally { globalThis.setTimeout = original; }
    assert.equal(result.error.code, 'SESSION_COORDINATION_TIMEOUT');
    assert.equal(result.error.sessionRequestNotSent, true); assert.equal(state.requests.length, 1);
    await blocker.release();
    state.route = () => response(signed(B, 'access-B'));
    assert.ok((await api.login(B.email, 'synthetic-password')).data);
    assert.equal(state.requests.length, 2);
});

test('lock provider rejection does not fall back to an unordered credential request', async () => {
    Object.defineProperty(dom.window.navigator, 'locks', { configurable: true, value: {
        request() { throw new DOMException('Lock access unavailable', 'SecurityError'); },
    } });
    const result = await api.login(B.email, 'synthetic-password');
    assert.equal(result.error.code, 'SESSION_COORDINATION_UNAVAILABLE'); assert.equal(state.requests.length, 0);
    installLocks(); state.route = () => response(signed(B, 'access-B'));
    assert.ok((await api.login(B.email, 'synthetic-password')).data);
});

test('an aborted transport releases the local lock and remains an unknown outcome without an automatic retry', async () => {
    installLocks(); const gate = deferred();
    state.route = request => request.path.endsWith('/auth/logout') ? gate.promise : response(signed(B, 'access-B'));
    const transport = globalThis.fetch; const timer = globalThis.setTimeout; let aborted = false;
    globalThis.fetch = (url, options) => {
        const result = transport(url, options);
        if (!String(url).endsWith('/auth/logout')) return result;
        return new Promise((resolve, reject) => {
            options.signal.addEventListener('abort', () => { aborted = true; reject(new DOMException('Aborted', 'AbortError')); }, { once: true });
            result.then(resolve, reject);
        });
    };
    globalThis.setTimeout = (callback, ms, ...args) => timer(callback, ms === 20000 ? 5 : ms, ...args);
    let result;
    try { result = await api.logout(); }
    finally { globalThis.fetch = transport; globalThis.setTimeout = timer; gate.resolve(response({})); }
    assert.equal(aborted, true); assert.equal(result.error.code, 'REQUEST_TIMEOUT');
    assert.equal(result.error.sessionRequestNotSent, undefined, 'a dispatched request can have an unknown outcome');
    assert.ok((await api.login(B.email, 'synthetic-password')).data);
    assert.equal(state.requests.filter(row => row.path.endsWith('/auth/logout')).length, 1);
    // This only proves local cleanup, not whether a server committed or a real
    // browser could apply delayed headers after an aborted transport.
});

test('without Web Locks no unordered credential request is dispatched', async () => {
    delete dom.window.navigator.locks;
    state.route = () => response(signed(B, 'access-B'));
    const result = await api.login(B.email, 'synthetic-password');
    assert.equal(result.error.code, 'SESSION_COORDINATION_UNAVAILABLE');
    assert.equal(result.error.sessionRequestNotSent, true); assert.equal(state.requests.length, 0);
});

test('canonical identity verification can refresh after login without reacquiring a held login lock', async () => {
    installLocks(); api.setToken(null); localStorage.setItem('talky.admin.logout.pending', 'unconfirmed');
    state.route = request => {
        if (request.path.endsWith('/auth/login')) return response(signed(B, 'initial-B'));
        if (request.path.endsWith('/auth/refresh')) return response(signed(B, 'renewed-B'));
        if (request.path.endsWith('/auth/me')) return request.authorization === 'Bearer renewed-B' ? response(B) : response({}, 401);
        return response({}, 500);
    };
    await draw(); await signInB(); await settle();
    assert.equal(principal(), B.id); assert.equal(api.getToken(), 'renewed-B');
    assert.equal(state.requests.filter(row => row.path.endsWith('/auth/login')).length, 1);
    assert.equal(state.requests.filter(row => row.path.endsWith('/auth/refresh')).length, 1);
});

test('logout queued behind renewal keeps its reviewed credentials and shows explicit recovery if that bearer has expired', async () => {
    installLocks(); const refreshGate = deferred(); api.setToken('expiring-A');
    state.route = request => {
        if (request.path.endsWith('/auth/me')) return response(A);
        if (request.path.endsWith('/auth/refresh')) return refreshGate.promise;
        if (request.path.endsWith('/auth/logout')) return request.authorization ? response({ detail: 'Expired bearer' }, 401) : response({});
        return response({ detail: 'Expired bearer' }, 401);
    };
    await draw(); const backgroundRead = api.getDashboardStats(); await settle();
    await click('Sign out'); assert.equal(principal(), 'signed out');
    assert.equal(state.requests.filter(row => row.path.endsWith('/auth/logout')).length, 0);
    await act(async () => { refreshGate.resolve(response(signed(A, 'fresh-A'))); await backgroundRead; }); await settle();
    const first = state.requests.filter(row => row.path.endsWith('/auth/logout'));
    assert.equal(first.length, 1); assert.equal(first[0].authorization, 'Bearer expiring-A');
    assert.equal(first[0].headers['X-Talky-Expected-User'], A.id);
    assert.equal(principal(), 'signed out'); assert.equal(localStorage.getItem('talky.admin.logout.pending'), 'unconfirmed');
    assert.match(find('[role="alert"]').textContent, /Server sign-out could not be confirmed/);
    await click('Retry browser sign-out');
    const all = state.requests.filter(row => row.path.endsWith('/auth/logout'));
    assert.equal(all.length, 2); assert.equal(all[1].authorization, undefined);
    assert.equal(localStorage.getItem('talky.admin.logout.pending'), null); assert.equal(principal(), 'signed out');
});

let auth;
function Screen() {
    auth = C.useAuth();
    return el('div', null, el('p', { 'data-principal': true }, auth.user?.id ?? 'signed out'),
        auth.isAuthenticated ? el(C.Header) : el(C.LoginPage));
}
const draw = async () => {
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(Screen)))); await settle();
};
const principal = () => find('[data-principal]').textContent;
const signInB = async () => {
    await setInput(find('#email'), B.email); await setInput(find('#password'), 'synthetic-password'); await click('Sign In');
};

test('a second tab login waits for an older successful logout response before receiving new cookies', async () => {
    const lockNames = installLocks(); const oldLogout = deferred(); let cookieOwner = A.id;
    api.setToken('access-A');
    const firstTab = new api.constructor('http://localhost:8000/api/v1');
    firstTab.setToken('access-A', false); firstTab.expectIdentity({ userId: A.id, tenantId: null });
    state.route = async request => {
        if (request.path.endsWith('/auth/logout')) { await oldLogout.promise; cookieOwner = null; return response({ detail: 'Logged out' }); }
        if (request.path.endsWith('/auth/login')) { cookieOwner = B.id; return response(signed(B, 'access-B')); }
        if (request.path.endsWith('/auth/me')) return response(request.authorization === 'Bearer access-B' ? B : A);
        return response({ detail: 'Unexpected request' }, 500);
    };
    await draw(); assert.equal(principal(), A.id);
    const logout = firstTab.logout(); firstTab.invalidateIdentity(false); await settle();
    // The real other-tab marker retires this tab, but has no shared pending ref.
    await act(async () => {
        localStorage.removeItem('admin_token'); localStorage.setItem('talky.admin.logout.pending', 'unconfirmed');
        window.dispatchEvent(new dom.window.StorageEvent('storage', { key: 'talky.admin.identity.v1',
            newValue: JSON.stringify({ kind: 'logout', nonce: 'held-logout' }) }));
    });
    assert.equal(principal(), 'signed out');
    await signInB();
    try {
        assert.equal(state.requests.filter(request => request.path.endsWith('/auth/login')).length, 0,
            'new login must not reach transport while old successful logout headers are undelivered');
    } finally { await act(async () => { oldLogout.resolve(); await logout; }); await settle(); }
    assert.equal(principal(), B.id); assert.equal(cookieOwner, B.id);
    assert.equal(state.requests.filter(request => request.path.endsWith('/auth/login')).length, 1);
    assert.equal(new Set(lockNames).size, 1);
});
