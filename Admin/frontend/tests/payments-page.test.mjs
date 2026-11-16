// Payments page: GET /admin/billing/reconciliation + GET /billing/config, read-only.
// Mode banner, totals per currency, truncation notice, browser filters, and the rule
// that a failed read never looks like "no payments".
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { C, api, act, dom, el, find, click, render, response, setInput, settle, state, text } from './bh-harness.mjs';

const TENANT = '20000000-0000-4000-8000-000000000001';
const OTHER = '20000000-0000-4000-8000-000000000002';
const ADMIN = { id: 'admin', email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null };
const entry = (extra = {}) => ({
    created_at: '2026-11-10T10:00:00Z', tenant_id: TENANT, business_name: 'Synthetic tenant', kind: 'topup', minutes_delta: 100,
    amount_cents: 1250, currency: 'usd', package_code: 'starter_100', provider_event_id: 'evt_1', provider_payment_id: 'pi_1',
    order_status: 'paid', note: '', ...extra,
});
const usd = { currency: 'usd', currency_exponent: 2, gross_cents: 1250, reversed_cents: 500, net_cents: 750 };
const recon = (entries, extra = {}, totals = {}) => ({
    entries, truncated: false, limit: 5000,
    totals: { scope: 'returned_rows', rows: entries.length, minutes_sold: 100, minutes_reversed: 40, minutes_used: 7, by_currency: [usd], ...totals },
    ...extra,
});
const isRecon = (request) => request.path.startsWith('/api/v1/admin/billing/reconciliation');
const isConfig = (request) => request.path === '/api/v1/billing/config';
const reconReads = () => state.requests.filter(isRecon);
const lastQuery = () => new URL(`https://x.invalid${reconReads().at(-1).path}`).searchParams;
const route = ({ data = () => response(recon([entry()])), config = () => response({ billing_mode: 'test' }) } = {}) => {
    state.route = (request) => request.path.endsWith('/auth/me') ? response(ADMIN)
        : isRecon(request) ? data(request) : isConfig(request) ? config()
            : request.path.startsWith('/api/v1/admin/tenants') ? response([]) : response({ detail: 'Unexpected synthetic request' }, 503);
};
const draw = async () => { await render2(); };
const rowsText = () => [...state.host.querySelectorAll('table[aria-label="Payments"] tbody tr')].map((row) => row.textContent);
const changeSelect = async (selector, value) => {
    await act(async () => {
        const node = find(selector); assert.equal(node !== null, true, selector); node.value = value;
        node.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
    });
    await settle();
};
const render2 = async (path = '/payments', role = ADMIN) => {
    state.route = ((inner) => (request) => request.path.endsWith('/auth/me') ? response(role) : inner(request))(state.route);
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: [path] },
        el(C.AdminRouteGuard, { allowedRoles: ['platform_admin', 'super_admin'] }, el(C.PaymentsPage)))));
    await settle(60);
};

test('api: reconciliation and config are plain GET reads', async () => {
    route();
    await api.getAdminBillingReconciliation({ since: '2026-11-01T00:00:00.000Z', until: '2026-11-06T00:00:00.000Z' });
    assert.equal(reconReads().at(-1).method, 'GET');
    assert.deepEqual(Object.fromEntries(lastQuery()), { since: '2026-11-01T00:00:00.000Z', until: '2026-11-06T00:00:00.000Z' });
    await api.getAdminBillingReconciliation();
    assert.equal(reconReads().at(-1).path, '/api/v1/admin/billing/reconciliation');
    await api.getBillingConfig();
    assert.equal(state.requests.at(-1).path, '/api/v1/billing/config');
    assert.equal(state.requests.at(-1).method, 'GET');
});

test('menu: Payments sits directly after Phone lines, platform admins only, at /payments', () => {
    const ids = C.ADMIN_ROUTES.map((item) => item.id);
    assert.equal(ids.indexOf('payments'), ids.indexOf('trunks') + 1);
    assert.equal(ids.indexOf('trunks'), ids.indexOf('campaigns') + 1);
    assert.deepEqual({ ...C.ADMIN_ROUTES.find((rule) => rule.id === 'payments') }, { id: 'payments', label: 'Payments', path: '/payments', platformOnly: true });
    for (const role of ['platform_admin', 'super_admin']) assert.equal(C.canAccessAdminRoute('/payments', role), true, role);
    for (const role of ['partner_admin', 'tenant_admin', 'admin', 'owner', 'user', undefined]) assert.equal(C.canAccessAdminRoute('/payments', role), false, String(role));
});

