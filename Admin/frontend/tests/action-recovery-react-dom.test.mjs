import assert from 'node:assert/strict';
import test, { after, afterEach, beforeEach } from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import net from 'node:net';
import { build } from 'esbuild';
import { JSDOM } from 'jsdom';
import { installSessionLocks } from './session-lock-fixture.mjs';

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
    installSessionLocks(dom.window.navigator);
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    localStorage.removeItem('talky.admin.logout.pending');
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
// Retain the real React callback: detached DOM clicks alone do not reach the
// React event delegate and would miss stale-handler dispatch regressions.
const retainedClick = node => node[Object.keys(node).find(key => key.startsWith('__reactProps$'))].onClick;
const status = () => host.querySelector('.status-badge')?.textContent ?? host.querySelector('.action-status-badge')?.textContent;
function assertRecordedRecovery(request) {
    const summary = host.querySelector('[aria-label="Saved acknowledgement recovery"]');
    assert.ok(summary, 'The exact committed recovery record must survive a failed detail observation');
    assert.match(summary.textContent, /Saved acknowledgement recovered/);
    assert.ok(summary.textContent.includes(request.reason));
    assert.ok(summary.textContent.includes('30000000-0000-4000-8000-000000000001'));
    assert.ok(summary.textContent.includes('2026-10-06T14:00:00Z'));
    assert.match(summary.textContent, /accepted/);
    assert.match(summary.textContent, /does not establish delivery or payload correctness/i);
    assert.doesNotMatch(host.textContent, /Recovery is unconfirmed|refreshed receipt is unconfirmed|private response payload/i);
    for (const label of ['Retry same recovery request', 'Recover saved acknowledgement', 'Confirm saved acknowledgement recovery']) {
        const control = [...host.querySelectorAll('button')].find(node => node.textContent === label);
        assert.ok(!control || control.disabled, `A committed recovery cannot offer ${label}`);
    }
}
async function confirm(reason = REASON) {
    const textarea = host.querySelector('textarea[aria-label="Recovery reason"]'); assert.ok(textarea);
    await act(async () => {
        Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(textarea, reason);
        textarea.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    });
    assert.equal(button('Recover saved acknowledgement').disabled, false);
    await click('Recover saved acknowledgement');
    const retained = retainedClick(button('Confirm saved acknowledgement recovery'));
    await click('Confirm saved acknowledgement recovery');
    return retained;
}
const posts = () => requests.filter((request) => request.method === 'POST' && request.path.endsWith('/recover-acknowledgement'));
const detailReads = () => requests.filter((request) => /\/admin\/actions\/[^/]+$/.test(request.path));

test('real StrictMode retains committed recovery while current detail observation is still pending', async () => {
    const post = deferred(), reload = deferred(); let request;
    route = (call) => {
        if (call.method === 'POST') { request = call.body; return post.promise; }
        return request ? reload.promise : response(item());
    };
    await draw();
    assert.equal(posts().length, 0); assert.match(host.textContent, /Outcome unverified/);
    const oldConfirm = await confirm();
    await act(async () => { oldConfirm(); oldConfirm(); });
    assert.equal(posts().length, 1);
    await act(async () => post.resolve(response(record(request))));
    assertRecordedRecovery(request);
    // The committed recovery summary is authoritative historical evidence. It
    // must not manufacture a refreshed ActionDetail or current provider state.
    assert.equal(status(), 'Unknown');
    await act(async () => oldConfirm());
    assert.equal(posts().length, 1);
    await act(async () => reload.resolve(response(completed(request))));
    assertRecordedRecovery(request);
    assert.match(host.textContent, /Acknowledgement recorded/);
    assert.doesNotMatch(host.textContent, /Retry same recovery request|Outcome unverified/);
    assert.equal(posts().length, 1);
});

test('original committed summary survives a completed detail followed by an explicitly reloaded old unknown detail', async () => {
    let request, laterOldRead = false;
    route = (call) => {
        if (call.method === 'POST') { request = call.body; return response(record(request)); }
        if (!request) return response(item());
        return response(laterOldRead ? { ...item(), tenant_name: 'Later stale detail was loaded' } : completed(request));
    };
    await draw(); const oldConfirm = await confirm();
    assertRecordedRecovery(request);
    assert.equal(status(), 'Completed');
    laterOldRead = true;
    const reads = detailReads().length;
    await click('Reload saved receipt');
    assert.equal(detailReads().length, reads + 1);
    assert.match(host.textContent, /Later stale detail was loaded/);
    assert.equal(status(), 'Unknown', 'The historical summary must not manufacture current detail status');
    assertRecordedRecovery(request);
    await act(async () => oldConfirm());
    assert.equal(posts().length, 1, 'A later old observation cannot revive a confirmed recovery mutation');
});

