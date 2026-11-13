/**
 * A COPY GUARD, NOT A BEHAVIOUR TEST — say so plainly.
 *
 * This source guard is supplemented by the actual-render regressions in
 * session-revocation.test.tsx. It protects wording only, not server revocation.
 *
 * THE FACTS THE COPY HAS TO MATCH
 * -------------------------------
 * The old test trusted a stale sessions.py docstring and required the opposite
 * of current behavior. refresh.bind_refresh_to_session rejects a revoked sid;
 * get_current_user validates the live sid for bearer and cookie requests.
 * The legacy body-token TTL is configurable and its15-minute ceiling remains
 * behind AUTH_SHORT_LIVED_TOKENS_REQUIRED, so this page must not promise that
 * every deployment has completed that cutover. No backend behavior is changed.
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
        "Describe the next refresh/request, not an instantaneous browser transition",
    );
    assert.doesNotMatch(COPY, /revok\w*[^.]{0,40}immediately/i);
});

test("the sessions section states the next-refresh sign-out in always-visible text", () => {
    // §8: an essential warning must not live only inside a tooltip, so this has
    // to be in `description`, which is always on screen — not in `tip`.
    const description = COPY.match(/description="Everywhere your account is currently signed in\.[^"]*"/);
    assert.ok(description, "the Active sessions description must still be there");
    assert.match(description[0], /revoking signs that device out/i);
    assert.match(description[0], /next token refresh/i);
    assert.doesNotMatch(description[0], /does not sign|change your password|within 15 minutes/i);
});

test("the tooltip states revoked-session rejection without obsolete schema claims", () => {
    const tip = COPY.match(/tip="Revoked sessions[^"]*"/);
    assert.ok(tip, "the Active sessions tip must still be there");
    assert.match(tip[0], /cannot refresh/i);
    assert.match(tip[0], /new requests/i);
    assert.doesNotMatch(tip[0], /7 days|not linked|cannot invalidate|keeps access|within 15 minutes/i);

    // No apology, and no mechanism the platform does not have.
    assert.doesNotMatch(tip[0], /\b(sorry|apolog|unfortunately|we're working on)/i);
});

test("the related password copy describes observable sign-out without an unactivated TTL promise", () => {
    assert.match(
        COPY,
        /Changing your password signs out your other sessions when they next refresh or make a request\./,
    );
    assert.doesNotMatch(COPY, /each within 15 minutes/i);
});
