// FX7a follow-ups to FX6d.
// 0.1 (P6): from 1280 up the Campaigns table fits its box: its own styles
//           tighten the cells and let long campaign and tenant names wrap.
// 0.3 (2.1): the Calls history table returns to page 1 only when the search
//           really changed, so a "Next" click right after load stays on page 2.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { C, act, el, find, render, response, setInput, settle, state } from './bh-harness.mjs';

const TENANT = '20000000-0000-4000-8000-000000000001';
const LONG_NAME = 'Campaign with a long descriptive name that runs on for quite a while AndOneVeryLongUnbrokenWordWithNoSpacesAtAll';
const LONG_TENANT = 'Synthetic tenant with a very long registered business name for wrapping';
const query = (request) => new URL(`https://x.invalid${request.path}`).searchParams;
const pager = () => [...state.host.querySelectorAll('.pagination-controls button')];

// ---------------------------------------------------------------- 0.1

const css = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
/** The body of the one media block that holds the Campaigns table's own rules. */
function campaignsBlock() {
    const start = css.indexOf('@media (min-width: 1280px) {\n  .data-table.campaigns-table');
    assert.equal(start !== -1, true, 'the Campaigns table rules sit in a min-width: 1280px block');
    const end = css.indexOf('\n}\n', start);
    return css.slice(start, end);
}

test('0.1 campaigns table: carries its own class, and only the campaign and tenant cells may wrap anywhere', async () => {
    const campaign = {
        id: '10000000-0000-4000-8000-000000000001', tenant_id: TENANT, tenant_name: LONG_TENANT, name: LONG_NAME, status: 'running', direction: 'outbound',
        created_at: '2026-10-21T09:30:00Z', updated_at: '2026-10-22T09:30:00Z', started_at: '2026-10-21T10:00:00Z', contacts: 101, calls: 201, live_calls: 1,
    };
    state.route = (request) => request.path.startsWith('/api/v1/admin/campaigns') ? response({ items: [campaign], total: 1, page: 1, page_size: 20 })
        : request.path.startsWith('/api/v1/admin/tenants') ? response([{ id: TENANT, business_name: LONG_TENANT }])
            : response({ detail: 'Unexpected synthetic request' }, 503);
    await render(el(C.CampaignsTable));
    await settle();
    const table = find('.table-container table');
    assert.equal(table !== null, true);
    assert.equal(table.className, 'data-table campaigns-table');
    assert.deepEqual([...table.querySelectorAll('thead th')].map((node) => node.textContent),
        ['Campaign', 'Tenant', 'Status', 'Direction', 'Contacts', 'Calls', 'Live Calls', 'Created', 'Started']);
    const cells = [...table.querySelectorAll('tbody tr:first-child td')];
    assert.deepEqual(cells.map((node) => node.classList.contains('campaigns-table-wrap')), [true, true, false, false, false, false, false, false, false]);
    // No text is removed: both long names are printed whole.
    assert.equal(cells[0].textContent, LONG_NAME);
    assert.equal(cells[1].textContent, LONG_TENANT);
});

