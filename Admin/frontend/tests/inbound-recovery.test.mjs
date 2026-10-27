// Actual ReactDOM and API serialization; synthetic fetch with sockets forbidden.
import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { el, act, dom, state, render, response, text, find, button, click, settle, deferred } from './bh-harness.mjs';

const built = await build({ stdin: { contents: `export { InboundControlPage } from './src/pages/InboundControlPage';
    export { AuthProvider } from './src/lib/auth'; export { api } from './src/lib/api'; export { MemoryRouter } from 'react-router-dom';`,
    resolveDir: fileURLToPath(new URL('..', import.meta.url)), loader: 'ts' }, bundle: true, write: false, platform: 'node',
    format: 'cjs', packages: 'external', define: { 'import.meta.env': '{}' }, jsx: 'automatic', loader: { '.css': 'empty' } });
const loaded = { exports: {} };
new Function('require', 'module', 'exports', built.outputFiles[0].text)(createRequire(import.meta.url), loaded, loaded.exports);
const O = loaded.exports;
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const actor = id(1), other = id(2), reason = 'Independently reviewed original ownership';
const pending = { id: id(3), assignment_id: id(4), source_tenant_id: id(5), target_tenant_id: id(6), target_campaign_id: id(7),
    status: 'pending', requested_by: other, reason: 'Original customer request', requested_at: '2026-10-21T00:00:00Z',
    approved_assignment_id: null, approved_by: null, decision_reason: null, decided_at: null };
const approved = { ...pending, status: 'approved', approved_assignment_id: id(8), approved_by: actor,
    decision_reason: reason, decided_at: '2026-10-21T01:00:00Z' };
const cancellationReason = 'Target setup changed; retire this request';
const cancelled = { ...pending, status: 'cancelled', decision_reason: cancellationReason, decided_at: '2026-10-21T01:00:00Z' };
const second = { ...pending, id: id(9), assignment_id: id(10), target_tenant_id: id(11), reason: 'Second customer request' };
const pageOf = items => ({ items, has_more: false, next_cursor: null });
let stored, rows, custom, detailFailure, historyFailure, inventoryFailure;
const posts = () => state.requests.filter(item => item.method === 'POST' && item.path.endsWith('/approve'));
const cancellations = () => state.requests.filter(item => item.method === 'POST' && item.path.endsWith('/cancel'));
const panel = () => find('[aria-labelledby="reassignment-review-heading"]');
const props = node => node[Object.keys(node).find(key => key.startsWith('__reactProps'))];
test.beforeEach(() => {
    O.api.setToken('synthetic-inbound-recovery'); stored = pending; rows = null; custom = null;
    detailFailure = historyFailure = inventoryFailure = false;
    state.route = request => {
        const override = custom?.(request); if (override !== undefined) return override;
        const url = new URL(request.path, 'https://synthetic.invalid');
        if (url.pathname.endsWith('/auth/me')) return response({ id: actor, email: 'operator@example.invalid', role: 'platform_admin' });
        if (url.pathname.endsWith('/inbound/overview')) return inventoryFailure ? response({ detail: 'Inventory unavailable' }, 503)
            : response({ active_assignments: 0, paused_assignments: 1, quarantined_assignments: 0, denied_last_24h: 0,
                controls: { version: 1, inbound_enabled: false, recording_enabled: false, transfer_enabled: false, settlement_enabled: false } });
        if (url.pathname.endsWith('/inbound/assignments')) return response({ items: [], total: 0 });
        if (url.pathname.endsWith('/inbound/reassignments')) return historyFailure ? response({ detail: 'History unavailable' }, 503) : response(pageOf(rows ?? [stored]));
        if (url.pathname.endsWith('/approve')) { stored = { ...approved, decision_reason: request.body.reason }; return response(stored); }
        if (url.pathname.endsWith('/cancel')) { stored = { ...stored, status: 'cancelled', approved_by: null, approved_assignment_id: null,
            decision_reason: request.body.reason, decided_at: cancelled.decided_at }; return response(stored); }
        if (url.pathname.includes('/inbound/reassignments/')) return detailFailure ? response({ detail: 'Exact decision unavailable' }, 503)
            : response((rows ?? [stored]).find(row => row.id === url.pathname.split('/').at(-1)) ?? stored);
        if (url.pathname.endsWith('/admin/tenants')) return response([]);
        if (url.pathname.includes('/admin/tenants/')) return response({ id: url.pathname.split('/').at(-1), business_name: 'Exact tenant name' });
        return response({ detail: 'Unrouted fixture request' }, 500);
    };
});
test.afterEach(() => O.api.setToken(null));
async function mount() { await render(el(O.AuthProvider, null, el(O.MemoryRouter, null, el(O.InboundControlPage)))); await settle(); }
async function enterReason(value = reason) {
    await act(async () => {
        const area = find('#reassignment-review-reason'); assert.ok(area);
        Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(area, value);
        area.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    });
}
async function review() { await click('Review reassignment'); await settle(); await enterReason(); }
function loseApproval() {
    custom = request => { if (request.path.endsWith('/approve')) { stored = approved; throw new Error('Synthetic delivery lost'); } };
}

