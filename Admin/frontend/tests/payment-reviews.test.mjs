// Payments that need review: GET /admin/billing/payment-reviews and the close POST, drawn through the
// Payments page. A failed or malformed read never looks like "nothing to review"; closing needs a note,
// a tick and a confirmation, sends one POST and reads the list again.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, act, dom, el, find, click, render, response, settle, state, text } from './bh-harness.mjs';

const ADMIN = { id: 'admin', email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null };
const TENANT = '20000000-0000-4000-8000-000000000001';
const LIST = '/api/v1/admin/billing/payment-reviews';
const CLOSE = '/api/v1/admin/billing/payment-reviews/close';
const item = (extra = {}) => ({
    provider: 'stripe', event_id: 'evt_1', event_type: 'checkout.session.completed', state: 'needs_review',
    received_at: '2026-11-20T10:00:00Z', waiting_hours: 5, kind: 'minutes', tenant_id: TENANT, business_name: 'Synthetic tenant',
    amount_minor: 1250, currency: 'usd', currency_exponent: 2, reason_code: 'credit_not_confirmed',
    reason_text: 'Stripe message did not match our records.', provider_reference: 'cs_test_1', attempt_count: 2,
    can_close: true, close_blocked_reason: null, ...extra,
});
const paypal = (extra = {}) => item({
    provider: 'paypal', event_id: 'WH-9', event_type: 'LOCAL.PLAN.REVIEW', state: 'stuck', waiting_hours: 60, kind: 'plan',
    tenant_id: null, business_name: null, amount_minor: null, currency: null, currency_exponent: null,
    reason_code: 'binding', reason_text: 'PayPal subscription does not match our record.', provider_reference: 'I-ABC',
    attempt_count: 0, can_close: false, close_blocked_reason: 'PayPal may still send this message again.', ...extra,
});
const page = (items, extra = {}) => ({ items, truncated: false, limit: 100, ...extra });
const isList = (request) => request.path === LIST;
const lists = () => state.requests.filter(isList);
const posts = () => state.requests.filter((request) => request.path === CLOSE);
const route = ({ list = () => response(page([item()])), close = () => response({}, 500) } = {}) => {
    state.route = (request) => request.path.endsWith('/auth/me') ? response(ADMIN)
        : isList(request) ? list(request) : request.path === CLOSE ? close(request)
            : request.path.startsWith('/api/v1/admin/billing/reconciliation') ? response({ detail: 'unused' }, 503)
                : request.path === '/api/v1/billing/config' ? response({ billing_mode: 'test' }) : response({ detail: 'unrouted' }, 503);
};
const draw = async () => {
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: ['/payments'] },
        el(C.AdminRouteGuard, { allowedRoles: ['platform_admin', 'super_admin'] }, el(C.PaymentsPage)))));
    await settle(60);
};
const card = () => find('section[aria-label="Payments that need a person"]');
const cardText = () => card()?.textContent ?? '';
const rows = () => [...state.host.querySelectorAll('table[aria-label="Payments that need review"] tbody tr')];
const type = async (value) => {
    const area = find('textarea');
    await act(async () => {
        Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(area, value);
        area.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    });
};
const tick = async () => { await act(async () => find('input[type="checkbox"]').click()); };
const closeButton = () => [...state.host.querySelectorAll('button')].find((node) => node.textContent.trim() === 'Close this review');
const GOOD_NOTE = 'Checked at Stripe, refunded by hand.';

test('api: the list is a plain GET and the close is a POST with exactly four fields and no operator', async () => {
    route({ close: () => response({ provider: 'stripe', event_id: 'evt_1', state: 'dismissed', changed: true, prior_state: 'needs_review', financial_effects_applied: false }) });
    await api.getPaymentReviews();
    assert.equal(state.requests.at(-1).path, LIST);
    assert.equal(state.requests.at(-1).method, 'GET');
    await api.closePaymentReview({ provider: 'paypal', event_id: 'WH-9', seen_attempt_count: 3, reason: GOOD_NOTE });
    const sent = state.requests.at(-1);
    assert.equal(sent.path, CLOSE);
    assert.equal(sent.method, 'POST');
    assert.deepEqual(sent.body, { provider: 'paypal', event_id: 'WH-9', seen_attempt_count: 3, reason: GOOD_NOTE });
    assert.equal(Object.keys(sent.body).some((key) => /operator/i.test(key)), false);
});

test('list: a Stripe row and a PayPal row show their words, and a blocked row has no button', async () => {
    route({ list: () => response(page([item(), paypal()])) });
    await draw();
    assert.match(cardText(), /Payments that need a person/);
    assert.match(cardText(), /This list is what sets off the payments alert\./);
    const [stripeRow, paypalRow] = rows().map((row) => row.textContent);
    assert.match(stripeRow, /5 hours/);
    assert.match(stripeRow, /Stripe/);
    assert.match(stripeRow, /Synthetic tenant/);
    assert.match(stripeRow, /Minutes/);
    assert.match(stripeRow, /\$12\.50/);
    assert.match(stripeRow, /Stripe message did not match our records\./);
    assert.match(stripeRow, /cs_test_1/);
    assert.match(stripeRow, /Mark as handled/);
    assert.match(paypalRow, /2 days/);
    assert.match(paypalRow, /PayPal/);
    assert.match(paypalRow, /Not known/);
    assert.match(paypalRow, /Plan/);
    assert.match(paypalRow, /I-ABC/);
    assert.match(paypalRow, /PayPal may still send this message again\./);
    assert.equal(rows()[1].querySelector('button'), null);
    assert.equal(rows()[0].querySelectorAll('button').length, 1);
    assert.equal(find('[data-testid="payment-reviews-empty"]'), null);
});

