// Shared DOM harness for the bughunt admin-frontend regression tests.
// Real React renderer/effects/event dispatch in jsdom with a stubbed fetch.
// Not a browser or provider test: no network is reachable (sockets throw).
import assert from 'node:assert/strict';
import { after, afterEach, beforeEach } from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import net from 'node:net';
import { build } from 'esbuild';
import { JSDOM } from 'jsdom';

export const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://admin.example.invalid/' });
const originalGlobals = new Map();
for (const [key, value] of Object.entries({ window: dom.window, document: dom.window.document,
    navigator: dom.window.navigator, localStorage: dom.window.localStorage,
    HTMLElement: dom.window.HTMLElement, Event: dom.window.Event, IS_REACT_ACT_ENVIRONMENT: true })) {
    originalGlobals.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { value, writable: true, configurable: true });
}
// DOM must exist before React DOM initializes its event support.
export const { createElement: el, StrictMode, act } = await import('react');
const { createRoot } = await import('react-dom/client');

const bundle = await build({
    stdin: {
        contents: `
            export { TenantsTable } from './src/components/TenantsTable';
            export { TenantsPage } from './src/pages/TenantsPage';
            export { ConnectorDetailDrawer } from './src/components/ConnectorDetailDrawer';
            export { ConnectorsTable } from './src/components/ConnectorsTable';
            export { ActionsTable } from './src/components/ActionsTable';
            export { LiveCalls } from './src/components/LiveCalls';
            export { StatsGrid } from './src/components/StatsGrid';
            export { Incidents } from './src/components/Incidents';
            export { SystemHealth } from './src/components/SystemHealth';
            export { RecordingsTable } from './src/components/RecordingsTable';
            export { FeedbackTable } from './src/components/FeedbackTable';
            export { AuthProvider, useAuth } from './src/lib/auth';
            export { api } from './src/lib/api';
            export { MemoryRouter } from 'react-router-dom';
            export { CallDetailDrawer } from './src/components/CallDetailDrawer';
        `,
        resolveDir: fileURLToPath(new URL('..', import.meta.url)),
        loader: 'ts',
    },
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
    define: { 'import.meta.env': '{}' }, jsx: 'automatic', loader: { '.css': 'empty' },
});
const loaded = { exports: {} };
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), loaded, loaded.exports);
export const C = loaded.exports;
export const api = C.api;

const originalFetch = globalThis.fetch, originalConnect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function () { throw new Error('Network is forbidden in React DOM unit controls'); };

export const response = (data, status = 200) => new Response(JSON.stringify(data), { status });
export const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
export const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

export const state = { root: null, host: null, requests: [], route: null };
// Default: any request not explicitly routed is an error response, so a test
// only has to describe the calls it cares about.
export const unrouted = () => response({ detail: 'unrouted synthetic request' }, 500);

beforeEach(() => {
    state.host = document.createElement('div'); document.body.append(state.host);
    state.root = createRoot(state.host);
    api.setToken(`synthetic-${crypto.randomUUID()}`);
    state.requests = [];
    state.route = unrouted;
    globalThis.fetch = async (url, options = {}) => {
        const request = { path: new URL(url).pathname + new URL(url).search, method: options.method ?? 'GET',
            authorization: options.headers?.Authorization, body: options.body ? JSON.parse(options.body) : undefined };
        state.requests.push(request);
        return state.route(request);
    };
});
afterEach(async () => { await act(async () => state.root.unmount()); state.host.remove(); api.setToken(null); });
after(() => {
    globalThis.fetch = originalFetch; net.Socket.prototype.connect = originalConnect; dom.window.close();
    for (const [key, descriptor] of originalGlobals) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
    }
});

export const render = async (element) => { await act(async () => state.root.render(el(StrictMode, null, element))); };
export const text = () => state.host.textContent;
export const find = (selector) => state.host.querySelector(selector);
export const button = (label) => {
    const match = [...state.host.querySelectorAll('button')].find((node) => node.textContent.trim() === label);
    assert.ok(match, `Missing button: ${label}; DOM text: ${text()}`); return match;
};
export const click = async (label) => { await act(async () => button(label).click()); };
export const setInput = async (node, value) => {
    await act(async () => {
        Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, 'value').set.call(node, value);
        node.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    });
};
export const settle = async (ms = 20) => { await act(async () => { await sleep(ms); }); };
