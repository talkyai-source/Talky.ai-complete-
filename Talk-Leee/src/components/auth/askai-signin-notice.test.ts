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
        // The homepage is only ever chosen from the flag, never from a URL,
        // and never for a white-label admin.
        assert.match(source, /fromAskAi\s*&&\s*\w+\.role\s*!==\s*"white_label_admin"\s*\?\s*"\/"\s*:\s*postAuthDashboard\(/);
        assert.doesNotMatch(source, /\.get\(\s*["']next["']\s*\)/);
    });
}

test("the Ask AI notice has no dismiss control", () => {
    const source = readFileSync(path.join(here, "askai-signin-notice.tsx"), "utf8");
    assert.doesNotMatch(source, /<button/);
    assert.doesNotMatch(source, /aria-label="Dismiss"/);
    assert.match(source, /Sign in to talk<\/span>\s*\{" "\}\s*<span[^>]*>to the AI agent/);
});
