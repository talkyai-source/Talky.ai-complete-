import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, state, render, response, text, button, click } from './bh-harness.mjs';

const connector = (extra = {}) => ({
    id: 'c0000000-0000-4000-8000-000000000001', tenant_id: 'tenant-owned', tenant_name: 'Owned tenant',
    type: 'email', provider: 'gmail', name: 'Owned Gmail', status: 'active',
    account_email: null, token_expires_at: null, token_status: 'unknown', last_refreshed_at: null,
    created_at: '2026-10-01T00:00:00Z', refresh_state: 'idle', provider_revocation_status: 'not_requested',
    scopes: [], error_message: null, ...extra,
});
const posts = () => state.requests.filter(request => request.method === 'POST');
const mount = card => render(el(C.ConnectorDetailDrawer, { connector: card, onClose() {} }));

test('pending authorization explains why token refresh is unavailable and sends no refresh', async () => {
    state.route = () => response(connector({ status: 'pending' }));
    await mount(connector({ status: 'pending' }));
    assert.equal(button('Refresh token').disabled, true);
    assert.match(text(), /Authorization is pending/);
    assert.match(text(), /finish connecting this account/);
    await click('Refresh token');
    assert.equal(posts().length, 0);
    assert.equal(button('Revoke Access').disabled, false, 'Pending authorization can still be cancelled');
});

test('fresh pending detail controls refresh even when the list card was active', async () => {
    state.route = () => response(connector({ status: 'pending' }));
    await mount(connector());
    assert.equal(button('Refresh token').disabled, true);
    await click('Refresh token');
    assert.equal(posts().length, 0);
});

test('a real detail reload can enable refresh after pending authorization becomes active', async () => {
    let status = 'pending';
    state.route = request => request.method === 'POST'
        ? response({ success: true, connector_id: connector().id })
        : response(connector({ status }));
    await mount(connector({ status }));
    assert.equal(button('Refresh token').disabled, true);
    status = 'active';
    await click('Reload details');
    assert.equal(button('Refresh token').disabled, false);
    assert.doesNotMatch(text(), /Authorization is pending/);
    await click('Refresh token');
    assert.equal(posts().length, 1);
    assert.match(text(), /Token refresh confirmed/);
});

for (const status of ['active', 'expired', 'error']) {
    test(`the Admin backend's ${status} refresh path remains available with idle detail`, async () => {
        state.route = request => request.method === 'POST'
            ? response({ success: true, connector_id: connector().id })
            : response(connector({ status }));
        await mount(connector({ status }));
        assert.equal(button('Refresh token').disabled, false);
        await click('Refresh token');
        assert.equal(posts().length, 1);
        assert.match(text(), /Token refresh confirmed/);
    });
}

test('an unsupported status cannot offer a token refresh', async () => {
    state.route = () => response(connector({ status: 'unknown' }));
    await mount(connector());
    assert.equal(button('Refresh token').disabled, true);
    await click('Refresh token');
    assert.equal(posts().length, 0);
});
