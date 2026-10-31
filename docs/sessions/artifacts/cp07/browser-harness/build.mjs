import { createRequire } from 'node:module';
import { resolve } from 'node:path';
const root = resolve(process.cwd());
const require = createRequire(resolve(root, 'Talk-Leee/package.json'));
const esbuild = require('esbuild');
await esbuild.build({
  entryPoints: [resolve(root, 'tmp/cp07-browser/harness.tsx')],
  bundle: true, outfile: resolve(root, 'tmp/cp07-browser/bundle.js'),
  platform: 'browser', format: 'iife', jsx: 'automatic',
  nodePaths: [resolve(root, 'Talk-Leee/node_modules')],
  alias: {'@/lib/backend-api': resolve(root, 'tmp/cp07-browser/backend-api.ts'), '@/lib/auth-context': resolve(root, 'tmp/cp07-browser/auth-context.ts'), '@': resolve(root, 'Talk-Leee/src')},
  define: {'process.env.NODE_ENV':'"development"'},
  metafile: true,
}).then(async ({metafile}) => {
  const {writeFile} = await import('node:fs/promises');
  await writeFile(resolve(root, 'tmp/cp07-browser/bundle-inputs.json'), JSON.stringify(Object.keys(metafile.inputs), null, 2));
});
