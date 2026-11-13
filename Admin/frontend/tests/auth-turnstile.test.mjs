// QA F4-12: the backend can require a Cloudflare Turnstile token on POST /auth/login
// (AUTH_TURNSTILE_REQUIRED=true). The Admin sign-in must send one whenever its build has a
// site key (VITE_TURNSTILE_SITE_KEY) and must be exactly the old sign-in when it has none.
// Real React with the real LoginPage, AuthProvider and api client in jsdom. Cloudflare's script
// is never downloaded: a fake window.turnstile stands in for it, so these tests prove our wiring
// (script, widget options, token hand-off, single use), not Cloudflare's challenge itself.
import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { api as harnessApi, el, act, dom, state, render, response, find, text, click, setInput, settle } from './bh-harness.mjs';

// Vite inlines import.meta.env.VITE_* at build time, so each configuration is its own bundle.
async function bundle(env) {
    const built = await build({
        stdin: { contents: `export { LoginPage } from './src/pages/LoginPage';
            export { AuthProvider } from './src/lib/auth'; export { api } from './src/lib/api';
            export { MemoryRouter, Routes, Route } from 'react-router-dom';`,
        resolveDir: fileURLToPath(new URL('..', import.meta.url)), loader: 'ts' },
        bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
        define: { 'import.meta.env': JSON.stringify(env) }, jsx: 'automatic', loader: { '.css': 'empty' },
    });
    const loaded = { exports: {} };
    new Function('require', 'module', 'exports', built.outputFiles[0].text)(createRequire(import.meta.url), loaded, loaded.exports);
    return loaded.exports;
}
const SITE_KEY = 'public-site-key-for-tests';
const SCRIPT_URL = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
const unkeyed = await bundle({});
const keyed = await bundle({ VITE_TURNSTILE_SITE_KEY: SITE_KEY });

test.beforeEach(() => {
    harnessApi.setToken(null); unkeyed.api.setToken(null); keyed.api.setToken(null);
    localStorage.removeItem('admin_token');
});
test.afterEach(() => {
    delete dom.window.turnstile;
    for (const tag of document.querySelectorAll('script')) tag.remove();
});

const denied = () => response({ detail: 'Authentication required' }, 401);
const wrongPassword = () => response({ detail: 'Invalid credentials' }, 401);
const routes = (handlers) => (request) => {
    const suffix = Object.keys(handlers).find((path) => request.path.endsWith(path));
    return suffix ? handlers[suffix](request) : denied();
};
const logins = () => state.requests.filter((request) => request.path.endsWith('/auth/login'));
const sentTokens = () => logins().map((request) => request.body.turnstile_token);

const draw = async (app) => {
    await render(el(app.AuthProvider, null, el(app.MemoryRouter, { initialEntries: ['/login'] },
        el(app.Routes, null, el(app.Route, { path: '/login', element: el(app.LoginPage) }),
            el(app.Route, { path: '*', element: el('p', null, 'Available section') })))));
    await settle();
};
const fillCredentials = async () => {
    await setInput(find('#email'), 'admin@example.invalid');
    await setInput(find('#password'), 'synthetic-password');
};
const signIn = async () => { await click('Sign In'); await settle(); };
const submitButton = () => find('button[type="submit"]');
const cloudflareScripts = () => [...document.querySelectorAll('script')]
    .filter((tag) => (tag.getAttribute('src') ?? '').includes('challenges.cloudflare.com'));

// Plays Cloudflare's api.js: records what the page asks of it, then fires the script's load event.
async function cloudflareLoads({ renderThrows = false, renderReturnsNothing = false } = {}) {
    const [tag] = cloudflareScripts();
    assert.ok(tag, 'the page asked for the Turnstile script');
    const widgets = [];
    dom.window.turnstile = {
        render: (container, params) => {
            if (renderThrows) throw new Error('[Cloudflare Turnstile] Invalid input for parameter "sitekey"');
            if (renderReturnsNothing) return undefined;
            widgets.push({ id: `widget-${widgets.length + 1}`, container, params, resets: 0, removed: false });
            return widgets.at(-1).id;
        },
        reset: (id) => { widgets.find((widget) => widget.id === id).resets += 1; },
        remove: (id) => { widgets.find((widget) => widget.id === id).removed = true; },
    };
    await act(async () => { tag.dispatchEvent(new dom.window.Event('load')); });
    return { live: () => widgets.filter((widget) => !widget.removed) };
}
const issue = async (widget, token) => { await act(async () => widget.params.callback(token)); };

