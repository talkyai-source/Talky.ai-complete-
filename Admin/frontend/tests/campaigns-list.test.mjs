// FX6d / plan 2.1: the Admin Campaigns list. GET /admin/campaigns (platform
// admin, read-only): menu entry after Calls, the page and its guarded route,
// the table, its filters, paging and its loading, empty and error states.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { C, api, act, dom, el, find, click, render, response, setInput, settle, state, text } from './bh-harness.mjs';

const TENANT = '20000000-0000-4000-8000-000000000001';
const item = (n, extra = {}) => ({
    id: `10000000-0000-4000-8000-${String(n).padStart(12, '0')}`, tenant_id: TENANT, tenant_name: 'Synthetic tenant',
    name: `Campaign ${n}`, status: 'running', direction: 'outbound',
    created_at: '2026-10-21T09:30:00Z', updated_at: '2026-10-22T09:30:00Z', started_at: '2026-10-21T10:00:00Z',
    contacts: 100 + n, calls: 200 + n, live_calls: n, ...extra,
});
const pageOf = (items, total = items.length, page = 1) => ({ items, total, page, page_size: 20 });
const tenants = [{ id: TENANT, business_name: 'Synthetic tenant' }];
const isList = (request) => request.path.startsWith('/api/v1/admin/campaigns');
const listReads = () => state.requests.filter(isList);
const lastQuery = () => new URL(`https://x.invalid${listReads().at(-1).path}`).searchParams;
/** Routes the list read; every other read is the tenant filter's options. */
const route = (answer) => {
    state.route = (request) => isList(request) ? answer(request)
        : request.path.startsWith('/api/v1/admin/tenants') ? response(tenants)
            : response({ detail: 'Unexpected synthetic request' }, 503);
};
const draw = async () => { await render(el(C.CampaignsTable)); await settle(); };
const changeSelect = async (selector, value) => {
    await act(async () => {
        const node = find(selector); assert.equal(node !== null, true, selector); node.value = value;
        node.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
    });
    await settle();
};
const headings = () => [...state.host.querySelectorAll('thead th')].map((node) => node.textContent);
const cells = (row) => [...state.host.querySelectorAll('tbody tr')[row].querySelectorAll('td')].map((node) => node.textContent);
// The same format the Calls history table prints (CallHistoryTable formatDate).
const stamp = (value) => { const date = new Date(value); return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); };

test('api: getAdminCampaigns reads GET /admin/campaigns with only the filters that are set', async () => {
    route(() => response(pageOf([item(1)])));
    const plain = await api.getAdminCampaigns();
    assert.equal(listReads().at(-1).path, '/api/v1/admin/campaigns');
    assert.equal(listReads().at(-1).method, 'GET');
    assert.deepEqual(plain.data, pageOf([item(1)]));
    await api.getAdminCampaigns({ tenant_id: TENANT, status: 'paused', direction: 'inbound', search: 'spring sale', page: 3, page_size: 20 });
    assert.deepEqual(Object.fromEntries(lastQuery()), { page: '3', page_size: '20', search: 'spring sale', status: 'paused', tenant_id: TENANT, direction: 'inbound' });
});

test('menu: Campaigns sits directly after Calls, platform admins only, at /campaigns', () => {
    const ids = C.ADMIN_ROUTES.map((entry) => entry.id);
    assert.equal(ids.length, 14);
    assert.equal(ids.indexOf('campaigns'), ids.indexOf('calls') + 1);
    const entry = C.ADMIN_ROUTES.find((rule) => rule.id === 'campaigns');
    assert.deepEqual({ ...entry }, { id: 'campaigns', label: 'Campaigns', path: '/campaigns', platformOnly: true });
    for (const role of ['platform_admin', 'super_admin']) assert.equal(C.canAccessAdminRoute('/campaigns', role), true, role);
    for (const role of ['partner_admin', 'tenant_admin', 'admin', 'owner', 'user', 'readonly', undefined]) assert.equal(C.canAccessAdminRoute('/campaigns', role), false, String(role));
});

