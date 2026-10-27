import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, dom, state, render, response, find, text, click, deferred, settle } from './bh-harness.mjs';

const ME = { id: 'logout-admin', email: 'logout@example.invalid', name: 'Logout admin', role: 'platform_admin', tenant_id: null };
const marker = 'talky.admin.logout.pending';
let auth;
function Screen() {
    auth = C.useAuth();
    return el('div', null, el('p', { 'data-principal': true }, auth.user?.id ?? 'signed out'),
        auth.isAuthenticated ? el(C.Header) : el(C.LoginPage));
}
const draw = async (key = 'initial') => {
    await render(el(C.AuthProvider, { key }, el(C.MemoryRouter, null, el(Screen)))); await settle();
};
const logoutRequests = () => state.requests.filter(request => request.path.endsWith('/auth/logout'));
const identityReads = () => state.requests.filter(request => request.path.endsWith('/auth/me'));
const principal = () => find('[data-principal]').textContent;

test('the actual recovery button retries cookie sign-out after reload without rediscovering the live session', async () => {
    api.setToken(null); localStorage.setItem(marker, 'unconfirmed');
    state.route = request => request.path.endsWith('/auth/logout') ? response({}) : response(ME);
    await draw();
    assert.match(text(), /Server sign-out could not be confirmed/);
    assert.match(text(), /Retrying signs out the account currently signed in in this browser/);
    const recovery = find('[role="alert"] button'); assert.ok(recovery);
    const label = recovery.textContent;
    // Click the actual rendered control, including the original broken "Retry session check" baseline.
    await act(async () => recovery.click()); await settle();
    assert.equal(logoutRequests().length, 1, 'visible recovery must dispatch canonical server logout');
    assert.equal(label, 'Retry browser sign-out');
    assert.equal(logoutRequests()[0].method, 'POST');
    assert.equal(logoutRequests()[0].credentials, 'include');
    assert.equal(logoutRequests()[0].authorization, undefined);
    assert.equal(identityReads().length, 0, 'successful retry must never authenticate the old cookie');
    assert.equal(principal(), 'signed out'); assert.equal(api.getToken(), null);
    assert.equal(localStorage.getItem(marker), null);
    assert.doesNotMatch(text(), /Server sign-out could not be confirmed/);
});

test('failed logout retries remain explicit, single-flight and signed out until a confirmed success', async () => {
    const retry = deferred(); let logoutAttempt = 0;
    state.route = request => request.path.endsWith('/auth/logout')
        ? ++logoutAttempt === 1 ? response({ detail: 'gateway unavailable' }, 503) : retry.promise
        : response(ME);
    await draw(); await click('Sign out');
    assert.equal(principal(), 'signed out'); assert.match(text(), /Server sign-out could not be confirmed/);
    const reads = identityReads().length;
    const button = find('[role="alert"] button');
    await act(async () => { button.click(); button.click(); });
    assert.equal(logoutRequests().length, 2, 'one initial attempt and one deliberate retry');
    assert.equal(find('#email').disabled, true);
    await act(async () => auth.login('new@example.invalid', 'unused'));
    assert.equal(state.requests.filter(request => request.path.endsWith('/auth/login')).length, 0);
    await act(async () => retry.resolve(response({ detail: 'still unavailable' }, 503)));
    assert.equal(localStorage.getItem(marker), 'unconfirmed'); assert.equal(principal(), 'signed out');
    assert.match(text(), /Server sign-out could not be confirmed/);
    state.route = request => request.path.endsWith('/auth/logout') ? response({}) : response(ME);
    await click('Retry browser sign-out');
    assert.equal(logoutRequests().length, 3); assert.equal(identityReads().length, reads);
    assert.equal(localStorage.getItem(marker), null); assert.equal(principal(), 'signed out');
});

test('a late failed retry response cannot erase newer frontend cross-tab identity state', async () => {
    const retry = deferred(); const newer = { ...ME, id: 'replacement', email: 'replacement@example.invalid', role: 'tenant_admin' };
    api.setToken(null); localStorage.setItem(marker, 'unconfirmed');
    state.route = request => request.path.endsWith('/auth/logout') ? retry.promise : response(newer);
    await draw(); await click('Retry browser sign-out');
    await act(async () => {
        localStorage.removeItem(marker); localStorage.setItem('admin_token', 'replacement-token');
        window.dispatchEvent(new dom.window.StorageEvent('storage', { key: 'admin_token', oldValue: null, newValue: 'replacement-token' }));
    });
    await act(async () => retry.resolve(response({ detail: 'old sign-out response failed' }, 503))); await settle();
    assert.equal(principal(), newer.id); assert.equal(api.getToken(), 'replacement-token');
    assert.doesNotMatch(text(), /Server sign-out could not be confirmed/);
});