test('without a site key the sign-in is unchanged: no script, no widget, no turnstile_token', async () => {
    state.route = routes({ '/auth/login': wrongPassword });
    await draw(unkeyed);
    assert.equal(document.querySelectorAll('script').length, 0, 'no script of any kind is added');
    assert.equal(dom.window.turnstile, undefined);
    assert.deepEqual([...find('form').children].map((node) => node.className), ['form-group', 'form-group', 'login-button'],
        'the form has no extra UI');
    assert.equal(submitButton().disabled, false, 'submit does not wait for anything');
    await fillCredentials(); await signIn();
    assert.equal(logins().length, 1);
    assert.deepEqual(logins()[0].body, { email: 'admin@example.invalid', password: 'synthetic-password' });
    assert.match(text(), /Invalid credentials/, 'the attempt ran to its normal end');
    assert.equal(document.querySelectorAll('script').length, 0);
});

test('an empty site key counts as no site key', async () => {
    const blank = await bundle({ VITE_TURNSTILE_SITE_KEY: '   ' });
    state.route = routes({ '/auth/login': wrongPassword });
    await render(el(blank.AuthProvider, null, el(blank.MemoryRouter, null, el(blank.LoginPage)))); await settle();
    assert.equal(document.querySelectorAll('script').length, 0);
    assert.equal(find('form').children.length, 3);
    await fillCredentials(); await signIn();
    assert.equal('turnstile_token' in logins()[0].body, false);
});

test('with a site key the page loads the explicit-render script once and renders a login widget in the form', async () => {
    state.route = routes({ '/auth/login': wrongPassword });
    await draw(keyed);
    assert.equal(cloudflareScripts().length, 1);
    assert.equal(cloudflareScripts()[0].getAttribute('src'), SCRIPT_URL);
    const cloudflare = await cloudflareLoads();
    assert.equal(cloudflare.live().length, 1);
    const [widget] = cloudflare.live();
    assert.equal(widget.params.sitekey, SITE_KEY);
    assert.equal(widget.params.action, 'login');
    assert.ok(find('form').contains(widget.container), 'the widget sits inside the sign-in form');
    assert.equal(cloudflareScripts().length, 1, 'still a single script tag');
});

