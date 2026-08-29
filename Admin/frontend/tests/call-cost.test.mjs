import assert from 'node:assert/strict';
import test from 'node:test';

import { formatCallCost, formatCurrencyAmount } from '../src/lib/call-cost.ts';

test('formats an authoritative ISO currency without assuming dollars', () => {
    assert.equal(formatCallCost(12.3456, 'eur'), 'EUR 12.3456');
    assert.equal(formatCallCost(12.3456, 'USD'), 'USD 12.3456');
});

test('labels a ledger amount whose currency was not recorded', () => {
    assert.equal(
        formatCallCost(12.3456, null),
        '12.3456 (currency unavailable)',
    );
});

test('renders a reversed monetary state as zero in its ledger currency', () => {
    assert.equal(formatCallCost(0, 'USD'), 'USD 0.0000');
});

test('does not render absent or non-finite costs', () => {
    assert.equal(formatCallCost(null, 'USD'), '—');
    assert.equal(formatCallCost(Number.NaN, 'USD'), '—');
});

test('formats aggregate USD estimates without a hardcoded dollar symbol', () => {
    assert.equal(formatCurrencyAmount(2.625, 'USD', 2), 'USD 2.63');
    assert.equal(formatCurrencyAmount(2.625, 'EUR', 2), 'EUR 2.63');
});
