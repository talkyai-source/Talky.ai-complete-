// FX1b Task 8: "Retry session check" does not show beside the server's
// wrong email or password error. It stays for errors where a session
// re-check is the next step.
import assert from 'node:assert/strict';
import test from 'node:test';
import { Routes, Route } from 'react-router-dom';
import { C, api, el, state, render, response, find, click, setInput } from './bh-harness.mjs';

const draw = async () => {
    await render(el(C.AuthProvider, null, el(C.MemoryRouter, { initialEntries: ['/login'] },
        el(Routes, null, el(Route, { path: '/login', element: el(C.LoginPage) }), el(Route, { path: '*', element: el('p', null, 'Available section') })))));
};
const signIn = async () => {
    await setInput(find('#email'), 'a@example.invalid'); await setInput(find('#password'), 'synthetic-password'); await click('Sign in');
};
const routes = (login) => (request) => {
    if (request.path.endsWith('/auth/login')) return login();
    return response({ detail: 'Authentication required' }, 401);
};
const alertText = () => find('.login-error span')?.textContent ?? null;
const retryButton = () => [...state.host.querySelectorAll('button')].find((node) => node.textContent.trim() === 'Retry session check');

test.beforeEach(() => { state.mfaProof = false; api.setToken(null); localStorage.removeItem('admin_token'); });

test('a wrong email or password shows the server error without "Retry session check"', async () => {
    state.route = routes(() => response({ detail: 'Invalid email or password.' }, 401));
    await draw(); await signIn();
    assert.equal(alertText(), 'Invalid email or password.');
    assert.equal(retryButton() !== undefined, false);
    assert.equal(find('#password') !== null, true, 'stays on the password form');
});

test('a sign-in that failed for another reason still offers "Retry session check"', async () => {
    state.route = routes(() => response({ detail: 'Gateway down' }, 503));
    await draw(); await signIn();
    assert.equal(alertText() !== null, true);
    assert.equal(retryButton() !== undefined, true);
});

test('a wrong password followed by a failed sign-in for another reason offers the retry again', async () => {
    let wrong = true;
    state.route = routes(() => wrong ? response({ detail: 'Invalid email or password.' }, 401) : response({ detail: 'Gateway down' }, 503));
    await draw(); await signIn();
    assert.equal(retryButton() !== undefined, false);
    wrong = false; await signIn();
    assert.equal(retryButton() !== undefined, true);
});
