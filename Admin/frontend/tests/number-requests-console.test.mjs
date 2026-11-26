// Phone Number Requests pages through the real React renderer with a stubbed API:
// the list (status tabs, pager, one read and no timer), one request (documents,
// download, approve, reject), the server's whole error table shown word for word,
// the second-decision 409 that is never resent, and the roles that are refused.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, dom, state, render, response, deferred, find, text, click, button, settle, sleep } from './bh-harness.mjs';

// jsdom does not implement anchor navigation; record what a download would be saved as.
const downloads = [];
const realClick = dom.window.HTMLAnchorElement.prototype.click;
dom.window.HTMLAnchorElement.prototype.click = function () {
    if (this.hasAttribute('download')) { downloads.push(this.download); return; }
    realClick.call(this);
};
test.after(() => { dom.window.HTMLAnchorElement.prototype.click = realClick; });
test.beforeEach(() => { downloads.length = 0; });

const PLATFORM = { id: 'admin-A', email: 'a@example.invalid', name: 'Admin A', role: 'platform_admin', tenant_id: null };
const TENANT = '6b1d0d8e-2222-4bbb-9ccc-000000000002';
const OTHER_TENANT = '7c2e1e9f-3333-4ccc-8ddd-000000000003';
const REQUEST = '0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d';
const DOC_A = 'd0c00001-0000-4000-8000-000000000001';
const DOC_B = 'd0c00002-0000-4000-8000-000000000002';
const USER = '11111111-aaaa-4bbb-8ccc-ddddeeeeffff';
const STAMP = '2026-11-25T12:00:00+00:00';
const BASE = '/admin/telephony/number-requests';
const PLATFORM_ROLES = ['platform_admin', 'super_admin'];
const INCOMPLETE = 'The server returned an incomplete or unexpected response.';

const item = (over = {}) => ({ id: REQUEST, tenant_id: TENANT, business_name: 'Acme Dental', requested_by_user_id: USER,
    use_type: 'business', status: 'submitted', submitted_at: STAMP, reviewed_at: null, files_deleted: false, ...over });
const page = (over = {}) => ({ items: [item()], limit: 20, offset: 0, has_more: false, ...over });
const doc = (over = {}) => ({ id: DOC_A, kind: 'business_registration', file_name: 'registration.pdf', content_type: 'application/pdf',
    size_bytes: 20480, sha256: 'a'.repeat(64), scan_status: 'not_scanned', created_at: STAMP, available: true, ...over });
const detail = (over = {}) => ({ id: REQUEST, tenant_id: TENANT, business_name: 'Acme Dental', requested_by_user_id: USER,
    requester_email: 'owner@example.invalid', use_type: 'business', status: 'submitted', review_note: null, reviewed_by_user_id: null,
    reviewed_at: null, submitted_at: STAMP, files_deleted: false,
    documents: [doc(), doc({ id: DOC_B, kind: 'business_photo_id', content_type: 'image/jpeg', size_bytes: 3 * 1048576, scan_status: 'clean' })], ...over });
const answer = (over = {}) => ({ id: REQUEST, status: 'approved', reviewed_at: STAMP, email: 'sent', ...over });
const failure = (status, code, message, details = null) => response({ error: { code, message, details, request_id: 'req-1' } }, status);
const file = (type = 'application/pdf') => new Response(new Blob(['%PDF-1.7 synthetic'], { type }), { status: 200, headers: { 'Content-Type': type } });

/** The four staff routes; each answer can be replaced. Anything else is the harness's unrouted 500. */
function backend(over = {}, me = PLATFORM) {
    const world = { list: () => response(page()), detail: () => response(detail()), document: () => file(), decision: () => response(answer()),
        refresh: () => response({ detail: 'No refresh token.' }, 401), ...over };
    state.route = (request) => {
        if (request.path.endsWith('/auth/me')) return response(me);
        if (request.path.endsWith('/auth/refresh')) return world.refresh(request);
        const url = new URL(request.path, 'https://fixture.invalid');
        const match = url.pathname.match(/\/admin\/telephony\/number-requests(?:\/([^/]+)\/([^/]+)(?:\/documents\/([^/]+)|\/(decision))?)?$/);
        if (!match) return response({ detail: 'unrouted synthetic request' }, 500);
        if (!match[1]) return world.list(url, request);
        if (match[3]) return world.document(match.slice(1, 4), request);
        if (match[4]) return world.decision(request.body, request);
        return world.detail(match.slice(1, 3), request);
    };
    return world;
}
const calls = () => state.requests.filter((request) => request.path.includes(BASE));
const listCalls = () => calls().filter((request) => request.method === 'GET' && /number-requests\?/.test(request.path));
const detailCalls = () => calls().filter((request) => request.method === 'GET' && request.path.endsWith(`${BASE}/${TENANT}/${REQUEST}`));
const documentCalls = () => calls().filter((request) => request.path.includes('/documents/'));
const posts = () => calls().filter((request) => request.method === 'POST');

