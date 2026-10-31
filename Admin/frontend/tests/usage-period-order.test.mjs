import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, state, render, response, find, text, deferred, setInput, settle } from './bh-harness.mjs';

const summary = { period_start: '2026-10-01', period_end: '2026-10-21', monetary_note: 'Ledger boundary',
    total_call_minutes: 1, total_action_records: 2, legacy_outbound_estimate: null };
const breakdown = name => ({ monetary_note: 'Ledger boundary', breakdown: [{
    tenant_id: name, tenant_name: name, call_count: 1, total_minutes: 1, total_seconds: 60,
    total_cost: null, legacy_outbound_estimate: null,
}] });
const normal = request => request.path.endsWith('/auth/me')
    ? response({ id: 'admin', email: 'admin@example.invalid', name: 'Test admin', role: 'platform_admin', tenant_id: null })
    : request.path.includes('/usage/summary') ? response(summary) : response(breakdown('Initial tenant'));
const draw = async () => {
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, el(C.UsageCostPage))));
    await settle();
};

for (const [latestFails, oldFails] of [[false, false], [true, false], [false, true]]) {
    test(`late old-period ${oldFails ? 'failure' : 'success'} cannot replace current ${latestFails ? 'failure' : 'rows'}`, async () => {
        const old = deferred();
        state.route = request => request.path.includes('/usage/breakdown')
            ? request.path.includes('from_date=2026-09-01')
                ? latestFails ? response({ detail: 'Current unavailable' }, 503) : response(breakdown('Current tenant'))
                : old.promise
            : normal(request);
        await draw();
        await setInput(find('input[type="date"]'), '2026-09-01');
        await act(async () => old.resolve(oldFails ? response({ detail: 'Old failure' }, 503) : response(breakdown('Old tenant'))));
        assert.doesNotMatch(text(), /Old tenant/);
        if (latestFails) assert.match(text(), /Tenant usage is unavailable/);
        else assert.match(text(), /Current tenant/);
    });
}

for (const malformed of [{}, { monetary_note: 'Bad rows', breakdown: [{}] },
    { monetary_note: 'Bad label', breakdown: [{ ...breakdown('one').breakdown[0], tenant_name: { bad: 'label' } }] }]) {
    test('malformed successful tenant usage cannot become empty success or crash', async () => {
        state.route = request => request.path.includes('/usage/breakdown') ? response(malformed) : normal(request);
        await draw();
        assert.match(text(), /Tenant usage is unavailable/);
        assert.doesNotMatch(text(), /No usage recorded for this period/);
    });
}

test('late tenant usage from a previous account is retired', async () => {
    const old = deferred();
    state.route = request => request.path.includes('/usage/breakdown') ? old.promise : normal(request);
    await draw();
    state.route = request => request.path.includes('/usage/breakdown')
        ? response({ detail: 'Replacement denied' }, 403) : normal(request);
    await act(async () => api.setToken('new-account'));
    await act(async () => old.resolve(response(breakdown('Private previous tenant'))));
    assert.doesNotMatch(text(), /Private previous tenant/);
    assert.match(text(), /Tenant usage is unavailable/);
});
