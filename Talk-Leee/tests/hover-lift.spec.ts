import { expect, test, type Locator, type Page } from "@playwright/test";
import { E2E_AUTH_TOKEN, E2E_USER, mockApi, mockDashboardApi, mockPageApis, signInAsE2eUser } from "./support/session";

/*
 * Every box that lifts 2px on hover (the class hover:-translate-y-0.5), on every page that shows one.
 *
 * The lift used to take the box's bottom 2px out from under a pointer resting there: the box dropped,
 * was hovered again and lifted again on every frame (a shimmer). Each box now carries, while hovered, an
 * invisible strip over the space it left (see src/components/ui/card.tsx). For each box this spec checks:
 *   - with the pointer at every half pixel from 6px to 0.5px above its at-rest bottom edge, it stays still;
 *   - it still lifts 2px and its shadow still changes;
 *   - with the pointer just below its at-rest bottom edge, it is not hovered (nothing below is taken over).
 *
 * Five boxes clip their content (two image frames, the home services card, the two Billing overview
 * cards). A box that clips itself would clip its strip too, so in those five the clipping sits on an inner
 * wrapper. For them this spec also checks that the content is still clipped at the box's inner edge.
 *
 * Every /api/v1 request is answered here. Run it against a build made with
 * NEXT_PUBLIC_API_BASE_URL=http://localhost:3100/api/v1, served on port 3100 (localhost, not 127.0.0.1).
 *
 * Not opened here: the "connector setup required" screens of src/components/guards/route-guard.tsx. The
 * only pages that use them (/email, /meetings, /reminders, /assistant/meetings) always answer "page not
 * found". Their classes are covered by the unit guard (src/components/ui/hover-lift.guard.test.ts).
 */

const ORIGIN = "http://localhost:3100";
test.use({ baseURL: ORIGIN });
test.setTimeout(3_600_000);

const SIZES: Array<[number, number]> = [[320, 568], [768, 1024], [1280, 800], [1920, 1080]];
const THEMES = ["light", "dark"] as const;
type Theme = (typeof THEMES)[number];

type PageSpec = { route: string; label?: string; signedIn: boolean; tabs?: string[]; menu?: boolean; noPlan?: boolean; expect: string[] };

const INDUSTRIES = ["education", "financial-services", "healthcare", "marketing-automation", "professional-services", "real-estate", "recruitment", "retail-ecommerce", "software-tech-support", "travel-industry"];

// Each page, and the kinds of lifting box it must show (see kindOf below).
const PAGES: PageSpec[] = [
    { route: "/", signedIn: false, menu: true, expect: ["services card", "faq item"] },
    { route: "/ai-voice-agent", signedIn: false, expect: ["feature card", "image frame", "closing box"] },
    { route: "/ai-voice-dialer", signedIn: false, expect: ["feature card", "closing box"] },
    { route: "/ai-assist", signedIn: false, expect: ["feature card", "closing box"] },
    { route: "/pricing", signedIn: false, expect: ["feature card", "closing box"] },
    { route: "/products", signedIn: false, expect: ["feature card"] },
    { route: "/industries", signedIn: false, expect: ["feature card"] },
    { route: "/use-cases", signedIn: false, expect: ["feature card"] },
    ...INDUSTRIES.map((name) => ({ route: `/industries/${name}`, signedIn: false, expect: name === "education" ? ["feature card"] : ["feature card", "closing box"] })),
    { route: "/use-cases/automated-lead-qualification", signedIn: false, expect: ["feature card", "closing box"] },
    { route: "/use-cases/customer-services-support", signedIn: false, expect: ["feature card", "image frame", "closing box"] },
    { route: "/analytics", signedIn: true, expect: ["analytics figure"] },
    { route: "/connectors", signedIn: true, expect: ["connector card"] },
    { route: "/settings", signedIn: true, tabs: ["Profile", "Devices", "Telephony"], expect: ["settings box", "settings row", "notification type box", "sound row", "shared Card"] },
    { route: "/security", signedIn: true, expect: ["settings row"] },
    // The Billing overview card has two forms, one with a plan and one without; each is its own element in the source.
    { route: "/billing", label: "/billing (with a plan)", signedIn: true, expect: ["shared Card", "billing overview card"] },
    { route: "/billing", label: "/billing (no plan)", signedIn: true, noPlan: true, expect: ["shared Card", "billing overview card"] },
    { route: "/dashboard", signedIn: true, expect: ["shared Card"] },
];