test('0.1 campaigns table: from 1280 up its own rules tighten the cells and wrap long names', () => {
    const block = campaignsBlock();
    assert.match(block, /\.data-table\.campaigns-table th,\n  \.data-table\.campaigns-table td \{\n    padding-left: 12px;\n    padding-right: 12px;\n  \}/);
    assert.match(block, /\.data-table\.campaigns-table td\.campaigns-table-wrap \{\n    overflow-wrap: anywhere;\n  \}/);
    // Every selector in the block is the Campaigns table's own.
    const selectors = block.split('\n').filter((line) => /[{,]$/.test(line.trim()) && !line.startsWith('@media')).map((line) => line.trim());
    assert.equal(selectors.length, 3);
    assert.equal(selectors.every((selector) => selector.startsWith('.data-table.campaigns-table ')), true, selectors.join(' | '));
    // Nothing outside that block names the table, so below 1280 it is styled as before.
    assert.equal(css.replace(block, '').includes('campaigns-table'), false);
});

test('0.1 campaigns table: the shared table box still scrolls sideways, unchanged', () => {
    assert.equal(css.includes('.table-container {\n  overflow-x: auto;\n}\n'), true);
    assert.equal(css.split('.table-container {').length - 1, 1, 'one .table-container rule');
});

// ---------------------------------------------------------------- 0.3

const call = (n) => ({
    id: `30000000-0000-4000-8000-${String(n).padStart(12, '0')}`, tenant_id: TENANT, tenant_name: 'Synthetic tenant', phone_number: `+1555000${String(1000 + n)}`,
    status: 'completed', outcome: null, created_at: '2026-10-21T00:00:00Z', duration_seconds: 42, direction: 'outbound', has_recording: false, has_feedback: false,
});
const CALLS = Array.from({ length: 45 }, (_, index) => call(index + 1));
const isHistory = (request) => request.path.startsWith('/api/v1/admin/calls/history');
const historyReads = () => state.requests.filter(isHistory);
const routeCalls = () => {
    state.route = (request) => {
        if (isHistory(request)) {
            const page = Number(query(request).get('page'));
            return response({ items: CALLS.slice((page - 1) * 20, page * 20), total: CALLS.length, page, page_size: 20 });
        }
        return request.path.startsWith('/api/v1/admin/tenants') ? response([]) : response({ detail: 'Unexpected synthetic request' }, 503);
    };
};
const drawCalls = async () => { await render(el(C.CallHistoryTable, { onCallSelect() {} })); await settle(); };

test('0.3 calls: "Next" clicked right after load stays on page 2 once the search timer has run', async () => {
    routeCalls();
    await drawCalls();
    assert.equal(find('.page-indicator').textContent, 'Page 1 of 3');
    await act(async () => pager()[1].click());
    await settle();
    assert.equal(find('.page-indicator').textContent, 'Page 2 of 3');
    await settle(450); // longer than the 300 ms search debounce
    assert.equal(find('.page-indicator').textContent, 'Page 2 of 3');
    assert.equal(find('.pagination-info').textContent, 'Showing 21 - 40 of 45');
    assert.equal(find('tbody tr td.phone-cell').textContent, '+15550001021');
    // After the page 2 read nothing reads page 1 again.
    const pages = historyReads().map((request) => query(request).get('page'));
    assert.deepEqual(pages.slice(pages.indexOf('2')), ['2']);
});

test('0.3 calls: a search that really changed still returns to page 1, after the debounce', async () => {
    routeCalls();
    await drawCalls();
    await settle(450);
    await act(async () => pager()[1].click());
    await settle();
    assert.equal(find('.page-indicator').textContent, 'Page 2 of 3');
    await setInput(find('.search-input'), '+1555');
    await settle(100);
    assert.equal(query(historyReads().at(-1)).get('page'), '2', 'nothing is read before the debounce ends');
    await settle(350);
    assert.deepEqual(Object.fromEntries(query(historyReads().at(-1))), { page: '1', page_size: '20', search: '+1555' });
    assert.equal(find('.page-indicator').textContent, 'Page 1 of 3');
});

test('0.3 calls: clearing the search with Reset on a later page returns to page 1 without the search', async () => {
    routeCalls();
    await drawCalls();
    await setInput(find('.search-input'), '+1555');
    await settle(450);
    await act(async () => pager()[1].click());
    await settle();
    assert.deepEqual(Object.fromEntries(query(historyReads().at(-1))), { page: '2', page_size: '20', search: '+1555' });
    await act(async () => [...state.host.querySelectorAll('button')].find((node) => node.textContent.trim() === 'Reset').click());
    await settle(450);
    assert.deepEqual(Object.fromEntries(query(historyReads().at(-1))), { page: '1', page_size: '20' });
    assert.equal(find('.page-indicator').textContent, 'Page 1 of 3');
});
