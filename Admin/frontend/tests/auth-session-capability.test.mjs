import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, dom, state, render, response, find, text, click, setInput, settle, deferred, installSessionLocks } from './bh-harness.mjs';

const ME = { id: 'capability-admin', email: 'capability@example.invalid', name: 'Capability admin', role: 'platform_admin', tenant_id: null };
const signed = { access_token: 'capability-access', user_id: ME.id, tenant_id: null, role: ME.role };
const challenge = { access_token: '', mfa_required: true, mfa_challenge_token: 'capability-one-use-challenge' };
const marker = 'talky.admin.logout.pending';
const noLock = () => { delete dom.window.navigator.locks; };
const setLocks = value => Object.defineProperty(dom.window.navigator, 'locks', { configurable: true, value });
const postRequests = () => state.requests.filter(request => request.method === 'POST');
const principal = () => find('[data-principal]')?.textContent;
function Screen() {
    const auth = C.useAuth();
    return el('div', null, el('p', { 'data-principal': true }, auth.user?.id ?? 'signed out'),
        auth.isAuthenticated ? el(C.Header) : el(C.LoginPage));
}
const draw = async (key = 'initial') => {
    await render(el(C.AuthProvider, { key }, el(C.MemoryRouter, null, el(Screen)))); await settle();
};
const signIn = async () => {
    await setInput(find('#email'), ME.email); await setInput(find('#password'), 'synthetic-password'); await click('Sign In');
};
const setupMfa = async () => {
    api.setToken(null);
    state.route = request => {
        if (request.path.endsWith('/auth/login')) return response(challenge);
        if (request.path.endsWith('/auth/mfa/verify')) return response(signed);
        return request.authorization === 'Bearer capability-access' ? response(ME) : response({ detail: 'Sign in required' }, 401);
    };
    await draw(); await signIn(); assert.ok(find('#factor'));
};

for (const [name, disable] of [
    ['missing capability', noLock],
    ['non-callable request', () => setLocks({ request: true })],
    ['throwing capability getter', () => Object.defineProperty(dom.window.navigator, 'locks', {
        configurable: true, get() { throw new DOMException('Storage denied', 'SecurityError'); },
    })],
    ['synchronous lock rejection', () => setLocks({ request() { throw new DOMException('Storage denied', 'SecurityError'); } })],
    ['asynchronous lock rejection', () => setLocks({ request() { return Promise.reject(new DOMException('Inactive document', 'InvalidStateError')); } })],
]) {
    test(`${name} never dispatches login, MFA, logout or refresh`, async () => {
        disable(); const token = api.getToken();
        state.route = () => response({ detail: 'Expired access' }, 401);
        for (const invoke of [() => api.login(ME.email, 'synthetic-password'),
            () => api.verifyMfaChallenge(challenge.mfa_challenge_token, { recovery_code: 'one-use-code' }),
            () => api.logout(), () => api.getTenants()]) {
            const result = await invoke();
            assert.equal(result.error.code, 'SESSION_COORDINATION_UNAVAILABLE');
            assert.equal(result.error.sessionRequestNotSent, true);
            assert.equal(result.error.status, undefined, 'client capability refusal is not a fabricated HTTP response');
        }
        assert.equal(postRequests().length, 0);
        assert.equal(state.requests.length, 1, 'only the initial protected read reaches transport');
        assert.equal(api.getToken(), token, 'unavailable renewal does not fabricate revoked credentials');
    });
}

test('real sign-in shows missing coordination and deliberately recovers when browser support returns', async () => {
    api.setToken(null); noLock();
    state.route = request => request.path.endsWith('/auth/login') ? response(signed)
        : request.authorization === 'Bearer capability-access' ? response(ME) : response({}, 401);
    await draw(); await signIn();
    assert.equal(principal(), 'signed out'); assert.equal(postRequests().length, 0);
    assert.match(text(), /request was not sent.*coordinate browser sessions/i);
    assert.match(text(), /HTTPS.*supported browser.*site storage/i);
    installSessionLocks(); await signIn(); await settle();
    assert.equal(principal(), ME.id);
    assert.equal(postRequests().length, 1, 'only a deliberate recovered sign-in is dispatched');
});

test('MFA capability refusal preserves the reviewed challenge and recovery code is sent only after deliberate recovery', async () => {
    await setupMfa(); await click('Use a recovery code'); noLock();
    await setInput(find('#factor'), 'synthetic-one-use-code'); await click('Verify');
    assert.ok(find('#factor')); assert.match(text(), /Verify your sign-in/);
    assert.match(text(), /request was not sent/); assert.doesNotMatch(text(), /may already have been used/);
    assert.equal(state.requests.filter(request => request.path.endsWith('/auth/mfa/verify')).length, 0);
    installSessionLocks(); await setInput(find('#factor'), 'synthetic-one-use-code'); await click('Verify'); await settle();
    const sent = state.requests.filter(request => request.path.endsWith('/auth/mfa/verify'));
    assert.equal(sent.length, 1); assert.equal(sent[0].body.challenge_token, challenge.mfa_challenge_token);
    assert.equal(sent[0].body.recovery_code, 'synthetic-one-use-code'); assert.equal(principal(), ME.id);
});

