// Real ReactDOM/effects/events with synthetic HTTP and forbidden sockets in bh-harness.
// These are local component regressions, not backend, provider or native-browser acceptance.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, dom, state, render, response, find, text, click, settle, deferred, setInput } from './bh-harness.mjs';

const endpoint = '/api/v1/admin/connectors';
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const row = n => ({ id: id(n), tenant_id: id(100), tenant_name: `Owned tenant ${n}`,
    type: 'email', provider: 'gmail', name: `Pending Gmail ${n}`, status: 'pending',
    account_email: `owned${n}@example.invalid`, token_expires_at: null, token_status: 'unknown',
    last_refreshed_at: null, created_at: '2026-10-24T00:00:00Z', refresh_state: 'idle',
    provider_revocation_status: 'not_requested' });
const rows = Array.from({ length: 41 }, (_, index) => row(index + 1));
const listResponse = (total, page, items = rows.slice(0, Math.max(total, 0)).slice((page - 1) * 20, page * 20)) =>
    response({ items, total, page, page_size: 20 });
const requestURL = request => new URL(request.path, 'https://synthetic.invalid');
const pageOf = request => Number(requestURL(request).searchParams.get('page') || 1);
const filtersOf = request => {
    const query = requestURL(request).searchParams;
    query.delete('page'); return [...query.entries()].sort();
};
const select = async (label, value) => {
    await act(async () => {
        const control = find(`select[aria-label="${label}"]`);
        assert.ok(control); assert.equal(control.disabled, false);
        control.value = value; control.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
    });
    await settle();
};
const assertSaved = () => assert.match(text(), /Disabled locally\. Provider access revocation is unconfirmed/);

async function preparedLastPage(options = {}) {
    const initialTotal = options.initialTotal ?? 21;
    const lastPage = Math.ceil(initialTotal / 20);
    let changed = false;
    const afterReads = [];
    state.route = request => {
        const url = requestURL(request);
        if (request.method === 'GET' && url.pathname === endpoint) {
            const page = pageOf(request);
            if (changed) {
                afterReads.push(request);
                const custom = options.afterRead?.(request, page);
                if (custom !== undefined) return custom;
            }
            return listResponse(changed ? (options.totalAfter ?? initialTotal - 1) : initialTotal, page);
        }
        if (request.method === 'POST' && url.pathname === `${endpoint}/${id(initialTotal)}/revoke`) {
            const custom = options.mutationResponse?.(request);
            if (custom !== undefined) return custom;
            changed = true;
            return response({ success: true, connector_id: id(initialTotal), provider_revoked: false,
                provider_revocation_status: 'unconfirmed' });
        }
        return response({ detail: 'Unrouted synthetic connector recovery request' }, 500);
    };
    await render(el(C.ConnectorsTable, {}));
    await select('Connector status', 'pending');
    await select('Connector type', 'email');
    await select('Connector provider', 'gmail');
    for (let page = 1; page < lastPage; page++) {
        await act(async () => find('button[aria-label="Next connectors page"]').click());
        await settle();
    }
    assert.match(text(), new RegExp(`Page ${lastPage} of ${lastPage}`));
    assert.equal(state.host.querySelectorAll('tbody tr').length, 1);
    const reads = () => state.requests.filter(request => request.method === 'GET' && requestURL(request).pathname === endpoint);
    const reviewed = reads().at(-1);
    assert.deepEqual(filtersOf(reviewed), [['page_size', '20'], ['provider', 'gmail'], ['status', 'pending'], ['type', 'email']]);
    const writes = () => state.requests.filter(request => request.method === 'POST');
    const open = async () => {
        const origin = find(`button[aria-label="Revoke access for Pending Gmail ${initialTotal}"]`);
        await act(async () => { origin.focus(); origin.click(); });
        return origin;
    };
    const perform = async () => { await open(); await click('Revoke'); await settle(); await settle(); };
    return { afterReads, reviewed, writes, open, perform, reads };
}

for (const initialTotal of [21, 41]) {
    test(`connector last-page recovery: ${initialTotal} filtered rows returns to the last valid page without repeating revocation`, async () => {
        const run = await preparedLastPage({ initialTotal });
        await setInput(find('input[aria-label="Search connectors on this page"]'), 'Owned tenant');
        await run.perform(); assertSaved();
        assert.equal(run.writes().length, 1);
        assert.equal(find('select[aria-label="Connector status"]').value, 'pending');
        assert.equal(find('select[aria-label="Connector type"]').value, 'email');
        assert.equal(find('select[aria-label="Connector provider"]').value, 'gmail');
        assert.equal(find('input[aria-label="Search connectors on this page"]').value, 'Owned tenant');
        assert.ok(run.afterReads.every(request => JSON.stringify(filtersOf(request)) === JSON.stringify(filtersOf(run.reviewed))));
        const observation = { initial_total: initialTotal, post_read_pages: run.afterReads.map(pageOf),
            visible_rows: state.host.querySelectorAll('tbody tr').length, empty: /No Connectors Found/.test(text()),
            pagination: find('.page-info')?.textContent ?? null, mutation_count: run.writes().length };
        console.log('connector_last_page_observation', JSON.stringify(observation));
        assert.deepEqual(run.afterReads.map(pageOf), [Math.ceil(initialTotal / 20), Math.ceil((initialTotal - 1) / 20)]);
        assert.equal(state.host.querySelectorAll('tbody tr').length, 20);
        assert.doesNotMatch(text(), /No Connectors Found/);
        if (initialTotal === 41) assert.match(text(), /Page 2 of 2/);
        await click('Refresh'); await settle();
        assert.equal(pageOf(run.reads().at(-1)), Math.ceil((initialTotal - 1) / 20), 'Refresh retains the corrected page');
        assert.equal(run.writes().length, 1);
    });
}

