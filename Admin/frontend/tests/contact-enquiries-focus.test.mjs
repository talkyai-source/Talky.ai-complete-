// Focused final-page and failed-read keyboard recovery through the existing enquiry workflow.
// Existing real ReactDOM/StrictMode + synthetic HTTP harness; no native or DB claim.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, state, render, response, find, text, click, settle, deferred, button } from './bh-harness.mjs';

const stamp = '2026-10-25T00:00:00Z';
const item = (id, status = 'new') => ({ id, name: `Enquiry ${id}`, email: `${id}@example.invalid`,
    company: '', message: `Synthetic enquiry ${id}`, status, revision: status === 'handled' ? 1 : 0,
    created_at: stamp, handled_at: status === 'handled' ? stamp : null });
const firstPage = () => Array.from({ length: 50 }, (_, index) => item(`first-${index}`));
const page = (items, total, offset = 0) => ({ items, total, offset, limit: 50 });
const auth = () => response({ id: 'focus-operator', name: 'Operator', email: 'focus@example.invalid',
    role: 'platform_admin', tenant_id: null });
const reply = pending => pending.promise.then(value => value.clone());
const requestProps = node => node[Object.keys(node).find(key => key.startsWith('__reactProps$'))];
const writes = () => state.requests.filter(request => request.method === 'PATCH');
const reads = () => state.requests.filter(request => request.method === 'GET' && request.path.includes('/admin/contact-enquiries?'));
const heading = () => find('h1');
const otherControl = () => {
    const node = find('a[href="/calls"]');
    assert.ok(node?.isConnected, 'Use the existing Calls navigation link as deliberately chosen focus');
    return node;
};

async function arrange({ holdWrite = false, holdCorrectedRead = false } = {}) {
    const writeReceipt = deferred(), correctedRead = deferred(), retryRead = deferred();
    let saved = false, correctedReads = 0;
    state.route = request => {
        if (request.path.endsWith('/auth/me')) return auth();
        if (request.method === 'PATCH') {
            assert.match(request.path, /\/admin\/contact-enquiries\/last$/);
            assert.deepEqual(request.body, { status: 'handled', expected_revision: 0 });
            saved = true;
            return holdWrite ? reply(writeReceipt) : response(item('last', 'handled'));
        }
        if (request.path.includes('/admin/contact-enquiries?')) {
            const url = new URL(request.path, 'https://fixture.invalid');
            const offset = Number(url.searchParams.get('offset'));
            if (url.searchParams.get('status') === 'handled') return response(page(saved ? [item('last', 'handled')] : [], saved ? 1 : 0));
            if (offset === 50) return response(page(saved ? [] : [item('last')], saved ? 50 : 51, 50));
            if (!saved) return response(page(firstPage(), 51));
            correctedReads += 1;
            if (correctedReads === 1) return holdCorrectedRead ? reply(correctedRead)
                : response({ detail: 'Corrected enquiry inventory unavailable' }, 503);
            return reply(retryRead);
        }
        return response({ detail: 'Unapproved synthetic request' }, 500);
    };
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.ContactEnquiriesPage))));
    await settle();
    await click('Next'); await settle();
    assert.match(text(), /Enquiry last/);
    const action = button('Mark handled');
    const perform = async () => { await act(async () => { action.focus(); action.click(); }); await settle(); };
    return { action, perform, writeReceipt, correctedRead, retryRead, correctedReads: () => correctedReads };
}

test('handled final-page enquiry retains keyboard recovery through failed corrected read and GET-only retry', async () => {
    const run = await arrange({ holdCorrectedRead: true });
    await run.perform();
    assert.equal(run.action.isConnected, false);
    assert.equal(run.correctedReads(), 1, 'The empty final page already advanced to the corrected page');
    const focusBeforeRead = document.activeElement;
    await act(async () => run.correctedRead.resolve(response({ detail: 'Corrected enquiry inventory unavailable' }, 503)));
    await settle();
    const failedReadFocus = document.activeElement;
    assert.match(text(), /Enquiry marked as handled/);
    assert.match(text(), /Corrected enquiry inventory unavailable/);
    assert.equal(button('Refresh').disabled, false);
    assert.equal(writes().length, 1);

    // A new deliberate keyboard retry reads the list only; its button becomes disabled while loading.
    const retry = button('Refresh');
    const readsBeforeRetry = reads().length;
    await act(async () => { retry.focus(); retry.click(); });
    const retryPendingFocus = document.activeElement;
    await act(async () => run.retryRead.resolve(response(page(firstPage(), 50)))); await settle();
    assert.match(text(), /Enquiry first-0/);
    assert.doesNotMatch(text(), /Enquiry last|Corrected enquiry inventory unavailable/);
    assert.match(text(), /Enquiry marked as handled/);
    assert.equal(writes().length, 1, 'Recovery must never resend the status mutation');
    assert.equal(reads().length, readsBeforeRetry + 1, 'Manual recovery makes one additional list read');
    assert.equal(new URL(reads().at(-1).path, 'https://fixture.invalid').searchParams.get('offset'), '0');
    console.log('enquiry_focus_observation', JSON.stringify({
        before_corrected_read: focusBeforeRead?.tagName,
        failed_corrected_read: failedReadFocus?.tagName,
        retry_pending: retryPendingFocus?.tagName,
        after_recovery: document.activeElement?.tagName,
    }));
    assert.equal(focusBeforeRead === heading(), true, 'Move focus before removing the invoking card, without waiting for the read');
    assert.equal(failedReadFocus === heading(), true, 'Failed correction retains a stable connected recovery location');
    assert.equal(retryPendingFocus === heading() && retryPendingFocus.isConnected, true,
        'Move focus before disabling Refresh; native browsers can drop disabled-button focus on completion');
    assert.equal(document.activeElement === heading() && heading().isConnected, true);
    assert.ok(heading().isConnected && heading().tabIndex === -1);
});

