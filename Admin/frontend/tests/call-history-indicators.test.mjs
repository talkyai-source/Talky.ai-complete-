import assert from 'node:assert/strict';
import test from 'node:test';
import { C, act, el, render, response, settle, state, text } from './bh-harness.mjs';

const base = { id: '10000000-0000-4000-8000-000000000001', tenant_id: '20000000-0000-4000-8000-000000000001',
    tenant_name: 'Synthetic tenant', phone_number: '+15550000000', status: 'completed',
    created_at: '2026-10-21T00:00:00Z', duration_seconds: 42, direction: 'outbound', feedback_transcript_status: 'completed' };

function route(row) {
    state.route = request => request.path.startsWith('/api/v1/admin/calls/history')
        ? response({ items: [row], total: 1, page: 1, page_size: 20 })
        : request.path.startsWith('/api/v1/admin/tenants') ? response([])
            : response({ detail: 'Unexpected synthetic request' }, 503);
}

for (const [recording, feedback, expected, absent] of [
    [null, true, /(?=.*Recording status unavailable)(?=.*Feedback)/, /Feedback status unavailable/],
    [true, null, /Recording.*Feedback status unavailable/, /Recording status unavailable/],
    [null, null, /Recording status unavailable.*Feedback status unavailable/, /^—$/],
    [undefined, undefined, /Recording status unavailable.*Feedback status unavailable/, /^—$/],
]) test(`independent unknown indicators remain explicit (${recording}/${feedback}) and the call remains selectable`, async () => {
    route({ ...base, has_recording: recording, has_feedback: feedback });
    let selected;
    await render(el(C.CallHistoryTable, { onCallSelect(id) { selected = id; } }));
    await settle();
    const media = state.host.querySelector('.media-indicators');
    assert.match(media.textContent, expected);
    assert.doesNotMatch(media.textContent, absent);
    assert.match(text(), /Synthetic tenant/);
    assert.match(text(), /0:42/);
    await act(async () => state.host.querySelector('tbody tr').click());
    assert.equal(selected, base.id);
});

test('confirmed absent metadata preserves the existing no-media display', async () => {
    route({ ...base, has_recording: false, has_feedback: false });
    await render(el(C.CallHistoryTable, { onCallSelect() {} }));
    await settle();
    assert.equal(state.host.querySelector('.media-indicators').textContent, '—');
    assert.doesNotMatch(text(), /status unavailable/);
});

test('confirmed present metadata preserves both existing media badges', async () => {
    route({ ...base, has_recording: true, has_feedback: true });
    await render(el(C.CallHistoryTable, { onCallSelect() {} }));
    await settle();
    assert.ok(state.host.querySelector('[title="Call recording available"]'));
    assert.ok(state.host.querySelector('[title="Feedback transcription: completed"]'));
    assert.doesNotMatch(text(), /status unavailable/);
});
