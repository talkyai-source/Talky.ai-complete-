import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { renderToStaticMarkup } from 'react-dom/server';

// Actual drawer/section with synthetic hook scheduling and API promises.
// This is component-boundary evidence, not browser/React-concurrency acceptance.
const bundle = await build({
    stdin: { contents: `export { CallDetailDrawer, CRMReceiptsSection } from './src/components/CallDetailDrawer';
        export { api } from './src/lib/api';`, resolveDir: fileURLToPath(new URL('..', import.meta.url)) },
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
    define: { 'import.meta.env': '{}' }, jsx: 'automatic',
    loader: { '.css': 'empty' },
    plugins: [{ name: 'actual-crm-hooks', setup(build) {
        build.onResolve({ filter: /^react$/ }, (args) => /CallDetailDrawer\.tsx$/.test(args.importer)
            ? { path: 'hooks', namespace: 'synthetic' } : undefined);
        build.onLoad({ filter: /.*/, namespace: 'synthetic' }, () => ({ contents: `
            export const useState = (...a) => globalThis.__crmHooks.useState(...a);
            export const useRef = (...a) => globalThis.__crmHooks.useRef(...a);
            export const useCallback = (fn) => fn;
            export const useEffect = (...a) => globalThis.__crmHooks.useEffect(...a);` }));
    } }],
});
const loaded = { exports: {} };
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), loaded, loaded.exports);
const { CallDetailDrawer, CRMReceiptsSection, api } = loaded.exports;
const settle = () => new Promise((resolve) => setImmediate(resolve));

function hooks() {
    const values = [], effects = [], pending = [];
    let cursor = 0;
    const runtime = {
        useState(initial) {
            const index = cursor++;
            if (!(index in values)) values[index] = initial;
            return [values[index], (value) => { values[index] = typeof value === 'function' ? value(values[index]) : value; }];
        },
        useRef(initial) {
            const index = cursor++;
            if (!(index in values)) values[index] = { current: initial };
            return values[index];
        },
        useEffect(effect, deps) {
            const index = cursor++, old = effects[index];
            if (!old || deps.some((value, i) => value !== old.deps[i])) {
                pending.push(() => { old?.cleanup?.(); effects[index] = { deps, cleanup: effect() }; });
            }
        },
    };
    return {
        render(Component, props) {
            globalThis.__crmHooks = runtime;
            cursor = 0;
            const tree = Component(props);
            for (const effect of pending.splice(0)) effect();
            return tree;
        },
        close() { for (const effect of effects) effect?.cleanup?.(); delete globalThis.__crmHooks; },
    };
}

function walk(node, predicate) {
    if (node == null || typeof node !== 'object') return null;
    if (predicate(node)) return node;
    for (const child of [node.props?.children].flat(Infinity)) { const match = walk(child, predicate); if (match) return match; }
    return null;
}
const button = (tree) => walk(tree, (node) => node.type === 'button' && node.props.children === 'Inspect original account');
const receipt = (extra = {}) => ({
    provider: 'hubspot', status: 'unknown', phase: 'creating_call',
    destination_connector_id: 'original-connector', destination_account_id: 'original-org',
    remote_contact_id: 'saved-contact', remote_call_id: null, updated_at: '2026-10-06T03:00:00Z',
    inspection_available: true, ...extra,
});
const props = (callId = 'A', receipts = [receipt()]) => ({ callId, receipts, available: true });
const observed = (callId = 'A', outcome = 'observed_reference', reason = 'reference_observed_only') => ({
    call_id: callId, provider: 'hubspot', outcome, reason, observed_remote_id: outcome === 'observed_reference' ? 'observed-activity' : null,
    observed_at: '2026-10-06T03:01:00Z',
});