async function cancelReason(value = cancellationReason) {
    await act(async () => {
        const area = find('#reassignment-cancellation-reason'); assert.ok(area);
        Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(area, value);
        area.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    });
}

const keyboardAssignment = { id: id(41), tenant_id: id(5), tenant_name: 'Source tenant', campaign_id: id(42),
    campaign_name: 'Paused source', masked_did: '***0041', status: 'quarantined', version: 4,
    readiness: { ready: false, blockers: [] } };
const inlineCases = [
    { name: 'assignment confirmation', trigger: 'Unquarantine', section: 'assignment-action-heading', entry: '#assignment-action-reason', close: 'Cancel' },
    { name: 'reassignment form', trigger: 'Reassign', section: 'reassign-heading', entry: '[aria-label="Search target tenant options"]', close: 'Cancel' },
    { name: 'selected review', trigger: 'Review reassignment', section: 'reassignment-review-heading', entry: '#reassignment-review-heading', close: 'Cancel review' },
];
async function mountInlineCase() {
    custom = request => new URL(request.path, 'https://synthetic.invalid').pathname.endsWith('/inbound/assignments')
        ? response({ items: [keyboardAssignment], total: 1 }) : undefined;
    await mount();
}
function inlineClose(spec) {
    return [...find(`[aria-labelledby="${spec.section}"]`).querySelectorAll('button')].find(node => node.textContent.trim() === spec.close);
}
for (const spec of inlineCases) {
    test(`inline keyboard baseline: ${spec.name} enters its first meaningful field`, async () => {
        await mountInlineCase(); button(spec.trigger).focus(); await click(spec.trigger); await settle();
        assert.equal(document.activeElement === find(spec.entry), true, 'Opening the inline form must move focus into it');
        assert.equal(state.requests.filter(request => !['GET', 'HEAD'].includes(request.method)).length, 0);
    });
    test(`inline keyboard baseline: ${spec.name} Cancel returns to its exact origin`, async () => {
        await mountInlineCase(); const origin = button(spec.trigger); origin.focus(); await click(spec.trigger); await settle();
        const close = inlineClose(spec); assert.ok(close); close.focus(); await act(async () => close.click()); await settle();
        assert.equal(document.activeElement === origin, true, 'Cancelling the inline form must restore its original action');
        assert.equal(find(`[aria-labelledby="${spec.section}"]`), null);
        assert.equal(state.requests.filter(request => !['GET', 'HEAD'].includes(request.method)).length, 0);
    });
    test(`inline keyboard recovery: ${spec.name} returns to a visible filter if refresh removed its origin`, async () => {
        await mountInlineCase(); const origin = button(spec.trigger); origin.focus(); await click(spec.trigger); await settle();
        rows = [];
        custom = request => new URL(request.path, 'https://synthetic.invalid').pathname.endsWith('/inbound/assignments')
            ? response({ items: [], total: 0 }) : undefined;
        await click('Refresh'); await settle();
        assert.equal(origin.isConnected, false);
        const close = inlineClose(spec); close.focus(); await act(async () => close.click()); await settle();
        const fallback = find(`[aria-label="${spec.name === 'selected review' ? 'Filter reassignment requests by status' : 'Filter inbound assignments by status'}"]`);
        assert.equal(document.activeElement === fallback, true, 'A removed origin must have a stable visible return target');
        assert.equal(state.requests.filter(request => !['GET', 'HEAD'].includes(request.method)).length, 0);
    });
}

test('inline keyboard recovery: late exact read does not take focus from the operator', async () => {
    const held = deferred(); custom = request => request.path.endsWith(`/reassignments/${pending.id}`) ? held.promise : undefined;
    await mount(); button('Review reassignment').focus(); await click('Review reassignment');
    assert.equal(document.activeElement === find('#reassignment-review-heading'), true);
    const close = button('Cancel review'); close.focus();
    await act(async () => held.resolve(response(pending))); await settle();
    assert.equal(document.activeElement === close, true, 'Completing the selected read must not steal focus');
});

