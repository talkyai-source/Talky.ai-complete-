import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Structural guard for the root layout's mount order.
 *
 * SignupPopup reads the sign-in state with useAuth(), which throws on the
 * client when no AuthProvider is above it (src/lib/auth-context.tsx). The
 * provider comes from <AppProviders>, so the popup must be rendered inside
 * that element. It once sat after it and could not see the state at all.
 * If this test breaks, move <SignupPopup /> back inside <AppProviders>.
 */
const LAYOUT = path.join(path.dirname(fileURLToPath(import.meta.url)), "layout.tsx");

test("SignupPopup is mounted inside AppProviders in the root layout, exactly once", () => {
    const src = readFileSync(LAYOUT, "utf8");
    const open = src.indexOf("<AppProviders>");
    const close = src.indexOf("</AppProviders>");
    const popup = src.indexOf("<SignupPopup />");
    assert.ok(open !== -1 && close !== -1 && open < close, "the layout renders <AppProviders>...</AppProviders>");
    assert.equal(src.split("<SignupPopup />").length - 1, 1, "<SignupPopup /> is rendered exactly once");
    assert.ok(popup > open && popup < close, "<SignupPopup /> sits between <AppProviders> and </AppProviders>");
});