test('with a site key, submit waits for a token and then sends exactly the token the widget gave', async () => {
    state.route = routes({ '/auth/login': wrongPassword });
    await draw(keyed);
    const [widget] = (await cloudflareLoads()).live();
    await fillCredentials();
    assert.equal(submitButton().disabled, true, 'no token yet');
    await signIn();
    await act(async () => { find('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); });
    await settle();
    assert.equal(logins().length, 0, 'nothing is sent while the challenge is unsolved');
    await issue(widget, 'token-one');
    assert.equal(submitButton().disabled, false);
    await signIn();
    assert.equal(logins().length, 1);
    assert.deepEqual(logins()[0].body, { email: 'admin@example.invalid', password: 'synthetic-password', turnstile_token: 'token-one' });
});

test('each attempt spends its token: the widget is reset and the next attempt carries a fresh one', async () => {
    state.route = routes({ '/auth/login': wrongPassword });
    await draw(keyed);
    const [widget] = (await cloudflareLoads()).live();
    await issue(widget, 'token-one');
    await fillCredentials(); await signIn();
    assert.equal(widget.resets, 1, 'the widget is reset after the attempt');
    assert.equal(submitButton().disabled, true, 'the spent token cannot be sent again');
    await fillCredentials(); await signIn();
    assert.equal(logins().length, 1, 'no second attempt until the widget issues a new token');
    await issue(widget, 'token-two');
    await signIn();
    assert.deepEqual(sentTokens(), ['token-one', 'token-two']);
    assert.equal(widget.resets, 2);
});

test('an expired or failed token disables submit until the widget issues another', async () => {
    state.route = routes({ '/auth/login': wrongPassword });
    await draw(keyed);
    const [widget] = (await cloudflareLoads()).live();
    await issue(widget, 'token-one');
    assert.equal(submitButton().disabled, false);
    await act(async () => widget.params['expired-callback']());
    assert.equal(submitButton().disabled, true, 'an expired token is not sendable');
    await issue(widget, 'token-two');
    assert.equal(submitButton().disabled, false);
    await act(async () => widget.params['error-callback']());
    assert.equal(submitButton().disabled, true, 'a failed challenge is not sendable');
    await fillCredentials(); await signIn();
    assert.equal(logins().length, 0);
});

test('a Turnstile script that cannot load is reported and never lets an unverified sign-in through', async () => {
    state.route = routes({ '/auth/login': wrongPassword });
    await draw(keyed);
    await act(async () => { cloudflareScripts()[0].dispatchEvent(new dom.window.Event('error')); });
    assert.match(text(), /Bot verification could not be loaded/);
    assert.equal(submitButton().disabled, true);
    assert.equal(cloudflareScripts().length, 0, 'the failed tag is dropped so a reload tries the download again');
    await fillCredentials(); await signIn();
    assert.equal(logins().length, 0);
});

for (const [what, failure] of [['throws', { renderThrows: true }], ['returns no widget id', { renderReturnsNothing: true }]]) {
    test(`a widget that cannot be rendered (render ${what}, e.g. a malformed site key) is reported and the form stays up`, async () => {
        state.route = routes({ '/auth/login': wrongPassword });
        await draw(keyed);
        await cloudflareLoads(failure);
        assert.match(text(), /Bot verification could not be loaded/);
        assert.ok(find('#email') && find('#password'), 'the sign-in page is not blanked');
        assert.equal(submitButton().disabled, true);
        await fillCredentials(); await signIn();
        assert.equal(logins().length, 0);
    });
}

test('the MFA step needs no token, and going back to sign in brings a fresh widget that needs one', async () => {
    state.route = routes({
        '/auth/login': () => response({ access_token: '', mfa_required: true, mfa_challenge_token: 'one-use-challenge' }),
        '/auth/mfa/verify': () => response({ detail: 'Invalid code' }, 401),
    });
    await draw(keyed);
    const cloudflare = await cloudflareLoads();
    await issue(cloudflare.live()[0], 'token-one');
    await fillCredentials(); await signIn();
    assert.ok(find('#factor'), 'the second factor is asked for');
    assert.equal(cloudflare.live().length, 0, 'the widget leaves with the password form');
    await setInput(find('#factor'), '123456');
    assert.equal(submitButton().disabled, false, 'the MFA step does not wait for a Turnstile token');
    await click('Verify'); await settle();
    const verify = state.requests.find((request) => request.path.endsWith('/auth/mfa/verify'));
    assert.deepEqual(verify.body, { challenge_token: 'one-use-challenge', code: '123456' });
    await click('Back to sign in');
    assert.equal(cloudflare.live().length, 1, 'a new widget for the new sign-in');
    assert.equal(submitButton().disabled, true, 'a new sign-in needs a new token');
    await issue(cloudflare.live()[0], 'token-two');
    await fillCredentials(); await signIn();
    assert.deepEqual(sentTokens(), ['token-one', 'token-two']);
});

test('api.login adds turnstile_token only when it is given one', async () => {
    state.route = routes({ '/auth/login': wrongPassword });
    await unkeyed.api.login('admin@example.invalid', 'synthetic-password');
    await unkeyed.api.login('admin@example.invalid', 'synthetic-password', undefined, 'token-one');
    assert.deepEqual(logins().map((request) => request.body), [
        { email: 'admin@example.invalid', password: 'synthetic-password' },
        { email: 'admin@example.invalid', password: 'synthetic-password', turnstile_token: 'token-one' },
    ]);
});