test('inline keyboard recovery: a retired session cannot restore an old form or its focus', async () => {
    await mountInlineCase(); button('Unquarantine').focus(); await click('Unquarantine');
    const retainedClose = props(inlineClose(inlineCases[0])).onClick;
    await act(async () => O.api.setToken('synthetic-replacement-inbound-owner')); await settle();
    const fallback = find('[aria-label="Filter inbound assignments by status"]'); fallback.focus();
    await act(async () => retainedClose()); await settle();
    assert.equal(document.activeElement === fallback, true);
    assert.equal(find('[aria-labelledby="assignment-action-heading"]'), null);
    assert.equal(state.requests.filter(request => !['GET', 'HEAD'].includes(request.method)).length, 0);
});

test('cancellation baseline: requester can review and cancel without approving their own request', async () => {
    stored = { ...pending, requested_by: actor };
    await mount(); await click('Review reassignment'); await settle();
    assert.equal(button('Approve reassignment').disabled, true);
    await click('Cancel saved request'); await cancelReason(); await click('Confirm cancellation'); await settle();
    assert.equal(cancellations().length, 1); assert.equal(posts().length, 0);
    assert.match(panel().textContent, /Server-saved decision/); assert.match(panel().textContent, /Saved status: cancelled/);
    assert.doesNotMatch(panel().textContent, /Approved by|Resulting assignment ID/);
});

test('cancellation baseline: stranded request can be cancelled when tenant details are unavailable', async () => {
    custom = request => request.path.includes('/admin/tenants/') ? response({ detail: 'Tenant unavailable' }, 503) : undefined;
    await mount(); await click('Review reassignment'); await settle();
    assert.equal(button('Approve reassignment').disabled, true);
    await click('Cancel saved request'); assert.equal(cancellations().length, 0);
    assert.equal(button('Confirm cancellation').disabled, true);
    await cancelReason(); await click('Confirm cancellation'); await settle();
    assert.deepEqual(cancellations()[0].body, { reason: cancellationReason });
    assert.ok(cancellations()[0].headers['Idempotency-Key']); assert.equal(posts().length, 0);
    assert.match(panel().textContent, /does not move|unchanged/);
});

test('Cancel review and Back to review do not cancel the server request', async () => {
    await mount(); await review(); await click('Cancel saved request'); await cancelReason();
    const oldConfirm = props(button('Confirm cancellation')).onClick;
    await click('Back to review'); await act(async () => oldConfirm());
    await click('Cancel review'); await settle();
    assert.equal(panel(), null); assert.equal(cancellations().length, 0); assert.equal(posts().length, 0);
});

test('cancellation focus: opening enters the reason and Back restores the originating action', async () => {
    await mount(); await review(); button('Cancel saved request').focus();
    await click('Cancel saved request');
    assert.equal(document.activeElement === find('#reassignment-cancellation-reason'), true, 'Opening cancellation must focus its reason');
    button('Back to review').focus(); await click('Back to review');
    assert.equal(document.activeElement === button('Cancel saved request'), true, 'Back must restore the originating action'); assert.equal(cancellations().length, 0);
});

test('cancel confirmation claims once before await and preserves confirmed decision on failed followup reads', async () => {
    const held = deferred(); custom = request => request.path.endsWith('/cancel') ? held.promise : undefined;
    await mount(); await review(); await click('Cancel saved request'); await cancelReason();
    const confirm = props(button('Confirm cancellation')).onClick;
    await act(async () => { confirm(); confirm(); });
    assert.equal(cancellations().length, 1); assert.equal(posts().length, 0);
    stored = cancelled; historyFailure = inventoryFailure = detailFailure = true;
    await act(async () => held.resolve(response(cancelled))); await settle();
    await click('Check saved decision'); await settle();
    assert.match(panel().textContent, /Server-saved decision/); assert.match(panel().textContent, /Exact decision unavailable/);
    assert.match(panel().textContent, /Saved status: cancelled/); assert.equal(cancellations().length, 1);
    assert.equal([...state.host.querySelectorAll('button')].some(node => node.textContent === 'Confirm cancellation'), false);
});

test('lost cancellation recovers through failed read and cleared-storage history without mutation replay', async () => {
    custom = request => { if (request.path.endsWith('/cancel')) { stored = cancelled; throw new Error('Cancellation delivery lost'); } };
    await mount(); await review(); await click('Cancel saved request'); await cancelReason(); await click('Confirm cancellation'); await settle();
    assert.match(panel().textContent, /Cancellation delivery lost/); assert.equal(button('Retry original cancellation').disabled, true);
    detailFailure = true; await click('Check saved decision'); await settle();
    assert.match(panel().textContent, /Exact decision unavailable/); assert.equal(cancellations().length, 1);
    detailFailure = false; await click('Check saved decision'); await settle();
    assert.match(panel().textContent, /Server-saved decision/); assert.ok(panel().textContent.includes(cancellationReason));
    await render(null); localStorage.clear(); dom.window.sessionStorage.clear(); O.api.setToken('synthetic-cancelled-new-session');
    await mount(); await click('View saved decision'); await settle();
    assert.match(panel().textContent, /Saved status: cancelled/); assert.equal(cancellations().length, 1);
    assert.doesNotMatch(panel().textContent, /Approved by|Resulting assignment ID/);
});

