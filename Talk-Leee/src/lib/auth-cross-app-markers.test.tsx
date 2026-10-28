import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { useLayoutEffect } from 'react';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import { AuthProvider, useAuth } from './auth-context';
import { clearFreshLoginGrace, setSessionExpiredHandler } from './http-client';
import { notificationsStore } from './notifications';

const OWN = 'talklee.auth.identity.v1', FOREIGN = 'talky.admin.identity.v1';
const A = { id: 'marker-A', email: 'a@example.invalid', name: 'A', role: 'tenant_admin', tenant_id: 'tenant-A', minutes_remaining: 1, minutes_state: 'known' };
const B = { ...A, id: 'marker-B', email: 'b@example.invalid', tenant_id: 'tenant-B' };
const originalFetch = globalThis.fetch;
const originalFallback = process.env.NEXT_PUBLIC_BEARER_FALLBACK;
const originalChannel = Object.getOwnPropertyDescriptor(window, 'BroadcastChannel');
const originalStorage = Object.getOwnPropertyDescriptor(window, 'localStorage')!;
let auth: ReturnType<typeof useAuth>;
let reads: Array<Headers>, route: () => Response | Promise<Response>;
let channels: Set<Channel>, published: unknown[];
class Channel {
    onmessage: ((event: { data: unknown }) => void) | null = null;
    constructor(public name: string) { channels.add(this); }
    postMessage(value: unknown) {
        published.push(value);
        for (const other of channels) if (other !== this && other.name === this.name) queueMicrotask(() => {
            if (channels.has(other)) other.onmessage?.({ data: value });
        });
    }
    close() { channels.delete(this); }
}
function Probe() { const value = useAuth(); useLayoutEffect(() => { auth = value; }); return <p>{value.user?.id ?? 'anonymous'}</p>; }
function marker(kind = 'changed', nonce = crypto.randomUUID()) { return { kind, nonce }; }
function storage(key: string, value: unknown) { window.dispatchEvent(new window.StorageEvent('storage', { key, newValue: JSON.stringify(value) })); }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(yes => { resolve = yes; }); return { promise, resolve }; }
async function boot() {
    window.localStorage.setItem('talklee.auth.token', 'old-customer-bearer');
    route = () => Response.json(A); render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => assert.equal(auth.user?.id, A.id)); reads = [];
}
beforeEach(() => {
    process.env.NEXT_PUBLIC_BEARER_FALLBACK = 'true'; reads = []; channels = new Set(); published = [];
    Object.defineProperty(window, 'BroadcastChannel', { configurable: true, value: Channel });
    globalThis.fetch = (async (url, options) => {
        assert.equal(new URL(String(url)).pathname, '/api/v1/auth/me', 'only canonical identity reads are permitted');
        reads.push(new Headers(options?.headers)); return route();
    }) as typeof fetch;
});
afterEach(() => {
    cleanup(); notificationsStore.setIdentity(null); globalThis.fetch = originalFetch;
    Object.defineProperty(window, 'localStorage', originalStorage);
    if (originalChannel) Object.defineProperty(window, 'BroadcastChannel', originalChannel); else Reflect.deleteProperty(window, 'BroadcastChannel');
    window.localStorage.clear(); clearFreshLoginGrace();
    if (originalFallback === undefined) delete process.env.NEXT_PUBLIC_BEARER_FALLBACK; else process.env.NEXT_PUBLIC_BEARER_FALLBACK = originalFallback;
});

test('foreign storage change clears persisted customer bearer before canonical cookie verification', async () => {
    await boot(); route = () => Response.json(B);
    await act(async () => storage(FOREIGN, marker()));
    await waitFor(() => assert.equal(auth.user?.id, B.id));
    assert.equal(reads.length, 1); assert.equal(reads[0].get('authorization'), null);
    assert.equal(reads[0].get('X-Talky-Expected-User'), null);
    assert.equal(auth.accessToken, null); assert.equal(window.localStorage.getItem('talklee.auth.token'), null);
    assert.equal(published.length, 0, 'no foreign hint relay');
});

