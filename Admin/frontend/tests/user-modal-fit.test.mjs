import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

// Sign-off 1.3: at 360x740 the Add user dialog was 830 px tall and its top was
// cut off above the window. This is a source guard; the behaviour is measured
// in the browser at the eight sizes.
const css = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const page = readFileSync(new URL('../src/pages/UsersPage.tsx', import.meta.url), 'utf8');

function rule(selector) {
    const start = css.indexOf(`\n${selector} {`);
    assert.equal(start >= 0, true, `${selector} rule is present`);
    return css.slice(start, css.indexOf('}', start));
}

test('a user dialog is never taller than the window and scrolls inside itself', () => {
    const body = rule('.user-modal');
    assert.match(body, /max-height:\s*90vh;/);
    assert.match(body, /max-height:\s*90dvh;/);
    assert.match(body, /overflow-y:\s*auto;/);
});

test('the Add user and Edit user dialogs use that rule', () => {
    assert.match(page, /aria-labelledby="add-user-title"[^>]*className="modal-content user-modal"/);
    assert.match(page, /aria-labelledby="edit-user-title"[^>]*className="modal-content user-modal"/);
});