test('an HTTP payload cannot spoof local not-sent evidence for a dispatched MFA operation', async () => {
    await setupMfa();
    state.route = () => response({ error: { code: 'SESSION_COORDINATION_UNAVAILABLE',
        message: 'Pretend request was not sent', sessionRequestNotSent: true } }, 503);
    await setInput(find('#factor'), '123456'); await click('Verify');
    assert.equal(state.requests.filter(request => request.path.endsWith('/auth/mfa/verify')).length, 1);
    assert.equal(find('#factor'), null); assert.match(text(), /may already have been used/);
    assert.equal(principal(), 'signed out');
});

test('missing coordination masks logout immediately and retains current-browser recovery across remount', async () => {
    api.setToken('capability-access'); state.route = request => request.path.endsWith('/auth/logout') ? response({}) : response(ME);
    await draw(); assert.equal(principal(), ME.id); noLock();
    await click('Sign out');
    assert.equal(principal(), 'signed out'); assert.equal(api.getToken(), null);
    assert.equal(localStorage.getItem(marker), 'unconfirmed'); assert.equal(postRequests().length, 0);
    assert.match(text(), /Server sign-out was not sent/); assert.match(text(), /Restore session support in this browser/);
    assert.match(text(), /currently signed in in this browser/);
    await click('Retry browser sign-out'); assert.equal(postRequests().length, 0);
    const reads = state.requests.length; await draw('remounted');
    assert.equal(state.requests.length, reads, 'pending logout never rediscovers a still-live cookie identity');
    assert.equal(localStorage.getItem(marker), 'unconfirmed');
    installSessionLocks(); await click('Retry browser sign-out');
    assert.equal(postRequests().length, 1); assert.equal(postRequests()[0].path.endsWith('/auth/logout'), true);
    assert.equal(postRequests()[0].authorization, undefined, 'deliberate recovery keeps existing current-browser semantics');
    assert.equal(localStorage.getItem(marker), null); assert.equal(principal(), 'signed out');
});

test('a missing-capability renewal masks an unverified view, retains credentials, and recovers without login', async () => {
    api.setToken('expired-access'); noLock();
    state.route = request => request.path.endsWith('/auth/refresh') ? response(signed)
        : request.authorization === 'Bearer capability-access' ? response(ME) : response({}, 401);
    await draw(); assert.equal(principal(), 'signed out'); assert.equal(api.getToken(), 'expired-access');
    assert.equal(postRequests().length, 0); assert.match(text(), /request was not sent/);
    installSessionLocks(); await click('Retry session check'); await settle();
    assert.equal(principal(), ME.id); assert.equal(postRequests().length, 1);
    assert.equal(postRequests()[0].path.endsWith('/auth/refresh'), true);
});

test('a queued logout timeout asks to wait, never to restore an already-working browser capability', async () => {
    api.setToken('capability-access'); const gate = deferred();
    state.route = request => request.path.endsWith('/auth/login') ? gate.promise
        : request.path.endsWith('/auth/logout') ? response({}) : response(ME);
    await draw();
    const other = new api.constructor('http://localhost:8000/api/v1');
    other.setToken(null, false);
    const blocker = other.login('holding@example.invalid', 'synthetic-password'); await settle();
    const timer = globalThis.setTimeout;
    globalThis.setTimeout = (callback, ms, ...args) => timer(callback, ms === 20000 ? 5 : ms, ...args);
    try { await click('Sign out'); await settle(); }
    finally { globalThis.setTimeout = timer; }
    assert.equal(principal(), 'signed out'); assert.equal(localStorage.getItem(marker), 'unconfirmed');
    assert.equal(state.requests.filter(request => request.path.endsWith('/auth/logout')).length, 0);
    assert.match(text(), /Server sign-out was not sent.*another browser session update is still running/);
    assert.match(text(), /Wait for that update to finish/); assert.doesNotMatch(text(), /Restore session support/);
    gate.resolve(response(signed)); await blocker; await settle();
    assert.equal(state.requests.filter(request => request.path.endsWith('/auth/logout')).length, 0, 'queue timeout never replays the abandoned operation');
    await click('Retry browser sign-out');
    assert.equal(state.requests.filter(request => request.path.endsWith('/auth/logout')).length, 1);
    assert.equal(localStorage.getItem(marker), null);
});

test('an unexpected rejection after admission never claims the session request was unsent', async () => {
    setLocks({ async request(_name, _options, callback) {
        await callback({ name: 'synthetic-admitted', mode: 'exclusive' }); throw new Error('Post-admission failure');
    } });
    state.route = () => response(signed);
    const result = await api.login(ME.email, 'synthetic-password');
    assert.equal(postRequests().length, 1); assert.equal(result.error.code, 'SESSION_UPDATE_UNCONFIRMED');
    assert.equal(result.error.sessionRequestNotSent, undefined);
});

test('grant clears the acquisition timer without cancelling admitted transport work', async () => {
    const timer = globalThis.setTimeout, clear = globalThis.clearTimeout; const live = new Set(); let signal;
    globalThis.setTimeout = (callback, ms, ...args) => { const id = timer(callback, ms, ...args); if (ms === 20000) live.add(id); return id; };
    globalThis.clearTimeout = id => { live.delete(id); clear(id); };
    setLocks({ request(_name, options, callback) {
        signal = options.signal;
        return callback({ name: 'synthetic-granted', mode: 'exclusive' });
    } });
    state.route = () => { assert.equal(live.size, 1, 'only the request transport timer remains after grant'); return response(signed); };
    try {
        const result = await api.login(ME.email, 'synthetic-password');
        assert.ok(result.data); assert.equal(signal.aborted, false); assert.equal(live.size, 0);
    } finally { globalThis.setTimeout = timer; globalThis.clearTimeout = clear; }
});