for (const source of ['same', 'different']) {
    test(`later valid same-action other-review detail with ${source} source preserves the original POST summary`, async () => {
        let request, laterOtherRead = false, servedOtherDetail;
        const other = {
            id: '60000000-0000-4000-8000-000000000001',
            actor_id: '70000000-0000-4000-8000-000000000001',
            request_id: '80000000-0000-4000-8000-000000000001',
            reason: 'Different saved review from another admin',
            recorded_at: '2026-10-07T15:00:00Z',
            source_digest: source === 'same' ? DIGEST : 'b'.repeat(64),
        };
        route = (call) => {
            if (call.method === 'POST') { request = call.body; return response(record(request)); }
            if (!request) return response(item());
            if (!laterOtherRead) return response(completed(request));
            servedOtherDetail = { ...completed(request, other), tenant_name: `Loaded ${source}-source other-review detail` };
            return response(servedOtherDetail);
        };
        await draw(); await confirm();
        assertRecordedRecovery(request);
        assert.equal(status(), 'Completed');
        laterOtherRead = true;
        const reads = detailReads().length;
        await click('Reload saved receipt');
        assert.equal(detailReads().length, reads + 1);
        assert.equal(servedOtherDetail.id, A);
        assert.equal(servedOtherDetail.acknowledgement_recovery_record.actor_id, other.actor_id);
        assert.equal(servedOtherDetail.acknowledgement_recovery_record.source_digest, other.source_digest);
        assert.ok(host.textContent.includes(`Loaded ${source}-source other-review detail`), 'The later server detail must actually reach the drawer');
        assertRecordedRecovery(request);
        const summary = host.querySelector('[aria-label="Saved acknowledgement recovery"]').textContent;
        assert.ok(!summary.includes(other.actor_id));
        assert.ok(!summary.includes(other.reason));
        assert.ok(!summary.includes(other.recorded_at));
        assert.equal(posts().length, 1);
    });
}

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

for (const failure of ['null', 'reject', 'foreign', 'old']) {
    test(`valid committed recovery survives ${failure} detail without another mutation and reloads read-only`, async () => {
        let request, repaired = false;
        route = (call) => {
            if (call.method === 'POST') {
                request = call.body;
                return response(record(request, { private_payload: 'private response payload' }));
            }
            if (!request) return response(item());
            if (repaired) return response(completed(request));
            if (failure === 'reject') return Promise.reject(new Error('Private read failure'));
            return response(failure === 'null' ? null : failure === 'foreign' ? item(B) : item());
        };
        await draw(); const oldConfirm = await confirm();
        assertRecordedRecovery(request);
        assert.equal(status(), 'Unknown', 'A recovery record must not synthesize current action details');
        assert.equal(posts().length, 1);
        await act(async () => oldConfirm());
        assert.equal(posts().length, 1, 'A retained handler cannot repeat a known committed recovery');
        repaired = true;
        const reads = detailReads().length;
        await click('Reload saved receipt');
        assert.equal(detailReads().length, reads + 1);
        assertRecordedRecovery(request);
        assert.equal(status(), 'Completed');
        assert.equal(posts().length, 1, 'Recovery of the read observation must issue only GET');
    });
}

for (const transition of ['close/reopen', 'unmount/remount']) {
    test(`committed recovery survives same-owner ${transition} with stale detail and no replay`, async () => {
        let request, reopened = false, repaired = false;
        route = (call) => {
            if (call.method === 'POST') { request = call.body; return response(record(request)); }
            if (!request) return response(item());
            return response(repaired ? completed(request) : reopened ? item() : null);
        };
        await draw(); const oldConfirm = await confirm();
        assertRecordedRecovery(request);
        if (transition === 'close/reopen') await draw(null);
        else { await act(async () => root.unmount()); root = createRoot(host); }
        reopened = true;
        await draw();
        assertRecordedRecovery(request);
        assert.equal(host.querySelector('textarea'), null);
        assert.equal(status(), 'Unknown');
        await act(async () => oldConfirm());
        assert.equal(posts().length, 1);
        repaired = true;
        await click('Reload saved receipt');
        assertRecordedRecovery(request);
        assert.equal(status(), 'Completed');
        assert.equal(posts().length, 1);
    });
}

for (const variant of ['wrong action', 'wrong digest', 'invalid timestamp']) {
    test(`malformed recovery ${variant} remains unconfirmed and retains its exact pending review`, async () => {
        route = (call) => {
            if (call.method !== 'POST') return response(item());
            const extra = variant === 'wrong action' ? { action_id: B }
                : variant === 'wrong digest' ? { source_digest: 'b'.repeat(64) } : { recorded_at: 'not-a-time' };
            return response(record(call.body, extra));
        };
        await draw(); const reads = detailReads().length;
        await confirm();
        assert.equal(host.querySelector('[aria-label="Saved acknowledgement recovery"]'), null);
        assert.match(host.textContent, /Recovery is unconfirmed/);
        assert.equal(detailReads().length, reads, 'An invalid record cannot authorize a follow-up confirmation read');
        const original = posts()[0].body;
        await click('Retry same recovery request');
        assert.deepEqual(posts()[1].body, original);
    });
}

