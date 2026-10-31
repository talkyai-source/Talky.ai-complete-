import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, dom, state, render, response, find, text, click, settle, deferred, setInput, button } from './bh-harness.mjs';
import { prepared, operationId, userId, tenantId } from './users-operation-fixture.mjs';

const user = (id = 'one') => ({ id: id === 'one' ? userId : id, name: id, email: `${id}@example.invalid`, role: 'user', effective_role: 'user', is_active: true, is_verified: true, tenant_id: null, review_token: 'v1:' + '1'.repeat(64) });
const ordinary = (request) => {
    if (request.path.startsWith('/api/v1/admin/users/operations?')) return response({ items: [], next_cursor: null });
    if (request.path.endsWith('/admin/users/operations') && request.method === 'POST') return response(prepared(request.body), 201);
    if (request.path.endsWith('/auth/me')) return response({ id: 'admin', email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null });
    if (request.path.endsWith('/rbac/roles')) return response(['platform_admin', 'user'].map((name) => ({ id: name, name, level: 1 })));
    if (request.path.endsWith('/rbac/permissions')) return response([]);
    if (request.path.endsWith('/admin/tenants')) return response([{ id: 'tenant-one', business_name: 'Fixture tenant' }]);
    if (request.path.startsWith('/api/v1/admin/users') && request.method === 'GET') return response([user()]);
    return response({ detail: 'unrouted synthetic request' }, 500);
};
const draw = async () => {
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.UsersPage))));
    await settle(350);
};
const ariaClick = async (name) => { const node = find(`[aria-label="${name}"]`); assert.ok(node, name); await act(async () => node.click()); };
const submit = async () => { await act(async () => find('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))); };

test.beforeEach(() => { dom.window.sessionStorage.clear(); state.route = ordinary; });

test('deactivated fallback is reported as deactivated, never as deleted', async () => {
    state.route = (r) => r.method === 'DELETE' ? response({ id: userId, outcome: 'deactivated', operation_id: operationId, audit_event_id: '30000000-0000-4000-8000-000000000001', detail: 'User had linked records and was deactivated instead of hard-deleted.', user: { ...user(), is_active: false, review_token: 'v1:' + '2'.repeat(64) } }) : ordinary(r);
    await draw(); await ariaClick('Delete one@example.invalid'); await click('Delete');
    assert.match(find('[role="status"]').textContent, /deactivated instead/);
    assert.doesNotMatch(find('[role="status"]').textContent, /one@example.invalid deleted/);
});

test('failed user reads show unavailable instead of a successful empty list', async () => {
    state.route = (r) => r.path.includes('/admin/users') ? response({ detail: 'database unavailable' }, 503) : ordinary(r);
    await draw();
    assert.match(text(), /database unavailable/);
    assert.match(text(), /User list unavailable/);
    assert.doesNotMatch(text(), /No users found/);
});

test('a slow old search cannot overwrite the latest search', async () => {
    const old = deferred();
    state.route = (r) => r.path.includes('/admin/users?search=latest') ? response([user('latest')])
        : r.path.includes('/admin/users?') ? old.promise.then((v) => v.clone()) : ordinary(r);
    await draw();
    await setInput(find('[aria-label="Search users"]'), 'latest'); await settle(350);
    await act(async () => old.resolve(response([user('obsolete')])));
    assert.match(text(), /latest@example.invalid/);
    assert.doesNotMatch(text(), /obsolete@example.invalid/);
});

test('missing tenant/reference data blocks creation instead of inventing usable options', async () => {
    state.route = (r) => r.path.endsWith('/admin/tenants') ? response({ detail: 'tenant lookup unavailable' }, 503) : ordinary(r);
    await draw();
    assert.match(text(), /tenant lookup unavailable/);
    assert.equal(button('Add User').disabled, true);
});

test('tenant is required for ordinary users and temporary password is masked', async () => {
    await draw(); await click('Add User');
    assert.equal(find('#add-user-tenant').required, true);
    assert.equal(find('#add-user-password').type, 'password');
    await setInput(find('#add-user-name'), 'Test user');
    await setInput(find('#add-user-email'), 'new@example.invalid');
    await setInput(find('#add-user-password'), 'synthetic-password');
    await submit();
    assert.match(find('[role="alert"]').textContent, /Select a tenant/);
    assert.equal(state.requests.filter((r) => r.method === 'POST' && r.path.endsWith('/admin/users')).length, 0);
    await act(async () => { const role = find('#add-user-role'); role.value = 'platform_admin'; role.dispatchEvent(new dom.window.Event('change', { bubbles: true })); });
    assert.equal(find('#add-user-tenant').required, false);
});

test('rapid block clicks dispatch only one mutation and late old-session result is discarded', async () => {
    const pending = deferred();
    state.route = (r) => r.method === 'PATCH' ? pending.promise : ordinary(r);
    await draw();
    const control = find('[aria-label="Block one@example.invalid"]');
    await act(async () => { control.click(); control.click(); });
    assert.equal(state.requests.filter((r) => r.method === 'PATCH').length, 1);
    await act(async () => api.setToken('different-session'));
    await act(async () => pending.resolve(response({ ...user(), is_active: false })));
    assert.doesNotMatch(text(), /one@example.invalid blocked\./);
});

test('failed mutation remains visible through a successful list reload', async () => {
    state.route = (r) => r.method === 'PATCH' ? response({ detail: 'Current authority changed' }, 409) : ordinary(r);
    await draw(); await ariaClick('Block one@example.invalid'); await click('Refresh');
    assert.match(find('[role="alert"]').textContent, /Current authority changed/);
});

test('user modal traps focus, makes background inert, closes with Escape and returns focus', async () => {
    await draw();
    const opener = button('Add User'); opener.focus(); await click('Add User');
    assert.equal(document.activeElement.id, 'add-user-name');
    assert.ok(find('.main-content').hasAttribute('inert'));
    const first = find('[aria-label="Close add user"]'), last = button('Create user');
    last.focus();
    document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }));
    assert.equal(document.activeElement, first);
    document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }));
    assert.equal(document.activeElement, last);
    await act(async () => document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    assert.equal(find('[role="dialog"]'), null);
    assert.equal(document.activeElement, opener);
    assert.equal(find('.main-content').hasAttribute('inert'), false);
});

