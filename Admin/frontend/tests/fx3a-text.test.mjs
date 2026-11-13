// FX3a Tasks 1.10 and 6 — text and labels in the Admin app: the brand name
// and sign-in button, a tenant's status badge, the pause button and an
// incident's severity and status.
import assert from 'node:assert/strict';
import test from 'node:test';
import { Routes, Route } from 'react-router-dom';
import { C, api, el, dom, state, render, response, find, settle } from './bh-harness.mjs';

const labels = () => [...state.host.querySelectorAll('button')].map((b) => b.textContent.trim());

// Login ----------------------------------------------------------------------

test('login: the brand reads Talk-Lee and the button reads Sign in', async () => {
    state.mfaProof = false; api.setToken(null); localStorage.removeItem('admin_token');
    state.route = () => response({ detail: 'Authentication required' }, 401);
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: ['/login'] },
        el(Routes, null, el(Route, { path: '/login', element: el(C.LoginPage) })))));
    await settle();
    assert.equal(find('.login-logo-text').textContent, 'Talk-Lee');
    assert.equal(state.host.textContent.includes('Talk-lee'), false);
    assert.equal(labels().includes('Sign in'), true);
    assert.equal(labels().includes('Sign In'), false);
});

// Tenants --------------------------------------------------------------------

const tenant = (status) => ({
    id: '10000000-0000-4000-8000-000000000001', business_name: 'Acme Ltd', plan_id: 'p1', plan_name: 'Pro',
    minutes_used: 40, minutes_allocated: 500, status, user_count: 2, campaign_count: 1,
    max_concurrent_calls: 5, created_at: null, updated_at: '2026-10-21T00:00:00Z',
});

test('tenants: a past_due status badge reads Past due, as the filter does', async () => {
    state.route = () => response({ detail: 'unrouted' }, 500);
    await render(el(C.TenantsTable, { tenants: [tenant('past_due')], loading: false, onRefresh() {}, searchTerm: '',
        onSearchChange() {}, statusFilter: '', onStatusFilterChange() {} }));
    const cells = [...state.host.querySelectorAll('tbody td span')].map((node) => node.textContent.trim());
    assert.equal(cells.includes('Past due'), true);
    assert.equal(cells.includes('Past_due'), false);
    const option = [...state.host.querySelectorAll('option')].find((node) => node.value === 'past_due');
    assert.equal(option.textContent, 'Past due');
});

test('tenants: a one-word status badge is unchanged', async () => {
    state.route = () => response({ detail: 'unrouted' }, 500);
    await render(el(C.TenantsTable, { tenants: [tenant('active')], loading: false, onRefresh() {}, searchTerm: '',
        onSearchChange() {}, statusFilter: '', onStatusFilterChange() {} }));
    const cells = [...state.host.querySelectorAll('tbody td span')].map((node) => node.textContent.trim());
    assert.equal(cells.includes('Active'), true);
});

// Live calls -----------------------------------------------------------------

test('live calls: the pause button is sentence case', async () => {
    const realSetInterval = dom.window.setInterval.bind(dom.window);
    const realClearInterval = dom.window.clearInterval.bind(dom.window);
    dom.window.setInterval = () => 1; dom.window.clearInterval = () => {};
    try {
        state.route = (call) => call.path.endsWith('/admin/calls/live') ? response([])
            : call.path.endsWith('/admin/calls/pause-status') ? response({ paused: false, reason: null, paused_at: null, paused_by: null })
                : response({ detail: 'unrouted' }, 500);
        await render(el(C.LiveCalls));
        await settle();
        assert.equal(labels().includes('Pause all calls'), true);
        assert.equal(labels().includes('Pause All Calls'), false);
    } finally {
        dom.window.setInterval = realSetInterval; dom.window.clearInterval = realClearInterval;
    }
});

// Incidents ------------------------------------------------------------------

test('incidents: a row shows severity and status as the filters name them', async () => {
    const item = { id: 'one', title: 'Incident one', severity: 'critical', status: 'open',
        description: 'An actual recorded condition', triggered_at: '2026-10-21T00:00:00Z', acknowledged_at: null,
        acknowledged_by: null, resolved_at: null, resolved_by: null };
    state.route = (r) => {
        if (r.path.endsWith('/auth/me')) return response({ id: 'admin', email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null });
        if (r.path.includes('/admin/incidents?')) return response({ items: [item], total: 1, page: 1, page_size: 20, open_count: 1, critical_count: 1 });
        return response({ detail: 'unrouted synthetic request' }, 500);
    };
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.IncidentsPage))));
    await settle();
    const cells = [...state.host.querySelectorAll('tbody tr td')].map((node) => node.textContent.trim());
    assert.equal(cells[1], 'Critical');
    assert.equal(cells[2], 'Open');
    assert.equal(cells.includes('critical'), false);
    assert.equal(cells.includes('open'), false);
});