/** Names the kind of lifting box from its class list. */
function kindOf(className: string): string {
    const has = (part: string) => className.includes(part);
    // The shared Card. Where a page passes it classes of its own, the class merge can drop some of the Card's
    // (a Card given its own dark hover colour loses "dark:hover:bg-slate-800"), so it is also known by its corner.
    if (has("dark:hover:bg-slate-800") || (has("rounded-xl") && has("hover:bg-gray-50") && !has("justify-between"))) return has("from-primary/[0.08]") ? "billing overview card" : "shared Card";
    if (has("group/card")) return "menu item";
    if (has("rounded-3xl") && has("text-center")) return "closing box";
    if (has("rounded-3xl") && has("hover:brightness-[1.02]")) return "image frame";
    if (has("hover:scale-[1.01]") && has("hover:brightness-[1.02]") && !has("p-5")) return "services card";
    if (has("hover:brightness-[1.02]") && has("p-6")) return "feature card";
    if (has("hover:brightness-[1.02]") && has("p-5")) return "faq item";
    if (has("sm:w-[220px]")) return "sound row";
    if (has("hover:bg-gray-50") && has("rounded-2xl") && has("p-4") && has("space-y-4")) return "settings box";
    if (has("hover:bg-gray-50") && has("justify-between")) return "settings row";
    if (has("hover:bg-gray-50") && has("rounded-2xl") && has("p-4")) return "notification type box";
    if (has("bg-muted/60") && has("backdrop-blur-sm")) return "analytics figure";
    if (has("hover:bg-background/40")) return "connector card";
    return `other: ${className.slice(0, 80)}`;
}

/** The boxes whose content is clipped at their inner edge. */
const CLIPPING_KINDS = ["services card", "image frame", "billing overview card"];

const BILLING_PLAN = { id: "starter", name: "Starter", price: 49, description: "For a first calling team", minutes: 500, agents: 1, concurrent_calls: 2, features: ["Outbound campaigns"], not_included: [], popular: false, billing_mode: "test", price_options: [] };
const billingApi = (noPlan: boolean) => [
    { path: "/billing/plans", body: [BILLING_PLAN] },
    { path: "/billing/subscription", body: noPlan ? null : { status: "active", plan_id: "starter", plan_name: "Starter", current_period_start: "2026-11-01T00:00:00Z", current_period_end: "2026-12-01T00:00:00Z", cancel_at_period_end: false, minutes_allocated: 500, minutes_used: 120, minutes_remaining: 380, minutes_state: "known", purchased_price_option: null, billing_portal_available: false } },
    { path: "/billing/usage", body: { usage_type: "minutes", total_used: 120, allocated: 500, remaining: 380, overage: 0, unlimited: false, metering_period: "calendar_month" } },
    { path: "/billing/usage/daily", body: [] },
    { path: "/billing/invoices", body: { invoices: [], count: 0 } },
    { path: "/billing/adjustments", body: [] },
    { path: "/billing/overage-alerts", body: [] },
    { path: "/billing/config", body: { stripe_configured: true, mock_mode: false, billing_mode: "test", publishable_key: "pk_test_journey", checkout_open: true, checkout_unavailable_reason: null, topups_open: true, topups_unavailable_reason: null } },
    { path: "/billing/topups/packages", body: [] },
    { path: "/billing/topups/balance", body: { allocated: 500, used_minutes: 120, remaining_minutes: 380, unlimited: false, exhausted: false, purchased_minutes: 0, topup_minutes_left: 0 } },
    { path: "/billing/topups/orders", body: { orders: [] } },
    { path: "/billing/topups/ledger", body: { entries: [] } },
];