let drawn = 0;
const tree = (path, extraRoutes = []) => el(C.AuthProvider, { key: ++drawn }, el(C.MemoryRouter, { initialEntries: [path] },
    el(C.Routes, null,
        el(C.Route, { path: '/number-requests', element: el(C.AdminRouteGuard, { allowedRoles: PLATFORM_ROLES }, el(C.NumberRequestsPage)) }),
        el(C.Route, { path: '/number-requests/:tenantId/:requestId', element: el(C.AdminRouteGuard, { allowedRoles: PLATFORM_ROLES }, el(C.NumberRequestPage)) }),
        el(C.Route, { path: '/login', element: el('p', null, 'login page') }),
        ...extraRoutes)));
async function draw(path, extraRoutes) { await render(tree(path, extraRoutes)); await settle(); }
const REQUEST_PATH = `/number-requests/${TENANT}/${REQUEST}`;

const dialog = () => find('[role="dialog"]');
const dialogButton = (label) => {
    const match = [...(dialog()?.querySelectorAll('button') ?? [])].find((node) => node.textContent.trim() === label);
    assert.ok(match, `Missing dialog button: ${label}; DOM text: ${text()}`); return match;
};
const pressInDialog = async (label) => { await act(async () => dialogButton(label).click()); await settle(); };
const setReason = async (value) => {
    const node = find('[role="dialog"] textarea');
    await act(async () => {
        Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(node, value);
        node.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    });
};
const tab = (label) => [...state.host.querySelectorAll('nav[aria-label="Filter by status"] a')].find((node) => node.textContent.trim() === label);
const currentTab = () => [...state.host.querySelectorAll('nav[aria-label="Filter by status"] a[aria-current="true"]')].map((node) => node.textContent.trim());
const alerts = () => [...state.host.querySelectorAll('[role="alert"]')].map((node) => node.textContent.trim());

// ───────────────────────── the list ─────────────────────────

test('the list reads submitted requests once with limit 20 and offset 0, and shows exactly the fields the server returns', async () => {
    backend({ list: () => response(page({ items: [
        item(),
        item({ id: 'aaaaaaaa-0000-4000-8000-00000000000a', tenant_id: OTHER_TENANT, business_name: null, use_type: 'personal', status: 'submitted' }),
    ] })) });
    await draw('/number-requests');
    assert.match(listCalls()[0].path, /\/admin\/telephony\/number-requests\?status=submitted&limit=20&offset=0$/);
    assert.equal(find('h1').textContent, 'Phone Number Requests');
    assert.match(text(), /Phone number requests across every company\. Open a request to download its documents, then approve or reject it\./);
    assert.deepEqual([...state.host.querySelectorAll('thead th')].map((node) => node.textContent), ['Company', 'Use', 'Status', 'Submitted', 'Reviewed', 'Files']);
    const rows = [...state.host.querySelectorAll('tbody tr')].map((row) => [...row.querySelectorAll('td')].map((cell) => cell.textContent));
    assert.equal(rows.length, 2);
    assert.deepEqual([rows[0][0], rows[0][1], rows[0][2], rows[0][4], rows[0][5]], ['Acme Dental', 'Business Use', 'Submitted', '—', 'Available']);
    assert.deepEqual([rows[1][0], rows[1][1]], [OTHER_TENANT, 'Personal Use'], 'the account id stands in for a missing company name');
    assert.equal(rows[0][3], new Date(STAMP).toLocaleString());
    assert.ok(find(`a[href="/number-requests/${TENANT}/${REQUEST}"]`), 'a row links to its request page');
    assert.deepEqual(currentTab(), ['Submitted']);
    assert.deepEqual([...state.host.querySelectorAll('nav[aria-label="Filter by status"] a')].map((node) => node.textContent.trim()), ['Submitted', 'Approved', 'Rejected']);
    assert.equal(button('Previous page').disabled, true);
    assert.equal(button('Next page').disabled, true);
    assert.match(text(), /Page 1/);
    assert.ok(find('a.sidebar-nav-item[href="/number-requests"]'), 'the sidebar entry is shown to platform staff');
    assert.equal(find('a.sidebar-nav-item[href="/number-requests"]').textContent.trim(), 'Phone Number Requests');
});

test('a deleted-files row says Deleted and shows its review date', async () => {
    backend({ list: () => response(page({ items: [item({ status: 'rejected', reviewed_at: STAMP, files_deleted: true })] })) });
    await draw('/number-requests?status=rejected');
    const cells = [...state.host.querySelectorAll('tbody td')].map((cell) => cell.textContent);
    assert.deepEqual([cells[2], cells[4], cells[5]], ['Rejected', new Date(STAMP).toLocaleString(), 'Deleted']);
});

