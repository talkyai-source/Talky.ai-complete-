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
        export { ActionsTable } from './src/components/ActionsTable'; export { api } from './src/lib/api';`,
        resolveDir: fileURLToPath(new URL('..', import.meta.url)) },
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
    define: { 'import.meta.env': '{}' }, jsx: 'automatic',
    plugins: [{ name: 'actual-action-hooks', setup(build) {
        build.onResolve({ filter: /^react$/ }, (args) => /(?:ActionDetailDrawer|ActionsTable)\.tsx$/.test(args.importer)
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
const { ActionDetailDrawer, ActionsTable, api } = loaded.exports;
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
