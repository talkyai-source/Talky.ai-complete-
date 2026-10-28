// Real ReactDOM with synthetic HTTP only. Dedicated last-page baseline; no native browser,
// providers, object storage or database. The existing built candidate remains untouched.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, dom, state, render, response, find, text, click, settle, sleep, deferred } from './bh-harness.mjs';

const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const stamp = '2026-10-21T00:00:00Z';
const item = n => ({ id: id(n), call_id: id(100 + n), tenant_id: id(200), tenant_name: 'Owned pagination tenant',
    created_at: stamp, phone_number: `Owned row ${n}`, status: 'uploaded', mime_type: 'audio/wav', storage: 'local',
    duration_seconds: 3, file_size_bytes: 44, playable: false, legal_hold: false, direction: 'inbound',
    audio_mime_type: 'audio/wav', audio_size_bytes: 44, transcript: null, transcript_status: 'failed',
    transcript_error: 'Synthetic previous attempt', transcription_attempts: 1, retryable: true });
const rows = Array.from({ length: 21 }, (_, index) => item(index + 1));
const cases = [
    { name: 'recording deletion', component: 'RecordingsTable', endpoint: 'recordings', kind: 'recording', filter: 'uploaded', confirm: 'Delete recording', method: 'DELETE' },
    { name: 'feedback deletion', component: 'FeedbackTable', endpoint: 'feedback', kind: 'call_feedback', filter: 'failed', confirm: 'Delete feedback', method: 'DELETE' },
    { name: 'failed feedback retry', component: 'FeedbackTable', endpoint: 'feedback', filter: 'failed', method: 'POST' },
];
const queryWithoutPage = request => {
    const params = new URL(request.path, 'https://synthetic.invalid').searchParams;
    params.delete('page'); return [...params.entries()].sort();
};

for (const scenario of cases) {
    test(`last-page recovery: ${scenario.name} returns to page one with exact filters and saved result`, async () => {
        let changed = false;
        state.route = request => {
            const url = new URL(request.path, 'https://synthetic.invalid');
            if (url.pathname === '/api/v1/admin/tenants') return response([]);
            if (request.method === 'GET' && url.pathname === `/api/v1/admin/${scenario.endpoint}`) {
                const page = Number(url.searchParams.get('page') || 1);
                const filtered = changed ? rows.slice(0, 20) : rows;
                return response({ items: filtered.slice((page - 1) * 20, page * 20), total: filtered.length, page, page_size: 20 });
            }
            if (request.method === scenario.method && url.pathname.startsWith(`/api/v1/admin/${scenario.endpoint}/${rows[20].id}`)) {
                changed = true;
                return response(scenario.method === 'DELETE'
                    ? { detail: 'Saved deletion of the reviewed last row', resource_type: scenario.kind,
                        resource_id: rows[20].id, outcome: 'completed', deletion_intent_id: id(300) }
                    : { ...rows[20], transcript_status: 'done', transcript: 'Saved transcript after retry',
                        transcript_error: null, transcription_attempts: 2, retryable: false });
            }
            return response({ detail: 'Unrouted synthetic last-page request' }, 500);
        };
        await render(el(C[scenario.component], { onCallSelect() {} }));
        const status = find('.filter-group select');
        await act(async () => { status.value = scenario.filter; status.dispatchEvent(new dom.window.Event('change', { bubbles: true })); });
        // Let the unrelated initial search debounce settle before selecting page2.
        await act(async () => { await sleep(350); }); await settle();
        const next = state.host.querySelector('.pagination .pagination-controls button:last-child');
        assert.ok(next); assert.equal(next.disabled, false);
        await act(async () => next.click()); await settle();
        assert.match(text(), /Showing 21–21 of 21/); assert.match(text(), /Page 2 of 2/);
        assert.equal(state.host.querySelectorAll('tbody tr').length, 1);
        const listReads = () => state.requests.filter(request => request.method === 'GET'
            && new URL(request.path, 'https://synthetic.invalid').pathname === `/api/v1/admin/${scenario.endpoint}`);
        const reviewedQuery = listReads().at(-1);
        const readsBefore = listReads().length;
        if (scenario.method === 'DELETE') {
            await act(async () => { [...state.host.querySelectorAll('button')].find(node => node.textContent.trim() === 'Delete').focus(); });
            await click('Delete'); const reason = find('textarea');
            await act(async () => {
                Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(reason, 'Reviewed last row deletion');
                reason.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
            });
            await click(scenario.confirm);
        } else await click('Retry transcript');
        await settle(); await settle();
        const postReads = listReads().slice(readsBefore);
        const mutations = state.requests.filter(request => request.method === scenario.method);
        assert.equal(mutations.length, 1, 'Recovery must not send a second mutation');
        assert.match(text(), scenario.method === 'DELETE' ? /Deletion confirmed/ : /Saved transcription result.*done; attempt 2/);
        assert.equal(status.value, scenario.filter, 'Selected filter remains unchanged');
        assert.ok(postReads.length >= 1, 'A successful mutation must reread inventory');
        assert.ok(postReads.every(request => JSON.stringify(queryWithoutPage(request)) === JSON.stringify(queryWithoutPage(reviewedQuery))),
            'All recovery reads must preserve the reviewed inventory query except page');
        const observation = { scenario: scenario.name, pagination: find('.pagination')?.textContent.trim() ?? null,
            visible_rows: state.host.querySelectorAll('tbody tr').length,
            post_read_paths: postReads.map(request => request.path), mutation_count: mutations.length };
        console.log('last_page_observation', JSON.stringify(observation));
        assert.ok(postReads.some(request => new URL(request.path, 'https://synthetic.invalid').searchParams.get('page') === '1'),
            `A reduced total requires a page-one read; observed ${JSON.stringify(observation)}`);
        assert.match(text(), /Showing 1–20 of 20/); assert.match(text(), /Page 1 of 1/);
        assert.equal(state.host.querySelectorAll('tbody tr').length, 20);
        assert.doesNotMatch(text(), /Showing 21–20|Page 2 of 1|No recordings found|No feedback notes found/);
    });
}

