// Actual ReactDOM/StrictMode and API client. Synthetic HTTP only; no PBX/DB/native claim.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, act, state, render, response, settle } from './bh-harness.mjs';

const call = (id, direction) => ({ id, tenant_id: `tenant-${id}`, tenant_name: `Tenant ${id}`,
    phone_number: '+15555550100', direction, called_did: direction === 'inbound' ? '+15555550200' : null,
    status: 'in_progress', campaign_name: null, duration_seconds: 12,
    termination_status: 'none', provider_hangup_confirmed: false });
const row = (id, direction) => state.host.querySelector(`tr[aria-label="Open ${direction} call ${id}"]`);
const action = (node, label) => [...node.querySelectorAll('button')].find(button => button.textContent.trim() === label);
const posts = id => state.requests.filter(request => request.method === 'POST' && request.path === `/api/v1/admin/calls/${id}/terminate`);
const reads = () => state.requests.filter(request => request.path === '/api/v1/admin/calls/live').length;

async function clickWithin(id, direction, label) {
    const button = action(row(id, direction), label);
    assert.ok(button && !button.disabled, `Expected reachable ${label} for ${id}`);
    await act(async () => button.click());
    await settle();
}

for (const direction of ['inbound', 'outbound']) {
    for (const surface of ['table-control', 'calls-page']) {
        test(`${surface}: confirming another call preserves ${direction} transport-unknown termination`, async () => {
            const uncertain = call('uncertain-call', direction);
            const confirmed = call('confirmed-call', direction === 'inbound' ? 'outbound' : 'inbound');
            let otherEnded = false, uncertainEnded = false;
            state.route = request => {
                if (request.path === '/api/v1/auth/me') return response({ id: 'operator', name: 'Operator',
                    email: 'operator@example.invalid', role: 'platform_admin', tenant_id: null });
                if (request.path === '/api/v1/admin/calls/live') return response(uncertainEnded ? [] : otherEnded ? [uncertain] : [uncertain, confirmed]);
                if (request.path === `/api/v1/admin/calls/${uncertain.id}/terminate` && request.method === 'POST') {
                    return response({ detail: 'Synthetic termination acknowledgement unavailable' }, 503);
                }
                if (request.path === `/api/v1/admin/calls/${confirmed.id}/terminate` && request.method === 'POST') {
                    otherEnded = true;
                    return response({ status: 'confirmed', call_id: confirmed.id, termination_status: 'confirmed',
                        provider_hangup_confirmed: true, provider_hangup_requested: true, termination_receipt_saved: true });
                }
                if (request.path === `/api/v1/admin/calls/${uncertain.id}` && request.method === 'GET') {
                    uncertainEnded = true;
                    return response({ ...uncertain, status: 'ended', termination_status: 'confirmed', provider_hangup_confirmed: true });
                }
                return response({ detail: 'Unexpected synthetic request' }, 500);
            };
            const component = surface === 'calls-page' ? el(C.CallsPage) : el(C.LiveCallsTable, { onCallSelect() {} });
            await render(el(C.AuthProvider, null, el(C.MemoryRouter, null, component)));
            await settle();
            await clickWithin(uncertain.id, direction, 'End');
            await clickWithin(uncertain.id, direction, 'Yes');
            const originalRow = row(uncertain.id, direction);
            assert.match(originalRow.textContent, /Outcome unknown/);
            assert.ok(action(originalRow, 'Check status'));
            assert.equal(posts(uncertain.id).length, 1);
            const readsBeforeOtherTermination = reads();

            await clickWithin(confirmed.id, confirmed.direction, 'End');
            await clickWithin(confirmed.id, confirmed.direction, 'Yes');
            const currentRow = row(uncertain.id, direction);
            assert.ok(currentRow, 'The uncertain call still exists in the fresh server list');
            assert.equal(row(confirmed.id, confirmed.direction) === null, true);
            assert.equal(posts(uncertain.id).length, 1);
            assert.equal(posts(confirmed.id).length, 1);
            const observed = { surface, direction, original_row_connected: originalRow.isConnected,
                same_row: currentRow === originalRow, text: currentRow.textContent,
                check_status_visible: Boolean(action(currentRow, 'Check status')),
                end_visible: Boolean(action(currentRow, 'End')),
                post_count_uncertain: posts(uncertain.id).length,
                post_count_confirmed: posts(confirmed.id).length,
                live_read_delta: reads() - readsBeforeOtherTermination };
            console.log('cross_call_termination_observation', JSON.stringify(observed));
            assert.equal(observed.end_visible, false, 'Unrelated confirmation must not re-expose a new termination action');
            assert.equal(observed.check_status_visible, true, 'Retain read-only reconciliation for the uncertain call');
            assert.match(currentRow.textContent, /Outcome unknown/);

            // A deliberate exact read may resolve the original request once
            // the server supplies saved provider confirmation. Never resend it.
            await clickWithin(uncertain.id, direction, 'Check status');
            assert.equal(row(uncertain.id, direction) === null, true);
            assert.match(state.host.textContent, /No Active Calls/);
            assert.equal(state.requests.filter(request => request.method === 'GET'
                && request.path === `/api/v1/admin/calls/${uncertain.id}`).length, 1);
            assert.equal(posts(uncertain.id).length, 1);
            assert.equal(posts(confirmed.id).length, 1);
        });
    }
}
