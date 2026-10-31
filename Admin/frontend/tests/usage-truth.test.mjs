import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

// Exercise the actual shared rendering used by the mounted Admin components.
// Vite already supplies esbuild. No browser, HTTP, billing or provider effect.
const bundle = buildSync({
    stdin: {
        contents: `export { UsageSummaryView } from './src/components/UsageBreakdownCard';
                   export { QuotaUsageView } from './src/components/QuotaUsage';
                   export { formatLegacyEstimate, formatRecordedCount, sumRecordedValues } from './src/lib/usage-evidence';`,
        resolveDir: fileURLToPath(new URL('..', import.meta.url)),
    },
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
    define: { 'import.meta.env': '{}' }, jsx: 'automatic',
});
const loaded = { exports: {} };
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(
    createRequire(import.meta.url), loaded, loaded.exports,
);
const { UsageSummaryView, QuotaUsageView, formatLegacyEstimate, formatRecordedCount, sumRecordedValues } = loaded.exports;
const observed = JSON.parse(readFileSync(new URL('../../../docs/sessions/artifacts/op11/api-example.json', import.meta.url), 'utf8'));
const render = (component, props) => renderToStaticMarkup(createElement(component, props));

test('actual endpoint example renders recorded usage, unavailable attribution and explicit estimate coverage', () => {
    const html = render(UsageSummaryView, { summary: observed.summary });
    assert.match(html, /Recorded call minutes/);
    assert.match(html, /Action records/);
    assert.match(html, /Provider attribution unavailable/);
    assert.match(html, /USD 5\.00; 1 recorded, 1 missing; not complete supplier cost/);
    assert.doesNotMatch(html, /Deepgram|Groq|Twilio|API Calls|No usage/);
});

test('failed usage read renders an error and unavailable counts, never invented zero', () => {
    const html = render(QuotaUsageView, { summary: null, error: true });
    assert.match(html, /role="alert"/);
    assert.match(html, /Recorded call minutes: <strong>Unavailable/);
    assert.match(html, /Action records: <strong>Unavailable/);
    assert.doesNotMatch(html, /<strong>0|USD 0|0\.00/);
});

test('loading usage is not a zero observation', () => {
    const html = render(QuotaUsageView, { summary: null, loading: true });
    assert.match(html, /Loading/);
    assert.doesNotMatch(html, /<strong>0|0\.00/);
});

test('an actually recorded zero count is displayed while supplier cost stays unavailable', () => {
    const html = render(QuotaUsageView, { summary: { ...observed.summary, total_call_minutes: 0, total_action_records: 0 } });
    assert.match(html, /Recorded call minutes: <strong>0/);
    assert.match(html, /Action records: <strong>0/);
    assert.match(html, /Supplier cost: <strong>Unavailable/);
});

test('known legacy zero is distinct from missing and invalid money', () => {
    const base = observed.summary.legacy_outbound_estimate;
    assert.match(formatLegacyEstimate({ ...base, recorded_total: 0 }), /^USD 0\.00/);
    for (const recorded_total of [null, undefined, NaN, Infinity]) {
        assert.equal(formatLegacyEstimate({ ...base, recorded_total }), 'Unavailable');
    }
    assert.equal(formatLegacyEstimate(null), 'Unavailable');
    assert.equal(formatRecordedCount(undefined), 'Unavailable');
    assert.equal(formatRecordedCount(NaN), 'Unavailable');
    assert.equal(formatRecordedCount(0), '0');
});

test('unknown legacy cost does not become USD zero in the actual summary view', () => {
    const summary = { ...observed.summary, legacy_outbound_estimate: { ...observed.summary.legacy_outbound_estimate, recorded_total: null } };
    const html = render(UsageSummaryView, { summary });
    assert.match(html, /Legacy outbound USD estimate: Unavailable/);
    assert.doesNotMatch(html, /USD 0\.00/);
});

test('recorded totals do not turn a missing row measurement into zero', () => {
    assert.equal(sumRecordedValues([59, 59]), 118);
    assert.equal(sumRecordedValues([0, 0]), 0);
    assert.equal(sumRecordedValues([59, undefined]), null);
    assert.equal(sumRecordedValues([NaN]), null);
});
