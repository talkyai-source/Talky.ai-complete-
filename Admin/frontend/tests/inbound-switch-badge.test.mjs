// The inbound admission badge shows what the SERVER last returned, never the unsaved draft.
import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { el, act, state, render, response, text, find, click, settle, setInput } from './bh-harness.mjs';

const built = await build({
    stdin: { contents: `export { InboundControlPage } from './src/pages/InboundControlPage';
        export { AuthProvider } from './src/lib/auth'; export { api } from './src/lib/api';
        export { MemoryRouter } from 'react-router-dom';`,
        resolveDir: fileURLToPath(new URL('..', import.meta.url)), loader: 'ts' },
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
    define: { 'import.meta.env': '{}' }, jsx: 'automatic', loader: { '.css': 'empty' },
});
const loaded = { exports: {} };
new Function('require', 'module', 'exports', built.outputFiles[0].text)(createRequire(import.meta.url), loaded, loaded.exports);
const O = loaded.exports;
test.beforeEach(() => O.api.setToken('synthetic-badge'));
test.afterEach(() => O.api.setToken(null));

const base = { inbound_enabled: true, recording_enabled: false, transfer_enabled: false, settlement_enabled: true, version: 1 };
const badge = () => find('#runtime-controls-heading').closest('.card-header').querySelector('.call-status-badge').textContent;
const NOT_SAVED = /Not saved yet: incoming calls are still enabled/;
function routeWith(server) {
    state.route = (request) => {
        if (request.path.endsWith('/auth/me')) return response({ id: '00000000-0000-4000-8000-000000006006', email: 'a@example.invalid', role: 'platform_admin' });
        if (request.path.endsWith('/admin/inbound/overview')) return response({ controls: server.current, active_assignments: 0, paused_assignments: 0, quarantined_assignments: 0, denied_last_24h: 0 });
        if (request.path.includes('/admin/inbound/assignments')) return response({ items: [], total: 0 });
        if (request.path.split('?')[0].endsWith('/admin/inbound/reassignments')) return response({ items: [], has_more: false, next_cursor: null });
        if (request.method === 'PATCH') { server.current = { ...server.current, ...request.body, version: 2 }; return response(server.current); }
        if (request.path.split('?')[0].endsWith('/admin/tenants')) return response([]);
        return response({ detail: 'unrouted synthetic request' }, 500);
    };
}
const mount = async () => { await render(el(O.AuthProvider, null, el(O.MemoryRouter, null, el(O.InboundControlPage)))); await settle(); };

test('moving the admission switch does not change the badge; the page says the change is not saved', async () => {
    routeWith({ current: base });
    await mount();
    assert.equal(badge(), 'inbound enabled');
    assert.doesNotMatch(text(), /Not saved yet/);
    await act(async () => find('.inbound-switch input').click());
    assert.equal(badge(), 'inbound enabled');
    assert.match(text(), NOT_SAVED);
});

test('after a save the server answers disabled: the badge follows it and the not-saved words are gone', async () => {
    routeWith({ current: base });
    await mount();
    await act(async () => find('.inbound-switch input').click());
    assert.match(text(), NOT_SAVED);
    await setInput(find('#control-reason'), 'Synthetic emergency stop');
    await click('Apply version 2'); await settle();
    assert.equal(badge(), 'inbound disabled');
    assert.doesNotMatch(text(), /Not saved yet/);
});
