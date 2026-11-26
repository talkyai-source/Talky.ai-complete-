// Phone Number Requests: the response validators, the server's error table read
// through the console's own error reader, the URL filters, the copied document
// labels and the download name. No DOM and no network.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { build } from 'esbuild';
import * as contracts from '../src/lib/number-request-contracts.ts';
import * as filters from '../src/lib/number-request-filters.ts';

// api.ts reads import.meta.env and imports without file extensions, so it is bundled the way bh-harness.mjs does it.
const bundle = await build({
    stdin: { contents: `export { normalizeAdminApiError } from './src/lib/api';`, resolveDir: fileURLToPath(new URL('..', import.meta.url)), loader: 'ts' },
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', define: { 'import.meta.env': '{}' },
});
const loaded = { exports: {} };
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), loaded, loaded.exports);
const { normalizeAdminApiError } = loaded.exports;

const TENANT = '6b1d0d8e-2222-4bbb-9ccc-000000000002';
const REQUEST = '0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d';
const USER = '11111111-aaaa-4bbb-8ccc-ddddeeeeffff';
const STAMP = '2026-11-25T12:00:00+00:00';
const item = (over = {}) => ({ id: REQUEST, tenant_id: TENANT, business_name: 'Acme Dental', requested_by_user_id: USER,
    use_type: 'business', status: 'submitted', submitted_at: STAMP, reviewed_at: null, files_deleted: false, ...over });
const page = (over = {}) => ({ items: [item()], limit: 20, offset: 0, has_more: false, ...over });
const doc = (over = {}) => ({ id: 'd0c00001-0000-4000-8000-000000000001', kind: 'business_registration', file_name: 'registration.pdf',
    content_type: 'application/pdf', size_bytes: 20480, sha256: 'a'.repeat(64), scan_status: 'not_scanned', created_at: STAMP, available: true, ...over });
const detail = (over = {}) => ({ id: REQUEST, tenant_id: TENANT, business_name: 'Acme Dental', requested_by_user_id: USER,
    requester_email: 'owner@example.com', use_type: 'business', status: 'submitted', review_note: null, reviewed_by_user_id: null,
    reviewed_at: null, submitted_at: STAMP, files_deleted: false, documents: [doc(), doc({ id: 'd0c00002-0000-4000-8000-000000000002', kind: 'business_photo_id' })], ...over });
const answer = (over = {}) => ({ id: REQUEST, status: 'approved', reviewed_at: STAMP, email: 'sent', ...over });
const without = (value, name) => { const copy = { ...value }; delete copy[name]; return copy; };

// Each field: values that must be refused (a missing field is always refused too).
const WRONG = { text: [null, 7, '', {}], nullableText: [7, {}, false], stamp: [7, {}, false, ''], flag: [null, 'true', 1], count: [null, '20', -1, 1.5] };

function refuses(valid, make, rules, label) {
    for (const [name, kind] of Object.entries(rules)) {
        assert.equal(valid(make(without(make(), name), true)), false, `${label}: missing ${name}`);
        for (const wrong of WRONG[kind]) assert.equal(valid(make({ [name]: wrong })), false, `${label}: ${name}=${JSON.stringify(wrong)}`);
    }
}