test('menu: the sidebar shows Payments to a platform admin only', async () => {
    state.route = (request) => request.path.endsWith('/auth/me') ? response(ADMIN) : response({ detail: 'unrouted' }, 500);
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: ['/payments'] }, el(C.Sidebar))));
    await settle();
    assert.equal(find('a[href="/payments"]').getAttribute('aria-current'), 'page');
});

test('route: /payments is registered in App.tsx with the same guard as /campaigns', () => {
    const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8').replace(/\s+/g, ' ');
    assert.equal(app.includes('<Route path="/payments" element={ <AdminRouteGuard allowedRoles={PLATFORM_ROLES}> <PaymentsPage /> </AdminRouteGuard> } />'), true);
});

test('route: a tenant admin is kept out and nothing is read', async () => {
    api.setToken('access-A');
    route();
    await render2('/payments', { id: 'scoped', email: 's@example.invalid', name: 'S', role: 'tenant_admin', tenant_id: TENANT });
    assert.match(text(), /Access Denied/);
    assert.equal(reconReads().length, 0);
    assert.equal(state.requests.filter(isConfig).length, 0);
});

test('page: a platform admin makes both reads and sees the page', async () => {
    api.setToken('access-A');
    route();
    await render2();
    assert.equal(find('h1.page-title').textContent, 'Payments');
    assert.equal(reconReads().length >= 1, true);
    assert.equal(state.requests.filter(isConfig).length >= 1, true);
    assert.equal(lastQuery().has('since'), false);
});

const MODES = [
    ['live', 'Payments are LIVE: real money moves.', true],
    ['test', 'Payments are in TEST mode: no real money moves.', false],
    ['mock', 'Payments are in MOCK mode: no payment provider is called and no real money moves.', false],
    ['disabled', 'Payments are switched off: no one can pay.', false],
    ['unconfigured', 'Payments are not set up: no one can pay.', false],
];
for (const [mode, sentence, green] of MODES) {
    test(`banner: ${mode} mode says its one sentence${green ? ' and is the only green one' : ' and is never green'}`, async () => {
        route({ config: () => response({ billing_mode: mode }) });
        await draw();
        const banner = find('[data-testid="payment-mode"]');
        assert.ok(banner);
        assert.equal(banner.textContent, sentence);
        assert.equal(banner.classList.contains('success-banner'), green);
    });
}

test('banner: an unreadable mode is said plainly and is not green', async () => {
    route({ config: () => response({ detail: 'down' }, 500) });
    await draw();
    const banner = find('[data-testid="payment-mode"]');
    assert.match(banner.textContent, /could not be read/);
    assert.equal(banner.classList.contains('success-banner'), false);
    assert.equal(rowsText().length, 1, 'the payment list still reads');
});

test('totals: per currency with the server exponent, and the minutes', async () => {
    const jpy = { currency: 'jpy', currency_exponent: 0, gross_cents: 5000, reversed_cents: 1000, net_cents: 4000 };
    route({ data: () => response(recon([entry()], {}, { by_currency: [usd, jpy] })) });
    await draw();
    const rows = [...state.host.querySelectorAll('table[aria-label="Totals by currency"] tbody tr')].map((row) => row.textContent);
    assert.equal(rows.length, 2);
    assert.match(rows[0], /USD/); assert.match(rows[0], /\$12\.50/); assert.match(rows[0], /\$5\.00/); assert.match(rows[0], /\$7\.50/);
    assert.match(rows[1], /JPY/);
    assert.ok(rows[1].includes(C.formatMinorAmount(5000, 'JPY', 0))); assert.ok(rows[1].includes(C.formatMinorAmount(4000, 'JPY', 0)));
    assert.equal(rows[1].includes('50.00'), false, 'a zero-decimal currency is not divided by 100');
    const minutes = find('[data-testid="minute-totals"]').textContent;
    assert.match(minutes, /Minutes sold100/); assert.match(minutes, /Minutes reversed40/); assert.match(minutes, /Minutes used7/);
});

