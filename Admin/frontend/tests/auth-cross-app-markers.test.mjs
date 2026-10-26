import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, dom, state, render, response, deferred, settle } from './bh-harness.mjs';

const OWN = 'talky.admin.identity.v1', FOREIGN = 'talklee.auth.identity.v1';
const A = { id: 'marker-A', email: 'a@example.invalid', name: 'A', role: 'platform_admin', tenant_id: null };
const B = { ...A, id: 'marker-B', email: 'b@example.invalid' };
const originalChannel = Object.getOwnPropertyDescriptor(window, 'BroadcastChannel');
const originalWindowStorage = Object.getOwnPropertyDescriptor(window, 'localStorage');
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
let auth, channels, sent;
function Probe() { auth = C.useAuth(); return el('p', null, auth.user?.id ?? 'anonymous'); }
function marker(kind = 'changed', nonce = crypto.randomUUID()) { return { kind, nonce }; }
function emitStorage(key, value) {
    window.dispatchEvent(new dom.window.StorageEvent('storage', { key, newValue: JSON.stringify(value) }));
}
function installChannels() {
    channels = new Set(); sent = [];
    class Channel {
        onmessage = null;
        constructor(name) { this.name = name; channels.add(this); }
        postMessage(value) {
            sent.push({ name: this.name, value });
            for (const other of channels) if (other !== this && other.name === this.name) queueMicrotask(() => {
                if (channels.has(other)) other.onmessage?.({ data: value });
            });
        }
        close() { channels.delete(this); }
    }
    Object.defineProperty(window, 'BroadcastChannel', { configurable: true, value: Channel });
    return Channel;
}
async function boot() {
    api.setToken('old-admin-bearer');
    state.route = () => response(A);
    await render(el(C.AuthProvider, null, el(Probe))); await settle();
    assert.equal(auth.user?.id, A.id); state.requests = [];
}
test.beforeEach(() => { installChannels(); });
test.afterEach(() => {
    if (originalChannel) Object.defineProperty(window, 'BroadcastChannel', originalChannel); else delete window.BroadcastChannel;
    Object.defineProperty(window, 'localStorage', originalWindowStorage);
    Object.defineProperty(globalThis, 'localStorage', originalStorage);
});

test('foreign storage change removes old Admin bearer before canonical cookie verification', async () => {
    await boot(); state.route = () => response(B);
    await act(async () => emitStorage(FOREIGN, marker())); await settle();
    assert.equal(auth.user?.id, B.id);
    assert.equal(state.requests.length, 1);
    assert.equal(state.requests[0].authorization, undefined);
    assert.equal(state.requests[0].headers['X-Talky-Expected-User'], undefined);
    assert.equal(api.getToken(), null); assert.equal(localStorage.getItem('admin_token'), null);
    assert.equal(sent.length, 0, 'received hints must not be relayed');
});

test('foreign channel-only change clears in-memory bearer with storage blocked', async () => {
    await boot(); state.route = () => response(B);
    const blocked = { configurable: true, get() { throw new Error('Synthetic storage blocked'); } };
    Object.defineProperty(window, 'localStorage', blocked); Object.defineProperty(globalThis, 'localStorage', blocked);
    const external = new window.BroadcastChannel(FOREIGN);
    await act(async () => external.postMessage(marker())); await settle(); external.close();
    assert.equal(auth.user?.id, B.id); assert.equal(api.getToken(), null);
    assert.equal(state.requests.length, 1); assert.equal(state.requests[0].authorization, undefined);
    assert.equal(sent.length, 1, 'only the originating channel message is published');
});

for (const order of ['marker-first', 'token-first']) test(`own changed marker preserves new sibling bearer (${order})`, async () => {
    await boot(); state.route = () => response(B);
    localStorage.setItem('admin_token', 'new-admin-bearer');
    if (order === 'token-first') await act(async () => window.dispatchEvent(new dom.window.StorageEvent('storage', { key: 'admin_token', newValue: 'new-admin-bearer' })));
    await act(async () => emitStorage(OWN, marker())); await settle();
    assert.equal(auth.user?.id, B.id); assert.equal(api.getToken(), 'new-admin-bearer');
    assert.equal(state.requests[0].authorization, 'Bearer new-admin-bearer');
    assert.ok(state.requests.every(request => request.authorization === 'Bearer new-admin-bearer'));
    const count = state.requests.length;
    await act(async () => window.dispatchEvent(new dom.window.StorageEvent('storage', { key: 'admin_token', newValue: 'new-admin-bearer' })));
    assert.equal(state.requests.length, count, 'late matching token event is not another identity change');
});

test('foreign storage plus channel duplicate verifies once and ignores subsequent matching token removal', async () => {
    await boot(); const read = deferred(); state.route = () => read.promise;
    const value = marker(), external = new window.BroadcastChannel(FOREIGN);
    await act(async () => { emitStorage(FOREIGN, value); external.postMessage(value); }); await settle();
    assert.equal(auth.user, null); assert.equal(state.requests.length, 1);
    await act(async () => window.dispatchEvent(new dom.window.StorageEvent('storage', { key: 'admin_token', newValue: null })));
    assert.equal(state.requests.length, 1);
    await act(async () => read.resolve(response(B))); await settle(); external.close();
    assert.equal(auth.user?.id, B.id); assert.equal(state.requests[0].authorization, undefined);
});

test('foreign logout retires old persisted bearer and rejects an earlier delayed profile', async () => {
    await boot(); const read = deferred(); state.route = () => read.promise;
    let pending; await act(async () => { pending = auth.checkAuth(); });
    await act(async () => emitStorage(FOREIGN, marker('logout')));
    assert.equal(auth.user, null); assert.equal(api.getToken(), null); assert.equal(localStorage.getItem('admin_token'), null);
    await act(async () => { read.resolve(response(A)); await pending; }); await settle();
    assert.equal(auth.user, null); assert.equal(auth.logoutRecoveryRequired, true);
});

test('a foreign changed hint preserves an explicit unconfirmed local logout fence', async () => {
    await boot(); localStorage.setItem('talky.admin.logout.pending', 'unconfirmed');
    await act(async () => emitStorage(FOREIGN, marker())); await settle();
    assert.equal(auth.user, null); assert.equal(state.requests.length, 0);
    assert.equal(localStorage.getItem('talky.admin.logout.pending'), 'unconfirmed');
});
