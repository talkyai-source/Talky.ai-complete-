// Actual ReactDOM with synthetic HTTP/HTMLMediaElement events. No native decoder,
// provider, object storage or database is exercised by these controls.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, dom, state, render, response, find, text, click, settle } from './bh-harness.mjs';

const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const callId = id(1), tenantId = id(2), intentId = id(5), stamp = '2026-10-21T00:00:00Z';
const call = { id: callId, tenant_id: tenantId, tenant_name: 'Owned media tenant', phone_number: 'synthetic caller',
    campaign_id: null, campaign_name: null, status: 'completed', duration_seconds: 5, timeline: [], created_at: stamp };
const recording = { id: id(3), call_id: callId, tenant_id: tenantId, tenant_name: call.tenant_name,
    status: 'uploaded', mime_type: 'audio/wav', storage: 'local', duration_seconds: 5, created_at: stamp, playable: true, legal_hold: false };
const feedback = { id: id(4), call_id: callId, tenant_id: tenantId, tenant_name: call.tenant_name,
    created_by_name: 'Owned reviewer', audio_mime_type: 'audio/wav', audio_size_bytes: 44, duration_seconds: 3,
    transcript: null, transcript_status: 'failed', transcript_error: 'Previous transcription failed',
    transcription_attempts: 1, created_at: stamp, retryable: true, legal_hold: false };
const page = items => ({ items, total: items.length, page: 1, page_size: 20 });
const modal = () => find('[aria-labelledby="modal-title"]');
const deletes = () => state.requests.filter(request => request.method === 'DELETE');
let override, removed, currentFeedback;
const originalCreate = URL.createObjectURL, originalRevoke = URL.revokeObjectURL;
test.beforeEach(() => {
    override = null; removed = false; currentFeedback = feedback;
    URL.createObjectURL = () => 'blob:synthetic-media-operator'; URL.revokeObjectURL = () => {};
    state.route = request => {
        const custom = override?.(request); if (custom !== undefined) return custom;
        const url = new URL(request.path, 'https://synthetic.invalid');
        if (url.pathname.endsWith(`/admin/calls/${callId}`)) return response(call);
        if (url.pathname.endsWith('/admin/recordings')) return response(page(removed ? [] : [recording]));
        if (url.pathname.endsWith('/admin/feedback')) return response(page(removed
            || url.searchParams.get('transcript_status') && url.searchParams.get('transcript_status') !== currentFeedback.transcript_status ? [] : [currentFeedback]));
        if (url.pathname.endsWith('/admin/tenants')) return response([]);
        return response({ detail: 'Unrouted synthetic media request' }, 500);
    };
});
test.after(() => { URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke; });

const surfaces = [
    { name: 'recording drawer', drawer: true, tab: 'Recordings', confirm: 'Delete permanently' },
    { name: 'feedback drawer', drawer: true, tab: 'Feedback', confirm: 'Delete permanently' },
    { name: 'recordings table', component: 'RecordingsTable', confirm: 'Delete recording' },
    { name: 'feedback table', component: 'FeedbackTable', confirm: 'Delete feedback' },
];
async function mountSurface(surface) {
    await render(surface.drawer ? el(C.CallDetailDrawer, { callId, onClose() {} }) : el(C[surface.component], { onCallSelect() {} }));
    await settle();
    if (surface.drawer) {
        const tab = [...state.host.querySelectorAll('[role="tab"]')].find(node => node.textContent.includes(surface.tab));
        assert.ok(tab); await act(async () => tab.click());
    }
}
async function openDeletion() {
    await click('Delete'); const reason = modal().querySelector('textarea');
    await act(async () => {
        Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(reason, 'Reviewed removal of owned synthetic media');
        reason.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    });
}

