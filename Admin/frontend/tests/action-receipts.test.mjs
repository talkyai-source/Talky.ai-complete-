import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { renderToStaticMarkup } from 'react-dom/server';

// Actual component/effect code with the existing minimal hook-scheduler style.
// Synthetic API promises only; not a browser or React-concurrency qualification.
const bundle = await build({
    stdin: { contents: `export { ActionDetailDrawer } from './src/components/ActionDetailDrawer';
        export { ActionsTable } from './src/components/ActionsTable';
        export { ActionReceiptPanel } from './src/components/ActionReceiptPanel'; export { api } from './src/lib/api';`,
        resolveDir: fileURLToPath(new URL('..', import.meta.url)) },
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
    define: { 'import.meta.env': '{}' }, jsx: 'automatic',
    plugins: [{ name: 'actual-action-hooks', setup(build) {
        build.onResolve({ filter: /^react$/ }, (args) => /(?:ActionDetailDrawer|ActionsTable|ActionReceiptPanel)\.tsx$/.test(args.importer)
            ? { path: 'hooks', namespace: 'synthetic' } : undefined);
        build.onLoad({ filter: /.*/, namespace: 'synthetic' }, () => ({ contents: `
            export const useState = (...a) => globalThis.__actionHooks.useState(...a);
            export const useRef = (...a) => globalThis.__actionHooks.useRef(...a);
            export const useCallback = (fn) => fn;
            export const useEffect = (...a) => globalThis.__actionHooks.useEffect(...a);` }));
    } }],
});
const loaded = { exports: {} };
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), loaded, loaded.exports);
const { ActionDetailDrawer, ActionsTable, ActionReceiptPanel, api } = loaded.exports;
const settle = () => new Promise((resolve) => setImmediate(resolve));

function hooks(initial = []) {
    const values = [...initial], effects = [], pending = [];
    let cursor = 0;
    const runtime = {
        useState(initialValue) {
            const index = cursor++;
            if (!(index in values)) values[index] = initialValue;
            return [values[index], (value) => { values[index] = typeof value === 'function' ? value(values[index]) : value; }];
        },
        useRef(initialValue) {
            const index = cursor++;
            if (!(index in values)) values[index] = { current: initialValue };
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
            globalThis.__actionHooks = runtime;
            cursor = 0;
            const tree = Component(props);
            for (const effect of pending.splice(0)) effect();
            return tree;
        },
        close() { for (const effect of effects) effect?.cleanup?.(); delete globalThis.__actionHooks; },
    };
}

const action = (id, status = 'unknown', extra = {}) => ({
    id, type: 'send_email', tenant_name: `Synthetic tenant ${id}`, status,
    created_at: '2026-10-05T12:00:00Z', duration_ms: null, is_cancellable: false, is_retryable: false,
    saved_receipt: { action_id: id, status, success: false, confirmation_allowed: false,
        receipt: { provider: 'gmail', external_account_id: `original-${id}`, message_id: `message-${id}` } },
    ...extra,
});
function walk(node, predicate) {
    if (node == null || typeof node !== 'object') return null;
    if (predicate(node)) return node;
    for (const child of [node.props?.children].flat(Infinity)) { const match = walk(child, predicate); if (match) return match; }
    return null;
}

for (const status of ['unknown', 'scheduled']) {
    test(`table preserves ${status} and offers its actual filter`, () => {
        const h = hooks([[action('A', status)], false]);
        const before = api.getActions;
        api.getActions = async () => ({ data: { items: [], total: 0 } });
        try {
            const html = renderToStaticMarkup(h.render(ActionsTable, { onActionSelect() {} }));
            const row = html.slice(html.indexOf('<tbody'));
            assert.match(row, new RegExp(`>${status[0].toUpperCase() + status.slice(1)}<`));
            assert.doesNotMatch(row, />Pending</);
            assert.match(html, new RegExp(`option value="${status}"`));
        } finally { h.close(); api.getActions = before; }
    });
}

test('drawer shows sanitized unconfirmed receipt without raw result or current-account substitution', async () => {
    const h = hooks(), before = api.getActionDetail;
    api.getActionDetail = async () => ({ data: action('A', 'completed', {
        connector_name: 'Current connector display',
        input_data: { token: 'do-not-display', recipient: 'private@example.invalid' },
        output_data: { secret: 'do-not-display', success: true },
    }) });
    const props = { actionId: 'A', onClose() {} };
    try {
        h.render(ActionDetailDrawer, props); await settle();
        const html = renderToStaticMarkup(h.render(ActionDetailDrawer, props));
        assert.match(html, /original-A/);
        assert.match(html, /Outcome unverified/);
        assert.doesNotMatch(html, /Acknowledgement recorded|Input Payload|Output \/ Result|do-not-display|private@example/);
    } finally { h.close(); api.getActionDetail = before; }
});

