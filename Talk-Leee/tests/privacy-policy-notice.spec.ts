import { expect, test, type Page } from "@playwright/test";
import { E2E_AUTH_TOKEN, E2E_USER, mockApi, mockDashboardApi, mockPageApis, signInAsE2eUser } from "./support/session";
import { PRIVACY_POLICY_DATES } from "../src/lib/privacy-policy-dates";

/*
 * The Privacy Policy change notice (src/components/legal/privacy-policy-notice.tsx):
 * shown at the top of every signed-in page from the policy's start date until
 * its Effective Date, 30 days later (both read from src/lib/privacy-policy-dates.ts,
 * never typed in here). Measured on every signed-in page
 * that can be opened without a backend, at every supported width, in both
 * themes, with the sidebar expanded and collapsed; stacked with the suspension
 * and offline banners; with the sidebar drawer open; and its behaviour
 * (dates, Dismiss, roles, link, keyboard).
 *
 * Every /api/v1 request is answered here. Run it against a build made with
 * NEXT_PUBLIC_API_BASE_URL=http://localhost:3100/api/v1, served on port 3100
 * (localhost, not 127.0.0.1: the page and its API must share one origin).
 * The page clock is fixed, so the result does not depend on the day it runs.
 *
 * Not opened here: /admin/* and /white-label/dashboard (the server asks the
 * backend for the role and answers /403 without one) and the partner pages
 * under /white-label/{partner}/ (they need a partner session from the
 * backend). Those pages use the same layout; the roles are covered by the
 * unit tests.
 */

const ORIGIN = "http://localhost:3100";
test.use({ baseURL: ORIGIN });
test.setTimeout(900_000);

const SIZES: Array<[number, number]> = [
    [320, 568], [360, 740], [375, 667], [390, 844], [414, 896], [768, 1024], [820, 1180],
    [1024, 768], [1280, 800], [1366, 768], [1440, 900], [1536, 864], [1920, 1080],
];
const THEMES = ["light", "dark"] as const;
type Theme = (typeof THEMES)[number];
type Sidebar = "drawer" | "expanded" | "collapsed";
const sidebarsFor = (width: number): Sidebar[] => (width >= 1024 ? ["expanded", "collapsed"] : ["drawer"]);

/** "2026-12-20" -> "20 December 2026", worked out here without the code under test. */
const printed = (isoDate: string) => new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(`${isoDate}T00:00:00Z`));
const START_MS = Date.parse(`${PRIVACY_POLICY_DATES.noticeStart}T00:00:00Z`);
const EFFECTIVE_MS = Date.parse(`${PRIVACY_POLICY_DATES.effectiveDate}T00:00:00Z`);
const at = (ms: number) => new Date(ms).toISOString();
const TEXT = `We're updating our Privacy Policy on ${printed(PRIVACY_POLICY_DATES.effectiveDate)}.`;
const LINK = "Read the Privacy Policy";
const NOTICE = "[data-privacy-policy-notice]";
// Inside the notice's 30 days.
const INSIDE = at(START_MS + 5.5 * 86_400_000);

// Every signed-in page that opens without a backend. /email, /meetings, /reminders, /assistant and
// /assistant/meetings are not here: those pages always answer "page not found" (their features are off).
const ROUTES = [
    "/dashboard", "/campaigns", "/campaigns/new", "/campaigns/camp-001", "/campaigns/camp-001/edit", "/contacts", "/contacts/do-not-call",
    "/inbound-campaigns", "/inbound-campaigns/new", "/calls", "/calls/call-001", "/recordings", "/analytics", "/connectors", "/ai-options",
    "/billing", "/billing/plans", "/billing/invoices", "/settings", "/team", "/security", "/support", "/support/tickets", "/notifications",
    "/assistant/actions",
];

type Me = Record<string, unknown>;
type Options = { theme?: Theme; sidebar?: Sidebar; me?: Me; now?: string };