async function openApp(page: Page, signedIn: boolean, theme: Theme, noPlan = false) {
    await page.route((url) => /\/api\/v1(?:\/|$)/.test(url.pathname), async (route) => {
        if (route.request().resourceType() === "document") return route.fallback();
        return route.fulfill({ status: signedIn ? 404 : 401, contentType: "application/json", body: JSON.stringify({ detail: signedIn ? "Not Found" : "Not authenticated" }) });
    });
    if (signedIn) {
        await signInAsE2eUser(page);
        await page.context().addCookies([{ name: "talklee_auth_token", value: E2E_AUTH_TOKEN, url: ORIGIN }]);
        await mockDashboardApi(page);
        await mockPageApis(page);
        await mockApi(page, [
            { path: "/auth/me", body: E2E_USER },
            { path: "/events", body: { items: [], next_cursor: null, tenant_id: E2E_USER.tenant_id ?? null, user_id: E2E_USER.id } },
            { path: "/alerts", body: { items: [], next_cursor: null, tenant_id: E2E_USER.tenant_id ?? null, user_id: E2E_USER.id } },
            ...billingApi(noPlan),
            { path: "/sessions/active", body: { sessions: [{ id: "session-1", device_name: "Office laptop", device_type: "desktop", browser: "Chrome", os: "Windows", ip_address: "203.0.113.5", is_current: true, created_at: "2026-11-01T10:00:00Z", last_active_at: "2026-11-20T10:00:00Z", expires_at: "2026-12-20T10:00:00Z" }], total_count: 1, current_session_id: "session-1" } },
        ]);
    }
    await page.addInitScript(([theme]) => {
        try {
            window.localStorage.setItem("talklee.theme", theme as string);
            window.localStorage.setItem("talklee.sidebar.state.v1", JSON.stringify({ collapsed: false, mobileOpen: false }));
        } catch { /* storage unavailable */ }
        // Messages and the sign-up invitation float over the page; they are not what is measured here.
        const hide = () => {
            const style = document.createElement("style");
            style.textContent = '[role="status"][aria-label$=" notification"],[role="alert"][aria-label$=" notification"],[data-signup-popup]{display:none !important}';
            document.documentElement.appendChild(style);
        };
        if (document.documentElement) hide(); else document.addEventListener("DOMContentLoaded", hide);
    }, [theme] as const);
}

/** Marks every visible lifting box on the page and returns its class list (run in the page). */
function markBoxes(): Array<{ className: string; id: string; text: string }> {
    const found: Array<{ className: string; id: string; text: string }> = [];
    // The Analytics chart lifts through its own animation, not the lift class: it is found by the strip it carries.
    for (const el of Array.from(document.querySelectorAll<HTMLElement>('[class*="hover:-translate-y-0.5"], [class*="hover:after:top-full"]'))) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0 || !el.checkVisibility({ checkOpacity: false, checkVisibilityCSS: true })) continue;
        const id = `lift-${found.length}-${Math.round(performance.now())}`;
        el.setAttribute("data-lift-probe", id);
        found.push({ className: typeof el.className === "string" ? el.className : "", id, text: (el.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 28) });
    }
    return found;
}

type Reading = { flickersAt: number[]; lift: number; expectedLift: number; growth: number; clipped?: string; shadowChanges: boolean; restShadow: string; hoverShadow: string; hoveredBelow: number[]; note?: string };

/** The pointer positions the box is tried at, measured against its at-rest bottom edge. */
const ABOVE = [6, 5.5, 5, 4.5, 4, 3.5, 3, 2.5, 2, 1.5, 1, 0.5];
const BELOW = [0.5, 1, 2];