test('list: the empty message shows only after a successful read with no rows', async () => {
    route({ list: () => response(page([])) });
    await draw();
    assert.equal(find('[data-testid="payment-reviews-empty"]').textContent,
        'Nothing needs review: every Stripe and PayPal payment message was handled.');
    assert.equal(find('[data-testid="payment-reviews-error"]'), null);
});

test('list: a failed read shows the error text and never the empty message', async () => {
    route({ list: () => response({ detail: 'down' }, 503) });
    await draw();
    assert.match(find('[data-testid="payment-reviews-error"]').textContent,
        /The list of payments that need review could not be read\. This does not mean there are none\./);
    assert.equal(find('[data-testid="payment-reviews-empty"]'), null);
    assert.doesNotMatch(cardText(), /Nothing needs review/);
});

test('list: a malformed answer is a failure, not an empty list', async () => {
    for (const bad of [{ items: [] }, page([item({ state: 'closed' })]), page([item({ waiting_hours: '5' })]),
        page([{ ...item(), can_close: undefined }]), { items: 'none', truncated: false, limit: 100 }]) {
        route({ list: () => response(bad) });
        await draw();
        assert.ok(find('[data-testid="payment-reviews-error"]'), JSON.stringify(bad).slice(0, 60));
        assert.equal(find('[data-testid="payment-reviews-empty"]'), null);
        await act(async () => state.root.render(null));
    }
});

test('list: a truncated answer says only the oldest are shown', async () => {
    route({ list: () => response(page([item()], { truncated: true })) });
    await draw();
    assert.equal(find('[data-testid="payment-reviews-truncated"]').textContent,
        'Only the oldest 100 for each provider are shown. Close some to see the rest.');
});

test('close: no textarea before the click; the button needs 12 characters and the tick; one POST; the list is read again', async () => {
    let closed = false;
    route({
        list: () => response(page(closed ? [] : [item()])),
        close: () => { closed = true; return response({ provider: 'stripe', event_id: 'evt_1', state: 'dismissed', changed: true, prior_state: 'needs_review', financial_effects_applied: false }); },
    });
    await draw();
    assert.equal(card().querySelector('textarea'), null);
    await click('Mark as handled');
    assert.ok(card().querySelector('textarea'));
    assert.match(cardText(), /What did you check and do\? \(at least 12 characters\)/);
    assert.match(cardText(), /I checked this payment at Stripe or PayPal and settled it by hand where needed\./);
    assert.equal(closeButton().disabled, true);
    await type('too short');
    await tick();
    assert.equal(closeButton().disabled, true, 'a short note stays disabled');
    await type(GOOD_NOTE);
    assert.equal(closeButton().disabled, false);
    await act(async () => closeButton().click());
    assert.equal(posts().length, 0, 'nothing is sent before the confirmation');
    assert.match(text(), /Close this review\?/);
    assert.match(text(), /It leaves this list and the payments alert\. Nothing is given or taken back\. Your name and note are recorded\./);
    const before = lists().length;
    await click('Close review'); await settle(60);
    assert.equal(posts().length, 1);
    assert.deepEqual(posts()[0].body, { provider: 'stripe', event_id: 'evt_1', seen_attempt_count: 2, reason: GOOD_NOTE });
    assert.equal(lists().length > before, true, 'the list is read again');
    assert.match(cardText(), /Closed\. Your name and note are recorded\./);
    assert.ok(find('[data-testid="payment-reviews-empty"]'));
});

test('close: an unchecked box keeps the button disabled even with a long note', async () => {
    route();
    await draw();
    await click('Mark as handled');
    await type(GOOD_NOTE);
    assert.equal(closeButton().disabled, true);
    assert.equal(posts().length, 0);
});

test('close: a 409 shows the server text, and the list is read again', async () => {
    route({ close: () => response({ detail: 'This payment changed after the list was read. Read the list again.' }, 409) });
    await draw();
    await click('Mark as handled'); await type(GOOD_NOTE); await tick();
    await act(async () => closeButton().click());
    const before = lists().length;
    await click('Close review'); await settle(60);
    assert.equal(posts().length, 1);
    assert.match(cardText(), /This payment changed after the list was read\. Read the list again\./);
    assert.equal(lists().length > before, true);
});

test('close: a 503 says the result is not confirmed, and "already closed" is said when nothing changed', async () => {
    route({ close: () => response({ detail: 'x' }, 503) });
    await draw();
    await click('Mark as handled'); await type(GOOD_NOTE); await tick();
    await act(async () => closeButton().click());
    await click('Close review'); await settle(60);
    assert.match(cardText(), /The result is not confirmed\. Read the list again before trying again\./);
    route({ close: () => response({ provider: 'stripe', event_id: 'evt_1', state: 'dismissed', changed: false, prior_state: 'dismissed', financial_effects_applied: false }) });
    await click('Mark as handled'); await type(GOOD_NOTE); await tick();
    await act(async () => closeButton().click());
    await click('Close review'); await settle(60);
    assert.match(cardText(), /This review was already closed\./);
});
