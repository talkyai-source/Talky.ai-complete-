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

// Audit of 28 Nov 2026: with "Documentation" the bar is about 124 px wider, so the
// horizontal menu starts at 1200 px, not 1060 px; below that the drawer shows. The
// browser cannot be measured here, so this reads the source: one breakpoint everywhere.
test("W5-T12 the horizontal menu starts at 1200px in the component and in the stylesheet", () => {
    const navbar = fs.readFileSync(path.join(process.cwd(), "src/components/home/navbar.tsx"), "utf8");
    const css = fs.readFileSync(path.join(process.cwd(), "src/app/globals.css"), "utf8");
    assert.doesNotMatch(navbar, /min-\[1060px\]/);
    assert.equal(navbar.split(/\r?\n/).filter((line) => line.includes("min-[1200px]")).length, 7, "the seven lines that switch drawer and bar");
    assert.doesNotMatch(css, /min-width:\s*1060px/);
    assert.match(css, /@media \(min-width: 1200px\)\s*\{\s*\.home-navbar-fixed/);
});