test('each tab re-reads its status, is kept in the address, and restarts at the first page', async () => {
    backend({ list: (url) => response(page({ items: [item({ status: url.searchParams.get('status'), business_name: `Co ${url.searchParams.get('status')}`,
        reviewed_at: url.searchParams.get('status') === 'submitted' ? null : STAMP })], offset: Number(url.searchParams.get('offset')), has_more: true })) });
    await draw('/number-requests?offset=40');
    assert.match(listCalls().at(-1).path, /status=submitted&limit=20&offset=40$/);
    assert.match(text(), /Page 3/);
    for (const [label, status] of [['Approved', 'approved'], ['Rejected', 'rejected'], ['Submitted', 'submitted']]) {
        await act(async () => tab(label).click());
        await settle();
        assert.match(listCalls().at(-1).path, new RegExp(`status=${status}&limit=20&offset=0$`), label);
        assert.deepEqual(currentTab(), [label], 'the marked tab is read from the address');
        assert.match(text(), new RegExp(`Co ${status}`));
        assert.match(text(), /Page 1/);
    }
    assert.equal(tab('Approved').getAttribute('href'), '/number-requests?status=approved');
    assert.equal(tab('Submitted').getAttribute('href'), '/number-requests');
});

test('the pager moves the offset by 20, and Next page is off on the last page and at the server\'s offset limit', async () => {
    backend({ list: (url) => response(page({ offset: Number(url.searchParams.get('offset')), has_more: Number(url.searchParams.get('offset')) < 40,
        items: [item({ business_name: `Offset ${url.searchParams.get('offset')}` })] })) });
    await draw('/number-requests?status=approved');
    await click('Next page'); await settle();
    assert.match(listCalls().at(-1).path, /status=approved&limit=20&offset=20$/);
    assert.match(text(), /Offset 20/); assert.match(text(), /Page 2/);
    await click('Next page'); await settle();
    assert.match(listCalls().at(-1).path, /offset=40$/);
    assert.equal(button('Next page').disabled, true, 'has_more is false');
    await click('Previous page'); await settle();
    assert.match(listCalls().at(-1).path, /status=approved&limit=20&offset=20$/);
    assert.deepEqual(currentTab(), ['Approved']);

    backend({ list: () => response(page({ offset: 1000, has_more: true })) });
    await draw('/number-requests?offset=1000');
    assert.match(listCalls().at(-1).path, /offset=1000$/);
    assert.equal(button('Next page').disabled, true, 'offset 1020 would be refused by the server');
    assert.equal(button('Previous page').disabled, false);
});

test('unknown address values fall back to submitted and the first page', async () => {
    backend();
    await draw('/number-requests?status=deleted&offset=5000');
    assert.match(listCalls().at(-1).path, /status=submitted&limit=20&offset=0$/);
    assert.deepEqual(currentTab(), ['Submitted']);
});

test('loading, then the empty line; an empty list is not an error', async () => {
    const gate = deferred();
    // One fresh answer per read: a Response body can be read only once.
    backend({ list: () => gate.promise.then(() => response(page({ items: [] }))) });
    await draw('/number-requests');
    assert.match(text(), /Loading requests…/);
    await act(async () => { gate.resolve(); await sleep(20); });
    await settle();
    assert.match(text(), /No requests with this status\./);
    assert.doesNotMatch(text(), /Loading requests…/);
    assert.deepEqual(alerts(), []);
    assert.equal(Boolean(find('table')), false);
});

for (const [label, body] of [
    ['a missing has_more', { items: [], limit: 20, offset: 0 }],
    ['a row with an unknown status', page({ items: [item({ status: 'deleted' })] })],
    ['items that is not a list', page({ items: null })],
    ['an empty object', {}],
]) {
    test(`a list answer with ${label} shows the incomplete-answer line, never an empty list`, async () => {
        backend({ list: () => response(body) });
        await draw('/number-requests');
        assert.deepEqual(alerts(), [`Requests unavailable: ${INCOMPLETE}`]);
        assert.doesNotMatch(text(), /No requests with this status\./);
        assert.equal(Boolean(find('table')), false);
        assert.equal(button('Retry').disabled, false);
    });
}

const LIST_ERRORS = [
    [403, 'forbidden', 'Platform admin access required'],
    [403, 'forbidden', 'Current platform admin authority is required.'],
    [422, 'validation_error', 'Request payload failed validation', [{ type: 'literal_error', loc: ['query', 'status'], msg: 'Input should be submitted, approved or rejected' }]],
    [503, 'staff_unavailable', 'This could not be completed. Nothing was changed. Try again.'],
];
for (const [status, code, message, details] of LIST_ERRORS) {
    test(`list ${status} ${code}: "${message}" is shown word for word`, async () => {
        backend({ list: () => failure(status, code, message, details) });
        await draw('/number-requests');
        assert.deepEqual(alerts(), [`Requests unavailable: ${message}`]);
        assert.doesNotMatch(text(), /No requests with this status\./);
        assert.doesNotMatch(text(), /Showing the last successful read/);
    });
}

test('list 401 with no session to renew ends the session: the sign-in page, no message kept on screen', async () => {
    backend({ list: () => failure(401, 'unauthorized', 'Current authenticated session is required.') });
    await draw('/number-requests');
    assert.match(text(), /login page/);
    assert.equal(posts().length, 0);
});