async function openApp(page: Page, options: Options = {}) {
    // Registered first, so it runs last: whatever no mock answered gets the server's own "not found".
    await page.route((url) => /\/api\/v1(?:\/|$)/.test(url.pathname), async (route) => {
        if (route.request().resourceType() === "document") return route.fallback();
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Not Found" }) });
    });
    await signInAsE2eUser(page);
    await page.context().addCookies([{ name: "talklee_auth_token", value: E2E_AUTH_TOKEN, url: ORIGIN }]);
    await mockDashboardApi(page);
    await mockPageApis(page);
    const me = { ...E2E_USER, ...(options.me ?? {}) };
    await mockApi(page, [
        { path: "/auth/me", body: me },
        { path: "/events", body: { items: [], next_cursor: null, tenant_id: me.tenant_id ?? null, user_id: me.id } },
        { path: "/alerts", body: { items: [], next_cursor: null, tenant_id: me.tenant_id ?? null, user_id: me.id } },
    ]);
    await page.addInitScript(([theme, collapsed]) => {
        try {
            window.localStorage.setItem("talklee.theme", theme as string);
            window.localStorage.setItem("talklee.sidebar.state.v1", JSON.stringify({ collapsed, mobileOpen: false }));
        } catch { /* storage unavailable */ }
        // Pages opened with partly answered reads raise their own error messages (toasts). They are not
        // what is measured here and would lie over the top of the page, so they are kept off screen.
        const hide = () => {
            const style = document.createElement("style");
            style.textContent = '[role="status"][aria-label$=" notification"],[role="alert"][aria-label$=" notification"]{display:none !important}';
            document.documentElement.appendChild(style);
        };
        if (document.documentElement) hide(); else document.addEventListener("DOMContentLoaded", hide);
    }, [options.theme ?? "light", options.sidebar === "collapsed"] as const);
    await page.clock.setFixedTime(new Date(options.now ?? INSIDE));
}

type NoticeReading = {
    found: boolean; text: string; whole: string; textCut: boolean; lines: number; insideWidth: boolean; inView: boolean; onTop: boolean;
    link: { inside: boolean; reachable: boolean; href: string | null }; dismiss: { inside: boolean; reachable: boolean };
    partsOverlap: boolean; neighboursOverlap: string[]; horizontalScroll: boolean; rect: number[];
};

/** Reads the notice as it stands on the page (run in the page). */
function readNotice(): NoticeReading {
    const empty: NoticeReading = { found: false, text: "", whole: "", textCut: false, lines: 0, insideWidth: false, inView: false, onTop: false, link: { inside: false, reachable: false, href: null }, dismiss: { inside: false, reachable: false }, partsOverlap: false, neighboursOverlap: [], horizontalScroll: false, rect: [] };
    const notice = document.querySelector("[data-privacy-policy-notice]") as HTMLElement | null;
    if (!notice) return empty;
    notice.scrollIntoView({ block: "nearest" });
    const r = notice.getBoundingClientRect();
    const p = notice.querySelector("p") as HTMLElement;
    const link = notice.querySelector("a") as HTMLElement;
    const button = notice.querySelector("button") as HTMLElement;
    const within = (inner: DOMRect, outer: DOMRect) => inner.left >= outer.left - 0.5 && inner.right <= outer.right + 0.5 && inner.top >= outer.top - 0.5 && inner.bottom <= outer.bottom + 0.5;
    const crosses = (a: DOMRect, b: DOMRect) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1;
    const reachable = (el: HTMLElement) => { const c = el.getBoundingClientRect(); const top = document.elementFromPoint(c.left + c.width / 2, c.top + c.height / 2); return Boolean(top && el.contains(top)); };
    const parts = [p, link, button].map((el) => el.getBoundingClientRect());
    const partsOverlap = crosses(parts[0], parts[1]) || crosses(parts[0], parts[2]) || crosses(parts[1], parts[2]);
    // Everything that sits beside it in the page: the other banners, the page content, the header.
    const neighbours: Element[] = [...Array.from(notice.parentElement?.children ?? []).filter((el) => el !== notice), ...Array.from(document.querySelectorAll("header"))];
    const neighboursOverlap = neighbours.filter((el) => { const o = el.getBoundingClientRect(); return o.width > 1 && o.height > 1 && crosses(r, o); }).map((el) => `${el.tagName.toLowerCase()}:${(el.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, 40)}`);
    const textRange = document.createRange();
    textRange.selectNodeContents(p);
    const pr = p.getBoundingClientRect();
    return {
        found: true, text: p.textContent ?? "", whole: notice.textContent ?? "",
        textCut: p.scrollWidth > p.clientWidth + 1 || p.scrollHeight > p.clientHeight + 1 || !within(textRange.getBoundingClientRect(), pr) || getComputedStyle(p).textOverflow === "ellipsis",
        lines: Math.round(pr.height / parseFloat(getComputedStyle(p).lineHeight)),
        insideWidth: r.left >= -0.5 && r.right <= innerWidth + 0.5, inView: r.top >= -0.5 && r.bottom <= innerHeight + 0.5,
        onTop: reachable(p),
        link: { inside: within(parts[1], r), reachable: reachable(link), href: link.getAttribute("href") },
        dismiss: { inside: within(parts[2], r), reachable: reachable(button) },
        partsOverlap, neighboursOverlap, horizontalScroll: document.documentElement.scrollWidth > innerWidth + 1,
        rect: [r.left, r.top, r.right, r.bottom].map(Math.round),
    };
}

