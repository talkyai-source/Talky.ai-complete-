// /tenants row action menu: the open menu is a viewport-positioned floating
// panel, so .table-container's overflow cannot clip it and it never leaves the window.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, dom, state, render, response, find, click } from './bh-harness.mjs';

const tenant = (id, extra = {}) => ({
    id, business_name: `Tenant ${id}`, plan_id: 'p1', plan_name: 'Pro', minutes_used: 40, minutes_allocated: 500,
    status: 'active', user_count: 2, campaign_count: 1, max_concurrent_calls: 5, created_at: null, updated_at: '2026-10-21T00:00:00Z', ...extra,
});
const draw = async (tenants) => {
    await render(el(C.TenantsTable, { tenants, loading: false, onRefresh() {}, searchTerm: '', onSearchChange() {},
        statusFilter: '', onStatusFilterChange() {} }));
};
const MENU = { width: 160, height: 132 };
const WIDTHS = [360, 390, 768, 1024, 1280, 1366, 1440, 1920];
const prototype = dom.window.HTMLElement.prototype;
const originalRect = prototype.getBoundingClientRect;
const viewport = (width, height) => {
    Object.defineProperty(dom.window, 'innerWidth', { configurable: true, value: width });
    Object.defineProperty(dom.window, 'innerHeight', { configurable: true, value: height });
};
/** Buttons sit at the table's right edge; the first row near the top, the last row near the bottom. */
const geometry = (width, height) => {
    prototype.getBoundingClientRect = function () {
        if (this.classList?.contains('action-menu')) return { ...MENU, top: 0, left: 0, right: MENU.width, bottom: MENU.height };
        if (this.classList?.contains('action-menu-btn')) {
            const last = this.getAttribute('aria-label')?.endsWith('last');
            const top = last ? height - 40 : 120;
            return { top, bottom: top + 28, left: width - 44, right: width - 16, width: 28, height: 28 };
        }
        return originalRect.call(this);
    };
};
test.beforeEach(() => { state.route = () => response({}); });
test.afterEach(() => { prototype.getBoundingClientRect = originalRect; });

const within = (style, width, height) => {
    const top = parseFloat(style.top), left = parseFloat(style.left);
    assert.equal(style.position, 'fixed');
    assert.ok(top >= 8, `top ${top} inside the window`);
    assert.ok(top + MENU.height <= height - 8, `bottom ${top + MENU.height} inside a ${height}px window`);
    assert.ok(left >= 8, `left ${left} inside the window`);
    assert.ok(left + MENU.width <= width - 8, `right ${left + MENU.width} inside a ${width}px window`);
    return { top, left };
};

for (const width of WIDTHS) {
    test(`the open menu stays inside a ${width}px window for the first and last rows`, async () => {
        const height = width < 500 ? 740 : 800;
        viewport(width, height); geometry(width, height);
        await draw([tenant('first'), tenant('middle'), tenant('last')]);
        await act(async () => find('[aria-label="Actions for Tenant first"]').click());
        const first = within(find('.action-menu').style, width, height);
        assert.equal(first.top, 120 + 28 + 4, 'first row opens below its button');
        assert.equal(first.left, Math.min(width - 16 - MENU.width, width - 8 - MENU.width), 'right-aligned to the button, inside the window');
        await act(async () => find('[aria-label="Actions for Tenant last"]').click());
        const last = within(find('.action-menu').style, width, height);
        assert.equal(last.top, height - 40 - 4 - MENU.height, 'last row flips above its button');
    });
}

test('the floating menu is not clipped by the scrolling table container', async () => {
    viewport(1024, 800); geometry(1024, 800);
    await draw([tenant('first')]);
    await act(async () => find('[aria-label="Actions for Tenant first"]').click());
    const menu = find('.action-menu');
    assert.ok(find('.table-container').contains(menu), 'markup stays in the actions cell for focus return');
    assert.equal(menu.style.position, 'fixed');
    assert.equal(menu.style.right, 'auto');
});

test('placement clamps every edge', () => {
    const screen = { width: 360, height: 740 };
    const tight = C.placeFloatingMenu({ top: 700, bottom: 728, left: 320, right: 348 }, MENU, screen);
    assert.deepEqual([tight.top, tight.left], [700 - 4 - MENU.height, 348 - MENU.width]);
    const topLeft = C.placeFloatingMenu({ top: 2, bottom: 30, left: 0, right: 20 }, MENU, screen);
    assert.deepEqual([topLeft.top, topLeft.left], [34, 8]);
    const noRoom = C.placeFloatingMenu({ top: 40, bottom: 68, left: 100, right: 200 }, { width: 160, height: 1000 }, screen);
    assert.equal(noRoom.top, 8, 'never placed above the window');
});

test('the menu closes when the page scrolls or resizes and the actions still run', async () => {
    viewport(1280, 800); geometry(1280, 800);
    await draw([tenant('first')]);
    await act(async () => find('[aria-label="Actions for Tenant first"]').click());
    assert.ok(find('.action-menu'));
    await act(async () => document.dispatchEvent(new dom.window.Event('scroll')));
    assert.equal(find('.action-menu') === null, true, 'closed on scroll');
    await act(async () => find('[aria-label="Actions for Tenant first"]').click());
    await act(async () => dom.window.dispatchEvent(new dom.window.Event('resize')));
    assert.equal(find('.action-menu') === null, true, 'closed on resize');
    await act(async () => find('[aria-label="Actions for Tenant first"]').click());
    await click('Suspend subscription');
    assert.ok(find('.modal-backdrop'), 'Suspend still opens its confirmation');
});
