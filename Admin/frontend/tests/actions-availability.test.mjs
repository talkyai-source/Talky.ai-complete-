import assert from 'node:assert/strict';
import test from 'node:test';
import { C, click, el, find, render, response, settle, state, text } from './bh-harness.mjs';

// Real client normalization + ReactDOM, using the actual endpoint's wire shapes.
test('Actions reports HTTP503 as unavailable and Retry can recover to a confirmed empty result', async () => {
    let unavailable = true;
    state.route = request => request.path.startsWith('/api/v1/admin/actions')
        ? unavailable
            ? response({ detail: 'Action records are unavailable. Please retry.' }, 503)
            : response({ items: [], total: 0, page: 1, page_size: 20 })
        : response({ detail: 'Unexpected request' }, 503);
    await render(el(C.ActionsTable, { onActionSelect() {} }));
    await settle();
    assert.match(find('[role=alert]').textContent, /Action records are unavailable/);
    assert.doesNotMatch(text(), /No Actions Found/);
    unavailable = false;
    await click('Retry');
    await settle();
    assert.equal(find('[role=alert]'), null);
    assert.match(text(), /No Actions Found/);
    assert.ok(state.requests.every(request => request.method === 'GET'));
});

test('Actions outage hides a previously loaded row until recovery establishes current results', async () => {
    let unavailable = false;
    const item = { id: 'synthetic-action', tenant_id: 'synthetic-tenant', tenant_name: 'Known synthetic tenant',
        type: 'send_email', status: 'failed', created_at: '2026-10-21T00:00:00Z' };
    state.route = () => unavailable
        ? response({ detail: 'Action records are unavailable. Please retry.' }, 503)
        : response({ items: [item], total: 1, page: 1, page_size: 20 });
    await render(el(C.ActionsTable, { onActionSelect() {} }));
    await settle();
    assert.match(text(), /Known synthetic tenant/);
    unavailable = true;
    // Changing an existing filter creates a new read, without mutation.
    const select = state.host.querySelector('select');
    select.value = 'failed';
    const { act, dom } = await import('./bh-harness.mjs');
    await act(async () => select.dispatchEvent(new dom.window.Event('change', { bubbles: true })));
    await settle();
    assert.match(find('[role=alert]').textContent, /Action records are unavailable/);
    assert.doesNotMatch(text(), /Known synthetic tenant|No Actions Found/);
    unavailable = false;
    await click('Retry');
    await settle();
    assert.match(text(), /Known synthetic tenant/);
    assert.equal(find('[role=alert]'), null);
});
