import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, state, render, response, text, find, click, settle } from './bh-harness.mjs';

const connector = (extra = {}) => ({
    id: 'c0000000-0000-4000-8000-000000000001', tenant_id: 't1', tenant_name: 'Acme Ltd', type: 'email',
    provider: 'gmail', name: 'Acme Gmail', status: 'active', account_email: 'ops@acme.example',
    token_expires_at: null, token_status: 'valid', last_refreshed_at: null, created_at: '2026-10-01T00:00:00Z',
    ...extra,
});
const detail = (extra = {}) => ({ ...connector(), scopes: [], error_message: null, ...extra });
const mutations = () => state.requests.filter((r) => r.method === 'POST');
const list = (items) => ({ items, total: items.length, page: 1, page_size: 20 });
// Once the confirm modal is open its confirm button shares a label with the opener; it renders last.
const modalConfirm = async (label) => {
    const matches = [...state.host.querySelectorAll('button')].filter((b) => b.textContent.trim() === label);
    await act(async () => matches.at(-1).click());
};
const iconButton = async (title) => { await act(async () => find(`button[title="${title}"]`).click()); };

// admin-frontend-gateway-2: drawer -------------------------------------------

const drawerRoute = (failPath, status = 500, message = 'Provider revoke failed') => (call) => {
    if (call.path.endsWith(failPath)) return response({ detail: message }, status);
    if (call.method === 'POST') return response({ success: true });
    return response(detail());
};

test('drawer: a failed revoke keeps the confirm open, shows the error, and does not close or refresh', async () => {
    state.route = drawerRoute('/revoke', 500, 'Provider revoke failed');
    let closed = 0, refreshed = 0;
    await render(el(C.ConnectorDetailDrawer, { connector: connector(), onClose: () => { closed += 1; },
        onRefresh: () => { refreshed += 1; } }));
    await click('Revoke Access');
    await modalConfirm('Revoke Access');
    assert.equal(mutations().length, 1);
    assert.match(text(), /Revoke Connector Access/, 'confirm must stay open on failure');
    assert.match(find('[role="alert"]').textContent, /Provider revoke failed/);
    assert.equal(closed, 0);
    assert.equal(refreshed, 0);
});

test('drawer: a successful revoke refreshes and closes', async () => {
    state.route = drawerRoute('/nope');
    let closed = 0, refreshed = 0;
    await render(el(C.ConnectorDetailDrawer, { connector: connector(), onClose: () => { closed += 1; },
        onRefresh: () => { refreshed += 1; } }));
    await click('Revoke Access');
    await modalConfirm('Revoke Access');
    assert.equal(closed, 1);
    assert.equal(refreshed, 1);
    assert.equal(find('[role="alert"]'), null);
});

test('drawer: a failed reconnect shows the error and does not report a refresh', async () => {
    state.route = drawerRoute('/reconnect', 502, 'Token endpoint rejected the refresh');
    let refreshed = 0;
    await render(el(C.ConnectorDetailDrawer, { connector: connector(), onClose() {}, onRefresh: () => { refreshed += 1; } }));
    await click('Force Reconnect');
    assert.equal(mutations().length, 1);
    assert.match(find('[role="alert"]').textContent, /Token endpoint rejected the refresh/);
    assert.equal(refreshed, 0);
});

// admin-frontend-gateway-2: list ----------------------------------------------

test('table: a failed connector list shows an error, never "No Connectors Found"', async () => {
    state.route = () => response({ detail: 'Connector service down' }, 503);
    await render(el(C.ConnectorsTable, {}));
    await settle();
    assert.doesNotMatch(text(), /No Connectors Found/);
    assert.match(find('[role="alert"]').textContent, /Connector service down/);
});

test('table: a failed revoke keeps the confirm open and shows the error', async () => {
    state.route = (call) => {
        if (call.path.includes('/revoke')) return response({ detail: 'Provider revoke failed' }, 500);
        return response(list([connector()]));
    };
    await render(el(C.ConnectorsTable, {}));
    await settle();
    await iconButton('Revoke Access');
    await modalConfirm('Revoke');
    assert.equal(mutations().length, 1);
    assert.match(text(), /Revoke Connector Access/, 'confirm must stay open on failure');
    assert.match(find('[role="alert"]').textContent, /Provider revoke failed/);
});

test('table: a failed reconnect is reported', async () => {
    state.route = (call) => {
        if (call.path.includes('/reconnect')) return response({ detail: 'Reconnect refused' }, 500);
        return response(list([connector({ token_status: 'expired' })]));
    };
    await render(el(C.ConnectorsTable, {}));
    await settle();
    await iconButton('Force Reconnect');
    assert.equal(mutations().length, 1);
    assert.match(find('[role="alert"]').textContent, /Reconnect refused/);
});

// admin-frontend-gateway-2: actions list --------------------------------------

test('actions table: an API error response renders the error banner with Retry', async () => {
    state.route = () => response({ detail: 'Actions store unavailable' }, 500);
    await render(el(C.ActionsTable, { onActionSelect() {} }));
    await settle();
    assert.doesNotMatch(text(), /No Actions Found/);
    assert.match(text(), /Actions store unavailable/);
    assert.match(text(), /Retry/);
});