test('menu: a platform admin sees Campaigns after Calls and it is the current entry on /campaigns', async () => {
    state.route = (request) => request.path.endsWith('/auth/me')
        ? response({ id: 'admin', email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null })
        : response({ detail: 'unrouted' }, 500);
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: ['/campaigns'] }, el(C.Sidebar))));
    await settle();
    const links = [...state.host.querySelectorAll('.sidebar-nav a')];
    const labels = links.map((node) => node.textContent.trim());
    assert.equal(labels.length, 14);
    assert.equal(labels[labels.indexOf('Calls') + 1], 'Campaigns');
    const current = links.filter((node) => node.getAttribute('aria-current') === 'page');
    assert.deepEqual(current.map((node) => node.textContent.trim()), ['Campaigns']);
    assert.equal(current[0].getAttribute('href'), '/campaigns');
});

test('menu: a tenant admin is not shown Campaigns', async () => {
    state.route = (request) => request.path.endsWith('/auth/me')
        ? response({ id: 'scoped', email: 'scoped@example.invalid', name: 'Scoped admin', role: 'tenant_admin', tenant_id: TENANT })
        : response({ detail: 'unrouted' }, 500);
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: ['/actions'] }, el(C.Sidebar))));
    await settle();
    const labels = [...state.host.querySelectorAll('.sidebar-nav a')].map((node) => node.textContent.trim());
    assert.equal(labels.includes('Campaigns'), false);
    assert.equal(labels.includes('Actions'), true);
});

test('route: /campaigns is registered in App.tsx with the same guard as /calls', () => {
    const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8').replace(/\s+/g, ' ');
    const guarded = (path, page) => app.includes(`<Route path="${path}" element={ <AdminRouteGuard allowedRoles={PLATFORM_ROLES}> <${page} /> </AdminRouteGuard> } />`);
    assert.equal(guarded('/calls', 'CallsPage'), true);
    assert.equal(guarded('/campaigns', 'CampaignsPage'), true);
});

test('route: the guard keeps a tenant admin out of the Campaigns page and reads nothing', async () => {
    api.setToken('access-A');
    state.route = (request) => request.path.endsWith('/auth/me')
        ? response({ id: 'scoped', email: 'scoped@example.invalid', name: 'Scoped admin', role: 'tenant_admin', tenant_id: TENANT })
        : response(pageOf([item(1)]));
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: ['/campaigns'] },
        el(C.AdminRouteGuard, { allowedRoles: ['platform_admin', 'super_admin'] }, el(C.CampaignsPage)))));
    await settle();
    assert.match(text(), /Access Denied/);
    assert.equal(listReads().length, 0);
});

test('page: a platform admin gets the Campaigns page with its list', async () => {
    api.setToken('access-A');
    state.route = (request) => request.path.endsWith('/auth/me')
        ? response({ id: 'admin', email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null })
        : isList(request) ? response(pageOf([item(1)])) : request.path.startsWith('/api/v1/admin/tenants') ? response(tenants) : response({ detail: 'unrouted' }, 500);
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: ['/campaigns'] },
        el(C.AdminRouteGuard, { allowedRoles: ['platform_admin', 'super_admin'] }, el(C.CampaignsPage)))));
    await settle(60);
    assert.equal(find('h1.page-title').textContent, 'Campaigns');
    assert.match(text(), /Campaign 1/);
});

test('table: the nine columns, the server values as sent, and dates in the Calls format', async () => {
    route(() => response(pageOf([item(1), item(2, { status: 'draft', direction: 'inbound', tenant_name: 'Other tenant', started_at: null, live_calls: 0 })])));
    await draw();
    assert.deepEqual(headings(), ['Campaign', 'Tenant', 'Status', 'Direction', 'Contacts', 'Calls', 'Live Calls', 'Created', 'Started']);
    assert.deepEqual(cells(0), ['Campaign 1', 'Synthetic tenant', 'running', 'outbound', '101', '201', '1', stamp('2026-10-21T09:30:00Z'), stamp('2026-10-21T10:00:00Z')]);
    assert.deepEqual(cells(1), ['Campaign 2', 'Other tenant', 'draft', 'inbound', '102', '202', '0', stamp('2026-10-21T09:30:00Z'), '-']);
    // The first read asks for page 1 of 20 and nothing else.
    assert.deepEqual(Object.fromEntries(new URL(`https://x.invalid${listReads()[0].path}`).searchParams), { page: '1', page_size: '20' });
});

