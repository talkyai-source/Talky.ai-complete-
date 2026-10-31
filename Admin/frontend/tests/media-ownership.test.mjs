import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, dom, state, render, response, find, text, click, settle, deferred } from './bh-harness.mjs';

const page = (items) => ({ items, page: 1, page_size: 20, total: items.length });
const detail = (id) => ({ id, tenant_id: `tenant-${id}`, tenant_name: id, phone_number: id,
    campaign_id: null, campaign_name: null, status: 'completed', duration_seconds: 5,
    timeline: [], created_at: '2026-10-01T00:00:00Z' });
const recording = (id) => ({ id: `recording-${id}`, call_id: id, tenant_id: `tenant-${id}`,
    status: 'ready', mime_type: 'audio/mpeg', storage: 'local', duration_seconds: 5,
    created_at: '2026-10-01T00:00:00Z', playable: true, legal_hold: false });
const feedback = (id) => ({ id: `feedback-${id}`, call_id: id, tenant_id: `tenant-${id}`,
    created_by_name: `Reviewer ${id}`, audio_mime_type: 'audio/mpeg', duration_seconds: 3,
    transcript: `Transcript ${id}`, transcript_status: 'failed', transcript_error: null,
    transcription_attempts: 1, created_at: '2026-10-01T00:00:00Z', retryable: true, legal_hold: false });
const audio = () => new Blob(['synthetic'], { type: 'audio/mpeg' });
const ordinaryRoute = (request) => {
    if (request.path.startsWith('/api/v1/admin/calls/')) return response(detail(request.path.split('/').at(-1)));
    const query = new URL(request.path, 'https://example.invalid');
    const id = query.searchParams.get('call_id');
    if (query.pathname === '/api/v1/admin/recordings') return response(page([recording(id)]));
    if (query.pathname === '/api/v1/admin/feedback') return response(page([feedback(id)]));
    return response({ detail: 'unrouted' }, 500);
};
const drawer = (id) => el(C.CallDetailDrawer, { callId: id, onClose() {} });
const tab = async (name) => {
    const node = [...state.host.querySelectorAll('[role="tab"]')].find((item) => item.textContent.includes(name));
    assert.ok(node, `Missing ${name} tab`);
    await act(async () => node.click());
};
const reason = async () => {
    const field = find('textarea');
    await act(async () => {
        Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(field, 'Authorized synthetic retention request');
        field.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    });
};
const downloads = [], urls = [], revoked = [];
const originalClick = dom.window.HTMLAnchorElement.prototype.click;
const originalCreate = URL.createObjectURL, originalRevoke = URL.revokeObjectURL;
test.beforeEach(() => {
    downloads.length = 0; urls.length = 0; revoked.length = 0;
    state.route = ordinaryRoute;
    dom.window.HTMLAnchorElement.prototype.click = function () { downloads.push(this.download); };
    URL.createObjectURL = () => { const url = `blob:synthetic-${urls.length}`; urls.push(url); return url; };
    URL.revokeObjectURL = (url) => { revoked.push(url); };
});
test.after(() => {
    dom.window.HTMLAnchorElement.prototype.click = originalClick;
    URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke;
});

test('failed media read for B cannot retain A audio or confirmation', async () => {
    await render(drawer('A')); await tab('Recordings'); await click('Delete');
    assert.ok(find('[aria-labelledby="modal-title"]'));
    state.route = (request) => request.path.includes('/admin/recordings?') && request.path.includes('call_id=B')
        ? response({ detail: 'B media unavailable' }, 503) : ordinaryRoute(request);
    await render(drawer('B')); await tab('Recordings');
    assert.match(text(), /B media unavailable/);
    assert.equal(find('[aria-label="Download audio"]'), null);
    assert.equal(find('[aria-labelledby="modal-title"]'), null);
});

test('late detail A cannot supply CRM receipt evidence to selected B', async () => {
    const pendingA = deferred(), pendingB = deferred();
    const withCRM = (id) => ({ ...detail(id), crm_receipts_available: true, crm_deliveries: [{
        provider: 'hubspot', status: 'unknown', phase: 'creating_call',
        destination_connector_id: `connector-${id}`, destination_account_id: `original-${id}`,
        remote_contact_id: null, remote_call_id: null, inspection_available: false,
    }] });
    state.route = (request) => {
        if (request.path === '/api/v1/admin/calls/A') return pendingA.promise.then((result) => result.clone());
        if (request.path === '/api/v1/admin/calls/B') return pendingB.promise.then((result) => result.clone());
        return ordinaryRoute(request);
    };
    await render(drawer('A')); await render(drawer('B'));
    await act(async () => pendingB.resolve(response(withCRM('B'))));
    await act(async () => pendingA.resolve(response(withCRM('A'))));
    const saved = find('[aria-label="Saved CRM delivery evidence"]');
    assert.match(saved.textContent, /original-B/);
    assert.doesNotMatch(saved.textContent, /original-A/);
    const mediaReads = state.requests.filter((request) => /\/admin\/(recordings|feedback)\?/.test(request.path));
    assert.equal(mediaReads.length, 2);
    assert.ok(mediaReads.every((request) => request.path.includes('call_id=B')));
});