test('pending cancellation requires explicit confirmation retry with immutable original reason and key', async () => {
    let attempts = 0;
    custom = request => { if (request.path.endsWith('/cancel') && ++attempts === 1) throw new Error('Cancellation unknown'); };
    await mount(); await review(); await click('Cancel saved request'); await cancelReason(); await click('Confirm cancellation'); await settle();
    await click('Cancel review'); await click('Review reassignment'); await settle();
    assert.equal(cancellations().length, 1); assert.equal(button('Approve reassignment').disabled, true);
    await click('Retry original cancellation');
    assert.equal(find('#reassignment-cancellation-reason').value, cancellationReason);
    assert.equal(find('#reassignment-cancellation-reason').disabled, true);
    assert.equal(document.activeElement === button('Confirm cancellation'), true, 'Frozen retry enters its explicit confirmation');
    await click('Confirm cancellation'); await settle();
    assert.equal(cancellations().length, 2); assert.deepEqual(cancellations()[0].body, cancellations()[1].body);
    assert.equal(cancellations()[0].headers['Idempotency-Key'], cancellations()[1].headers['Idempotency-Key']);
    assert.match(panel().textContent, /Server-saved decision/);
});

test('cancellation reason uses the actual trimmed3–2000 character contract', async () => {
    await mount(); await review(); await click('Cancel saved request');
    await cancelReason('  ab  '); assert.equal(button('Confirm cancellation').disabled, true);
    await cancelReason('x'.repeat(2001)); assert.equal(button('Confirm cancellation').disabled, true);
    await cancelReason('  abc  '); assert.equal(button('Confirm cancellation').disabled, false);
    await click('Confirm cancellation'); await settle();
    assert.deepEqual(cancellations()[0].body, { reason: 'abc' });
});

for (const changes of [{ decision_reason: null }, { decision_reason: '  ' }, { decided_at: null },
    { decided_at: '2026-10-20T00:00:00Z' }, { approved_by: actor }, { approved_assignment_id: id(88) }]) {
    test(`malformed cancelled history cannot become actionable ${JSON.stringify(changes)}`, async () => {
        stored = { ...cancelled, ...changes }; await mount();
        assert.match(text(), /history could not be refreshed/i); assert.equal(panel(), null);
        assert.equal([...state.host.querySelectorAll('button')].some(node => node.textContent === 'View saved decision'), false);
        assert.equal(cancellations().length, 0); assert.equal(posts().length, 0);
    });
    test(`malformed exact cancelled read is never confirmed ${JSON.stringify(changes)}`, async () => {
        await mount(); await review();
        custom = request => request.path.endsWith(`/reassignments/${pending.id}`) ? response({ ...cancelled, ...changes }) : undefined;
        await click('Check saved decision'); await settle();
        assert.match(panel().textContent, /does not match this request/); assert.doesNotMatch(panel().textContent, /Server-saved decision/);
        assert.equal(button('Cancel saved request').disabled, true); assert.equal(button('Approve reassignment').disabled, true);
        assert.equal(cancellations().length, 0); assert.equal(posts().length, 0);
    });
}

test('unknown approval can be deliberately cancelled only after exact pending reconciliation', async () => {
    custom = request => { if (request.path.endsWith('/approve')) throw new Error('Approval unknown'); };
    await mount(); await review(); await click('Approve reassignment'); await settle();
    assert.equal(button('Cancel saved request').disabled, true);
    await click('Check saved decision'); await settle();
    const oldApprove = props(button('Retry original approval')).onClick;
    await click('Cancel saved request'); await cancelReason();
    await act(async () => oldApprove()); assert.equal(posts().length, 1);
    await click('Confirm cancellation'); await settle();
    assert.equal(cancellations().length, 1); assert.equal(posts().length, 1);
    assert.match(panel().textContent, /Saved status: cancelled/);
});