async function measure(page: Page, id: string, restPoint: { x: number; y: number }, strictBelow = false): Promise<Reading> {
    const box = page.locator(`[data-lift-probe="${id}"]`);
    await page.mouse.move(restPoint.x, restPoint.y);
    // Bring the box's bottom edge to the middle of the screen.
    await box.evaluate((el) => {
        const main = document.getElementById("main-content");
        const want = () => el.getBoundingClientRect().bottom - Math.round(innerHeight / 2);
        if (main) main.scrollBy({ top: want(), behavior: "instant" });
        window.scrollBy({ top: want(), behavior: "instant" });
    });
    await page.waitForTimeout(350);
    // At rest: its place against its nearest lifting parent (a box inside a lifting box moves with it).
    const rest = await box.evaluate((el) => {
        const parent = el.parentElement?.closest<HTMLElement>('[class*="hover:-translate-y-0.5"]') ?? null;
        const r = el.getBoundingClientRect();
        // A place along the bottom edge where the box itself (not something over it) is under the pointer.
        let x = -1;
        for (const part of [0.5, 0.25, 0.75, 0.12, 0.88]) {
            const px = r.left + r.width * part;
            const top = document.elementFromPoint(px, r.bottom - 1.5);
            if (top && el.contains(top) && (top === el || top.closest('[class*="hover:-translate-y-0.5"], [class*="hover:after:top-full"]') === el)) { x = px; break; }
        }
        return { x, bottom: r.bottom, top: r.top, height: r.height, parentTop: parent ? parent.getBoundingClientRect().top : 0, shadow: getComputedStyle(el).boxShadow, inView: r.bottom > 0 && r.bottom < innerHeight };
    });
    if (rest.x < 0 || !rest.inView) return { flickersAt: [], lift: NaN, expectedLift: NaN, growth: 0, shadowChanges: false, restShadow: "", hoverShadow: "", hoveredBelow: [], note: "its bottom edge could not be reached with the pointer" };
    const x = rest.x;

    // Hovered well inside: the lift and the shadow.
    await page.mouse.move(x, rest.bottom - 11, { steps: 2 });
    await page.mouse.move(x, rest.bottom - 12, { steps: 2 });
    // What is around the box may move too while the pointer is on it, and not at once: a box inside a lifting box is
    // carried up with it, and a dashboard Card sits in a panel that grows by 1% about half a second after the pointer
    // arrives. So wait until the box has stopped moving, then read it.
    await page.waitForTimeout(1200);
    for (let i = 0, same = 0, last = ""; i < 20 && same < 3; i++) {
        const now = await box.evaluate((el) => { const r = el.getBoundingClientRect(); return `${r.top.toFixed(3)} ${r.height.toFixed(3)}`; });
        same = now === last ? same + 1 : 0;
        last = now;
        await page.waitForTimeout(150);
    }
    // The box as it is now, and its own outline with only its own hover movement switched off (everything around it
    // left as it is): the difference is the box's own lift and growth, and that outline's bottom is its at-rest edge.
    const hovered = await box.evaluate((el) => {
        const saved = el.getAttribute("style");
        const put = (css: string | null) => { if (css === null) el.removeAttribute("style"); else el.setAttribute("style", css); };
        const shadow = getComputedStyle(el).boxShadow;
        const on = el.getBoundingClientRect();
        put(`${saved ?? ""};transition:none !important;translate:none !important;scale:none !important;transform:none !important`);
        const off = el.getBoundingClientRect();
        // Back exactly as it was, without setting off the box's own transition.
        put(`${saved ?? ""};transition:none !important`);
        void el.getBoundingClientRect();
        put(saved);
        return { top: on.top, height: on.height, offTop: off.top, offHeight: off.height, offBottom: off.bottom, shadow };
    });
    const edge = hovered.offBottom;
    // The lift, read at the box's middle: a box that also grows on hover moves its top edge further and its bottom edge less.
    const lift = Number(((hovered.top + hovered.height / 2) - (hovered.offTop + hovered.offHeight / 2)).toFixed(2));
    const expectedLift = -2;
    const growth = Number((hovered.height - hovered.offHeight).toFixed(2));

    // The pointer resting near the bottom edge: does the box keep still?
    const flickersAt: number[] = [];
    for (const above of ABOVE) {
        await page.mouse.move(x, edge - 14);
        await page.waitForTimeout(140);
        await page.mouse.move(x, edge - above, { steps: 4 });
        await page.waitForTimeout(260);
        // Still, and still held: the pointer is inside the box's at-rest outline, so the box must stay hovered.
        // One case is not a fault: the box is at rest, not hovered, and is not what the pointer is on. The browser
        // tests the pointer against the box snapped to whole pixels, so a box whose edge falls between two pixels
        // (a box inside one that grows on hover, for example) has a sliver under a pixel thick that the pointer can
        // never be "on", lifted or not. A box that is under the pointer and still not held is a fault.
        const steady = await box.evaluate(async (el, [px, py]) => {
            const parent = el.parentElement?.closest<HTMLElement>('[class*="hover:-translate-y-0.5"]') ?? null;
            const tops = new Set<string>();
            let letGo = false;
            for (let i = 0; i < 48; i++) {
                tops.add((el.getBoundingClientRect().top - (parent ? parent.getBoundingClientRect().top : 0)).toFixed(2));
                if (!el.matches(":hover")) letGo = true;
                await new Promise((r) => requestAnimationFrame(r));
            }
            const r = el.getBoundingClientRect();
            const under = document.elementFromPoint(px, py);
            const offTheBox = tops.size === 1 && letGo && !el.matches(":hover") && !(under && el.contains(under));
            return { ok: (tops.size === 1 && !letGo) || offTheBox, offTheBox, detail: `places ${[...tops].join("/")}; ${letGo ? "let go" : "held"}; box ${r.left.toFixed(2)},${r.top.toFixed(2)} ${r.width.toFixed(2)}x${r.height.toFixed(2)}; "${(el.textContent ?? "").trim().slice(0, 40)}"` };
        }, [x, edge - above] as const);
        if (steady.offTheBox) console.log(`[lift-detail] ${above}px above the edge: the pointer is not on the box there, lifted or at rest (pixel snapping); ${steady.detail}`);
        if (!steady.ok) { flickersAt.push(above); console.log(`[lift-detail] ${above}px above the edge (pointer at ${x.toFixed(2)},${(edge - above).toFixed(2)}): ${steady.detail}`); }
    }

    // Just below the at-rest bottom edge, coming from inside the box: the box must let go.
    // (A box that also grows on hover reaches past its at-rest edge once it is taller than 400px, as it always did.)
    const grows = Math.max(0, growth / 2 - 2);
    // The browser tests the pointer against the box snapped to whole pixels. Where the at-rest edge falls inside a
    // pixel row (a Card in a grown dashboard panel ends at 512.16), the box at rest, with no strip at all, is still
    // "under" a pointer anywhere in that row. So "below the edge" is counted from the first whole pixel row under it.
    const firstRowBelow = Math.ceil(edge - 0.001);
    const hoveredBelow: number[] = [];
    for (const below of BELOW) {
        await page.mouse.move(x, edge - 14);
        await page.waitForTimeout(140);
        await page.mouse.move(x, firstRowBelow + below + grows, { steps: 6 });
        const left = await letsGo(box);
        // The Analytics chart only: taken again below its edge, even for one frame, is a fault (its lift and its growth
        // are now undone together, so its body is never under a pointer resting there).
        if (!left.ok || (strictBelow && left.hoveredFrames > 0)) hoveredBelow.push(below);
        if (left.hoveredFrames) console.log(`[lift-detail] ${below}px below the edge: hovered for ${left.hoveredFrames} frames after the pointer arrived, then ${left.ok ? "let go for good" : "NOT let go"}`);
    }
    await page.mouse.move(restPoint.x, restPoint.y);
    return { flickersAt, lift, expectedLift, growth, shadowChanges: hovered.shadow !== rest.shadow, restShadow: rest.shadow, hoverShadow: hovered.shadow, hoveredBelow };
}