test('totals: a currency the server gives no exponent for reads Not available, never a guess', async () => {
    route({ data: () => response(recon([entry({ currency: 'xyz', amount_cents: 999 })], {}, { by_currency: [{ currency: 'xyz', currency_exponent: null, gross_cents: 999, reversed_cents: 0, net_cents: 999 }] })) });
    await draw();
    const row = state.host.querySelector('table[aria-label="Totals by currency"] tbody tr').textContent;
    assert.match(row, /Not available/);
    assert.equal(/9\.99|999/.test(row), false);
    assert.doesNotMatch(text(), /null|undefined|NaN/);
});

test('truncated: the page says plainly that only the newest rows are covered', async () => {
    route({ data: () => response(recon([entry()], { truncated: true, limit: 5000 })) });
    await draw();
    const notice = find('[data-testid="truncated-notice"]');
    assert.ok(notice);
    assert.match(notice.textContent, /newest 5000 rows/);
    assert.match(notice.textContent, /totals/);
});

test('truncated: no notice when nothing was cut', async () => {
    route();
    await draw();
    assert.equal(find('[data-testid="truncated-notice"]') === null, true);
});

test('table: columns, plain kinds, amounts and references', async () => {
    route({ data: () => response(recon([
        entry(),
        entry({ kind: 'refund', minutes_delta: -40, amount_cents: -500, provider_payment_id: 'pi_2', provider_event_id: 'evt_2', order_status: 'refunded', note: 'Customer asked' }),
        entry({ kind: 'dispute', minutes_delta: -10, amount_cents: -100, business_name: '' }),
        entry({ kind: 'adjustment', minutes_delta: -7, amount_cents: 0, currency: '', package_code: '' }),
    ])) });
    await draw();
    assert.deepEqual([...state.host.querySelectorAll('table[aria-label="Payments"] thead th')].map((node) => node.textContent),
        ['Date', 'Account', 'Kind', 'Minutes', 'Amount', 'Package', 'Order status', 'Payment reference', 'Event reference', 'Note']);
    const rows = rowsText();
    assert.equal(rows.length, 4);
    assert.match(rows[0], /Synthetic tenant/); assert.match(rows[0], /Top-up/); assert.match(rows[0], /\$12\.50/); assert.match(rows[0], /starter_100/); assert.match(rows[0], /pi_1/); assert.match(rows[0], /evt_1/);
    assert.match(rows[1], /Refund/); assert.match(rows[1], /-\$5\.00/); assert.match(rows[1], /pi_2/); assert.match(rows[1], /Customer asked/);
    assert.match(rows[2], /Dispute/); assert.match(rows[2], /Not available/);
    assert.match(rows[3], /Adjustment/);
    assert.doesNotMatch(text(), /\bnull\b|undefined|NaN/);
});

test('table: a refund or dispute row looks different from a top-up', async () => {
    route({ data: () => response(recon([entry(), entry({ kind: 'refund', minutes_delta: -40, amount_cents: -500 }), entry({ kind: 'dispute', minutes_delta: -10, amount_cents: -100 })])) });
    await draw();
    const kind = (row) => state.host.querySelectorAll('table[aria-label="Payments"] tbody tr')[row].querySelector('.call-status-badge');
    assert.equal(kind(0).classList.contains('status-completed'), true);
    assert.equal(kind(1).classList.contains('status-failed'), true);
    assert.equal(kind(2).classList.contains('status-failed'), true);
    assert.equal(kind(0).classList.contains('status-failed'), false);
});

test('table: the note is text only', async () => {
    route({ data: () => response(recon([entry({ note: '<img src=x onerror=alert(1)>' })])) });
    await draw();
    assert.equal(find('table img') === null, true);
    assert.equal(state.host.querySelectorAll('main img').length, 0, 'the sidebar logo is outside main');
    assert.ok(rowsText()[0].includes('<img src=x onerror=alert(1)>'));
});

test('filters: from and to dates are sent as since and until; the to day is included', async () => {
    route();
    await draw();
    await setInput(find('input[aria-label="Payments from date"]'), '2026-11-01'); await settle(40);
    await setInput(find('input[aria-label="Payments to date"]'), '2026-11-05'); await settle(40);
    assert.deepEqual(Object.fromEntries(lastQuery()), { since: '2026-11-01T00:00:00.000Z', until: '2026-11-06T00:00:00.000Z' });
});