test('R1: a valid page and item pass; an empty list is valid; every missing or wrong field is refused', () => {
    assert.equal(contracts.validNumberRequestPage(page()), true);
    assert.equal(contracts.validNumberRequestPage(page({ items: [] })), true, 'no requests is a valid answer');
    assert.equal(contracts.validNumberRequestPage(page({ items: [item({ business_name: null, reviewed_at: null })] })), true);
    assert.equal(contracts.validNumberRequestPage(page({ items: [item({ status: 'approved', reviewed_at: STAMP, files_deleted: true, use_type: 'personal' })], has_more: true, offset: 20 })), true);

    for (const bad of [null, undefined, [], 'x', {}]) assert.equal(contracts.validNumberRequestPage(bad), false);
    refuses(contracts.validNumberRequestPage, (over = {}, whole = false) => whole ? over : page(over),
        { limit: 'count', offset: 'count', has_more: 'flag' }, 'page');
    for (const bad of [null, 'x', {}, undefined]) assert.equal(contracts.validNumberRequestPage(page({ items: bad })), false, `items=${JSON.stringify(bad)}`);
    assert.equal(contracts.validNumberRequestPage(without(page(), 'items')), false);
    assert.equal(contracts.validNumberRequestPage(page({ items: [item(), null] })), false, 'one bad row refuses the page');

    refuses(contracts.validNumberRequestItem, (over = {}, whole = false) => whole ? over : item(over), {
        id: 'text', tenant_id: 'text', requested_by_user_id: 'text', submitted_at: 'text',
        business_name: 'nullableText', reviewed_at: 'stamp', files_deleted: 'flag',
    }, 'item');
    for (const [name, values] of [['status', ['deleted', 'Submitted', '', null, 1]], ['use_type', ['company', 'Business', '', null]]]) {
        for (const value of values) assert.equal(contracts.validNumberRequestItem(item({ [name]: value })), false, `${name}=${JSON.stringify(value)}`);
        assert.equal(contracts.validNumberRequestItem(without(item(), name)), false, `missing ${name}`);
    }
    for (const status of contracts.NUMBER_REQUEST_STATUSES) assert.equal(contracts.validNumberRequestItem(item({ status })), true);
    for (const use_type of contracts.NUMBER_REQUEST_USES) assert.equal(contracts.validNumberRequestItem(item({ use_type })), true);
});

test('R2: a valid request and document pass; nullable fields are accepted; every missing or wrong field is refused', () => {
    assert.equal(contracts.validNumberRequestDetail(detail()), true);
    assert.equal(contracts.validNumberRequestDetail(detail({ business_name: null, requester_email: null, review_note: null, reviewed_by_user_id: null, reviewed_at: null })), true);
    assert.equal(contracts.validNumberRequestDetail(detail({ status: 'rejected', review_note: 'Too old', reviewed_by_user_id: USER, reviewed_at: STAMP, files_deleted: true,
        documents: [doc({ available: false, file_name: null })] })), true, 'a deleted document has no file name');
    assert.equal(contracts.validNumberRequestDetail(detail({ documents: [] })), true);

    for (const bad of [null, undefined, [], 'x', {}]) assert.equal(contracts.validNumberRequestDetail(bad), false);
    refuses(contracts.validNumberRequestDetail, (over = {}, whole = false) => whole ? over : detail(over), {
        id: 'text', tenant_id: 'text', requested_by_user_id: 'text', submitted_at: 'text',
        business_name: 'nullableText', requester_email: 'nullableText', review_note: 'nullableText', reviewed_by_user_id: 'nullableText',
        reviewed_at: 'stamp', files_deleted: 'flag',
    }, 'request');
    for (const [name, values] of [['status', ['deleted', '', null]], ['use_type', ['company', '', null]]]) {
        for (const value of values) assert.equal(contracts.validNumberRequestDetail(detail({ [name]: value })), false, `${name}=${JSON.stringify(value)}`);
        assert.equal(contracts.validNumberRequestDetail(without(detail(), name)), false, `missing ${name}`);
    }
    for (const bad of [null, 'x', {}, undefined]) assert.equal(contracts.validNumberRequestDetail(detail({ documents: bad })), false);
    assert.equal(contracts.validNumberRequestDetail(without(detail(), 'documents')), false);
    assert.equal(contracts.validNumberRequestDetail(detail({ documents: [doc(), {}] })), false, 'one bad document refuses the request');

    assert.equal(contracts.validNumberRequestDocument(doc()), true);
    refuses(contracts.validNumberRequestDocument, (over = {}, whole = false) => whole ? over : doc(over), {
        id: 'text', content_type: 'text', sha256: 'text', created_at: 'text', file_name: 'nullableText', available: 'flag',
    }, 'document');
    for (const wrong of [null, '20480', -1, 0, 1.5]) assert.equal(contracts.validNumberRequestDocument(doc({ size_bytes: wrong })), false, `size_bytes=${wrong}`);
    assert.equal(contracts.validNumberRequestDocument(without(doc(), 'size_bytes')), false);
    for (const [name, values] of [['kind', ['passport', '', null]], ['scan_status', ['pending', 'Clean', '', null]]]) {
        for (const value of values) assert.equal(contracts.validNumberRequestDocument(doc({ [name]: value })), false, `${name}=${JSON.stringify(value)}`);
        assert.equal(contracts.validNumberRequestDocument(without(doc(), name)), false, `missing ${name}`);
    }
    for (const kind of contracts.NUMBER_REQUEST_KINDS) assert.equal(contracts.validNumberRequestDocument(doc({ kind })), true);
    for (const scan_status of contracts.NUMBER_REQUEST_SCANS) assert.equal(contracts.validNumberRequestDocument(doc({ scan_status })), true);
});

