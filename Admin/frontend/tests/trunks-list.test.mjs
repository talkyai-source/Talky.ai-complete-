// Phone lines page: GET /admin/sip-trunks (platform admin, read-only). Menu entry after
// Campaigns, the guarded route, the table, its filters, paging, its states, and the
// rule that no sign-in detail (username or password) is ever requested or shown.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { C, api, act, dom, el, find, click, render, response, setInput, settle, state, text } from './bh-harness.mjs';

const TENANT = '20000000-0000-4000-8000-000000000001';
const trunk = (n, extra = {}) => ({
    id: `30000000-0000-4000-8000-${String(n).padStart(12, '0')}`, tenant_id: TENANT, tenant_name: 'Synthetic tenant',
    trunk_name: `Line ${n}`, sip_domain: `sip${n}.carrier.invalid`, port: 5060, transport: 'udp', direction: 'outbound',
    is_active: true, auth_configured: true, role: 'primary', live_registration_status: 'registered', live_status_detail: null,
    live_status_checked_at: '2026-11-17T08:00:00Z', last_tested_at: '2026-11-16T08:00:00Z',
    created_at: '2026-10-21T09:30:00Z', updated_at: '2026-10-22T09:30:00Z', ...extra,
});
const pageOf = (items, total = items.length, page = 1) => ({ items, total, page, page_size: 20 });
const tenants = [{ id: TENANT, business_name: 'Synthetic tenant' }];
const isList = (request) => request.path.startsWith('/api/v1/admin/sip-trunks');
const listReads = () => state.requests.filter(isList);
const lastQuery = () => new URL(`https://x.invalid${listReads().at(-1).path}`).searchParams;
const route = (answer) => {
    state.route = (request) => isList(request) ? answer(request)
        : request.path.startsWith('/api/v1/admin/tenants') ? response(tenants)
            : response({ detail: 'Unexpected synthetic request' }, 503);
};
const draw = async () => { await render(el(C.TrunksTable)); await settle(); };
const changeSelect = async (selector, value) => {
    await act(async () => {
        const node = find(selector); assert.equal(node !== null, true, selector); node.value = value;
        node.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
    });
    await settle();
};
const headings = () => [...state.host.querySelectorAll('thead th')].map((node) => node.textContent);
const cells = (row) => [...state.host.querySelectorAll('tbody tr')[row].querySelectorAll('td')].map((node) => node.textContent);
const stamp = (value) => { const date = new Date(value); return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); };
const ADMIN = { id: 'admin', email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null };

test('api: getAdminSipTrunks reads GET /admin/sip-trunks with only the filters that are set', async () => {
    route(() => response(pageOf([trunk(1)])));
    await api.getAdminSipTrunks();
    assert.equal(listReads().at(-1).path, '/api/v1/admin/sip-trunks');
    assert.equal(listReads().at(-1).method, 'GET');
    await api.getAdminSipTrunks({ tenant_id: TENANT, is_active: false, registration: 'rejected', page: 2, page_size: 20 });
    assert.deepEqual(Object.fromEntries(lastQuery()), { page: '2', page_size: '20', tenant_id: TENANT, is_active: 'false', registration: 'rejected' });
});

test('menu: Phone lines sits directly after Campaigns, platform admins only, at /trunks', () => {
    const ids = C.ADMIN_ROUTES.map((entry) => entry.id);
    assert.equal(ids.indexOf('trunks'), ids.indexOf('campaigns') + 1);
    const entry = C.ADMIN_ROUTES.find((rule) => rule.id === 'trunks');
    assert.deepEqual({ ...entry }, { id: 'trunks', label: 'Phone lines', path: '/trunks', platformOnly: true });
    for (const role of ['platform_admin', 'super_admin']) assert.equal(C.canAccessAdminRoute('/trunks', role), true, role);
    for (const role of ['partner_admin', 'tenant_admin', 'admin', 'owner', 'user', 'readonly', undefined]) assert.equal(C.canAccessAdminRoute('/trunks', role), false, String(role));
});

test('menu: a platform admin sees Phone lines, a tenant admin does not', async () => {
    state.route = (request) => request.path.endsWith('/auth/me') ? response(ADMIN) : response({ detail: 'unrouted' }, 500);
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: ['/trunks'] }, el(C.Sidebar))));
    await settle();
    const labels = [...state.host.querySelectorAll('.sidebar-nav a')].map((node) => node.textContent.trim());
    assert.equal(labels[labels.indexOf('Campaigns') + 1], 'Phone lines');
    assert.equal(find('a[href="/trunks"]').getAttribute('aria-current'), 'page');
});

test('menu: a tenant admin is not shown Phone lines', async () => {
    state.route = (request) => request.path.endsWith('/auth/me')
        ? response({ id: 'scoped', email: 'scoped@example.invalid', name: 'Scoped admin', role: 'tenant_admin', tenant_id: TENANT })
        : response({ detail: 'unrouted' }, 500);
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: ['/actions'] }, el(C.Sidebar))));
    await settle();
    assert.equal([...state.host.querySelectorAll('.sidebar-nav a')].map((node) => node.textContent.trim()).includes('Phone lines'), false);
});

