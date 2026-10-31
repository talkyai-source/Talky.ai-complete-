// Actual ReactDOM; HTTP responses are synthetic and sockets forbidden by the
// shared harness. These controls do not prove a native browser or backend run.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, state, render, response, find, click, settle, button } from './bh-harness.mjs';

const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const incidentId = id(1), actor = id(2), originalActor = id(3);
const triggeredAt = '2026-10-21T00:00:00Z', acknowledgedAt = '2026-10-21T01:00:00Z', resolvedAt = '2026-10-21T02:00:00Z';
const incident = (status = 'open') => ({ id: incidentId, title: 'Owned recovery incident', severity: 'warning', status,
    description: 'One recorded operator condition', triggered_at: triggeredAt,
    acknowledged_at: status === 'acknowledged' ? acknowledgedAt : null,
    acknowledged_by: status === 'acknowledged' ? originalActor : null,
    resolved_at: status === 'resolved' ? resolvedAt : null, resolved_by: status === 'resolved' ? originalActor : null });
const receipt = (status = 'acknowledged') => ({ success: true, incident_id: incidentId, status, message: `Incident ${status}`,
    acknowledged_at: status === 'acknowledged' ? acknowledgedAt : null,
    acknowledged_by: status === 'acknowledged' ? originalActor : null,
    resolved_at: status === 'resolved' ? resolvedAt : null, resolved_by: status === 'resolved' ? originalActor : null });
const list = items => ({ items, total: items.length, page: 1, page_size: 20,
    open_count: items.filter(row => row.status === 'open').length, critical_count: 0 });
const exactPath = `/admin/incidents/${incidentId}`;
const posts = () => state.requests.filter(request => request.method === 'POST');
const dialog = () => find('[role="dialog"]');
let saved, override;
test.beforeEach(() => {
    saved = incident(); override = null;
    state.route = request => {
        const custom = override?.(request); if (custom !== undefined) return custom;
        if (request.path.endsWith('/auth/me')) return response({ id: actor, email: 'operator@example.invalid', role: 'platform_admin', tenant_id: null });
        if (request.path.includes('/admin/incidents?')) return response(list(saved.status === 'open' ? [saved] : []));
        if (request.path.endsWith(exactPath)) return response(saved);
        return response({ detail: 'Unrouted synthetic request' }, 500);
    };
});
async function mountAndOpen() {
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.IncidentsPage)))); await settle();
    const view = find('[aria-label="View incident Owned recovery incident"]'); assert.ok(view);
    view.focus(); await act(async () => view.click()); await settle(); return view;
}
async function resolve() { await click('Resolve'); await click('Confirm resolution'); await settle(); }

test('incident recovery baseline: a valid exact resolved read retires the obsolete unknown error and confirmation', async () => {
    let unavailable = false;
    override = request => {
        if (request.method === 'POST') { saved = incident('resolved'); throw new Error('Synthetic resolve delivery lost'); }
        if (unavailable && request.path.endsWith(exactPath)) return response({ detail: 'Exact incident temporarily unavailable' }, 503);
    };
    await mountAndOpen(); await resolve();
    assert.match(dialog().textContent, /Synthetic resolve delivery lost/);
    unavailable = true; await click('Refresh record'); await settle();
    assert.match(dialog().textContent, /Exact incident temporarily unavailable/);
    assert.match(dialog().textContent, /Synthetic resolve delivery lost/);
    unavailable = false; await click('Refresh record'); await settle();
    assert.match(dialog().textContent, /Operator statusresolved/);
    assert.ok(dialog().textContent.includes(originalActor));
    assert.doesNotMatch(dialog().textContent, /Synthetic resolve delivery lost|Refresh the record before another action|does not repair the system|Cancel resolution/);
    assert.equal(posts().length, 1, 'Recovery must not replay the transition');
    assert.equal(find('[role="status"]') === null, true, 'A current read must not manufacture an original mutation receipt');
});

test('incident recovery control: wrong exact identity does not retire an unknown action', async () => {
    let changed = false;
    override = request => {
        if (request.method === 'POST') { changed = true; throw new Error('Synthetic acknowledgement delivery lost'); }
        if (changed && request.path.endsWith(exactPath)) return response({ ...incident('resolved'), id: id(99) });
    };
    await mountAndOpen(); await click('Acknowledge'); await click('Refresh record'); await settle();
    assert.match(dialog().textContent, /No matching incident record/);
    assert.match(dialog().textContent, /Synthetic acknowledgement delivery lost/);
    assert.equal(button('Acknowledge').disabled, true); assert.equal(posts().length, 1);
});

