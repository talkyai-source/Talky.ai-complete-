import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, state, render, response, text, find, click, settle, deferred, api, dom } from './bh-harness.mjs';

const connector = (extra = {}) => ({
    id: 'c0000000-0000-4000-8000-000000000001', tenant_id: 't1', tenant_name: 'Acme Ltd', type: 'email',
    provider: 'gmail', name: 'Acme Gmail', status: 'active', account_email: 'ops@acme.example',
    token_expires_at: null, token_status: 'valid', last_refreshed_at: null, created_at: '2026-10-01T00:00:00Z', refresh_state: 'idle', provider_revocation_status: 'not_requested',
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
    if (call.method === 'POST') return response({ success: true, connector_id: connector().id, provider_revoked: false, provider_revocation_status: 'unconfirmed' });
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

test('drawer: a successful local revoke retains provider uncertainty in the open drawer', async () => {
    state.route = drawerRoute('/nope');
    let closed = 0, refreshed = 0;
    await render(el(C.ConnectorDetailDrawer, { connector: connector(), onClose: () => { closed += 1; },
        onRefresh: () => { refreshed += 1; } }));
    await click('Revoke Access');
    await modalConfirm('Revoke Access');
    assert.equal(closed, 0);
    assert.match(text(), /Disabled locally.*revocation is unconfirmed/);
    assert.equal(refreshed, 1);
    assert.equal(find('[role="alert"]'), null);
});

test('drawer: a failed reconnect shows the error and does not report a refresh', async () => {
    state.route = drawerRoute('/reconnect', 502, 'Token endpoint rejected the refresh');
    let refreshed = 0;
    await render(el(C.ConnectorDetailDrawer, { connector: connector(), onClose() {}, onRefresh: () => { refreshed += 1; } }));
    await click('Refresh token');
    assert.equal(mutations().length, 1);
    assert.match(text(), /Token endpoint rejected the refresh/);
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
    await iconButton('Refresh token');
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

test('drawer selection change clears private details and confirmation; late old detail cannot repopulate', async () => {
    const old = deferred(); let hold = false;
    const next = connector({ id: 'connector-B', tenant_id: 'tenant-B', name: 'B Gmail' });
    state.route = request => request.path.endsWith('/connector-B') ? response({ detail: 'B detail unavailable' }, 503)
        : hold ? old.promise : response(detail({ account_email: 'private-A@example.invalid' }));
    await render(el(C.ConnectorDetailDrawer, { connector: connector(), onClose() {} }));
    hold = true; await click('Reload details');
    await render(el(C.ConnectorDetailDrawer, { connector: next, onClose() {} }));
    assert.doesNotMatch(text(), /private-A|Revoke Connector Access/);
    assert.match(text(), /B detail unavailable/);
    await act(async () => old.resolve(response(detail({ account_email: 'late-A@example.invalid' }))));
    assert.doesNotMatch(text(), /late-A|private-A/);
    assert.equal([...state.host.querySelectorAll('button')].find(button => button.textContent.trim() === 'Revoke Access').disabled, true);
});

test('drawer rejects foreign detail and blocks action controls', async () => {
    state.route = () => response(detail({ tenant_id: 'different-tenant' }));
    await render(el(C.ConnectorDetailDrawer, { connector: connector(), onClose() {} }));
    assert.match(text(), /different connector/);
    await click('Revoke Access');
    assert.equal(mutations().length, 0);
    assert.doesNotMatch(text(), /Revoke Connector Access/);
});

test('uncertain provider refresh is visible and cannot be blindly replayed', async () => {
    state.route = () => response(detail({ refresh_state: 'uncertain', token_status: 'unknown' }));
    await render(el(C.ConnectorDetailDrawer, { connector: connector(), onClose() {} }));
    assert.match(text(), /uncertain outcome/);
    await click('Refresh token');
    assert.equal(mutations().length, 0);
});

test('same-frame double confirm sends one revoke; a late reply from the old session cannot refresh or close', async () => {
    const pending = deferred(); let refreshed = 0, closed = 0;
    state.route = request => request.method === 'POST' ? pending.promise : response(detail());
    await render(el(C.ConnectorDetailDrawer, { connector: connector(), onClose() { closed++; }, onRefresh() { refreshed++; } }));
    await click('Revoke Access');
    await act(async () => {
        const buttons = [...state.host.querySelectorAll('button')].filter(button => button.textContent.trim() === 'Revoke Access');
        buttons.at(-1).click(); buttons.at(-1).click();
    });
    assert.equal(mutations().length, 1);
    state.route = () => response({ detail: 'New authority denied details' }, 403);
    await act(async () => api.setToken('different-principal'));
    await act(async () => pending.resolve(response({ success: true, connector_id: connector().id, provider_revoked: true, provider_revocation_status: 'confirmed' })));
    assert.equal(refreshed, 0); assert.equal(closed, 0);
    assert.doesNotMatch(text(), /Provider access revocation confirmed|Revoke Connector Access/);
});

test('malformed success receipt cannot report connector revoked', async () => {
    state.route = request => request.method === 'POST' ? response({ success: true, connector_id: 'another-connector' }) : response(detail());
    await render(el(C.ConnectorDetailDrawer, { connector: connector(), onClose() {} }));
    await click('Revoke Access'); await modalConfirm('Revoke Access');
    assert.match(text(), /Disconnection was not confirmed/);
    assert.doesNotMatch(text(), /Disabled locally/);
});

test('connector filtered list ignores late earlier query results', async () => {
    const older = deferred();
    state.route = request => request.path.includes('status=error') ? response(list([connector({ name: 'Error connector', status: 'error' })])) : older.promise;
    await render(el(C.ConnectorsTable));
    await act(async () => {
        const select = find('select[aria-label="Connector status"]'); select.value = 'error';
        select.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
    });
    assert.match(text(), /Acme Ltd/);
    assert.equal(find('button[aria-label="View details for Error connector"]') !== null, true);
    await act(async () => older.resolve(response(list([connector({ name: 'Stale connector' })]))));
    assert.equal(find('button[aria-label="View details for Stale connector"]'), null);
});

test('connector totals use exact global backend counts beyond one hundred and show missing aggregates as unavailable', async () => {
    let summary = { scope: 'all_connectors', total: 207, active: 181, expiring_or_expired: 54, error: 15 };
    state.route = request => request.path.endsWith('/auth/me') ? response({ id: 'platform-admin', role: 'platform_admin', email: 'a@example.invalid', name: 'Admin' })
        : request.path.includes('/admin/connectors?') ? response({ ...list([connector()]), total: 207, summary })
        : response({ detail: 'Usage unavailable' }, 503);
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.ConnectorsPage))));
    const values = () => [...state.host.querySelectorAll('.stats-grid .stat-value')].map(node => node.textContent);
    assert.deepEqual(values(), ['207', '181', '54', '15']);
    summary = undefined;
    await act(async () => state.host.querySelector('.page-header button').click());
    assert.deepEqual(values(), ['Unavailable', 'Unavailable', 'Unavailable', 'Unavailable']);
    assert.match(text(), /Complete connector totals are unavailable/);
});
