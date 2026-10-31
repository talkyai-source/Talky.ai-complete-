// Support console pages through the real React renderer with a stubbed API:
// role denial, the queue's honest gaps, the 409 reload path, a retry that
// reuses its request_id, and metrics with null figures.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, state, render, response, find, text, click, button, settle } from './bh-harness.mjs';

const PLATFORM = { id: 'admin-A', email: 'a@example.invalid', name: 'Admin A', role: 'platform_admin', tenant_id: null };
const TENANT_ADMIN = { ...PLATFORM, id: 'tenant-B', role: 'tenant_admin', tenant_id: 'tenant-uuid' };
const stamp = '2026-10-30T10:00:00+00:00';
const sla = { first_response_due_at: '2026-10-31T10:00:00+00:00', resolution_due_at: '2026-11-02T10:00:00+00:00', breached: false };
const item = (over = {}) => ({ id: 'ticket-1', number: 'SUP-000123', subject: 'Campaign stuck', status: 'open', priority: 'high',
    category: 'calls_campaigns', updated_at: stamp, created_at: stamp, unread: false,
    tenant: { id: '6b1d0d8e-2222-4bbb-9ccc-000000000002', name: 'Acme' }, assignee: null, sla, ...over });
const detail = (over = {}) => ({ ...item(), version: 3, requester: { id: 'u1', name: 'Pat', email: 'pat@example.invalid' }, related: {},
    messages: [{ id: 'm1', author_type: 'customer', author_name: 'Pat', body: 'It is stuck.', attachments: [], created_at: stamp, is_internal: false },
        { id: 'm2', author_type: 'staff', author_name: 'Sam', body: 'Looking.', attachments: [], created_at: stamp, is_internal: true }],
    csat: null, allowed_actions: [], tenant: { id: item().tenant.id, name: 'Acme', plan: 'Growth' }, assignee: null,
    sla: { ...sla, first_responded_at: null }, conversation: [], diagnostics: { source: 'ticket_form' }, events: [], ...over });
const guard = (page, roles) => el(C.AdminRouteGuard, roles ? { allowedRoles: roles } : null, page);
const PLATFORM_ROLES = ['platform_admin', 'super_admin'];

async function draw(path, me, extraRoutes = []) {
    state.route = (request) => request.path.endsWith('/auth/me') ? response(me) : state.support(request);
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: [path] },
        el(C.Routes, null,
            el(C.Route, { path: '/support', element: guard(el(C.SupportTicketsPage), PLATFORM_ROLES) }),
            el(C.Route, { path: '/support/articles', element: guard(el(C.SupportArticlesPage), PLATFORM_ROLES) }),
            el(C.Route, { path: '/support/:id', element: guard(el(C.SupportTicketPage), PLATFORM_ROLES) }),
            el(C.Route, { path: '/login', element: el('p', null, 'login page') }),
            ...extraRoutes))));
    await settle();
}
const supportRequests = () => state.requests.filter((request) => request.path.includes('/admin/support'));

test('a tenant admin is denied /support, sees no Support link, and no support request is made', async () => {
    state.support = () => response({ detail: 'must not be called' }, 500);
    await draw('/support', TENANT_ADMIN, [el(C.Route, { path: '/actions', element: el(C.Sidebar) })]);
    assert.match(text(), /Access Denied/);
    assert.equal(find('a[href="/support"]'), null);
    assert.equal(supportRequests().length, 0);
    assert.equal(C.canAccessAdminRoute('/support', 'tenant_admin'), false);
    assert.equal(C.canAccessAdminRoute('/support/ticket-1', 'tenant_admin'), false);
    assert.equal(C.canAccessAdminRoute('/support/articles', 'platform_admin'), true);
});

test('the queue lists tickets, states the requester and sort limits, colours deadlines, and pages by cursor', async () => {
    state.support = (request) => {
        const url = new URL(request.path, 'https://fixture.invalid');
        if (url.searchParams.get('cursor') === 'next-1') return response({ items: [item({ id: 'ticket-2', number: 'SUP-000124', subject: 'Second page' })], next_cursor: null });
        return response({ items: [item(), item({ id: 'ticket-3', number: 'SUP-000125', subject: 'Breached one', sla: { ...sla, breached: true } })], next_cursor: 'next-1' });
    };
    await draw('/support?status=open&priority=high', PLATFORM);
    assert.match(text(), /SUP-000123/);
    assert.match(text(), /not provided by the server/);
    assert.match(text(), /reorders only the page shown here/);
    assert.ok(find('td.support-deadline.overdue'), 'breached ticket is marked overdue');
    assert.ok(find('a[href="/support/ticket-1"]'));
    assert.ok(find('a[href="/tenants"]'), 'company links open the Tenants list');
    const first = supportRequests()[0];
    assert.match(first.path, /\/admin\/support\/tickets\?status=open&priority=high$/);
    await click('Next page');
    assert.match(text(), /Second page/);
    assert.match(supportRequests().at(-1).path, /cursor=next-1/);
    assert.match(text(), /Page 2/);
    await click('Previous page');
    assert.match(text(), /SUP-000123/);
});