async function preparedLastPage(scenario, options = {}) {
    let changed = false;
    const postReads = [];
    const normalRead = request => {
        const url = new URL(request.path, 'https://synthetic.invalid');
        if (url.pathname === '/api/v1/admin/tenants') return response([]);
        if (request.method !== 'GET' || url.pathname !== `/api/v1/admin/${scenario.endpoint}`) return undefined;
        const page = Number(url.searchParams.get('page') || 1);
        if (changed) {
            postReads.push(request);
            const custom = options.afterRead?.(request, page);
            if (custom !== undefined) return custom;
        }
        const total = changed ? (options.totalAfter ?? 20) : 21;
        return response({ items: rows.slice(0, Math.max(total, 0)).slice((page - 1) * 20, page * 20), total, page, page_size: 20 });
    };
    state.route = request => {
        const url = new URL(request.path, 'https://synthetic.invalid');
        if (request.method === scenario.method && url.pathname.startsWith(`/api/v1/admin/${scenario.endpoint}/${rows[20].id}`)) {
            changed = true;
            const custom = options.mutationResponse?.(request);
            if (custom !== undefined) return custom;
            return response(scenario.method === 'DELETE'
                ? { detail: 'Saved deletion of the reviewed last row', resource_type: scenario.kind,
                    resource_id: rows[20].id, outcome: 'completed', deletion_intent_id: id(300) }
                : { ...rows[20], transcript_status: 'done', transcript: 'Saved transcript after retry',
                    transcript_error: null, transcription_attempts: 2, retryable: false });
        }
        return normalRead(request) ?? response({ detail: 'Unrouted synthetic boundary request' }, 500);
    };
    await render(el(C[scenario.component], { onCallSelect() {} }));
    const status = find('.filter-group select');
    await act(async () => { status.value = scenario.filter; status.dispatchEvent(new dom.window.Event('change', { bubbles: true })); });
    await act(async () => { await sleep(350); }); await settle();
    await act(async () => state.host.querySelector('.pagination .pagination-controls button:last-child').click());
    await settle(); assert.match(text(), /Page 2 of 2/);
    const writes = () => state.requests.filter(request => request.method === scenario.method);
    const assertSaved = () => assert.match(text(), scenario.method === 'DELETE' ? /Deletion confirmed/ : /Saved transcription result.*done; attempt 2/);
    const perform = async () => {
        if (scenario.method === 'DELETE') {
            await act(async () => { [...state.host.querySelectorAll('button')].find(node => node.textContent.trim() === 'Delete').focus(); });
            await click('Delete'); const reason = find('textarea');
            await act(async () => {
                Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(reason, 'Reviewed last row deletion');
                reason.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
            });
            await click(scenario.confirm);
        } else {
            await act(async () => { [...state.host.querySelectorAll('button')].find(node => node.textContent.trim() === 'Retry transcript').focus(); });
            await click('Retry transcript');
        }
        await settle(); await settle();
    };
    return { perform, postReads, writes, assertSaved, status };
}

