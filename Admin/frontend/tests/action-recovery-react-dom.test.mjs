import assert from 'node:assert/strict';
import test, { after, afterEach, beforeEach } from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import net from 'node:net';
import { build } from 'esbuild';
import { JSDOM } from 'jsdom';

// Real React renderer/effects/event dispatch in an emulated DOM. This is not a
// browser, layout, provider, or customer acceptance test. No hooks are replaced.
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://admin.example.invalid/' });
const originalGlobals = new Map();
for (const [key, value] of Object.entries({ window: dom.window, document: dom.window.document,
    navigator: dom.window.navigator, localStorage: dom.window.localStorage,
    HTMLElement: dom.window.HTMLElement, Event: dom.window.Event, IS_REACT_ACT_ENVIRONMENT: true })) {
    originalGlobals.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { value, writable: true, configurable: true });
}
// DOM must exist before React DOM initializes its event support.
const { createElement: el, StrictMode, act, useState } = await import('react');
const { createRoot } = await import('react-dom/client');
const bundle = await build({
    stdin: { contents: `export { ActionDetailDrawer } from './src/components/ActionDetailDrawer';
        export { AuthProvider, useAuth } from './src/lib/auth'; export { api } from './src/lib/api';`,
        resolveDir: fileURLToPath(new URL('..', import.meta.url)) },
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
    define: { 'import.meta.env': '{}' }, jsx: 'automatic',
});
const loaded = { exports: {} };
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), loaded, loaded.exports);
const { ActionDetailDrawer, AuthProvider, useAuth, api } = loaded.exports;
const originalFetch = globalThis.fetch, originalConnect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function () { throw new Error('Network is forbidden in React DOM unit controls'); };
const A = '10000000-0000-4000-8000-000000000001';
const B = '10000000-0000-4000-8000-000000000002';
const DIGEST = 'a'.repeat(64);
const REASON = 'Reviewed the original saved acknowledgement.';
const item = (id = A) => ({
    id, type: 'send_email', tenant_name: `Tenant ${id}`, status: 'unknown',
    created_at: '2026-10-06T12:00:00Z', duration_ms: null, is_cancellable: false, is_retryable: false,
    acknowledgement_recovery: { source_digest: DIGEST, provider_status: 'accepted' },
    saved_receipt: { action_id: id, status: 'unknown', success: false, confirmation_allowed: false,
        receipt: { provider: 'gmail', message_id: `message-${id}` } },
});
const record = (request, extra = {}) => ({
    id: '20000000-0000-4000-8000-000000000001', action_id: A,
    actor_id: '30000000-0000-4000-8000-000000000001', actor_role: 'platform_admin',
    request_id: request.request_id, source_digest: request.expected_source_digest, reason: request.reason,
    original_status: 'unknown', recovered_status: 'completed', provider_status: 'accepted',
    recorded_at: '2026-10-06T14:00:00Z', ...extra,
});
const completed = (request, extra = {}) => {
    const action = item();
    action.status = 'completed'; action.acknowledgement_recovery = null;
    action.saved_receipt.status = 'completed'; action.saved_receipt.success = true; action.saved_receipt.confirmation_allowed = true;
    action.acknowledgement_recovery_record = record(request, extra);
    return action;
};
const response = (data, status = 200) => new Response(JSON.stringify(data), { status });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
let root, host, requests, route;
beforeEach(() => {
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    api.setToken(`synthetic-${crypto.randomUUID()}`);
    requests = [];
    route = () => { throw new Error('Unexpected synthetic API request'); };
    globalThis.fetch = async (url, options) => {
        assert.match(String(url), /^http:\/\/localhost:8000\/api\/v1\//);
        const request = { path: new URL(url).pathname, method: options.method,
            authorization: options.headers.Authorization, body: options.body ? JSON.parse(options.body) : undefined };
        requests.push(request);
        return route(request);
    };
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); api.setToken(null); });
after(() => {
    globalThis.fetch = originalFetch; net.Socket.prototype.connect = originalConnect; dom.window.close();
    for (const [key, descriptor] of originalGlobals) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
    }
});
const button = (label) => {
    const match = [...host.querySelectorAll('button')].find((node) => node.textContent === label);
    assert.ok(match, `Missing button: ${label}`); return match;
};
const click = async (label) => { await act(async () => button(label).click()); };
const draw = async (id = A) => {
    await act(async () => root.render(el(StrictMode, null, el(ActionDetailDrawer, { actionId: id, onClose() {} }))));
};
async function confirm(reason = REASON) {
    const textarea = host.querySelector('textarea[aria-label="Recovery reason"]'); assert.ok(textarea);
    await act(async () => {
        Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(textarea, reason);
        textarea.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    });
    assert.equal(button('Recover saved acknowledgement').disabled, false);
    await click('Recover saved acknowledgement');
    await click('Confirm saved acknowledgement recovery');
}
const posts = () => requests.filter((request) => request.method === 'POST' && request.path.endsWith('/recover-acknowledgement'));
const detailReads = () => requests.filter((request) => /\/admin\/actions\/[^/]+$/.test(request.path));