test('late A detail cannot replace selected B receipt', async () => {
    const h = hooks(), before = api.getActionDetail, pending = [];
    api.getActionDetail = (id) => new Promise((resolve) => pending.push({ id, resolve }));
    try {
        h.render(ActionDetailDrawer, { actionId: 'A', onClose() {} });
        const props = { actionId: 'B', onClose() {} };
        h.render(ActionDetailDrawer, props);
        pending[1].resolve({ data: action('B') }); await settle();
        pending[0].resolve({ data: action('A') }); await settle();
        const html = renderToStaticMarkup(h.render(ActionDetailDrawer, props));
        assert.match(html, /Synthetic tenant B/);
        assert.doesNotMatch(html, /Synthetic tenant A/);
        assert.match(html, /original-B/);
        assert.doesNotMatch(html, /original-A/);
    } finally { h.close(); api.getActionDetail = before; }
});

for (const variant of ['missing', 'mismatched', 'explicit-proof']) {
    test(`receipt ${variant} retains the actual proof boundary`, async () => {
        const h = hooks(), before = api.getActionDetail;
        const record = action('A', 'completed', { connector_name: 'Current account label' });
        if (variant === 'missing') record.saved_receipt = null;
        else {
            record.saved_receipt.success = true;
            record.saved_receipt.confirmation_allowed = true;
            if (variant === 'mismatched') record.saved_receipt.action_id = 'B';
        }
        api.getActionDetail = async () => ({ data: record });
        const props = { actionId: 'A', onClose() {} };
        try {
            h.render(ActionDetailDrawer, props); await settle();
            const html = renderToStaticMarkup(h.render(ActionDetailDrawer, props));
            if (variant === 'explicit-proof') {
                assert.match(html, /Acknowledgement recorded/);
                assert.match(html, /does not verify current provider state or recipient delivery/);
            } else {
                assert.match(html, /Outcome unverified/);
                assert.match(html, /Original account ID<\/span><span class="value mono">Unavailable/);
                assert.doesNotMatch(html, /original-A|Acknowledgement recorded/);
            }
        } finally { h.close(); api.getActionDetail = before; }
    });
}

test('late A cancellation refresh cannot replace B or refresh the old action', async () => {
    const h = hooks(), before = api.getActionDetail, beforeCancel = api.cancelAction;
    const reads = []; let release;
    api.getActionDetail = async (id) => { reads.push(id); return { data: action(id, 'pending', { is_cancellable: true }) }; };
    api.cancelAction = () => new Promise((resolve) => { release = resolve; });
    const props = (id) => ({ actionId: id, onClose() {} });
    const button = (tree, label) => walk(tree, (n) => n.type === 'button' && [n.props.children].flat().includes(label));
    try {
        h.render(ActionDetailDrawer, props('A')); await settle();
        await button(h.render(ActionDetailDrawer, props('A')), 'Cancel Action').props.onClick();
        const cancel = button(h.render(ActionDetailDrawer, props('A')), 'Yes, Cancel').props.onClick();
        h.render(ActionDetailDrawer, props('B')); await settle();
        release({ data: { action_id: 'A', new_status: 'cancelled' } }); await cancel;
        const html = renderToStaticMarkup(h.render(ActionDetailDrawer, props('B')));
        assert.deepEqual(reads, ['A', 'B']);
        assert.match(html, /Synthetic tenant B/);
        assert.doesNotMatch(html, /Synthetic tenant A|Yes, Cancel/);
    } finally { h.close(); api.getActionDetail = before; api.cancelAction = beforeCancel; }
});

test('a foreign detail response cannot populate the current selection', async () => {
    const h = hooks(), before = api.getActionDetail;
    api.getActionDetail = async () => ({ data: action('other') });
    const props = { actionId: 'A', onClose() {} };
    try {
        h.render(ActionDetailDrawer, props); await settle();
        const html = renderToStaticMarkup(h.render(ActionDetailDrawer, props));
        assert.match(html, /selected action receipt is unavailable/);
        assert.doesNotMatch(html, /original-other/);
    } finally { h.close(); api.getActionDetail = before; }
});