for (const scenario of cases) {
    test(`last-page boundary: ${scenario.name} retains saved result when corrected-page read fails`, async () => {
        const run = await preparedLastPage(scenario, { afterRead: (_request, page) => page === 1
            ? response({ detail: 'Corrected page temporarily unavailable' }, 503) : undefined });
        await run.perform(); run.assertSaved();
        assert.match(text(), /Corrected page temporarily unavailable/);
        assert.doesNotMatch(text(), /Showing 21–20|Page 2 of 1|No recordings found|No feedback notes found/);
        assert.equal(run.writes().length, 1);
        assert.deepEqual(run.postReads.map(request => new URL(request.path, 'https://synthetic.invalid').searchParams.get('page')), ['2', '1']);
    });
    test(`last-page boundary: ${scenario.name} corrects a zero total without replay`, async () => {
        const run = await preparedLastPage(scenario, { totalAfter: 0 });
        await run.perform(); run.assertSaved();
        assert.match(text(), scenario.endpoint === 'recordings' ? /No recordings found/ : /No feedback notes found/);
        assert.equal(Boolean(find('.pagination')), false); assert.equal(state.host.querySelectorAll('tbody tr').length, 0);
        assert.deepEqual(run.postReads.map(request => new URL(request.path, 'https://synthetic.invalid').searchParams.get('page')), ['2', '1']);
        assert.equal(run.writes().length, 1);
    });
}

for (const scenario of cases.slice(0, 2)) {
    test(`last-page boundary: ${scenario.name} rejects negative total without inventing a valid page`, async () => {
        const run = await preparedLastPage(scenario, { totalAfter: -1 });
        await run.perform(); run.assertSaved();
        assert.match(text(), /media list response is incomplete/); assert.equal(Boolean(find('.pagination')), false);
        assert.deepEqual(run.postReads.map(request => new URL(request.path, 'https://synthetic.invalid').searchParams.get('page')), ['2']);
        assert.equal(run.writes().length, 1);
    });
    test(`last-page ownership: ${scenario.name} ignores delayed corrected page after filter changes`, async () => {
        const pending = deferred();
        const changedStatus = scenario.endpoint === 'recordings' ? 'failed' : 'done';
        const run = await preparedLastPage(scenario, { afterRead: (request, page) => {
            const url = new URL(request.path, 'https://synthetic.invalid');
            if (url.searchParams.get(scenario.endpoint === 'recordings' ? 'status' : 'transcript_status') === changedStatus)
                return response({ items: [{ ...rows[0], tenant_name: 'Current query row', status: changedStatus, transcript_status: changedStatus }], total: 1, page: 1, page_size: 20 });
            return page === 1 ? pending.promise : undefined;
        } });
        await run.perform(); run.assertSaved();
        assert.match(text(), /Loading recordings|Loading feedback/);
        await act(async () => { run.status.value = changedStatus; run.status.dispatchEvent(new dom.window.Event('change', { bubbles: true })); });
        await settle();
        await act(async () => pending.resolve(response({ items: [{ ...rows[0], tenant_name: 'Stale corrected page' }], total: 20, page: 1, page_size: 20 })));
        assert.match(text(), /Current query row/); assert.doesNotMatch(text(), /Stale corrected page|Page 2 of 1/);
        run.assertSaved(); assert.equal(run.writes().length, 1);
    });
    test(`last-page ownership: ${scenario.name} retires saved result and late corrected read after session replacement`, async () => {
        const pending = deferred();
        const run = await preparedLastPage(scenario, { afterRead: (_request, page) => page === 1 ? pending.promise : undefined });
        await run.perform(); run.assertSaved();
        state.route = request => request.path.includes('/admin/tenants') ? response([])
            : response({ items: [{ ...rows[0], tenant_name: 'New account row' }], total: 1, page: 1, page_size: 20 });
        await act(async () => api.setToken('new-synthetic-account'));
        await settle();
        await act(async () => pending.resolve(response({ items: [{ ...rows[0], tenant_name: 'Stale previous account' }], total: 20, page: 1, page_size: 20 })));
        assert.match(text(), /New account row/); assert.doesNotMatch(text(), /Stale previous account|Deletion confirmed/);
        assert.equal(run.writes().length, 1);
    });
}

