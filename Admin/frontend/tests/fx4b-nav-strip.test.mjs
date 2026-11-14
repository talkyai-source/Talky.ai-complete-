// FX4b Task 5 — the Admin navigation strip on small screens (360 and 390 wide).
// On load the current entry is scrolled fully into view; from 601px up the
// navigation is a column that does not scroll sideways, and nothing is moved.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, dom, state, render, response, settle } from './bh-harness.mjs';
import { stripScrollLeftToReveal } from '../src/lib/nav-strip.ts';

test('strip position: an entry already in view leaves the strip where it is', () => {
    assert.equal(stripScrollLeftToReveal(0, 340, 10, 150), 0);
    assert.equal(stripScrollLeftToReveal(200, 340, 200, 540), 200);
});

test('strip position: an entry past the right edge is brought in to end at the edge', () => {
    assert.equal(stripScrollLeftToReveal(0, 340, 1591, 1746), 1746 - 340);
    assert.equal(stripScrollLeftToReveal(0, 340, 300, 460), 120, 'a half-shown entry counts as not in view');
});

test('strip position: an entry before the left edge is brought in to start at the edge', () => {
    assert.equal(stripScrollLeftToReveal(900, 340, 158, 300), 158);
    assert.equal(stripScrollLeftToReveal(900, 340, -4, 120), 0, 'never a negative position');
});

test('strip position: an entry wider than the strip shows its start', () => {
    assert.equal(stripScrollLeftToReveal(0, 100, 400, 640), 400);
});

// ---- Rendered sidebar with a stubbed layout (jsdom has none) -----------------

const ITEM = 150;
const GAP = 8;
const PAD = 10;
const scrollLeftOf = new WeakMap();

const stubLayout = (viewWidth) => {
    const proto = dom.window.HTMLElement.prototype;
    const saved = ['scrollWidth', 'clientWidth', 'scrollLeft'].map((key) => [key, Object.getOwnPropertyDescriptor(proto, key)]);
    const savedRect = proto.getBoundingClientRect;
    const items = (nav) => [...nav.querySelectorAll('.sidebar-nav-item')];
    const isNav = (node) => node.classList.contains('sidebar-nav');
    Object.defineProperty(proto, 'scrollWidth', { configurable: true, get() { return isNav(this) ? PAD * 2 + items(this).length * (ITEM + GAP) - GAP : 0; } });
    Object.defineProperty(proto, 'clientWidth', { configurable: true, get() { return isNav(this) ? viewWidth : 0; } });
    Object.defineProperty(proto, 'scrollLeft', {
        configurable: true,
        get() { return scrollLeftOf.get(this) ?? 0; },
        set(value) { scrollLeftOf.set(this, value); },
    });
    proto.getBoundingClientRect = function rect() {
        if (isNav(this)) return { left: 0, width: viewWidth };
        const nav = this.closest('.sidebar-nav');
        if (!nav) return { left: 0, width: 0 };
        const index = items(nav).indexOf(this);
        return { left: PAD + index * (ITEM + GAP) - (scrollLeftOf.get(nav) ?? 0), width: ITEM };
    };
    return () => {
        for (const [key, descriptor] of saved) {
            if (descriptor) Object.defineProperty(proto, key, descriptor); else delete proto[key];
        }
        proto.getBoundingClientRect = savedRect;
    };
};

const drawSidebar = async (path) => {
    state.route = (r) => r.path.endsWith('/auth/me')
        ? response({ id: 'admin', email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null })
        : response({ detail: 'unrouted' }, 500);
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: [path] }, el(C.Sidebar))));
    await settle();
};
const routes = () => C.ADMIN_ROUTES.filter((route) => C.canAccessAdminRoute(route.path, 'platform_admin'));
const visibleRange = (nav, viewWidth) => {
    const current = nav.querySelector('[aria-current="page"]');
    const rect = current.getBoundingClientRect();
    return { fullyVisible: rect.left >= 0 && rect.left + rect.width <= viewWidth, left: rect.left };
};

test('sidebar strip: on load the current entry far to the right is scrolled fully into view', async () => {
    const restore = stubLayout(340);
    try {
        const list = routes();
        const last = list[list.length - 1];
        await drawSidebar(last.path);
        const nav = state.host.querySelector('.sidebar-nav');
        assert.equal(nav !== null, true);
        assert.equal(nav.scrollLeft > 0, true, 'the strip moved');
        assert.equal(visibleRange(nav, 340).fullyVisible, true, 'the current entry is fully inside the strip');
    } finally { restore(); }
});

test('sidebar strip: the first entry needs no scrolling', async () => {
    const restore = stubLayout(340);
    try {
        await drawSidebar(routes()[0].path);
        const nav = state.host.querySelector('.sidebar-nav');
        assert.equal(nav.scrollLeft, 0);
        assert.equal(visibleRange(nav, 340).fullyVisible, true);
    } finally { restore(); }
});

test('sidebar column (no sideways overflow): nothing is scrolled', async () => {
    const restore = stubLayout(100000);
    try {
        const list = routes();
        await drawSidebar(list[list.length - 1].path);
        const nav = state.host.querySelector('.sidebar-nav');
        assert.equal(nav.scrollLeft, 0);
    } finally { restore(); }
});

test('sidebar strip: an entry that takes focus while cut off is scrolled fully into view', async () => {
    const restore = stubLayout(340);
    try {
        const list = routes();
        await drawSidebar(list[0].path);
        const nav = state.host.querySelector('.sidebar-nav');
        const items = [...nav.querySelectorAll('.sidebar-nav-item')];
        // The third entry spans 326-476 of a 340px strip: only its first 14px show.
        const cutOff = items[2];
        const before = cutOff.getBoundingClientRect();
        assert.equal(before.left < 340 && before.left + before.width > 340, true, 'partly visible before focus');
        await act(async () => { cutOff.focus(); });
        const after = cutOff.getBoundingClientRect();
        assert.equal(after.left >= 0 && after.left + after.width <= 340, true, 'fully visible once focused');
        // An entry already in view does not move the strip.
        const position = nav.scrollLeft;
        await act(async () => { cutOff.focus(); });
        assert.equal(nav.scrollLeft, position);
    } finally { restore(); }
});