test('a failed Refresh keeps the last list on screen and says it may be stale; Retry reads again', async () => {
    let fail = false;
    backend({ list: () => fail ? failure(503, 'staff_unavailable', 'This could not be completed. Nothing was changed. Try again.') : response(page()) });
    await draw('/number-requests');
    assert.match(text(), /Acme Dental/);
    const before = listCalls().length;
    fail = true;
    await click('Refresh'); await settle();
    assert.equal(listCalls().length, before + 1);
    assert.deepEqual(alerts(), ['Requests unavailable: This could not be completed. Nothing was changed. Try again. Showing the last successful read, which may be stale.']);
    assert.match(text(), /Acme Dental/);
    fail = false;
    await click('Retry'); await settle();
    assert.deepEqual(alerts(), []);
    assert.equal(listCalls().length, before + 2);
});

test('no timer: after a wait the list has been read exactly once', async () => {
    backend();
    // Without StrictMode's development-only second mount, as in the shipped build.
    await act(async () => state.root.render(tree('/number-requests')));
    await settle();
    assert.equal(listCalls().length, 1);
    await settle(400);
    assert.equal(listCalls().length, 1, 'nothing re-reads the list on its own');
    assert.match(text(), /Acme Dental/);
});

// ───────────────────────── one request ─────────────────────────

test('the request page reads the request and shows its summary and one block per document', async () => {
    backend();
    await draw(REQUEST_PATH);
    assert.ok(detailCalls().length >= 1);
    assert.equal(find('h1').textContent, 'Phone number request');
    assert.equal(find('a[href="/number-requests"]:not(.sidebar-nav-item)').textContent, 'Back to the queue');
    const summary = Object.fromEntries([...state.host.querySelectorAll('.number-request-summary dl > div')].map((row) => [row.querySelector('dt').textContent, row.querySelector('dd').textContent]));
    assert.deepEqual(summary, { Company: 'Acme Dental', Use: 'Business Use', Status: 'Submitted', Requester: 'owner@example.invalid',
        Submitted: new Date(STAMP).toLocaleString(), Reviewed: '—' });
    assert.equal(find('.number-request-documents h2').textContent, 'Documents');
    const blocks = [...state.host.querySelectorAll('.number-request-document')].map((block) => ({
        title: block.querySelector('h3').textContent,
        fields: Object.fromEntries([...block.querySelectorAll('dl > div')].map((row) => [row.querySelector('dt').textContent, row.querySelector('dd').textContent])),
        caution: block.querySelector('.number-request-caution')?.textContent ?? null,
        download: Boolean([...block.querySelectorAll('button')].find((node) => node.textContent.trim() === 'Download')),
    }));
    assert.deepEqual(blocks, [
        { title: 'Business registration', fields: { Type: 'PDF', Size: '20 KB', 'Virus scan': 'Not scanned' },
            caution: 'That document has not been cleared by a virus scan.', download: true },
        { title: 'Photo ID matching the business registration', fields: { Type: 'JPG', Size: '3.0 MB', 'Virus scan': 'Clean' }, caution: null, download: true },
    ]);
    assert.doesNotMatch(text(), /registration\.pdf/, 'the customer\'s file name is not shown');
    assert.equal(button('Approve').disabled, false);
    assert.equal(button('Reject').disabled, false);
    assert.equal(posts().length, 0);
});

test('a personal request, missing values and the other document labels', async () => {
    backend({ detail: () => response(detail({ business_name: null, requester_email: null, use_type: 'personal', documents: [
        doc({ kind: 'utility_bill', content_type: 'image/png', scan_status: 'infected' }), doc({ id: DOC_B, kind: 'personal_photo_id', scan_status: 'clean' }),
    ] })) });
    await draw(REQUEST_PATH);
    const summary = Object.fromEntries([...state.host.querySelectorAll('.number-request-summary dl > div')].map((row) => [row.querySelector('dt').textContent, row.querySelector('dd').textContent]));
    assert.equal(summary.Company, TENANT);
    assert.equal(summary.Use, 'Personal Use');
    assert.equal(summary.Requester, '—');
    assert.deepEqual([...state.host.querySelectorAll('.number-request-document h3')].map((node) => node.textContent),
        ['Utility bill (proof of address)', 'Photo ID matching the utility bill']);
    assert.match(find('.number-request-document').textContent, /PNG/);
    assert.match(find('.number-request-document').textContent, /Infected/);
    assert.match(find('.number-request-document').textContent, /That document has not been cleared by a virus scan\./);
});

