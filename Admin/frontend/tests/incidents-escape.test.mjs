// FX1a Task 4.2 — one Escape closes only the Acknowledge confirmation, not the
// incident details dialog under it; the next Escape closes the details.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, dom, state, render, response, find, click, settle } from './bh-harness.mjs';
const stamp = '2026-10-21T00:00:00Z';
const item = { id: 'one', title: 'Incident one', severity: 'critical', status: 'open',
    description: 'An actual recorded condition', triggered_at: stamp, acknowledged_at: null,
    acknowledged_by: null, resolved_at: null, resolved_by: null };
const list = { items: [item], total: 1, page: 1, page_size: 20, open_count: 1, critical_count: 1 };
const requests = [];
test.beforeEach(() => {
    requests.length = 0;
    state.route = (r) => {
        requests.push(`${r.method ?? 'GET'} ${r.path}`);
        if (r.path.endsWith('/auth/me')) return response({ id: 'admin', email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null });
        if (r.path.includes('/admin/incidents?')) return response(list);
        if (r.path.endsWith('/admin/incidents/one')) return response(item);
        return response({ detail: 'unrouted synthetic request' }, 500);
    };
});
const escape = async () => { await act(async () => document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))); await settle(); };
const dialogs = () => [...state.host.ownerDocument.querySelectorAll('[role="dialog"], [role="alertdialog"]')];
const detailsOpen = () => dialogs().some((d) => /Incident details/.test(d.textContent));
const confirmationOpen = () => state.host.ownerDocument.body.textContent.includes('This records ownership of the incident');

test('one Escape closes only the Acknowledge confirmation and leaves the incident details open', async () => {
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.IncidentsPage)))); await settle();
    await act(async () => find('[aria-label="View incident Incident one"]').click()); await settle();
    assert.equal(detailsOpen(), true);
    await click('Acknowledge');
    assert.equal(confirmationOpen(), true);

    await escape();
    assert.equal(confirmationOpen(), false, 'the confirmation closed');
    assert.equal(detailsOpen(), true, 'the details dialog under it stayed open');
    assert.equal(requests.some((r) => /acknowledge/.test(r)), false, 'Escape never acknowledges');

    await escape();
    assert.equal(detailsOpen(), false, 'the next Escape closes the details dialog');
});
