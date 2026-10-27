import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, act, el, state, render, response, text, click, settle, button, deferred, find } from './bh-harness.mjs';

const stamp = '2026-10-21T00:00:00Z';
const enquiry = (status = 'new') => ({ id: 'owned-enquiry', name: 'Owned enquiry',
    email: 'owned@example.invalid', company: '', message: 'Synthetic contact request',
    status, revision: status === 'handled' ? 1 : 0, created_at: stamp, handled_at: status === 'handled' ? stamp : null });
const page = items => ({ items, total: items.length, limit: 50, offset: 0 });
const exact = item => ({ ...page([item]), limit: 1 });
const auth = () => response({ id: 'operator', name: 'Operator', email: 'operator@example.invalid',
    role: 'platform_admin', tenant_id: null });
const draw = async () => {
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.ContactEnquiriesPage))));
    await settle();
};

test('an unconfirmed status write requires a fresh read before another mutation', async () => {
    let current = enquiry();
    state.route = request => {
        if (request.path.endsWith('/auth/me')) return auth();
        if (request.method === 'PATCH') {
            // The application cannot infer rollback from a missing receipt.
            current = enquiry('handled');
            return response({ detail: 'Receipt delivery failed after the owned write' }, 503);
        }
        if (request.path.includes('enquiry_id=')) return response(exact(current));
        if (request.path.includes('/admin/contact-enquiries?')) return response(page(current.status === 'new' ? [current] : []));
        return response({ detail: 'Unapproved synthetic request' }, 500);
    };
    await draw();
    await click('Mark handled');
    assert.match(text(), /Receipt delivery failed|could not be confirmed/);
    await click('Mark handled');
    assert.equal(state.requests.filter(request => request.method === 'PATCH').length, 1,
        'an unknown saved outcome must not be blindly sent again from the stale row');
    assert.equal(button('Refresh').disabled, false);
    await click('Refresh'); await settle();
    assert.match(text(), /No enquiries match/);
    assert.match(text(), /Current saved status is Handled/);
    assert.doesNotMatch(text(), /Enquiry marked as handled/);
});

for (const [name, saved] of [
    ['empty filtered inventory', { ...page([]), limit: 1 }],
    ['another reference', exact({ ...enquiry('handled'), id: 'another-enquiry' })],
    ['missing revision', exact({ ...enquiry('handled'), revision: undefined })],
    ['wrong page contract', page([enquiry('handled')])],
]) {
    test(`an unconfirmed write stays fenced after reconciliation returns ${name}`, async () => {
        state.route = request => request.path.endsWith('/auth/me') ? auth()
            : request.method === 'PATCH' ? response({ detail: 'Unknown saved outcome' }, 503)
            : request.path.includes('enquiry_id=') ? response(saved)
            : request.path.includes('/admin/contact-enquiries?') ? response(page([enquiry()]))
            : response({}, 500);
        await draw(); await click('Mark handled'); await click('Refresh'); await settle();
        assert.equal(button('Mark handled').disabled, true);
        assert.match(text(), /Saved status is unavailable/);
        assert.equal(state.requests.filter(request => request.method === 'PATCH').length, 1);
    });
}

test('a fresh exact read permits only a new deliberate version-bound decision', async () => {
    let writes = 0;
    state.route = request => {
        if (request.path.endsWith('/auth/me')) return auth();
        if (request.method === 'PATCH') {
            writes++;
            assert.deepEqual(request.body, { status: 'handled', expected_revision: 0 });
            return response({ detail: writes === 1 ? 'Unknown outcome' : 'Current version changed' }, writes === 1 ? 503 : 409);
        }
        if (request.path.includes('enquiry_id=')) return response(exact(enquiry()));
        if (request.path.includes('/admin/contact-enquiries?')) return response(page([enquiry()]));
        return response({}, 500);
    };
    await draw(); await click('Mark handled'); await click('Refresh'); await settle();
    assert.equal(writes, 1); assert.equal(button('Mark handled').disabled, false);
    assert.match(text(), /This read does not confirm the earlier request/);
    await click('Mark handled');
    assert.equal(writes, 2); assert.equal(button('Mark handled').disabled, true);
    assert.match(text(), /Current version changed/);
});

test('a confirmed handling receipt survives failure of its following list read', async () => {
    let saved = false;
    state.route = request => {
        if (request.path.endsWith('/auth/me')) return auth();
        if (request.method === 'PATCH') { saved = true; return response(enquiry('handled')); }
        if (request.path.includes('/admin/contact-enquiries?')) return saved
            ? response({ detail: 'Inventory temporarily unavailable' }, 503) : response(page([enquiry()]));
        return response({}, 500);
    };
    await draw(); await click('Mark handled'); await settle();
    assert.match(text(), /Enquiry marked as handled/);
    assert.match(text(), /Inventory temporarily unavailable/);
    assert.equal(state.requests.filter(request => request.method === 'PATCH').length, 1);
});

test('an old row callback cannot mutate after a fresh list replaces its review', async () => {
    state.route = request => request.path.endsWith('/auth/me') ? auth()
        : request.path.includes('/admin/contact-enquiries?') ? response(page([enquiry()])) : response({}, 500);
    await draw();
    const control = button('Mark handled');
    const props = control[Object.keys(control).find(key => key.startsWith('__reactProps$'))];
    await click('Refresh'); await settle();
    await act(async () => props.onClick());
    assert.equal(state.requests.filter(request => request.method === 'PATCH').length, 0);
});

test('late exact-row recovery cannot install the previous session outcome', async () => {
    const pending = deferred();
    state.route = request => request.path.endsWith('/auth/me') ? auth()
        : request.method === 'PATCH' ? response({ detail: 'Unknown outcome' }, 503)
        : request.path.includes('enquiry_id=') ? pending.promise
        : request.path.includes('/admin/contact-enquiries?') ? response(page([enquiry()])) : response({}, 500);
    await draw(); await click('Mark handled'); await click('Refresh');
    state.route = request => request.path.endsWith('/auth/me') ? auth()
        : request.path.includes('/admin/contact-enquiries?') ? response(page([])) : response({}, 500);
    await act(async () => api.setToken('replacement-enquiry-owner')); await settle();
    await act(async () => pending.resolve(response(exact(enquiry('handled'))))); await settle();
    assert.match(text(), /No enquiries match/);
    assert.doesNotMatch(text(), /Current saved status|Unknown outcome|owned-enquiry/);
    assert.equal(find('.enquiry-card'), null);
});