test('confirmation dialog keeps focus inside and returns it when cancelled', async () => {
    await draw();
    const opener = find('[aria-label="Delete one@example.invalid"]'); opener.focus();
    await ariaClick('Delete one@example.invalid');
    const confirm = button('Delete'); confirm.focus();
    document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }));
    assert.equal(document.activeElement.getAttribute('aria-label'), 'Close');
    await click('Cancel');
    assert.equal(document.activeElement, opener);
});

test('user pagination requests the existing server offset and shows every page without a false total', async () => {
    state.route = (r) => {
        if (!r.path.includes('/admin/users?')) return ordinary(r);
        const query = new URL(r.path, 'https://example.invalid').searchParams;
        assert.equal(query.get('limit'), '51');
        return response(query.get('offset') === '50' ? [user('page-two')]
            : Array.from({ length: 51 }, (_, i) => user(`entry-${i}`)));
    };
    await draw();
    assert.match(text(), /50 shown · Page 1/);
    assert.doesNotMatch(text(), /entry-50@example.invalid/);
    await click('Next users'); await settle(350);
    assert.match(text(), /page-two@example.invalid/);
    assert.match(text(), /1 shown · Page 2/);
    assert.equal(button('Next users').disabled, true);
    await click('Previous users'); await settle(350);
    assert.match(text(), /entry-0@example.invalid/);
});

test('tenant lookup uses server search and submits the chosen tenant identity', async () => {
    state.route = (r) => r.path.includes('/admin/tenants?search=Remote')
        ? response([{ id: tenantId, business_name: 'Remote tenant' }])
        : r.method === 'POST' && r.path.endsWith('/admin/users') ? response({ request_id: r.body.request_id, state: 'created', audit_event_id: '30000000-0000-4000-8000-000000000001', user_id: '40000000-0000-4000-8000-000000000001', user: { ...user('40000000-0000-4000-8000-000000000001'), ...r.body }, intent: { name: r.body.name, email: r.body.email, role: r.body.role, tenant_id: r.body.tenant_id } }) : ordinary(r);
    await draw(); await click('Add User');
    await setInput(find('#add-user-name'), 'Test user');
    await setInput(find('#add-user-email'), 'new@example.invalid');
    await setInput(find('#add-user-password'), 'synthetic-password');
    await setInput(find('#add-user-tenant-search'), 'Remote'); await settle(350);
    assert.match(find('#add-user-tenant').textContent, /Remote tenant/);
    await act(async () => { const select = find('#add-user-tenant'); select.value = tenantId; select.dispatchEvent(new dom.window.Event('change', { bubbles: true })); });
    await submit();
    const mutation = state.requests.find((r) => r.method === 'POST' && r.path.endsWith('/admin/users'));
    assert.equal(mutation.body.tenant_id, tenantId);
    assert.match(text(), /new@example.invalid created/);
});