test('connector last-page recovery: failed corrected read retains saved revocation and retries only inventory', async () => {
    let unavailable = true;
    const retry = deferred();
    const run = await preparedLastPage({ afterRead: (_request, page) => page === 1
        ? unavailable ? response({ detail: 'Corrected connector page unavailable' }, 503) : retry.promise : undefined });
    await run.perform(); assertSaved();
    assert.match(text(), /Corrected connector page unavailable/);
    assert.doesNotMatch(text(), /No Connectors Found/);
    assert.equal(state.host.querySelectorAll('tbody tr').length, 0);
    assert.deepEqual(run.afterReads.map(pageOf), [2, 1]);
    const retryButton = find('.error-banner button');
    await act(async () => retryButton.focus());
    unavailable = false; await click('Retry'); await settle(); assertSaved();
    console.log('connector_retry_focus_observation', JSON.stringify({ active_tag: document.activeElement?.tagName,
        active_label: document.activeElement?.getAttribute('aria-label') }));
    assert.match(text(), /Loading connectors/);
    assert.ok(document.activeElement === find('input[aria-label="Search connectors on this page"]'), 'Keyboard Retry returns focus to search before its button is removed');
    assert.equal(run.writes().length, 1);
    await act(async () => retry.resolve(listResponse(20, 1))); await settle();
    assert.equal(state.host.querySelectorAll('tbody tr').length, 20); assertSaved();
    assert.ok(document.activeElement === find('input[aria-label="Search connectors on this page"]'), 'Recovered inventory preserves search focus');
    assert.equal(run.writes().length, 1);
});

test('connector Retry focus control: an unfocused activation and delayed read preserve deliberate filter focus', async () => {
    let unavailable = true;
    const retry = deferred();
    const run = await preparedLastPage({ afterRead: (_request, page) => page === 1
        ? unavailable ? response({ detail: 'Corrected connector page unavailable' }, 503) : retry.promise : undefined });
    await run.perform(); assertSaved();
    const status = find('select[aria-label="Connector status"]');
    await act(async () => status.focus());
    unavailable = false; await click('Retry'); await settle();
    assert.ok(document.activeElement === status, 'Retry cannot take focus from another control');
    await act(async () => retry.resolve(listResponse(20, 1))); await settle();
    assert.ok(document.activeElement === status, 'The delayed retry response cannot take focus from another control');
    assertSaved(); assert.equal(run.writes().length, 1);
});

for (const replacement of ['filter', 'session']) {
    test(`connector Retry ownership: retired ${replacement} retry control cannot issue another request`, async () => {
        const run = await preparedLastPage({ afterRead: () => response({ detail: 'Corrected connector page unavailable' }, 503) });
        await run.perform(); assertSaved();
        const retired = find('.error-banner button');
        state.route = () => listResponse(1, 1, [{ ...row(1), tenant_name: 'Current owned connector' }]);
        if (replacement === 'filter') await select('Connector status', 'error');
        else { await act(async () => api.setToken('replacement-retry-session')); await settle(); }
        assert.equal(retired.isConnected, false);
        const focus = find('select[aria-label="Connector status"]');
        await act(async () => focus.focus());
        const before = state.requests.length;
        await act(async () => retired.click()); await settle();
        assert.equal(state.requests.length, before);
        assert.ok(document.activeElement === focus, 'Retired Retry cannot move focus');
        assert.match(text(), /Current owned connector/); assert.equal(run.writes().length, 1);
    });
}

test('connector last-page control: failed initial reread preserves success and does not invent a lower page', async () => {
    const run = await preparedLastPage({ afterRead: () => response({ detail: 'Current inventory unavailable' }, 503) });
    await run.perform(); assertSaved();
    assert.match(text(), /Current inventory unavailable/); assert.doesNotMatch(text(), /No Connectors Found/);
    assert.deepEqual(run.afterReads.map(pageOf), [2]); assert.equal(run.writes().length, 1);
});

