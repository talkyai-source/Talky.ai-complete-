import assert from "node:assert/strict";
import fs from "node:fs";
import Module from "node:module";
import path from "node:path";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render } from "@testing-library/react";

// Same boundary stubs as navbar.test.tsx.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { usePathname: () => "/", useRouter: () => ({ push() {}, prefetch() {} }) };
    if (id.includes("providers/theme-provider")) return { useTheme: () => ({ theme: "light", setTheme() {}, toggleTheme() {} }) };
    if (id.includes("lib/auth-context")) return { useAuth: () => ({ accessToken: null, setToken() {}, status: "anonymous", user: null }) };
    return originalLoad.call(this, id, ...args);
};
let Navbar: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Navbar = require("./navbar").Navbar;
} finally {
    loader._load = originalLoad;
}

afterEach(() => cleanup());

const appDir = path.join(process.cwd(), "src", "app");
function routeExists(href: string): boolean {
    const segments = href.split(/[?#]/)[0].split("/").filter(Boolean);
    let dir = appDir;
    for (const seg of segments) {
        const exact = path.join(dir, seg);
        const dynamic = fs
            .readdirSync(dir, { withFileTypes: true })
            .filter((d) => d.isDirectory() && /^\[[^\]]+\]$/.test(d.name))
            .map((d) => path.join(dir, d.name))[0];
        const next = fs.existsSync(exact) ? exact : dynamic;
        if (!next) return false;
        dir = next;
    }
    return fs.existsSync(path.join(dir, "page.tsx"));
}

test("W5-T8 Documentation points to /docs in the desktop menu and in the phone menu", () => {
    const { container } = render(<Navbar />);
    const desktop = [...container.querySelectorAll<HTMLAnchorElement>("a.home-nav-link")].find((a) => a.textContent === "Documentation");
    assert.ok(desktop, "desktop menu entry");
    assert.equal(desktop.getAttribute("href"), "/docs");
    const phone = [...container.querySelectorAll<HTMLAnchorElement>("details.navbarMobileMenu a.home-mobile-link")].find((a) => a.textContent === "Documentation");
    assert.ok(phone, "phone menu entry");
    assert.equal(phone.getAttribute("href"), "/docs");
});

test("W5-T8 every navigation target is an existing route", () => {
    const { container } = render(<Navbar />);
    const hrefs = new Set([...container.querySelectorAll("a[href^='/']")].map((a) => a.getAttribute("href")!));
    assert.ok(hrefs.size > 10, "the menus were read");
    for (const href of hrefs) {
        if (href === "/") continue;
        assert.ok(routeExists(href), `${href} has no page`);
    }
});