function expectNoticeFits(reading: NoticeReading, where: string) {
    expect(reading.found, `${where}: the notice is shown`).toBe(true);
    expect(reading.text, `${where}: words`).toBe(TEXT);
    expect(reading.whole, `${where}: nothing else in it`).toBe(`${TEXT}${LINK}Dismiss`);
    expect(reading.textCut, `${where}: text cut off`).toBe(false);
    expect(reading.insideWidth, `${where}: inside the device window ${JSON.stringify(reading.rect)}`).toBe(true);
    expect(reading.inView, `${where}: fully visible ${JSON.stringify(reading.rect)}`).toBe(true);
    expect(reading.onTop, `${where}: not covered`).toBe(true);
    expect(reading.link, `${where}: link`).toEqual({ inside: true, reachable: true, href: "/privacy" });
    expect(reading.dismiss, `${where}: Dismiss`).toEqual({ inside: true, reachable: true });
    expect(reading.partsOverlap, `${where}: its text, link and button overlap`).toBe(false);
    expect(reading.neighboursOverlap, `${where}: overlaps`).toEqual([]);
    expect(reading.horizontalScroll, `${where}: horizontal scroll`).toBe(false);
}

async function openRoute(page: Page, route: string, timeout = 30_000) {
    await page.goto(route, { waitUntil: "domcontentloaded" });
    await page.locator(NOTICE).waitFor({ timeout });
    await page.mouse.move(1, 1);
    await page.waitForTimeout(350);
}

// ───────────────────────── every signed-in page, every width / theme / sidebar ─────────────────────────

for (const [width, height] of SIZES) {
    for (const theme of THEMES) {
        for (const sidebar of sidebarsFor(width)) {
            test(`notice ${width}x${height} ${theme} sidebar ${sidebar}: on every signed-in page`, async ({ page }) => {
                await page.setViewportSize({ width, height });
                await openApp(page, { theme, sidebar });
                const lines = new Set<number>();
                const missing: string[] = [];
                for (const route of ROUTES) {
                    try {
                        await openRoute(page, route, 12_000);
                    } catch {
                        missing.push(`${route} (ended at ${new URL(page.url()).pathname})`);
                        continue;
                    }
                    expect(new URL(page.url()).pathname.replace(/\/$/, ""), `${route}: stayed on the page`).toBe(route);
                    expect(await page.evaluate(() => document.documentElement.classList.contains("dark")), `${route}: theme`).toBe(theme === "dark");
                    const reading = await page.evaluate(readNotice);
                    expectNoticeFits(reading, route);
                    lines.add(reading.lines);
                }
                expect(missing, "pages where the notice did not show").toEqual([]);
                console.log(`[notice] ${width}x${height} ${theme} ${sidebar}: ${ROUTES.length} pages; text on ${Array.from(lines).sort().join(" or ")} line(s)`);
            });
        }
    }
}

// ───────────────────────── stacked with the suspension and offline banners; the drawer ─────────────────────────

const goOffline = (page: Page) => page.evaluate(() => {
    Object.defineProperty(window.navigator, "onLine", { configurable: true, get: () => false });
    window.dispatchEvent(new Event("offline"));
});

