import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, dom, el, act, state, render, response, find, text, deferred, setInput, settle } from './bh-harness.mjs';

const id = '00000000-0000-4000-8000-000000000001';
const capture = { id, request_id: id, supplier: 'deepgram', source_kind: 'deepgram_billing_breakdown_v1',
    account_id: id, reference: 'Owned supplier export', period_start: '2026-09-01', period_end: '2026-09-30',
    currency: 'USD', reported_charges: '12.34000000', source_sha256: 'a'.repeat(64), imported_by: id,
    imported_at: '2026-10-01T00:00:00Z', stage: 'imported', superseded_by: null, reviews: [] };
const page = items => ({ items, has_more: false, net_spend: null, coverage: 'listed_account_periods_only' });
const summary = { period_start: '2026-10-01', period_end: '2026-10-27', monetary_note: 'No supplier allocation',
    total_call_minutes: 1, total_action_records: 2, legacy_outbound_estimate: null };
const normal = request => request.path.endsWith('/auth/me')
    ? response({ id, email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null })
    : request.path.includes('/usage/statements') ? response(page([capture]))
        : request.path.includes('/usage/summary') ? response(summary) : response({ monetary_note: 'No allocation', breakdown: [] });
const button = label => [...document.querySelectorAll('button')].find(item => item.textContent.trim() === label);
const click = async label => { await act(async () => button(label).click()); await settle(); };
const draw = async () => { await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.UsageCostPage)))); await settle(); };

test('retained reported charges stay distinct from net spending and missing suppliers', async () => {
    state.route = normal; await draw();
    assert.match(text(), /12\.34000000 USD/);
    assert.match(text(), /Imported; source not reviewed/);
    assert.match(text(), /Groq and Cartesia statement coverage is missing/);
    assert.match(text(), /not paid invoices or net spend/);
    await click('Owned supplier export');
    assert.match(text(), /Unallocated reported charges: 12\.34000000 USD/);
    assert.match(text(), /Net spend and credits remain unknown/);
});

test('malformed and failed supplier reads cannot become empty or zero evidence', async () => {
    state.route = request => request.path.includes('/usage/statements') ? response({ items: [] }) : normal(request);
    await draw();
    assert.match(text(), /Supplier evidence could not be read/);
    assert.doesNotMatch(text(), /No supplier evidence retained/);
});

test('actual zero charge has fixed-point display while net spending remains unknown', async () => {
    state.route = request => request.path.includes('/usage/statements')
        ? response(page([{ ...capture, reported_charges: '0.00000000' }])) : normal(request);
    await draw(); await click('Owned supplier export');
    assert.match(text(), /0\.00000000 USD/);
    assert.match(text(), /Net spend and credits remain unknown/);
    assert.doesNotMatch(text(), /Supplier evidence could not be read/);
});

test('delayed old-account private supplier rows are retired', async () => {
    const old = deferred();
    state.route = request => request.path.includes('/usage/statements') ? old.promise : normal(request);
    await draw();
    state.route = request => request.path.includes('/usage/statements') ? response(page([])) : normal(request);
    await act(async () => api.setToken('replacement-account'));
    await act(async () => old.resolve(response(page([{ ...capture, reference: 'Retired private supplier' }]))));
    assert.doesNotMatch(text(), /Retired private supplier/);
});

