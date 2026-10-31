// Actual ReactDOM and media events; byte decoding is covered by native browser acceptance.
import assert from 'node:assert/strict';
import test from 'node:test';
import { C, api, el, act, dom, render, find, click, settle, deferred } from './bh-harness.mjs';

const urls = [], revoked = [], downloads = [];
const originalCreate = URL.createObjectURL, originalRevoke = URL.revokeObjectURL;
const originalClick = dom.window.HTMLAnchorElement.prototype.click;
const blob = () => new Blob(['owned audio bytes'], {type:'audio/wav'});
const player = (load, id = 'recording:owned-A') => el(C.AdminMediaPlayer,
    {resourceId:id, filenameBase:'owned-audio', fallbackExtension:'wav', load});
const errorEvent = async audio => {
    Object.defineProperty(audio, 'error', {configurable:true, value:{code:3}});
    await act(async () => audio.dispatchEvent(new dom.window.Event('error')));
    await settle();
};
const download = async () => { await act(async () => find('[aria-label="Download audio"]').click()); };

test.beforeEach(() => {
    urls.length = 0; revoked.length = 0; downloads.length = 0;
    URL.createObjectURL = () => { const url = `blob:owned-${urls.length}`; urls.push(url); return url; };
    URL.revokeObjectURL = url => revoked.push(url);
    dom.window.HTMLAnchorElement.prototype.click = function () { downloads.push({url:this.href, name:this.download}); };
});
test.after(() => {
    URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke;
    dom.window.HTMLAnchorElement.prototype.click = originalClick;
});

test('native decoder failure is actionable and never automatically refetches', async () => {
    let loads = 0;
    await render(player(async () => {loads += 1; return blob();}));
    await click('Play'); await errorEvent(find('audio'));
    assert.match(find('[role="alert"]')?.textContent ?? '', /could not be played/i);
    await settle(); assert.equal(loads, 1);
    assert.ok([...document.querySelectorAll('button')].some(node => node.textContent.trim() === 'Reload audio'));
});

test('failed playback does not prevent downloading the already fetched bytes', async () => {
    let loads = 0;
    await render(player(async () => {loads += 1; return blob();}));
    await click('Play'); await errorEvent(find('audio')); await download();
    assert.equal(loads, 1); assert.deepEqual(downloads, [{url:urls[0], name:'owned-audio.wav'}]);
    assert.match(find('[role="alert"]')?.textContent ?? '', /could not be played/i);
});

test('explicit reload retires failed URL, fetches once and ignores old media events', async () => {
    let loads = 0;
    await render(player(async () => {loads += 1; return blob();}));
    await click('Play'); const retired = find('audio'); await errorEvent(retired);
    await click('Reload audio'); await settle();
    assert.equal(loads, 2); assert.deepEqual(revoked, [urls[0]]);
    assert.equal(find('audio').getAttribute('src'), urls[1]); assert.equal(find('[role="alert"]'), null);
    await errorEvent(retired); assert.equal(find('[role="alert"]'), null);
    await download(); assert.deepEqual(downloads, [{url:urls[1], name:'owned-audio.wav'}]);
    await render(el('p', null, 'Left media')); assert.deepEqual(revoked, urls);
});

test('failed explicit reload reports its fetch error and cannot download old bytes', async () => {
    let loads = 0;
    await render(player(async () => {loads += 1; if (loads > 1) throw new Error('Owned audio unavailable'); return blob();}));
    await click('Play'); await errorEvent(find('audio')); await click('Reload audio');
    assert.match(find('[role="alert"]')?.textContent ?? '', /Owned audio unavailable/);
    assert.equal(find('audio'), null); assert.deepEqual(revoked, urls); assert.equal(loads, 2);
    await download(); assert.deepEqual(downloads, []);
});

for (const retirement of ['resource', 'session', 'unmount']) {
    test(`late explicit reload cannot publish after ${retirement}`, async () => {
        let loads = 0; const pending = deferred();
        const load = async () => {loads += 1; return loads === 1 ? blob() : pending.promise;};
        await render(player(load)); await click('Play'); await errorEvent(find('audio')); await click('Reload audio');
        if (retirement === 'resource') await render(player(async () => blob(), 'recording:owned-B'));
        if (retirement === 'session') await act(async () => api.setToken('new-owner'));
        if (retirement === 'unmount') await render(el('p', null, 'Left media'));
        await act(async () => pending.resolve(blob())); await settle();
        assert.equal(find('audio'), null); assert.deepEqual(downloads, []);
        assert.equal(urls.length, 1); assert.deepEqual(revoked, urls);
    });
}
