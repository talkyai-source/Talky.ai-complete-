// Actual clients, synthetic exclusive scheduler and fetch; no real cookie jar.
import assert from 'node:assert/strict';
import { beforeEach, afterEach, test } from 'node:test';
import { build } from 'esbuild';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { api, dom, state, response, deferred, settle, installSessionLocks } from './bh-harness.mjs';

const customerRoot = fileURLToPath(new URL('../../../Talk-Leee/', import.meta.url));
const bundle = await build({ entryPoints: [customerRoot + 'src/lib/http-client.ts'], bundle: true,
    write: false, platform: 'node', format: 'cjs', packages: 'external',
    alias: { '@': customerRoot + 'src' } });
const loaded = { exports: {} };
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), loaded, loaded.exports);
const customer = loaded.exports;
beforeEach(() => {
    const transport = globalThis.fetch;
    globalThis.fetch = (url, init) => transport(new URL(url, dom.window.location.origin), init);
});
afterEach(() => { customer.setTokenProvider(null); customer.__resetRefreshStateForTests(); });
const base = dom.window.location.origin + '/api/v1';
const adminClient = (url = '/api/v1') => { const c = new api.constructor(url); c.setToken(null, false); return c; };
const customerClient = () => customer.createHttpClient({ baseUrl: base, getToken: () => null });

for (const first of ['Admin', 'customer']) {
    test(`${first} sign-out orders the other actual client's sign-in`, async () => {
        const names = installSessionLocks(); const gate = deferred(); const a = adminClient(); const c = customerClient();
        state.route = row => row.path.endsWith('/auth/logout') ? gate.promise : response({ access_token: 'synthetic' });
        const logout = first === 'Admin' ? a.logout() : c.request({ path: '/auth/logout', method: 'POST' });
        await settle();
        const login = first === 'Admin' ? c.request({ path: '/auth/login', method: 'POST', body: {} }) : a.login('test@example.invalid', 'synthetic');
        let beforeRelease;
        try { await settle(); beforeRelease = state.requests.filter(r => r.path.endsWith('/auth/login')).length; }
        finally { gate.resolve(response({})); await Promise.all([logout, login]); }
        assert.equal(state.requests.filter(r => r.path.endsWith('/auth/logout')).length, 1);
        assert.equal(state.requests.filter(r => r.path.endsWith('/auth/login')).length, 1);
        assert.equal(beforeRelease, 0, 'new cookie writer must wait for old sign-out headers');
        assert.equal(new Set(names).size, 1, 'relative and absolute API URLs share a key');
    });
}

test('customer cookie writer refuses unavailable browser coordination', async () => {
    delete dom.window.navigator.locks; state.route = () => response({});
    await assert.rejects(customerClient().request({ path: '/auth/login', method: 'POST' }),
        e => e.code === 'SESSION_COORDINATION_UNAVAILABLE' && e.sessionRequestNotSent === true);
    assert.equal(state.requests.length, 0);
});

for (const path of ['/auth/login', '/auth/mfa/verify', '/auth/logout', '/auth/logout-all',
    '/auth/refresh', '/auth/passkeys/login/complete', '/auth/signup/complete']) {
    for (const unavailable of ['missing', 'throws']) {
        test(`${path} never dispatches with ${unavailable} browser locks`, async () => {
            if (unavailable === 'missing') delete dom.window.navigator.locks;
            else Object.defineProperty(dom.window.navigator, 'locks', { configurable: true, get() { throw new Error('Restricted storage'); } });
            state.route = () => response({});
            await assert.rejects(customerClient().request({ path, method: 'POST' }),
                e => e.code === 'SESSION_COORDINATION_UNAVAILABLE' && e.sessionRequestNotSent === true);
            assert.equal(state.requests.length, 0);
        });
    }
}

for (const retire of ['identity', 'operation', 'signal']) {
    test(`queued customer credential request checks ${retire} ownership before dispatch`, async () => {
        installSessionLocks(); const gate = deferred();
        state.route = row => row.path.endsWith('/auth/logout') ? gate.promise : response({});
        const first = adminClient().logout(); await settle();
        let owned = true; const controller = new AbortController();
        const next = customerClient().request({ path: '/auth/passkeys/login/complete', method: 'POST',
            body: {}, ownsOperation: () => owned, signal: controller.signal });
        const rejected = assert.rejects(next, e => ['identity_changed', 'operation_cancelled', 'SESSION_REQUEST_CANCELLED'].includes(e.code));
        if (retire === 'identity') customer.invalidateRequestIdentity();
        if (retire === 'operation') owned = false;
        if (retire === 'signal') controller.abort();
        gate.resolve(response({})); await Promise.all([first, rejected]);
        assert.equal(state.requests.length, 1);
    });
}

test('queued acquisition deadline sends nothing and later explicit retry works', async () => {
    installSessionLocks(); const gate = deferred();
    state.route = row => row.path.endsWith('/auth/logout') ? gate.promise : response({});
    const first = adminClient().logout(); await settle();
    const timer = globalThis.setTimeout;
    globalThis.setTimeout = (fn, ms, ...args) => timer(fn, ms === 20000 ? 5 : ms, ...args);
    try {
        await assert.rejects(customerClient().request({ path: '/auth/login', method: 'POST' }),
            e => e.code === 'SESSION_COORDINATION_TIMEOUT' && e.sessionRequestNotSent === true);
    } finally { globalThis.setTimeout = timer; gate.resolve(response({})); await first; }
    assert.equal(state.requests.length, 1);
    await customerClient().request({ path: '/auth/login', method: 'POST' });
    assert.equal(state.requests.length, 2);
});

