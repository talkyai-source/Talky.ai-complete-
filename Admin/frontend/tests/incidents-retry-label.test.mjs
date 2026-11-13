// FX2a Task 6 — the button that reads the incident list again is labelled
// "Retry" (as on Tenants) while the list read has failed, and "Refresh list"
// while the list loaded fine.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, state, render, response, click, settle } from './bh-harness.mjs';
const stamp = '2026-10-21T00:00:00Z';
const item = { id: 'one', title: 'Incident one', severity: 'critical', status: 'open',
    description: 'An actual recorded condition', triggered_at: stamp, acknowledged_at: null,
    acknowledged_by: null, resolved_at: null, resolved_by: null };
const list = { items: [item], total: 1, page: 1, page_size: 20, open_count: 1, critical_count: 1 };
let failing, listReads;
test.beforeEach(() => {
    failing = false; listReads = 0;
    state.route = (r) => {
        if (r.path.endsWith('/auth/me')) return response({ id: 'admin', email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null });
        if (r.path.includes('/admin/incidents?')) { listReads += 1; return failing ? response({ detail: 'Incident store unavailable' }, 503) : response(list); }
        return response({ detail: 'unrouted synthetic request' }, 500);
    };
});
const labels = () => [...state.host.querySelectorAll('button')].map((b) => b.textContent.trim());
const draw = async () => { await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.IncidentsPage)))); await settle(); };

test('a failed incident list read is retried from a button labelled "Retry", which sends the request again', async () => {
    failing = true;
    await draw();
    assert.match(state.host.textContent, /Incident records unavailable: Incident store unavailable/);
    assert.equal(labels().includes('Retry'), true);
    assert.equal(labels().includes('Refresh list'), false);

    failing = false;
    const before = listReads;
    await click('Retry'); await settle();
    assert.equal(listReads > before, true, 'Retry read the list again');
    assert.match(state.host.textContent, /Incident one/);
    assert.equal(labels().includes('Refresh list'), true, 'a list that loaded is refreshed, not retried');
    assert.equal(labels().includes('Retry'), false);
});

test('a list that loaded fine keeps its "Refresh list" button', async () => {
    await draw();
    assert.match(state.host.textContent, /Incident one/);
    assert.equal(labels().includes('Refresh list'), true);
    assert.equal(labels().includes('Retry'), false);
});
