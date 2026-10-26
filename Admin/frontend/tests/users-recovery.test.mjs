import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { C, api, el, act, dom, state, render, response, find, click, settle, setInput, text, button, deferred } from './bh-harness.mjs';
import { prepared, terminal, operationId, auditId, userId, tenantId, reviewToken, nextToken, membershipToken } from './users-operation-fixture.mjs';

const target = { id: userId, name: 'Reviewed user', email: 'reviewed@example.com', role: 'user', effective_role: 'user', membership_status: 'missing',
    is_active: true, is_verified: true, tenant_id: tenantId, review_token: reviewToken };
const tenantB = '20000000-0000-4000-8000-000000000002';
let actor, currentUser, operations, intercept, serial;
const remember = (view, owner = actor) => { operations.set(view.operation_id, { owner, view }); return view; };
const operationList = (kind) => [...operations.values()].filter(item => item.owner === actor && (kind === 'pending' ? item.view.state === 'prepared' : item.view.state !== 'prepared')).map(item => item.view);
const saved = (changes = {}) => ({ ...currentUser, operation_id: operationId, ...changes, review_token: nextToken, operation: 'updated', audit_event_id: auditId });
const membershipReceipt = (intent) => ({ operation: 'primary_membership_configured', operation_id: operationId,
    user: { ...currentUser, tenant_id: intent.tenant_id, role: intent.role, review_token: nextToken },
    tenant_id: intent.tenant_id, membership: { id: '40000000-0000-4000-8000-000000000001', role: intent.role, status: 'active', is_primary: true },
    audit_event_id: auditId, credential_cutoff: { revoked_sessions: 1, revoked_refresh_tokens: 1, invalidated_mfa_challenges: 1, invalidated_passkey_challenges: 0 } });
const membershipReview = (tenant = tenantId, extra = {}) => ({ user: currentUser, tenant: { id: tenant, name: 'Fixture tenant' }, membership: null,
    eligible: true, reason: null, review_token: membershipToken, ...extra });
const mutation = (r) => r.method === 'PATCH' || r.method === 'DELETE' || r.method === 'POST' && (r.path.endsWith('/admin/users') || r.path.endsWith('/primary-membership'));
const defaultRoute = (r) => {
    if (r.path.endsWith('/auth/me')) return response({ id: actor, email: actor + '@example.com', name: actor, role: 'platform_admin', tenant_id: null });
    if (r.path.endsWith('/rbac/roles')) return response(['platform_admin', 'tenant_admin', 'user', 'readonly'].map(name => ({ id: name, name, level: 1 })));
    if (r.path.endsWith('/rbac/permissions')) return response([]);
    if (r.path.startsWith('/api/v1/admin/tenants')) return response([{ id: tenantId, business_name: 'Fixture tenant' }, { id: tenantB, business_name: 'Second tenant' }]);
    if (r.path.startsWith('/api/v1/admin/users/operations?')) return response({ items: operationList(new URL(r.path, 'https://fixture.invalid').searchParams.get('state')), next_cursor: null });
    if (r.path.endsWith('/admin/users/operations') && r.method === 'POST') {
        if (operationList('pending').length) return response({ detail: { code: 'user_operation_pending', message: 'An unresolved operation already exists' } }, 409);
        const view = remember(prepared(r.body, '50000000-0000-4000-8000-' + String(++serial).padStart(12, '0')));
        return response(view, 201);
    }
    if (r.path.includes('/admin/users/operations/')) {
        const id = r.path.split('/operations/')[1].split('/')[0], entry = operations.get(id);
        if (!entry || entry.owner !== actor) return response({ detail: 'Operation not found' }, 404);
        if (r.path.endsWith('/cancel')) return response(remember(entry.view.state === 'prepared' ? terminal(entry.view) : entry.view));
        return response(entry.view);
    }
    if (r.path.includes('/primary-membership?')) return response(membershipReview(new URL(r.path, 'https://fixture.invalid').searchParams.get('tenant_id')));
    if (mutation(r)) {
        const view = operations.get(r.body.operation_id)?.view;
        assert.ok(view, 'actual mutation must refer to a previously prepared server operation');
        let receipt;
        if (view.kind === 'create') {
            assert.equal(r.body.request_id, view.operation_id);
            const { password, operation_id, request_id, ...intent } = r.body;
            assert.equal(password, 'synthetic-password'); assert.deepEqual(intent, view.intent);
            receipt = { request_id, state: 'created', audit_event_id: auditId, user_id: userId, intent: view.intent,
                user: { ...target, ...view.intent } };
        } else if (view.kind === 'update') receipt = saved(Object.fromEntries(Object.entries(r.body).filter(([key]) => ['name', 'role', 'is_active'].includes(key))));
        else if (view.kind === 'delete') receipt = { id: userId, operation_id: view.operation_id, outcome: 'deleted', audit_event_id: auditId, user: null, detail: 'Deleted exact user' };
        else receipt = membershipReceipt(view.intent);
        if (view.kind !== 'create') receipt.operation_id = view.operation_id;
        remember(terminal(view, receipt));
        return response(receipt);
    }
    if (r.path.startsWith('/api/v1/admin/users') && r.method === 'GET') return response([currentUser]);
    return response({ detail: 'Unexpected synthetic request' }, 500);
};
const draw = async () => { await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.UsersPage)))); await settle(350); };
const reloadBundle = await build({ stdin: { contents: "export { UsersPage } from './src/pages/UsersPage'; export { AuthProvider } from './src/lib/auth'; export { MemoryRouter } from 'react-router-dom';",
    resolveDir: fileURLToPath(new URL('..', import.meta.url)), loader: 'ts' }, bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
    define: { 'import.meta.env': '{}' }, jsx: 'automatic', loader: { '.css': 'empty' } });