test('foreign channel-only change clears memory with storage blocked', async () => {
    await boot(); route = () => Response.json(B);
    Object.defineProperty(window, 'localStorage', { configurable: true, get() { throw new Error('Synthetic storage blocked'); } });
    const external = new Channel(FOREIGN);
    await act(async () => external.postMessage(marker()));
    await waitFor(() => assert.equal(auth.user?.id, B.id)); external.close();
    assert.equal(reads.length, 1); assert.equal(reads[0].get('authorization'), null); assert.equal(auth.accessToken, null);
    assert.equal(published.length, 1);
});

for (const order of ['marker-first', 'token-first']) test(`own marker keeps new sibling bearer (${order})`, async () => {
    await boot(); route = () => Response.json(B); window.localStorage.setItem('talklee.auth.token', 'new-customer-bearer');
    if (order === 'token-first') await act(async () => window.dispatchEvent(new window.StorageEvent('storage', { key: 'talklee.auth.token', newValue: 'new-customer-bearer' })));
    await act(async () => storage(OWN, marker())); await waitFor(() => assert.equal(auth.user?.id, B.id));
    assert.equal(auth.accessToken, 'new-customer-bearer'); assert.equal(reads[0].get('authorization'), 'Bearer new-customer-bearer');
    assert.ok(reads.every(headers => headers.get('authorization') === 'Bearer new-customer-bearer'));
    const count = reads.length;
    await act(async () => window.dispatchEvent(new window.StorageEvent('storage', { key: 'talklee.auth.token', newValue: 'new-customer-bearer' })));
    assert.equal(reads.length, count);
});

test('foreign storage/channel duplicate and matching removal cannot cancel new canonical identity', async () => {
    await boot(); const read = deferred<Response>(); route = () => read.promise;
    const value = marker(), external = new Channel(FOREIGN);
    await act(async () => { storage(FOREIGN, value); external.postMessage(value); });
    assert.equal(auth.user, null); assert.equal(reads.length, 1);
    await act(async () => window.dispatchEvent(new window.StorageEvent('storage', { key: 'talklee.auth.token', newValue: null })));
    assert.equal(reads.length, 1);
    await act(async () => read.resolve(Response.json(B))); await waitFor(() => assert.equal(auth.user?.id, B.id)); external.close();
    assert.equal(reads[0].get('authorization'), null);
});

test('foreign logout removes own cached bearer and fences old delayed profile', async () => {
    await boot(); const read = deferred<Response>(); route = () => read.promise;
    let pending!: Promise<void>; await act(async () => { pending = auth.refreshUser(); });
    await act(async () => storage(FOREIGN, marker('logout')));
    assert.equal(auth.user, null); assert.equal(auth.accessToken, null); assert.equal(window.localStorage.getItem('talklee.auth.token'), null);
    await act(async () => { read.resolve(Response.json(A)); await pending; });
    assert.equal(auth.user, null);
});

test('foreign identity outage keeps prior views masked and old bearer retired', async () => {
    await boot(); route = () => Response.json({ detail: 'Synthetic identity unavailable' }, { status: 503 });
    await act(async () => storage(FOREIGN, marker()));
    await waitFor(() => assert.equal(auth.loading, false));
    assert.equal(auth.user, null); assert.equal(auth.accessToken, null); assert.equal(reads.length, 1);
});