test('real StrictMode lifecycle waits for coherent parent detail after accepted POST', async () => {
    const post = deferred(), reload = deferred(); let request;
    route = (call) => {
        if (call.method === 'POST') { request = call.body; return post.promise; }
        return request ? reload.promise : response(item());
    };
    await draw();
    assert.equal(posts().length, 0); assert.match(host.textContent, /Outcome unverified/);
    await confirm();
    await act(async () => { button('Retry same recovery request').click(); button('Retry same recovery request').click(); });
    assert.equal(posts().length, 1);
    await act(async () => post.resolve(response(record(request))));
    assert.match(host.textContent, /Outcome unverified/);
    assert.doesNotMatch(host.textContent, /Saved acknowledgement recovered/);
    assert.equal(host.querySelector('.status-badge')?.textContent ?? host.querySelector('.action-status-badge')?.textContent, 'Unknown');
    await act(async () => reload.resolve(response(completed(request))));
    assert.match(host.textContent, /Saved acknowledgement recovered/);
    assert.match(host.textContent, /Acknowledgement recorded/);
    assert.doesNotMatch(host.textContent, /Retry same recovery request|Outcome unverified/);
    assert.equal(posts().length, 1);
});

test('real DOM close and reopen preserves uncertain request without automatic replay', async () => {
    route = (call) => call.method === 'POST' ? response({ detail: 'Unconfirmed' }, 503) : response(item());
    await draw(); await confirm();
    const original = posts()[0].body;
    await draw(null); assert.equal(host.textContent, '');
    await draw();
    assert.equal(posts().length, 1); assert.equal(host.querySelector('textarea'), null);
    assert.match(host.textContent, new RegExp(REASON.replace('.', '\\.')));
    await click('Retry same recovery request');
    assert.deepEqual(posts()[1].body, original);
});

test('actual unmount remount keeps same pending review within authentication generation', async () => {
    route = (call) => call.method === 'POST' ? response({ detail: 'Unconfirmed' }, 503) : response(item());
    await draw(); await confirm(); const original = posts()[0].body;
    await act(async () => root.unmount()); root = createRoot(host);
    await draw(); assert.equal(posts().length, 1);
    await click('Retry same recovery request'); assert.deepEqual(posts()[1].body, original);
});

test('real selection change discards late successful A response and leaves B untouched', async () => {
    const pending = deferred();
    route = (call) => call.method === 'POST' ? pending.promise : response(item(call.path.endsWith(B) ? B : A));
    await draw(); await confirm(); const request = posts()[0].body;
    await draw(B); const readCount = detailReads().length;
    await act(async () => pending.resolve(response(record(request))));
    assert.equal(detailReads().length, readCount);
    assert.match(host.textContent, new RegExp(`Tenant ${B}`));
    assert.doesNotMatch(host.textContent, /Saved acknowledgement recovered|Reviewed the original/);
});

test('real batched drawer refresh retires another validated review without claiming this request', async () => {
    let saved = item();
    route = (call) => call.method === 'POST' ? response({ detail: 'Unconfirmed' }, 503) : response(saved);
    await draw(); await confirm();
    saved = completed(posts()[0].body, { actor_id: '40000000-0000-4000-8000-000000000001',
        request_id: '50000000-0000-4000-8000-000000000001', reason: 'Different recorded review' });
    await click('Reload saved receipt');
    assert.match(host.textContent, /Different recorded review/);
    assert.doesNotMatch(host.textContent, /Retry same recovery request|Reviewed the original saved acknowledgement/);
    await draw(null); await draw();
    assert.doesNotMatch(host.textContent, /Retry same recovery request/); assert.equal(posts().length, 1);
});

test('real error reload remains disabled until explicit fresh receipt and reuses the exact request', async () => {
    route = (call) => call.method === 'POST' ? response({ detail: 'Conflict' }, 409) : response(item());
    await draw(); await confirm(); const original = posts()[0].body;
    assert.equal(button('Retry same recovery request').disabled, true);
    await click('Retry same recovery request'); assert.equal(posts().length, 1);
    await click('Reload saved receipt');
    assert.equal(button('Retry same recovery request').disabled, false);
    await click('Retry same recovery request'); assert.deepEqual(posts()[1].body, original);
});

test('real successful POST with failed detail refresh remains unknown and replays exact body', async () => {
    let submitted = false;
    route = (call) => {
        if (call.method === 'POST') { submitted = true; return response(record(call.body)); }
        return submitted ? response({ detail: 'Unavailable' }, 503) : response(item());
    };
    await draw(); await confirm(); const original = posts()[0].body;
    assert.match(host.textContent, /Outcome unverified/); assert.doesNotMatch(host.textContent, /Saved acknowledgement recovered/);
    await click('Retry same recovery request'); assert.deepEqual(posts()[1].body, original);
});