test('tenant selection can reach same-named options beyond the first page', async () => {
    state.route = (r) => r.path.includes('/admin/tenants?') ? response(r.path.includes('offset=50')
        ? [{ id: 'tenant-fifty-one', business_name: 'Repeated business' }]
        : Array.from({ length: 51 }, (_, i) => ({ id: `tenant-${i}`, business_name: 'Repeated business' }))) : ordinary(r);
    await draw(); await click('Add User'); await settle(350);
    await click('Next tenant options'); await settle(350);
    const option = find('#add-user-tenant option[value="tenant-fifty-one"]');
    assert.ok(option); assert.match(option.textContent, /tenant-fifty-one/);
    assert.equal(button('Next tenant options').disabled, true);
});

test('editing only a name does not resend an old role assignment', async () => {
    state.route = (r) => r.method === 'PATCH' ? response({ ...user(), role: 'readonly', ...r.body, review_token: 'v1:' + '2'.repeat(64), operation: 'updated', operation_id: operationId, audit_event_id: '30000000-0000-4000-8000-000000000001' }) : ordinary(r);
    await draw(); await ariaClick('Edit one@example.invalid');
    await setInput(find('#edit-user-name'), 'Updated name');
    await submit();
    const mutation = state.requests.find((r) => r.method === 'PATCH');
    assert.deepEqual(mutation.body, { name: 'Updated name', expected_review_token: 'v1:' + '1'.repeat(64), operation_id: operationId });
    assert.match(text(), /account .* updated/);
});

test('a role edit includes the reviewed role and omits the unchanged name', async () => {
    state.route = (r) => r.method === 'PATCH' ? response({ ...user(), ...r.body, review_token: 'v1:' + '2'.repeat(64), operation: 'updated', operation_id: operationId, audit_event_id: '30000000-0000-4000-8000-000000000001' }) : ordinary(r);
    await draw(); await ariaClick('Edit one@example.invalid');
    await act(async () => { const select = find('#edit-user-role'); select.value = 'platform_admin'; select.dispatchEvent(new dom.window.Event('change', { bubbles: true })); });
    await submit();
    const mutation = state.requests.find((r) => r.method === 'PATCH');
    assert.deepEqual(mutation.body, { role: 'platform_admin', expected_role: 'user', expected_review_token: 'v1:' + '1'.repeat(64), operation_id: operationId });
});

test('an unchanged edit does not write or claim a saved change', async () => {
    state.route = (r) => r.method === 'PATCH' ? response(user()) : ordinary(r);
    await draw(); await ariaClick('Edit one@example.invalid');
    assert.equal(button('Save changes').disabled, true);
    await submit();
    assert.equal(state.requests.filter((r) => r.method === 'PATCH').length, 0);
    assert.doesNotMatch(text(), /one@example.invalid updated/);
});

test('temporary password input uses the canonical backend maximum', async () => {
    await draw(); await click('Add User');
    assert.equal(find('#add-user-password').maxLength, 128);
});

test('a successful null user directory is unavailable while a true empty array remains valid', async () => {
    let payload = null;
    state.route = (r) => r.path.startsWith('/api/v1/admin/users') && r.method === 'GET' ? response(payload) : ordinary(r);
    await draw();
    assert.match(text(), /User list unavailable/);
    assert.doesNotMatch(text(), /No users found/);
    payload = [];
    await click('Refresh'); await settle();
    assert.match(text(), /No users found/);
    assert.doesNotMatch(text(), /User list unavailable/);
});

test('missing active state cannot become an active user or expose a mutation control', async () => {
    const incomplete = user(); delete incomplete.is_active;
    let payload = [incomplete];
    state.route = (r) => r.path.startsWith('/api/v1/admin/users') && r.method === 'GET' ? response(payload) : ordinary(r);
    await draw();
    assert.match(text(), /User list unavailable/);
    assert.equal(find('[aria-label="Block one@example.invalid"]'), null);
    assert.equal(find('[aria-label="Unblock one@example.invalid"]'), null);
    assert.equal(state.requests.filter((r) => r.method !== 'GET').length, 0);
    payload = [{ ...user(), role: 'future_role', effective_role: 'future_role' }];
    await click('Refresh'); await settle();
    assert.doesNotMatch(text(), /User list unavailable/);
    assert.ok(find('[aria-label="Block one@example.invalid"]'), 'an explicit boolean and nonempty role remain valid without a hardcoded role catalog');
});