test('an unfocused Refresh activation preserves the control the operator chose', async () => {
    const run = await arrange();
    await run.perform();
    const chosen = otherControl(); await act(async () => chosen.focus());
    const before = reads().length;
    await act(async () => button('Refresh').click());
    assert.equal(document.activeElement === chosen, true);
    await act(async () => run.retryRead.resolve(response(page(firstPage(), 50)))); await settle();
    assert.equal(document.activeElement === chosen, true);
    assert.equal(reads().length, before + 1);
    assert.equal(writes().length, 1);
});

test('a delayed Refresh completion does not take focus from another control chosen during the read', async () => {
    const run = await arrange();
    await run.perform();
    const retry = button('Refresh');
    const before = reads().length;
    await act(async () => { retry.focus(); retry.click(); });
    assert.equal(document.activeElement === heading(), true);
    const chosen = otherControl(); await act(async () => chosen.focus());
    await act(async () => run.retryRead.resolve(response(page(firstPage(), 50)))); await settle();
    assert.equal(document.activeElement === chosen, true);
    assert.equal(reads().length, before + 1);
    assert.equal(writes().length, 1);
});

test('pending and retained Refresh work cannot move focus or dispatch after unmount', async () => {
    const run = await arrange();
    await run.perform();
    const retry = button('Refresh'), retainedRefresh = requestProps(retry).onClick;
    await act(async () => { retry.focus(); retry.click(); });
    assert.equal(document.activeElement === heading(), true);
    await render(el('button', { id: 'replacement-page-control' }, 'Replacement page control'));
    const chosen = find('#replacement-page-control'); await act(async () => chosen.focus());
    const before = state.requests.length;
    await act(async () => {
        retainedRefresh({ currentTarget: retry });
        run.retryRead.resolve(response(page(firstPage(), 50)));
    });
    await settle();
    assert.equal(document.activeElement === chosen, true);
    assert.equal(state.requests.length, before);
    assert.equal(writes().length, 1);
});

test('a saved receipt does not take focus from navigation chosen while the write was pending', async () => {
    const run = await arrange({ holdWrite: true });
    await run.perform();
    const chosen = otherControl(); await act(async () => chosen.focus());
    await act(async () => run.writeReceipt.resolve(response(item('last', 'handled')))); await settle();
    assert.match(text(), /Enquiry marked as handled/);
    assert.equal(document.activeElement === chosen, true);
    assert.equal(writes().length, 1);
});

test('a delayed corrected read does not take focus after the operator chooses another control', async () => {
    const run = await arrange({ holdCorrectedRead: true });
    await run.perform();
    const chosen = otherControl(); await act(async () => chosen.focus());
    await act(async () => run.correctedRead.resolve(response(page(firstPage(), 50)))); await settle();
    assert.match(text(), /Enquiry first-0/);
    assert.equal(document.activeElement === chosen, true);
    assert.equal(writes().length, 1);
});

for (const retiredBy of ['filter', 'identity']) {
    test(`retained enquiry action and Refresh handlers cannot move focus or dispatch after ${retiredBy} retirement`, async () => {
        const run = await arrange();
        const oldAction = requestProps(run.action).onClick;
        const oldRefreshNode = button('Refresh'), oldRefresh = requestProps(oldRefreshNode).onClick;
        if (retiredBy === 'filter') {
            await act(async () => {
                const status = find('#enquiry-status'); status.value = 'handled';
                status.dispatchEvent(new window.Event('change', { bubbles: true }));
            });
        } else {
            await act(async () => api.setToken('replacement-enquiry-focus-owner'));
        }
        await settle();
        // Filter changes reuse the connected Refresh node. A retained callback
        // still cannot move it even when that same node currently owns focus.
        const chosen = oldRefreshNode.isConnected ? oldRefreshNode : otherControl();
        await act(async () => chosen.focus());
        const before = state.requests.length;
        await act(async () => {
            oldAction({ currentTarget: run.action });
            oldRefresh({ currentTarget: oldRefreshNode });
        });
        assert.equal(state.requests.length, before);
        assert.equal(document.activeElement === chosen, true);
        assert.equal(writes().length, 0);
    });
}

test('a pending receipt cannot move focus or refresh after the Enquiries page unmounts', async () => {
    const run = await arrange({ holdWrite: true });
    await run.perform();
    await render(el('button', { id: 'replacement-page-control' }, 'Replacement page control'));
    const chosen = find('#replacement-page-control'); await act(async () => chosen.focus());
    const before = state.requests.length;
    await act(async () => run.writeReceipt.resolve(response(item('last', 'handled')))); await settle();
    assert.equal(state.requests.length, before);
    assert.equal(document.activeElement === chosen, true);
    assert.equal(writes().length, 1);
    assert.doesNotMatch(text(), /Enquiry marked as handled/);
});
