import assert from 'node:assert/strict';
import test from 'node:test';
import { validateAdminBuildConfig } from '../build-config.ts';

test('release builds require an explicit deployed API', () => {
  for (const base of [undefined, '', ' ', 'http://localhost:8000/api/v1', 'https://127.0.0.1/api/v1',
    'https://[::1]/api/v1', 'https://localhost/api/v1', 'https://dev.local/api/v1',
    '//api.example.invalid/api/v1', '/api', 'https://api.example.invalid',
    'https://user:secret@api.example.invalid/api/v1', 'https://api.example.invalid/api/v1?token=test',
    'https://api.example.invalid/api/v1#fragment']) {
    assert.throws(() => validateAdminBuildConfig({ VITE_API_BASE_URL: base }), /VITE_API_BASE_URL/);
  }
});

test('release builds reject either authentication bypass even with a valid API', () => {
  for (const flag of ['VITE_USE_DUMMY_AUTH', 'VITE_ADMIN_DEV_MODE']) {
    for (const value of ['true', 'TRUE', ' true ']) {
      assert.throws(() => validateAdminBuildConfig({ VITE_API_BASE_URL: '/api/v1', [flag]: value }), new RegExp(flag));
    }
  }
});

test('explicit HTTPS and same-origin API routes are supported without bypasses', () => {
  assert.equal(validateAdminBuildConfig({ VITE_API_BASE_URL: 'https://api.example.invalid/api/v1/' }), 'https://api.example.invalid/api/v1');
  assert.equal(validateAdminBuildConfig({ VITE_API_BASE_URL: '/api/v1', VITE_USE_DUMMY_AUTH: 'false', VITE_ADMIN_DEV_MODE: 'false' }), '/api/v1');
});
