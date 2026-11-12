// QA F1-33: "Block user" used to act on a single click. Block and Unblock now ask first through the
// shared ConfirmationModal (the pattern in confirmations.test.mjs). Cancel changes nothing; Confirm runs
// the existing reviewed operation (one prepare, one update) exactly once.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, state, render, response, text, find, click, settle, button } from './bh-harness.mjs';
import { prepared, auditId, userId, reviewToken, nextToken } from './users-operation-fixture.mjs';

const EMAIL = 'reviewed@example.com';
const account = (active) => ({ id: userId, name: 'Reviewed user', email: EMAIL, role: 'user', effective_role: 'user',
    is_active: active, is_verified: true, tenant_id: null, review_token: reviewToken });
let current;
const route = (request) => {
    if (request.path.startsWith('/api/v1/admin/users/operations?')) return response({ items: [], next_cursor: null });
    if (request.path.endsWith('/admin/users/operations') && request.method === 'POST') return response(prepared(request.body), 201);
    if (request.method === 'PATCH') {
        current = { ...current, is_active: request.body.is_active, review_token: nextToken };
        return response({ ...current, operation: 'updated', operation_id: request.body.operation_id, audit_event_id: auditId });
    }
    if (request.path.endsWith('/auth/me')) return response({ id: 'admin', email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null });
    if (request.path.endsWith('/rbac/roles')) return response(['platform_admin', 'user'].map((name) => ({ id: name, name, level: 1 })));
    if (request.path.endsWith('/rbac/permissions')) return response([]);
    if (request.path.endsWith('/admin/tenants')) return response([{ id: 'tenant-one', business_name: 'Fixture tenant' }]);
    if (request.path.startsWith('/api/v1/admin/users') && request.method === 'GET') return response([current]);
    return response({ detail: 'unrouted synthetic request' }, 500);
};
const draw = async () => { await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.UsersPage)))); await settle(350); };
const writes = () => state.requests.filter((request) => request.method !== 'GET');
const prepares = () => state.requests.filter((request) => request.method === 'POST' && request.path.endsWith('/admin/users/operations'));
const updates = () => state.requests.filter((request) => request.method === 'PATCH');
const dialog = () => find('.modal-backdrop');
const control = (verb) => find(`[aria-label="${verb} ${EMAIL}"]`);
// The confirmation's own Cancel button (the page has other buttons with other labels).
const cancelDialog = async () => { await act(async () => [...dialog().querySelectorAll('button')].find((node) => node.textContent.trim() === 'Cancel').click()); };

test.beforeEach(() => { state.route = route; });

test('block user: the click only asks; cancel changes nothing; confirm sends one prepare and one update', async () => {
    current = account(true);
    await draw();
    const opener = control('Block'); opener.focus();
    await act(async () => opener.click());
    assert.equal(writes().length, 0, 'clicking Block must not send anything');
    assert.ok(dialog(), 'a confirmation opens');
    assert.match(dialog().textContent, /Block user/);
    assert.match(dialog().textContent, /reviewed@example\.com/, 'it names the account');
    assert.match(dialog().textContent, /not be able to sign in/, 'it says what blocking does');
    await cancelDialog();
    assert.equal(dialog() === null, true, 'no confirmation is open'); assert.equal(writes().length, 0);
    assert.ok(control('Block'), 'the account still shows as active'); assert.equal(control('Unblock') === null, true, 'the account is not shown as blocked');
    assert.equal(document.activeElement === opener, true, 'focus returns to the Block control');
    await act(async () => control('Block').click()); await click('Confirm');
    assert.equal(document.activeElement === find('[aria-label="Search users"]'), true, 'focus falls back to the search box while the Block control is busy');
    await settle();
    assert.equal(dialog() === null, true, 'no confirmation is open');
    assert.equal(prepares().length, 1); assert.equal(updates().length, 1);
    assert.equal(updates()[0].body.is_active, false); assert.equal(updates()[0].body.expected_review_token, reviewToken);
    assert.match(text(), /account \S+ blocked\./);
    assert.ok(control('Unblock'), 'the refreshed row offers Unblock');
});

test('unblock user: the click only asks; cancel changes nothing; confirm sends one prepare and one update', async () => {
    current = account(false);
    await draw();
    await act(async () => control('Unblock').click());
    assert.equal(writes().length, 0, 'clicking Unblock must not send anything');
    assert.ok(dialog(), 'a confirmation opens');
    assert.match(dialog().textContent, /Unblock user/);
    assert.match(dialog().textContent, /reviewed@example\.com/, 'it names the account');
    assert.match(dialog().textContent, /sign in again/, 'it says what unblocking does');
    await cancelDialog();
    assert.equal(dialog() === null, true, 'no confirmation is open'); assert.equal(writes().length, 0);
    assert.ok(control('Unblock'), 'the account still shows as blocked');
    await act(async () => control('Unblock').click()); await click('Confirm'); await settle();
    assert.equal(dialog() === null, true, 'no confirmation is open');
    assert.equal(prepares().length, 1); assert.equal(updates().length, 1);
    assert.equal(updates()[0].body.is_active, true);
    assert.match(text(), /account \S+ unblocked\./);
    assert.ok(control('Block'), 'the refreshed row offers Block');
});

test('a confirmation left open across a directory refresh cannot dispatch the old review', async () => {
    current = account(true);
    await draw();
    await act(async () => control('Block').click());
    assert.ok(dialog());
    const reads = () => state.requests.filter((request) => request.method === 'GET' && request.path.startsWith('/api/v1/admin/users?')).length;
    const before = reads();
    await click('Refresh'); await settle();
    assert.equal(reads(), before + 1, 'the directory was read again while the confirmation was open');
    await click('Confirm'); await settle();
    assert.equal(prepares().length, 0); assert.equal(writes().length, 0);
});

test('double-clicking Confirm sends a single operation', async () => {
    current = account(true);
    await draw();
    await act(async () => control('Block').click());
    const confirm = button('Confirm');
    await act(async () => { confirm.click(); confirm.click(); });
    await settle();
    assert.equal(prepares().length, 1); assert.equal(updates().length, 1);
});