for (const mismatch of [{ id: id(99) }, { decision_reason: 'Different reason' }, { status: 'pending' },
    { approved_by: actor }, { approved_assignment_id: id(77) }, { decided_at: '2026-10-20T00:00:00Z' }]) {
    test(`malformed cancellation receipt stays unknown until exact read ${JSON.stringify(mismatch)}`, async () => {
        custom = request => { if (request.path.endsWith('/cancel')) { stored = cancelled; return response({ ...cancelled, ...mismatch }); } };
        await mount(); await review(); await click('Cancel saved request'); await cancelReason(); await click('Confirm cancellation'); await settle();
        assert.match(panel().textContent, /cancellation response could not be verified/); assert.doesNotMatch(panel().textContent, /Server-saved decision/);
        await click('Check saved decision'); await settle();
        assert.match(panel().textContent, /Saved status: cancelled/); assert.equal(cancellations().length, 1);
    });
}

test('approval wins cancellation race: precise409 remains visible and exact read shows saved approval', async () => {
    const winner = { ...approved, approved_by: id(44) };
    custom = request => { if (request.path.endsWith('/cancel')) {
        stored = winner; return response({ detail: { code: 'already_decided', message: 'Reassignment is already approved' } }, 409);
    } };
    await mount(); await review(); await click('Cancel saved request'); await cancelReason(); await click('Confirm cancellation'); await settle();
    assert.match(panel().textContent, /server rejected this cancellation \(409\)/); assert.doesNotMatch(panel().textContent, /response was lost/);
    await click('Check saved decision'); await settle();
    assert.match(panel().textContent, /Saved status: approved/); assert.ok(panel().textContent.includes(id(44)));
    assert.equal(cancellations().length, 1); assert.equal(posts().length, 0);
});

test('refresh retires cancellation confirmation even after the refreshed pending row returns', async () => {
    await mount(); await review(); await click('Cancel saved request'); await cancelReason();
    const oldConfirm = props(button('Confirm cancellation')).onClick;
    await click('Refresh'); await settle(); await act(async () => oldConfirm());
    assert.equal(cancellations().length, 0);
    assert.equal([...state.host.querySelectorAll('button')].some(node => node.textContent === 'Confirm cancellation'), false);
});

test('retained cancellation callbacks cannot affect a different selected request or edited reason', async () => {
    rows = [pending, second]; await mount(); await review(); await click('Cancel saved request'); await cancelReason();
    const oldConfirm = props(button('Confirm cancellation')).onClick;
    await cancelReason('A different freshly reviewed reason'); await act(async () => oldConfirm()); assert.equal(cancellations().length, 0);
    const editedConfirm = props(button('Confirm cancellation')).onClick;
    await click('Cancel review');
    await act(async () => [...state.host.querySelectorAll('button')].filter(node => node.textContent === 'Review reassignment')[1].click()); await settle();
    await act(async () => editedConfirm()); assert.equal(cancellations().length, 0); assert.match(panel().textContent, /Second customer request/);
});

for (const retirement of ['identity', 'unmount']) test(`retired ${retirement} cannot submit a retained cancellation confirmation`, async () => {
    await mount(); await review(); await click('Cancel saved request'); await cancelReason();
    const oldConfirm = props(button('Confirm cancellation')).onClick;
    if (retirement === 'unmount') await render(null);
    else await act(async () => O.api.setToken(null));
    await act(async () => oldConfirm()); await settle();
    assert.equal(cancellations().length, 0); assert.equal(panel(), null);
});

test('late committed cancellation cannot restore private decision after identity retirement', async () => {
    const held = deferred(); custom = request => request.path.endsWith('/cancel') ? held.promise : undefined;
    await mount(); await review(); await click('Cancel saved request'); await cancelReason(); await click('Confirm cancellation');
    await act(async () => O.api.setToken(null));
    await act(async () => held.resolve(response(cancelled))); await settle();
    assert.equal(panel(), null); assert.equal(cancellations().length, 1); assert.doesNotMatch(text(), /Server-saved decision/);
});