test('a decided request shows its reason and review date and offers no Approve or Reject', async () => {
    backend({ detail: () => response(detail({ status: 'rejected', review_note: 'The utility bill is older than three months', reviewed_by_user_id: USER, reviewed_at: STAMP })) });
    await draw(REQUEST_PATH);
    const summary = Object.fromEntries([...state.host.querySelectorAll('.number-request-summary dl > div')].map((row) => [row.querySelector('dt').textContent, row.querySelector('dd').textContent]));
    assert.equal(summary.Status, 'Rejected');
    assert.equal(summary.Reason, 'The utility bill is older than three months');
    assert.equal(summary.Reviewed, new Date(STAMP).toLocaleString());
    assert.equal([...state.host.querySelectorAll('button')].some((node) => ['Approve', 'Reject'].includes(node.textContent.trim())), false);
    assert.doesNotMatch(text(), new RegExp(USER), 'the reviewer id is not shown');
});

test('deleted files: no Download button and the deleted line instead', async () => {
    backend({ detail: () => response(detail({ status: 'rejected', reviewed_at: STAMP, reviewed_by_user_id: USER, files_deleted: true,
        documents: [doc({ available: false, file_name: null }), doc({ id: DOC_B, kind: 'business_photo_id', available: false, file_name: null, scan_status: 'clean' })] })) });
    await draw(REQUEST_PATH);
    assert.equal([...state.host.querySelectorAll('button')].some((node) => node.textContent.trim() === 'Download'), false);
    assert.deepEqual([...state.host.querySelectorAll('.number-request-deleted')].map((node) => node.textContent), ['That document has been deleted.', 'That document has been deleted.']);
    assert.equal(documentCalls().length, 0);
});

const downloadButtons = () => [...state.host.querySelectorAll('.number-request-document button')].filter((node) => node.textContent.trim() === 'Download');

test('Download asks for that document with the account, request and document ids and saves it under the server\'s name', async () => {
    backend({ document: ([, , id]) => file(id === DOC_A ? 'application/pdf' : 'image/jpeg') });
    await draw(REQUEST_PATH);
    await act(async () => downloadButtons()[0].click()); await settle(100);
    await act(async () => downloadButtons()[1].click()); await settle(100);
    assert.deepEqual(documentCalls().map((request) => [request.method, request.path]), [
        ['GET', `/api/v1${BASE}/${TENANT}/${REQUEST}/documents/${DOC_A}`],
        ['GET', `/api/v1${BASE}/${TENANT}/${REQUEST}/documents/${DOC_B}`],
    ]);
    assert.match(documentCalls()[0].authorization, /^Bearer synthetic-/);
    assert.equal(documentCalls()[0].credentials, 'include');
    assert.deepEqual(downloads, ['business_registration-d0c00001.pdf', 'business_photo_id-d0c00002.jpg']);
    assert.deepEqual(alerts(), []);
});

const DOCUMENT_ERRORS = [
    [404, 'document_not_found', 'That document was not found.'],
    [404, 'document_file_missing', 'The stored file for that document is missing.'],
    [410, 'document_deleted', 'That document has been deleted.'],
    [409, 'document_not_cleared', 'That document has not been cleared by a virus scan.', { scan_status: 'not_scanned' }],
    [500, 'document_unreadable', 'That document could not be read.'],
    [503, 'staff_unavailable', 'This could not be completed. Nothing was changed. Try again.'],
    [403, 'forbidden', 'Current platform admin authority is required.'],
    [422, 'validation_error', 'Request payload failed validation', [{ type: 'uuid_parsing', loc: ['path', 'document_id'], msg: 'Input should be a valid UUID' }]],
];
for (const [status, code, message, details] of DOCUMENT_ERRORS) {
    test(`download ${status} ${code}: "${message}" is shown beside that document, and nothing is saved`, async () => {
        backend({ document: () => failure(status, code, message, details) });
        await draw(REQUEST_PATH);
        await act(async () => downloadButtons()[1].click()); await settle(100);
        const blocks = [...state.host.querySelectorAll('.number-request-document')];
        assert.equal(Boolean(blocks[0].querySelector('[role="alert"]')), false);
        assert.equal(blocks[1].querySelector('[role="alert"]').textContent.trim(), message);
        assert.deepEqual(downloads, []);
        assert.equal(documentCalls().length, 1);
        assert.equal(downloadButtons()[1].disabled, false, 'the document can be asked for again');
    });
}

test('the request page says "Loading request…" while the read is pending, and not once the request or an error is shown', async () => {
    for (const [label, answer] of [
        ['the request', () => response(detail())],
        ['an error', () => failure(503, 'staff_unavailable', 'This could not be completed. Nothing was changed. Try again.')],
    ]) {
        let gate = deferred();
        // One fresh answer per read: a Response body can be read only once.
        backend({ detail: () => gate.promise.then(answer) });
        await draw(REQUEST_PATH);
        assert.deepEqual([...state.host.querySelectorAll('[role="status"]')].map((node) => node.textContent), ['Loading request…'], label);
        assert.equal(Boolean(find('.number-request-summary')), false, label);
        assert.deepEqual(alerts(), [], label);
        await act(async () => { gate.resolve(); await sleep(20); });
        await settle();
        assert.doesNotMatch(text(), /Loading request…/, label);
        assert.equal(state.host.querySelectorAll('[role="status"]').length, 0, label);
        if (label === 'an error') { assert.deepEqual(alerts(), ['This could not be completed. Nothing was changed. Try again.']); continue; }
        assert.equal(Boolean(find('.number-request-summary')), true);
        // A reload with the request already on screen keeps the request and shows no loading line.
        gate = deferred();
        await click('Reload');
        assert.equal(button('Reload').disabled, true, 'the read is pending');
        assert.doesNotMatch(text(), /Loading request…/);
        assert.equal(Boolean(find('.number-request-summary')), true);
        await act(async () => { gate.resolve(); await sleep(20); });
        await settle();
    }
});