for (const transport of ['storage', 'channel']) test(`foreign ${transport} change preserves an unconfirmed local logout fence`, async () => {
    await boot();
    const readFetch = globalThis.fetch, originalWarn = console.warn;
    let logouts = 0;
    globalThis.fetch = (async (url, options) => {
        if (new URL(String(url)).pathname !== '/api/v1/auth/logout') return readFetch(url, options);
        assert.equal(options?.method, 'POST'); logouts++;
        return Response.json({ detail: 'Synthetic logout unavailable' }, { status: 503 });
    }) as typeof fetch;
    console.warn = () => {}; // Expected synthetic failure; avoid logging auth objects.
    try {
        let result: Awaited<ReturnType<typeof auth.logout>> | undefined;
        await act(async () => { result = await auth.logout(); });
        assert.equal(logouts, 1); assert.equal(result?.serverConfirmed, false);
        assert.equal(auth.user, null); assert.ok(window.localStorage.getItem('talky.logout.pending'));
        reads = []; route = () => Response.json(B);
        const external = new Channel(FOREIGN);
        try { await act(async () => transport === 'storage' ? storage(FOREIGN, marker()) : external.postMessage(marker())); }
        finally { external.close(); }
        assert.equal(reads.length, 0, 'an identity hint cannot resolve an unknown logout');
        assert.equal(auth.user, null); assert.equal(auth.accessToken, null);
        assert.ok(window.localStorage.getItem('talky.logout.pending'));
    } finally { console.warn = originalWarn; }
});

function blockStorage(mode: 'getter' | 'methods') {
    const targets = [window, globalThis];
    const descriptors = targets.map(target => Object.getOwnPropertyDescriptor(target, 'localStorage'));
    const unavailable = () => { throw new Error('Synthetic storage unavailable'); };
    for (const target of targets) Object.defineProperty(target, 'localStorage', mode === 'getter'
        ? { configurable: true, get: unavailable }
        : { configurable: true, value: { getItem: unavailable, setItem: unavailable, removeItem: unavailable } });
    return () => targets.forEach((target, index) => {
        if (descriptors[index]) Object.defineProperty(target, 'localStorage', descriptors[index]!);
        else Reflect.deleteProperty(target, 'localStorage');
    });
}

test('volatile logout intent blocks a foreign hint while logout is in flight and fences a late profile', async () => {
    await boot(); const profile = deferred<Response>(), ending = deferred<Response>();
    route = () => profile.promise;
    let refreshing!: Promise<void>, logout!: ReturnType<typeof auth.logout>;
    await act(async () => { refreshing = auth.refreshUser(); });
    const readFetch = globalThis.fetch, originalWarn = console.warn;
    let logouts = 0;
    globalThis.fetch = (async (url, options) => {
        if (new URL(String(url)).pathname !== '/api/v1/auth/logout') return readFetch(url, options);
        logouts++; return ending.promise;
    }) as typeof fetch;
    console.warn = () => {};
    const external = new Channel(FOREIGN);
    try {
        await act(async () => { logout = auth.logout(); });
        await waitFor(() => assert.equal(logouts, 1));
        assert.ok(window.localStorage.getItem('talky.logout.pending'), 'intent is durable before the response settles');
        reads = []; route = () => Response.json(B);
        await act(async () => external.postMessage(marker()));
        assert.equal(reads.length, 0, 'pending local logout must close marker read admission');
        await act(async () => { profile.resolve(Response.json(A)); await refreshing; });
        assert.equal(auth.user, null); assert.equal(auth.loading, false);
        await act(async () => { ending.resolve(Response.json({})); await logout; });
        await act(async () => external.postMessage(marker()));
        assert.equal(reads.length, 0, 'late completion cannot reopen the local logout intent');
        assert.equal(auth.user, null);
    } finally {
        await act(async () => { profile.resolve(Response.json(A)); ending.resolve(Response.json({})); await Promise.allSettled([refreshing, logout]); });
        console.warn = originalWarn; external.close();
    }
});

