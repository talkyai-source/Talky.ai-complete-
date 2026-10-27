import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, api, dom, state, render, response, find, click, deferred, settle } from './bh-harness.mjs';

const ME = { id: 'admin-1', email: 'admin@example.invalid', role: 'platform_admin' };
const TOKEN_KEY = 'admin_token';

function Probe() {
    const auth = C.useAuth();
    return el('div', null,
        el('p', { 'data-testid': 'principal' }, auth.user?.id ?? 'signed out'),
        el('p', { 'data-testid': 'loading' }, String(auth.isLoading)),
        el('p', { 'data-testid': 'error' }, auth.error ?? ''),
        el('button', { onClick: () => void api.getTenants() }, 'Call tenants'),
        el('button', { onClick: () => void api.getAdminRecordingAudio('rec-1').catch(() => {}) }, 'Fetch audio'),
        el('button', { onClick: () => void auth.login('admin@example.invalid', 'wrong-password') }, 'Log in'),
        el('button', { onClick: () => void auth.checkAuth() }, 'Retry session check'));
}
const principal = () => find('[data-testid="principal"]').textContent;
const draw = async () => { await render(el(C.AuthProvider, null, el(Probe))); await settle(); };
const stored = () => dom.window.localStorage.getItem(TOKEN_KEY);

// admin-frontend-gateway-6 ---------------------------------------------------

test('page load during a backend 5xx keeps the stored token and does not authenticate', async () => {
    const token = api.getToken();
    state.route = () => response({ detail: 'upstream restarting' }, 503);
    await draw();
    assert.equal(stored(), token, 'a transient /auth/me failure must not wipe the token');
    assert.equal(api.getToken(), token);
    assert.equal(principal(), 'signed out');
    assert.equal(find('[data-testid="loading"]').textContent, 'false');
    assert.ok(find('[data-testid="error"]').textContent.length > 0, 'the operator must see why they are not signed in');
});

test('page load with the network down keeps the stored token', async () => {
    const token = api.getToken();
    state.route = () => { throw new Error('connect ECONNREFUSED'); };
    await draw();
    assert.equal(stored(), token);
    assert.equal(principal(), 'signed out');
    assert.match(find('[data-testid="error"]').textContent, /ECONNREFUSED|reach|unavailable/i);
});

test('a retry after the outage recovers the session without re-entering credentials', async () => {
    let up = false;
    state.route = () => (up ? response(ME) : response({ detail: 'down' }, 502));
    await draw();
    assert.equal(principal(), 'signed out');
    up = true;
    await click('Retry session check');
    assert.equal(principal(), 'admin-1');
    assert.equal(find('[data-testid="error"]').textContent, '');
});

for (const status of [401, 403]) {
    test(`page load with a rejected token (${status}) clears it`, async () => {
        state.route = () => response({ detail: 'Not authenticated' }, status);
        await draw();
        assert.equal(stored(), null);
        assert.equal(api.getToken(), null);
        assert.equal(principal(), 'signed out');
    });
}

// admin-frontend-gateway-5 ---------------------------------------------------

const signedInRoute = (other) => (call) => (call.path.endsWith('/auth/me') ? response(ME) : other(call));

test('a 401 from any admin call signs the UI out and clears the stored token', async () => {
    state.route = signedInRoute(() => response({ detail: 'Session expired' }, 401));
    await draw();
    assert.equal(principal(), 'admin-1');
    await click('Call tenants');
    assert.equal(principal(), 'signed out');
    assert.equal(stored(), null);
    assert.equal(api.getToken(), null);
    assert.match(find('[data-testid="error"]').textContent, /session/i);
});

test('a 401 from an audio download also signs the UI out', async () => {
    state.route = signedInRoute(() => response({ detail: 'Session expired' }, 401));
    await draw();
    await click('Fetch audio');
    assert.equal(principal(), 'signed out');
    assert.equal(stored(), null);
});

test('a 500 does not sign the admin out', async () => {
    state.route = signedInRoute(() => response({ detail: 'boom' }, 500));
    await draw();
    await click('Call tenants');
    assert.equal(principal(), 'admin-1');
    assert.ok(stored());
});

test('a 401 from the login call means bad credentials, not an expired session', async () => {
    state.route = (call) => {
        if (call.path.endsWith('/auth/me')) return response({ detail: 'Not authenticated' }, 401);
        if (call.path.endsWith('/auth/login')) return response({ detail: 'Invalid credentials' }, 401);
        return response({}, 500);
    };
    await draw();
    await click('Log in');
    assert.equal(find('[data-testid="error"]').textContent, 'Invalid credentials');
});

test('a late 401 from a previous session cannot sign out a newer login', async () => {
    const slow = deferred();
    state.route = (call) => {
        if (call.path.endsWith('/auth/me')) return response(call.authorization === 'Bearer new-token'
            ? { ...ME, id: 'admin-2', email: 'b@example.invalid' } : ME);
        if (call.path.endsWith('/auth/login')) {
            return response({ access_token: 'new-token', user_id: 'admin-2', email: 'b@example.invalid', role: 'platform_admin' });
        }
        return slow.promise;
    };
    await draw();
    await click('Call tenants');
    await click('Log in');
    assert.equal(principal(), 'admin-2');
    await act(async () => slow.resolve(response({ detail: 'old session expired' }, 401)));
    assert.equal(principal(), 'admin-2');
    assert.equal(api.getToken(), 'new-token');
});