const DETAIL_ERRORS = [
    [404, 'number_request_not_found', 'That request was not found.'],
    [403, 'forbidden', 'Current platform admin authority is required.'],
    [422, 'validation_error', 'Request payload failed validation', [{ type: 'uuid_parsing', loc: ['path', 'request_id'], msg: 'Input should be a valid UUID' }]],
    [503, 'staff_unavailable', 'This could not be completed. Nothing was changed. Try again.'],
];
for (const [status, code, message, details] of DETAIL_ERRORS) {
    test(`request ${status} ${code}: "${message}" is shown word for word`, async () => {
        backend({ detail: () => failure(status, code, message, details) });
        await draw(REQUEST_PATH);
        assert.deepEqual(alerts(), [message]);
        assert.equal(Boolean(find('.number-request-summary')), false);
        assert.equal([...state.host.querySelectorAll('button')].some((node) => ['Approve', 'Reject', 'Download'].includes(node.textContent.trim())), false);
    });
}

for (const [label, body] of [
    ['a missing documents list', (() => { const value = detail(); delete value.documents; return value; })()],
    ['a document with an unknown kind', detail({ documents: [doc({ kind: 'passport' })] })],
    ['an unknown status', detail({ status: 'deleted' })],
]) {
    test(`a request answer with ${label} shows the incomplete-answer line and no request`, async () => {
        backend({ detail: () => response(body) });
        await draw(REQUEST_PATH);
        assert.deepEqual(alerts(), [INCOMPLETE]);
        assert.equal(Boolean(find('.number-request-summary')), false);
    });
}

// ───────────────────────── deciding ─────────────────────────

test('Approve asks first, posts only {decision: "approved"}, shows the result line and reads the request again', async () => {
    let decided = false;
    backend({ detail: () => response(decided ? detail({ status: 'approved', reviewed_at: STAMP, reviewed_by_user_id: USER }) : detail()),
        decision: () => { decided = true; return response(answer()); } });
    await draw(REQUEST_PATH);
    await click('Approve');
    assert.equal(posts().length, 0, 'opening the dialog sends nothing');
    assert.equal(dialog().querySelector('#modal-title').textContent, 'Approve this request?');
    assert.match(dialog().textContent, /The customer will be emailed that their phone number request was approved\. This cannot be undone\./);
    assert.equal(Boolean(dialog().querySelector('textarea')), false, 'Approve has no note field');
    const readsBefore = detailCalls().length;
    await pressInDialog('Approve');
    assert.equal(posts().length, 1);
    assert.equal(posts()[0].path, `/api/v1${BASE}/${TENANT}/${REQUEST}/decision`);
    assert.deepEqual(posts()[0].body, { decision: 'approved' });
    assert.equal(Boolean(dialog()), false);
    assert.equal(find('[role="status"]').textContent, 'Approved. The customer was emailed.');
    assert.equal(detailCalls().length, readsBefore + 1, 'the request is read again after the decision');
    assert.match(find('.number-request-summary').textContent, /Approved/);
    assert.equal([...state.host.querySelectorAll('button')].some((node) => ['Approve', 'Reject'].includes(node.textContent.trim())), false);
});

test('Reject cannot be confirmed with a blank reason, and posts the trimmed reason', async () => {
    let decided = false;
    backend({ detail: () => response(decided ? detail({ status: 'rejected', review_note: 'Too old', reviewed_at: STAMP, reviewed_by_user_id: USER }) : detail()),
        decision: () => { decided = true; return response(answer({ status: 'rejected' })); } });
    await draw(REQUEST_PATH);
    await click('Reject');
    assert.equal(dialog().querySelector('#modal-title').textContent, 'Reject this request?');
    assert.match(dialog().textContent, /The customer will be emailed that their phone number request was not approved\. This cannot be undone\./);
    assert.equal(dialog().querySelector('.modal-reason-field > span').textContent, 'Reason');
    assert.equal(dialog().querySelector('#modal-reason-help').textContent, 'The customer is emailed this reason.');
    assert.equal(dialog().querySelector('textarea').getAttribute('placeholder'), '');
    assert.equal(dialog().querySelector('textarea').maxLength, 1000);
    assert.equal(dialogButton('Reject').disabled, true, 'empty');
    await setReason('   \n  ');
    assert.equal(dialogButton('Reject').disabled, true, 'blank');
    await act(async () => dialogButton('Reject').click());
    assert.equal(posts().length, 0);
    await setReason('ok');
    assert.equal(dialogButton('Reject').disabled, false, 'any non-blank reason is accepted, as on the server');
    assert.equal(dialog().querySelector('#modal-reason-help').textContent, 'The customer is emailed this reason.', 'the only helper line');
    await setReason('  Too old  ');
    const readsBefore = detailCalls().length;
    await pressInDialog('Reject');
    assert.deepEqual(posts().map((request) => request.body), [{ decision: 'rejected', note: 'Too old' }]);
    assert.equal(find('[role="status"]').textContent, 'Rejected. The customer was emailed.');
    assert.equal(detailCalls().length, readsBefore + 1);
    assert.match(find('.number-request-summary').textContent, /Too old/);
});