test('saved CRM evidence does not inspect until explicit click and never changes saved status', async () => {
    const h = hooks(), original = api.inspectAdminCRMDelivery, reads = [], receipts = [receipt()];
    const before = structuredClone(receipts);
    api.inspectAdminCRMDelivery = async (...args) => { reads.push(args); return { data: observed() }; };
    try {
        const tree = h.render(CRMReceiptsSection, props('A', receipts));
        assert.deepEqual(reads, []);
        const html = renderToStaticMarkup(tree);
        assert.match(html, /original-org/);
        assert.match(html, /unknown.*creating call/);
        button(tree).props.onClick(); await settle();
        const updated = renderToStaticMarkup(h.render(CRMReceiptsSection, props('A', receipts)));
        assert.deepEqual(reads, [['A', 'hubspot']]);
        assert.match(updated, /Matching reference observed/);
        assert.match(updated, /does not verify the payload, contact, or completion/);
        assert.match(updated, /unknown.*creating call/);
        assert.deepEqual(receipts, before);
        await settle(); h.render(CRMReceiptsSection, props('A', receipts));
        assert.equal(reads.length, 1);
    } finally { h.close(); api.inspectAdminCRMDelivery = original; }
});

for (const [outcome, reason, label] of [
    ['no_match', 'absence_is_inconclusive', /empty search does not prove.*not executed/],
    ['ambiguous', 'multiple_references', /Multiple matching references — unresolved/],
    ['unavailable', 'original_receipt_unavailable', /saved receipt does not contain/],
    ['unavailable', 'original_authorization_unavailable', /unique, active original account/],
    ['unavailable', 'provider_read_unavailable', /provider observation could not be obtained/],
]) {
    test(`${outcome}/${reason} is truthful and leaves status unresolved`, async () => {
        const h = hooks(), original = api.inspectAdminCRMDelivery;
        api.inspectAdminCRMDelivery = async () => ({ data: observed('A', outcome, reason) });
        try {
            button(h.render(CRMReceiptsSection, props())).props.onClick(); await settle();
            const html = renderToStaticMarkup(h.render(CRMReceiptsSection, props()));
            assert.match(html, label);
            assert.match(html, /unknown.*creating call/);
            assert.doesNotMatch(html, />Retry<|>Resolve<|>Complete<|observed-activity/);
        } finally { h.close(); api.inspectAdminCRMDelivery = original; }
    });
}

test('double click sends one read, with no implicit retry', async () => {
    const h = hooks(), original = api.inspectAdminCRMDelivery;
    let release, count = 0;
    api.inspectAdminCRMDelivery = () => { count++; return new Promise((resolve) => { release = resolve; }); };
    try {
        const click = button(h.render(CRMReceiptsSection, props())).props.onClick;
        click(); click();
        assert.equal(count, 1);
        assert.match(renderToStaticMarkup(h.render(CRMReceiptsSection, props())), /disabled.*Inspecting original account/);
        release({ error: { message: 'private network details' } }); await settle();
        const html = renderToStaticMarkup(h.render(CRMReceiptsSection, props()));
        assert.match(html, /Inspection unavailable. Saved receipt unchanged/);
        assert.doesNotMatch(html, /private network/);
        assert.equal(count, 1);
    } finally { h.close(); api.inspectAdminCRMDelivery = original; }
});

test('late A observation cannot replace B and cannot clear its pending read', async () => {
    const h = hooks(), original = api.inspectAdminCRMDelivery, pending = [];
    api.inspectAdminCRMDelivery = (id) => new Promise((resolve) => pending.push({ id, resolve }));
    try {
        button(h.render(CRMReceiptsSection, props('A'))).props.onClick();
        h.render(CRMReceiptsSection, props('B'));
        button(h.render(CRMReceiptsSection, props('B'))).props.onClick();
        pending[0].resolve({ data: observed('A') }); await settle();
        const waiting = renderToStaticMarkup(h.render(CRMReceiptsSection, props('B')));
        assert.match(waiting, /Inspecting original account/);
        assert.doesNotMatch(waiting, /observed-activity/);
        pending[1].resolve({ data: observed('B') }); await settle();
        assert.match(renderToStaticMarkup(h.render(CRMReceiptsSection, props('B'))), /observed-activity/);
    } finally { h.close(); api.inspectAdminCRMDelivery = original; }
});