test('lost review response requires a GET; saved original review is retained without a second POST', async () => {
    const saved = { ...capture, stage: 'reviewed', reviews: [{ id, request_id: id, stage: 'reviewed',
        review_reference: 'Finance source check', notes: 'Matched to original owned account export.', reviewer_id: id,
        recorded_at: '2026-10-02T00:00:00Z' }] };
    let posts = 0, reads = 0;
    state.route = request => {
        if (request.path.endsWith('/reviews')) { posts += 1; saved.reviews[0].request_id = request.body.request_id; return response({}, 503); }
        if (request.path.endsWith(`/statements/${id}`)) { reads += 1; return response(saved); }
        return normal(request);
    };
    await draw(); await click('Owned supplier export');
    const section = find('section[aria-label="Selected supplier evidence"]');
    await setInput(section.querySelector('input'), 'Finance source check');
    await act(async () => {
        const textarea = section.querySelector('textarea');
        Object.getOwnPropertyDescriptor(Object.getPrototypeOf(textarea), 'value').set.call(textarea, 'Matched to original owned account export.');
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => section.querySelector('input[type="checkbox"]').click());
    await click('Record source review');
    assert.equal(posts, 1);
    assert.equal(button('Record source review').disabled, true);
    assert.equal(button('Refresh').disabled, true);
    assert.equal(find('input[type="date"]').disabled, true);
    assert.equal(button('Owned supplier export').disabled, true);
    await click('Refresh');
    await setInput(find('input[type="date"]'), '2026-08-01');
    assert.equal(button('Record source review').disabled, true);
    await click('Read saved evidence');
    assert.equal(reads, 1); assert.equal(posts, 1);
    assert.match(text(), /Source review recorded/);
    assert.match(text(), /Net spend and credits remain unknown/);
    assert.equal(button('Record reconciliation with difference').disabled, true);
});

test('superseded source cannot receive another review or silently disappear', async () => {
    state.route = request => request.path.includes('/usage/statements')
        ? response(page([{ ...capture, superseded_by: '00000000-0000-4000-8000-000000000002' }])) : normal(request);
    await draw(); await click('Owned supplier export');
    assert.match(text(), /Superseded; original retained/);
    assert.equal(button('Record source review'), undefined);
    assert.equal(button('Prepare correction').disabled, true);
});

async function prepareImport(readFile) {
    const section = find('section[aria-label="Supplier source evidence"]');
    const inputs = section.querySelectorAll('details input');
    await setInput(inputs[0], id); await setInput(inputs[1], 'Owned supplier import');
    const file = new dom.window.File(['{"synthetic":true}'], 'owned.json', { type: 'application/json' });
    Object.defineProperty(file, 'arrayBuffer', { value: async () => {
        const value = await readFile();
        return typeof value === 'string' ? new TextEncoder().encode(value).buffer : value;
    } });
    Object.defineProperty(inputs[2], 'files', { configurable: true, value: [file] });
    if (!inputs[3].checked) await act(async () => inputs[3].click());
}

test('lost import receipt is recovered by exact GET with no repeated POST', async () => {
    let posted, posts = 0, reads = 0, reviews = 0;
    state.route = request => {
        if (request.path.endsWith('/reviews')) { reviews += 1; return response({}, 503); }
        if (request.method === 'POST' && request.path.endsWith('/statements')) {
            posts += 1; posted = request.body; return response({}, 503);
        }
        if (request.path.includes('/statements/imports/')) {
            reads += 1;
            assert.ok(request.path.endsWith(posted.request_id));
            return response({ ...capture, request_id: posted.request_id });
        }
        return normal(request);
    };
    await draw(); await click('Owned supplier export');
    const section = find('section[aria-label="Selected supplier evidence"]');
    await setInput(section.querySelector('input'), 'Prepared source review');
    await act(async () => {
        const textarea = section.querySelector('textarea');
        Object.getOwnPropertyDescriptor(Object.getPrototypeOf(textarea), 'value').set.call(textarea, 'Owned original source compared.');
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        section.querySelector('input[type="checkbox"]').click();
    });
    assert.equal(button('Record source review').disabled, false);
    await prepareImport(async () => '\ufeff{"synthetic":true}');
    await click('Save source evidence');
    assert.equal(posts, 1); assert.equal(button('Retry exact retained import').disabled, true);
    assert.equal(posted.source_content, '\ufeff{"synthetic":true}');
    assert.equal(button('Refresh').disabled, true);
    assert.equal(find('input[type="date"]').disabled, true);
    assert.equal(button('Record source review').disabled, true);
    await click('Record source review');
    assert.equal(reviews, 0);
    await click('Refresh');
    await setInput(find('input[type="date"]'), '2026-08-01');
    assert.equal(button('Retry exact retained import').disabled, true);
    await click('Check saved import');
    assert.equal(reads, 1); assert.equal(posts, 1);
    assert.match(text(), /original saved import was found/);
    assert.match(text(), /Imported; source not reviewed/);
});

test('recovered correction completes its form before a fresh independent import', async () => {
    const posts = [];
    let reads = 0;
    const corrected = { ...capture, id: '00000000-0000-4000-8000-000000000002', reference: 'Corrected source' };
    state.route = request => {
        if (request.method === 'POST' && request.path.endsWith('/statements')) {
            posts.push(request.body);
            return posts.length === 1 ? response({}, 503) : response({ ...capture, request_id: request.body.request_id });
        }
        if (request.path.includes('/statements/imports/')) {
            reads += 1;
            assert.ok(request.path.endsWith(posts[0].request_id));
            return response({ ...corrected, request_id: posts[0].request_id });
        }
        return normal(request);
    };
    await draw(); await click('Owned supplier export'); await click('Prepare correction');
    await prepareImport(async () => '{"synthetic":true}');
    await click('Save source evidence');
    assert.equal(posts[0].supersedes_id, id);
    assert.match(text(), /Correction of retained evidence/);
    await click('Check saved import');
    assert.equal(posts.length, 1); assert.equal(reads, 1);
    assert.doesNotMatch(text(), /Correction of retained evidence/);
    assert.equal(find('input[type="file"]').value, '');
    await prepareImport(async () => '{"synthetic":"fresh"}');
    await click('Save source evidence');
    assert.equal(posts.length, 2);
    assert.equal(posts[1].supersedes_id, null);
    assert.notEqual(posts[1].request_id, posts[0].request_id);
});

test('retired account cannot dispatch an upload after its file read finishes', async () => {
    const file = deferred(); let posts = 0;
    state.route = request => { if (request.method === 'POST') posts += 1; return normal(request); };
    await draw(); await prepareImport(() => file.promise);
    await act(async () => button('Save source evidence').click());
    await act(async () => api.setToken('new-supplier-reader'));
    await act(async () => file.resolve('{"synthetic":true}'));
    assert.equal(posts, 0);
});

test('invalid UTF-8 source is refused before preparing or sending an import', async () => {
    let posts = 0;
    state.route = request => { if (request.method === 'POST') posts += 1; return normal(request); };
    await draw(); await prepareImport(async () => new Uint8Array([0xff, 0x7b, 0x7d]).buffer);
    await click('Save source evidence');
    assert.equal(posts, 0);
    assert.equal(button('Check saved import'), undefined);
});
