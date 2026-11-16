// The panel reports its own errors to POST /admin/client-errors: fixed body, scrubbed text,
// bounded rate, nothing while signed out, and a failed report is never reported itself.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { C, api, el, find, render, response, settle, state, text, click } from './bh-harness.mjs';

const FIELDS = ['app_version', 'kind', 'message', 'name', 'occurred_at', 'route', 'stack'];
const T0 = Date.parse('2026-11-17T10:00:00Z');
const make = (over = {}) => {
    const sent = [];
    const clock = { now: T0 };
    const report = C.createErrorReporter({
        send: (body) => { sent.push(body); return Promise.resolve(true); },
        now: () => clock.now, isSignedIn: () => true, route: () => '/campaigns', appVersion: null, ...over,
    });
    return { sent, clock, report };
};
const failure = (message, stack) => { const error = new Error(message); if (stack) error.stack = stack; return error; };

test('body: exactly the allowed fields, with the contract limits', () => {
    const { sent, report } = make();
    report('error', failure('Boom', 'Error: Boom\n    at run (https://admin.example.invalid/assets/index.js:10:5)'));
    assert.equal(sent.length, 1);
    assert.deepEqual(Object.keys(sent[0]).sort(), FIELDS);
    assert.equal(sent[0].kind, 'error'); assert.equal(sent[0].name, 'Error'); assert.equal(sent[0].message, 'Boom');
    assert.equal(sent[0].route, '/campaigns'); assert.equal(sent[0].app_version, null);
    assert.equal(sent[0].occurred_at, '2026-11-17T10:00:00.000Z');
    assert.deepEqual(sent[0].stack, ['at run (https://admin.example.invalid/assets/index.js:10:5)']);
});

test('body: lengths are capped (name 100, message 500, 10 stack lines of 300) and the body stays under 8 KB', () => {
    const { sent, report } = make();
    const error = failure('word '.repeat(200), ['Error: x', ...Array.from({ length: 30 }, (_, index) => `at f${index} (${'ab/'.repeat(200)})`)].join('\n'));
    error.name = 'N '.repeat(150);
    report('render', error);
    assert.equal(sent[0].name.length, 100); assert.equal(sent[0].message.length, 500);
    assert.equal(sent[0].stack.length, 10); assert.equal(sent[0].stack.every((line) => line.length <= 300), true);
    const wide = failure('\u{1F600}'.repeat(500), Array.from({ length: 12 }, () => `at ${'\u{1F600}'.repeat(150)}`).join('\n'));
    report('error', wide);
    assert.equal(new TextEncoder().encode(JSON.stringify(sent[1])).length <= 8192, true);
});

test('scrub: email, phone, token, bearer and query string are removed from message and every stack line', () => {
    const { sent, report } = make();
    const jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.abcDEF123_-xyz';
    const message = `Failed for jane.doe@example.com call +44 7700 900123 or 0207-946-0958 with ${jwt} Bearer abc.def-123456 key ABCDEFGHIJKLMNOPQRSTUVWX12 at https://api.example.invalid/v1/x?email=a@b.co&token=zzz`;
    report('error', failure(message, `Error: ${message}\n    at https://admin.example.invalid/assets/a.js?v=1&user=bob@example.com:1:2\n    at call 442079460958 (x.js:1:1)`));
    const everything = [sent[0].message, ...sent[0].stack].join('\n');
    for (const secret of ['jane.doe', '7700', '900123', '946-0958', 'eyJhbGci', 'abc.def-123456', 'ABCDEFGHIJKLMNOPQRSTUVWX12', 'email=', 'token=', 'zzz', 'v=1', 'bob@', '442079460958']) {
        assert.equal(everything.includes(secret), false, `${secret} leaked`);
    }
    assert.match(sent[0].message, /\[email\]/); assert.match(sent[0].message, /\[phone\]/); assert.match(sent[0].message, /\[token\]/);
    assert.match(sent[0].message, /https:\/\/api\.example\.invalid\/v1\/x/);
    assert.equal(sent[0].stack.length, 2);
});

