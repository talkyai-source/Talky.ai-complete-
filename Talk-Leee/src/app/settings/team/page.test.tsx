import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { getURLFromRedirectError } from "next/dist/client/components/redirect";
import { isRedirectError } from "next/dist/client/components/redirect-error";
import SettingsTeamRedirectPage from "./page";

// There is one Team page, at /team: the member list, the role controls and,
// for admins, the invite form (QA F1-15, D4-2). Its tests are in
// src/app/team/page.test.tsx. /settings/team was a second copy of it; it now
// only sends people on, so links, bookmarks and anything already sent to a
// colleague keep working.

test("/settings/team sends people to /team instead of showing a second Team page", () => {
    let thrown: unknown = null;
    try {
        SettingsTeamRedirectPage();
    } catch (err) {
        thrown = err;
    }
    // redirect() ends rendering by throwing; anything else means the page rendered.
    if (!isRedirectError(thrown)) assert.fail("the page must redirect, not render");
    assert.equal(getURLFromRedirectError(thrown), "/team");
});

test("Settings links to the Team page at /team, not to the retired /settings/team", () => {
    const settings = readFileSync(path.join(process.cwd(), "src/app/settings/page.tsx"), "utf8");
    assert.match(settings, /href="\/team"/);
    assert.doesNotMatch(settings, /href="\/settings\/team"/);
});