type Stack = { order: string[]; overlap: boolean; sameEdges: boolean; insideWidth: boolean; horizontalScroll: boolean };
const readStack = (page: Page) => page.evaluate((): Stack => {
    const notice = document.querySelector("[data-privacy-policy-notice]") as HTMLElement;
    const banners = Array.from(notice.parentElement?.children ?? []).filter((el) => el.getBoundingClientRect().height > 1).slice(0, 3);
    const rects = banners.map((el) => el.getBoundingClientRect());
    let overlap = false;
    for (let i = 1; i < rects.length; i++) if (rects[i].top < rects[i - 1].bottom - 0.5) overlap = true;
    return {
        order: banners.map((el) => (el.getAttribute("data-testid") ?? (el.hasAttribute("data-privacy-policy-notice") ? "privacy-notice" : (el.textContent ?? "").trim().slice(0, 14)))),
        overlap, sameEdges: rects.every((r) => Math.abs(r.left - rects[0].left) <= 0.5 && Math.abs(r.right - rects[0].right) <= 0.5),
        insideWidth: rects.every((r) => r.left >= -0.5 && r.right <= innerWidth + 0.5), horizontalScroll: document.documentElement.scrollWidth > innerWidth + 1,
    };
});

for (const [width, height] of SIZES) {
    for (const theme of THEMES) {
        test(`notice ${width}x${height} ${theme}: stacked with the suspension and offline banners, and with the drawer open`, async ({ page }) => {
            await page.setViewportSize({ width, height });
            // A suspended subscription: the account banner shows and the page stays usable.
            await openApp(page, { theme, sidebar: width >= 1024 ? "expanded" : "drawer", me: { account_state: "suspended_subscription" } });
            await openRoute(page, "/dashboard");
            await expect(page.getByTestId("account-status-banner")).toBeVisible();
            await goOffline(page);
            await expect(page.getByText("You're offline. Changes can't be saved until you reconnect.")).toBeVisible();
            await page.waitForTimeout(300);
            expect(await readStack(page), "three banners").toEqual({ order: ["account-status-banner", "You're offline", "privacy-notice"], overlap: false, sameEdges: true, insideWidth: true, horizontalScroll: false });
            const stacked = await page.evaluate(readNotice);
            // With three banners the notice may start below the first screen on the smallest phones; it is reached by scrolling.
            expectNoticeFits({ ...stacked, inView: true }, "stacked");

            // The sidebar drawer, opened with the notice showing (below 1024 only).
            if (width < 1024) {
                await page.getByRole("button", { name: "Open sidebar" }).click();
                const close = page.getByRole("button", { name: "Close sidebar" });
                await expect(close).toBeVisible();
                await page.waitForTimeout(400);
                // The drawer panel itself (320px wide), not the full-screen layer that holds it and its backdrop:
                // from 768 up the middle of that layer is the backdrop, not the drawer.
                const drawer = await page.getByRole("dialog", { name: "Sidebar" }).evaluate((panel) => {
                    const r = panel.getBoundingClientRect();
                    return { inside: r.left >= -0.5 && r.top >= -0.5 && r.right <= innerWidth + 0.5 && r.bottom <= innerHeight + 0.5, horizontalScroll: document.documentElement.scrollWidth > innerWidth + 1, onTop: Boolean(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)?.closest("aside, nav, [role=dialog]")) };
                });
                expect(drawer, "drawer open over the notice").toEqual({ inside: true, horizontalScroll: false, onTop: true });
                await close.click();
            }
        });
    }
}

test("a held account: the page is locked but the notice's link and Dismiss still work", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await openApp(page, { me: { tenant_status: "suspended", suspended_scope: "tenant" } });
    await openRoute(page, "/dashboard");
    await expect(page.getByTestId("account-status-banner")).toContainText("Account suspended");
    expectNoticeFits(await page.evaluate(readNotice), "held account");
    await page.locator(NOTICE).getByRole("button", { name: "Dismiss" }).click();
    await expect(page.locator(NOTICE)).toHaveCount(0);
});

// ───────────────────────── behaviour ─────────────────────────