test('filters: kind and account filter the rows already read, with no new request', async () => {
    route({ data: () => response(recon([entry(), entry({ kind: 'refund', minutes_delta: -40, amount_cents: -500 }),
        entry({ tenant_id: OTHER, business_name: 'Other tenant', provider_payment_id: 'pi_9' })])) });
    await draw();
    const before = state.requests.length;
    assert.deepEqual([...find('select[aria-label="Filter by kind"]').options].map((option) => [option.value, option.textContent]),
        [['', 'All kinds'], ['topup', 'Top-up'], ['refund', 'Refund'], ['dispute', 'Dispute'], ['adjustment', 'Adjustment']]);
    await changeSelect('select[aria-label="Filter by kind"]', 'refund');
    assert.equal(rowsText().length, 1); assert.match(rowsText()[0], /Refund/);
    await changeSelect('select[aria-label="Filter by kind"]', '');
    assert.deepEqual([...find('select[aria-label="Filter by account"]').options].map((option) => option.textContent), ['All accounts', 'Synthetic tenant', 'Other tenant']);
    await changeSelect('select[aria-label="Filter by account"]', OTHER);
    assert.equal(rowsText().length, 1); assert.match(rowsText()[0], /Other tenant/);
    assert.equal(state.requests.length, before, 'filtering in the browser sends nothing');
    await changeSelect('select[aria-label="Filter by account"]', OTHER);
    await changeSelect('select[aria-label="Filter by kind"]', 'dispute');
    assert.equal(rowsText().length, 0);
    assert.match(find('.empty-state').textContent, /No payments match these filters/);
    await click('Reset');
    await settle(40);
    assert.equal(rowsText().length, 3);
});

test('states: a failed read never looks like "no payments", and Retry reads again', async () => {
    let fail = true;
    route({ data: () => fail ? response({ detail: 'Ledger unavailable' }, 500) : response(recon([entry()])) });
    await draw();
    const alert = find('[data-testid="payments-error"]');
    assert.ok(alert); assert.match(alert.textContent, /Ledger unavailable/);
    assert.equal(find('table[aria-label="Payments"]') === null, true);
    assert.equal(find('.empty-state') === null, true);
    assert.equal(find('table[aria-label="Totals by currency"]') === null, true);
    assert.doesNotMatch(text(), /No payments/);
    fail = false;
    await click('Retry'); await settle(40);
    assert.equal(find('[data-testid="payments-error"]') === null, true);
    assert.equal(rowsText().length, 1);
});

test('states: a malformed answer is a failure, not an empty list', async () => {
    route({ data: () => response({ entries: 'none' }) });
    await draw();
    assert.ok(find('[data-testid="payments-error"]'));
    assert.equal(find('.empty-state') === null, true);
});

test('states: the empty message shows only after a successful read with no rows', async () => {
    route({ data: () => response(recon([], {}, { minutes_sold: 0, minutes_reversed: 0, minutes_used: 0, by_currency: [] })) });
    await draw();
    assert.match(find('.empty-state').textContent, /No payments in this period/);
    assert.equal(find('[data-testid="payments-error"]') === null, true);
});

test('states: loading shows while the read is pending', async () => {
    let release;
    route({ data: () => new Promise((done) => { release = () => done(response(recon([entry()]))); }) });
    await draw();
    assert.match(find('.table-loading').textContent, /Loading payments/);
    assert.equal(find('.empty-state') === null, true);
    await act(async () => release()); await settle(40);
    assert.equal(rowsText().length, 1);
});

test('read only: every request the page makes is a GET, and none reaches a write route', async () => {
    route();
    await draw();
    await setInput(find('input[aria-label="Payments from date"]'), '2026-11-01'); await settle(40);
    await click('Reset'); await settle(40);
    assert.equal(state.requests.every((request) => request.method === 'GET'), true);
    for (const request of state.requests) assert.doesNotMatch(request.path, /billing\/packages|\/quota/);
    assert.equal(state.host.querySelectorAll('input[type="text"], textarea').length, 0, 'nothing to type into');
});
