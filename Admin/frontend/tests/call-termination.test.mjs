import assert from 'node:assert/strict';
import test from 'node:test';

import {
    classifyTerminationResult,
    getTerminationPhase,
    mergePolledLiveCalls,
    terminationPatchFromResult,
} from '../src/lib/call-termination.ts';

const activeCall = {
    id: 'call-1',
    tenant_id: 'tenant-1',
    tenant_name: 'Acme',
    phone_number: '+15555550100',
    campaign_name: null,
    status: 'in_call',
    started_at: '2026-08-26T12:00:00.000Z',
    duration_seconds: 12,
};

function response(overrides = {}) {
    return {
        status: 'requested',
        call_id: activeCall.id,
        previous_status: activeCall.status,
        new_status: 'ended',
        call_status: 'ended',
        termination_status: 'requested',
        provider_hangup_requested: true,
        provider_hangup_confirmed: false,
        provider_hangup_error: null,
        ...overrides,
    };
}

test('requested is pending even when a status-looking field says ended', () => {
    assert.deepEqual(classifyTerminationResult(response()), {
        phase: 'requested',
        error: null,
    });
});

test('confirmed requires the provider-confirmed contract', () => {
    assert.equal(classifyTerminationResult(response({
        status: 'confirmed',
        termination_status: 'confirmed',
        provider_hangup_confirmed: true,
    })).phase, 'confirmed');

    assert.equal(classifyTerminationResult(response({
        status: 'confirmed',
        termination_status: 'confirmed',
        provider_hangup_confirmed: false,
    })).phase, 'failed');
});

test('already-terminal is safe to remove from the live list', () => {
    assert.equal(classifyTerminationResult(response({
        status: 'already_terminal',
        termination_status: 'confirmed',
    })).phase, 'confirmed');
});

test('failed provider response preserves the useful error for retry', () => {
    const classified = classifyTerminationResult(response({
        status: 'failed',
        termination_status: 'failed',
        provider_hangup_error: 'ARI channel did not close',
    }));
    assert.deepEqual(classified, {
        phase: 'failed',
        error: 'ARI channel did not close',
    });
});

test('a requested response produces a pending-only live-row patch', () => {
    const patch = terminationPatchFromResult(
        response(),
        new Date('2026-08-26T12:01:00.000Z'),
    );
    assert.equal(patch.termination_status, 'requested');
    assert.equal(patch.termination_requested_at, '2026-08-26T12:01:00.000Z');
    assert.equal(patch.provider_hangup_confirmed, false);
});

test('poll reconciliation preserves a raced pending marker', () => {
    const pending = {
        ...activeCall,
        termination_status: 'requested',
        termination_requested_at: '2026-08-26T12:01:00.000Z',
        provider_hangup_requested: true,
        provider_hangup_confirmed: false,
    };
    const merged = mergePolledLiveCalls([pending], [{
        ...activeCall,
        termination_status: 'none',
    }]);
    assert.equal(getTerminationPhase(merged[0]), 'requested');
});

test('poll reconciliation accepts failed and missing terminal rows', () => {
    const pending = { ...activeCall, termination_status: 'requested' };
    const failed = mergePolledLiveCalls([pending], [{
        ...activeCall,
        termination_status: 'failed',
        termination_error: 'No confirmation received',
    }]);
    assert.equal(getTerminationPhase(failed[0]), 'failed');
    assert.deepEqual(mergePolledLiveCalls([pending], []), []);
});

test('a confirmed live-row marker is terminal, never actionable pending', () => {
    assert.equal(getTerminationPhase({
        ...activeCall,
        termination_status: 'confirmed',
    }), 'confirmed');
});