test('connector last-page recovery: confirmed zero total reads page one and shows genuine empty inventory', async () => {
    const run = await preparedLastPage({ totalAfter: 0 });
    await run.perform(); assertSaved();
    assert.deepEqual(run.afterReads.map(pageOf), [2, 1]);
    assert.match(text(), /No Connectors Found/); assert.equal(find('.page-info'), null);
    assert.equal(run.writes().length, 1);
});

test('connector last-page control: invalid total is an error and cannot select a recovery page', async () => {
    const run = await preparedLastPage({ totalAfter: -1 });
    await run.perform(); assertSaved();
    assert.match(text(), /connector list response is incomplete/); assert.doesNotMatch(text(), /No Connectors Found/);
    assert.deepEqual(run.afterReads.map(pageOf), [2]); assert.equal(run.writes().length, 1);
});

test('connector last-page ownership: a stale shrinking total cannot reset the new filter page', async () => {
    const pending = deferred();
    const run = await preparedLastPage({ afterRead: (request, page) => requestURL(request).searchParams.get('status') === 'pending'
        ? pending.promise : listResponse(21, page, [row(page === 2 ? 21 : 1)]) });
    await run.perform(); assertSaved();
    await select('Connector status', 'error');
    await act(async () => find('button[aria-label="Next connectors page"]').click()); await settle();
    assert.match(text(), /Page 2 of 2/);
    const readsBefore = run.reads().length;
    await act(async () => pending.resolve(listResponse(20, 2))); await settle();
    assert.equal(run.reads().length, readsBefore); assert.match(text(), /Page 2 of 2/);
    assert.equal(find('select[aria-label="Connector status"]').value, 'error'); assert.equal(run.writes().length, 1);
});

test('connector last-page ownership: delayed corrected read cannot replace the new filter', async () => {
    const pending = deferred();
    const run = await preparedLastPage({ afterRead: (request, page) => {
        if (requestURL(request).searchParams.get('status') === 'error') return listResponse(1, 1, [{ ...row(1), tenant_name: 'Current error filter' }]);
        return page === 1 ? pending.promise : undefined;
    } });
    await run.perform(); assertSaved();
    assert.deepEqual(run.afterReads.map(pageOf), [2, 1]); assert.match(text(), /Loading connectors/);
    await select('Connector status', 'error');
    await act(async () => pending.resolve(listResponse(20, 1, [{ ...row(1), tenant_name: 'Stale corrected filter' }])));
    assert.match(text(), /Current error filter/); assert.doesNotMatch(text(), /Stale corrected filter/);
    assertSaved(); assert.equal(run.writes().length, 1);
});

test('connector last-page ownership: session replacement retires corrected read and prior receipt', async () => {
    const pending = deferred();
    const run = await preparedLastPage({ afterRead: (_request, page) => page === 1 ? pending.promise : undefined });
    await run.perform(); assertSaved(); assert.deepEqual(run.afterReads.map(pageOf), [2, 1]);
    state.route = () => listResponse(1, 1, [{ ...row(1), tenant_name: 'New session connector' }]);
    await act(async () => api.setToken('different-synthetic-session')); await settle();
    await act(async () => pending.resolve(listResponse(20, 1, [{ ...row(1), tenant_name: 'Retired session connector' }])));
    assert.match(text(), /New session connector/); assert.doesNotMatch(text(), /Retired session connector|Disabled locally/);
    assert.equal(run.writes().length, 1);
});

test('connector last-page focus: removed invoking row returns focus to stable table search', async () => {
    const run = await preparedLastPage(); await run.perform(); assertSaved();
    console.log('connector_focus_observation', JSON.stringify({ active_tag: document.activeElement?.tagName,
        active_label: document.activeElement?.getAttribute('aria-label') }));
    assert.ok(document.activeElement === find('input[aria-label="Search connectors on this page"]'), 'Focus returns to the stable search field');
    assert.equal(run.writes().length, 1);
});

test('connector last-page focus: a delayed corrected read does not steal deliberate filter focus', async () => {
    const pending = deferred();
    const run = await preparedLastPage({ afterRead: (_request, page) => page === 1 ? pending.promise : undefined });
    await run.perform(); assertSaved(); assert.deepEqual(run.afterReads.map(pageOf), [2, 1]);
    assert.ok(document.activeElement === find('input[aria-label="Search connectors on this page"]'), 'Focus returns to search before the delayed read completes');
    const status = find('select[aria-label="Connector status"]'); await act(async () => status.focus());
    await act(async () => pending.resolve(listResponse(20, 1))); await settle();
    assert.ok(document.activeElement === status, 'Completing the corrected read preserves deliberate filter focus'); assert.equal(run.writes().length, 1);
});

test('connector last-page focus control: cancel restores the still-existing invoking button without a write', async () => {
    const run = await preparedLastPage(); const origin = await run.open(); await click('Cancel');
    assert.ok(document.activeElement === origin, 'Cancel returns focus to the existing invoking button'); assert.equal(run.writes().length, 0);
});
