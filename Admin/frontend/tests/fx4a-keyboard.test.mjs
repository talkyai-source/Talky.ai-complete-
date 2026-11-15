// FX4a Task 5.2 and Task 7 — keyboard and screen-reader support in the Admin app.
// Sidebar: the active entry carries aria-current="page", and only that entry.
// Tenant row menu: role="menu" with menu items; opening it moves focus to the
// first item; the arrow keys move between items; Escape closes it and returns
// focus to that row's button; Tab closes it.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, dom, state, render, response, find, settle } from './bh-harness.mjs';

// ---- Sidebar ---------------------------------------------------------------

const drawSidebar = async (path) => {
    state.route = (r) => r.path.endsWith('/auth/me')
        ? response({ id: 'admin', email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null })
        : response({ detail: 'unrouted' }, 500);
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: [path] }, el(C.Sidebar))));
    await settle();
};
const entries = () => [...state.host.querySelectorAll('.sidebar-nav-item')];
const marked = () => entries().filter((a) => a.getAttribute('aria-current') === 'page').map((a) => a.textContent.trim());
const highlighted = () => entries().filter((a) => a.classList.contains('active')).map((a) => a.textContent.trim());

test('sidebar: the active entry, and only it, has aria-current="page"', async () => {
    const routes = C.ADMIN_ROUTES.filter((route) => C.canAccessAdminRoute(route.path, 'platform_admin'));
    assert.ok(routes.length >= 3, 'several entries are listed');
    await drawSidebar(routes[1].path);
    assert.equal(entries().length, routes.length);
    assert.deepEqual(marked(), [routes[1].label]);
    assert.deepEqual(highlighted(), [routes[1].label], 'the marked entry is the highlighted one');
    assert.equal(entries().filter((a) => a.hasAttribute('aria-current')).length, 1, 'the other entries carry no aria-current at all');
});

test('sidebar: another page marks its own entry', async () => {
    const routes = C.ADMIN_ROUTES.filter((route) => C.canAccessAdminRoute(route.path, 'platform_admin'));
    const last = routes[routes.length - 1];
    await drawSidebar(last.path);
    assert.deepEqual(marked(), [last.label]);
    assert.deepEqual(highlighted(), [last.label]);
});

// ---- Tenant row menu -------------------------------------------------------

const tenant = (id, extra = {}) => ({
    id, business_name: `Tenant ${id}`, plan_id: 'p1', plan_name: 'Pro', minutes_used: 40, minutes_allocated: 500,
    status: 'active', user_count: 2, campaign_count: 1, max_concurrent_calls: 5, created_at: null, updated_at: '2026-10-21T00:00:00Z', ...extra,
});
const drawTable = async (tenants, extra = {}) => {
    state.route = () => response({});
    await render(el(C.TenantsTable, { tenants, loading: false, onRefresh() {}, searchTerm: '', onSearchChange() {},
        statusFilter: '', onStatusFilterChange() {}, ...extra }));
};
const rowButton = (name) => find(`[aria-label="Actions for Tenant ${name}"]`);
const open = async (name) => { await act(async () => rowButton(name).click()); };
const menu = () => find('.action-menu');
const items = () => [...state.host.querySelectorAll('.action-menu [role="menuitem"]')];
const focusIsOn = (node) => node !== null && node !== undefined && dom.window.document.activeElement === node;
const key = async (node, name, init = {}) => {
    let notCancelled = true;
    await act(async () => {
        notCancelled = node.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true, ...init }));
    });
    return notCancelled;
};

test('row menu: it is a menu of menu items, named after its row, and its button says so', async () => {
    await drawTable([tenant('first'), tenant('last')]);
    assert.equal(rowButton('first').getAttribute('aria-haspopup'), 'menu');
    assert.equal(rowButton('first').getAttribute('aria-expanded'), 'false');
    await open('first');
    assert.equal(rowButton('first').getAttribute('aria-expanded'), 'true');
    assert.equal(menu().getAttribute('role'), 'menu');
    assert.equal(menu().getAttribute('aria-label'), 'Actions for Tenant first');
    assert.deepEqual(items().map((item) => item.textContent.trim()), ['Suspend subscription', 'Edit Quota']);
    assert.equal(menu().querySelectorAll('button:not([role="menuitem"])').length, 0, 'every control in the menu is a menu item');
    assert.deepEqual(items().map((item) => item.getAttribute('tabindex')), ['-1', '-1'], 'the items are reached with the arrow keys, not Tab');
});

test('row menu: opening it moves focus to the first item', async () => {
    await drawTable([tenant('first'), tenant('last')]);
    await open('last');
    assert.equal(focusIsOn(items()[0]), true);
    assert.equal(items()[0].textContent.trim(), 'Suspend subscription');
});

test('row menu: the arrow keys, Home and End move between the items and do not scroll the page', async () => {
    await drawTable([tenant('first')], { onFormalHolds() {} });
    await open('first');
    const all = items();
    assert.deepEqual(all.map((item) => item.textContent.trim()), ['Formal holds', 'Suspend subscription', 'Edit Quota']);
    assert.equal(focusIsOn(all[0]), true);
    assert.equal(await key(all[0], 'ArrowDown'), false, 'ArrowDown is handled, so the page does not scroll');
    assert.equal(focusIsOn(all[1]), true, 'ArrowDown');
    await key(all[1], 'ArrowDown');
    assert.equal(focusIsOn(all[2]), true);
    await key(all[2], 'ArrowDown');
    assert.equal(focusIsOn(all[0]), true, 'ArrowDown on the last item goes round to the first');
    await key(all[0], 'ArrowUp');
    assert.equal(focusIsOn(all[2]), true, 'ArrowUp on the first item goes round to the last');
    await key(all[2], 'Home');
    assert.equal(focusIsOn(all[0]), true, 'Home');
    await key(all[0], 'End');
    assert.equal(focusIsOn(all[2]), true, 'End');
    assert.equal(menu() !== null, true, 'the menu is still open');
});

test('row menu: Escape closes it and returns focus to that row\'s button', async () => {
    await drawTable([tenant('first'), tenant('last')]);
    await open('last');
    await key(items()[0], 'ArrowDown');
    await key(items()[1], 'Escape');
    assert.equal(menu() === null, true, 'closed');
    assert.equal(rowButton('last').getAttribute('aria-expanded'), 'false');
    assert.equal(focusIsOn(rowButton('last')), true, 'focus is on the row that opened it');
    assert.equal(focusIsOn(rowButton('first')), false);
});

test('row menu: Tab closes it and is left to the browser, which moves on from the row\'s button', async () => {
    await drawTable([tenant('first'), tenant('last')]);
    await open('first');
    assert.equal(await key(items()[0], 'Tab'), true, 'Tab is not cancelled');
    assert.equal(menu() === null, true, 'closed');
    assert.equal(focusIsOn(rowButton('first')), true);

    await open('first');
    assert.equal(await key(items()[0], 'Tab', { shiftKey: true }), true);
    assert.equal(menu() === null, true, 'Shift+Tab closes it too');
    assert.equal(focusIsOn(rowButton('first')), true);
});

test('row menu: choosing an item with the keyboard still opens its dialog', async () => {
    await drawTable([tenant('first')]);
    await open('first');
    await key(items()[0], 'ArrowDown');
    assert.equal(items()[1].textContent.trim(), 'Edit Quota');
    await act(async () => items()[1].click());
    assert.equal(menu() === null, true);
    assert.equal(state.host.textContent.includes('Edit Quota'), true, 'the quota dialog opened');
});