for (const variant of ['foreign-call', 'foreign-tenant', 'foreign-feedback', 'missing-media-id']) {
    test(`rejects ${variant} media rather than making it playable/deletable`, async () => {
        state.route = (request) => {
            if (request.path.includes('/admin/recordings?') && variant !== 'foreign-feedback') {
                return response(page([{ ...recording('A'), ...(variant === 'foreign-call' ? { call_id: 'B' }
                    : variant === 'foreign-tenant' ? { tenant_id: 'tenant-B' } : { id: null }) }]));
            }
            if (request.path.includes('/admin/feedback?') && variant === 'foreign-feedback') return response(page([feedback('B')]));
            return ordinaryRoute(request);
        };
        await render(drawer('A')); await tab('Recordings');
        assert.match(text(), /ownership could not be verified/);
        assert.equal(find('[aria-label="Download audio"]'), null);
    });
}

test('delete response from closed A cannot refresh or change selected B', async () => {
    const pending = deferred();
    state.route = (request) => request.method === 'DELETE' ? pending.promise : ordinaryRoute(request);
    await render(drawer('A')); await tab('Recordings'); await click('Delete'); await reason();
    await click('Delete permanently');
    await render(drawer('B')); await tab('Feedback');
    const readCount = state.requests.length;
    await act(async () => pending.resolve(response({ detail: 'Deleted', deletion_intent_id: 'original-A' })));
    assert.equal(state.requests.length, readCount, 'old deletion must not refresh A using a new view/session');
    assert.match(text(), /Transcript B/); assert.doesNotMatch(text(), /Transcript A/);
});

test('unconfirmed deletion retains its original idempotency key when deliberately retried', async () => {
    state.route = (request) => request.method === 'DELETE' ? response({ detail: 'Storage unavailable; deletion unconfirmed' }, 503) : ordinaryRoute(request);
    await render(drawer('A')); await tab('Recordings'); await click('Delete'); await reason();
    await click('Delete permanently');
    assert.equal(find('textarea').readOnly, true);
    await act(async () => {
        const field = find('textarea');
        Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(field, 'Different reason after first attempt');
        field.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    });
    assert.equal(find('textarea').value, 'Authorized synthetic retention request');
    await click('Delete permanently');
    const writes = state.requests.filter((request) => request.method === 'DELETE');
    assert.equal(writes.length, 2);
    assert.ok(writes[0].headers['Idempotency-Key']);
    assert.equal(writes[0].headers['Idempotency-Key'], writes[1].headers['Idempotency-Key']);
    assert.deepEqual(writes[0].body, writes[1].body);
    assert.match(find('[aria-labelledby="modal-title"]').textContent, /deletion unconfirmed/);
});

test('current-authority refusal remains visible without deleting media or replaying the write', async () => {
    state.route = (request) => request.method === 'DELETE'
        ? response({ detail: 'Current platform admin authority is required.' }, 403)
        : ordinaryRoute(request);
    await render(drawer('A')); await tab('Recordings'); await click('Delete'); await reason();
    const readsBefore = state.requests.filter((request) => request.method === 'GET').length;
    await click('Delete permanently'); await settle();
    assert.match(find('[aria-labelledby="modal-title"]').textContent, /Current platform admin authority is required/);
    assert.equal(state.requests.filter((request) => request.method === 'DELETE').length, 1);
    assert.equal(state.requests.filter((request) => request.method === 'GET').length, readsBefore);
    assert.ok(find('[aria-label="Download audio"]'), 'refusal must not falsely remove the retained media row');
    assert.equal(find('textarea').readOnly, true);
});

