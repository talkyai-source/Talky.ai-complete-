import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { el, act, dom, state, render, response, text, find, button, click, settle, setInput, deferred } from './bh-harness.mjs';

const built = await build({
    stdin: { contents: `export { InboundControlPage } from './src/pages/InboundControlPage';
        export { AuthProvider } from './src/lib/auth'; export { api } from './src/lib/api';
        export { MemoryRouter } from 'react-router-dom';`,
        resolveDir: fileURLToPath(new URL('..', import.meta.url)), loader: 'ts' },
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
    define: { 'import.meta.env': '{}' }, jsx: 'automatic', loader: { '.css': 'empty' },
});
const loaded = { exports: {} };
new Function('require', 'module', 'exports', built.outputFiles[0].text)(createRequire(import.meta.url), loaded, loaded.exports);
const O = loaded.exports;
test.beforeEach(() => O.api.setToken('synthetic-inbound-selector'));
test.afterEach(() => O.api.setToken(null));
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const tenantId = n => id(1000 + n);
const tenants = Array.from({ length: 251 }, (_, i) => ({ id: tenantId(i), business_name: `Tenant ${i}` }));
const assignments = [id(3000), id(3001)].map((id, i) => ({ id, tenant_id: tenantId(i),
    tenant_name: `Tenant ${i}`, campaign_id: `campaign-${i}`, campaign_name: `Campaign ${i}`,
    masked_did: `***${i}`, status: 'paused', version: 4, config_version: 1,
    readiness: { ready: true, blockers: [] }, last_call_at: null }));
const approval = { id: id(5000), assignment_id: id(3000), source_tenant_id: tenantId(0),
    target_tenant_id: tenantId(250), target_campaign_id: id(4000), requested_by: id(2001),
    status: 'pending', reason: 'Authorized customer move', requested_at: '2026-10-21T00:00:00Z' };

function route(extra = {}) {
    return (request) => {
        const custom = extra.route?.(request); if (custom !== undefined) return custom;
        const url = new URL(request.path, 'https://synthetic.invalid');
        if (url.pathname.endsWith('/auth/me')) return response({ id: id(2000), email: 'operator@example.invalid', role: 'platform_admin' });
        if (url.pathname.endsWith('/admin/inbound/overview')) return response({ active_assignments: 0, paused_assignments: 2,
            quarantined_assignments: 0, denied_last_24h: 0, controls: { version: 1, inbound_enabled: true,
                recording_enabled: false, transfer_enabled: false, settlement_enabled: true } });
        if (url.pathname.endsWith('/admin/inbound/assignments')) return response({ items: assignments, total: 2 });
        if (url.pathname.endsWith('/admin/inbound/reassignments')) return request.method === 'GET'
            ? response({ items: extra.approvals ?? [], has_more: false, next_cursor: null })
            : response({ id: 'created-request', status: 'pending', source_tenant_id: tenantId(0), ...request.body });
        if (url.pathname.includes('/admin/inbound/reassignments/')) return response((extra.approvals ?? []).find(row => row.id === url.pathname.split('/').at(-1)) ?? null);
        if (url.pathname.endsWith('/admin/tenants')) {
            const search = url.searchParams.get('search') ?? '';
            const matching = tenants.filter(item => item.business_name.includes(search));
            const offset = Number(url.searchParams.get('offset') ?? 0), limit = Number(url.searchParams.get('limit') ?? 200);
            return response(matching.slice(offset, offset + limit));
        }
        if (url.pathname.includes('/admin/tenants/')) {
            const item = tenants.find(item => item.id === url.pathname.split('/').at(-1));
            return item ? response(item) : response({ detail: 'Unknown fixture tenant' }, 404);
        }
        return response({ detail: 'Unrouted fixture request' }, 500);
    };
}
async function mount() {
    await render(el(O.AuthProvider, null, el(O.MemoryRouter, null, el(O.InboundControlPage)))); await settle();
}
async function select(selector, value) {
    await act(async () => {
        const node = find(selector); assert.ok(node, `Missing select ${selector}`);
        node.value = value; node.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
    });
}
async function textarea(selector, value) {
    await act(async () => {
        const node = find(selector); assert.ok(node);
        Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(node, value);
        node.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    });
}
async function openAssignment(index = 0) {
    await act(async () => [...state.host.querySelectorAll('button')].filter(node => node.textContent.trim() === 'Reassign')[index].click());
    await settle();
}