for (const decision of ['approved', 'rejected']) {
    for (const [email, line] of [['sent', 'The customer was emailed.'], ['failed', 'The email to the customer failed.'],
        ['skipped', 'No email address is on file, so no email was sent.'], ['off', 'Decision emails are turned off.']]) {
        test(`${decision} with email "${email}" shows its exact result line`, async () => {
            backend({ decision: () => response(answer({ status: decision, email })) });
            await draw(REQUEST_PATH);
            const label = decision === 'approved' ? 'Approve' : 'Reject';
            await click(label);
            if (decision === 'rejected') await setReason('Photo is unreadable');
            await pressInDialog(label);
            assert.equal(find('[role="status"]').textContent, `${decision === 'approved' ? 'Approved.' : 'Rejected.'} ${line}`);
            assert.equal(posts().length, 1);
        });
    }
}

test('while a decision is being sent nothing can be sent twice, and the dialog cannot be closed', async () => {
    const gate = deferred();
    backend({ decision: () => gate.promise });
    await draw(REQUEST_PATH);
    await click('Approve');
    await act(async () => dialogButton('Approve').click());
    assert.match(dialog().textContent, /Processing\.\.\./);
    assert.equal(dialogButton('Cancel').disabled, true);
    const confirm = [...dialog().querySelectorAll('.modal-footer button')].at(-1);
    assert.equal(confirm.disabled, true);
    await act(async () => { confirm.click(); confirm.click(); });
    await act(async () => document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    assert.ok(dialog(), 'Escape does not close a busy dialog');
    assert.equal(posts().length, 1);
    await act(async () => { gate.resolve(response(answer())); await sleep(20); });
    await settle();
    assert.equal(posts().length, 1);
    assert.equal(find('[role="status"]').textContent, 'Approved. The customer was emailed.');
});

test('a second decision: the 409 message shows, no second POST is sent, and Reload reads the request again', async () => {
    let decidedElsewhere = false;
    backend({ detail: () => response(decidedElsewhere ? detail({ status: 'approved', reviewed_at: STAMP, reviewed_by_user_id: USER }) : detail()),
        decision: () => { decidedElsewhere = true; return failure(409, 'already_decided', 'That request has already been decided.', { status: 'approved' }); } });
    await draw(REQUEST_PATH);
    await click('Reject');
    await setReason('Not the right document');
    await pressInDialog('Reject');
    assert.ok(dialog(), 'the dialog stays open');
    assert.equal(dialog().querySelector('[role="alert"]').textContent.trim(), 'That request has already been decided.');
    assert.equal(dialogButton('Reject').disabled, true, 'the refused decision cannot be sent again');
    await act(async () => dialogButton('Reject').click());
    assert.equal(posts().length, 1);
    await pressInDialog('Cancel');
    assert.equal(Boolean(dialog()), false);
    assert.deepEqual(alerts(), ['That request has already been decided.Reload']);
    assert.equal(button('Approve').disabled, true);
    assert.equal(button('Reject').disabled, true);
    const reads = detailCalls().length;
    // The banner's own Reload (the page header has one too).
    await act(async () => find('.number-request-decision [role="alert"] button').click());
    await settle();
    assert.equal(detailCalls().length, reads + 1);
    assert.equal(posts().length, 1, 'a reload only reads; it never replays the refused decision');
    assert.match(find('.number-request-summary').textContent, /Approved/);
    assert.deepEqual(alerts(), []);
    assert.equal([...state.host.querySelectorAll('button')].some((node) => ['Approve', 'Reject'].includes(node.textContent.trim())), false);
});

test('503 decision_unconfirmed: the server\'s message, nothing sent again until the request has been reloaded', async () => {
    let attempts = 0;
    backend({ decision: () => { attempts += 1; return failure(503, 'decision_unconfirmed', 'The decision is not confirmed. Read the request again before trying again.'); } });
    await draw(REQUEST_PATH);
    await click('Approve');
    await pressInDialog('Approve');
    assert.equal(dialog().querySelector('[role="alert"]').textContent.trim(), 'The decision is not confirmed. Read the request again before trying again.');
    assert.equal(dialogButton('Approve').disabled, true);
    await act(async () => dialogButton('Approve').click());
    assert.equal(attempts, 1);
    await act(async () => dialog().querySelector('button[aria-label="Close"]').click());
    await settle();
    assert.deepEqual(alerts(), ['The decision is not confirmed. Read the request again before trying again.Reload']);
    assert.equal(button('Approve').disabled, true);
    await act(async () => find('.number-request-decision [role="alert"] button').click());
    await settle();
    assert.deepEqual(alerts(), []);
    assert.equal(button('Approve').disabled, false, 'after the reload the still-submitted request can be decided');
    assert.equal(attempts, 1);
});

const DECISION_ERRORS = [
    [422, 'note_required', 'Give a reason when you reject a request.', { field: 'note' }],
    [404, 'number_request_not_found', 'That request was not found.'],
    [503, 'service_unavailable', 'Current administrator authority could not be confirmed.'],
    [403, 'forbidden', 'Current platform admin authority is required.'],
    [422, 'validation_error', 'Request payload failed validation', [{ type: 'string_too_long', loc: ['body', 'note'], msg: 'String should have at most 1000 characters' }]],
];
for (const [status, code, message, details] of DECISION_ERRORS) {
    test(`decision ${status} ${code}: "${message}" is shown in the open dialog, and nothing is sent again on its own`, async () => {
        backend({ decision: () => failure(status, code, message, details) });
        await draw(REQUEST_PATH);
        await click('Reject');
        await setReason('A reason');
        await pressInDialog('Reject');
        assert.ok(dialog(), 'the dialog stays open');
        assert.equal(dialog().querySelector('[role="alert"]').textContent.trim(), message);
        assert.equal(dialog().querySelector('textarea').value, 'A reason', 'the reason is kept');
        await settle(100);
        assert.equal(posts().length, 1);
        assert.equal(Boolean(find('[role="status"]')), false, 'no result line');
    });
}

test('a session renewed during the decision: the client\'s own message, and the decision is not replayed', async () => {
    let sent = 0;
    backend({ decision: () => { sent += 1; return failure(401, 'unauthorized', 'Current authenticated session is required.'); },
        refresh: () => response({ access_token: 'renewed-token', user_id: PLATFORM.id, tenant_id: null, role: 'platform_admin' }) });
    await draw(REQUEST_PATH);
    await click('Approve');
    await pressInDialog('Approve');
    assert.equal(sent, 1, 'a mutation is never replayed after a renewal');
    assert.equal(dialog().querySelector('[role="alert"]').textContent.trim(), 'Your session was renewed. Review the current state, then confirm the action again.');
    assert.equal(dialogButton('Approve').disabled, false);
});

test('an unreadable decision answer shows the incomplete-answer line and waits for a reload', async () => {
    backend({ decision: () => response({ id: REQUEST, status: 'approved', reviewed_at: STAMP, email: 'queued' }) });
    await draw(REQUEST_PATH);
    await click('Approve');
    await pressInDialog('Approve');
    assert.equal(dialog().querySelector('[role="alert"]').textContent.trim(), INCOMPLETE);
    assert.equal(dialogButton('Approve').disabled, true);
    assert.equal(posts().length, 1);
    assert.equal(Boolean(find('[role="status"]')), false);
});

// ───────────────────────── who may see it ─────────────────────────

for (const role of ['partner_admin', 'tenant_admin', 'user', 'readonly']) {
    test(`${role}: Access Denied at both addresses, no sidebar entry, and no number-request call`, async () => {
        const me = { ...PLATFORM, id: `id-${role}`, role, tenant_id: role === 'partner_admin' ? null : 'tenant-uuid' };
        backend({ list: () => response({ detail: 'must not be called' }, 500), detail: () => response({ detail: 'must not be called' }, 500) }, me);
        const sidebarOnly = [el(C.Route, { key: 'side', path: '/side', element: el(C.Sidebar) })];
        for (const path of ['/number-requests', REQUEST_PATH, '/number-requests?status=approved']) {
            await draw(path, sidebarOnly);
            assert.match(text(), /Access Denied/, path);
            assert.doesNotMatch(text(), /Phone Number Requests|Phone number request/, path);
        }
        await draw('/side', sidebarOnly);
        assert.equal(Boolean(find('a[href="/number-requests"]')), false, 'no sidebar entry');
        assert.equal(C.canAccessAdminRoute('/number-requests', role), false);
        assert.equal(C.canAccessAdminRoute(REQUEST_PATH, role), false);
        assert.equal(calls().length, 0);
    });
}

test('platform staff reach both addresses; the nested request address shares the list\'s rule', () => {
    for (const role of ['platform_admin', 'super_admin']) {
        assert.equal(C.canAccessAdminRoute('/number-requests', role), true);
        assert.equal(C.canAccessAdminRoute(REQUEST_PATH, role), true);
    }
    assert.deepEqual(C.ADMIN_ROUTES.at(-1), { id: 'number-requests', label: 'Phone Number Requests', path: '/number-requests', platformOnly: true });
    assert.equal(C.canAccessAdminRoute('/number-requestsX', 'platform_admin'), false);
    assert.equal(api.getAuthGeneration() >= 0, true);
});
