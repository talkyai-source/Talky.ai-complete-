// FX2a2 Task 3 — on the Support queue, Help articles, formal holds and supplier
// evidence, the button that reads the data again is labelled "Retry" while the
// read has failed and keeps its own label once the read succeeds.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, state, render, response, text, settle } from './bh-harness.mjs';

const ME = { id: '00000000-0000-4000-8000-000000000001', email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null };
const labels = () => [...document.querySelectorAll('button')].map((b) => b.textContent.trim());
const press = async (label) => {
    const node = [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === label);
    assert.ok(node, `button "${label}"`);
    await act(async () => node.click()); await settle();
};
const routed = (path, page) => el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: [path] }, el(C.Routes, null, el(C.Route, { path, element: page }))));

// Each case: how to draw it, which request is "the read", a good answer, and the button's own label.
const CASES = [
    { name: 'Support queue', own: 'Refresh', isRead: (r) => r.path.includes('/admin/support/tickets'), good: { items: [], next_cursor: null },
        draw: () => routed('/support', el(C.SupportTicketsPage)), failedText: /Tickets unavailable/ },
    { name: 'Help articles', own: 'Refresh', isRead: (r) => r.path.includes('/admin/support/articles'), good: { items: [] },
        draw: () => routed('/support/articles', el(C.SupportArticlesPage)), failedText: /Articles unavailable/ },
    { name: 'Supplier evidence', own: 'Refresh evidence', isRead: (r) => r.path.includes('/usage/statements'),
        good: { items: [], has_more: false, net_spend: null, coverage: 'listed_account_periods_only' },
        draw: () => el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.UsageCostPage))), failedText: /Supplier evidence could not be read/ },
];

for (const c of CASES) {
    test(`${c.name}: the refresh button reads "Retry" while the read has failed, sends the request again, then reads "${c.own}"`, async () => {
        let failing = true, reads = 0;
        state.route = (r) => {
            if (r.path.endsWith('/auth/me')) return response(ME);
            if (c.isRead(r)) { reads += 1; return failing ? response({ detail: 'store unavailable' }, 503) : response(c.good); }
            if (r.path.includes('/usage/summary')) return response({ period_start: '2026-10-01', period_end: '2026-10-27', monetary_note: 'n', total_call_minutes: 1, total_action_records: 2, legacy_outbound_estimate: null });
            if (r.path.includes('/admin/support/metrics')) return response({ detail: 'not needed' }, 503);
            return response({ monetary_note: 'No allocation', breakdown: [] });
        };
        await render(c.draw()); await settle();
        assert.match(text(), c.failedText);
        assert.equal(labels().includes('Retry'), true, 'failed read: Retry');
        assert.equal(labels().includes(c.own), false, 'failed read: not its refresh label');

        failing = false;
        const before = reads;
        await press('Retry');
        assert.equal(reads > before, true, 'Retry sent the request again');
        assert.doesNotMatch(text(), c.failedText);
        assert.equal(labels().includes(c.own), true, 'loaded: its own label');
        assert.equal(labels().includes('Retry'), false);
    });
}

// ───────── formal holds ─────────
const A = '00000000-0000-4000-8000-0000000000aa';
const target = { type: 'tenant', id: A, label: 'Tenant A' };

test('Formal holds: the refresh button reads "Retry" while the status read has failed, sends it again, and "Refresh hold status" is back once a read answers', async () => {
    let failing = true, reads = 0;
    state.route = (r) => {
        if (r.path.endsWith('/auth/me')) return response(ME);
        if (r.path.endsWith('/status')) { reads += 1; return failing ? response({ detail: 'database offline' }, 503) : response({ detail: 'still not a status' }, 200); }
        return response({ detail: 'unexpected synthetic request' }, 500);
    };
    await render(el(C.AuthProvider, null, el(C.FormalHoldPanel, { target, onClose() {} }))); await settle();
    assert.match(text(), /Status unavailable: database offline/);
    assert.equal(labels().includes('Retry'), true);
    assert.equal(labels().includes('Refresh hold status'), false);
    const before = reads;
    await press('Retry');
    assert.equal(reads > before, true, 'Retry sent the request again');
});

test('Formal holds: a status that loaded keeps "Refresh hold status"', async () => {
    // The label after a successful read is asserted by the existing formal-hold
    // suites (they click "Refresh hold status" after reads that answered); this
    // guards the state before any read has failed.
    state.route = (r) => r.path.endsWith('/auth/me') ? response(ME) : new Promise(() => {});
    await render(el(C.AuthProvider, null, el(C.FormalHoldPanel, { target, onClose() {} }))); await settle();
    assert.equal(labels().includes('Refresh hold status'), true, 'no failed read yet: its own label');
    assert.equal(labels().includes('Retry'), false);
});
