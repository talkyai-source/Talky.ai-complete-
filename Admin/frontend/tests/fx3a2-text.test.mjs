// FX3a2 Tasks 2 and 3.5 — Admin text: the sidebar brand, the resume button and
// the saved tenant change banner (tenant name and a readable time).
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, dom, state, render, response, find, click, settle } from './bh-harness.mjs';

const labels = () => [...state.host.querySelectorAll('button')].map((b) => b.textContent.trim());

test('sidebar: the brand reads Talk-Lee', async () => {
    state.route = (r) => r.path.endsWith('/auth/me')
        ? response({ id: 'admin', email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null })
        : response({ detail: 'unrouted' }, 500);
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.Sidebar))));
    await settle();
    assert.equal(find('.sidebar-logo-text').textContent, 'Talk-Lee');
    assert.equal(state.host.textContent.includes('Talk-lee'), false);
});

test('live calls: while paused the button reads Resume calls', async () => {
    const realSetInterval = dom.window.setInterval.bind(dom.window);
    const realClearInterval = dom.window.clearInterval.bind(dom.window);
    dom.window.setInterval = () => 1; dom.window.clearInterval = () => {};
    try {
        state.route = (call) => call.path.endsWith('/admin/calls/live') ? response([])
            : call.path.endsWith('/admin/calls/pause-status') ? response({ paused: true, reason: 'Maintenance', paused_at: null, paused_by: null })
                : response({ detail: 'unrouted' }, 500);
        await render(el(C.LiveCalls));
        await settle();
        assert.equal(labels().includes('Resume calls'), true);
        assert.equal(labels().includes('Resume Calls'), false);
    } finally {
        dom.window.setInterval = realSetInterval; dom.window.clearInterval = realClearInterval;
    }
});

const TENANT_ID = '10000000-0000-4000-8000-000000000001';
const SAVED_AT = '2026-10-21T00:01:00Z';
const tenant = {
    id: TENANT_ID, business_name: 'Acme Ltd', plan_id: 'p1', plan_name: 'Pro',
    minutes_used: 40, minutes_allocated: 500, status: 'active', user_count: 2, campaign_count: 1,
    max_concurrent_calls: 5, created_at: null, updated_at: '2026-10-21T00:00:00Z',
};

test('tenants: the saved change banner names the tenant and shows a readable time', async () => {
    state.route = () => response({ id: TENANT_ID, detail: 'ok', status: 'suspended', changed: true, updated_at: SAVED_AT, audit_event_id: 'audit-fixture' });
    await render(el(C.TenantsTable, { tenants: [tenant], loading: false, onRefresh() {}, searchTerm: '',
        onSearchChange() {}, statusFilter: '', onStatusFilterChange() {} }));
    await act(async () => find('.action-menu-btn').click());
    await click('Suspend subscription');
    await click('Suspend');
    const banner = find('[aria-label="Saved tenant change"]');
    assert.equal(banner !== null, true);
    const text = banner.textContent;
    assert.equal(text.includes('Saved change confirmed for Acme Ltd. '), true);
    assert.equal(text.includes(TENANT_ID), false);
    assert.equal(text.includes(`Saved version: ${new Date(SAVED_AT).toLocaleString()}`), true);
    assert.equal(text.includes(SAVED_AT), false);
});