test('late feedback retry for A cannot replace B feedback', async () => {
    const pending = deferred();
    state.route = (request) => request.method === 'POST' ? pending.promise : ordinaryRoute(request);
    await render(drawer('A')); await tab('Feedback'); await click('Retry');
    await render(drawer('B')); await tab('Feedback');
    await act(async () => pending.resolve(response({ ...feedback('A'), transcript: 'Late A result' })));
    assert.match(text(), /Transcript B/); assert.doesNotMatch(text(), /Late A result/);
});

test('feedback retry validates returned feedback identity', async () => {
    state.route = (request) => request.method === 'POST' ? response(feedback('B')) : ordinaryRoute(request);
    await render(drawer('A')); await tab('Feedback'); await click('Retry');
    assert.match(text(), /Feedback ownership could not be verified/);
    assert.match(text(), /Transcript A/); assert.doesNotMatch(text(), /Transcript B/);
});

test('session change retires confirmation and stale mutation buttons without a parent render', async () => {
    await render(drawer('A')); await tab('Feedback');
    const retry = [...state.host.querySelectorAll('button')].find((node) => node.textContent.trim() === 'Retry');
    await click('Delete'); await reason();
    const confirm = [...state.host.querySelectorAll('button')].find((node) => node.textContent.trim() === 'Delete permanently');
    await act(async () => {
        api.setToken('different-session');
        retry.click(); confirm.click();
    });
    assert.ok(state.requests.every((request) => request.method === 'GET'));
    assert.equal(find('[aria-labelledby="modal-title"]'), null);
});

test('session change retires pending detail and refetches with current authority', async () => {
    const old = deferred(), current = deferred();
    const priorToken = api.getAuthGeneration();
    state.route = (request) => request.path === '/api/v1/admin/calls/B'
        ? (api.getAuthGeneration() === priorToken ? old.promise : current.promise).then((result) => result.clone())
        : ordinaryRoute(request);
    await render(drawer('B'));
    await act(async () => api.setToken('another-session'));
    await act(async () => old.resolve(response({ ...detail('B'), tenant_name: 'Retired session detail' })));
    assert.equal(find('[role="tab"]'), null);
    await act(async () => current.resolve(response(detail('B'))));
    assert.ok(find('[role="tab"]'));
    assert.doesNotMatch(text(), /Retired session detail/);
});

const player = (resourceId, load, disabled = false) => el(C.AdminMediaPlayer, {
    resourceId, load, disabled, filenameBase: 'same-call-label', fallbackExtension: 'wav',
});
const download = async () => { await act(async () => find('[aria-label="Download audio"]').click()); };

for (const change of ['unmount', 'session-without-render', 'resource', 'disabled']) {
    test(`late audio cannot publish/download after ${change}`, async () => {
        const pending = deferred();
        await render(player('A', () => pending.promise)); await download();
        if (change === 'unmount') await render(el('p', null, 'Left media view'));
        if (change === 'session-without-render') await act(async () => api.setToken('new-media-session'));
        if (change === 'resource') await render(player('B', async () => audio()));
        if (change === 'disabled') await render(player('A', async () => audio(), true));
        await act(async () => pending.resolve(audio())); await settle();
        assert.deepEqual(downloads, []); assert.deepEqual(urls, []);
        assert.equal(find('audio'), null);
    });
}

test('resource change revokes a loaded blob even when filename is unchanged', async () => {
    await render(player('A', async () => audio())); await download();
    assert.deepEqual(downloads, ['same-call-label.mp3']);
    await render(player('B', async () => audio()));
    assert.deepEqual(revoked, ['blob:synthetic-0']);
    assert.equal(find('audio'), null);
});

test('session change revokes already loaded audio without a parent render', async () => {
    await render(player('A', async () => audio())); await download();
    assert.ok(find('audio'));
    await act(async () => api.setToken('new-loaded-media-session'));
    assert.deepEqual(revoked, ['blob:synthetic-0']);
    assert.equal(find('audio'), null);
});

test('ownership lost during URL creation revokes the late URL without downloading', async () => {
    URL.createObjectURL = () => { api.setToken('changed-at-publication'); return 'blob:late'; };
    await render(player('A', async () => audio())); await download();
    assert.deepEqual(revoked, ['blob:late']); assert.deepEqual(downloads, []);
});

test('two same-frame download clicks share one load and one anchor activation', async () => {
    const pending = deferred(); let loads = 0;
    await render(player('A', () => { loads += 1; return pending.promise; }));
    await act(async () => { find('[aria-label="Download audio"]').click(); find('[aria-label="Download audio"]').click(); });
    await act(async () => pending.resolve(audio()));
    assert.equal(loads, 1); assert.deepEqual(downloads, ['same-call-label.mp3']);
});