for (const mode of ['getter', 'methods'] as const) test(`volatile logout intent survives storage ${mode} failure until explicit successful login`, async () => {
    await boot(); const restoreStorage = blockStorage(mode);
    const readFetch = globalThis.fetch, originalWarn = console.warn;
    let loginSucceeds = false, logouts = 0, logins = 0;
    globalThis.fetch = (async (url, options) => {
        const path = new URL(String(url)).pathname;
        if (path === '/api/v1/auth/logout') { logouts++; return Response.json({ detail: 'Synthetic outage' }, { status: 503 }); }
        if (path === '/api/v1/auth/login') {
            logins++;
            return loginSucceeds ? Response.json({ ...B, user_id: B.id, access_token: 'new-owned-token' })
                : Response.json({ detail: 'Synthetic invalid credentials' }, { status: 401 });
        }
        return readFetch(url, options);
    }) as typeof fetch;
    console.warn = () => {}; const external = new Channel(FOREIGN);
    try {
        await act(async () => { assert.equal((await auth.logout()).serverConfirmed, false); });
        assert.equal(logouts, 1); reads = []; route = () => Response.json(B);
        await act(async () => external.postMessage(marker()));
        assert.equal(reads.length, 0, 'failed persistence must not erase local logout intent');
        assert.equal(auth.user, null);
        await act(async () => { await assert.rejects(auth.login(B.email, 'synthetic-password')); });
        await act(async () => external.postMessage(marker()));
        assert.equal(reads.length, 0, 'failed explicit sign-in must retain the fence');
        loginSucceeds = true;
        await act(async () => { await auth.login(B.email, 'synthetic-password'); });
        await waitFor(() => assert.equal(auth.user?.id, B.id)); assert.equal(logins, 2);
        await act(async () => external.postMessage(marker()));
        await waitFor(() => assert.equal(auth.user?.id, B.id));
        assert.equal(reads.length, 1, 'successful own sign-in reopens canonical verification');
    } finally { restoreStorage(); console.warn = originalWarn; external.close(); }
});

test('volatile fence seeded from durable pending survives later unavailable storage', async () => {
    window.localStorage.setItem('talky.logout.pending', 'synthetic-prior-outcome');
    route = () => Response.json(B); render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => assert.equal(auth.loading, false)); assert.equal(reads.length, 0);
    const restoreStorage = blockStorage('getter'), external = new Channel(FOREIGN);
    try {
        await act(async () => external.postMessage(marker()));
        assert.equal(reads.length, 0); assert.equal(auth.user, null);
    } finally { restoreStorage(); external.close(); }
});

for (const status of [200, 503]) test(`late logout ${status} body cannot clear a newer explicit own login or rearm its fence`, async () => {
    await boot(); const readFetch = globalThis.fetch, originalWarn = console.warn;
    let finishBody!: () => void, logouts = 0;
    const body = new ReadableStream({ start(controller) { finishBody = () => { controller.enqueue(new TextEncoder().encode('{}')); controller.close(); }; } });
    const ending = new Response(body, { status, headers: { 'content-type': 'application/json' } });
    globalThis.fetch = (async (url, options) => {
        const path = new URL(String(url)).pathname;
        if (path === '/api/v1/auth/logout') { logouts++; return ending; }
        if (path === '/api/v1/auth/login') return Response.json({ ...B, user_id: B.id, access_token: 'new-owned-token' });
        return readFetch(url, options);
    }) as typeof fetch;
    console.warn = () => {}; let logout!: ReturnType<typeof auth.logout>, finished = false;
    const external = new Channel(FOREIGN);
    try {
        await act(async () => { logout = auth.logout(); });
        await waitFor(() => assert.equal(logouts, 1));
        await act(async () => { await auth.login(B.email, 'synthetic-password'); });
        assert.equal(auth.user?.id, B.id);
        await act(async () => { finishBody(); finished = true; assert.equal((await logout).identityCurrent, false); });
        assert.equal(auth.user?.id, B.id); assert.equal(auth.accessToken, 'new-owned-token');
        assert.equal(window.localStorage.getItem('talky.logout.pending'), null);
        reads = []; route = () => Response.json(B);
        await act(async () => external.postMessage(marker()));
        await waitFor(() => assert.equal(auth.user?.id, B.id)); assert.equal(reads.length, 1);
    } finally {
        if (!finished) finishBody();
        await act(async () => { await logout; });
        console.warn = originalWarn; external.close();
    }
});