for (const surface of surfaces) {
    test(`media receipt baseline: ${surface.name} rejects a malformed successful acknowledgement`, async () => {
        override = request => request.method === 'DELETE' ? response({ detail: { unexpected: true }, deletion_intent_id: 'not-a-uuid' }) : undefined;
        await mountSurface(surface); await openDeletion(); await click(surface.confirm); await settle();
        assert.equal(Boolean(modal()), true, 'Malformed receipt must leave the original deletion recoverable');
        assert.equal(modal().querySelector('textarea').readOnly, true);
        assert.equal(deletes().length, 1, 'Receipt validation must not replay deletion');
    });
    test(`media receipt control: ${surface.name} accepts the existing complete committed response`, async () => {
        override = request => {
            if (request.method === 'DELETE') { removed = true; return response({ detail: 'Media permanently deleted', resource_type: request.path.includes('/recordings/') ? 'recording' : 'call_feedback', resource_id: request.path.split('/').at(-1), outcome: 'completed', deletion_intent_id: intentId }); }
        };
        await mountSurface(surface); await openDeletion(); await click(surface.confirm); await settle();
        assert.equal(modal() === null, true); assert.equal(deletes().length, 1);
    });
}

for (const surface of surfaces.filter(item => item.drawer)) {
    test(`media receipt baseline: ${surface.name} does not treat empty JSON as a confirmed deletion`, async () => {
        override = request => request.method === 'DELETE' ? response({}) : undefined;
        await mountSurface(surface); await openDeletion(); await click(surface.confirm); await settle();
        assert.equal(Boolean(modal()), true, 'No-error HTTP200 alone is not a deletion receipt');
        assert.equal(deletes().length, 1);
    });
}

test('feedback filter baseline: a successful retry cannot retain Done in the Failed result set', async () => {
    override = request => {
        if (request.method === 'POST') {
            currentFeedback = { ...feedback, transcript_status: 'done', transcript: 'Recovered synthetic transcript',
                transcript_error: null, transcription_attempts: 2, retryable: false };
            return response(currentFeedback);
        }
    };
    await mountSurface(surfaces[3]);
    const filter = [...state.host.querySelectorAll('select')].find(node => [...node.options].some(option => option.textContent === 'All transcript states'));
    assert.ok(filter); await act(async () => { filter.value = 'failed'; filter.dispatchEvent(new dom.window.Event('change', { bubbles: true })); }); await settle();
    await click('Retry transcript'); await settle();
    assert.equal(filter.value, 'failed');
    assert.equal(Boolean(find('tbody .status-done')), false, 'The row no longer belongs to the selected Failed filter');
    assert.doesNotMatch(text(), /Showing 1–1 of 1/);
    assert.equal(state.requests.filter(request => request.method === 'POST').length, 1);
});

test('media decode baseline: a loaded blob error is visible without automatically refetching', async () => {
    let loads = 0;
    await render(el(C.AdminMediaPlayer, { resourceId: `recording:${recording.id}`, filenameBase: 'owned-recording', fallbackExtension: 'wav',
        load: async () => { loads += 1; return new Blob(['not an actual WAV'], { type: 'audio/wav' }); } }));
    await click('Play'); await settle(); const audio = find('audio'); assert.ok(audio);
    Object.defineProperty(audio, 'error', { configurable: true, value: { code: 3, message: 'Synthetic decode failure event' } });
    await act(async () => audio.dispatchEvent(new dom.window.Event('error'))); await settle();
    assert.equal(Boolean(find('[role="alert"]')), true, 'Native media errors need an app-visible recovery message');
    assert.equal(loads, 1, 'A decode event must not automatically replay the fetch');
});

test('media load control: a rejected audio request already produces a visible error', async () => {
    await render(el(C.AdminMediaPlayer, { resourceId: `recording:${recording.id}`, filenameBase: 'owned-recording', fallbackExtension: 'wav',
        load: async () => { throw new Error('Synthetic audio read unavailable'); } }));
    await click('Play'); await settle(); assert.match(find('[role="alert"]').textContent, /Synthetic audio read unavailable/);
});