test('a stale version 409 shows the conflict with Reload and never resends the change', async () => {
    let reads = 0;
    state.support = (request) => {
        if (request.method === 'PATCH') return response({ error: { code: 'version_conflict', message: 'This ticket changed after you loaded it. Reload it before saving.', details: null, request_id: 'r' } }, 409);
        reads += 1;
        return response(detail({ version: 3 + reads }));
    };
    await draw('/support/ticket-1', PLATFORM);
    assert.match(text(), /SUP-000123/);
    assert.match(text(), /Internal note · not visible to the customer/);
    const loadedVersion = 3 + reads; // StrictMode may run the load effect twice; the shown version is the last read
    await click('Assign to me');
    assert.match(text(), /This ticket was changed by someone else/);
    const patches = () => state.requests.filter((request) => request.method === 'PATCH');
    assert.equal(patches().length, 1);
    assert.deepEqual(patches()[0].body, { request_id: patches()[0].body.request_id, version: loadedVersion, assignee_user_id: 'admin-A' });
    assert.match(patches()[0].body.request_id, /^[0-9a-f-]{36}$/);
    assert.ok(button('Reload'));
    // While the conflict stands nothing can be saved.
    assert.equal(button('Assign to me').disabled, true);
    const readsBefore = reads;
    await click('Reload');
    assert.equal(reads, readsBefore + 1);
    assert.equal(patches().length, 1, 'a reload only reads; it never replays the refused change');
    assert.doesNotMatch(text(), /This ticket was changed by someone else/);
    assert.equal(button('Assign to me').disabled, false);
});

test('a successful PATCH replaces the ticket and a retried failure reuses its request_id', async () => {
    let attempt = 0;
    state.support = (request) => {
        if (request.method === 'PATCH') {
            attempt += 1;
            if (attempt === 1) return response({ detail: 'Receipt delivery failed' }, 503);
            return response(detail({ version: 4, assignee: { id: 'admin-A', name: 'Admin A', email: 'a@example.invalid' } }));
        }
        if (request.method === 'POST') {
            return attempt < 3 && (attempt += 1) ? response({ detail: 'storage unavailable' }, 503)
                : response({ id: 'm3', author_type: 'staff', author_name: 'Admin A', body: 'Hello', attachments: [], created_at: stamp, is_internal: false }, 201);
        }
        return response(detail());
    };
    await draw('/support/ticket-1', PLATFORM);
    await click('Assign to me');
    assert.match(text(), /Retry sends the same request/);
    await click('Assign to me');
    const patches = state.requests.filter((request) => request.method === 'PATCH');
    assert.equal(patches.length, 2);
    assert.equal(patches[0].body.request_id, patches[1].body.request_id, 'the retry reuses the request_id');
    assert.match(text(), /Saved\./);
    assert.match(text(), /Admin A/);
});

test('a reply that fails is sent again with the same request_id, then clears on success', async () => {
    let posts = 0;
    state.support = (request) => {
        if (request.method === 'POST') {
            posts += 1;
            if (posts === 1) return response({ detail: 'storage unavailable' }, 503);
            return response({ id: 'm3', author_type: 'staff', author_name: 'Admin A', body: request.body.body, attachments: [], created_at: stamp, is_internal: request.body.internal }, 201);
        }
        return response(detail());
    };
    await draw('/support/ticket-1', PLATFORM);
    const area = find('#support-composer-body');
    await act(async () => {
        Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set.call(area, 'Hello from staff');
        area.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    await click('Send reply');
    assert.match(text(), /storage unavailable/);
    await click('Send again (same request)');
    const sent = state.requests.filter((request) => request.method === 'POST');
    assert.equal(sent.length, 2);
    assert.equal(sent[0].body.request_id, sent[1].body.request_id);
    assert.deepEqual(sent[1].body, { request_id: sent[0].body.request_id, body: 'Hello from staff', internal: false, attachment_ids: [] });
    assert.equal(find('#support-composer-body').value, '');
});

test('the metrics card renders null figures as unavailable and a 7/30 toggle re-reads', async () => {
    state.route = (request) => request.path.endsWith('/auth/me') ? response(PLATFORM)
        : response({ days: Number(new URL(request.path, 'https://x.invalid').searchParams.get('days')), conversations: 0, bot_solved_pct: null,
            tickets_created: 0, open_by_priority: { urgent: 0, high: 0, normal: 0, low: 0 }, sla_breaches: 0,
            median_first_response_minutes: null, avg_csat: null });
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.SupportMetricsCard))));
    await settle();
    assert.equal((text().match(/—/g) ?? []).length, 3, 'three unavailable figures');
    assert.match(text(), /Open tickets0/);
    await click('30 days');
    assert.ok(state.requests.some((request) => request.path.endsWith('/admin/support/metrics?days=30')));
});

test('articles: docs rows are read-only with the repository note; a staff row can be unpublished after confirmation', async () => {
    const row = (over = {}) => ({ id: 'a1', slug: 'refunds', title: 'Refunds', section: '', body_md: '# Refunds', category: 'billing', source: 'staff',
        published: true, url: '/support/articles/refunds', updated_by: 'admin-A', created_at: stamp, updated_at: stamp, ...over });
    state.support = (request) => request.method === 'PATCH'
        ? response(row({ published: request.body.published }))
        : response({ items: [row(), row({ id: 'a2', slug: 'docs-billing-intro', title: 'Billing', source: 'docs', url: '/docs/billing' })] });
    await draw('/support/articles', PLATFORM);
    assert.match(text(), /Documentation \(read-only\)/);
    await click('View');
    assert.match(text(), /Edit this in the repository/);
    assert.equal(find('#support-article-title').disabled, true);
    await click('Close');
    await click('Unpublish');
    assert.match(text(), /Unpublish this article\?/);
    await act(async () => button('Unpublish').click());
    await act(async () => [...document.querySelectorAll('button')].find((node) => node.textContent.trim() === 'Unpublish' && node.closest('.modal-content'))?.click());
    await settle();
    const patch = state.requests.find((request) => request.method === 'PATCH');
    assert.ok(patch, 'the unpublish PATCH was sent');
    assert.equal(patch.body.published, false);
    assert.match(patch.body.request_id, /^[0-9a-f-]{36}$/);
});

test.after(() => { api.setToken(null); });