test('volatile logout intent remains durable across reload after an in-flight hint and failed response', async () => {
    await boot(); const ending = deferred<Response>();
    const readFetch = globalThis.fetch, originalWarn = console.warn;
    let logouts = 0, logout!: ReturnType<typeof auth.logout>;
    globalThis.fetch = (async (url, options) => {
        if (new URL(String(url)).pathname !== '/api/v1/auth/logout') return readFetch(url, options);
        logouts++; return ending.promise;
    }) as typeof fetch;
    console.warn = () => {}; const external = new Channel(FOREIGN);
    try {
        await act(async () => { logout = auth.logout(); });
        await waitFor(() => assert.equal(logouts, 1));
        await act(async () => external.postMessage(marker()));
        await act(async () => { ending.resolve(Response.json({ detail: 'Synthetic outage' }, { status: 503 })); await logout; });
        assert.ok(window.localStorage.getItem('talky.logout.pending'), 'the ignored hint must not lose durable logout intent');
        cleanup(); reads = []; route = () => Response.json(B);
        process.env.NEXT_PUBLIC_BEARER_FALLBACK = 'false';
        render(<AuthProvider><Probe /></AuthProvider>);
        await waitFor(() => assert.equal(auth.loading, false));
        assert.equal(reads.length, 0, 'cookie-only remount must preserve durable intent'); assert.equal(auth.user, null);
    } finally {
        await act(async () => { ending.resolve(Response.json({})); await logout; });
        console.warn = originalWarn; external.close();
    }
});

for (const fallback of ['default', 'true']) test(`cookie bootstrap verifies a session without a stored bearer (${fallback} fallback)`, async () => {
    if (fallback === 'default') delete process.env.NEXT_PUBLIC_BEARER_FALLBACK;
    else process.env.NEXT_PUBLIC_BEARER_FALLBACK = fallback;
    assert.equal(window.localStorage.getItem('talklee.auth.token'), null);
    const readFetch = globalThis.fetch;
    globalThis.fetch = ((url, options) => {
        assert.equal(options?.credentials, 'include', 'the canonical read must carry existing cookies');
        return readFetch(url, options);
    }) as typeof fetch;
    route = () => Response.json(A); render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => assert.equal(auth.user?.id, A.id));
    assert.equal(auth.status, 'authenticated'); assert.equal(auth.accessToken, null);
    assert.equal(reads.length, 1); assert.equal(reads[0].get('authorization'), null);
    assert.equal(window.localStorage.getItem('talklee.auth.token'), null);
});

test('cookie bootstrap cold 401 settles anonymous without a session-expired redirect loop', async () => {
    delete process.env.NEXT_PUBLIC_BEARER_FALLBACK;
    const paths: string[] = []; let expired = 0;
    setSessionExpiredHandler(() => { expired++; });
    globalThis.fetch = (async (url, options) => {
        const path = new URL(String(url)).pathname;
        assert.ok(['/api/v1/auth/me', '/api/v1/auth/refresh'].includes(path));
        assert.equal(options?.credentials, 'include');
        assert.equal(new Headers(options?.headers).get('authorization'), null);
        paths.push(path); return Response.json({ detail: 'Synthetic anonymous session' }, { status: 401 });
    }) as typeof fetch;
    try {
        render(<AuthProvider><Probe /></AuthProvider>);
        await waitFor(() => assert.equal(auth.loading, false));
        assert.equal(auth.user, null); assert.equal(auth.status, 'anonymous');
        assert.deepEqual(paths, ['/api/v1/auth/me', '/api/v1/auth/refresh']);
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
        assert.equal(paths.length, 2); assert.equal(expired, 0);
    } finally { setSessionExpiredHandler(null); }
});

test('cookie bootstrap retains the explicit logout fence without probing a valid cookie', async () => {
    delete process.env.NEXT_PUBLIC_BEARER_FALLBACK;
    window.localStorage.setItem('talky.logout.pending', 'synthetic-unconfirmed-logout');
    route = () => Response.json(B); render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => assert.equal(auth.loading, false));
    await act(async () => { storage(FOREIGN, marker()); await auth.refreshUser(); });
    assert.equal(auth.user, null); assert.equal(auth.status, 'anonymous'); assert.equal(reads.length, 0);
    assert.equal(window.localStorage.getItem('talky.logout.pending'), 'synthetic-unconfirmed-logout');
});