test('lost committed approval recovers exact saved decision without another POST or pending-list membership', async () => {
    loseApproval(); await mount(); await review(); await click('Approve reassignment'); await settle();
    assert.match(panel().textContent, /Synthetic delivery lost/); assert.equal(posts().length, 1);
    assert.equal(button('Retry original approval').disabled, true);
    await click('Check saved decision'); await settle();
    assert.match(panel().textContent, /Server-saved decision/);
    for (const value of [approved.id, approved.approved_assignment_id, actor, reason]) assert.ok(panel().textContent.includes(value));
    assert.equal(posts().length, 1); assert.equal([...state.host.querySelectorAll('button')].some(node => node.textContent === 'Approve reassignment'), false);
});
test('new mount with browser storage cleared discovers completed history and exact original decision', async () => {
    loseApproval(); await mount(); await review(); await click('Approve reassignment'); await settle();
    await render(null); localStorage.clear(); O.api.setToken('synthetic-new-session');
    await mount(); assert.equal(find('[aria-label="Filter reassignment requests by status"]').value, 'all');
    await click('View saved decision'); await settle();
    assert.match(panel().textContent, /Server-saved decision/); assert.ok(panel().textContent.includes(reason)); assert.equal(posts().length, 1);
});
test('pending recovery requires explicit retry and reuses frozen reason and key after close/reopen', async () => {
    let attempts = 0;
    custom = request => { if (request.path.endsWith('/approve') && ++attempts === 1) throw new Error('No acknowledgement'); };
    await mount(); await review(); await click('Approve reassignment'); await settle();
    await click('Cancel review'); await click('Review reassignment'); await settle();
    assert.equal(find('#reassignment-review-reason').value, reason); assert.equal(find('#reassignment-review-reason').disabled, true);
    assert.equal(posts().length, 1);
    await click('Retry original approval'); await settle();
    assert.equal(posts().length, 2); assert.deepEqual(posts()[0].body, posts()[1].body);
    assert.equal(posts()[0].headers['Idempotency-Key'], posts()[1].headers['Idempotency-Key']);
    assert.match(panel().textContent, /Server-saved decision/);
});
for (const status of [409, 503]) test(`HTTP${status} preserves actual refusal/error reason without claiming lost delivery`, async () => {
    custom = request => request.path.endsWith('/approve') ? response({ detail: { code: 'target_trunk_not_ready', message: 'Target trunk is not ready' } }, status) : undefined;
    await mount(); await review(); await click('Approve reassignment'); await settle();
    assert.match(panel().textContent, /Target trunk is not ready/); assert.doesNotMatch(panel().textContent, /response was lost/);
    if (status === 409) assert.match(panel().textContent, /server rejected this approval \(409\)/);
    assert.equal(posts().length, 1); assert.equal(button('Retry original approval').disabled, true);
});
test('malformed successful approval remains unconfirmed until exact read recovers it', async () => {
    custom = request => { if (request.path.endsWith('/approve')) { stored = approved; return response({ ...approved, approved_assignment_id: null }); } };
    await mount(); await review(); await click('Approve reassignment'); await settle();
    assert.match(panel().textContent, /response could not be verified/); assert.doesNotMatch(panel().textContent, /Server-saved decision/);
    await click('Check saved decision'); assert.match(panel().textContent, /Server-saved decision/); assert.equal(posts().length, 1);
});
for (const mismatch of [{ id: id(99) }, { target_tenant_id: id(99) }, { approved_by: null }, { decision_reason: {} }]) {
    test(`wrong/malformed exact saved decision stays unverified ${JSON.stringify(mismatch)}`, async () => {
        loseApproval(); await mount(); await review(); await click('Approve reassignment'); await settle();
        custom = request => request.path.endsWith(`/reassignments/${pending.id}`) ? response({ ...approved, ...mismatch }) : undefined;
        await click('Check saved decision'); await settle();
        assert.match(panel().textContent, /does not match this request/); assert.doesNotMatch(panel().textContent, /Server-saved decision/);
        assert.equal(posts().length, 1); assert.equal(button('Retry original approval').disabled, true);
    });
}
test('confirmed decision remains visible when exact/history/inventory reloads fail', async () => {
    stored = approved; await mount(); await click('View saved decision'); await settle();
    detailFailure = historyFailure = inventoryFailure = true;
    await click('Refresh'); await settle();
    assert.match(panel().textContent, /Server-saved decision/); assert.match(panel().textContent, /Exact decision unavailable/);
    assert.match(text(), /History unavailable/); assert.match(text(), /Inventory unavailable/);
    assert.equal(button('Check saved decision').disabled, false); assert.equal(posts().length, 0);
});
test('terminal decision does not require surviving tenant names', async () => {
    stored = approved;
    custom = request => request.path.includes('/admin/tenants/') ? response({ detail: 'Tenant removed' }, 404) : undefined;
    await mount(); await click('View saved decision'); await settle();
    assert.match(panel().textContent, /Server-saved decision/);
    assert.ok(panel().textContent.includes(pending.source_tenant_id)); assert.equal([...state.host.querySelectorAll('button')].some(node => node.textContent === 'Approve reassignment'), false);
    assert.ok(state.requests.filter(request => request.path.includes('/admin/tenants/')).length >= 2);
    assert.match(panel().textContent, /Some tenant names are unavailable/);
});
test('top Refresh retires pending approval and reads a newly completed decision', async () => {
    await mount(); await review(); assert.equal(button('Approve reassignment').disabled, false);
    const oldApprove = props(button('Approve reassignment')).onClick;
    const read = deferred(); stored = approved;
    custom = request => request.path.endsWith(`/reassignments/${pending.id}`) ? read.promise : undefined;
    await click('Refresh'); await act(async () => oldApprove()); assert.equal(posts().length, 0);
    await act(async () => read.resolve(response(approved))); await settle();
    assert.match(panel().textContent, /Server-saved decision/); assert.equal([...state.host.querySelectorAll('button')].some(node => node.textContent === 'Approve reassignment'), false);
});
test('late exact selection A and retained callbacks cannot overwrite or approve selection B', async () => {
    rows = [pending, second]; await mount(); await review();
    const oldApprove = props(button('Approve reassignment')).onClick;
    const oldCheck = props(button('Check saved decision')).onClick;
    const late = deferred(); custom = request => request.path.endsWith(`/reassignments/${pending.id}`) ? late.promise : undefined;
    await click('Check saved decision'); await click('Cancel review');
    await act(async () => [...state.host.querySelectorAll('button')].filter(node => node.textContent === 'Review reassignment')[1].click()); await settle();
    const before = state.requests.length;
    await act(async () => { oldApprove(); oldCheck(); late.resolve(response(approved)); }); await settle();
    assert.match(panel().textContent, /Second customer request/); assert.doesNotMatch(panel().textContent, /Server-saved decision/);
    assert.equal(posts().length, 0); assert.equal(state.requests.length, before);
});
test('session retirement and unmount prevent late exact decision adoption or retained approval dispatch', async () => {
    await mount(); await review(); const oldApprove = props(button('Approve reassignment')).onClick;
    const late = deferred(); custom = request => request.path.endsWith(`/reassignments/${pending.id}`) ? late.promise : undefined;
    await click('Check saved decision'); await render(null);
    await act(async () => { O.api.setToken('replacement-account-token'); oldApprove(); late.resolve(response(approved)); });
    assert.equal(text(), ''); assert.equal(posts().length, 0);
});
test('mounted identity retirement retires the selected request and ignores its late decision', async () => {
    await mount(); await review(); const oldApprove = props(button('Approve reassignment')).onClick;
    const late = deferred(); custom = request => request.path.endsWith(`/reassignments/${pending.id}`) ? late.promise : undefined;
    await click('Check saved decision');
    await act(async () => { O.api.setToken(null); });
    await act(async () => { oldApprove(); late.resolve(response(approved)); }); await settle();
    assert.equal(panel(), null); assert.doesNotMatch(text(), /Server-saved decision/); assert.equal(posts().length, 0);
});
test('direct confirmed approval survives failed automatic history and inventory reads', async () => {
    custom = request => {
        if (!request.path.endsWith('/approve')) return;
        inventoryFailure = historyFailure = true; stored = approved; return response(approved);
    };
    await mount(); await review(); await click('Approve reassignment'); await settle();
    assert.match(panel().textContent, /Server-saved decision/); assert.match(text(), /Reassignment approved and applied atomically/);
    assert.match(text(), /History unavailable/); assert.match(text(), /Inventory unavailable/); assert.equal(posts().length, 1);
});
test('history uses bounded cursor pagination and resetting filter returns to first page', async () => {
    const cursor = 'v1:opaque+/=';
    custom = request => {
        const url = new URL(request.path, 'https://synthetic.invalid');
        if (!url.pathname.endsWith('/inbound/reassignments')) return;
        if (url.searchParams.get('status') === 'approved') return response(pageOf([approved]));
        return response(url.searchParams.has('cursor') ? pageOf([second]) : { items: [pending], has_more: true, next_cursor: cursor });
    };
    await mount(); await click('Next requests'); await settle();
    assert.match(text(), /Page 2/); assert.match(text(), /Second customer request/);
    const query = new URL(state.requests.filter(request => request.path.includes('/reassignments?')).at(-1).path, 'https://synthetic.invalid').searchParams;
    assert.equal(query.get('cursor'), cursor); assert.equal(query.get('limit'), '50');
    await act(async () => { const node = find('[aria-label="Filter reassignment requests by status"]'); node.value = 'approved'; node.dispatchEvent(new dom.window.Event('change', { bubbles: true })); }); await settle();
    assert.match(text(), /Page 1/); assert.equal(button('Previous requests').disabled, true);
    assert.ok(!state.requests.filter(request => request.path.includes('/reassignments?')).at(-1).path.includes('cursor='));
});
test('malformed page continuation fails visibly and never claims complete empty history', async () => {
    custom = request => request.path.includes('/reassignments?') ? response({ items: [], has_more: true, next_cursor: null }) : undefined;
    await mount(); assert.match(text(), /history response could not be verified/);
    assert.doesNotMatch(text(), /No reassignment requests in this view/); assert.equal(button('Next requests').disabled, true);
    custom = null; await click('Retry request history'); await settle(); assert.ok(button('Review reassignment'));
});
test('contradictory later pending read cannot reverse a previously confirmed decision', async () => {
    stored = approved; await mount(); await click('View saved decision'); await settle(); stored = pending;
    await click('Check saved decision'); await settle();
    assert.match(panel().textContent, /Server-saved decision/); assert.match(panel().textContent, /does not match this request/);
    assert.equal([...state.host.querySelectorAll('button')].some(node => node.textContent === 'Approve reassignment'), false); assert.equal(posts().length, 0);
});
test('saved decision by another approver is displayed without attributing approval to this actor', async () => {
    stored = { ...approved, approved_by: id(20), decision_reason: 'Another operator decided this request' };
    await mount(); await click('View saved decision'); await settle();
    assert.match(panel().textContent, /Another operator decided this request/); assert.ok(panel().textContent.includes(id(20)));
    assert.doesNotMatch(text(), /Reassignment approved and applied atomically/); assert.equal(posts().length, 0);
});