test('cancel rejection remains an error, without a success refresh', async () => {
    const h = hooks(), before = api.getActionDetail, beforeCancel = api.cancelAction;
    let reads = 0;
    api.getActionDetail = async () => { reads++; return { data: action('A', 'pending', { is_cancellable: true }) }; };
    api.cancelAction = async () => ({ error: { code: 'conflict', message: 'Cancellation is not confirmed.' } });
    const props = { actionId: 'A', onClose() {} };
    const button = (tree, label) => walk(tree, (n) => n.type === 'button' && [n.props.children].flat().includes(label));
    try {
        h.render(ActionDetailDrawer, props); await settle();
        await button(h.render(ActionDetailDrawer, props), 'Cancel Action').props.onClick();
        await button(h.render(ActionDetailDrawer, props), 'Yes, Cancel').props.onClick();
        assert.match(renderToStaticMarkup(h.render(ActionDetailDrawer, props)), /Cancellation is not confirmed/);
        assert.equal(reads, 1);
    } finally { h.close(); api.getActionDetail = before; api.cancelAction = beforeCancel; }
});

test('a cancellation confirmation belongs only to its selected action', async () => {
    const h = hooks(), before = api.getActionDetail;
    api.getActionDetail = async (id) => ({ data: action(id, 'pending', { is_cancellable: true }) });
    const props = (id) => ({ actionId: id, onClose() {} });
    try {
        h.render(ActionDetailDrawer, props('A')); await settle();
        const tree = h.render(ActionDetailDrawer, props('A'));
        const button = walk(tree, (n) => n.type === 'button' && [n.props.children].flat().includes('Cancel Action'));
        assert.ok(button);
        await button.props.onClick();
        assert.match(renderToStaticMarkup(h.render(ActionDetailDrawer, props('A'))), /Yes, Cancel/);
        h.render(ActionDetailDrawer, props('B')); await settle();
        assert.doesNotMatch(renderToStaticMarkup(h.render(ActionDetailDrawer, props('B'))), /Yes, Cancel/);
    } finally { h.close(); api.getActionDetail = before; }
});

const inspectable = (id = 'A') => {
    const item = action(id);
    item.email_inspection_available = true;
    item.saved_receipt.receipt.identity_version = 'authorization_row_v1';
    item.saved_receipt.receipt.account_row_id = `row-${id}`;
    delete item.saved_receipt.receipt.external_account_id;
    return item;
};
const inspection = (id = 'A', outcome = 'observed_message', reason = 'exact_message_observed_only') => ({
    action_id: id, outcome, reason, observed_message_id: outcome === 'observed_message' ? `message-${id}` : null,
    observed_at: '2026-10-06T04:00:00Z',
});
const inspectionButton = (tree) => walk(tree, (node) => node.type === 'button' && node.props.children === 'Inspect saved Gmail message');

test('Gmail observation requires an explicit click and retains saved unknown state', async () => {
    const h = hooks(), original = api.inspectAdminEmailAction, reads = [], item = inspectable();
    const before = structuredClone(item);
    api.inspectAdminEmailAction = async (id) => { reads.push(id); return { data: inspection(id) }; };
    try {
        const tree = h.render(ActionReceiptPanel, { action: item });
        assert.deepEqual(reads, []);
        const saved = renderToStaticMarkup(tree);
        assert.match(saved, /Original authorization row/); assert.match(saved, /row-A/);
        assert.match(saved, /authorization_row_v1/);
        assert.match(saved, /Original account ID<\/span><span class="value mono">Unavailable/);
        inspectionButton(tree).props.onClick(); await settle();
        const html = renderToStaticMarkup(h.render(ActionReceiptPanel, { action: item }));
        assert.match(html, /Exact message observed/);
        assert.match(html, /does not prove it was sent, delivered, or contained the intended payload/);
        assert.match(html, /Outcome unverified/);
        assert.deepEqual(item, before); assert.deepEqual(reads, ['A']);
        await settle(); h.render(ActionReceiptPanel, { action: item }); assert.equal(reads.length, 1);
    } finally { h.close(); api.inspectAdminEmailAction = original; }
});

for (const [outcome, reason, expected] of [
    ['not_observed', 'absence_is_inconclusive', /does not prove non-execution or make another send safe/],
    ['unavailable', 'saved_proof_unavailable', /complete, consistent original authorization/],
    ['unavailable', 'original_authorization_unavailable', /original active authorization/],
    ['unavailable', 'provider_read_unavailable', /provider observation could not be obtained/],
]) {
    test(`Gmail ${reason} never offers retry or completion`, async () => {
        const h = hooks(), original = api.inspectAdminEmailAction, item = inspectable();
        api.inspectAdminEmailAction = async () => ({ data: inspection('A', outcome, reason) });
        try {
            inspectionButton(h.render(ActionReceiptPanel, { action: item })).props.onClick(); await settle();
            const html = renderToStaticMarkup(h.render(ActionReceiptPanel, { action: item }));
            assert.match(html, expected); assert.match(html, /Outcome unverified/);
            assert.doesNotMatch(html, />Retry<|>Resolve<|>Complete<|Observed message ID/);
        } finally { h.close(); api.inspectAdminEmailAction = original; }
    });
}

