// The System Health page must not look healthy while the server health watch is red or silent.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, el, render, response, text, find, state, settle } from './bh-harness.mjs';

const detailed = (watch) => ({
    uptime_seconds: 7200, uptime_display: '2h', memory_usage_mb: 1024, memory_total_mb: 4096, memory_percent: 25,
    cpu_usage_percent: 5, disk_usage_percent: 91.5, disk_free_gb: 3.2, os_info: 'Linux 6', python_version: '3.12',
    version: 'abc', environment: 'production', providers: [], checked_at: '2026-11-20T00:00:00Z',
    last_updated: '2026-11-20T00:00:00Z', metrics_scope: 'Host as reported by this API process', health_watch: watch,
});
const watch = (extra) => ({ overall: 'ok', last_run_at: 1, age_seconds: 20, interval_seconds: 60, alerting: [], ...extra });
const draw = async (health) => { state.route = () => response(health); await render(el(C.HealthOverviewCards)); await settle(); };

test('an alerting watch turns the top of the page red and lists its checks', async () => {
    await draw(detailed(watch({ overall: 'alert', alerting: [
        { name: 'trunks', status: 'alert', sentence: '2 phone lines are not connected.' },
        { name: 'disk', status: 'alert', sentence: 'The server disk is nearly full.' }] })));
    const banner = find('[role="alert"].health-watch-banner');
    assert.ok(banner, 'a red banner is shown');
    assert.match(banner.textContent, /2 problems/);
    assert.match(banner.textContent, /2 phone lines are not connected\./);
    assert.match(banner.textContent, /The server disk is nearly full\./);
    assert.match(banner.getAttribute('style'), /accent-red/);
});

test('a quiet watch shows no red banner and no "has not reported" sentence; the disk percentage is shown', async () => {
    await draw(detailed(watch()));
    assert.equal(find('.health-watch-banner'), null);
    assert.doesNotMatch(text(), /has not reported/);
    assert.match(text(), /Disk Usage/);
    assert.match(text(), /91\.5%/);
});

test('an old result says the watch has not reported, in minutes', async () => {
    await draw(detailed(watch({ age_seconds: 7 * 60 + 5 })));
    assert.match(text(), /The health watch has not reported for 7 minutes/);
    assert.equal(find('[role="alert"].health-watch-banner'), null);
});

test('no run on record says it has not reported for more than 10 minutes', async () => {
    await draw(detailed(watch({ overall: 'unknown', last_run_at: null, age_seconds: null })));
    assert.match(text(), /The health watch has not reported for more than 10 minutes/);
});

test('a server from before this change sends no watch state: neither banner is shown', async () => {
    // The panel goes live on merge, the server on its next release.
    const old = detailed(undefined);
    delete old.health_watch;
    await draw(old);
    assert.equal(find('.health-watch-banner'), null);
    assert.doesNotMatch(text(), /has not reported/);
});

test('a server that sends the state as null (it could not be read) says the watch has not reported', async () => {
    await draw(detailed(null));
    assert.match(text(), /The health watch has not reported for more than 10 minutes/);
});