test('R4: the decision answer passes for each email outcome; unknown values and missing fields are refused', () => {
    for (const email of ['sent', 'failed', 'skipped', 'off']) assert.equal(contracts.validNumberRequestDecision(answer({ email })), true, email);
    assert.equal(contracts.validNumberRequestDecision(answer({ status: 'rejected' })), true);
    for (const email of ['queued', 'Sent', '', null, true]) assert.equal(contracts.validNumberRequestDecision(answer({ email })), false, `email=${JSON.stringify(email)}`);
    for (const status of ['done', '', null]) assert.equal(contracts.validNumberRequestDecision(answer({ status })), false);
    for (const name of ['id', 'status', 'reviewed_at', 'email']) assert.equal(contracts.validNumberRequestDecision(without(answer(), name)), false, `missing ${name}`);
    for (const wrong of WRONG.text) {
        assert.equal(contracts.validNumberRequestDecision(answer({ id: wrong })), false);
        assert.equal(contracts.validNumberRequestDecision(answer({ reviewed_at: wrong })), false);
    }
    for (const bad of [null, undefined, [], 'x', {}]) assert.equal(contracts.validNumberRequestDecision(bad), false);
});

// The server's error table (backend/app/api/v1/endpoints/admin/telephony_number_requests.py and
// backend/app/core/error_handlers.py): exact envelope, status, and which helper answers yes.
const envelope = (code, message, details = null) => ({ error: { code, message, details, request_id: 'req-1' } });
const ERRORS = [
    [401, 'unauthorized', 'Current authenticated session is required.', null, null],
    [403, 'forbidden', 'Platform admin access required', null, null],
    [403, 'forbidden', 'Current platform admin authority is required.', null, null],
    [422, 'validation_error', 'Request payload failed validation', [{ type: 'uuid_parsing', loc: ['path', 'tenant_id'], msg: 'Input should be a valid UUID' }], null],
    [503, 'staff_unavailable', 'This could not be completed. Nothing was changed. Try again.', null, null],
    [404, 'number_request_not_found', 'That request was not found.', null, null],
    [404, 'document_not_found', 'That document was not found.', null, null],
    [404, 'document_file_missing', 'The stored file for that document is missing.', null, null],
    [410, 'document_deleted', 'That document has been deleted.', null, 'isDocumentDeleted'],
    [409, 'document_not_cleared', 'That document has not been cleared by a virus scan.', { scan_status: 'not_scanned' }, 'isDocumentNotCleared'],
    [500, 'document_unreadable', 'That document could not be read.', null, null],
    [422, 'note_required', 'Give a reason when you reject a request.', { field: 'note' }, 'isNoteRequired'],
    [409, 'already_decided', 'That request has already been decided.', { status: 'approved' }, 'isAlreadyDecided'],
    [503, 'decision_unconfirmed', 'The decision is not confirmed. Read the request again before trying again.', null, 'isDecisionUnconfirmed'],
    [503, 'service_unavailable', 'Current administrator authority could not be confirmed.', null, null],
];
const HELPERS = ['isAlreadyDecided', 'isNoteRequired', 'isDocumentDeleted', 'isDocumentNotCleared', 'isDecisionUnconfirmed'];

