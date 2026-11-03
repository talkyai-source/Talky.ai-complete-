// Platform admins must complete a second factor before the Admin console admits
// them. The backend does not enforce this yet (BACKEND GAP G2), so the client
// refuses a full token that arrives without an MFA challenge and only restores a
// session that carries the local proof-of-MFA marker.
import assert from 'node:assert/strict';
import test from 'node:test';
import { Routes, Route } from 'react-router-dom';
import { C, api, el, state, render, response, find, click, setInput } from './bh-harness.mjs';

const ADMIN = { id: 'admin-A', email: 'a@example.invalid', name: 'Admin A', role: 'platform_admin', tenant_id: null };
const AGENT = { id: 'agent-Z', email: 'z@example.invalid', name: 'Agent Z', role: 'agent', tenant_id: 'tenant-Z' };
const fullToken = (me) => ({ access_token: `access-${me.id}`, user_id: me.id, email: me.email, role: me.role, tenant_id: me.tenant_id, mfa_required: false });
const challenge = { access_token: '', mfa_required: true, mfa_challenge_token: 'one-use-challenge-A' };
const denied = () => response({ detail: 'Authentication required' }, 401);
const MARKER = C.MFA_MARKER_KEY;
let currentAuth;
function Probe() {
    currentAuth = C.useAuth();
    return el('p', { 'data-principal': true }, currentAuth.user?.id ?? 'signed out');
}
const principal = () => find('[data-principal]')?.textContent;
const draw = async () => {
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: ['/login'] },
        el(Probe), el(Routes, null, el(Route, { path: '/login', element: el(C.LoginPage) }), el(Route, { path: '*', element: el('p', null, 'Available section') })))));
};
const signIn = async (email) => {
    await setInput(find('#email'), email); await setInput(find('#password'), 'synthetic-password'); await click('Sign In');
};
const routes = ({ login, me, bearer }) => (request) => {
    if (request.path.endsWith('/auth/login')) return response(login);
    if (request.path.endsWith('/auth/mfa/verify')) return response(fullToken(ADMIN));
    if (request.path.endsWith('/auth/logout')) return response({});
    if (request.path.endsWith('/auth/me')) return request.authorization === `Bearer ${bearer}` ? response(me) : denied();
    if (request.path.endsWith('/auth/refresh')) return denied();
    return response({}, 500);
};
const logouts = () => state.requests.filter((request) => request.path.endsWith('/auth/logout'));
const alertText = () => find('.login-error')?.textContent ?? null;
const retryButton = () => [...state.host.querySelectorAll('button')].find((node) => node.textContent.trim() === 'Retry session check');
// These tests exercise the rule itself: the harness must not vouch for 2FA.
test.beforeEach(() => { state.mfaProof = false; api.setToken(null); localStorage.removeItem(MARKER); localStorage.removeItem('admin_token'); });

test('a password login that returns a full token without an MFA challenge is refused for a platform admin', async () => {
    state.route = routes({ login: fullToken(ADMIN), me: ADMIN, bearer: 'access-admin-A' });
    await draw(); await signIn(ADMIN.email);
    assert.equal(principal(), 'signed out');
    assert.equal(api.getToken(), null);
    assert.equal(localStorage.getItem('admin_token'), null, 'the refused token is never persisted');
    assert.equal(localStorage.getItem(MARKER), null);
    assert.equal(logouts().length, 1, 'the server session opened by the login is ended');
    assert.equal(logouts()[0].authorization, 'Bearer access-admin-A');
    assert.equal(state.requests.some((request) => request.path.endsWith('/auth/me') && request.authorization === 'Bearer access-admin-A'), false,
        'the refused session is never verified as the signed-in identity');
    assert.ok(find('#password'), 'stays on the password form');
    assert.equal(alertText(), C.MFA_REQUIRED_MESSAGE);
    assert.equal(retryButton(), undefined, 'no session retry is offered for the MFA requirement');
});

test('a role the route guard never admits keeps the existing password-only flow', async () => {
    state.route = routes({ login: fullToken(AGENT), me: AGENT, bearer: 'access-agent-Z' });
    await draw(); await signIn(AGENT.email);
    assert.equal(principal(), AGENT.id);
    assert.equal(logouts().length, 0);
    assert.equal(localStorage.getItem(MARKER), null);
});

test('completing the MFA challenge stores the marker for that user', async () => {
    state.route = routes({ login: challenge, me: ADMIN, bearer: 'access-admin-A' });
    await draw(); await signIn(ADMIN.email);
    assert.equal(principal(), 'signed out'); assert.equal(localStorage.getItem(MARKER), null);
    await setInput(find('#factor'), '123456'); await click('Verify');
    assert.equal(principal(), ADMIN.id);
    assert.deepEqual(JSON.parse(localStorage.getItem(MARKER)), { userId: ADMIN.id });
    assert.equal(logouts().length, 0);
});

for (const [variant, marker] of [['no marker', null], ['another user\'s marker', { userId: 'admin-B' }]]) {
    test(`a restored platform admin session with ${variant} is signed out and sent to /login`, async () => {
        localStorage.setItem('admin_token', 'saved-A');
        if (marker) localStorage.setItem(MARKER, JSON.stringify(marker));
        state.route = routes({ login: challenge, me: ADMIN, bearer: 'saved-A' });
        await draw();
        assert.equal(principal(), 'signed out');
        assert.equal(api.getToken(), null);
        assert.equal(localStorage.getItem('admin_token'), null);
        assert.equal(localStorage.getItem(MARKER), null);
        assert.equal(logouts().length, 1); assert.equal(logouts()[0].authorization, 'Bearer saved-A');
        assert.ok(find('#password'));
        assert.equal(alertText(), C.MFA_REQUIRED_MESSAGE);
    });
}

test('a restored platform admin session with its own marker is admitted', async () => {
    localStorage.setItem('admin_token', 'saved-A');
    localStorage.setItem(MARKER, JSON.stringify({ userId: ADMIN.id }));
    state.route = routes({ login: challenge, me: ADMIN, bearer: 'saved-A' });
    await draw();
    assert.equal(principal(), ADMIN.id);
    assert.equal(logouts().length, 0);
    assert.deepEqual(JSON.parse(localStorage.getItem(MARKER)), { userId: ADMIN.id });
});

test('other sign-in errors keep the session retry control', async () => {
    state.route = (request) => request.path.endsWith('/auth/me') ? response({ detail: 'Gateway down' }, 503) : denied();
    await draw();
    assert.equal(principal(), 'signed out');
    assert.ok(alertText()); assert.notEqual(alertText(), C.MFA_REQUIRED_MESSAGE);
    assert.ok(retryButton());
});