test('scrub: ordinary short words, line numbers and file names survive', () => {
    assert.equal(C.scrubText('Cannot read properties of undefined (reading "map") at index.js:12:34'), 'Cannot read properties of undefined (reading "map") at index.js:12:34');
});

test('route: the path only, never the query string or fragment', () => {
    assert.equal(C.currentRoutePath({ pathname: '/campaigns', search: '?tenant=1&x=2', hash: '#top' }), '/campaigns');
    assert.equal(C.currentRoutePath({ pathname: '/a'.repeat(150), search: '', hash: '' }).length, 200);
    const { sent, report } = make({ route: () => C.currentRoutePath({ pathname: '/users', search: '?email=a@b.co', hash: '' }) });
    report('error', failure('x'));
    assert.equal(sent[0].route, '/users');
});

test('limits: at most 5 reports per minute, then reports resume', () => {
    const { sent, clock, report } = make();
    for (let index = 0; index < 8; index += 1) report('error', failure(`different ${index}`));
    assert.equal(sent.length, 5);
    clock.now += 61_000;
    report('error', failure('different 9'));
    assert.equal(sent.length, 6);
});

test('limits: the same name, message and route is reported once per 5 minutes', () => {
    const { sent, clock, report } = make();
    report('error', failure('same')); report('error', failure('same'));
    assert.equal(sent.length, 1);
    clock.now += 299_000; report('error', failure('same'));
    assert.equal(sent.length, 1);
    clock.now += 2_000; report('error', failure('same'));
    assert.equal(sent.length, 2);
    report('error', failure('other'));
    assert.equal(sent.length, 3);
});

test('limits: the same error on another route is a different report', () => {
    let path = '/a';
    const { sent, report } = make({ route: () => path });
    report('error', failure('same')); path = '/b'; report('error', failure('same'));
    assert.equal(sent.length, 2);
});

test('signed out: nothing is sent and nothing is counted against the limits', () => {
    let signedIn = false;
    const { sent, report } = make({ isSignedIn: () => signedIn });
    for (let index = 0; index < 10; index += 1) report('error', failure('same'));
    assert.equal(sent.length, 0);
    signedIn = true; report('error', failure('same'));
    assert.equal(sent.length, 1);
});

test('failure: a send that rejects or throws raises nothing, is not retried and is not reported', async () => {
    let calls = 0;
    const rejecting = make({ send: () => { calls += 1; return Promise.reject(new Error('network down')); } });
    assert.doesNotThrow(() => rejecting.report('error', failure('one')));
    await new Promise((done) => setTimeout(done, 10));
    assert.equal(calls, 1);
    const throwing = make({ send: () => { calls += 1; throw new Error('sync failure'); } });
    assert.doesNotThrow(() => throwing.report('error', failure('two')));
    assert.equal(calls, 2);
    assert.doesNotThrow(() => throwing.report('error', undefined));
    assert.doesNotThrow(() => throwing.report('error', { toString() { throw new Error('hostile'); } }));
});

test('non-error values are reported as plain text', () => {
    const { sent, report } = make();
    report('unhandledrejection', 'plain string reason');
    assert.equal(sent[0].kind, 'unhandledrejection'); assert.equal(sent[0].message, 'plain string reason'); assert.deepEqual(sent[0].stack, []);
});

test('listeners: error and unhandledrejection are registered once, however often it is asked', () => {
    const added = [];
    const target = { addEventListener: (type, handler) => added.push([type, handler]) };
    const reports = [];
    const report = (kind, error) => reports.push([kind, error]);
    C.registerGlobalErrorListeners(target, report);
    C.registerGlobalErrorListeners(target, report);
    C.registerGlobalErrorListeners(target, report);
    assert.deepEqual(added.map(([type]) => type).sort(), ['error', 'unhandledrejection']);
    const handler = (type) => added.find(([name]) => name === type)[1];
    const boom = new Error('boom');
    handler('error')({ error: boom, message: 'boom' });
    handler('error')({ message: '' });
    handler('unhandledrejection')({ reason: 'why' });
    assert.deepEqual(reports, [['error', boom], ['unhandledrejection', 'why']]);
});