/**
 * With the pointer resting just below the box's at-rest edge: does the box let go, and stay let go?
 * A strip reaching below the edge would hold the box for as long as the pointer rests there, and a box hovered and
 * let go in turn would never come to rest; both fail here. What does not fail is a box that is taken once more for a
 * few frames and then let go for good: a box that grows on hover through an animation (the analytics figure) is back
 * down at once but shrinks back over about a tenth of a second, so for that moment its own body, not the strip, is
 * under a pointer resting within its growth below the edge. It did the same before the strip existed.
 */
async function letsGo(box: Locator): Promise<{ ok: boolean; hoveredFrames: number }> {
    return box.evaluate(async (el) => {
        let hoveredFrames = 0;
        let quiet = 0;
        let last = "";
        // Up to about 3 seconds; let go for good = 20 frames in a row not hovered and not moving.
        for (let i = 0; i < 180 && quiet < 20; i++) {
            const r = el.getBoundingClientRect();
            const now = `${r.top.toFixed(2)} ${r.height.toFixed(2)}`;
            const hovered = el.matches(":hover");
            if (hovered) hoveredFrames++;
            quiet = !hovered && now === last ? quiet + 1 : 0;
            last = now;
            await new Promise((done) => requestAnimationFrame(done));
        }
        return { ok: quiet >= 20, hoveredFrames };
    });
}

