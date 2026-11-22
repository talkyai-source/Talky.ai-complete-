import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, dom, state, render, response, text, find } from './bh-harness.mjs';

const ME = { id: 'admin-A', email: 'a@example.invalid', name: 'Admin A', role: 'platform_admin', tenant_id: null };
const signedIn = { access_token: 'access-A2', user_id: ME.id, email: ME.email, role: ME.role, tenant_id: null };
const denied = () => response({ detail: 'Authentication required' }, 401);
const jwt = (sub, tenant = null, role = 'platform_admin') => `h.${Buffer.from(JSON.stringify({ sub, tenant_id: tenant, role })).toString('base64url')}.s`;
const upload = () => api.uploadSupportAttachment(new File(['x'], 'log.txt', { type: 'text/plain' }));
const uploads = () => state.requests.filter((request) => request.path.endsWith('/support/attachments'));
const refreshes = () => state.requests.filter((request) => request.path.endsWith('/auth/refresh'));
test.beforeEach(() => { api.setToken(null); localStorage.setItem(C.MFA_MARKER_KEY, JSON.stringify({ userId: ME.id })); });

test('an upload rejected for auth renews once, is not re-sent, and signs nobody out', async () => {
    api.setToken('expired-A'); api.expectIdentity({ userId: ME.id, tenantId: null, role: ME.role });
    let signedOut = 0; const off = api.onUnauthorized(() => { signedOut += 1; });
    state.route = (request) => request.path.endsWith('/auth/refresh') ? response(signedIn) : denied();
    const result = await upload(); off();
    assert.equal(result.error.code, 'AUTH_RETRY_REQUIRED');
    assert.equal(refreshes().length, 1); assert.equal(uploads().length, 1);
    assert.equal(signedOut, 0); assert.equal(api.getToken(), 'access-A2');
});

test('an upload whose renewal is refused still signs the person out', async () => {
    api.setToken('expired-A'); api.expectIdentity({ userId: ME.id, tenantId: null, role: ME.role });
    let signedOut = 0; const off = api.onUnauthorized(() => { signedOut += 1; });
    state.route = () => denied();
    const result = await upload(); off();
    assert.equal(refreshes().length, 1); assert.equal(uploads().length, 1);
    assert.equal(signedOut, 1); assert.equal(result.error.status, 401);
});

const guarded = () => render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: ['/system-health'] },
    el(C.Routes, null, el(C.Route, { path: '/login', element: el('p', null, 'Sign-in screen') }),
        el(C.Route, { path: '*', element: el(C.AdminRouteGuard, null, el('p', null, 'Private page')) })))));
const storage = (value) => act(async () => {
    if (value) localStorage.setItem('admin_token', value); else localStorage.removeItem('admin_token');
    window.dispatchEvent(new dom.window.StorageEvent('storage', { key: 'admin_token', newValue: value }));
});

test('a renewed token for the same user and tenant from another tab keeps the page mounted', async () => {
    api.setToken(jwt(ME.id)); state.route = () => response(ME); await guarded();
    assert.match(text(), /Private page/);
    const before = api.getAuthGeneration(), seen = state.requests.length, renewed = jwt(ME.id) + 'x';
    await storage(renewed);
    assert.match(text(), /Private page/); assert.doesNotMatch(text(), /Verifying access/);
    assert.equal(api.getAuthGeneration(), before); assert.equal(api.getToken(), renewed);
    assert.equal(state.requests.length, seen, 'no re-verification round trip');
    await act(async () => api.getTenants());
    assert.equal(state.requests.at(-1).authorization, `Bearer ${renewed}`);
});

for (const [name, value] of [['another user', jwt('admin-B')], ['another tenant', jwt(ME.id, 'tenant-X')],
    ['an unreadable token', 'not-a-jwt'], ['a removed token', null],
    ['the same user with another role', jwt(ME.id, null, 'support_agent')]]) {
    test(`a token change to ${name} from another tab still resets this tab`, async () => {
        api.setToken(jwt(ME.id)); state.route = () => response(ME); await guarded();
        assert.match(text(), /Private page/);
        const before = api.getAuthGeneration();
        state.route = () => denied();
        await storage(value);
        assert.doesNotMatch(text(), /Private page/); assert.ok(api.getAuthGeneration() > before);
    });
}
