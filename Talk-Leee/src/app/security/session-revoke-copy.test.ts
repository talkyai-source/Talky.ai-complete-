/**
 * A COPY GUARD, NOT A BEHAVIOUR TEST — say so plainly.
 *
 * This reads the source of security/page.tsx and asserts on its wording. That
 * proves nothing about what the page does; rendering it needs auth context, a
 * dashboard shell and three live endpoints, and none of that would tell us any
 * more about a sentence than reading the sentence does. What it is for is
 * narrow and worth having: the page previously told users "Revoking ends that
 * session immediately", which is false, and a claim like that comes back the
 * next time someone tidies the copy.
 *
 * THE FACTS THE COPY HAS TO MATCH
 * -------------------------------
 * backend/app/api/v1/endpoints/sessions.py:213-249 documents it: DELETE
 * /sessions/{id} revokes the `security_sessions` row and the legacy `talky_sid`
 * session, and cannot touch that device's refresh token, because `refresh_tokens`
 * has no session_id and `security_sessions` has no family_id. The device keeps
 * working and rotates its token on a rolling window of
 * REFRESH_TOKEN_LIFETIME_DAYS = 7 (backend/app/core/security/refresh_tokens.py:25).
 *
 * The remedy named in the copy is real and is checked here too:
 * backend/app/api/v1/endpoints/auth/password.py:131 calls
 * revoke_all_user_refresh_tokens on a password change, so every other device
 * has to re-authenticate once its access token expires — 15 minutes at most.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const SOURCE = readFileSync(
    path.join(process.cwd(), "src/app/security/page.tsx"),
    "utf8",
);

/** Only the strings a user reads — comments in this file explain the change and
 *  legitimately quote the old wording. */
const COPY = SOURCE.replace(/\{\/\*[\s\S]*?\*\/\}/g, "").replace(/\/\*[\s\S]*?\*\//g, "");

test("the sessions section no longer claims revoking ends the session immediately", () => {
    assert.doesNotMatch(
        COPY,
        /ends that session immediately/i,
        "the backend cannot revoke that device's refresh token — see sessions.py:213-249",
    );
    assert.doesNotMatch(COPY, /revok\w*[^.]{0,40}immediately/i);
});

test("the sessions section states, in the always-visible text, that the device is not signed out", () => {
    // §8: an essential warning must not live only inside a tooltip, so this has
    // to be in `description`, which is always on screen — not in `tip`.
    const description = COPY.match(/description="Everywhere your account is currently signed in\.[^"]*"/);
    assert.ok(description, "the Active sessions description must still be there");
    assert.match(description[0], /does not sign that device out/i);
    assert.match(description[0], /change your password/i);
});

test("the tooltip states the rolling window and the remedy in plain language", () => {
    const tip = COPY.match(/tip="Revoking removes the entry[^"]*"/);
    assert.ok(tip, "the Active sessions tip must still be there");

    // The window a revoked device keeps renewing for. REFRESH_TOKEN_LIFETIME_DAYS = 7.
    assert.match(tip[0], /7 days/);
    // What the revoke genuinely does do, so it does not read as a no-op.
    assert.match(tip[0], /removes the entry from this list and ends its server-side session/i);
    // Why it cannot do more — the fact, not an apology and not a mechanism.
    assert.match(tip[0], /not linked in the database/i);
    // The route that actually works, with its own window.
    assert.match(tip[0], /changing your password/i);
    assert.match(tip[0], /15 minutes/);

    // No apology, and no mechanism the platform does not have.
    assert.doesNotMatch(tip[0], /\b(sorry|apolog|unfortunately|we're working on)/i);
});

test("the password form states the window rather than implying an instant sign-out", () => {
    // auth/password.py:131 revokes every refresh token, but an access token
    // already in hand stays valid until it expires.
    assert.match(
        COPY,
        /Changing your password signs out all of your other sessions, each within 15 minutes\./,
    );
});