const reloadModules = async () => {
    await render(null);
    const loaded = { exports: {} };
    new Function('require', 'module', 'exports', reloadBundle.outputFiles[0].text)(createRequire(import.meta.url), loaded, loaded.exports);
    const fresh = loaded.exports;
    await render(el(fresh.AuthProvider, null, el(fresh.MemoryRouter, null, el(fresh.UsersPage)))); await settle(350);
};
const ariaClick = async name => { const node = find('[aria-label="' + name + '"]'); assert.ok(node, name); await act(async () => node.click()); };
const submit = async () => { await act(async () => find('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))); };
const setSelect = async (selector, value) => { await act(async () => { const node = find(selector); node.value = value; node.dispatchEvent(new dom.window.Event('change', { bubbles: true })); }); };
const edit = async () => { await ariaClick('Edit ' + target.email); await setInput(find('#edit-user-name'), 'Changed'); await submit(); };
const fillCreate = async () => {
    await click('Add User'); await settle(350); await setInput(find('#add-user-name'), 'New user'); await setInput(find('#add-user-email'), 'new@example.com');
    await setInput(find('#add-user-password'), 'synthetic-password'); await setSelect('#add-user-role', 'platform_admin');
};
const perform = async kind => {
    if (kind === 'create') { await fillCreate(); await submit(); }
    else if (kind === 'edit') await edit();
    else if (kind === 'block') await ariaClick('Block ' + target.email);
    else { await ariaClick('Delete ' + target.email); await click('Delete'); }
};
const writeRequests = () => state.requests.filter(mutation);
test.beforeEach(() => {
    dom.window.sessionStorage.clear(); actor = 'admin'; currentUser = { ...target }; operations = new Map(); serial = 0; intercept = () => undefined;
    state.route = r => intercept(r) ?? defaultRoute(r);
});

for (const kind of ['create', 'edit', 'block', 'delete']) {
    test(kind + ' prepares once, sends the server operation ID, and recovers its original lost-response receipt without replay', async () => {
        intercept = r => { if (mutation(r)) { defaultRoute(r); return response({ detail: 'Reply lost after synthetic commit' }, 503); } };
        await draw(); await perform(kind);
        assert.equal(state.requests.filter(r => r.path.endsWith('/users/operations') && r.method === 'POST').length, 1);
        assert.equal(writeRequests().length, 1); assert.equal(writeRequests()[0].body.operation_id, operationId);
        assert.match(text(), /not confirmed/);
        await click('Discover server operations'); await settle();
        assert.match(text(), new RegExp(auditId)); assert.doesNotMatch(text(), /action for .* is not confirmed/);
        assert.equal(writeRequests().length, 1);
        if (kind === 'create') assert.equal(writeRequests()[0].body.request_id, operationId);
    });
}

test('prepare ack loss never dispatches a mutation and server discovery recovers its pending record', async () => {
    intercept = r => { if (r.path.endsWith('/users/operations') && r.method === 'POST') { defaultRoute(r); return response({ detail: 'Prepare reply lost' }, 503); } };
    await draw(); await edit(); assert.equal(writeRequests().length, 0);
    await click('Discover server operations'); assert.match(text(), /Unresolved update/);
    assert.equal(button('Save changes').disabled, true);
    await click('Cancel unresolved operation'); assert.match(text(), /was cancelled/);
    assert.equal(writeRequests().length, 0); assert.equal(button('Add User').disabled, false);
});

for (const status of [403, 409, 422]) {
    test('mutation rejection ' + status + ' leaves preparation unresolved until exact cancellation', async () => {
        intercept = r => mutation(r) ? response({ detail: 'Write rejected' }, status) : undefined;
        await draw(); await edit();
        assert.equal(button('Save changes').disabled, true);
        await click('Discover server operations'); assert.match(text(), /Unresolved update/);
        await click('Cancel unresolved operation');
        assert.match(text(), /No account change is claimed/);
        assert.equal(writeRequests().length, 1);
    });
}

for (const stage of ['unmount', 'identity']) {
    test('late preparation after ' + stage + ' cannot dispatch its business mutation', async () => {
        const waiting = deferred(); let request;
        intercept = r => { if (r.path.endsWith('/users/operations') && r.method === 'POST') { request = r.body; return waiting.promise; } };
        await draw(); await edit(); assert.equal(writeRequests().length, 0);
        if (stage === 'unmount') await render(null);
        else { actor = 'replacement'; await act(async () => api.setToken('replacement-token')); }
        const view = remember(prepared(request), 'admin');
        await act(async () => waiting.resolve(response(view, 201)));
        assert.equal(writeRequests().length, 0);
        if (stage === 'unmount') { await draw(); assert.match(text(), /Unresolved update/); }
        else assert.doesNotMatch(text(), /was cancelled|Audit:/);
    });
}

test('closing deletion recovery before preparation finishes retires that delete owner', async () => {
    const wait = deferred(); let draft;
    intercept = r => { if (r.path.endsWith('/users/operations') && r.method === 'POST') { draft = r.body; return wait.promise; } };
    await draw(); await ariaClick('Delete ' + target.email); await click('Delete');
    await ariaClick('Close user recovery');
    await act(async () => wait.resolve(response(remember(prepared(draft)), 201)));
    assert.equal(writeRequests().length, 0);
    assert.match(text(), /prepared action was not dispatched/);
});

test('a newer directory read retires a block review still waiting for preparation', async () => {
    const wait = deferred(); let draft;
    intercept = r => { if (r.path.endsWith('/users/operations') && r.method === 'POST') { draft = r.body; return wait.promise; } };
    await draw(); await ariaClick('Block ' + target.email); await click('Refresh'); await settle();
    await act(async () => wait.resolve(response(remember(prepared(draft)), 201)));
    assert.equal(writeRequests().length, 0); assert.match(text(), /prepared action was not dispatched/);
});

test('rapid retained form submissions cannot prepare two operations', async () => {
    const wait = deferred(); intercept = r => r.path.endsWith('/users/operations') && r.method === 'POST' ? wait.promise : undefined;
    await draw(); await ariaClick('Edit ' + target.email); await setInput(find('#edit-user-name'), 'Changed');
    await submit(); await submit();
    const prepares = state.requests.filter(r => r.path.endsWith('/users/operations') && r.method === 'POST');
    assert.equal(prepares.length, 1);
    await act(async () => wait.resolve(response(remember(prepared(prepares[0].body)), 201)));
    assert.equal(writeRequests().length, 1);
});

test('initial discovery failure visibly fences writes and can be retried', async () => {
    let fail = true;
    intercept = r => r.path.includes('/users/operations?') && fail ? response({ detail: 'Recovery database unavailable' }, 503) : undefined;
    await draw(); assert.equal(button('Add User').disabled, true); assert.match(text(), /Recovery database unavailable/);
    fail = false; await click('Discover server operations'); assert.equal(button('Add User').disabled, false);
    assert.equal(writeRequests().length, 0);
});

for (const invalid of [null, [], { items: [], next_cursor: '' }, { items: [], next_cursor: {} }, { items: [{ state: 'committed' }], next_cursor: null }]) {
    test('malformed discovery does not become no pending work: ' + JSON.stringify(invalid), async () => {
        intercept = r => r.path.includes('/users/operations?') ? response(invalid) : undefined;
        await draw(); assert.equal(button('Add User').disabled, true); assert.match(text(), /invalid pending-operation evidence/);
        assert.equal(writeRequests().length, 0);
    });
}

test('pending discovery follows the opaque cursor and retains its fence if the next page fails', async () => {
    let fail = true;
    const view = prepared({ kind: 'delete', target_user_id: userId, intent: { expected_review_token: reviewToken } });
    intercept = r => {
        if (!r.path.includes('/users/operations?')) return;
        if (!r.path.includes('cursor=')) return response({ items: [], next_cursor: 'exact opaque + cursor' });
        assert.equal(new URL(r.path, 'https://fixture.invalid').searchParams.get('cursor'), 'exact opaque + cursor');
        return fail ? response({ detail: 'Next page unavailable' }, 503) : response({ items: [view], next_cursor: null });
    };
    await draw(); assert.equal(button('Add User').disabled, true); assert.match(text(), /Next page unavailable/);
    fail = false; await click('Discover server operations'); assert.match(text(), /Unresolved delete/);
    assert.equal(button('Add User').disabled, true);
});

test('repeated pending cursor is incomplete, never a successful empty inventory', async () => {
    intercept = r => r.path.includes('/users/operations?') ? response({ items: [], next_cursor: 'same' }) : undefined;
    await draw(); assert.equal(button('Add User').disabled, true); assert.match(text(), /pagination did not advance/);
});

for (const kind of ['update', 'delete', 'create']) {
    test('full module reload with all session storage cleared discovers pending ' + kind + ' without replay', async () => {
        const intent = kind === 'create' ? { name: 'New', email: 'new@example.com', role: 'platform_admin', tenant_id: null }
            : kind === 'delete' ? { expected_review_token: reviewToken } : { expected_review_token: reviewToken, name: 'Changed' };
        remember(prepared({ kind, target_user_id: kind === 'create' ? null : userId, intent }));
        dom.window.sessionStorage.clear(); await reloadModules();
        assert.match(text(), new RegExp('Unresolved ' + kind)); assert.equal(button('Add User').disabled, true); assert.equal(writeRequests().length, 0);
    });
}

test('unavailable browser storage cannot hide pending server work or prevent its exact cancellation', async () => {
    remember(prepared({ kind: 'delete', target_user_id: userId, intent: { expected_review_token: reviewToken } }));
    const get = dom.window.Storage.prototype.getItem, set = dom.window.Storage.prototype.setItem;
    dom.window.Storage.prototype.getItem = function (key) { if (key.includes('user-')) throw new Error('Storage denied'); return get.call(this, key); };
    dom.window.Storage.prototype.setItem = function (key, value) { if (key.includes('user-')) throw new Error('Storage denied'); return set.call(this, key, value); };
    try {
        await draw(); assert.match(text(), /Unresolved delete/); await click('Cancel unresolved operation');
        assert.match(text(), /was cancelled/); assert.equal(button('Add User').disabled, false);
    } finally { dom.window.Storage.prototype.getItem = get; dom.window.Storage.prototype.setItem = set; }
});

test('expired preparation remains blocked until server cancellation; expiry is never no-effect proof', async () => {
    remember({ ...prepared({ kind: 'delete', target_user_id: userId, intent: { expected_review_token: reviewToken } }), dispatch_eligible: false });
    await draw(); assert.match(text(), /expiry is not cancellation or proof of no effect/); assert.equal(button('Add User').disabled, true);
    await click('Cancel unresolved operation'); assert.equal(button('Add User').disabled, false); assert.equal(writeRequests().length, 0);
});

test('lost cancellation reply stays fenced and an exact read recovers the terminal receipt', async () => {
    remember(prepared({ kind: 'delete', target_user_id: userId, intent: { expected_review_token: reviewToken } }));
    intercept = r => { if (r.path.endsWith('/cancel')) { defaultRoute(r); return response({ detail: 'Cancel reply lost' }, 503); } };
    await draw(); await click('Cancel unresolved operation'); assert.match(text(), /Cancel reply lost/); assert.equal(button('Add User').disabled, true);
    await click('Check exact operation'); assert.match(text(), /was cancelled/);
    assert.equal(state.requests.filter(r => r.path.endsWith('/cancel')).length, 1);
});

for (const change of [view => ({ ...view, operation_id: '50000000-0000-4000-8000-000000000099' }),
    view => ({ ...view, target_user_id: tenantId }), view => ({ ...view, intent: { ...view.intent, expected_review_token: nextToken } }),
    view => ({ ...view, state: 'committed', dispatch_eligible: false, terminal_audit_event_id: auditId, receipt: {} })]) {
    test('mismatched exact operation cannot release the unresolved fence ' + change.toString(), async () => {
        const view = remember(prepared({ kind: 'delete', target_user_id: userId, intent: { expected_review_token: reviewToken } }));
        intercept = r => r.path.endsWith('/operations/' + operationId) ? response(change(view)) : undefined;
        await draw(); await click('Check exact operation');
        assert.match(text(), /exact operation response is unavailable or inconsistent/); assert.equal(button('Add User').disabled, true);
    });
}

test('same-session unmount during cancellation retains its committed receipt for reopen', async () => {
    const view = remember(prepared({ kind: 'delete', target_user_id: userId, intent: { expected_review_token: reviewToken } }));
    const wait = deferred(); intercept = r => r.path.endsWith('/cancel') ? wait.promise : undefined;
    await draw(); await click('Cancel unresolved operation'); await render(null);
    await act(async () => wait.resolve(response(remember(terminal(view)))));
    await draw(); assert.match(text(), /was cancelled/); assert.equal(button('Add User').disabled, false);
});

test('identity replacement cannot adopt an old cancellation or expose its record to the new operator', async () => {
    const view = remember(prepared({ kind: 'delete', target_user_id: userId, intent: { expected_review_token: reviewToken } }));
    const wait = deferred(); intercept = r => r.path.endsWith('/cancel') ? wait.promise : undefined;
    await draw(); await click('Cancel unresolved operation');
    actor = 'replacement'; await act(async () => api.setToken('replacement-token'));
    await act(async () => wait.resolve(response(terminal(view))));
    await reloadModules(); assert.doesNotMatch(text(), /Unresolved delete|was cancelled|Audit:/); assert.equal(button('Add User').disabled, false);
});

test('confirmed result and audit survive a failed list reload and same-session remount', async () => {
    let committed = false;
    intercept = r => {
        if (mutation(r)) { committed = true; return defaultRoute(r); }
        if (committed && r.path.startsWith('/api/v1/admin/users?')) return response({ detail: 'Directory unavailable' }, 503);
    };
    await draw(); await edit(); assert.match(text(), new RegExp(auditId)); assert.match(text(), /Directory unavailable/);
    await render(null); await draw(); assert.match(text(), new RegExp(auditId));
});

test('cleared storage and module reload recover committed original receipt through paginated server history', async () => {
    const first = terminal(prepared({ kind: 'update', target_user_id: userId, intent: { name: 'Old saved', expected_review_token: reviewToken } }), saved({ name: 'Old saved' }));
    const second = terminal(prepared({ kind: 'delete', target_user_id: userId, intent: { expected_review_token: reviewToken } }, '50000000-0000-4000-8000-000000000002'));
    let fail = true;
    intercept = r => {
        if (!r.path.includes('state=terminal')) return;
        if (!r.path.includes('cursor=')) return response({ items: [first], next_cursor: 'history + page2' });
        return fail ? response({ detail: 'History second page unavailable' }, 503) : response({ items: [second], next_cursor: null });
    };
    dom.window.sessionStorage.clear(); await reloadModules(); await click('View operation history');
    assert.match(text(), /Original receipts for this signed-in operator/); assert.match(text(), new RegExp(auditId));
    await click('Load more operation history'); assert.match(text(), /History second page unavailable/); assert.match(text(), new RegExp(first.operation_id));
    fail = false; await click('Load more operation history'); assert.match(text(), new RegExp(second.operation_id)); assert.equal(writeRequests().length, 0);
});

test('malformed original receipt never appears as confirmed history', async () => {
    intercept = r => r.path.includes('state=terminal') ? response({ items: [{ ...terminal(prepared({ kind: 'delete', target_user_id: userId, intent: { expected_review_token: reviewToken } })),
        state: 'committed', receipt: { id: userId, outcome: 'deleted', detail: 'Deleted', user: null } }], next_cursor: null }) : undefined;
    await draw(); await click('View operation history'); assert.match(text(), /history is incomplete or invalid/); assert.doesNotMatch(text(), /permanently deleted/);
});

test('retained history pagination cannot append an old page after refreshing history', async () => {
    const view = terminal(prepared({ kind: 'delete', target_user_id: userId, intent: { expected_review_token: reviewToken } }));
    intercept = r => r.path.includes('state=terminal') ? response({ items: [view], next_cursor: 'old-page' }) : undefined;
    await draw(); await click('View operation history');
    const more = button('Load more operation history'), props = Object.keys(more).find(key => key.startsWith('__reactProps')), old = more[props].onClick;
    intercept = r => r.path.includes('state=terminal') ? response({ items: [], next_cursor: null }) : undefined;
    await click('View operation history');
    const before = state.requests.length; await act(async () => old());
    assert.equal(state.requests.length, before); assert.match(text(), /No terminal operations were returned/);
});

test('a retained old edit submit after unmount cannot prepare or mutate', async () => {
    await draw(); await ariaClick('Edit ' + target.email); await setInput(find('#edit-user-name'), 'Changed');
    const form = find('form'), props = Object.keys(form).find(key => key.startsWith('__reactProps')), old = form[props].onSubmit;
    await render(null); await act(async () => old({ preventDefault() {} }));
    assert.equal(state.requests.filter(r => r.method !== 'GET').length, 0);
});

test('a retained block callback after directory refresh cannot dispatch the old review', async () => {
    await draw(); const control = find('[aria-label="Block ' + target.email + '"]'), props = Object.keys(control).find(key => key.startsWith('__reactProps')), old = control[props].onClick;
    await click('Refresh'); await settle(); await act(async () => old());
    assert.equal(state.requests.filter(r => r.method !== 'GET').length, 0);
});

test('malformed preparation receipt cannot dispatch a mutation', async () => {
    intercept = r => r.path.endsWith('/users/operations') && r.method === 'POST' ? response({ ...prepared(r.body), intent: { ...r.body.intent, name: 'Not reviewed' } }, 201) : undefined;
    await draw(); await edit(); assert.equal(writeRequests().length, 0); assert.match(text(), /Preparation was not confirmed/);
});

test('wrong intended mutation fields or missing audit remains unknown until server evidence', async () => {
    intercept = r => mutation(r) ? response(saved({ name: 'Wrong saved name', audit_event_id: null })) : undefined;
    await draw(); await edit(); assert.match(text(), /did not confirm the exact operation/); assert.equal(button('Save changes').disabled, true);
});

test('same-target intended update receipt from a different prepared operation cannot confirm this action', async () => {
    intercept = r => mutation(r) ? response(saved({ name: 'Changed', operation_id: '50000000-0000-4000-8000-000000000099' })) : undefined;
    await draw(); await edit();
    assert.match(text(), /did not confirm the exact operation/);
    assert.doesNotMatch(text(), /account .* updated/);
});

for (const [kind, missing] of [['edit', true], ['delete', true], ['delete', false], ['primary_membership', true], ['primary_membership', false]]) {
    test(kind + ' receipt with ' + (missing ? 'missing' : 'wrong') + ' operation ID stays unknown', async () => {
        intercept = r => {
            if (mutation(r)) return defaultRoute(r).json().then(receipt => {
                if (missing) delete receipt.operation_id;
                else receipt.operation_id = '50000000-0000-4000-8000-000000000099';
                return response(receipt);
            });
        };
        await draw();
        if (kind === 'primary_membership') { await openMembership(); await click('Review exact membership'); await confirmMembership(); await submit(); }
        else await perform(kind);
        assert.match(text(), /did not confirm the exact operation/);
        assert.doesNotMatch(text(), /Audit:/); assert.equal(writeRequests().length, 1);
    });
}

test('empty discovery after a rejected preparation does not invent a terminal receipt', async () => {
    intercept = r => r.path.endsWith('/users/operations') && r.method === 'POST' ? response({ error: { code: 'validation_error', message: 'Intent validation failed' } }, 422) : undefined;
    await draw(); await edit(); await click('Discover server operations');
    assert.match(text(), /No unresolved server operations were returned/);
    assert.doesNotMatch(text(), /has a terminal receipt|Audit:/); assert.equal(writeRequests().length, 0);
});

for (const snapshot of [null, { name: 'Wrong name' }, { role: 'readonly' }, { tenant_id: tenantId }, { is_active: false }, { is_verified: false }]) {
    test('create receipt requires the complete original reviewed snapshot: ' + JSON.stringify(snapshot), async () => {
        intercept = r => {
            if (r.path.endsWith('/admin/users') && r.method === 'POST') {
                const view = operations.get(r.body.operation_id).view;
                return response({ request_id: view.operation_id, state: 'created', audit_event_id: auditId, user_id: userId, intent: view.intent,
                    user: snapshot === null ? null : { ...target, ...view.intent, ...snapshot } });
            }
        };
        await draw(); await fillCreate(); await submit();
        assert.match(text(), /did not confirm the exact operation/); assert.equal(button('Create user').disabled, true);
        assert.doesNotMatch(text(), /original saved receipt/);
    });
}

for (const terminalAvailable of [true, false]) {
    test('commit between exact prepared read and pending-list snapshot requires terminal re-read: ' + terminalAvailable, async () => {
        let exactReads = 0, startedRecovery = false;
        intercept = r => {
            if (mutation(r)) return response({ detail: 'Original response unavailable' }, 503);
            if (r.path.endsWith('/operations/' + operationId)) {
                exactReads++;
                const view = operations.get(operationId).view;
                if (exactReads === 1) { startedRecovery = true; return response(view); }
                return terminalAvailable ? response(remember(terminal(view, saved({ name: 'Changed' })))) : response({ detail: 'Terminal read unavailable' }, 503);
            }
            if (startedRecovery && r.path.includes('/users/operations?')) return response({ items: [], next_cursor: null });
        };
        await draw(); await edit(); await click('Discover server operations');
        assert.equal(exactReads, 2); assert.equal(writeRequests().length, 1);
        if (terminalAvailable) { assert.match(text(), new RegExp(auditId)); assert.equal(button('Add User').disabled, false); }
        else { assert.match(text(), /Terminal read unavailable/); assert.equal(button('Save changes').disabled, true); assert.doesNotMatch(text(), /account .* updated/); }
    });
}

const openMembership = async () => { await ariaClick('Set primary membership for ' + target.email); };
const confirmMembership = async () => { const checkbox = find('input[type="checkbox"]'); assert.ok(checkbox); await act(async () => checkbox.click()); };
test('missing membership at existing primary uses exact read and prepares the bounded repair with fresh-login confirmation', async () => {
    await draw(); await openMembership();
    assert.equal(find('[aria-label="Primary membership tenant"]'), null);
    assert.equal(button('Set primary membership').disabled, true);
    await click('Review exact membership'); assert.match(text(), /Fresh sign-in is required/);
    await confirmMembership(); await submit();
    const prep = state.requests.find(r => r.path.endsWith('/users/operations') && r.method === 'POST');
    assert.deepEqual(prep.body, { kind: 'primary_membership', target_user_id: userId, intent: { tenant_id: tenantId, role: 'user', expected_review_token: membershipToken } });
    assert.equal(writeRequests()[0].body.operation_id, operationId);
    assert.match(text(), /Primary membership configured/); assert.match(text(), new RegExp(auditId));
});

test('NULL-primary attachment uses the existing tenant selector and requires a new review after selection changes', async () => {
    currentUser = { ...target, tenant_id: null };
    await draw(); await openMembership(); await settle(350);
    await setSelect('#primary-tenant', tenantId); await click('Review exact membership'); await confirmMembership();
    assert.equal(button('Set primary membership').disabled, false);
    await setSelect('#primary-tenant', tenantB); assert.equal(button('Set primary membership').disabled, true);
    assert.equal(find('input[type="checkbox"]'), null);
    await click('Review exact membership'); await confirmMembership(); await submit();
    assert.equal(writeRequests()[0].body.tenant_id, tenantB);
});

test('role changes invalidate membership confirmation and require a fresh candidate review', async () => {
    await draw(); await openMembership(); await click('Review exact membership'); await confirmMembership();
    await setSelect('#primary-role', 'readonly');
    assert.equal(button('Set primary membership').disabled, true); assert.equal(find('input[type="checkbox"]'), null);
    await click('Review exact membership'); await confirmMembership(); await submit();
    assert.equal(writeRequests()[0].body.role, 'readonly');
});

test('a retained membership submit cannot resurrect confirmation after the role changes', async () => {
    await draw(); await openMembership(); await click('Review exact membership'); await confirmMembership();
    const form = find('form'), props = Object.keys(form).find(key => key.startsWith('__reactProps')), old = form[props].onSubmit;
    await setSelect('#primary-role', 'readonly');
    await act(async () => old({ preventDefault() {} }));
    assert.equal(state.requests.filter(r => r.method !== 'GET').length, 0);
});

test('withdrawing membership confirmation invalidates a retained confirmed submit', async () => {
    await draw(); await openMembership(); await click('Review exact membership'); await confirmMembership();
    const form = find('form'), props = Object.keys(form).find(key => key.startsWith('__reactProps')), old = form[props].onSubmit;
    await confirmMembership(); await act(async () => old({ preventDefault() {} }));
    assert.equal(state.requests.filter(r => r.method !== 'GET').length, 0);
});

test('a retained old membership read cannot replace the newly selected tenant review', async () => {
    currentUser = { ...target, tenant_id: null };
    await draw(); await openMembership(); await settle(350); await setSelect('#primary-tenant', tenantId);
    const control = button('Review exact membership'), props = Object.keys(control).find(key => key.startsWith('__reactProps')), old = control[props].onClick;
    await setSelect('#primary-tenant', tenantB);
    const before = state.requests.length; await act(async () => old());
    assert.equal(state.requests.length, before); assert.equal(find('input[type="checkbox"]'), null);
});

for (const extra of [
    { eligible: false, reason: 'The candidate membership is suspended.', membership: { id: userId, role: 'user', status: 'suspended', is_primary: false } },
    { eligible: false, reason: 'A different primary membership already exists.' },
    { eligible: false, reason: 'This destination has an active formal hold.' },
]) {
    test('ineligible membership review is visible and cannot dispatch: ' + extra.reason, async () => {
        intercept = r => r.path.includes('/primary-membership?') ? response(membershipReview(tenantId, extra)) : undefined;
        await draw(); await openMembership(); await click('Review exact membership');
        assert.match(text(), new RegExp(extra.reason.replaceAll('.', '\\.'))); assert.equal(button('Set primary membership').disabled, true);
        await submit(); assert.equal(state.requests.filter(r => r.method !== 'GET').length, 0);
    });
}

test('NULL-primary can reuse only the exact reviewed active candidate membership', async () => {
    currentUser = { ...target, tenant_id: null };
    intercept = r => r.path.includes('/primary-membership?') ? response(membershipReview(tenantId, { membership: { id: userId, role: 'readonly', status: 'active', is_primary: false } })) : undefined;
    await draw(); await openMembership(); await settle(350); await setSelect('#primary-tenant', tenantId); await click('Review exact membership');
    assert.match(text(), /Existing membership:/); await confirmMembership(); await submit(); assert.equal(writeRequests().length, 1);
});

test('stale candidate read after changing selection cannot enable membership submit', async () => {
    currentUser = { ...target, tenant_id: null }; const wait = deferred();
    intercept = r => r.path.includes('/primary-membership?') ? wait.promise : undefined;
    await draw(); await openMembership(); await settle(350); await setSelect('#primary-tenant', tenantId); await click('Review exact membership');
    await setSelect('#primary-tenant', tenantB); await act(async () => wait.resolve(response(membershipReview(tenantId))));
    assert.equal(find('input[type="checkbox"]'), null); assert.equal(button('Set primary membership').disabled, true);
});

test('malformed or mismatched membership review cannot become actionable', async () => {
    intercept = r => r.path.includes('/primary-membership?') ? response(membershipReview(tenantB)) : undefined;
    await draw(); await openMembership(); await click('Review exact membership');
    assert.match(text(), /exact membership review is unavailable or invalid/); assert.equal(button('Set primary membership').disabled, true);
});

test('membership conflict leaves the prepared operation recoverable and never retries a changed association', async () => {
    intercept = r => mutation(r) ? response({ detail: { code: 'primary_membership_changed', message: 'Membership changed after review' } }, 409) : undefined;
    await draw(); await openMembership(); await click('Review exact membership'); await confirmMembership(); await submit();
    assert.match(text(), /Membership changed after review/); await click('Discover server operations'); assert.match(text(), /Unresolved primary membership/);
    assert.equal(writeRequests().length, 1); await click('Cancel unresolved operation'); assert.equal(button('Add User').disabled, false);
});

test('primary membership dialog contains keyboard focus and restores its opener', async () => {
    await draw(); const opener = find('[aria-label="Set primary membership for ' + target.email + '"]'); opener.focus(); await openMembership();
    assert.ok(find('.main-content').hasAttribute('inert'));
    const close = find('[aria-label="Close primary membership"]'); close.focus();
    document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }));
    assert.equal(document.activeElement.textContent, 'Cancel');
    await act(async () => document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    assert.equal(find('[role="dialog"]'), null); assert.equal(document.activeElement, opener);
});