test('inbound inventory can search and filter a tenant beyond the first200', async () => {
    state.route = route(); await mount();
    const search = find('[aria-label="Search inbound tenant options"]'); assert.ok(search, 'Inbound tenant search is missing');
    await setInput(search, 'Tenant 250'); await settle(300);
    await select('[aria-label="Filter inbound assignments by tenant"]', tenantId(250));
    assert.ok(state.requests.some(request => request.path.includes(`/admin/inbound/assignments?tenant_id=${tenantId(250)}`)));
    assert.equal(find('[aria-label="Filter inbound assignments by tenant"]').value, tenantId(250));
});

test('reassignment pages beyond first200 and submits exact selected target; source cannot be selected', async () => {
    state.route = route(); await mount(); await openAssignment();
    const panel = find('[aria-labelledby="reassign-heading"]');
    assert.ok(panel.querySelector('[aria-label="Search target tenant options"]'), 'Target tenant search is missing');
    assert.ok(![...panel.querySelector('select').options].some(option => option.value === tenantId(0)));
    for (let page = 1; page <= 4; page += 1) {
        await act(async () => [...panel.querySelectorAll('button')].find(node => node.textContent === 'Next tenant options').click());
        await settle();
    }
    await select('#target-tenant', tenantId(205));
    await setInput(find('#target-campaign'), id(4000));
    await textarea('#reassignment-reason', 'Reviewed synthetic ownership move');
    await click('Submit for approval');
    const request = state.requests.find(request => request.method === 'POST');
    assert.equal(request.body.target_tenant_id, tenantId(205));
    assert.equal(request.body.assignment_id, id(3000));
    assert.equal(request.body.expected_version, 4);
});

test('opening another assignment discards the previous target, campaign and reason', async () => {
    state.route = route(); await mount(); await openAssignment();
    await select('#target-tenant', tenantId(2));
    await setInput(find('#target-campaign'), 'old-campaign');
    await textarea('#reassignment-reason', 'Old assignment reason');
    await click('Cancel'); await openAssignment(1);
    assert.equal(find('#target-tenant').value, '');
    assert.equal(find('#target-campaign').value, '');
    assert.equal(find('#reassignment-reason').value, '');
    assert.equal(state.requests.filter(request => request.method !== 'GET').length, 0);
});

test('approval looks up both exact tenants outside the first page and blocks on unavailable details', async () => {
    let unavailable = true;
    state.route = route({ approvals: [approval], route: request => request.path.endsWith(`/admin/tenants/${tenantId(250)}`) && unavailable
        ? response({ detail: 'Synthetic target lookup unavailable' }, 503) : undefined });
    await mount(); await click('Review reassignment'); await textarea('#reassignment-review-reason', 'Independently verified ownership');
    assert.ok(state.requests.some(request => request.path.endsWith(`/admin/tenants/${tenantId(250)}`)), 'Review must resolve exact target');
    assert.equal(button('Approve reassignment').disabled, true);
    assert.match(text(), /Synthetic target lookup unavailable/);
    unavailable = false; await click('Retry tenant details'); await settle();
    assert.match(find('[aria-labelledby="reassignment-review-heading"]').textContent, /Tenant 250/);
    assert.equal(button('Approve reassignment').disabled, false);
    assert.equal(state.requests.filter(request => request.method !== 'GET').length, 0);
});

test('late tenant lookup from a closed review cannot replace the next reviewed target', async () => {
    const old = deferred();
    const second = { ...approval, id: id(5001), target_tenant_id: tenantId(249) };
    state.route = route({ approvals: [approval, second], route: request => request.path.endsWith(`/admin/tenants/${tenantId(250)}`) ? old.promise : undefined });
    await mount(); await click('Review reassignment'); await click('Cancel review');
    await act(async () => [...state.host.querySelectorAll('button')].filter(node => node.textContent === 'Review reassignment')[1].click());
    await settle(); await act(async () => old.resolve(response(tenants[250])));
    const panel = find('[aria-labelledby="reassignment-review-heading"]');
    assert.match(panel.textContent, /Tenant 249/); assert.doesNotMatch(panel.textContent, /Tenant 250/);
});

test('malformed successful tenant detail cannot crash the review or enable approval', async () => {
    let malformed = true;
    state.route = route({ approvals: [approval], route: request => request.path.endsWith(`/admin/tenants/${tenantId(250)}`) && malformed
        ? response({ id: tenantId(250), business_name: {} }) : undefined });
    await mount(); await click('Review reassignment'); await textarea('#reassignment-review-reason', 'Independently verified ownership');
    assert.match(text(), /Tenant details do not match the reviewed request/);
    assert.equal(button('Approve reassignment').disabled, true);
    malformed = false; await click('Retry tenant details'); await settle();
    assert.match(find('[aria-labelledby="reassignment-review-heading"]').textContent, /Tenant 250/);
    assert.equal(button('Approve reassignment').disabled, false);
    assert.equal(state.requests.filter(request => request.method !== 'GET').length, 0);
});
