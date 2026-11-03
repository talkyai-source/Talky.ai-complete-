// Read-only Plans page: shows only what GET /billing/plans and
// GET /admin/billing/packages return, and never looks successful when a read failed.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, state, render, response, find, text, click, settle } from './bh-harness.mjs';

const ADMIN = { id: 'plans-admin', email: 'admin@example.invalid', name: 'Admin', role: 'platform_admin', tenant_id: null };
const plan = {
    id: 'growth', name: 'Growth', price: 49, description: 'Recorded description', minutes: 1500, agents: 3, concurrent_calls: 5,
    features: [], not_included: [], popular: true, billing_mode: 'test',
    price_options: [
        { id: 'opt-month', kind: 'stripe', interval: 'month', interval_count: 1, amount_minor: 4900, currency: 'USD', currency_exponent: 2, active: true, checkout_available: true, unavailable_reason: null },
        { id: 'opt-year', kind: 'stripe', interval: 'month', interval_count: 12, amount_minor: 49000, currency: 'USD', currency_exponent: 2, active: false, checkout_available: false, unavailable_reason: 'Price mismatch at the provider.' },
    ],
};
const bundle = { code: 'starter_100', name: 'Starter 100', minutes: 100, price_cents: 1250, currency: 'USD', audience: 'payg', is_active: true, sort_order: 1 };
const retired = { ...bundle, code: 'legacy_50', name: 'Legacy 50', minutes: 50, price_cents: 700, is_active: false };
const routes = ({ plans = () => response([plan]), packages = () => response({ items: [bundle, retired] }) } = {}) => (request) => {
    if (request.path.endsWith('/auth/me')) return response(ADMIN);
    if (request.path.endsWith('/billing/plans')) return plans();
    if (request.path.endsWith('/admin/billing/packages')) return packages();
    return response({ detail: 'unrouted synthetic request' }, 500);
};
const draw = async (path = '/plans') => {
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: [path] },
        el(C.Routes, null, el(C.Route, { path: '/plans', element: el(C.AdminRouteGuard, { allowedRoles: ['platform_admin', 'super_admin'] }, el(C.PlansPage)) }),
            el(C.Route, { path: '/login', element: el(C.LoginPage) })))));
    await settle();
};
const rows = (label) => [...(find(`table[aria-label="${label}"] tbody`)?.querySelectorAll('tr') ?? [])].map((row) => row.textContent);

test('the sidebar lists Plans as a platform-only route', async () => {
    const rule = C.ADMIN_ROUTES.find((item) => item.path === '/plans');
    assert.deepEqual(rule, { id: 'plans', label: 'Plans', path: '/plans', platformOnly: true });
    assert.equal(C.canAccessAdminRoute('/plans', 'platform_admin'), true);
    assert.equal(C.canAccessAdminRoute('/plans', 'tenant_admin'), false);
    state.route = routes(); await draw();
    assert.ok(find('a[href="/plans"]'), 'sidebar link is present for a platform admin');
});

test('plans and bundles show only backend-returned fields with prices formatted from minor units', async () => {
    state.route = routes(); await draw();
    const plans = rows('Subscription plans');
    assert.equal(plans.length, 1);
    assert.match(plans[0], /Growth/); assert.match(plans[0], /growth · popular/); assert.match(plans[0], /Recorded description/);
    assert.match(plans[0], /\$49\.00 per month/); assert.match(plans[0], /\$490\.00 every 12 months/);
    assert.match(plans[0], /Active/); assert.match(plans[0], /Inactive/);
    assert.match(plans[0], /Checkout unavailable: Price mismatch at the provider\./);
    assert.match(plans[0], /1500/); assert.match(plans[0], /test/);
    const bundles = rows('Minute bundles');
    assert.equal(bundles.length, 2);
    assert.match(bundles[0], /Starter 100/); assert.match(bundles[0], /starter_100/); assert.match(bundles[0], /payg/);
    assert.match(bundles[0], /\$12\.50/); assert.match(bundles[0], /Active/);
    assert.match(bundles[1], /Legacy 50/); assert.match(bundles[1], /\$7\.00/); assert.match(bundles[1], /Inactive/);
    assert.match(text(), /1 plans/); assert.match(text(), /2 bundles/);
    assert.equal(find('[role="alert"]') === null, true, 'no alert');
    assert.equal(state.requests.filter((request) => request.method !== 'GET').length, 0, 'the page never writes');
});

test('a failed read is shown as unavailable, never as an empty catalogue', async () => {
    state.route = routes({ plans: () => response({ detail: 'Failed to list plans' }, 500) }); await draw();
    const alert = find('[role="alert"]');
    assert.ok(alert); assert.match(alert.textContent, /Subscription plans are unavailable: Failed to list plans/);
    assert.match(text(), /Subscription plans are unavailable\./);
    assert.equal(find('table[aria-label="Subscription plans"]') === null, true);
    assert.doesNotMatch(text(), /No subscription plans are offered/);
    assert.equal(rows('Minute bundles').length, 2, 'the other read still renders');
    let healthy = false;
    state.route = routes({ plans: () => healthy ? response([plan]) : response({ detail: 'Failed to list plans' }, 500) });
    healthy = true; await click('Retry'); await settle();
    assert.equal(find('[role="alert"]') === null, true, 'no alert'); assert.equal(rows('Subscription plans').length, 1);
});

test('a malformed catalogue is rejected instead of rendered', async () => {
    state.route = routes({ packages: () => response({ items: [{ ...bundle, price_cents: 'free' }] }) }); await draw();
    assert.match(find('[role="alert"]').textContent, /Minute bundles are unavailable/);
    assert.equal(find('table[aria-label="Minute bundles"]') === null, true);
});

test('empty catalogues are shown as empty, not as errors', async () => {
    state.route = routes({ plans: () => response([]), packages: () => response({ items: [] }) }); await draw();
    assert.equal(find('[role="alert"]') === null, true, 'no alert');
    assert.match(text(), /No subscription plans are offered/); assert.match(text(), /No minute bundles are recorded/);
    assert.match(text(), /0 plans/); assert.match(text(), /0 bundles/);
});

test('minor-unit formatting honours the currency exponent', () => {
    assert.equal(C.formatMinorAmount(4900, 'USD', 2), '$49.00');
    assert.equal(C.formatMinorAmount(500, 'JPY', 0), '¥500');
    assert.equal(C.formatMinorAmount(1250, 'EUR', 2), '€12.50');
});
