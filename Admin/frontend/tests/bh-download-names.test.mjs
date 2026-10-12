import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, dom, state, render, response, find, settle } from './bh-harness.mjs';

// jsdom does not implement anchor navigation; record what the player tries to download.
const downloads = [];
const realClick = dom.window.HTMLAnchorElement.prototype.click;
dom.window.HTMLAnchorElement.prototype.click = function () { downloads.push(this.download); };
test.after(() => { dom.window.HTMLAnchorElement.prototype.click = realClick; });
test.beforeEach(() => { downloads.length = 0; });

const audio = (type) => new Response(new Blob(['x'], { type }), { status: 200, headers: { 'Content-Type': type } });
const page = (items) => ({ items, page: 1, page_size: 20, total: items.length });
const recording = (mime) => ({
    id: 'r0000000-0000-4000-8000-000000000001', call_id: 'call-123', tenant_id: 't1', tenant_name: 'Acme',
    phone_number: '+15550100', campaign_id: null, campaign_name: null, status: 'ready', mime_type: mime,
    storage: 'local', duration_seconds: 5, file_size_bytes: 10, created_at: '2026-10-01T00:00:00Z',
    updated_at: null, playable: true, legal_hold: false,
});
const feedbackItem = (mime) => ({
    id: 'f0000000-0000-4000-8000-000000000001', call_id: 'call-456', tenant_id: 't1', tenant_name: 'Acme',
    phone_number: '+15550100', campaign_id: null, campaign_name: null, created_by: null, created_by_name: 'Rev',
    created_by_email: null, audio_mime_type: mime, audio_size_bytes: 10, duration_seconds: 3, transcript: null,
    transcript_status: 'pending', transcript_error: null, transcription_attempts: 0,
    transcript_provider: null, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
    retryable: false, legal_hold: false,
});
const clickDownload = async () => { await act(async () => find('button[aria-label="Download audio"]').click()); await settle(150); };

// admin-frontend-gateway-11 ---------------------------------------------------

for (const [mime, ext] of [
    ['audio/mpeg', 'mp3'], ['audio/ogg', 'ogg'], ['audio/mp4', 'm4a'], ['audio/wav', 'wav'],
    ['audio/x-wav', 'wav'], ['audio/webm', 'webm'], ['audio/webm;codecs=opus', 'webm'],
]) {
    test(`recordings table names a ${mime} download .${ext}`, async () => {
        state.route = (call) => {
            if (call.path.includes('/admin/recordings/') && call.path.endsWith('/audio')) return audio(mime);
            if (call.path.includes('/admin/recordings')) return response(page([recording(mime)]));
            return response([]);
        };
        await render(el(C.RecordingsTable, { onCallSelect() {} }));
        await settle();
        await clickDownload();
        assert.deepEqual(downloads, [`call-call-123.${ext}`]);
    });
}

test('recordings table falls back to the downloaded blob type when the row mime is unknown', async () => {
    state.route = (call) => {
        if (call.path.endsWith('/audio')) return audio('audio/mpeg');
        if (call.path.includes('/admin/recordings')) return response(page([recording('application/octet-stream')]));
        return response([]);
    };
    await render(el(C.RecordingsTable, { onCallSelect() {} }));
    await settle();
    await clickDownload();
    assert.deepEqual(downloads, ['call-call-123.mp3']);
});

for (const [mime, ext] of [['audio/mp4', 'm4a'], ['audio/webm', 'webm'], ['audio/ogg', 'ogg'], ['audio/mpeg', 'mp3']]) {
    test(`feedback table names a ${mime} note .${ext}`, async () => {
        state.route = (call) => {
            if (call.path.endsWith('/audio')) return audio(mime);
            if (call.path.includes('/admin/feedback')) return response(page([feedbackItem(mime)]));
            return response([]);
        };
        await render(el(C.FeedbackTable, { onCallSelect() {} }));
        await settle();
        await clickDownload();
        assert.deepEqual(downloads, [`feedback-call-456.${ext}`]);
    });
}

test('call detail drawer names recording and feedback downloads from their own mime types', async () => {
    state.route = (call) => {
        if (call.path.includes('/admin/recordings/') && call.path.endsWith('/audio')) return audio('audio/mpeg');
        if (call.path.includes('/admin/feedback/') && call.path.endsWith('/audio')) return audio('audio/mp4');
        if (call.path.includes('/admin/recordings')) return response(page([recording('audio/mpeg')]));
        if (call.path.includes('/admin/feedback')) return response(page([feedbackItem('audio/mp4')]));
        if (call.path.includes('/admin/calls/call-789')) {
            return response({
                id: 'call-789', tenant_id: 't1', tenant_name: 'Acme', phone_number: '+15550100', campaign_id: null,
                campaign_name: null, lead_id: null, status: 'completed', outcome: null, goal_achieved: false,
                started_at: null, answered_at: null, ended_at: null, duration_seconds: 5, transcript: null,
                transcript_json: null, summary: null, summary_json: null, recording_url: null, cost: null,
                timeline: [], created_at: '2026-10-01T00:00:00Z',
            });
        }
        return response({ detail: 'unrouted' }, 500);
    };
    await render(el(C.CallDetailDrawer, { callId: 'call-789', onClose() {} }));
    await settle();
    const tab = (label) => [...state.host.querySelectorAll('[role="tab"]')].find((n) => n.textContent.includes(label));
    await act(async () => tab('Recordings').click());
    await clickDownload();
    await act(async () => tab('Feedback').click());
    await clickDownload();
    assert.deepEqual(downloads, ['call-call-789.mp3', 'feedback-call-789.m4a']);
});
