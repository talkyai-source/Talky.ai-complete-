// QA F1-33: the "Actions Log" (a Coming Soon placeholder) and "System Config" (a provider switch with no
// confirmation) pages were never routed or linked, but their files stayed in the tree. They are gone, and
// nothing may route or link to them again.
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';
import { ADMIN_ROUTES, canAccessAdminRoute } from '../src/lib/admin-routes.ts';

const source = (path) => new URL(`../src/${path}`, import.meta.url);

test('the placeholder pages no longer exist in the tree', () => {
    for (const file of ['ActionsLogPage.tsx', 'SystemConfigPage.tsx']) {
        assert.equal(existsSync(source(`pages/${file}`)), false, `${file} must stay deleted`);
    }
});

test('no menu entry or route reaches the removed pages', () => {
    const entries = ADMIN_ROUTES.map((rule) => `${rule.id} ${rule.path} ${rule.label}`).join('\n');
    assert.doesNotMatch(entries, /actions-log|system-config|Actions Log|System Config/i);
    for (const path of ['/actions-log', '/system-config']) {
        assert.equal(canAccessAdminRoute(path, 'platform_admin'), false, path);
    }
    assert.doesNotMatch(readFileSync(source('App.tsx'), 'utf8'), /ActionsLogPage|SystemConfigPage|actions-log|system-config/);
});