test('admitted customer failure cannot be marked unsent by the HTTP response', async () => {
    installSessionLocks(); state.route = () => response({ error: { code: 'SESSION_COORDINATION_UNAVAILABLE',
        message: 'Synthetic remote refusal', sessionRequestNotSent: true } }, 503);
    await assert.rejects(customerClient().request({ path: '/auth/login', method: 'POST' }),
        e => e.status === 503 && e.sessionRequestNotSent === undefined);
    assert.equal(state.requests.length, 1);
});

for (const raw of [false, true]) {
    test(`${raw ? 'raw' : 'JSON'} protected read renews without nested session locks`, async () => {
        const names = installSessionLocks(); let token = 'old';
        customer.setRequestIdentity({ userId: 'user-A', tenantId: 'tenant-A' });
        state.route = row => row.path.endsWith('/auth/refresh')
            ? Response.json({ access_token: 'new', user_id: 'user-A', tenant_id: 'tenant-A' })
            : row.authorization === 'Bearer new' ? Response.json({ ok: true }) : response({}, 401);
        const c = customer.createHttpClient({ baseUrl: base, getToken: () => token, setToken: t => { token = t; } });
        const value = await (raw ? c.requestRaw({ path: '/protected' }).then(r => r.json()) : c.request({ path: '/protected' }));
        assert.deepEqual(value, { ok: true }); assert.equal(token, 'new');
        assert.equal(state.requests.length, 3); assert.equal(names.length, 1);
    });
}

test('automatic renewal fails closed when browser coordination is unavailable', async () => {
    delete dom.window.navigator.locks; state.route = () => response({}, 401);
    await assert.rejects(customerClient().request({ path: '/auth/me', suppressAuthRedirect: true }),
        e => e.code === 'SESSION_COORDINATION_UNAVAILABLE' && e.sessionRequestNotSent === true);
    assert.equal(state.requests.length, 1); assert.equal(state.requests[0].method, 'GET');
});

test('canonical lock key preserves API origin/path distinctions and default port equivalence', async () => {
    const names = installSessionLocks(); state.route = () => response({});
    for (const url of ['/api/v1', base, base + '/', 'https://admin.example.invalid:443/api/v1///']) {
        await adminClient(url).login('test@example.invalid', 'synthetic');
        await customer.createHttpClient({ baseUrl: new URL(url, dom.window.location.origin).href,
            getToken: () => null }).request({ path: '/auth/login', method: 'POST' });
    }
    assert.equal(new Set(names).size, 1);
    for (const url of ['https://other.example.invalid/api/v1', 'https://admin.example.invalid/api/v2']) {
        await adminClient(url).login('test@example.invalid', 'synthetic');
        await customer.createHttpClient({ baseUrl: url, getToken: () => null }).request({ path: '/auth/login', method: 'POST' });
    }
    assert.equal(new Set(names).size, 3);
});

test('customer transport deadline starts after queue admission', async () => {
    installSessionLocks(); const gate = deferred();
    state.route = row => row.path.endsWith('/auth/logout') ? gate.promise : response({});
    const transport = globalThis.fetch;
    globalThis.fetch = (url, init) => { init.signal?.throwIfAborted(); return transport(url, init); };
    const first = adminClient().logout(); await settle();
    const next = customerClient().request({ path: '/auth/login', method: 'POST', timeoutMs: 5 });
    await settle(25); assert.equal(state.requests.length, 1);
    gate.resolve(response({})); await Promise.all([first, next]);
    assert.equal(state.requests.length, 2);
});

test('queued automatic renewal is not sent after identity changes', async () => {
    installSessionLocks(); const gate = deferred();
    state.route = row => row.path.endsWith('/auth/logout') ? gate.promise : response({}, 401);
    const first = adminClient().logout(); await settle();
    const next = customerClient().request({ path: '/auth/me', suppressAuthRedirect: true });
    const rejected = assert.rejects(next, e => e.code === 'identity_changed');
    await settle(); customer.invalidateRequestIdentity();
    gate.resolve(response({})); await Promise.all([first, rejected]);
    assert.equal(state.requests.filter(row => row.path.endsWith('/auth/refresh')).length, 0);
});

test('admitted customer timeout releases the lock without an unsent claim or automatic replay', async () => {
    installSessionLocks(); let attempts = 0;
    globalThis.fetch = (_url, init) => {
        attempts++;
        return new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true }));
    };
    await assert.rejects(customerClient().request({ path: '/auth/logout', method: 'POST', timeoutMs: 5 }),
        e => e.code === 'timeout' && e.sessionRequestNotSent === undefined);
    assert.equal(attempts, 1);
    globalThis.fetch = async () => Response.json({});
    await customerClient().request({ path: '/auth/login', method: 'POST' });
});