test('route: /trunks is registered in App.tsx with the same guard as /campaigns', () => {
    const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8').replace(/\s+/g, ' ');
    const guarded = (path, page) => app.includes(`<Route path="${path}" element={ <AdminRouteGuard allowedRoles={PLATFORM_ROLES}> <${page} /> </AdminRouteGuard> } />`);
    assert.equal(guarded('/campaigns', 'CampaignsPage'), true);
    assert.equal(guarded('/trunks', 'TrunksPage'), true);
});

test('route: the guard keeps a tenant admin out of the page and reads nothing', async () => {
    api.setToken('access-A');
    state.route = (request) => request.path.endsWith('/auth/me')
        ? response({ id: 'scoped', email: 'scoped@example.invalid', name: 'Scoped admin', role: 'tenant_admin', tenant_id: TENANT })
        : response(pageOf([trunk(1)]));
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: ['/trunks'] },
        el(C.AdminRouteGuard, { allowedRoles: ['platform_admin', 'super_admin'] }, el(C.TrunksPage)))));
    await settle();
    assert.match(text(), /Access Denied/);
    assert.equal(listReads().length, 0);
});

test('page: a platform admin gets the Phone lines page with its list', async () => {
    api.setToken('access-A');
    state.route = (request) => request.path.endsWith('/auth/me') ? response(ADMIN)
        : isList(request) ? response(pageOf([trunk(1)])) : request.path.startsWith('/api/v1/admin/tenants') ? response(tenants) : response({ detail: 'unrouted' }, 500);
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: ['/trunks'] },
        el(C.AdminRouteGuard, { allowedRoles: ['platform_admin', 'super_admin'] }, el(C.TrunksPage)))));
    await settle(60);
    assert.equal(find('h1.page-title').textContent, 'Phone lines');
    assert.match(text(), /Line 1/);
});

test('table: the columns, plain values, and dates in the Campaigns format', async () => {
    route(() => response(pageOf([
        trunk(1),
        trunk(2, { is_active: false, auth_configured: false, role: null, direction: 'inbound', transport: 'tcp', port: 5061, last_tested_at: null, live_registration_status: null, live_status_checked_at: null }),
    ])));
    await draw();
    assert.deepEqual(headings(), ['Line', 'Account', 'Address', 'Direction', 'Role', 'Switched on', 'Sign-in details saved', 'Registration', 'Detail', 'Last checked', 'Last tested', 'Created']);
    assert.deepEqual(cells(0), ['Line 1', 'Synthetic tenant', 'sip1.carrier.invalid:5060 (udp)', 'outbound', 'primary', 'Yes', 'Yes', 'Registered', 'Not available',
        stamp('2026-11-17T08:00:00Z'), stamp('2026-11-16T08:00:00Z'), stamp('2026-10-21T09:30:00Z')]);
    assert.deepEqual(cells(1), ['Line 2', 'Synthetic tenant', 'sip2.carrier.invalid:5061 (tcp)', 'inbound', 'Not available', 'No', 'No', 'Not available', 'Not available',
        'Not available', 'Not available', stamp('2026-10-21T09:30:00Z')]);
    assert.deepEqual(Object.fromEntries(new URL(`https://x.invalid${listReads()[0].path}`).searchParams), { page: '1', page_size: '20' });
});

test('table: a rejected or not-registered line reads and looks different from a registered one', async () => {
    route(() => response(pageOf([
        trunk(1),
        trunk(2, { live_registration_status: 'rejected', live_status_detail: '403 Forbidden' }),
        trunk(3, { live_registration_status: 'unregistered' }),
        trunk(4, { live_registration_status: 'checking' }),
    ])));
    await draw();
    const badge = (row) => state.host.querySelectorAll('tbody tr')[row].querySelector('.call-status-badge');
    assert.equal(badge(0).textContent, 'Registered');
    assert.equal(badge(0).classList.contains('status-completed'), true);
    assert.equal(badge(1).textContent, 'Rejected');
    assert.equal(badge(1).classList.contains('status-failed'), true);
    assert.equal(cells(1)[8], '403 Forbidden');
    assert.equal(badge(2).textContent, 'Not registered');
    assert.equal(badge(2).classList.contains('status-failed'), true);
    assert.equal(badge(3).classList.contains('status-failed'), false);
    assert.equal(badge(3).classList.contains('status-completed'), false);
});

test('table: the detail is text only', async () => {
    route(() => response(pageOf([trunk(1, { live_registration_status: 'rejected', live_status_detail: '<img src=x onerror=alert(1)>' })])));
    await draw();
    assert.equal(find('tbody img') === null, true);
    assert.match(cells(0)[8], /<img src=x onerror=alert\(1\)>/);
});