for (const variant of ['foreign_call', 'foreign_provider', 'bad_outcome', 'bad_reason', 'inherited_outcome', 'inherited_reason', 'throw']) {
    test(`${variant} response never populates current evidence`, async () => {
        const h = hooks(), original = api.inspectAdminCRMDelivery, value = observed();
        if (variant === 'foreign_call') value.call_id = 'B';
        if (variant === 'foreign_provider') value.provider = 'salesforce';
        if (variant === 'bad_outcome') value.outcome = 'succeeded';
        if (variant === 'bad_reason') value.reason = 'completed';
        if (variant === 'inherited_outcome') value.outcome = 'constructor';
        if (variant === 'inherited_reason') value.reason = 'toString';
        api.inspectAdminCRMDelivery = async () => {
            if (variant === 'throw') throw new Error('private details');
            return { data: value };
        };
        try {
            button(h.render(CRMReceiptsSection, props())).props.onClick(); await settle();
            const html = renderToStaticMarkup(h.render(CRMReceiptsSection, props()));
            assert.match(html, /Inspection unavailable. Saved receipt unchanged/);
            assert.doesNotMatch(html, /observed-activity|private details/);
        } finally { h.close(); api.inspectAdminCRMDelivery = original; }
    });
}

test('missing proof offers no inspect button or current-account substitution', () => {
    const h = hooks();
    try {
        const tree = h.render(CRMReceiptsSection, props('A', [receipt({
            destination_account_id: null, destination_connector_id: null, inspection_available: false,
        })]));
        assert.equal(button(tree), null);
        assert.match(renderToStaticMarkup(tree), /missing original identity/);
        assert.doesNotMatch(renderToStaticMarkup(tree), /original-org/);
    } finally { h.close(); }
});

test('actual API request is one GET with only call and provider in its URL', async () => {
    const original = globalThis.fetch, requests = [];
    globalThis.fetch = async (url, options) => { requests.push({ url, options }); return new Response(JSON.stringify(observed())); };
    try {
        await api.inspectAdminCRMDelivery('A', 'hubspot');
        assert.equal(requests.length, 1);
        assert.match(requests[0].url, /\/admin\/calls\/A\/crm-deliveries\/hubspot\/inspection$/);
        assert.equal(requests[0].options.method, 'GET');
        assert.equal(requests[0].options.body, undefined);
    } finally { globalThis.fetch = original; }
});

test('late detail A cannot supply CRM receipt props to selected B', async () => {
    const h = hooks(), pending = [], reads = [], originals = {
        detail: api.getAdminCallDetail, recordings: api.getAdminRecordings, feedback: api.getAdminFeedback,
    };
    api.getAdminCallDetail = (id) => new Promise((resolve) => pending.push({ id, resolve }));
    api.getAdminRecordings = async ({ call_id }) => { reads.push(call_id); return { data: { items: [] } }; };
    api.getAdminFeedback = async () => ({ data: { items: [] } });
    const selected = (id) => ({ callId: id, onClose() {} });
    const detail = (id) => ({ id, summary_json: null, timeline: [], cost: null, duration_seconds: 0,
        crm_receipts_available: true, crm_deliveries: [receipt({ destination_account_id: `original-${id}` })] });
    try {
        h.render(CallDetailDrawer, selected('A'));
        h.render(CallDetailDrawer, selected('B'));
        pending[1].resolve({ data: detail('B') }); await settle();
        pending[0].resolve({ data: detail('A') }); await settle();
        const tree = h.render(CallDetailDrawer, selected('B'));
        const section = walk(tree, (node) => node.type === CRMReceiptsSection);
        assert.equal(section.props.callId, 'B');
        assert.equal(section.props.receipts[0].destination_account_id, 'original-B');
        assert.deepEqual(reads, ['B']);
    } finally {
        h.close(); api.getAdminCallDetail = originals.detail;
        api.getAdminRecordings = originals.recordings; api.getAdminFeedback = originals.feedback;
    }
});