test('usability: confirmed decision retains already loaded tenant names while their refresh is pending', async () => {
    await mount(); await review(); assert.match(panel().textContent, /Exact tenant name/);
    const names = deferred();
    custom = request => request.path.includes('/admin/tenants/') ? names.promise : undefined;
    try {
        await click('Approve reassignment'); await settle();
        assert.match(panel().textContent, /Server-saved decision/);
        assert.match(panel().textContent, /Exact tenant name/);
    } finally { await act(async () => names.resolve(response({ detail: 'Name refresh unavailable' }, 503))); }
});

test('usability: opening a terminal history row fetches available tenant names without gating its decision', async () => {
    stored = approved; await mount(); await click('View saved decision'); await settle();
    assert.match(panel().textContent, /Server-saved decision/);
    assert.match(panel().textContent, /Exact tenant name/);
    assert.ok(state.requests.some(request => request.path.endsWith(`/admin/tenants/${pending.source_tenant_id}`)));
    assert.equal(posts().length, 0);
});

test('usability: focused decision-check remains focusable while a read is busy', async () => {
    stored = approved; await mount(); await click('View saved decision'); await settle();
    const late = deferred(); custom = request => request.path.endsWith(`/reassignments/${pending.id}`) ? late.promise : undefined;
    const check = button('Check saved decision'); check.focus();
    try {
        await act(async () => check.click());
        assert.equal(check.disabled, false, 'Read loading must not natively disable the focused button');
        assert.equal(check.getAttribute('aria-disabled'), 'true'); assert.equal(check.getAttribute('aria-busy'), 'true');
        assert.equal(document.activeElement, check);
    } finally { await act(async () => late.resolve(response(approved))); }
    assert.equal(document.activeElement, check); assert.equal(check.getAttribute('aria-busy'), 'false');
});

