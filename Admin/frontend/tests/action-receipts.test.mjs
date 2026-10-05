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
        export { AuthProvider } from './src/lib/auth';
        export { ActionReceiptPanel } from './src/components/ActionReceiptPanel'; export { api } from './src/lib/api';`,
        resolveDir: fileURLToPath(new URL('..', import.meta.url)) },
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
    define: { 'import.meta.env': '{}' }, jsx: 'automatic',
    plugins: [{ name: 'actual-action-hooks', setup(build) {
        build.onResolve({ filter: /^react$/, namespace: 'synthetic' }, () => ({ path: 'react', external: true }));
        build.onResolve({ filter: /^react$/ }, (args) => /(?:ActionDetailDrawer|ActionsTable|ActionReceiptPanel|auth)\.tsx$/.test(args.importer)
            ? { path: 'hooks', namespace: 'synthetic' } : undefined);
        build.onLoad({ filter: /.*/, namespace: 'synthetic' }, () => ({ contents: `
            export { createContext, useContext } from 'react';
            export const useState = (...a) => globalThis.__actionHooks.useState(...a);
            export const useRef = (...a) => globalThis.__actionHooks.useRef(...a);
            export const useCallback = (fn) => fn;
            export const useEffect = (...a) => globalThis.__actionHooks.useEffect(...a);` }));
    } }],
});
const loaded = { exports: {} };
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), loaded, loaded.exports);
const { ActionDetailDrawer, ActionsTable, ActionReceiptPanel, AuthProvider, api } = loaded.exports;
const settle = () => new Promise((resolve) => setImmediate(resolve));

const recoveryActionId = '10000000-0000-4000-8000-000000000001';
const recoveryDigest = 'a'.repeat(64);
const recoverable = () => action(recoveryActionId, 'unknown', {
    acknowledgement_recovery: { source_digest: recoveryDigest, provider_status: 'accepted' },
});
const labelledButton = (tree, label) => walk(tree, (n) => n.type === 'button' && n.props.children === label);

test('saved acknowledgement recovery requires an explicit reason and confirmation, with no automatic request', () => {
    const h = hooks();
    try {
        const tree = h.render(ActionReceiptPanel, { action: recoverable(), onReloadReceipt: async () => null });
        const button = labelledButton(tree, 'Recover saved acknowledgement');
        assert.ok(button);
        assert.equal(button.props.disabled, true);
        assert.ok(walk(tree, (n) => n.type === 'textarea' && n.props['aria-label'] === 'Recovery reason'));
        assert.match(renderToStaticMarkup(tree), /does not send another email/);
    } finally { h.close(); }
});

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

const calendarItem = (id = 'A') => {
    const item = inspectable(id);
    item.type = 'book_meeting';
    item.email_inspection_available = false;
    item.calendar_inspection_available = true;
    item.saved_receipt.receipt.provider = 'google_calendar';
    item.saved_receipt.receipt.external_event_id = `event-${id}`;
    delete item.saved_receipt.receipt.message_id;
    return item;
};
const calendarResult = (id = 'A', outcome = 'observed_event', reason = 'exact_event_observed_only') => ({
    action_id: id, outcome, reason, observed_event_id: outcome === 'observed_event' ? `event-${id}` : null,
    observed_at: '2026-10-06T04:00:00Z',
});
const calendarButton = (tree) => walk(tree, (node) => node.type === 'button' && node.props.children === 'Inspect saved calendar event');

test('calendar exact observation is explicit, one read, and never an active booking or completed action', async () => {
    const h = hooks(), original = api.inspectAdminCalendarAction, item = calendarItem(), before = structuredClone(item);
    let reads = 0, release;
    api.inspectAdminCalendarAction = () => { reads++; return new Promise((resolve) => { release = resolve; }); };
    try {
        const tree = h.render(ActionReceiptPanel, { action: item });
        assert.equal(reads, 0);
        const click = calendarButton(tree).props.onClick;
        click(); click(); assert.equal(reads, 1);
        release({ data: calendarResult() }); await settle();
        const html = renderToStaticMarkup(h.render(ActionReceiptPanel, { action: item }));
        assert.match(html, /Exact event reference observed/);
        assert.match(html, /cancelled or deleted reference/);
        assert.match(html, /does not prove an active booking or that creation, update, or cancellation succeeded/);
        assert.match(html, /Outcome unverified/); assert.match(html, /Observed event ID: event-A/);
        assert.deepEqual(item, before);
    } finally { h.close(); api.inspectAdminCalendarAction = original; }
});

for (const [outcome, reason] of [
    ['not_observed', 'absence_is_inconclusive'], ['unavailable', 'saved_proof_unavailable'],
    ['unavailable', 'original_authorization_unavailable'], ['unavailable', 'provider_read_unavailable'],
]) {
    test(`calendar ${reason} retains uncertainty without retry`, async () => {
        const h = hooks(), original = api.inspectAdminCalendarAction, item = calendarItem();
        api.inspectAdminCalendarAction = async () => ({ data: calendarResult('A', outcome, reason) });
        try {
            calendarButton(h.render(ActionReceiptPanel, { action: item })).props.onClick(); await settle();
            const html = renderToStaticMarkup(h.render(ActionReceiptPanel, { action: item }));
            assert.match(html, /Outcome unverified/);
            assert.match(html, outcome === 'not_observed' ? /does not prove non-execution or make another calendar action safe/ : /Inspection unavailable/);
            assert.doesNotMatch(html, />Retry<|>Resolve<|>Complete<|Observed event ID/);
        } finally { h.close(); api.inspectAdminCalendarAction = original; }
    });
}

for (const bad of ['action', 'event', 'outcome', 'reason', 'inherited', 'contradictory', 'wrong_field', 'throw']) {
    test(`calendar ${bad} response cannot become an observation`, async () => {
        const h = hooks(), original = api.inspectAdminCalendarAction, item = calendarItem(), result = calendarResult();
        if (bad === 'action') result.action_id = 'B';
        if (bad === 'event') result.observed_event_id = 'different';
        if (bad === 'outcome') result.outcome = 'completed';
        if (bad === 'reason') result.reason = 'booked';
        if (bad === 'inherited') result.outcome = 'constructor';
        if (bad === 'contradictory') result.reason = 'absence_is_inconclusive';
        if (bad === 'wrong_field') { delete result.observed_event_id; result.observed_message_id = 'event-A'; }
        api.inspectAdminCalendarAction = async () => {
            if (bad === 'throw') throw new Error('private details');
            return { data: result };
        };
        try {
            calendarButton(h.render(ActionReceiptPanel, { action: item })).props.onClick(); await settle();
            const html = renderToStaticMarkup(h.render(ActionReceiptPanel, { action: item }));
            assert.match(html, /Inspection unavailable. Saved receipt unchanged/);
            assert.doesNotMatch(html, /Observed event ID|private details/);
        } finally { h.close(); api.inspectAdminCalendarAction = original; }
    });
}

for (const change of ['action', 'event', 'account', 'kind']) {
    test(`late calendar read is discarded when ${change} selection changes`, async () => {
        const h = hooks(), original = api.inspectAdminCalendarAction, item = calendarItem();
        let release;
        api.inspectAdminCalendarAction = () => new Promise((resolve) => { release = resolve; });
        try {
            calendarButton(h.render(ActionReceiptPanel, { action: item })).props.onClick();
            const replacement = change === 'action' ? calendarItem('B') : change === 'kind' ? inspectable() : structuredClone(item);
            if (change === 'event') replacement.saved_receipt.receipt.external_event_id = 'new-event';
            if (change === 'account') replacement.saved_receipt.receipt.account_row_id = 'new-row';
            h.render(ActionReceiptPanel, { action: replacement });
            release({ data: calendarResult() }); await settle();
            const html = renderToStaticMarkup(h.render(ActionReceiptPanel, { action: replacement }));
            assert.doesNotMatch(html, /Exact event reference observed|Observed event ID|Inspecting original/);
        } finally { h.close(); api.inspectAdminCalendarAction = original; }
    });
}

test('calendar unavailable, conflicting availability and mismatched receipt offer no inspection', () => {
    for (const variant of ['unavailable', 'both', 'mismatched']) {
        const h = hooks(), item = calendarItem();
        if (variant === 'unavailable') item.calendar_inspection_available = false;
        if (variant === 'both') item.email_inspection_available = true;
        if (variant === 'mismatched') item.saved_receipt.action_id = 'B';
        try {
            const tree = h.render(ActionReceiptPanel, { action: item });
            assert.equal(calendarButton(tree), null); assert.equal(inspectionButton(tree), null);
        } finally { h.close(); }
    }
});

for (const calendar of [false, true]) {
    test(`${calendar ? 'calendar' : 'Gmail'} mixed response fields never substitute the displayed reference`, async () => {
        const h = hooks(), method = calendar ? 'inspectAdminCalendarAction' : 'inspectAdminEmailAction', original = api[method];
        const item = calendar ? calendarItem() : inspectable();
        const result = calendar ? { ...calendarResult(), observed_message_id: 'foreign-reference' }
            : { ...inspection(), observed_event_id: 'foreign-reference' };
        api[method] = async () => ({ data: result });
        try {
            const tree = h.render(ActionReceiptPanel, { action: item });
            (calendar ? calendarButton(tree) : inspectionButton(tree)).props.onClick(); await settle();
            const html = renderToStaticMarkup(h.render(ActionReceiptPanel, { action: item }));
            assert.match(html, calendar ? /Observed event ID: event-A/ : /Observed message ID: message-A/);
            assert.doesNotMatch(html, /foreign-reference/);
        } finally { h.close(); api[method] = original; }
    });
}

for (const calendar of [false, true]) {
    for (const reference of [undefined, '']) {
        test(`${calendar ? 'calendar' : 'Gmail'} missing or blank matching references cannot prove an observation`, async () => {
            const h = hooks(), method = calendar ? 'inspectAdminCalendarAction' : 'inspectAdminEmailAction', original = api[method];
            const item = calendar ? calendarItem() : inspectable(), result = calendar ? calendarResult() : inspection();
            item.saved_receipt.receipt[calendar ? 'external_event_id' : 'message_id'] = reference;
            result[calendar ? 'observed_event_id' : 'observed_message_id'] = reference;
            api[method] = async () => ({ data: result });
            try {
                const tree = h.render(ActionReceiptPanel, { action: item });
                (calendar ? calendarButton(tree) : inspectionButton(tree)).props.onClick(); await settle();
                const html = renderToStaticMarkup(h.render(ActionReceiptPanel, { action: item }));
                assert.match(html, /Inspection unavailable. Saved receipt unchanged/);
                assert.doesNotMatch(html, /Observed event ID|Observed message ID|Observed at:/);
            } finally { h.close(); api[method] = original; }
        });
    }
    for (const timestamp of [{ private: 'bad value' }, [], 'invalid timestamp']) {
        test(`${calendar ? 'calendar' : 'Gmail'} invalid ${typeof timestamp} timestamp is rejected before rendering`, async () => {
            const h = hooks(), method = calendar ? 'inspectAdminCalendarAction' : 'inspectAdminEmailAction', original = api[method];
            const item = calendar ? calendarItem() : inspectable();
            const result = calendar ? calendarResult() : inspection();
            result.observed_at = timestamp;
            api[method] = async () => ({ data: result });
            try {
                const tree = h.render(ActionReceiptPanel, { action: item });
                (calendar ? calendarButton(tree) : inspectionButton(tree)).props.onClick(); await settle();
                const html = renderToStaticMarkup(h.render(ActionReceiptPanel, { action: item }));
                assert.match(html, /Inspection unavailable. Saved receipt unchanged/);
                assert.doesNotMatch(html, /Observed at:|Observed event ID|Observed message ID|bad value/);
            } finally { h.close(); api[method] = original; }
        });
    }
}

test('calendar API inspection makes one exact GET with no caller-supplied account or mutation payload', async () => {
    const original = globalThis.fetch, requests = [];
    globalThis.fetch = async (url, options) => { requests.push({ url, options }); return new Response(JSON.stringify(calendarResult())); };
    try {
        await api.inspectAdminCalendarAction('A/B');
        assert.equal(requests.length, 1);
        assert.match(requests[0].url, /\/admin\/actions\/A%2FB\/calendar-inspection$/);
        assert.equal(requests[0].options.method, 'GET'); assert.equal(requests[0].options.body, undefined);
    } finally { globalThis.fetch = original; }
});

function recoveryTest() {
    const h = hooks(), original = api.recoverSavedAcknowledgement, oldStorage = globalThis.localStorage, token = api.getToken();
    const storage = new Map();
    globalThis.localStorage = { getItem(key) { return storage.get(key) ?? null; },
        setItem(key, value) { storage.set(key, value); }, removeItem(key) { storage.delete(key); } };
    api.setToken(crypto.randomUUID());
    return { h, close() { h.close(); api.recoverSavedAcknowledgement = original; api.setToken(token); globalThis.localStorage = oldStorage; } };
}
const recordFor = (request, extra = {}) => ({
    id: '20000000-0000-4000-8000-000000000001', action_id: recoveryActionId,
    actor_id: '30000000-0000-4000-8000-000000000001', actor_role: 'platform_admin',
    request_id: request.request_id, source_digest: request.expected_source_digest, reason: request.reason,
    original_status: 'unknown', recovered_status: 'completed', provider_status: 'accepted',
    recorded_at: '2026-10-06T14:00:00Z', ...extra,
});
const recovered = (request) => {
    const item = action(recoveryActionId, 'completed', { acknowledgement_recovery_record: recordFor(request) });
    item.saved_receipt.success = true; item.saved_receipt.confirmation_allowed = true;
    return item;
};
async function confirmRecovery(h, props, reason = 'Reviewed the original saved acknowledgement.') {
    let tree = h.render(ActionReceiptPanel, props);
    walk(tree, (n) => n.type === 'textarea').props.onChange({ target: { value: reason } });
    tree = h.render(ActionReceiptPanel, props);
    labelledButton(tree, 'Recover saved acknowledgement').props.onClick();
    tree = h.render(ActionReceiptPanel, props);
    labelledButton(tree, 'Confirm saved acknowledgement recovery').props.onClick();
    await settle();
}

for (const reason of ['', '   ', 'line\nbreak', '\u007f', 'x'.repeat(501)]) {
    test(`recovery rejects invalid reason ${JSON.stringify(reason).slice(0, 35)}`, () => {
        const { h, close } = recoveryTest(), props = { action: recoverable(), onReloadReceipt: async () => null };
        try {
            walk(h.render(ActionReceiptPanel, props), (n) => n.type === 'textarea').props.onChange({ target: { value: reason } });
            assert.equal(labelledButton(h.render(ActionReceiptPanel, props), 'Recover saved acknowledgement').props.disabled, true);
        } finally { close(); }
    });
}

for (const variant of ['missing', 'digest', 'enum', 'wrong-type', 'wrong-status', 'wrong-receipt', 'no-reload']) {
    test(`recovery availability rejects ${variant}`, () => {
        const { h, close } = recoveryTest(), item = recoverable(), props = { action: item, onReloadReceipt: async () => null };
        if (variant === 'missing') delete item.acknowledgement_recovery;
        if (variant === 'digest') item.acknowledgement_recovery.source_digest = 'A'.repeat(64);
        if (variant === 'enum') item.acknowledgement_recovery.provider_status = 'delivered';
        if (variant === 'wrong-type') item.type = 'book_meeting';
        if (variant === 'wrong-status') item.status = item.saved_receipt.status = 'completed';
        if (variant === 'wrong-receipt') item.saved_receipt.action_id = 'other';
        if (variant === 'no-reload') delete props.onReloadReceipt;
        try { assert.equal(labelledButton(h.render(ActionReceiptPanel, props), 'Recover saved acknowledgement'), null); }
        finally { close(); }
    });
}

for (const error of [401, 403, 404, 409, 422, 503, 'network', 'throw']) {
    test(`recovery ${error} preserves the exact pending review and gates replay`, async () => {
        const { h, close } = recoveryTest(), calls = [];
        const props = { action: recoverable(), onReloadReceipt: async () => recoverable() };
        api.recoverSavedAcknowledgement = async (id, body) => {
            calls.push({ id, body: structuredClone(body) });
            if (error === 'throw') throw new Error('private exception');
            return { error: { status: typeof error === 'number' ? error : undefined, message: 'private error', code: 'UNKNOWN_ERROR' } };
        };
        try {
            await confirmRecovery(h, props, '  Same original review.  ');
            const first = calls[0]; assert.equal(first.body.reason, 'Same original review.');
            let tree = h.render(ActionReceiptPanel, props);
            assert.doesNotMatch(renderToStaticMarkup(tree), /private error|private exception/);
            const blocked = [401, 403, 404, 409, 422].includes(error);
            assert.equal(labelledButton(tree, 'Retry same recovery request').props.disabled, blocked);
            if (blocked) {
                labelledButton(tree, 'Retry same recovery request').props.onClick(); await settle(); assert.equal(calls.length, 1);
                labelledButton(tree, 'Reload saved receipt').props.onClick(); await settle();
                tree = h.render(ActionReceiptPanel, props);
            }
            labelledButton(tree, 'Retry same recovery request').props.onClick(); await settle();
            assert.deepEqual(calls[1], first);
        } finally { close(); }
    });
}

for (const [field, value] of [
    ['id', 'not-uuid'], ['action_id', '40000000-0000-4000-8000-000000000001'], ['actor_id', {}], ['actor_role', 'tenant_admin'],
    ['request_id', '40000000-0000-4000-8000-000000000001'], ['source_digest', 'b'.repeat(64)], ['reason', 'other'],
    ['provider_status', 'provider_accepted'], ['original_status', 'failed'], ['recovered_status', 'delivered'],
    ['recorded_at', {}], ['recorded_at', []], ['recorded_at', 'invalid'],
]) {
    test(`malformed recovery ${field} ${JSON.stringify(value)} remains unconfirmed without a detail refresh`, async () => {
        const { h, close } = recoveryTest(); let reads = 0;
        const props = { action: recoverable(), onReloadReceipt: async () => { reads++; return null; } };
        api.recoverSavedAcknowledgement = async (_, body) => ({ data: recordFor(body, { [field]: value }) });
        try {
            await confirmRecovery(h, props);
            const html = renderToStaticMarkup(h.render(ActionReceiptPanel, props));
            assert.match(html, /Recovery is unconfirmed/); assert.doesNotMatch(html, /Saved acknowledgement recovered/);
            assert.equal(reads, 0);
        } finally { close(); }
    });
}

test('double recovery confirmation sends once and successful refresh displays only the safe saved summary', async () => {
    const { h, close } = recoveryTest(); let release, calls = 0, body;
    const props = { action: recoverable(), onReloadReceipt: async () => (props.action = recovered(body)) };
    api.recoverSavedAcknowledgement = (_, request) => { calls++; body = request; return new Promise((resolve) => { release = resolve; }); };
    try {
        await confirmRecovery(h, props);
        labelledButton(h.render(ActionReceiptPanel, props), 'Retry same recovery request').props.onClick();
        assert.equal(calls, 1);
        release({ data: { ...recordFor(body), private_payload: 'never display this' } }); await settle();
        h.render(ActionReceiptPanel, props);
        const html = renderToStaticMarkup(h.render(ActionReceiptPanel, props));
        assert.match(html, /Saved acknowledgement recovered/); assert.match(html, /Reviewed by platform admin/);
        assert.match(html, /unknown → completed/); assert.doesNotMatch(html, /never display this|Retry same recovery request/);
    } finally { close(); }
});

test('unconfirmed recovery survives panel reopen and reuses exact request without automatic replay', async () => {
    const { h, close } = recoveryTest(), calls = [], props = { action: recoverable(), onReloadReceipt: async () => null };
    api.recoverSavedAcknowledgement = async (_, body) => { calls.push(structuredClone(body)); return { error: { status: 503 } }; };
    const reopened = hooks();
    try {
        await confirmRecovery(h, props); h.close();
        const tree = reopened.render(ActionReceiptPanel, props);
        assert.equal(calls.length, 1); assert.equal(walk(tree, (n) => n.type === 'textarea'), null);
        labelledButton(tree, 'Retry same recovery request').props.onClick(); await settle();
        assert.deepEqual(calls[1], calls[0]);
    } finally { reopened.close(); close(); }
});

test('late recovery result after selection change cannot refresh or change the selected action', async () => {
    const { h, close } = recoveryTest(); let release, body, reads = 0;
    api.recoverSavedAcknowledgement = (_, request) => { body = request; return new Promise((resolve) => { release = resolve; }); };
    const props = { action: recoverable(), onReloadReceipt: async () => { reads++; return recovered(body); } };
    try {
        await confirmRecovery(h, props);
        const other = { action: action('B'), onReloadReceipt: props.onReloadReceipt };
        h.render(ActionReceiptPanel, other);
        release({ data: recordFor(body) }); await settle();
        const html = renderToStaticMarkup(h.render(ActionReceiptPanel, other));
        assert.equal(reads, 0); assert.doesNotMatch(html, /original-10000000|Saved acknowledgement recovered|Review reason:/);
    } finally { close(); }
});

test('auth change discards previous review state and rejects an in-flight old-session response', async () => {
    const { h, close } = recoveryTest(); let release, body, reads = 0;
    api.recoverSavedAcknowledgement = (_, request) => { body = request; return new Promise((resolve) => { release = resolve; }); };
    const props = { action: recoverable(), onReloadReceipt: async () => { reads++; return recovered(body); } };
    try {
        await confirmRecovery(h, props, 'Prior admin private review');
        api.setToken('different-synthetic-session');
        release({ data: recordFor(body) }); await settle();
        h.render(ActionReceiptPanel, props);
        const html = renderToStaticMarkup(h.render(ActionReceiptPanel, props));
        assert.equal(reads, 0); assert.doesNotMatch(html, /Prior admin private review|Retry same recovery request|Saved acknowledgement recovered/);
    } finally { close(); }
});

test('drawer reload updates header and receipt together and ignores a later foreign selection', async () => {
    const { h, close } = recoveryTest(), original = api.getActionDetail; let release;
    api.getActionDetail = async () => ({ data: recoverable() });
    const props = { actionId: recoveryActionId, onClose() {} };
    try {
        h.render(ActionDetailDrawer, props); await settle();
        let tree = h.render(ActionDetailDrawer, props);
        const reload = walk(tree, (n) => n.type === ActionReceiptPanel).props.onReloadReceipt;
        api.getActionDetail = () => new Promise((resolve) => { release = resolve; });
        const pending = reload();
        assert.match(renderToStaticMarkup(h.render(ActionDetailDrawer, props)), />Unknown</);
        const saved = recovered({ request_id: '40000000-0000-4000-8000-000000000001', expected_source_digest: recoveryDigest, reason: 'Reviewed.' });
        release({ data: saved }); await pending;
        tree = h.render(ActionDetailDrawer, props);
        const html = renderToStaticMarkup(tree);
        assert.match(html, />Completed</); assert.match(html, /Acknowledgement recorded/); assert.match(html, /Saved acknowledgement recovered/);
        const staleReload = walk(tree, (n) => n.type === ActionReceiptPanel).props.onReloadReceipt;
        const stale = staleReload();
        api.getActionDetail = async () => ({ data: action('B') });
        h.render(ActionDetailDrawer, { ...props, actionId: 'B' }); await settle();
        release({ data: saved }); assert.equal(await stale, null);
        assert.match(renderToStaticMarkup(h.render(ActionDetailDrawer, { ...props, actionId: 'B' })), /Synthetic tenant B/);
    } finally { api.getActionDetail = original; close(); }
});

test('recovery API preserves exact POST fields and actual HTTP status', async () => {
    const original = globalThis.fetch, requests = [];
    const body = { request_id: '40000000-0000-4000-8000-000000000001', expected_source_digest: recoveryDigest, reason: 'Reviewed.' };
    globalThis.fetch = async (url, options) => { requests.push({ url, options }); return new Response(JSON.stringify({ detail: 'Conflict' }), { status: 409 }); };
    try {
        const response = await api.recoverSavedAcknowledgement('A/B', body);
        assert.equal(response.error.status, 409); assert.equal(response.error.message, 'Conflict');
        assert.match(requests[0].url, /\/admin\/actions\/A%2FB\/recover-acknowledgement$/);
        assert.equal(requests[0].options.method, 'POST'); assert.deepEqual(JSON.parse(requests[0].options.body), body);
        assert.equal(requests.length, 1);
    } finally { globalThis.fetch = original; }
});

test('changed saved evidence holds the original review without replacing its request ID', async () => {
    const { h, close } = recoveryTest(), calls = [], props = { action: recoverable(), onReloadReceipt: async () => props.action };
    api.recoverSavedAcknowledgement = async (_, body) => { calls.push(structuredClone(body)); return { error: { status: 503 } }; };
    try {
        await confirmRecovery(h, props);
        props.action = { ...props.action, acknowledgement_recovery: { source_digest: 'b'.repeat(64), provider_status: 'accepted' } };
        h.render(ActionReceiptPanel, props);
        labelledButton(h.render(ActionReceiptPanel, props), 'Retry same recovery request').props.onClick(); await settle();
        assert.equal(calls.length, 1);
        let tree = h.render(ActionReceiptPanel, props);
        assert.equal(labelledButton(tree, 'Retry same recovery request').props.disabled, true);
        labelledButton(tree, 'Reload saved receipt').props.onClick(); await settle();
        tree = h.render(ActionReceiptPanel, props);
        assert.match(renderToStaticMarkup(tree), /previous request remains held/);
        assert.equal(labelledButton(tree, 'Retry same recovery request').props.disabled, true);
        assert.equal(walk(tree, (n) => n.type === 'textarea'), null);
    } finally { close(); }
});

for (const refresh of ['throws', 'missing', 'foreign', 'old']) {
    test(`valid recovery response with ${refresh} detail retains uncertainty and original request`, async () => {
        const { h, close } = recoveryTest(), calls = [];
        const props = { action: recoverable(), onReloadReceipt: async () => {
            if (refresh === 'throws') throw new Error('private');
            return refresh === 'missing' ? null : refresh === 'foreign' ? action('B') : recoverable();
        } };
        api.recoverSavedAcknowledgement = async (_, body) => { calls.push(structuredClone(body)); return { data: recordFor(body) }; };
        try {
            await confirmRecovery(h, props);
            let tree = h.render(ActionReceiptPanel, props);
            assert.match(renderToStaticMarkup(tree), /unconfirmed/); assert.doesNotMatch(renderToStaticMarkup(tree), /Saved acknowledgement recovered|private/);
            labelledButton(tree, 'Retry same recovery request').props.onClick(); await settle();
            assert.deepEqual(calls[1], calls[0]);
            tree = h.render(ActionReceiptPanel, props);
            assert.equal(walk(tree, (n) => n.type === 'textarea'), null);
        } finally { close(); }
    });
}

for (const variant of ['wrong-action', 'invalid-time', 'object-reason', 'mismatched-receipt', 'unconfirmed-receipt']) {
    test(`saved recovery summary rejects ${variant}`, () => {
        const { h, close } = recoveryTest();
        const item = recovered({ request_id: '40000000-0000-4000-8000-000000000001', expected_source_digest: recoveryDigest, reason: 'Private reviewed reason' });
        if (variant === 'wrong-action') item.acknowledgement_recovery_record.action_id = '40000000-0000-4000-8000-000000000001';
        if (variant === 'invalid-time') item.acknowledgement_recovery_record.recorded_at = {};
        if (variant === 'object-reason') item.acknowledgement_recovery_record.reason = {};
        if (variant === 'mismatched-receipt') item.saved_receipt.action_id = 'B';
        if (variant === 'unconfirmed-receipt') item.saved_receipt.confirmation_allowed = false;
        try {
            const html = renderToStaticMarkup(h.render(ActionReceiptPanel, { action: item }));
            assert.doesNotMatch(html, /Saved acknowledgement recovered|Private reviewed reason/);
        } finally { close(); }
    });
}

test('pending review capacity refuses new requests without evicting an uncertain original', async () => {
    const { h, close } = recoveryTest(), calls = [], props = { action: recoverable(), onReloadReceipt: async () => null };
    api.recoverSavedAcknowledgement = async (id, body) => { calls.push({ id, body: structuredClone(body) }); return { error: { status: 503 } }; };
    try {
        for (let i = 1; i <= 33; i++) {
            const id = `10000000-0000-4000-8000-${String(i).padStart(12, '0')}`;
            props.action = { ...recoverable(), id, saved_receipt: { ...recoverable().saved_receipt, action_id: id } };
            h.render(ActionReceiptPanel, props);
            await confirmRecovery(h, props);
        }
        assert.equal(calls.length, 32);
        assert.match(renderToStaticMarkup(h.render(ActionReceiptPanel, props)), /Resolve an existing pending recovery/);
        props.action = recoverable(); h.render(ActionReceiptPanel, props);
        labelledButton(h.render(ActionReceiptPanel, props), 'Retry same recovery request').props.onClick(); await settle();
        assert.deepEqual(calls[32], calls[0]);
    } finally { close(); }
});

test('actual AuthProvider login and logout update API authentication and invalidate pending recovery', async () => {
    const { h, close } = recoveryTest(), auth = hooks(), originalFetch = globalThis.fetch, calls = [];
    let release, body, reads = 0;
    globalThis.fetch = async (url, options) => {
        calls.push({ url, options });
        if (url.endsWith('/auth/login')) return new Response(JSON.stringify({ access_token: 'new-admin-session', user_id: 'new-admin', role: 'platform_admin', email: 'admin@example.invalid' }));
        if (url.endsWith('/auth/me')) return new Response(JSON.stringify({ id: 'admin', email: 'admin@example.invalid', role: 'platform_admin' }));
        return new Response(JSON.stringify({}));
    };
    api.recoverSavedAcknowledgement = (_, request) => { body = request; return new Promise((resolve) => { release = resolve; }); };
    const props = { action: recoverable(), onReloadReceipt: async () => { reads++; return recovered(body); } };
    try {
        auth.render(AuthProvider, {}); await settle();
        await confirmRecovery(h, props, 'Previous actor private review');
        const oldGeneration = api.getAuthGeneration();
        await auth.render(AuthProvider, {}).props.value.logout();
        assert.equal(api.getToken(), null); assert.equal(localStorage.getItem('admin_token'), null);
        assert.ok(api.getAuthGeneration() > oldGeneration);
        assert.equal(await auth.render(AuthProvider, {}).props.value.login('admin@example.invalid', 'synthetic'), true);
        assert.equal(api.getToken(), 'new-admin-session');
        await api.getActionDetail(recoveryActionId);
        assert.equal(calls.at(-1).options.headers.Authorization, 'Bearer new-admin-session');
        release({ data: recordFor(body) }); await settle();
        h.render(ActionReceiptPanel, props);
        const html = renderToStaticMarkup(h.render(ActionReceiptPanel, props));
        assert.equal(reads, 0); assert.doesNotMatch(html, /Previous actor private review|Retry same recovery request/);
    } finally { globalThis.fetch = originalFetch; auth.close(); close(); }
});

for (const variant of ['stored', 'absent', 'rejected']) {
    test(`actual AuthProvider initial ${variant} token synchronizes the API session`, async () => {
        const { close } = recoveryTest(), auth = hooks(), originalFetch = globalThis.fetch, sent = [];
        if (variant === 'absent') localStorage.removeItem('admin_token');
        else localStorage.setItem('admin_token', 'stored-admin-session');
        globalThis.fetch = async (_, options) => {
            sent.push(options.headers.Authorization);
            return new Response(JSON.stringify(variant === 'rejected' ? { detail: 'Unauthorized' }
                : { id: 'admin', email: 'admin@example.invalid', role: 'platform_admin' }), { status: variant === 'rejected' ? 401 : 200 });
        };
        try {
            auth.render(AuthProvider, {}); await settle();
            assert.equal(api.getToken(), variant === 'stored' ? 'stored-admin-session' : null);
            assert.deepEqual(sent, variant === 'absent' ? [] : ['Bearer stored-admin-session']);
            assert.equal(auth.render(AuthProvider, {}).props.value.isAuthenticated, variant === 'stored');
        } finally { globalThis.fetch = originalFetch; auth.close(); close(); }
    });
}

test('stale drawer and panel reload callbacks cannot adopt a newer session before effect cleanup', async () => {
    const { h, close } = recoveryTest(), panel = hooks(), original = api.getActionDetail; let reads = 0, panelReads = 0;
    api.getActionDetail = async () => { reads++; return { data: recoverable() }; };
    const props = { actionId: recoveryActionId, onClose() {} };
    try {
        h.render(ActionDetailDrawer, props); await settle();
        const drawerReload = walk(h.render(ActionDetailDrawer, props), (n) => n.type === ActionReceiptPanel).props.onReloadReceipt;
        const panelReload = labelledButton(panel.render(ActionReceiptPanel, { action: recoverable(), onReloadReceipt: async () => { panelReads++; return recoverable(); } }), 'Reload saved receipt').props.onClick;
        api.setToken('other-session-before-render');
        assert.equal(await drawerReload(), null);
        panelReload(); await settle();
        assert.equal(reads, 1); assert.equal(panelReads, 0);
    } finally { api.getActionDetail = original; panel.close(); close(); }
});

for (const variant of ['same-source', 'different-source', 'malformed', 'unconfirmed']) {
    test(`another review's ${variant} saved recovery retires only a proven resolved pending entry`, async () => {
        const { h, close } = recoveryTest(), props = { action: recoverable(), onReloadReceipt: async () => null };
        let body;
        api.recoverSavedAcknowledgement = async (_, request) => { body = request; return { error: { status: 503 } }; };
        try {
            await confirmRecovery(h, props, 'Original pending review');
            const other = recovered(body);
            Object.assign(other.acknowledgement_recovery_record, {
                actor_id: '40000000-0000-4000-8000-000000000001', request_id: '50000000-0000-4000-8000-000000000001', reason: 'Another admin review',
            });
            if (variant === 'different-source') other.acknowledgement_recovery_record.source_digest = 'b'.repeat(64);
            if (variant === 'malformed') other.acknowledgement_recovery_record.actor_id = 'bad';
            if (variant === 'unconfirmed') other.saved_receipt.confirmation_allowed = false;
            props.onReloadReceipt = async () => other;
            labelledButton(h.render(ActionReceiptPanel, props), 'Reload saved receipt').props.onClick(); await settle();
            props.action = other; h.render(ActionReceiptPanel, props);
            const html = renderToStaticMarkup(h.render(ActionReceiptPanel, props));
            if (variant === 'same-source') {
                assert.doesNotMatch(html, /Original pending review|Retry same recovery request/);
                assert.match(html, /Another admin review/);
            } else {
                assert.match(html, /Original pending review|Retry same recovery request/);
            }
        } finally { close(); }
    });
}
