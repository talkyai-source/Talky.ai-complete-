import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { ASKAI_SIGNIN_FLAG, clearAskAiSignInFlag, hasAskAiSignInFlag } from "@/components/auth/askai-signin-notice";

const here = path.dirname(fileURLToPath(import.meta.url));
const authDir = path.join(here, "..", "..", "app", "auth");

test("hasAskAiSignInFlag is true only for the literal '1'", () => {
    sessionStorage.removeItem(ASKAI_SIGNIN_FLAG);
    assert.equal(hasAskAiSignInFlag(), false);
    sessionStorage.setItem(ASKAI_SIGNIN_FLAG, "yes");
    assert.equal(hasAskAiSignInFlag(), false);
    sessionStorage.setItem(ASKAI_SIGNIN_FLAG, "1");
    assert.equal(hasAskAiSignInFlag(), true);
    clearAskAiSignInFlag();
    assert.equal(hasAskAiSignInFlag(), false);
});

for (const file of ["login/login-client.tsx", "register/register-client.tsx"]) {
    test(`${file} reads the Ask AI flag before clearing it and gates the homepage return on it`, () => {
        const source = readFileSync(path.join(authDir, file), "utf8");
        const read = source.indexOf("hasAskAiSignInFlag()");
        const clear = source.indexOf("clearAskAiSignInFlag()");
        assert.ok(read >= 0, "reads the flag");
        assert.ok(clear >= 0, "clears the flag");
        assert.ok(read < clear, "the flag is read before it is cleared");
        // The Ask AI homepage shortcut stays flag-derived and excludes white-label admins.
        // Only login's fallback may consume a return URL, through the validated helper.
        const precedence = /fromAskAi\s*&&\s*\w+\.role\s*!==\s*"white_label_admin"\s*\?\s*"\/"\s*:\s*postAuthDashboard\(/;
        if (file === "login/login-client.tsx") assert.match(source, precedence);
        else {
            // Sign-up: the same precedence lives in one pure function, which the page calls with the flag it read.
            assert.match(source, /destinationAfterSignup\(\s*response\.role\s*,\s*next\s*,\s*fromAskAi\s*\)/);
            assert.match(readFileSync(path.join(authDir, "register", "register-next.ts"), "utf8"),
                /fromAskAi\s*&&\s*role\s*!==\s*"white_label_admin"\s*\?\s*"\/"\s*:\s*postAuthDashboard\(role,\s*next\)/);
        }
        if (file === "login/login-client.tsx") {
            assert.match(source, /import\s*\{\s*postAuthDashboard\s*\}\s*from\s*["']@\/lib\/post-auth-navigation["']/);
            const nextReads = source.match(/\.get\(\s*["']next["']\s*\)/g) ?? [];
            assert.equal(nextReads.length, 1, "the return target is read only for the validated helper");
            assert.match(source, /postAuthDashboard\(\s*\w+\.role\s*,\s*searchParams\?\.get\(\s*["']next["']\s*\)\s*\)/);
            assert.doesNotMatch(source, /(?:router\.(?:push|replace)|window\.location\.(?:assign|replace))\(\s*searchParams\??\.get\(\s*["']next["']\s*\)/);
        } else {
            // Sign-up: register-next.ts is the only file that reads `next` from the address; the page gets it
            // from nextFromAddress and passes it only to postAuthDashboard / destinationAfterSignup / signInHref
            // (the full rule is in lib/post-auth-navigation.test.ts), never to router.* or location.*.
            assert.doesNotMatch(source, /\.get\(\s*["']next["']\s*\)/);
            assert.match(source, /nextFromAddress\(\s*useSearchParams\(\)\s*\)/);
            const nextReads = readFileSync(path.join(authDir, "register", "register-next.ts"), "utf8").match(/\.get\(\s*["']next["']\s*\)/g) ?? [];
            assert.equal(nextReads.length, 1, "register-next.ts reads `next` exactly once");
            assert.doesNotMatch(source, /(?:router\.(?:push|replace)|window\.location\.(?:assign|replace)|onNavigate)\(\s*next\b/);
        }
    });
}

test("the Ask AI notice has no dismiss control", () => {
    const source = readFileSync(path.join(here, "askai-signin-notice.tsx"), "utf8");
    assert.doesNotMatch(source, /<button/);
    assert.doesNotMatch(source, /aria-label="Dismiss"/);
    assert.match(source, /Sign in to talk<\/span>\s*\{" "\}\s*<span[^>]*>to the AI agent/);
});