test('usability: two immediate decision-check activations issue only one exact read', async () => {
    stored = approved; await mount(); await click('View saved decision'); await settle();
    const late = deferred(); custom = request => request.path.endsWith(`/reassignments/${pending.id}`) ? late.promise : undefined;
    const exactReads = () => state.requests.filter(request => request.path.endsWith(`/reassignments/${pending.id}`)).length;
    const before = exactReads(), check = button('Check saved decision');
    try {
        await act(async () => { check.click(); check.click(); });
        assert.equal(exactReads() - before, 1);
    } finally { await act(async () => late.resolve(response(approved))); }
});

test('terminal name lookup shows the available tenant independently when the other lookup fails', async () => {
    stored = approved;
    custom = request => request.path.endsWith(`/admin/tenants/${pending.source_tenant_id}`)
        ? response({ detail: 'Source name unavailable' }, 503) : undefined;
    await mount(); await click('View saved decision'); await settle();
    assert.match(panel().textContent, /Server-saved decision/); assert.match(panel().textContent, /Exact tenant name/);
    assert.match(panel().textContent, /Some tenant names are unavailable/); assert.equal(posts().length, 0);
});

test('decision read completion never steals focus after the operator moves elsewhere', async () => {
    stored = approved; await mount(); await click('View saved decision'); await settle();
    const late = deferred(); custom = request => request.path.endsWith(`/reassignments/${pending.id}`) ? late.promise : undefined;
    const check = button('Check saved decision'); check.focus(); await act(async () => check.click());
    const cancel = button('Cancel review'); cancel.focus();
    await act(async () => late.resolve(response(approved)));
    assert.equal(document.activeElement, cancel); assert.equal(posts().length, 0);
});
