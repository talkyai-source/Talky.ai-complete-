import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

// Actual component callback/effect code with a minimal hook scheduler. This is
// an asynchronous unit control, not browser or React-concurrency acceptance.
const bundle = await build({
    stdin: { contents: `export { UsageCostPage } from './src/pages/UsageCostPage';
                       export { api } from './src/lib/api';`,
        resolveDir: fileURLToPath(new URL('..', import.meta.url)) },
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
    define: { 'import.meta.env': '{}' }, jsx: 'automatic',
    plugins: [{ name: 'component-hook-scheduler', setup(build) {
        build.onResolve({ filter: /^react$/ }, (args) => args.importer.endsWith('UsageCostPage.tsx')
            ? { path: 'scoped-hooks', namespace: 'synthetic' } : undefined);
        build.onLoad({ filter: /.*/, namespace: 'synthetic' }, () => ({ contents: `
            export const useState = (...a) => globalThis.__op11Hooks.useState(...a);
            export const useRef = (...a) => globalThis.__op11Hooks.useRef(...a);
            export const useCallback = (fn) => fn;
            export const useEffect = (...a) => globalThis.__op11Hooks.useEffect(...a);` }));
    } }],
});
const loaded = { exports: {} };
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), loaded, loaded.exports);
const { UsageCostPage, api } = loaded.exports;
const settle = () => new Promise((resolve) => setImmediate(resolve));

for (const [latestFails, oldFails] of [[false, false], [true, false], [false, true]]) {
    test(`late old-period ${oldFails ? 'failure' : 'success'} cannot replace the current ${latestFails ? 'failure' : 'rows'}`, async () => {
        const values = [], setters = [], pending = [];
        let cursor = 0, cleanup;
        globalThis.__op11Hooks = {
            useState(initial) {
                const index = cursor++;
                if (!(index in values)) values[index] = initial;
                const set = (value) => { values[index] = typeof value === 'function' ? value(values[index]) : value; };
                setters[index] = set;
                return [values[index], set];
            },
            useRef(initial) {
                const index = cursor++;
                if (!(index in values)) values[index] = { current: initial };
                return values[index];
            },
            useEffect(effect) { cleanup?.(); cleanup = effect(); },
        };
        const previous = api.getUsageBreakdown;
        api.getUsageBreakdown = () => new Promise((resolve, reject) => pending.push({ resolve, reject }));
        try {
            UsageCostPage();
            assert.equal(pending.length, 1);
            setters[0]('2026-09-01');
            cursor = 0;
            UsageCostPage();
            assert.equal(pending.length, 2);
            pending[1].resolve(latestFails ? { error: { message: 'synthetic failure' } }
                : { data: { breakdown: [{ tenant_id: 'period-B', total_minutes: 2 }], monetary_note: 'B' } });
            await settle();
            if (oldFails) pending[0].reject(new Error('synthetic old failure'));
            else pending[0].resolve({ data: { breakdown: [{ tenant_id: 'period-A', total_minutes: 9 }], monetary_note: 'A' } });
            await settle();
            assert.deepEqual(values[2], latestFails ? [] : [{ tenant_id: 'period-B', total_minutes: 2 }]);
            if (latestFails) assert.match(values[4], /unavailable/);
            else assert.equal(values[5], 'B');
        } finally {
            cleanup?.();
            api.getUsageBreakdown = previous;
            delete globalThis.__op11Hooks;
        }
    });
}