test.describe("behaviour", () => {
    test.beforeEach(async ({ page }) => { await page.setViewportSize({ width: 1280, height: 800 }); });

    for (const [now, shown] of [[at(START_MS - 30_000), false], [at(START_MS), true], [at(EFFECTIVE_MS - 30_000), true], [at(EFFECTIVE_MS), false], [at(EFFECTIVE_MS + 43.5 * 86_400_000), false]] as const) {
        test(`at ${now} the notice is ${shown ? "shown" : "not shown"}`, async ({ page }) => {
            await openApp(page, { now });
            await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
            await page.getByRole("button", { name: /Collapse sidebar|Expand sidebar/ }).first().waitFor();
            await page.waitForTimeout(1500);
            await expect(page.locator(NOTICE)).toHaveCount(shown ? 1 : 0);
        });
    }

    test("Dismiss hides it on every page and after a reload, for that person only", async ({ page }) => {
        await openApp(page);
        await openRoute(page, "/dashboard");
        await page.locator(NOTICE).getByRole("button", { name: "Dismiss" }).click();
        await expect(page.locator(NOTICE)).toHaveCount(0);
        for (const route of ["/dashboard", "/settings", "/calls"]) {
            await page.goto(route, { waitUntil: "domcontentloaded" });
            await page.getByRole("button", { name: /Collapse sidebar|Expand sidebar/ }).first().waitFor();
            await page.waitForTimeout(1500);
            await expect(page.locator(NOTICE), `${route} after Dismiss`).toHaveCount(0);
        }
        // Someone else signs in on the same browser: they have not dismissed it.
        const other = { ...E2E_USER, id: "0c5a6b1e-7d2f-4a3b-9c8d-2e1f0a9b8c7d", email: "other@example.com" };
        await mockApi(page, [
            { path: "/auth/me", body: other },
            { path: "/events", body: { items: [], next_cursor: null, tenant_id: other.tenant_id, user_id: other.id } },
            { path: "/alerts", body: { items: [], next_cursor: null, tenant_id: other.tenant_id, user_id: other.id } },
        ]);
        await openRoute(page, "/dashboard");
        await expect(page.locator(`${NOTICE} p`)).toHaveText(TEXT);
    });

    test("the link opens the Privacy Policy, which shows the same date", async ({ page }) => {
        await openApp(page);
        await openRoute(page, "/dashboard");
        await page.locator(NOTICE).getByRole("link", { name: LINK }).click();
        await expect(page).toHaveURL(/\/privacy\/?$/);
        await expect(page.getByText(/^Effective Date:/)).toHaveText(new RegExp(`Effective Date: ${printed(PRIVACY_POLICY_DATES.effectiveDate)}\\s+\\|\\s+Last Updated: ${printed(PRIVACY_POLICY_DATES.lastUpdated)}`));
    });

    test("the link and Dismiss are reached by Tab, in that order, and Enter on Dismiss hides the notice", async ({ page }) => {
        await openApp(page);
        await openRoute(page, "/dashboard");
        const seen: string[] = [];
        for (let i = 0; i < 80 && !seen.includes("Dismiss"); i++) {
            await page.keyboard.press("Tab");
            const name = await page.evaluate(() => { const el = document.activeElement; return el && el.closest("[data-privacy-policy-notice]") ? (el.textContent ?? "").trim() : ""; });
            if (name) seen.push(name);
        }
        expect(seen).toEqual([LINK, "Dismiss"]);
        const ring = await page.evaluate(() => { const s = getComputedStyle(document.activeElement as Element); return s.boxShadow !== "none" || s.outlineStyle !== "none"; });
        expect(ring, "Dismiss shows a focus ring").toBe(true);
        await page.keyboard.press("Enter");
        await expect(page.locator(NOTICE)).toHaveCount(0);
    });

    for (const role of ["user", "readonly", "agent", "billing_user", "campaign_manager", "tenant_admin", "partner_admin"]) {
        test(`the notice shows for the role ${role}`, async ({ page }) => {
            await openApp(page, { me: { role } });
            await openRoute(page, "/dashboard");
            await expect(page.locator(`${NOTICE} p`)).toHaveText(TEXT);
        });
    }

    test("nothing is shown to a visitor who is not signed in", async ({ page }) => {
        await page.clock.setFixedTime(new Date(INSIDE));
        await page.route((url) => /\/api\/v1(?:\/|$)/.test(url.pathname), (route) =>
            route.request().resourceType() === "document" ? route.fallback() : route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Not authenticated" }) }));
        for (const route of ["/auth/login", "/privacy", "/pricing"]) {
            await page.goto(route, { waitUntil: "domcontentloaded" });
            await page.waitForTimeout(1200);
            await expect(page.locator(NOTICE), route).toHaveCount(0);
        }
    });
});