test('table: a list only: no row opens, no row action', async () => {
    route(() => response(pageOf([item(1)])));
    await draw();
    const row = state.host.querySelector('tbody tr');
    assert.equal(row.getAttribute('role'), null);
    assert.equal(row.getAttribute('tabindex'), null);
    assert.equal(row.querySelectorAll('button, a').length, 0);
    const before = state.requests.length;
    await act(async () => row.click());
    assert.equal(state.requests.length, before);
});

test('states: loading shows while the read is pending', async () => {
    let release;
    route(() => new Promise((done) => { release = () => done(response(pageOf([item(1)]))); }));
    await draw();
    assert.match(find('.table-loading').textContent, /Loading campaigns…/);
    assert.equal(find('table') === null, true);
    await act(async () => release());
    await settle();
    assert.equal(find('.table-loading') === null, true);
    assert.match(text(), /Campaign 1/);
});

test('states: an empty answer shows the existing filter hint and no paging', async () => {
    route(() => response(pageOf([], 0)));
    await draw();
    assert.equal(find('.empty-state p').textContent, 'Try adjusting your search or filter criteria.');
    assert.equal(find('table') === null, true);
    assert.equal(find('.pagination') === null, true);
});

for (const [status, detail] of [[503, 'Campaign list is temporarily unavailable'], [422, 'Invalid tenant ID']]) {
    test(`states: a ${status} shows the server's own message, and Retry reads again`, async () => {
        let fail = true;
        route(() => fail ? response({ detail }, status) : response(pageOf([item(1)])));
        await draw();
        assert.equal(find('.error-banner p').textContent, detail);
        assert.equal(find('table') === null, true);
        const before = listReads().length;
        fail = false;
        await click('Retry');
        await settle();
        assert.equal(listReads().length, before + 1);
        assert.equal(find('.error-banner') === null, true);
        assert.match(text(), /Campaign 1/);
    });
}

test('filters: status lists the stored statuses and sends the chosen one from page 1', async () => {
    route(() => response(pageOf([item(1)])));
    await draw();
    const options = [...find('select[aria-label="Filter by status"]').options].map((option) => [option.value, option.textContent]);
    assert.deepEqual(options, [['', 'All Statuses'], ...['draft', 'scheduled', 'running', 'paused', 'completed', 'cancelled', 'stopped', 'deleted'].map((value) => [value, value])]);
    await changeSelect('select[aria-label="Filter by status"]', 'deleted');
    assert.deepEqual(Object.fromEntries(lastQuery()), { page: '1', page_size: '20', status: 'deleted' });
    await changeSelect('select[aria-label="Filter by status"]', '');
    assert.equal(lastQuery().has('status'), false);
});

test('filters: direction offers outbound and inbound and sends the chosen one', async () => {
    route(() => response(pageOf([item(1)])));
    await draw();
    const options = [...find('select[aria-label="Direction"]').options].map((option) => [option.value, option.textContent]);
    assert.deepEqual(options, [['', 'All directions'], ['outbound', 'Outbound'], ['inbound', 'Inbound']]);
    await changeSelect('select[aria-label="Direction"]', 'inbound');
    assert.deepEqual(Object.fromEntries(lastQuery()), { page: '1', page_size: '20', direction: 'inbound' });
    await changeSelect('select[aria-label="Direction"]', 'outbound');
    assert.equal(lastQuery().get('direction'), 'outbound');
});