test('every server error keeps the server\'s own sentence, and each helper answers only for its own error', () => {
    for (const [status, code, message, details, helper] of ERRORS) {
        const error = { ...normalizeAdminApiError(envelope(code, message, details)), status };
        assert.equal(error.message, message, code);
        assert.equal(error.code, code.toUpperCase(), code);
        if (details) assert.deepEqual(error.details, details, code);
        for (const name of HELPERS) assert.equal(contracts[name](error), name === helper, `${name} for ${status} ${code}`);
    }
    // The second decision: 409 already_decided carries the current status.
    const second = { ...normalizeAdminApiError(envelope('already_decided', 'That request has already been decided.', { status: 'rejected' })), status: 409 };
    assert.equal(contracts.isAlreadyDecided(second), true);
    assert.deepEqual(second.details, { status: 'rejected' });
    // The same code under another status, another 409, and no error at all are not it.
    assert.equal(contracts.isAlreadyDecided({ ...second, status: 400 }), false);
    assert.equal(contracts.isAlreadyDecided({ code: 'DOCUMENT_NOT_CLEARED', status: 409 }), false);
    assert.equal(contracts.isAlreadyDecided(undefined), false);
    assert.equal(contracts.isNoteRequired({ code: 'VALIDATION_ERROR', status: 422 }), false);
});

test('filters: defaults, each status, unknown values, the offset range, and a status change restarts paging', () => {
    const parse = filters.parseNumberRequestFilters;
    assert.deepEqual(parse(''), { status: 'submitted', offset: 0 });
    for (const status of ['submitted', 'approved', 'rejected']) assert.deepEqual(parse(`status=${status}`), { status, offset: 0 });
    for (const unknown of ['deleted', 'Approved', '', 'all']) assert.deepEqual(parse(`status=${unknown}&offset=40`), { status: 'submitted', offset: 40 });
    assert.deepEqual(parse('status=approved&offset=1000'), { status: 'approved', offset: 1000 });
    for (const offset of ['-20', '1001', '99999', 'abc', '2e2', '20.5', ' 20', '']) assert.equal(parse(`offset=${encodeURIComponent(offset)}`).offset, 0, `offset=${offset}`);
    assert.deepEqual(parse(new URLSearchParams('status=rejected&offset=20')), { status: 'rejected', offset: 20 });

    assert.equal(filters.numberRequestQuery(parse('')), 'status=submitted&limit=20&offset=0');
    assert.equal(filters.numberRequestQuery(parse('status=rejected&offset=60')), 'status=rejected&limit=20&offset=60');
    assert.equal(filters.serializeNumberRequestFilters(parse('')), '');
    assert.equal(filters.serializeNumberRequestFilters({ status: 'approved', offset: 40 }), 'status=approved&offset=40');
    assert.deepEqual(parse(filters.serializeNumberRequestFilters({ status: 'rejected', offset: 20 })), { status: 'rejected', offset: 20 });

    assert.deepEqual(filters.withStatus({ status: 'submitted', offset: 60 }, 'approved'), { status: 'approved', offset: 0 });
    assert.equal(filters.nextOffset({ status: 'submitted', offset: 20 }), 40);
    assert.equal(filters.previousOffset({ status: 'submitted', offset: 20 }), 0);
    assert.equal(filters.previousOffset({ status: 'submitted', offset: 10 }), 0);
    assert.equal(filters.pageNumber({ status: 'submitted', offset: 0 }), 1);
    assert.equal(filters.pageNumber({ status: 'submitted', offset: 40 }), 3);
    assert.equal(filters.canGoNext({ status: 'submitted', offset: 0 }, true), true);
    assert.equal(filters.canGoNext({ status: 'submitted', offset: 0 }, false), false);
    assert.equal(filters.canGoNext({ status: 'submitted', offset: 980 }, true), true, 'offset 1000 is still allowed');
    assert.equal(filters.canGoNext({ status: 'submitted', offset: 1000 }, true), false, 'the next offset would pass 1000');
});

