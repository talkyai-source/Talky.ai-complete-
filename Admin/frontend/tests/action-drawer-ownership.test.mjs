import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, state, render, response, find, text, click, settle, deferred, button, dom } from './bh-harness.mjs';

const A = '10000000-0000-4000-8000-000000000001';
const item = (extra = {}) => ({ id: A, type: 'set_reminder', tenant_name: 'Prior private tenant', status: 'queued',
    created_at: '2026-10-21T00:00:00Z', duration_ms: null, is_cancellable: true, is_retryable: false, ...extra });
const draw = () => render(el(C.ActionDetailDrawer, { actionId: A, onClose() {} }));
const reply = pending => pending.promise.then(value => value.clone());
const posts = () => state.requests.filter(r => r.method === 'POST');
const reads = () => state.requests.filter(r => r.path.endsWith(`/actions/${A}`));
test.beforeEach(() => { state.route = () => response(item()); });

test('account change immediately retires private action details and the old cancellation confirmation', async () => {
    await draw(); await click('Cancel Action');
    const oldButton = button('Yes, Cancel'), pending = deferred();
    state.route = () => reply(pending);
    await act(async () => { api.setToken('replacement-action-session'); oldButton.click(); });
    assert.doesNotMatch(text(), /Prior private tenant|Yes, Cancel/);
    assert.equal(posts().length, 0);
    await act(async () => pending.resolve(response(item({ tenant_name: 'New authorized view' }))));
    assert.match(text(), /New authorized view/); assert.doesNotMatch(text(), /Yes, Cancel|Retry Action/);
});

test('late old-session detail read cannot replace the new authorized view', async () => {
    const pending = deferred(); state.route = () => reply(pending); await draw();
    state.route = () => response(item({ tenant_name: 'New authorized view' }));
    await act(async () => api.setToken('replacement-action-session')); await settle();
    await act(async () => pending.resolve(response(item())));
    assert.match(text(), /New authorized view/); assert.doesNotMatch(text(), /Prior private tenant/);
});

test('late old-session cancellation cannot refresh or publish an error into the new view', async () => {
    const pending = deferred(); state.route = r => r.method === 'POST' ? reply(pending) : response(item());
    await draw(); await click('Cancel Action'); await click('Yes, Cancel');
    state.route = () => response(item({ tenant_name: 'New authorized view' }));
    await act(async () => api.setToken('replacement-action-session')); await settle();
    const before = reads().length;
    await act(async () => pending.resolve(response({ action_id: A, new_status: 'cancelled' })));
    assert.equal(reads().length, before); assert.match(text(), /New authorized view/);
    assert.doesNotMatch(text(), /sign-in changed|Prior private tenant/);
});

test('cancellation confirmation dispatches once and requires the exact saved outcome', async () => {
    const pending = deferred(); state.route = r => r.method === 'POST' ? reply(pending) : response(item());
    await draw(); await click('Cancel Action');
    const before = reads().length;
    await act(async () => { const confirm = button('Yes, Cancel'); confirm.click(); confirm.click(); });
    assert.equal(posts().length, 1);
    await act(async () => pending.resolve(response({ action_id: 'other', new_status: 'cancelled' })));
    assert.match(text(), /Cancellation is not confirmed/); assert.equal(reads().length, before);
});

test('confirmed cancellation reloads only the matching saved receipt', async () => {
    let cancelled = false;
    state.route = r => {
        if (r.method === 'POST') { cancelled = true; return response({ action_id: A, new_status: 'cancelled' }); }
        return response(item(cancelled ? { status: 'cancelled', is_cancellable: false } : {}));
    };
    await draw(); await click('Cancel Action'); await click('Yes, Cancel');
    assert.match(text(), /Cancelled/); assert.equal(posts().length, 1);
    assert.equal([...state.host.querySelectorAll('button')].some(b => b.textContent === 'Cancel Action'), false);
});

test('old-session saved-message inspection neither publishes nor gets repeated after account change', async () => {
    const pending = deferred();
    const message = item({ type: 'send_email', status: 'unknown', is_cancellable: false, email_inspection_available: true,
        saved_receipt: { action_id: A, status: 'unknown', success: false, confirmation_allowed: false,
            receipt: { provider: 'gmail', identity_version: 'authorization_row_v1', account_row_id: 'original-account', message_id: 'original-message' } } });
    state.route = r => r.path.endsWith('/email-inspection') ? reply(pending) : response(message);
    await draw(); await click('Inspect saved Gmail message');
    state.route = () => response(item({ tenant_name: 'New authorized view' }));
    await act(async () => api.setToken('replacement-inspection-session')); await settle();
    await act(async () => pending.resolve(response({ action_id: A, outcome: 'observed_message',
        reason: 'exact_message_observed_only', observed_message_id: 'original-message', observed_at: '2026-10-21T00:00:00Z' })));
    assert.match(text(), /New authorized view/); assert.doesNotMatch(text(), /Exact message observed|Inspection unavailable|original-account/);
    assert.equal(state.requests.filter(r => r.path.endsWith('/email-inspection')).length, 1);
    assert.equal(posts().length, 0);
});

test('unmounted action cancellation cannot dispatch a follow-up receipt read', async () => {
    const pending = deferred(); state.route = r => r.method === 'POST' ? reply(pending) : response(item());
    await draw(); await click('Cancel Action'); await click('Yes, Cancel');
    await render(el('p', null, 'Drawer closed'));
    const before = reads().length;
    await act(async () => pending.resolve(response({ action_id: A, new_status: 'cancelled' })));
    assert.equal(reads().length, before); assert.equal(find('.action-detail-drawer'), null);
});

