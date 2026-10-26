import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, dom, state, render, response, text, find, click, deferred, settle, setInput } from './bh-harness.mjs';

const row = (extra = {}) => ({ id: 'item-A', call_id: 'call-A', tenant_id: 'tenant-A', tenant_name: 'Tenant A',
    phone_number: '+15551000', name: 'A', status: 'completed', outcome: null, campaign_name: null, direction: 'inbound',
    created_at: '2026-10-21T00:00:00Z', duration_seconds: 3, file_size_bytes: 42, playable: true,
    legal_hold: false, mime_type: 'audio/wav', storage: 'local', audio_mime_type: 'audio/webm', audio_size_bytes: 42,
    transcript_status: 'failed', transcript: 'Original transcript', transcript_error: null, transcription_attempts: 1,
    retryable: true, created_by_name: 'Reviewer A', type: 'send_email', duration_ms: 10, ...extra });
const page = (items) => ({ items, total: items.length, page: 1, page_size: 20 });
const changeSelect = async (selector, value) => act(async () => {
    const node = find(selector); assert.ok(node); node.value = value;
    node.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
});
const configs = [
    ['RecordingsTable', '/admin/recordings', 'select', 'uploaded', 'status=uploaded'],
    ['FeedbackTable', '/admin/feedback', 'select', 'done', 'transcript_status=done'],
    ['CallHistoryTable', '/admin/calls/history', 'select', 'completed', 'status=completed'],
    ['ActionsTable', '/admin/actions', 'select', 'failed', 'status=failed'],
];
for (const [component, endpoint, selector, status, filter] of configs) {
    test(`${component}: later filters own rows despite older successful response arriving last`, async () => {
        const old = deferred();
        state.route = request => request.path.includes('/admin/tenants') ? response([])
            : request.path.includes(filter) ? response(page([row({ tenant_name: 'Current filtered tenant' })])) : old.promise;
        await render(el(C[component], { onCallSelect() {}, onActionSelect() {} }));
        await changeSelect(selector, status);
        assert.match(text(), /Current filtered tenant/);
        await act(async () => old.resolve(response(page([row({ tenant_name: 'Old unfiltered tenant' })]))));
        assert.doesNotMatch(text(), /Old unfiltered tenant/);
        assert.ok(state.requests.some(request => request.path.includes(endpoint)));
    });
    test(`${component}: account replacement clears previous rows and rejects its late read`, async () => {
        const old = deferred();
        state.route = request => request.path.includes('/admin/tenants') ? response([]) : old.promise;
        await render(el(C[component], { onCallSelect() {}, onActionSelect() {} }));
        state.route = request => request.path.includes('/admin/tenants') ? response([]) : response({ detail: 'New session has no access' }, 403);
        await act(async () => api.setToken('replacement-account'));
        await act(async () => old.resolve(response(page([row({ tenant_name: 'Private old tenant' })]))));
        assert.doesNotMatch(text(), /Private old tenant/);
        assert.match(text(), /New session has no access/);
    });
}

for (const component of ['RecordingsTable', 'FeedbackTable']) {
    test(`${component}: duplicate delete confirm is single flight, failed receipt keeps reason and key for retry`, async () => {
        const pending = deferred();
        state.route = request => request.path.includes('/admin/tenants') ? response([])
            : request.method === 'DELETE' ? pending.promise : response(page([row()]));
        await render(el(C[component], { onCallSelect() {} }));
        await click('Delete');
        await act(async () => {
            const reason = find('textarea');
            Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(reason, 'Delete after explicit owner approval');
            reason.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
        });
        const label = component === 'RecordingsTable' ? 'Delete recording' : 'Delete feedback';
        await act(async () => {
            const button = [...state.host.querySelectorAll('button')].find(button => button.textContent.trim() === label);
            button.click(); button.click();
        });
        assert.equal(state.requests.filter(request => request.method === 'DELETE').length, 1);
        await act(async () => pending.resolve(response({ detail: 'Storage outcome is unconfirmed' }, 503)));
        assert.match(find('[role="dialog"]').textContent, /Storage outcome is unconfirmed/);
        assert.equal(find('textarea').value, 'Delete after explicit owner approval');
        assert.equal(find('textarea').readOnly, true);
        await act(async () => {
            const reason = find('textarea');
            Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(reason, 'Attempted changed reason after sending');
            reason.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
        });
        assert.equal(find('textarea').value, 'Delete after explicit owner approval');
        await click(label);
        const deletes = state.requests.filter(request => request.method === 'DELETE');
        assert.equal(deletes.length, 2);
        assert.deepEqual(deletes[1].body, deletes[0].body);
        assert.equal(deletes[1].headers['Idempotency-Key'], deletes[0].headers['Idempotency-Key']);
        assert.match(text(), /Original transcript|Tenant A/);
    });
    test(`${component}: session changes close deletion confirmation and late success cannot fetch the old table`, async () => {
        const pending = deferred();
        state.route = request => request.path.includes('/admin/tenants') ? response([])
            : request.method === 'DELETE' ? pending.promise : response(page([row()]));
        await render(el(C[component], { onCallSelect() {} })); await click('Delete');
        await act(async () => {
            const reason = find('textarea');
            Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(reason, 'Remove after review by owner');
            reason.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
        });
        await click(component === 'RecordingsTable' ? 'Delete recording' : 'Delete feedback');
        state.route = request => request.path.includes('/admin/tenants') ? response([]) : response(page([]));
        await act(async () => api.setToken('replacement'));
        const before = state.requests.length;
        await act(async () => pending.resolve(response({ detail: 'Deleted', deletion_intent_id: 'intent' })));
        assert.equal(state.requests.length, before);
        assert.equal(find('[role="dialog"]'), null);
        assert.doesNotMatch(text(), /Tenant A/);
    });
}