for (const scenario of cases.slice(0, 2)) {
    test(`last-page focus baseline: ${scenario.name} returns focus to stable search after deleted row disappears`, async () => {
        const run = await preparedLastPage(scenario);
        await run.perform(); run.assertSaved();
        assert.equal(run.writes().length, 1); assert.equal(state.host.querySelectorAll('tbody tr').length, 20);
        const active = document.activeElement;
        console.log('last_page_focus_observation', JSON.stringify({ scenario: scenario.name, active_tag: active?.tagName,
            active_label: active?.getAttribute('aria-label'), active_class: active?.className }));
        assert.equal(active === find('.search-input'), true, 'Focus must remain on the owning media table after its invoking row is removed');
    });
}

for (const scenario of cases.slice(0, 2)) {
    test(`last-page focus: ${scenario.name} does not steal deliberate focus when corrected read completes`, async () => {
        const pending = deferred();
        const run = await preparedLastPage(scenario, { afterRead: (_request, page) => page === 1 ? pending.promise : undefined });
        await run.perform(); run.assertSaved();
        assert.equal(document.activeElement === find('.search-input'), true);
        await act(async () => run.status.focus());
        await act(async () => pending.resolve(response({ items: rows.slice(0, 20), total: 20, page: 1, page_size: 20 })));
        assert.equal(document.activeElement === run.status, true, 'A later inventory read must not move deliberately chosen focus');
        assert.equal(run.writes().length, 1);
    });
    test(`last-page focus: ${scenario.name} cancelling review returns to its still-existing row`, async () => {
        await preparedLastPage(scenario);
        const origin = [...state.host.querySelectorAll('button')].find(node => node.textContent.trim() === 'Delete');
        await act(async () => origin.focus()); await click('Delete'); await click('Cancel');
        assert.equal(document.activeElement === origin, true, 'Existing invoking control takes precedence over fallback');
        assert.equal(state.requests.filter(request => request.method === 'DELETE').length, 0);
    });
}

test('last-page retry focus baseline: confirmed retry removing its row returns to stable feedback search', async () => {
    const run = await preparedLastPage(cases[2]);
    await run.perform(); run.assertSaved();
    console.log('last_page_retry_focus_observation', JSON.stringify({ active_tag: document.activeElement?.tagName,
        active_label: document.activeElement?.getAttribute('aria-label') }));
    assert.equal(document.activeElement === find('.search-input'), true);
    assert.equal(run.writes().length, 1);
});
test('last-page retry focus control: confirmed retry does not steal deliberately moved focus', async () => {
    const pending = deferred();
    const run = await preparedLastPage(cases[2], { mutationResponse: () => pending.promise });
    await run.perform(); await act(async () => run.status.focus());
    await act(async () => pending.resolve(response({ ...rows[20], transcript_status: 'done', transcript: 'Saved transcript',
        transcript_error: null, transcription_attempts: 2, retryable: false })));
    await settle(); run.assertSaved();
    assert.equal(document.activeElement === run.status, true);
    assert.equal(run.writes().length, 1);
});