test('the four document labels are the backend\'s and the customer app\'s, word for word', () => {
    const root = new URL('../../../', import.meta.url);
    const backend = readFileSync(new URL('backend/app/domain/services/number_requests/config.py', root), 'utf8');
    const customer = readFileSync(new URL('Talk-Leee/src/lib/telephony-number-requests-api.ts', root), 'utf8');
    const block = backend.slice(backend.indexOf('KIND_LABELS'), backend.indexOf('}', backend.indexOf('KIND_LABELS')));
    const fromBackend = Object.fromEntries([...block.matchAll(/"([a-z_]+)":\s*"([^"]+)"/g)].map((match) => [match[1], match[2]]));
    const customerBlock = customer.slice(customer.indexOf('NUMBER_REQUEST_LABELS'), customer.indexOf('};', customer.indexOf('NUMBER_REQUEST_LABELS')));
    const fromCustomer = Object.fromEntries([...customerBlock.matchAll(/([a-z_]+):\s*"([^"]+)"/g)].map((match) => [match[1], match[2]]));
    assert.deepEqual(fromBackend, { ...contracts.NUMBER_REQUEST_KIND_LABELS });
    assert.deepEqual(fromCustomer, { ...contracts.NUMBER_REQUEST_KIND_LABELS });
    assert.deepEqual(Object.keys(contracts.NUMBER_REQUEST_KIND_LABELS), [...contracts.NUMBER_REQUEST_KINDS]);
});

test('the download name is the server\'s: kind, the first 8 characters of the id, and the type\'s extension', () => {
    const id = 'abcdef12-3456-4789-8abc-def012345678';
    for (const kind of contracts.NUMBER_REQUEST_KINDS) {
        for (const [content_type, extension] of [['application/pdf', '.pdf'], ['image/jpeg', '.jpg'], ['image/png', '.png']]) {
            assert.equal(contracts.documentDownloadName({ kind, id, content_type }), `${kind}-abcdef12${extension}`);
        }
        assert.equal(contracts.documentDownloadName({ kind, id, content_type: 'text/plain' }), `${kind}-abcdef12`, 'an unlisted type gets no extension, as on the server');
    }
});

test('display values: type, size, scan, use, status, company and the eight result lines', () => {
    assert.deepEqual(['application/pdf', 'image/jpeg', 'image/png', 'text/plain'].map(contracts.documentTypeLabel), ['PDF', 'JPG', 'PNG', '—']);
    assert.equal(contracts.documentSizeText(1), '1 KB');
    assert.equal(contracts.documentSizeText(20480), '20 KB');
    assert.equal(contracts.documentSizeText(1048575), '1024 KB');
    assert.equal(contracts.documentSizeText(1048576), '1.0 MB');
    assert.equal(contracts.documentSizeText(10 * 1048576 + 524288), '10.5 MB');
    assert.deepEqual({ ...contracts.NUMBER_REQUEST_SCAN_LABELS }, { not_scanned: 'Not scanned', clean: 'Clean', infected: 'Infected' });
    assert.deepEqual({ ...contracts.NUMBER_REQUEST_USE_LABELS }, { business: 'Business Use', personal: 'Personal Use' });
    assert.deepEqual({ ...contracts.NUMBER_REQUEST_STATUS_LABELS }, { submitted: 'Submitted', approved: 'Approved', rejected: 'Rejected' });
    assert.equal(contracts.companyText({ business_name: 'Acme', tenant_id: TENANT }), 'Acme');
    assert.equal(contracts.companyText({ business_name: null, tenant_id: TENANT }), TENANT);
    assert.equal(contracts.companyText({ business_name: '', tenant_id: TENANT }), TENANT);
    const lines = {
        sent: 'The customer was emailed.', failed: 'The email to the customer failed.',
        skipped: 'No email address is on file, so no email was sent.', off: 'Decision emails are turned off.',
    };
    for (const [email, line] of Object.entries(lines)) {
        assert.equal(contracts.decisionResultText('approved', email), `Approved. ${line}`);
        assert.equal(contracts.decisionResultText('rejected', email), `Rejected. ${line}`);
    }
});
