import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";

// isPublicPath is not exported, and for a signed-out visitor the fail-open
// branch below it returns the same response, so behaviour alone cannot tell
// "listed as public" from "let through by fail-open". This is a source guard:
// the two functions are cut out of src/proxy.ts and run as written.
function loadIsPublicPath(): (pathname: string) => boolean {
    const src = fs.readFileSync(path.join(process.cwd(), "src/proxy.ts"), "utf8");
    const grab = (name: string) => {
        const start = src.indexOf(`function ${name}(`);
        assert.ok(start >= 0, `${name} found in proxy.ts`);
        const end = src.indexOf("\n}\n", start);
        return src.slice(start, end + 3).replace(/\(pathname: string\)/, "(pathname)");
    };
    return new Function(`${grab("isPublicContactPath")}\n${grab("isPublicPath")}\nreturn isPublicPath;`)() as (p: string) => boolean;
}

test("T8: the legal pages are public by the list, in both slash forms", () => {
    const isPublicPath = loadIsPublicPath();
    for (const p of ["/privacy", "/privacy/", "/terms", "/terms/", "/accessibility", "/accessibility/"]) {
        assert.equal(isPublicPath(p), true, `${p} is not in the public list`);
    }
});

test("T9: signed-in pages are still not on the public list", () => {
    const isPublicPath = loadIsPublicPath();
    for (const p of ["/dashboard/", "/billing/", "/calls/", "/privacy-admin/", "/termsheet/", "/accessibility-x/"]) {
        assert.equal(isPublicPath(p), false, `${p} must not be public`);
    }
});