test('privacy: no username or password is requested, shown or sent', async () => {
    route(() => response(pageOf([trunk(1, { auth_username: 'SECRETUSER', auth_password: 'SECRETPASS', auth_password_encrypted: 'ENCRYPTED' })])));
    await draw();
    await settle(300);
    await changeSelect('select[aria-label="Registration"]', 'rejected');
    for (const request of state.requests) {
        assert.doesNotMatch(`${request.path} ${JSON.stringify(request.body ?? '')}`, /password|username|secret|credential/i);
    }
    assert.doesNotMatch(text(), /SECRETUSER|SECRETPASS|ENCRYPTED|password|username/i);
    assert.equal(state.host.querySelectorAll('input[type="password"]').length, 0);
    assert.equal(state.requests.every((request) => request.method === 'GET'), true, 'the page only reads');
});

test('states: loading shows while the read is pending', async () => {
    let release;
    route(() => new Promise((done) => { release = () => done(response(pageOf([trunk(1)]))); }));
    await draw();
    assert.match(find('.table-loading').textContent, /Loading phone lines/);
    assert.equal(find('table') === null, true);
    await act(async () => release());
    await settle();
    assert.equal(find('.table-loading') === null, true);
    assert.match(text(), /Line 1/);
});

test('states: an empty answer shows the empty message and no paging', async () => {
    route(() => response(pageOf([], 0)));
    await draw();
    assert.match(find('.empty-state p').textContent, /No phone lines found/);
    assert.equal(find('table') === null, true);
    assert.equal(find('.pagination') === null, true);
});

test('states: a failed read shows the server message with Retry, and Retry reads again', async () => {
    let fail = true;
    route(() => fail ? response({ detail: 'SIP trunk list is temporarily unavailable' }, 503) : response(pageOf([trunk(1)])));
    await draw();
    assert.equal(find('.error-banner p').textContent, 'SIP trunk list is temporarily unavailable');
    assert.equal(find('table') === null, true);
    assert.equal(find('.empty-state') === null, true, 'a failed read never looks empty');
    const before = listReads().length;
    fail = false;
    await click('Retry');
    await settle();
    assert.equal(listReads().length, before + 1);
    assert.equal(find('.error-banner') === null, true);
    assert.match(text(), /Line 1/);
});

test('filters: switched on offers all, yes and no and sends is_active', async () => {
    route(() => response(pageOf([trunk(1)])));
    await draw();
    const options = [...find('select[aria-label="Switched on"]').options].map((option) => [option.value, option.textContent]);
    assert.deepEqual(options, [['', 'All'], ['true', 'Yes'], ['false', 'No']]);
    await changeSelect('select[aria-label="Switched on"]', 'false');
    assert.deepEqual(Object.fromEntries(lastQuery()), { page: '1', page_size: '20', is_active: 'false' });
    await changeSelect('select[aria-label="Switched on"]', 'true');
    assert.equal(lastQuery().get('is_active'), 'true');
    await changeSelect('select[aria-label="Switched on"]', '');
    assert.equal(lastQuery().has('is_active'), false);
});

test('filters: registration is a select of the values the server stores and sends the chosen one', async () => {
    route(() => response(pageOf([trunk(1)])));
    await draw();
    const options = [...find('select[aria-label="Registration"]').options].map((option) => option.value);
    assert.deepEqual(options, ['', 'registered', 'reachable', 'loaded', 'checking', 'unregistered', 'rejected', 'unreachable', 'missing_config', 'unknown', 'inactive']);
    assert.equal(find('input[aria-label="Registration"]') === null, true, 'not free text');
    await changeSelect('select[aria-label="Registration"]', 'rejected');
    assert.deepEqual(Object.fromEntries(lastQuery()), { page: '1', page_size: '20', registration: 'rejected' });
});

test('filters: the account filter sends tenant_id; all filters combine and Reset clears them', async () => {
    route(() => response(pageOf([trunk(1)])));
    await draw();
    await settle(300);
    await changeSelect('select[aria-label="Filter by tenant"]', TENANT);
    assert.deepEqual(Object.fromEntries(lastQuery()), { page: '1', page_size: '20', tenant_id: TENANT });
    await changeSelect('select[aria-label="Switched on"]', 'true');
    await changeSelect('select[aria-label="Registration"]', 'registered');
    assert.deepEqual(Object.fromEntries(lastQuery()), { page: '1', page_size: '20', tenant_id: TENANT, is_active: 'true', registration: 'registered' });
    await click('Reset');
    await settle();
    assert.deepEqual(Object.fromEntries(lastQuery()), { page: '1', page_size: '20' });
});

test('paging: 20 per page, next and previous send the page, the last page stops, a filter returns to page 1', async () => {
    const all = Array.from({ length: 45 }, (_, index) => trunk(index + 1));
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
    await act(async () => pager()[1].click()); await settle();
    assert.equal(lastQuery().get('page'), '3');
    assert.equal(state.host.querySelectorAll('tbody tr').length, 5);
    assert.equal(pager()[1].disabled, true);
    await act(async () => pager()[0].click()); await settle();
    assert.equal(lastQuery().get('page'), '2');
    await changeSelect('select[aria-label="Switched on"]', 'true');
    assert.deepEqual(Object.fromEntries(lastQuery()), { page: '1', page_size: '20', is_active: 'true' });
});
