import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, state, render, response, find, text, click, settle, deferred, button } from './bh-harness.mjs';

const stamp = '2026-10-21T00:00:00Z';
const item = (id = 'one', status = 'new') => ({ id, name: `Enquiry ${id}`, email: `${id}@example.invalid`,
    company: '', message: `Private message ${id}`, status, revision: status === 'handled' ? 1 : 0,
    created_at: stamp, handled_at: status === 'handled' ? stamp : null });
const list = (items = [item()], extra = {}) => ({ items, total: items.length, limit: 50, offset: 0, ...extra });
const normal = r => r.path.endsWith('/auth/me')
    ? response({ id: 'admin', name: 'Test admin', email: 'admin@example.invalid', role: 'platform_admin', tenant_id: null })
    : r.path.includes('/admin/contact-enquiries?') ? response(list(undefined,
        r.path.includes('enquiry_id=') ? { limit: 1 } : {})) : response({ detail: 'Unrouted synthetic request' }, 500);
const draw = async () => { await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.ContactEnquiriesPage)))); await settle(); };
const reply = pending => pending.promise.then(value => value.clone());
test.beforeEach(() => { state.route = normal; });

test('late enquiry list from the previous session cannot disclose contact details', async () => {
    const pending = deferred();
    state.route = r => r.path.includes('/admin/contact-enquiries?') ? reply(pending) : normal(r);
    await draw();
    state.route = r => r.path.includes('/admin/contact-enquiries?') ? response(list([item('new-account')])) : normal(r);
    await act(async () => api.setToken('new-enquiry-session')); await settle();
    await act(async () => pending.resolve(response(list([item('old-account')]))));
    assert.match(text(), /Enquiry new-account/); assert.doesNotMatch(text(), /old-account/);
});

test('loaded enquiry data is retired immediately on account change', async () => {
    await draw();
    const pending = deferred();
    state.route = r => r.path.includes('/admin/contact-enquiries?') ? reply(pending) : normal(r);
    await act(async () => api.setToken('replacement-enquiry-session'));
    assert.doesNotMatch(text(), /Private message one|one@example/);
    await act(async () => pending.resolve(response(list([]))));
});

test('late old-session status receipt cannot publish success or refresh new-session records', async () => {
    const pending = deferred();
    state.route = r => r.method === 'PATCH' ? reply(pending) : normal(r);
    await draw(); await click('Mark handled');
    state.route = r => r.path.includes('/admin/contact-enquiries?') ? response(list([item('new-account')])) : normal(r);
    await act(async () => api.setToken('new-enquiry-session')); await settle();
    const reads = state.requests.filter(r => r.path.includes('/admin/contact-enquiries?')).length;
    await act(async () => pending.resolve(response(item('one', 'handled'))));
    assert.doesNotMatch(text(), /Enquiry marked as handled/);
    assert.match(text(), /Enquiry new-account/);
    assert.equal(state.requests.filter(r => r.path.includes('/admin/contact-enquiries?')).length, reads);
});

test('rejected update remains visible after a successful manual list refresh', async () => {
    state.route = r => r.method === 'PATCH' ? response({ detail: 'Update rejected by current policy' }, 409) : normal(r);
    await draw(); await click('Mark handled');
    assert.match(find('[role="alert"]').textContent, /Update rejected/);
    await click('Refresh'); await settle();
    assert.match(find('[role="alert"]').textContent, /Update rejected/);
    assert.match(text(), /Enquiry one/);
    assert.doesNotMatch(text(), /Enquiry marked as handled/);
});

for (const [name, body] of [['null', null], ['missing rows', {}],
    ['non-array rows', { ...list(), items: {} }], ['missing total', { items: [], limit: 50, offset: 0 }],
    ['object text', list([{ ...item(), message: {} }])], ['unsupported status', list([{ ...item(), status: 'done' }])]]) {
    test(`malformed enquiry list (${name}) is unavailable rather than empty`, async () => {
        state.route = r => r.path.includes('/admin/contact-enquiries?') ? response(body) : normal(r);
        await draw();
        assert.match(find('[role="alert"]').textContent, /load enquiries|unexpected response/);
        assert.doesNotMatch(text(), /No enquiries match/);
        assert.equal(find('.enquiry-card'), null);
    });
}

test('true empty enquiry response remains valid', async () => {
    state.route = r => r.path.includes('/admin/contact-enquiries?') ? response(list([])) : normal(r);
    await draw(); assert.match(text(), /No enquiries match/); assert.equal(find('[role="alert"]'), null);
});

test('status update is single flight and locks filters while awaiting an exact receipt', async () => {
    const pending = deferred(); state.route = r => r.method === 'PATCH' ? reply(pending) : normal(r);
    await draw();
    await act(async () => { const action = button('Mark handled'); action.click(); action.click(); });
    assert.equal(state.requests.filter(r => r.method === 'PATCH').length, 1);
    assert.equal(find('#enquiry-status').disabled, true); assert.equal(button('Refresh').disabled, true);
    await act(async () => pending.resolve(response(item('other', 'handled'))));
    assert.match(find('[role="alert"]').textContent, /could not be confirmed/);
    assert.doesNotMatch(text(), /Enquiry marked as handled/);
    assert.equal(find('#enquiry-status').disabled, false);
});

test('removing the last result from the final page returns to the preceding page', async () => {
    let saved = false;
    state.route = r => {
        if (r.method === 'PATCH') { saved = true; return response(item('last', 'handled')); }
        if (r.path.includes('/admin/contact-enquiries?')) {
            const offset = Number(new URL(r.path, 'https://fixture.invalid').searchParams.get('offset'));
            return response(offset === 50 ? list(saved ? [] : [item('last')], { total: saved ? 50 : 51, offset })
                : list(Array.from({ length: 50 }, (_, i) => item(`first-${i}`)), { total: saved ? 50 : 51 }));
        }
        return normal(r);
    };
    await draw(); await click('Next'); await settle(); assert.match(text(), /Enquiry last/);
    await click('Mark handled'); await settle();
    assert.match(text(), /Enquiry marked as handled/); assert.match(text(), /Enquiry first-0/);
    assert.doesNotMatch(text(), /Enquiry last/); assert.equal(button('Next').disabled, true);
    assert.equal(button('Previous').disabled, true);
});