test('feedback retry rejects mismatched media ownership and does not replace the original transcript', async () => {
    state.route = request => request.path.includes('/admin/tenants') ? response([])
        : request.method === 'POST' ? response(row({ call_id: 'foreign-call', transcript: 'Foreign transcript' })) : response(page([row()]));
    await render(el(C.FeedbackTable, { onCallSelect() {} })); await click('Retry transcript');
    assert.match(text(), /did not match this feedback/);
    assert.match(text(), /Original transcript/); assert.doesNotMatch(text(), /Foreign transcript/);
});

test('actions row is operable from the keyboard', async () => {
    state.route = () => response(page([row()])); let selected;
    await render(el(C.ActionsTable, { onActionSelect(id) { selected = id; } }));
    await act(async () => find('tr[tabindex="0"]').dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
    assert.equal(selected, 'item-A');
    await settle();
});

for (const status of ['unknown', 'scheduled']) {
    test(`actions table preserves ${status} and sends its actual filter through the API`, async () => {
        state.route = () => response(page([row({ status })]));
        await render(el(C.ActionsTable, { onActionSelect() {} }));
        const label = status[0].toUpperCase() + status.slice(1);
        assert.match(find('tbody').textContent, new RegExp(label));
        assert.doesNotMatch(find('tbody').textContent, /Pending/);
        assert.ok(find(`select option[value="${status}"]`));
        await changeSelect('select', status);
        assert.ok(state.requests.some(request => request.path.includes(`status=${status}`)));
        assert.match(find('tbody').textContent, new RegExp(label));
    });
}

test('tenant filter searches the server beyond initial options and reports option failures separately from rows', async () => {
    let fail = false;
    state.route = request => request.path.includes('/admin/tenants')
        ? fail ? response({ detail: 'Tenant directory unavailable' }, 503)
            : request.path.includes('search=Beyond') ? response([{ id: 'beyond-200', business_name: 'Beyond initial tenants' }])
            : response(Array.from({ length: 51 }, (_, index) => ({ id: `tenant-${index}`, business_name: `Tenant ${index}` })))
        : response(page([row({ tenant_id: 'beyond-200' })]));
    await render(el(C.RecordingsTable, { onCallSelect() {} }));
    assert.match(text(), /More tenants match/);
    await setInput(find('[aria-label="Search tenant filter options"]'), 'Beyond'); await settle(300);
    assert.match(find('[aria-label="Filter by tenant"]').textContent, /Beyond initial tenants/);
    assert.ok(state.requests.some(request => request.path.includes('search=Beyond') && request.path.includes('limit=51')));
    await changeSelect('[aria-label="Filter by tenant"]', 'beyond-200');
    assert.ok(state.requests.some(request => request.path.includes('/admin/recordings') && request.path.includes('tenant_id=beyond-200')));
    fail = true; await setInput(find('[aria-label="Search tenant filter options"]'), 'Unavailable'); await settle(300);
    assert.match(text(), /Tenant directory unavailable/);
    assert.match(text(), /Tenant A/);
    assert.equal(find('[aria-label="Filter by tenant"]').value, 'beyond-200');
});

test('historical media can select later same-named tenants and includes archived tenants', async () => {
    const duplicates = Array.from({ length: 51 }, (_, index) => ({ id: `duplicate-${index}`, business_name: 'Same business', status: index === 50 ? 'cancelled' : 'active' }));
    state.route = request => {
        if (!request.path.includes('/admin/tenants')) return response(page([]));
        const query = new URL(request.path, 'https://example.invalid').searchParams;
        assert.equal(query.get('include_archived'), 'true');
        const offset = Number(query.get('offset'));
        return response(duplicates.slice(offset, offset + 51));
    };
    await render(el(C.RecordingsTable, { onCallSelect() {} }));
    assert.equal(find('option[value="duplicate-50"]'), null);
    assert.match(find('option[value="duplicate-0"]').textContent, /Same business.*duplicate-0/);
    await click('Next tenant options');
    assert.match(find('option[value="duplicate-50"]').textContent, /Same business.*duplicate-50/);
    await changeSelect('[aria-label="Filter by tenant"]', 'duplicate-50');
    assert.ok(state.requests.some(request => request.path.includes('/admin/recordings') && request.path.includes('tenant_id=duplicate-50')));
    await click('Previous tenant options');
    assert.equal(find('[aria-label="Filter by tenant"]').value, 'duplicate-50');
    assert.match(find('option[value="duplicate-50"]').textContent, /duplicate-50/);
});