const malformed = [
    ['truthy nonboolean success', 'acknowledged', { success: 'yes' }],
    ['wrong incident identity', 'acknowledged', { incident_id: id(99) }],
    ['wrong saved state', 'acknowledged', { status: 'resolved' }],
    ['missing acknowledgement actor', 'acknowledged', { acknowledged_by: undefined }],
    ['null acknowledgement actor', 'acknowledged', { acknowledged_by: null }],
    ['object acknowledgement actor', 'acknowledged', { acknowledged_by: {} }],
    ['blank acknowledgement actor', 'acknowledged', { acknowledged_by: ' ' }],
    ['missing acknowledgement time', 'acknowledged', { acknowledged_at: undefined }],
    ['null acknowledgement time', 'acknowledged', { acknowledged_at: null }],
    ['invalid acknowledgement time', 'acknowledged', { acknowledged_at: 'not a date' }],
    ['unqualified acknowledgement time', 'acknowledged', { acknowledged_at: '2026-10-21T01:00:00' }],
    ['acknowledgement with a resolution pair', 'acknowledged', { resolved_by: originalActor, resolved_at: resolvedAt }],
    ['missing resolution actor', 'resolved', { resolved_by: undefined }],
    ['null resolution time', 'resolved', { resolved_at: null }],
    ['invalid resolution time', 'resolved', { resolved_at: 'not a date' }],
    ['partial prior acknowledgement', 'resolved', { acknowledged_by: originalActor, acknowledged_at: null }],
];
for (const [name, target, changes] of malformed) {
    test(`incident receipt baseline: ${name} cannot confirm a transition`, async () => {
        let attempted = false;
        override = request => {
            if (request.method === 'POST') { attempted = true; return response({ ...receipt(target), ...changes }); }
            if (attempted && request.path.endsWith(exactPath)) return response({ detail: 'Follow-up unavailable' }, 503);
        };
        await mountAndOpen();
        if (target === 'resolved') await resolve(); else { await click('Acknowledge'); await settle(); }
        assert.equal(find('[role="status"]') === null, true, 'An incomplete/mismatching receipt must not announce confirmed success');
        assert.match(dialog().textContent, /matching receipt|could not be verified|confirm its current state/);
        assert.equal(button('Acknowledge').disabled, true);
        assert.equal(posts().length, 1, 'Invalid receipts must never cause automatic mutation replay');
    });
}

for (const target of ['acknowledged', 'resolved']) {
    test(`incident receipt control: ${target} preserves a valid original actor despite failed follow-up`, async () => {
        let committed = false;
        override = request => {
            if (request.method === 'POST') { committed = true; return response(receipt(target)); }
            if (committed && request.path.endsWith(exactPath)) return response({ detail: 'Follow-up unavailable' }, 503);
        };
        await mountAndOpen();
        if (target === 'resolved') await resolve(); else { await click('Acknowledge'); await settle(); }
        assert.match(find('[role="status"]').textContent, /server confirmed/);
        assert.ok(dialog().textContent.includes(originalActor), 'Replay may preserve a different original operator');
        assert.match(dialog().textContent, /last confirmed record/); assert.equal(posts().length, 1);
        if (target === 'resolved') {
            assert.match(dialog().textContent, /AcknowledgedNot recorded/);
            assert.equal([...dialog().querySelectorAll('button')].some(node => node.textContent === 'Resolve'), false);
        }
    });
}

test('incident focus baseline: close after a committed transition restores a stable visible control', async () => {
    override = request => {
        if (request.method === 'POST') { saved = incident('acknowledged'); return response(receipt()); }
    };
    const origin = await mountAndOpen();
    await click('Acknowledge'); await settle();
    assert.equal(origin.isConnected, false, 'The revised open list has removed the original View button');
    const close = find('[aria-label="Close incident"]'); close.focus(); await act(async () => close.click()); await settle();
    assert.equal(dialog(), null);
    assert.equal(document.activeElement === button('Refresh list'), true, 'The disconnected original needs a stable return target');
    assert.equal(posts().length, 1);
});

test('incident focus control: ordinary close restores the connected original View button', async () => {
    const origin = await mountAndOpen();
    const close = find('[aria-label="Close incident"]'); close.focus(); await act(async () => close.click()); await settle();
    assert.equal(dialog(), null); assert.equal(document.activeElement === origin, true); assert.equal(posts().length, 0);
});