const B = '10000000-0000-4000-8000-000000000002';
// Invoke the actual React DOM callback retained by a caller, including after
// its control unmounts. Detached DOM clicks alone do not reach React delegation.
const retainedClick = node => node[Object.keys(node).find(key => key.startsWith('__reactProps$'))].onClick;

for (const failure of ['outage', 'wrong action', 'stale queued status']) {
    test(`confirmed cancellation survives ${failure} during detail reload and read-only recovery`, async () => {
        let cancelled = false, repaired = false;
        state.route = r => {
            if (r.method === 'POST') { cancelled = true; return response({ action_id: A, new_status: 'cancelled' }); }
            if (!cancelled) return response(item());
            if (repaired) return response(item({ status: 'cancelled', is_cancellable: false }));
            return failure === 'outage' ? response({ detail: 'Synthetic read outage' }, 503)
                : response(item(failure === 'wrong action' ? { id: B, status: 'cancelled', is_cancellable: false } : {}));
        };
        await draw(); await click('Cancel Action');
        const oldConfirm = retainedClick(button('Yes, Cancel'));
        await click('Yes, Cancel');
        assert.match(text(), /Cancellation confirmed/);
        assert.match(text(), /Cancelled/);
        assert.match(text(), /Prior private tenant/);
        assert.match(find('[role="alert"]').textContent, /Cancellation is confirmed.*latest action details/);
        assert.doesNotMatch(text(), /Cancel Action|Yes, Cancel|Retry Action/);
        await act(async () => oldConfirm());
        assert.equal(posts().length, 1, 'a retained confirmation cannot repeat a confirmed mutation');
        repaired = true;
        await click('Reload saved action details');
        assert.equal(posts().length, 1);
        assert.equal(find('[role="alert"]'), null);
        assert.match(text(), /Cancellation confirmed/);
    });
}

test('cancellation read recovery cannot publish into another selection or session', async () => {
    const pending = deferred(); let cancelled = false;
    state.route = r => {
        if (r.method === 'POST') { cancelled = true; return response({ action_id: A, new_status: 'cancelled' }); }
        return cancelled ? reply(pending) : response(item());
    };
    await draw(); await click('Cancel Action'); await click('Yes, Cancel');
    assert.match(text(), /Cancellation confirmed/);
    state.route = () => response(item({ id: B, tenant_name: 'Current selection B' }));
    await render(el(C.ActionDetailDrawer, { actionId: B, onClose() {} }));
    await act(async () => pending.resolve(response({ detail: 'Old read outage' }, 503)));
    assert.match(text(), /Current selection B/);
    assert.doesNotMatch(text(), /Cancellation confirmed|latest action details|Prior private tenant/);
    await act(async () => api.setToken('new-cancellation-owner'));
    await settle();
    assert.doesNotMatch(text(), /Cancellation confirmed|latest action details/);
});

const recoverable = (id = A) => item({ id, type: 'send_email', status: 'unknown', is_cancellable: false,
    acknowledgement_recovery: { source_digest: 'a'.repeat(64), provider_status: 'accepted' },
    saved_receipt: { action_id: id, status: 'unknown', success: false, confirmation_allowed: false,
        receipt: { provider: 'gmail', message_id: `synthetic-${id}` } } });

for (const transition of ['selection', 'unmount', 'session']) {
    test(`retained recovery and reload callbacks cannot dispatch after ${transition} change`, async () => {
        state.route = r => r.method === 'POST' ? response({ detail: 'Unexpected synthetic mutation' }, 503)
            : response(recoverable(r.path.endsWith(B) ? B : A));
        await draw();
        const textarea = find('textarea');
        await act(async () => {
            Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(textarea, 'Reviewed the original saved acknowledgement.');
            textarea.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
        });
        await click('Recover saved acknowledgement');
        const oldRecovery = retainedClick(button('Confirm saved acknowledgement recovery'));
        const oldReload = retainedClick(button('Reload saved receipt'));
        if (transition === 'selection') await render(el(C.ActionDetailDrawer, { actionId: B, onClose() {} }));
        else if (transition === 'unmount') await render(el('p', null, 'Closed'));
        else { await act(async () => api.setToken('new-recovery-owner')); await settle(); }
        const count = state.requests.length;
        await act(async () => { oldRecovery(); oldReload(); });
        assert.equal(state.requests.length, count);
        assert.equal(posts().length, 0);
        assert.doesNotMatch(text(), /pending.*review request|Recovery is unconfirmed/);
    });
}

test('action dialog contains keyboard focus, supports Escape and restores its invoking control', async () => {
    const opener = document.createElement('button'); opener.textContent = 'Synthetic opener'; document.body.append(opener); opener.focus();
    await render(el(C.ActionDetailDrawer, { actionId: A, onClose() { state.root.render(el('p', null, 'Drawer closed')); } }));
    const dialog = find('[role="dialog"]');
    assert.equal(dialog.getAttribute('aria-modal'), 'true');
    assert.equal(document.getElementById(dialog.getAttribute('aria-labelledby')).textContent, 'Action Details');
    const close = find('[aria-label="Close action details"]');
    assert.equal(document.activeElement, close);
    const last = button('Cancel Action');
    await act(async () => { last.focus(); document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })); });
    assert.equal(document.activeElement, close);
    await act(async () => document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true })));
    assert.equal(document.activeElement, last);
    await act(async () => opener.focus());
    assert.equal(document.activeElement, close);
    await act(async () => document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })));
    assert.equal(find('[role="dialog"]'), null);
    assert.equal(document.activeElement, opener);
    assert.equal(opener.hasAttribute('inert'), false);
    opener.remove();
});