for (const [width, height] of SIZES) {
    for (const theme of THEMES) {
        test(`hover lift ${width}x${height} ${theme}: every lifting box stays still with the pointer near its bottom edge`, async ({ browser }) => {
            const problems: string[] = [];
            // "named" is the kind with the box's first words after it, so the log shows which box was measured.
            const report = (where: string, named: string, reading: Reading, kind = named) => {
                const still = reading.flickersAt.length === 0;
                console.log(`[lift] ${width}x${height} ${theme} ${where} | ${named} | ${reading.note ?? `${still ? "still" : `FLICKERS OR LETS GO at ${reading.flickersAt.join(", ")}px above the edge`}; lift ${reading.lift}px (expected ${reading.expectedLift}), grows ${reading.growth}px; shadow ${reading.shadowChanges ? "changes" : "SAME"}; hovered below the edge: ${reading.hoveredBelow.length ? reading.hoveredBelow.join(", ") + "px" : "no"}${reading.clipped ? `; content ${reading.clipped}` : ""}; hover shadow ${reading.hoverShadow}`}`);
                if (reading.note) { problems.push(`${where} | ${kind}: ${reading.note}`); return; }
                if (!still) problems.push(`${where} | ${kind}: flickers or lets go of the pointer at ${reading.flickersAt.join(", ")}px above its bottom edge`);
                if (Math.abs(reading.lift - reading.expectedLift) > 0.1) problems.push(`${where} | ${kind}: lift ${reading.lift}px, expected ${reading.expectedLift}px`);
                if (!reading.shadowChanges) problems.push(`${where} | ${kind}: the shadow does not change on hover`);
                if (reading.hoveredBelow.length) problems.push(`${where} | ${kind}: still hovered ${reading.hoveredBelow.join(", ")}px below its bottom edge`);
                if (CLIPPING_KINDS.includes(kind) && reading.clipped !== "clipped at the inner edge") problems.push(`${where} | ${kind}: content ${reading.clipped}`);
            };

            // HOVER_LIFT_ONLY=/dashboard,/billing looks at those pages only (for a quick look; the full run takes every page).
            const only = (process.env.HOVER_LIFT_ONLY ?? "").split(",").filter(Boolean);
            for (const spec of PAGES) {
                if (only.length && !only.includes(spec.route)) continue;
                // A fresh browser session for each page: signed in or signed out as the page needs.
                const context = await browser.newContext({ viewport: { width, height }, baseURL: ORIGIN });
                const page = await context.newPage();
                await openApp(page, spec.signedIn, theme, spec.noPlan);
                await page.goto(spec.route, { waitUntil: "domcontentloaded" });
                try { await page.waitForLoadState("networkidle", { timeout: 6000 }); } catch { /* pages that keep reading */ }
                await page.mouse.move(1, 1);
                // Parts of the public pages load as they come into view: walk down the page once.
                await page.evaluate(async () => {
                    const main = document.getElementById("main-content");
                    for (let i = 0; i < 40; i++) {
                        const before = window.scrollY + (main ? main.scrollTop : 0);
                        window.scrollBy(0, innerHeight * 0.8); main?.scrollBy(0, innerHeight * 0.8);
                        await new Promise((r) => setTimeout(r, 120));
                        if (window.scrollY + (main ? main.scrollTop : 0) === before) break;
                    }
                    window.scrollTo(0, 0); main?.scrollTo(0, 0);
                });
                await page.waitForTimeout(3500);

                const seenKinds = new Set<string>();
                const views: Array<string | null> = spec.tabs ?? [null];
                for (const tab of views) {
                    if (tab) { await page.getByRole("tab", { name: tab, exact: true }).click(); await page.mouse.move(1, 1); await page.waitForTimeout(900); }
                    const boxes = await page.evaluate(markBoxes);
                    for (const { className, id, text } of boxes) {
                        const kind = kindOf(className);
                        if (seenKinds.has(kind)) continue;
                        seenKinds.add(kind);
                        const reading = await measure(page, id, { x: 1, y: 1 }, kind === "analytics figure");
                        if (CLIPPING_KINDS.includes(kind)) reading.clipped = await readClipping(page, id);
                        report(`${spec.label ?? spec.route}${tab ? ` [${tab}]` : ""}`, `${kind} "${text}"`, reading, kind);
                    }
                }

                // The top menu's panels (wide screens only): the links inside lift too.
                if (spec.menu && width >= 1024) {
                    const item = page.locator('a[class*="group/card"]').first();
                    const opener = item.locator("xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' group ')][1]");
                    await page.evaluate(() => { window.scrollTo(0, 0); document.getElementById("main-content")?.scrollTo(0, 0); });
                    await page.waitForTimeout(500);
                    // A click on the menu's name keeps its panel open while the pointer moves about inside it.
                    const name = opener.locator("button").first();
                    await name.click();
                    const point = await name.evaluate((el) => { const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
                    await expect(item).toBeVisible();
                    await page.waitForTimeout(400);
                    const id = await item.evaluate((el) => { el.setAttribute("data-lift-probe", "lift-menu"); return "lift-menu"; });
                    seenKinds.add("menu item");
                    report(`${spec.route} [top menu]`, "menu item", await measureInPlace(page, id, point));
                }

                for (const kind of [...spec.expect, ...(spec.menu && width >= 1024 ? ["menu item"] : [])]) if (!seenKinds.has(kind)) problems.push(`${spec.label ?? spec.route}: no "${kind}" was found on the page`);
                for (const kind of seenKinds) if (kind.startsWith("other:")) problems.push(`${spec.route}: a lifting box this spec does not know: ${kind}`);
                await context.close();
            }
            expect(problems, "every lifting box stays still, lifts 2px, changes its shadow and lets go below its edge").toEqual([]);
        });
    }
}

/**
 * Is the box's content clipped at the box's inner edge (inside its border, with its rounded corners)?
 * The clipping is either on the box itself or on a single wrapper that fills the box exactly.
 */
async function readClipping(page: Page, id: string): Promise<string> {
    return page.locator(`[data-lift-probe="${id}"]`).evaluate((el) => {
        const style = getComputedStyle(el);
        const border = parseFloat(style.borderTopWidth);
        const radius = parseFloat(style.borderTopLeftRadius);
        if (style.overflowX === "hidden" && style.overflowY === "hidden") return "clipped at the inner edge";
        const kids = Array.from(el.children) as HTMLElement[];
        if (kids.length !== 1) return `NOT CLIPPED: ${kids.length} children and no clipping on the box`;
        const inner = kids[0];
        const innerStyle = getComputedStyle(inner);
        if (innerStyle.overflowX !== "hidden" || innerStyle.overflowY !== "hidden") return "NOT CLIPPED: the wrapper does not clip";
        const a = el.getBoundingClientRect();
        const b = inner.getBoundingClientRect();
        const scale = a.width / el.offsetWidth;
        const fills = [b.left - a.left, b.top - a.top, a.right - b.right, a.bottom - b.bottom].every((gap) => Math.abs(gap - border * scale) <= 0.2);
        if (!fills) return `NOT CLIPPED AS BEFORE: the wrapper does not fill the box (gaps ${[b.left - a.left, b.top - a.top, a.right - b.right, a.bottom - b.bottom].map((g) => g.toFixed(2)).join(", ")})`;
        const corners = [innerStyle.borderTopLeftRadius, innerStyle.borderTopRightRadius, innerStyle.borderBottomLeftRadius, innerStyle.borderBottomRightRadius].map(parseFloat);
        if (!corners.every((corner) => Math.abs(corner - (radius - border)) <= 0.01)) return `NOT CLIPPED AS BEFORE: corner ${corners.join("/")}px, expected ${radius - border}px`;
        return "clipped at the inner edge";
    });
}

/** The same measurement for a box inside a hover-opened panel: the page is not scrolled and the pointer rests on the panel's opener. */
async function measureInPlace(page: Page, id: string, restPoint: { x: number; y: number }): Promise<Reading> {
    const box = page.locator(`[data-lift-probe="${id}"]`);
    const read = () => box.evaluate((el) => { const r = el.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, x: r.left + r.width / 2, height: r.height, shadow: getComputedStyle(el).boxShadow }; });
    const rest = await read();
    await page.mouse.move(rest.x, rest.bottom - 14, { steps: 4 });
    await page.waitForTimeout(450);
    const hovered = await read();
    const lift = Number(((hovered.top + hovered.height / 2) - (rest.top + rest.height / 2)).toFixed(2));
    const expectedLift = -2;
    const growth = Number((hovered.height - rest.height).toFixed(2));
    const flickersAt: number[] = [];
    for (const above of ABOVE) {
        await page.mouse.move(rest.x, rest.bottom - 14);
        await page.waitForTimeout(140);
        await page.mouse.move(rest.x, rest.bottom - above, { steps: 4 });
        await page.waitForTimeout(260);
        const steady = await box.evaluate(async (el, [px, py]) => {
            const tops = new Set<string>(); let letGo = false;
            for (let i = 0; i < 48; i++) { tops.add(el.getBoundingClientRect().top.toFixed(2)); if (!el.matches(":hover")) letGo = true; await new Promise((r) => requestAnimationFrame(r)); }
            const under = document.elementFromPoint(px, py);
            // As in measure(): at rest, not hovered and not under the pointer is the pixel-snapping sliver, not a fault.
            return (tops.size === 1 && !letGo) || (tops.size === 1 && letGo && !el.matches(":hover") && !(under && el.contains(under)));
        }, [rest.x, rest.bottom - above] as const);
        if (!steady) flickersAt.push(above);
    }
    const hoveredBelow: number[] = [];
    for (const below of BELOW) {
        await page.mouse.move(rest.x, rest.bottom - 14);
        await page.waitForTimeout(140);
        await page.mouse.move(rest.x, rest.bottom + below + Math.max(0, growth / 2 - 2), { steps: 6 });
        if (!(await letsGo(box)).ok) hoveredBelow.push(below);
    }
    await page.mouse.move(restPoint.x, restPoint.y);
    return { flickersAt, lift, expectedLift, growth, shadowChanges: hovered.shadow !== rest.shadow, restShadow: rest.shadow, hoverShadow: hovered.shadow, hoveredBelow };
}