test('main.tsx registers the listeners at module level, wraps App in the boundary, and not inside a component', () => {
    const main = readFileSync(new URL('../src/main.tsx', import.meta.url), 'utf8');
    assert.match(main, /<AppErrorBoundary>\s*<App \/>\s*<\/AppErrorBoundary>/);
    assert.equal((main.match(/registerGlobalErrorListeners\(\)/g) ?? []).length, 1);
    assert.equal(main.indexOf('registerGlobalErrorListeners()') < main.indexOf('createRoot('), true);
});

test('api: reportClientError posts the body once with the session and returns true on 204', async () => {
    api.setToken('access-A');
    state.route = () => new Response(null, { status: 204 });
    const body = { kind: 'error', name: 'Error', message: 'x', stack: [], route: '/', app_version: null, occurred_at: '2026-11-17T10:00:00.000Z' };
    assert.equal(await api.reportClientError(body), true);
    assert.equal(state.requests.length, 1);
    assert.equal(state.requests[0].path, '/api/v1/admin/client-errors');
    assert.equal(state.requests[0].method, 'POST');
    assert.equal(state.requests[0].authorization, 'Bearer access-A');
    assert.deepEqual(state.requests[0].body, body);
});

test('api: a 401, 429 or network failure is swallowed: no refresh, no retry, no sign-out', async () => {
    api.setToken('access-A');
    let signedOut = 0;
    const stop = api.onUnauthorized(() => { signedOut += 1; });
    for (const status of [401, 413, 422, 429]) {
        state.route = () => response({ detail: 'no' }, status);
        assert.equal(await api.reportClientError({ kind: 'error', name: 'E', message: 'm', stack: [], route: '/', app_version: null, occurred_at: 'x' }), false);
    }
    state.route = () => { throw new Error('offline'); };
    assert.equal(await api.reportClientError({ kind: 'error', name: 'E', message: 'm', stack: [], route: '/', app_version: null, occurred_at: 'x' }), false);
    stop();
    assert.equal(state.requests.length, 5, 'one request per call: no retry');
    assert.equal(state.requests.some((request) => request.path.includes('/auth/refresh')), false);
    assert.equal(signedOut, 0);
    assert.equal(api.getToken(), 'access-A');
});

const Bomb = () => { throw new Error('secret@example.com exploded 0207 946 0958'); };

test('boundary: a render error shows the plain screen, no error text, and reports once', async () => {
    api.setToken('access-A');
    state.route = () => new Response(null, { status: 204 });
    const original = console.error; console.error = () => {};
    try {
        await render(el(C.AppErrorBoundary, null, el(Bomb)));
        await settle(40);
    } finally { console.error = original; }
    assert.match(text(), /Something went wrong on this page\./);
    assert.doesNotMatch(text(), /exploded|secret@|0207|at Bomb/);
    assert.ok([...state.host.querySelectorAll('button')].some((node) => node.textContent.trim() === 'Reload'));
    assert.equal(find('a[href="/"]') !== null, true);
    const posts = state.requests.filter((request) => request.path === '/api/v1/admin/client-errors');
    assert.equal(posts.length, 1);
    assert.equal(posts[0].body.kind, 'render');
    assert.equal(JSON.stringify(posts[0].body).includes('secret@example.com'), false);
    assert.equal(JSON.stringify(posts[0].body).includes('946 0958'), false);
});

test('boundary: children render normally when nothing fails', async () => {
    await render(el(C.AppErrorBoundary, null, el('p', null, 'All fine')));
    assert.match(text(), /All fine/);
    assert.equal(state.requests.length, 0);
});

test('boundary: Reload reloads the page', async () => {
    const original = console.error; console.error = () => {};
    let reloaded = 0;
    try {
        await render(el(C.AppErrorBoundary, { reload: () => { reloaded += 1; } }, el(Bomb)));
        await settle(40);
    } finally { console.error = original; }
    await click('Reload');
    assert.equal(reloaded, 1);
});