test('mounted old-session DOM reload button cannot adopt a newer API session before rerender', async () => {
    route = () => response(item());
    await draw();
    const oldButton = button('Reload saved receipt'), reads = detailReads().length;
    api.setToken('new-session-before-rerender');
    await act(async () => oldButton.click());
    assert.equal(detailReads().length, reads);
    await draw();
    assert.ok(detailReads().length > reads);
    assert.equal(detailReads().at(-1).authorization, 'Bearer new-session-before-rerender');
});

function AuthScreen() {
    const auth = useAuth();
    const [visible, setVisible] = useState(true);
    return el('div', null,
        el('p', { 'data-testid': 'principal' }, auth.user?.id ?? 'signed out'),
        el('button', { onClick: () => void auth.logout() }, 'Sign out test session'),
        el('button', { onClick: () => void auth.login('admin@example.invalid', 'synthetic-password') }, 'Sign in new test session'),
        el('button', { onClick: () => setVisible(!visible) }, 'Toggle drawer'),
        auth.isAuthenticated && visible ? el(ActionDetailDrawer, { actionId: A, onClose: () => setVisible(false) }) : null);
}

test('actual AuthProvider DOM logout/login discards old review and old in-flight response', async () => {
    const pending = deferred();
    route = (call) => {
        if (call.path.endsWith('/auth/me')) return response({ id: 'old-admin', email: 'admin@example.invalid', role: 'platform_admin' });
        if (call.path.endsWith('/auth/logout')) return response({});
        if (call.path.endsWith('/auth/login')) return response({ access_token: 'new-synthetic-token', user_id: 'new-admin', email: 'admin@example.invalid', role: 'platform_admin' });
        if (call.method === 'POST') return pending.promise;
        return response(item());
    };
    await act(async () => root.render(el(StrictMode, null, el(AuthProvider, null, el(AuthScreen)))));
    assert.equal(host.querySelector('[data-testid="principal"]').textContent, 'old-admin');
    await confirm('Old admin private review'); const original = posts()[0].body;
    await click('Sign out test session'); assert.equal(api.getToken(), null);
    assert.doesNotMatch(host.textContent, /Old admin private review/);
    await click('Sign in new test session');
    assert.equal(host.querySelector('[data-testid="principal"]').textContent, 'new-admin');
    assert.equal(detailReads().at(-1).authorization, 'Bearer new-synthetic-token');
    const reads = detailReads().length;
    await act(async () => pending.resolve(response(record(original))));
    assert.equal(detailReads().length, reads);
    assert.doesNotMatch(host.textContent, /Old admin private review|Retry same recovery request|Saved acknowledgement recovered/);
    assert.ok(host.querySelector('textarea'));
});

test('actual AuthProvider cannot restore an old identity when initial verification finishes after logout', async () => {
    const verification = deferred();
    route = (call) => {
        if (call.path.endsWith('/auth/me')) return verification.promise.then((data) => response(data));
        if (call.path.endsWith('/auth/logout')) return response({});
        return response(item());
    };
    await act(async () => root.render(el(StrictMode, null, el(AuthProvider, null, el(AuthScreen)))));
    await click('Sign out test session');
    assert.equal(api.getToken(), null);
    await act(async () => verification.resolve({ id: 'old-admin', email: 'admin@example.invalid', role: 'platform_admin' }));
    assert.equal(host.querySelector('[data-testid="principal"]').textContent, 'signed out');
    assert.equal(detailReads().length, 0);
});

test('old failed verification cannot clear a newer successful actual AuthProvider login', async () => {
    const verification = deferred();
    route = (call) => {
        if (call.path.endsWith('/auth/me')) return verification.promise.then(() => response({ detail: 'Old session rejected' }, 401));
        if (call.path.endsWith('/auth/login')) return response({ access_token: 'new-login-token', user_id: 'new-admin', email: 'admin@example.invalid', role: 'platform_admin' });
        return response(item());
    };
    await act(async () => root.render(el(StrictMode, null, el(AuthProvider, null, el(AuthScreen)))));
    await click('Sign in new test session');
    assert.equal(api.getToken(), 'new-login-token');
    await act(async () => verification.resolve());
    assert.equal(api.getToken(), 'new-login-token');
    assert.equal(host.querySelector('[data-testid="principal"]').textContent, 'new-admin');
});

test('late actual AuthProvider logout completion cannot erase a newer login', async () => {
    const logout = deferred();
    route = (call) => {
        if (call.path.endsWith('/auth/me')) return response({ id: 'old-admin', email: 'admin@example.invalid', role: 'platform_admin' });
        if (call.path.endsWith('/auth/logout')) return logout.promise;
        if (call.path.endsWith('/auth/login')) return response({ access_token: 'new-login-token', user_id: 'new-admin', email: 'admin@example.invalid', role: 'platform_admin' });
        return response(item());
    };
    await act(async () => root.render(el(StrictMode, null, el(AuthProvider, null, el(AuthScreen)))));
    await click('Sign out test session');
    await click('Sign in new test session');
    assert.equal(api.getToken(), 'new-login-token');
    await act(async () => logout.resolve(response({})));
    assert.equal(api.getToken(), 'new-login-token');
    assert.equal(host.querySelector('[data-testid="principal"]').textContent, 'new-admin');
});