test('a known recovery belongs only to its action and stale reload handlers cannot affect another selection', async () => {
    let request;
    route = (call) => {
        if (call.method === 'POST') { request = call.body; return response(record(request)); }
        if (call.path.endsWith(B)) return response(item(B));
        return response(item());
    };
    await draw(); await confirm();
    assertRecordedRecovery(request);
    const oldReload = retainedClick(button('Reload saved receipt'));
    await draw(B);
    const reads = detailReads().length;
    await act(async () => oldReload());
    assert.equal(detailReads().length, reads);
    assert.equal(posts().length, 1);
    assert.match(host.textContent, new RegExp(`Tenant ${B}`));
    assert.doesNotMatch(host.textContent, /Saved acknowledgement recovered|Reviewed the original saved acknowledgement/);
    await draw();
    assertRecordedRecovery(request);
    assert.equal(posts().length, 1);
});

test('mounted old-session DOM reload button cannot adopt a newer API session before rerender', async () => {
    route = () => response(item());
    await draw();
    const oldButton = button('Reload saved receipt'), reads = detailReads().length;
    await act(async () => { api.setToken('new-session-before-rerender'); oldButton.click(); });
    // The subscription now starts exactly one new owned read automatically.
    // The old reload button must not add a second read under the new session.
    assert.equal(detailReads().length, reads + 1);
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

test('actual AuthProvider logout and login retire a known recovery and its retained read callback', async () => {
    let request;
    route = (call) => {
        const nextOwner = call.authorization === 'Bearer new-synthetic-token';
        if (call.path.endsWith('/auth/me')) return response({ id: nextOwner ? 'new-admin' : 'old-admin', email: 'admin@example.invalid', role: 'platform_admin' });
        if (call.path.endsWith('/auth/logout')) return response({});
        if (call.path.endsWith('/auth/login')) return response({ access_token: 'new-synthetic-token', user_id: 'new-admin', email: 'admin@example.invalid', role: 'platform_admin' });
        if (call.method === 'POST') { request = call.body; return response(record(request)); }
        return response(request && !nextOwner ? null : item());
    };
    await act(async () => root.render(el(StrictMode, null, el(AuthProvider, null, el(AuthScreen)))));
    await confirm('Old confirmed private review');
    assertRecordedRecovery(request);
    const oldReload = retainedClick(button('Reload saved receipt'));
    await click('Sign out test session');
    assert.doesNotMatch(host.textContent, /Old confirmed private review|Saved acknowledgement recovered/);
    await click('Sign in new test session');
    assert.equal(host.querySelector('[data-testid="principal"]').textContent, 'new-admin');
    const reads = detailReads().length;
    await act(async () => oldReload());
    assert.equal(detailReads().length, reads);
    assert.equal(posts().length, 1);
    assert.doesNotMatch(host.textContent, /Old confirmed private review|Saved acknowledgement recovered|Retry same recovery request/);
    assert.ok(host.querySelector('textarea'), 'The new owner receives only its current server-provided view');
});

test('actual AuthProvider DOM logout/login discards old review and old in-flight response', async () => {
    const pending = deferred();
    route = (call) => {
        if (call.path.endsWith('/auth/me')) return response({ id: call.authorization === 'Bearer new-synthetic-token' ? 'new-admin' : 'old-admin', email: 'admin@example.invalid', role: 'platform_admin' });
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
        if (call.path.endsWith('/auth/me')) return call.authorization === 'Bearer new-login-token'
            ? response({ id: 'new-admin', email: 'admin@example.invalid', role: 'platform_admin' })
            : verification.promise.then(() => response({ detail: 'Old session rejected' }, 401));
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

test('actual AuthProvider admits the next login only after cookie-clearing logout settles', async () => {
    const logout = deferred();
    route = (call) => {
        if (call.path.endsWith('/auth/me')) return response({ id: call.authorization === 'Bearer new-login-token' ? 'new-admin' : 'old-admin', email: 'admin@example.invalid', role: 'platform_admin' });
        if (call.path.endsWith('/auth/logout')) return logout.promise;
        if (call.path.endsWith('/auth/login')) return response({ access_token: 'new-login-token', user_id: 'new-admin', email: 'admin@example.invalid', role: 'platform_admin' });
        return response(item());
    };
    await act(async () => root.render(el(StrictMode, null, el(AuthProvider, null, el(AuthScreen)))));
    await click('Sign out test session');
    await click('Sign in new test session');
    assert.equal(api.getToken(), null);
    assert.equal(requests.filter((request) => request.path.endsWith('/auth/login')).length, 0);
    await act(async () => logout.resolve(response({})));
    await click('Sign in new test session');
    assert.equal(api.getToken(), 'new-login-token');
    assert.equal(host.querySelector('[data-testid="principal"]').textContent, 'new-admin');
});