test('Gmail double click sends one read and never echoes raw API errors', async () => {
    const h = hooks(), original = api.inspectAdminEmailAction, item = inspectable();
    let count = 0, release;
    api.inspectAdminEmailAction = () => { count++; return new Promise((resolve) => { release = resolve; }); };
    try {
        const click = inspectionButton(h.render(ActionReceiptPanel, { action: item })).props.onClick;
        click(); click(); assert.equal(count, 1);
        assert.match(renderToStaticMarkup(h.render(ActionReceiptPanel, { action: item })), /disabled.*Inspecting original authorization/);
        release({ error: { message: 'private provider error' } }); await settle();
        const html = renderToStaticMarkup(h.render(ActionReceiptPanel, { action: item }));
        assert.match(html, /Inspection unavailable. Saved receipt unchanged/);
        assert.doesNotMatch(html, /private provider/); assert.equal(count, 1);
    } finally { h.close(); api.inspectAdminEmailAction = original; }
});

test('late Gmail A observation cannot replace B or clear B pending state', async () => {
    const h = hooks(), original = api.inspectAdminEmailAction, pending = [];
    api.inspectAdminEmailAction = (id) => new Promise((resolve) => pending.push({ id, resolve }));
    try {
        inspectionButton(h.render(ActionReceiptPanel, { action: inspectable('A') })).props.onClick();
        h.render(ActionReceiptPanel, { action: inspectable('B') });
        inspectionButton(h.render(ActionReceiptPanel, { action: inspectable('B') })).props.onClick();
        pending[0].resolve({ data: inspection('A') }); await settle();
        const waiting = renderToStaticMarkup(h.render(ActionReceiptPanel, { action: inspectable('B') }));
        assert.match(waiting, /Inspecting original authorization/); assert.doesNotMatch(waiting, /Observed message ID/);
        pending[1].resolve({ data: inspection('B') }); await settle();
        const ready = renderToStaticMarkup(h.render(ActionReceiptPanel, { action: inspectable('B') }));
        assert.match(ready, /Observed message ID: message-B/); assert.doesNotMatch(ready, /message-A/);
    } finally { h.close(); api.inspectAdminEmailAction = original; }
});

for (const bad of ['action', 'message', 'outcome', 'reason', 'inherited', 'contradictory', 'throw']) {
    test(`Gmail ${bad} observation is rejected`, async () => {
        const h = hooks(), original = api.inspectAdminEmailAction, item = inspectable(), result = inspection();
        if (bad === 'action') result.action_id = 'B';
        if (bad === 'message') result.observed_message_id = 'different';
        if (bad === 'outcome') result.outcome = 'completed';
        if (bad === 'reason') result.reason = 'sent';
        if (bad === 'inherited') result.outcome = 'constructor';
        if (bad === 'contradictory') result.reason = 'absence_is_inconclusive';
        api.inspectAdminEmailAction = async () => {
            if (bad === 'throw') throw new Error('private details');
            return { data: result };
        };
        try {
            inspectionButton(h.render(ActionReceiptPanel, { action: item })).props.onClick(); await settle();
            const html = renderToStaticMarkup(h.render(ActionReceiptPanel, { action: item }));
            assert.match(html, /Inspection unavailable. Saved receipt unchanged/);
            assert.doesNotMatch(html, /Observed message ID|private details/);
        } finally { h.close(); api.inspectAdminEmailAction = original; }
    });
}

test('missing availability or mismatched saved receipt offers no Gmail inspection', () => {
    for (const variant of ['unavailable', 'mismatched']) {
        const h = hooks(), item = inspectable();
        if (variant === 'unavailable') item.email_inspection_available = false;
        else item.saved_receipt.action_id = 'B';
        try { assert.equal(inspectionButton(h.render(ActionReceiptPanel, { action: item })), null); }
        finally { h.close(); }
    }
});

test('Gmail API inspection makes one GET with only saved action ID', async () => {
    const original = globalThis.fetch, requests = [];
    globalThis.fetch = async (url, options) => { requests.push({ url, options }); return new Response(JSON.stringify(inspection())); };
    try {
        await api.inspectAdminEmailAction('A');
        assert.equal(requests.length, 1);
        assert.match(requests[0].url, /\/admin\/actions\/A\/email-inspection$/);
        assert.equal(requests[0].options.method, 'GET'); assert.equal(requests[0].options.body, undefined);
    } finally { globalThis.fetch = original; }
});