async function inventoryRetryFixture(scenario, options = {}) {
    let correctedReads = 0;
    const pending = deferred();
    const run = await preparedLastPage(scenario, { afterRead: (request, page) => {
        if (page !== 1) return undefined;
        correctedReads += 1;
        if (correctedReads === 1) return response({ detail: 'Corrected inventory read failed' }, 503);
        return options.nextRead?.(request) ?? pending.promise;
    } });
    await run.perform(); run.assertSaved();
    assert.match(text(), /Corrected inventory read failed/);
    const retry = [...state.host.querySelectorAll('button')].find(node => node.textContent.trim() === 'Retry');
    assert.ok(retry);
    const propsKey = Object.keys(retry).find(key => key.startsWith('__reactProps$'));
    const retained = retry[propsKey].onClick;
    const activate = async () => { await act(async () => retry.focus()); await click('Retry'); await settle(); };
    return { ...run, pending, retry, retained, activate };
}

for (const scenario of cases) {
    test(`manual inventory retry: ${scenario.name} hands off focus before Retry disappears and preserves saved result`, async () => {
        const run = await inventoryRetryFixture(scenario);
        await run.activate();
        assert.equal(document.activeElement === find('.search-input'), true, 'Focus moves before the loading read finishes');
        assert.equal(run.retry.isConnected, false);
        await act(async () => run.pending.resolve(response({ items: rows.slice(0, 20), total: 20, page: 1, page_size: 20 })));
        await settle(); run.assertSaved();
        assert.equal(document.activeElement === find('.search-input'), true);
        assert.match(text(), /Showing 1–20 of 20/); assert.doesNotMatch(text(), /Corrected inventory read failed/);
        assert.equal(run.writes().length, 1, 'Manual inventory recovery is read-only');
        assert.equal(new URL(run.postReads.at(-1).path, 'https://synthetic.invalid').searchParams.get('page'), '1');
        assert.ok(run.postReads.every(request => queryWithoutPage(request).some(([key, value]) =>
            key === (scenario.endpoint === 'recordings' ? 'status' : 'transcript_status') && value === scenario.filter)));
    });
}

for (const scenario of cases.slice(0, 2)) {
    test(`manual inventory retry: ${scenario.name} retains focus and receipt when the retry read fails`, async () => {
        const run = await inventoryRetryFixture(scenario);
        await run.activate();
        await act(async () => run.pending.resolve(response({ detail: 'Manual inventory retry unavailable' }, 503)));
        await settle(); run.assertSaved();
        assert.match(text(), /Manual inventory retry unavailable/);
        assert.equal(document.activeElement === find('.search-input'), true);
        assert.doesNotMatch(text(), /No recordings found|No feedback notes found/);
        assert.equal(run.writes().length, 1);
    });
    test(`manual inventory retry: ${scenario.name} never steals focus chosen while the read is pending`, async () => {
        const run = await inventoryRetryFixture(scenario);
        await run.activate(); assert.equal(document.activeElement === find('.search-input'), true);
        await act(async () => run.status.focus());
        await act(async () => run.pending.resolve(response({ items: rows.slice(0, 20), total: 20, page: 1, page_size: 20 })));
        await settle(); run.assertSaved();
        assert.equal(document.activeElement === run.status, true); assert.equal(run.writes().length, 1);
    });
    for (const replacement of ['query', 'session']) {
        test(`manual inventory retry: ${scenario.name} retained ${replacement} handler cannot read or move replacement focus`, async () => {
            const run = await inventoryRetryFixture(scenario);
            state.route = request => request.path.includes('/admin/tenants') ? response([])
                : response({ items: [{ ...rows[0], tenant_name: 'Current replacement view' }], total: 1, page: 1, page_size: 20 });
            if (replacement === 'session') await act(async () => api.setToken('manual-retry-new-account'));
            else await act(async () => { run.status.value = ''; run.status.dispatchEvent(new dom.window.Event('change', { bubbles: true })); });
            await settle();
            const currentFilter = find('.filter-group select'); await act(async () => currentFilter.focus());
            const readsBefore = state.requests.length;
            await act(async () => run.retained({ currentTarget: run.retry })); await settle();
            assert.equal(state.requests.length, readsBefore, 'Retired callback must not dispatch a read under the new owner');
            assert.equal(document.activeElement === currentFilter, true);
            assert.match(text(), /Current replacement view/); assert.equal(run.writes().length, 1);
        });
    }
}