test('filters: the tenant filter sends tenant_id', async () => {
    route(() => response(pageOf([item(1)])));
    await draw();
    await settle(300);
    await changeSelect('select[aria-label="Filter by tenant"]', TENANT);
    assert.deepEqual(Object.fromEntries(lastQuery()), { page: '1', page_size: '20', tenant_id: TENANT });
});

test('filters: search is sent only when it is 1 to 120 characters', async () => {
    route(() => response(pageOf([item(1)])));
    await draw();
    const input = find('input[aria-label="Search campaigns…"]');
    assert.equal(input.getAttribute('maxlength'), '120');
    const type = async (value) => { await setInput(input, value); await settle(380); };
    await type('spring');
    assert.equal(lastQuery().get('search'), 'spring');
    await type('a'.repeat(120));
    assert.equal(lastQuery().get('search'), 'a'.repeat(120));
    await type('a'.repeat(121));
    assert.equal(lastQuery().has('search'), false, '121 characters are not sent');
    await type('b');
    assert.equal(lastQuery().get('search'), 'b');
    await type('   ');
    assert.equal(lastQuery().has('search'), false, 'blank is not sent');
    await type('');
    assert.equal(lastQuery().has('search'), false);
});

test('filters: all filters combine in one query, and Reset clears them', async () => {
    route(() => response(pageOf([item(1)])));
    await draw();
    await settle(300);
    await changeSelect('select[aria-label="Filter by status"]', 'paused');
    await changeSelect('select[aria-label="Direction"]', 'outbound');
    await changeSelect('select[aria-label="Filter by tenant"]', TENANT);
    await setInput(find('input[aria-label="Search campaigns…"]'), 'sale');
    await settle(380);
    assert.deepEqual(Object.fromEntries(lastQuery()), { page: '1', page_size: '20', search: 'sale', status: 'paused', tenant_id: TENANT, direction: 'outbound' });
    await click('Reset');
    await settle(380);
    assert.deepEqual(Object.fromEntries(lastQuery()), { page: '1', page_size: '20' });
});

test('paging: 20 per page, "Page {page} of {totalPages}", next and previous send the page, the last page stops', async () => {
    const all = Array.from({ length: 45 }, (_, index) => item(index + 1));
    route((request) => {
        const page = Number(new URL(`https://x.invalid${request.path}`).searchParams.get('page'));
        return response(pageOf(all.slice((page - 1) * 20, page * 20), 45, page));
    });
    await draw();
    const pager = () => [...state.host.querySelectorAll('.pagination-controls button')];
    assert.equal(state.host.querySelectorAll('tbody tr').length, 20);
    assert.equal(find('.page-indicator').textContent, 'Page 1 of 3');
    assert.equal(find('.pagination-info').textContent, 'Showing 1 - 20 of 45');
    assert.equal(pager()[0].disabled, true);
    await act(async () => pager()[1].click()); await settle();
    assert.equal(lastQuery().get('page'), '2');
    assert.equal(find('.page-indicator').textContent, 'Page 2 of 3');
    await act(async () => pager()[1].click()); await settle();
    assert.equal(lastQuery().get('page'), '3');
    assert.equal(lastQuery().get('page_size'), '20');
    assert.equal(state.host.querySelectorAll('tbody tr').length, 5);
    assert.equal(find('.pagination-info').textContent, 'Showing 41 - 45 of 45');
    assert.equal(pager()[1].disabled, true);
    await act(async () => pager()[0].click()); await settle();
    assert.equal(lastQuery().get('page'), '2');
});

test('paging: changing a filter on a later page returns to page 1', async () => {
    route(() => response(pageOf(Array.from({ length: 20 }, (_, index) => item(index + 1)), 45)));
    await draw();
    await act(async () => [...state.host.querySelectorAll('.pagination-controls button')][1].click()); await settle();
    assert.equal(lastQuery().get('page'), '2');
    await changeSelect('select[aria-label="Direction"]', 'inbound');
    assert.deepEqual(Object.fromEntries(lastQuery()), { page: '1', page_size: '20', direction: 'inbound' });
});
