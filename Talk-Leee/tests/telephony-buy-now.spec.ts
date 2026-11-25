import { expect as baseExpect, test, type Page } from "@playwright/test";
import { E2E_AUTH_TOKEN, E2E_USER, mockApi, mockDashboardApi, mockPageApis, signInAsE2eUser } from "./support/session";
import { PRIVACY_POLICY_DATES } from "../src/lib/privacy-policy-dates";

/*
 * Settings → Telephony: the "Buy Now" card, its documents window, the four
 * upload boxes, the two Submit buttons and the message, measured at every
 * supported width, in both themes, with the sidebar expanded and collapsed,
 * for an account admin and for a role that only sees the admin note. The
 * section's other floating panels, the privacy page sentence and the docs part
 * are measured too. The clock is fixed inside the Privacy Policy notice's 30
 * days, so every measurement here is taken with that notice showing.
 *
 * Every /api/v1 request is answered here; nothing reaches a backend. Run it
 * against a build made with NEXT_PUBLIC_API_BASE_URL=http://localhost:3100/api/v1
 * and served on port 3100 (localhost, not 127.0.0.1: the page and its API must
 * share one origin, as the mocks send no CORS headers).
 */

const ORIGIN = "http://localhost:3100";
test.use({ baseURL: ORIGIN });
// Playwright's own 5 s default stands. A very slow machine running the dev server (a closing window was seen taking
// 9 s on a 1 GB laptop) may set PW_EXPECT_TIMEOUT, in milliseconds; no assertion changes.
const SLOW_MACHINE_TIMEOUT = Number(process.env.PW_EXPECT_TIMEOUT);
const expect = SLOW_MACHINE_TIMEOUT > 0 ? baseExpect.configure({ timeout: SLOW_MACHINE_TIMEOUT }) : baseExpect;
test.setTimeout(180_000);

const SIZES: Array<[number, number]> = [
    [320, 568], [360, 740], [375, 667], [390, 844], [414, 896], [768, 1024], [820, 1180],
    [1024, 768], [1280, 800], [1366, 768], [1440, 900], [1536, 864], [1920, 1080],
];
const THEMES = ["light", "dark"] as const;
type Theme = (typeof THEMES)[number];
type Sidebar = "drawer" | "expanded" | "collapsed";
type Role = "tenant_admin" | "user";
const sidebarsFor = (width: number): Sidebar[] => (width >= 1024 ? ["expanded", "collapsed"] : ["drawer"]);
// The two inner cards sit side by side from Tailwind's sm breakpoint (640px) and are stacked below it.
const SIDE_BY_SIDE_FROM = 640;

const CARD_LINES = [
    "Minute charges vary by destination. Local conditions apply.",
    "Info: Each destination has its own regulations, which may vary from region to region.",
];
const TITLE = "Documentation is required:";
const DESCRIPTION = "To buy a phone number, we need the documents that destination regulations require. Choose business use or personal use and upload both documents.";
const EMPTY = "Click to upload or drag and drop";
const MESSAGE = "Thank you. Your documents have been submitted.";
/** "2026-12-20" -> "20 December 2026", worked out here without the code under test. */
const printed = (isoDate: string) => new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(`${isoDate}T00:00:00Z`));
// The notice's words, with the Effective Date read from the shared dates (never typed in here).
const NOTICE = `We're updating our Privacy Policy on ${printed(PRIVACY_POLICY_DATES.effectiveDate)}.`;
// Five and a half days into the notice's 30 days: the notice shows whatever day this runs.
const FIXED_NOW = new Date(Date.parse(`${PRIVACY_POLICY_DATES.noticeStart}T00:00:00Z`) + 5.5 * 86_400_000).toISOString();
const NOTE = "Phone lines are managed by your account admin. Ask an admin if a line needs to be added or changed.";
const PRIVACY = "We do not collect Social Security numbers. If you buy a phone number in Telephony, we ask for the documents that destination regulations require: for business use, your business registration and a photo ID matching it; for personal use, a utility bill as proof of address and a photo ID matching it.";
// The first lines of the docs part, as the page prints them (the button name is bold there).
const DOCS_LINES = "Use Buy Now to buy a phone number in Telephony. Each destination has its own regulations, so the window lists the documents needed for business use or personal use and lets you upload them.";
// 190 characters.
const LONG_NAME = `${"company-registration-certificate-scan-".repeat(5).slice(0, 186)}.pdf`;
const pdf = (name: string) => ({ name, mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 test") });

const T = "2026-11-10T10:00:00Z";
const TRUNK = {
    id: "trunk-e2e", tenant_id: E2E_USER.tenant_id, trunk_name: "Main line", sip_domain: "sip.example.com", port: 5060, transport: "udp",
    direction: "both", is_active: true, auth_username: "e2e", auth_configured: true, metadata: {}, last_tested_at: T, last_test_result: null,
    live_registration_status: "registered", live_status_detail: null, live_status_checked_at: T, runtime_ready: true, inbound_runtime_ready: true,
    runtime_status_code: "ready", runtime_status_detail: "Ready", created_at: T,
};
// POST /telephony/sip/trunks/{id}/test (SipTrunkTestResponse, src/lib/telephony-api.ts:157).
const TEST_RESULT = {
    ok: true, status: "connected", summary: "Connected. Your provider accepted the sign-in.", detail: "The provider answered and accepted the registration.",
    sip_code: 200, latency_ms: 84, tested_at: T,
    steps: [
        { key: "dns", ok: true, label: "Found your provider", detail: "sip.example.com resolved." },
        { key: "reach", ok: true, label: "Reached your provider", detail: "The provider answered in 84 ms.", sip_code: 200 },
        { key: "register", ok: true, label: "Signed in", detail: "The registration was accepted with a deliberately long explanation so the panel has to wrap its text inside the line card.", sip_code: 200 },
    ],
};

// What Settings reads beyond the shared shell (shapes named by the consumer's file).
const SETTINGS_MOCKS = [
    { path: "/events", body: { items: [], next_cursor: null, tenant_id: E2E_USER.tenant_id, user_id: E2E_USER.id } },
    { path: "/sessions/active", body: { sessions: [], total_count: 0, current_session_id: null } },
    { path: "/recordings/policy", body: { configured: true, default_consent_mode: "announce", announcement_text: "This call may be recorded.", opt_out_dtmf_digit: "9", two_party_country_codes: ["US"], retention_days: 90, updated_at: T, effect: "Calls are recorded after the announcement." } },
    // providersListSchema (src/lib/telephony-api.ts:66). Saved Twilio credentials: the "forget credentials" confirm window exists.
    {
        path: "/telephony/providers",
        body: {
            active: "sip",
            providers: [
                { provider: "twilio", status: "active", label: null, from_number: "+15551230000", last_tested_at: T, last_test_result: { ok: true, check_scope: "provider_account", latency_ms: 120, account_status: "active" }, has_credentials: true },
                { provider: "vonage", status: "inactive", label: null, from_number: null, last_tested_at: null, last_test_result: null, has_credentials: false },
            ],
            availability: {
                twilio: { activation_allowed: false, qualification_only: true, reason: "Twilio is available for nonproduction qualification only on this deployment." },
                vonage: { activation_allowed: false, qualification_only: true, reason: "Vonage is available for nonproduction qualification only on this deployment." },
            },
        },
    },
    // NumberRequestConfigOut (backend telephony_number_requests.py): the limits the window reads when it opens.
    { path: "/telephony/number-requests/config", body: { accepted_types: ["application/pdf", "image/jpeg", "image/png"], accepted_extensions: [".pdf", ".jpg", ".jpeg", ".png"], max_file_bytes: 10485760 } },
    { path: "/telephony/sip/trunks", body: [TRUNK] },
    { path: "/telephony/sip/trunks/outbound-route", body: { ready: true, reason_code: "ready", reason: null, caller_id: "+15551230000", trunk_id: "trunk-e2e", trunk_name: "Main line" } },
    // Pool accounts, so the "Shared trunk account" select exists; one long label.
    {
        path: "/telephony/sip/trunks/pool",
        body: [
            { id: "pool-1", label: "Pool 150001", caller_id: "+15551230001", registration_status: "registered", runtime_ready: true, runtime_status_detail: "Ready" },
            { id: "pool-2", label: "Pool 150002 with a deliberately long account label", caller_id: "+15551230002", registration_status: "unregistered", runtime_ready: false, runtime_status_detail: "the provider has not accepted the registration yet" },
        ],
    },
    { path: "/telephony/sip/trunks/pool-assignment", body: { pool_trunk_id: null, label: null, caller_id: null } },
];

// ───────────────────────── measurement helpers that run in the page ─────────────────────────

type StaticResult = { missing: boolean; outside: string[]; cut: string[]; shortened: string[]; overlaps: string[]; covered: string[] };
type DialogResult = { missing: boolean; rect: number[]; inside: boolean; scrollsInside: boolean; unreachable: string[]; noHorizontalScroll: boolean };
type WindowLayout = {
    title: string | null; titleAtTop: boolean; cardTitles: string[]; arrangement: string; businessFirst: boolean; equalWidth: boolean; equalHeight: boolean;
    items: Array<{ text: string | null; boxDirectlyUnder: boolean; boxInsideCard: boolean }>;
    names: Array<{ insideBox: boolean; oneLine: boolean; nameOnlyAsText: boolean; shortened: boolean }>;
    submits: Array<{ insideCard: boolean; belowItems: boolean; disabled: boolean }>;
    cancelInsideWindow: boolean; cancelBelowCards: boolean;
};
type ToastResult = { missing: boolean; count: number; text: string; parts: number; inside: boolean; titleCut: boolean; lines: number; needs: number; room: number; onTop: boolean; overlapsCard: boolean; controlsUnder: string[] };
type Probe = {
    staticChecks(selector: string): StaticResult;
    endOfPage(selector: string): string[];
    dialogChecks(): DialogResult;
    windowLayout(): WindowLayout;
    toastChecks(): ToastResult;
    listbox(): { missing: boolean; rect: number[]; inside: boolean; onTop: boolean };
    horizontalScroll(): boolean;
    gapAbove(selector: string): number | null;
};

function installProbe() {
    const win = window as unknown as { __tb?: Probe; __pickerClicks?: number };
    if (win.__tb) return;
    // Every click that asks the browser to open a file picker, counted in the page itself.
    win.__pickerClicks = 0;
    document.addEventListener("click", (event) => {
        const target = event.target;
        if (target instanceof HTMLInputElement && target.type === "file") win.__pickerClicks = (win.__pickerClicks ?? 0) + 1;
    }, true);

    const visible = (el: Element) => {
        if (!el.isConnected) return false;
        if (!(el as HTMLElement).checkVisibility({ checkOpacity: false, checkVisibilityCSS: true })) return false;
        const r = el.getBoundingClientRect();
        return r.width > 1.5 && r.height > 1.5;
    };
    const label = (el: Element) => {
        const text = (el.getAttribute("aria-label") || (el as HTMLElement).innerText || el.getAttribute("placeholder") || el.id || "").replace(/\s+/g, " ").trim();
        return `${el.tagName.toLowerCase()}:${text.slice(0, 48)}`;
    };
    const ownText = (el: Element) => Array.from(el.childNodes).some((n) => n.nodeType === 3 && (n.textContent ?? "").trim().length > 0);
    const blocked = (el: Element) => (el as HTMLButtonElement).disabled === true || getComputedStyle(el).pointerEvents === "none";
    // The part of an element that is really shown: its box cut by every scrolling or clipping ancestor.
    const shown = (el: Element) => {
        const r = el.getBoundingClientRect();
        let left = r.left, top = r.top, right = r.right, bottom = r.bottom;
        for (let a = el.parentElement; a && a !== document.documentElement; a = a.parentElement) {
            const s = getComputedStyle(a);
            if (s.overflowX === "visible" && s.overflowY === "visible") continue;
            const ar = a.getBoundingClientRect();
            left = Math.max(left, ar.left); top = Math.max(top, ar.top); right = Math.min(right, ar.right); bottom = Math.min(bottom, ar.bottom);
        }
        return { left, top, right, bottom };
    };
    const hits = (el: Element, x: number, y: number) => {
        const top = document.elementFromPoint(x, y);
        return Boolean(top && (el.contains(top) || (top.contains(el) && top.closest("label,button"))));
    };
    const first = (selector: string) => Array.from(document.querySelectorAll(selector)).find(visible) ?? null;
    const unique = (list: string[]) => Array.from(new Set(list));
    const insideWindow = (r: DOMRect) => r.left >= -1 && r.top >= -1 && r.right <= innerWidth + 1 && r.bottom <= innerHeight + 1;
    const openDialog = () => Array.from(document.querySelectorAll('[role="dialog"]')).filter(visible).pop() ?? null;

    const staticChecks = (selector: string): StaticResult => {
        const root = first(selector);
        if (!root) return { missing: true, outside: [], cut: [], shortened: [], overlaps: [], covered: [] };
        const all = [root, ...Array.from(root.querySelectorAll("*"))].filter((el) => visible(el) && !el.closest("svg"));
        const outside = all.filter((el) => { const r = el.getBoundingClientRect(); return r.right > innerWidth + 1 || r.left < -1; }).map(label);
        const cut: string[] = [];
        const shortened: string[] = [];
        for (const el of all) {
            if (!ownText(el)) continue;
            const cs = getComputedStyle(el);
            if (cs.overflowX !== "visible" && el.scrollWidth > el.clientWidth + 1) (cs.textOverflow === "ellipsis" ? shortened : cut).push(label(el));
            if (/(hidden|clip)/.test(cs.overflowY) && el.scrollHeight > el.clientHeight + 2) cut.push(`${label(el)} (height)`);
            const r = el.getBoundingClientRect();
            for (let a = el.parentElement; a && a !== root.parentElement; a = a.parentElement) {
                const s = getComputedStyle(a);
                if (!/(hidden|clip)/.test(s.overflowX)) continue;
                const ar = a.getBoundingClientRect();
                if (r.right > ar.right + 1.5 || r.left < ar.left - 1.5) { cut.push(`${label(el)} clipped by an ancestor`); break; }
            }
        }
        const leaves = all.filter((el) => ownText(el) || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.getAttribute("role") === "switch");
        const overlaps: string[] = [];
        for (let i = 0; i < leaves.length; i++) {
            const a = leaves[i], ra = shown(a);
            for (let j = i + 1; j < leaves.length; j++) {
                const b = leaves[j];
                if (a.contains(b) || b.contains(a)) continue;
                const rb = shown(b);
                const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
                const h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
                if (w > 2 && h > 2) overlaps.push(`${label(a)} <> ${label(b)} (${Math.round(w)}x${Math.round(h)})`);
            }
        }
        const covered: string[] = [];
        for (const el of all.filter((x) => /^(BUTTON|INPUT|TEXTAREA|A)$/.test(x.tagName) || x.getAttribute("role") === "switch")) {
            if (blocked(el)) continue;
            el.scrollIntoView({ block: "center", inline: "nearest" });
            const r = el.getBoundingClientRect();
            const x = Math.min(innerWidth - 1, Math.max(0, r.left + r.width / 2)), y = Math.min(innerHeight - 1, Math.max(0, r.top + r.height / 2));
            if (!hits(el, x, y)) { const top = document.elementFromPoint(x, y); covered.push(`${label(el)} <- ${top ? label(top) : "nothing"}`); }
        }
        return { missing: false, outside: unique(outside), cut: unique(cut), shortened: unique(shortened), overlaps: unique(overlaps), covered: unique(covered) };
    };

    // With the page scrolled to its very end, where the floating buttons live: nothing in view may sit under them.
    const endOfPage = (selector: string): string[] => {
        const root = first(selector);
        if (!root) return ["missing"];
        for (let a: Element | null = root; a; a = a.parentElement) a.scrollTop = a.scrollHeight;
        window.scrollTo(0, document.documentElement.scrollHeight);
        const out: string[] = [];
        for (const el of Array.from(root.querySelectorAll("button,input,textarea,a,[role=switch]")).filter(visible)) {
            if (blocked(el)) continue;
            const r = el.getBoundingClientRect();
            // Only controls wholly in view: one cut by the top or bottom edge is reached by scrolling.
            if (r.top < 0 || r.bottom > innerHeight) continue;
            const points = [[r.left + r.width / 2, r.top + r.height / 2], [r.left + 7, r.top + 7], [r.right - 7, r.bottom - 7]];
            if (points.some(([x, y]) => !hits(el, x, y))) out.push(label(el));
        }
        return unique(out);
    };

    const dialogChecks = (): DialogResult => {
        const dlg = openDialog();
        if (!dlg) return { missing: true, rect: [], inside: false, scrollsInside: false, unreachable: [], noHorizontalScroll: false };
        const r = dlg.getBoundingClientRect();
        const body = Array.from(dlg.children).find((c) => /(auto|scroll)/.test(getComputedStyle(c).overflowY));
        const unreachable: string[] = [];
        for (const el of Array.from(dlg.querySelectorAll("button,input:not([type=file]),textarea,[role=switch],[role=combobox]")).filter(visible)) {
            el.scrollIntoView({ block: "center" });
            const e = el.getBoundingClientRect();
            const ok = e.left >= r.left - 1 && e.right <= r.right + 1 && e.top >= r.top - 1 && e.bottom <= r.bottom + 1 && e.top >= -1 && e.bottom <= innerHeight + 1;
            if (!ok) unreachable.push(`${label(el)} outside`);
            else if (!blocked(el) && !hits(el, e.left + e.width / 2, e.top + e.height / 2)) unreachable.push(`${label(el)} covered`);
        }
        return {
            missing: false, rect: [r.left, r.top, r.right, r.bottom].map(Math.round), inside: insideWindow(r),
            scrollsInside: body ? body.scrollHeight > body.clientHeight + 1 : false, unreachable,
            noHorizontalScroll: document.documentElement.scrollWidth <= innerWidth + 1 && (!body || body.scrollWidth <= body.clientWidth + 1),
        };
    };

    const windowLayout = (): WindowLayout => {
        const dlg = openDialog() as HTMLElement;
        const d = dlg.getBoundingClientRect();
        const titleId = dlg.getAttribute("aria-labelledby");
        const title = titleId ? document.getElementById(titleId) : null;
        const cards = Array.from(dlg.querySelectorAll("[data-document-card]"));
        const [a, b] = cards.map((c) => c.getBoundingClientRect());
        const sideBySide = b.left >= a.right - 0.5 && Math.abs(a.top - b.top) <= 1;
        const stacked = b.top >= a.bottom - 0.5 && Math.abs(a.left - b.left) <= 1;
        const items = Array.from(dlg.querySelectorAll("[data-document-item]")).map((item) => {
            const text = item.querySelector("p") as HTMLElement;
            const t = text.getBoundingClientRect();
            const box = (item.querySelector("[data-upload-box]") as HTMLElement).getBoundingClientRect();
            const card = (item.closest("[data-document-card]") as HTMLElement).getBoundingClientRect();
            return { text: text.textContent, boxDirectlyUnder: box.top >= t.bottom - 0.5 && box.top - t.bottom <= 12 && Math.abs(box.left - t.left) <= 1, boxInsideCard: box.left >= card.left && box.right <= card.right + 0.5 };
        });
        const names = Array.from(dlg.querySelectorAll("[data-file-name]")).map((name) => {
            const n = name as HTMLElement;
            const r = n.getBoundingClientRect();
            const box = (n.closest("[data-upload-box]") as HTMLElement).getBoundingClientRect();
            return { insideBox: r.left >= box.left && r.right <= box.right + 0.5, oneLine: r.height < 30, nameOnlyAsText: !n.hasAttribute("title") && n.getAttribute("data-sentry-element") === "number-request-file", shortened: n.scrollWidth > n.clientWidth };
        });
        const submits = cards.map((card) => {
            const button = Array.from(card.querySelectorAll("button")).find((x) => x.textContent === "Submit") as HTMLButtonElement;
            const s = button.getBoundingClientRect();
            const c = card.getBoundingClientRect();
            const lastBox = Array.from(card.querySelectorAll("[data-upload-box]")).pop() as HTMLElement;
            return { insideCard: s.left >= c.left && s.right <= c.right + 0.5 && s.bottom <= c.bottom + 0.5, belowItems: s.top >= lastBox.getBoundingClientRect().bottom - 0.5, disabled: button.disabled };
        });
        const cancel = (Array.from(dlg.querySelectorAll("button")).find((x) => x.textContent === "Cancel") as HTMLElement).getBoundingClientRect();
        const body = Array.from(dlg.children).find((c) => /(auto|scroll)/.test(getComputedStyle(c).overflowY)) as HTMLElement;
        return {
            title: title ? title.textContent : null, titleAtTop: title ? title.getBoundingClientRect().top - d.top < 30 : false,
            cardTitles: cards.map((c) => (c.querySelector("h3") as HTMLElement).textContent ?? ""),
            arrangement: sideBySide ? "side by side" : stacked ? "stacked" : "other", businessFirst: sideBySide ? a.left < b.left : a.top < b.top,
            equalWidth: Math.abs(a.width - b.width) <= 1, equalHeight: Math.abs(a.height - b.height) <= 1,
            items, names, submits,
            cancelInsideWindow: cancel.top >= 0 && cancel.bottom <= innerHeight && cancel.left >= 0 && cancel.right <= innerWidth,
            cancelBelowCards: cancel.top >= body.getBoundingClientRect().bottom - 0.5,
        };
    };

    const toastChecks = (): ToastResult => {
        const toasts = Array.from(document.querySelectorAll('[role="status"][aria-label$=" notification"], [role="alert"][aria-label$=" notification"]')).filter(visible);
        const toast = toasts.find((t) => t.getAttribute("aria-label") === "Success notification");
        if (!toast) return { missing: true, count: toasts.length, text: "", parts: 0, inside: false, titleCut: false, lines: 0, needs: 0, room: 0, onTop: false, overlapsCard: false, controlsUnder: [] };
        const r = toast.getBoundingClientRect();
        const parts = Array.from(toast.querySelectorAll("div")).filter((el) => el.children.length === 0 && (el.textContent ?? "").trim() !== "");
        const card = document.querySelector("[data-telephony-buy-card]");
        const cr = card ? card.getBoundingClientRect() : null;
        const crosses = (o: DOMRect) => Math.min(r.right, o.right) - Math.max(r.left, o.left) > 1 && Math.min(r.bottom, o.bottom) - Math.max(r.top, o.top) > 1;
        const controlsUnder = Array.from(document.querySelectorAll("button,a,input,[role=tab]")).filter((el) => !toast.contains(el) && visible(el) && crosses(el.getBoundingClientRect())).map(label);
        // The title line: how many lines it takes, how wide the sentence is on one line, and how much room the line has
        // (the row it shares with the Dismiss button, less that button and the gap between them).
        const title = parts[0] as HTMLElement;
        const row = title.parentElement?.parentElement as HTMLElement;
        const dismiss = row.querySelector("button") as HTMLElement;
        const one = title.cloneNode(true) as HTMLElement;
        one.style.cssText = "position:absolute;visibility:hidden;white-space:nowrap;width:auto;max-width:none";
        title.parentElement?.appendChild(one);
        const needs = one.getBoundingClientRect().width;
        one.remove();
        const room = row.clientWidth - dismiss.getBoundingClientRect().width - parseFloat(getComputedStyle(row).columnGap || "0");
        const titleStyle = getComputedStyle(title);
        return {
            missing: false, count: toasts.length, text: parts.map((p) => p.textContent).join(" "), parts: parts.length, inside: insideWindow(r),
            titleCut: parts.some((p) => p.scrollWidth > p.clientWidth + 1 || p.scrollHeight > p.clientHeight + 1) || titleStyle.textOverflow === "ellipsis" || titleStyle.whiteSpace === "nowrap",
            lines: Math.round(title.getBoundingClientRect().height / parseFloat(titleStyle.lineHeight)), needs: Math.round(needs * 10) / 10, room: Math.round(room * 10) / 10,
            onTop: Boolean(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)?.closest('[aria-label="Success notification"]')),
            overlapsCard: cr ? crosses(cr) : false, controlsUnder: unique(controlsUnder),
        };
    };

    const listbox = () => {
        const lb = Array.from(document.querySelectorAll('[role="listbox"]')).filter(visible).pop();
        if (!lb) return { missing: true, rect: [], inside: false, onTop: false };
        const r = lb.getBoundingClientRect();
        return { missing: false, rect: [r.left, r.top, r.right, r.bottom].map(Math.round), inside: insideWindow(r), onTop: Boolean(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)?.closest('[role="listbox"]')) };
    };

    const horizontalScroll = () => document.documentElement.scrollWidth > innerWidth + 1;
    const gapAbove = (selector: string) => {
        const el = first(selector);
        const previous = el?.previousElementSibling;
        return el && previous ? Math.round(el.getBoundingClientRect().top - previous.getBoundingClientRect().bottom) : null;
    };

    win.__tb = { staticChecks, endOfPage, dialogChecks, windowLayout, toastChecks, listbox, horizontalScroll, gapAbove };
}

const probe = <K extends keyof Probe>(page: Page, method: K, argument?: string) =>
    page.evaluate(([name, arg]) => {
        const tb = (window as unknown as { __tb: Record<string, (a?: string) => unknown> }).__tb;
        return tb[name as string](arg as string | undefined);
    }, [method, argument] as const) as Promise<ReturnType<Probe[K]>>;

const pickerClicks = (page: Page) => page.evaluate(() => (window as unknown as { __pickerClicks: number }).__pickerClicks);

function expectClean(result: StaticResult, what: string, allowShortened: RegExp | null = null) {
    expect(result.missing, `${what}: found`).toBe(false);
    expect(result.outside, `${what}: outside the device window`).toEqual([]);
    expect(result.cut, `${what}: cut-off text`).toEqual([]);
    expect(result.overlaps, `${what}: overlapping`).toEqual([]);
    expect(result.covered, `${what}: covered controls`).toEqual([]);
    expect(result.shortened.filter((entry) => !(allowShortened && allowShortened.test(entry))), `${what}: shortened text`).toEqual([]);
}

// ───────────────────────── page setup ─────────────────────────

type Posted = { headers: Record<string, string>; body: string };
type Tracker = {
    unanswered: string[]; writes: string[]; all: string[]; recording: boolean;
    /** The number-request POSTs the mock answered, with their headers and raw multipart body. */
    posts: Posted[];
    /** How the next POST is answered: 201, a server refusal (413), or held until `release()` and then refused. */
    answer: "created" | "refused" | "hold";
    release: () => void;
};
const REFUSAL = "Business registration is larger than 10 MB.";
const CREATED = { id: "11111111-1111-1111-1111-111111111111", use_type: "business", status: "submitted", submitted_at: T, documents: [] };

async function openApp(page: Page, options: { theme: Theme; sidebar: Sidebar; role: Role }): Promise<Tracker> {
    let open: () => void = () => {};
    const tracker: Tracker = { unanswered: [], writes: [], all: [], recording: false, posts: [], answer: "created", release: () => open() };
    let gate = new Promise<void>((resolve) => { open = resolve; });
    // Registered first, so it runs last: whatever no mock answered. One write is expected and answered: the line test.
    await page.route((url) => /\/api\/v1(?:\/|$)/.test(url.pathname), async (route) => {
        const request = route.request();
        if (request.resourceType() === "document") return route.fallback();
        const path = new URL(request.url()).pathname.replace(/^.*?\/api\/v1/, "");
        if (request.method() === "POST" && /^\/telephony\/sip\/trunks\/trunk-e2e\/test\/?$/.test(path)) {
            return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(TEST_RESULT) });
        }
        if (request.method() === "POST" && /^\/telephony\/number-requests\/?$/.test(path)) {
            tracker.posts.push({ headers: request.headers(), body: request.postDataBuffer()?.toString("latin1") ?? "" });
            if (tracker.answer === "hold") {
                await gate;
                gate = new Promise<void>((resolve) => { open = resolve; });
            }
            if (tracker.answer !== "created") {
                return route.fulfill({ status: 413, contentType: "application/json", body: JSON.stringify({ error: { code: "file_too_large", message: REFUSAL, details: { field: "business_registration" }, request_id: "r-e2e" } }) });
            }
            return route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(CREATED) });
        }
        (request.method() === "GET" ? tracker.unanswered : tracker.writes).push(`${request.method()} ${path}`);
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Not Found" }) });
    });
    await signInAsE2eUser(page);
    await page.context().addCookies([{ name: "talklee_auth_token", value: E2E_AUTH_TOKEN, url: ORIGIN }]);
    await mockDashboardApi(page);
    await mockPageApis(page);
    await mockApi(page, [...SETTINGS_MOCKS, { path: "/auth/me", body: { ...E2E_USER, role: options.role } }]);
    await page.addInitScript(([theme, collapsed]) => {
        try {
            window.localStorage.setItem("talklee.theme", theme as string);
            window.localStorage.setItem("talklee.sidebar.state.v1", JSON.stringify({ collapsed, mobileOpen: false }));
        } catch { /* storage unavailable */ }
    }, [options.theme, options.sidebar === "collapsed"] as const);
    await page.addInitScript(installProbe);
    await page.clock.setFixedTime(new Date(FIXED_NOW));
    page.on("request", (request) => {
        if (tracker.recording) tracker.all.push(`${request.method()} ${new URL(request.url()).pathname}`);
    });
    return tracker;
}

async function openTelephony(page: Page, role: Role) {
    await page.goto("/settings", { waitUntil: "domcontentloaded" });
    await page.getByRole("tab", { name: "Telephony" }).click();
    if (role === "tenant_admin") {
        await page.getByRole("heading", { name: "Your PBX", exact: true }).waitFor();
        await page.getByRole("combobox", { name: "Shared trunk account" }).waitFor();
        await page.getByRole("button", { name: "Edit Main line" }).waitFor();
    } else {
        await page.getByText(NOTE).waitFor();
    }
    await page.locator("[data-telephony-buy-card]").waitFor();
    // Everything on this tab is measured with the Privacy Policy notice showing above it.
    await expect(page.locator("[data-privacy-policy-notice] p")).toHaveText(NOTICE);
    await page.waitForTimeout(500);
}

const PANEL = '[role="tabpanel"][data-state="active"]';
const CARD = "[data-telephony-buy-card]";
const DIALOG = '[role="dialog"]';
const escape = async (page: Page) => { await page.keyboard.press("Escape"); await page.waitForTimeout(350); };

/** The window, measured as it stands (empty or filled). */
async function expectWindowFits(page: Page, width: number, state: "empty" | "filled") {
    const layout = await probe(page, "windowLayout");
    expect(layout.title, "title").toBe(TITLE);
    // The description, word for word, shown under the heading and read out as the window's description.
    const description = page.getByRole("dialog", { name: TITLE }).getByText(DESCRIPTION, { exact: true });
    await expect(description, "description").toBeVisible();
    await expect(page.getByRole("dialog", { name: TITLE }), "description read out with the window").toHaveAccessibleDescription(DESCRIPTION);
    expect(await description.evaluate((el) => el.previousElementSibling?.textContent ?? null), "description directly under the heading").toBe(TITLE);
    expect(layout.titleAtTop, "title at the top").toBe(true);
    expect(layout.cardTitles).toEqual(["1. Business Use", "2. Personal Use"]);
    expect(layout.arrangement, "cards").toBe(width >= SIDE_BY_SIDE_FROM ? "side by side" : "stacked");
    expect(layout.businessFirst, "Business Use first").toBe(true);
    expect(layout.equalWidth, "equal cards").toBe(true);
    if (layout.arrangement === "side by side") expect(layout.equalHeight, "equal height").toBe(true);
    expect(layout.items.map((item) => item.text)).toEqual([
        "A) Business registration", "B) Photo ID matching the business registration",
        "A) Utility bill (proof of address)", "B) Photo ID matching the utility bill",
    ]);
    for (const item of layout.items) expect(item, `${item.text}: its box directly under it, inside its card`).toMatchObject({ boxDirectlyUnder: true, boxInsideCard: true });
    for (const submit of layout.submits) expect(submit, "Submit ends its card").toMatchObject({ insideCard: true, belowItems: true, disabled: state === "empty" });
    expect(layout.cancelInsideWindow, "Cancel visible without scrolling").toBe(true);
    expect(layout.cancelBelowCards, "Cancel below both cards").toBe(true);
    expect(layout.names.length).toBe(state === "filled" ? 4 : 0);
    for (const name of layout.names) expect(name, "file name inside its box, one line, the name only as text (no title: Sentry records it), with the error-report label").toMatchObject({ insideBox: true, oneLine: true, nameOnlyAsText: true, shortened: true });

    const dialog = await probe(page, "dialogChecks");
    expect(dialog.missing).toBe(false);
    expect(dialog.inside, `window inside the device window ${JSON.stringify(dialog.rect)}`).toBe(true);
    expect(dialog.unreachable, "every box, both Submits and Cancel reachable").toEqual([]);
    expect(dialog.noHorizontalScroll, "no horizontal scroll").toBe(true);
    // File names are shortened on purpose; nothing else may be.
    expectClean(await probe(page, "staticChecks", DIALOG), `${state} window`, /^p:/);
    return { layout, dialog };
}

// ───────────────────────── Settings → Telephony, every width / theme / sidebar / role ─────────────────────────

for (const role of ["tenant_admin", "user"] as const) {
    for (const [width, height] of SIZES) {
        for (const theme of THEMES) {
            for (const sidebar of sidebarsFor(width)) {
                test(`${role === "tenant_admin" ? "admin" : "non-admin"} ${width}x${height} ${theme} sidebar ${sidebar}: card, window, message${role === "tenant_admin" ? " and the section's other panels" : ""}`, async ({ page }) => {
                    await page.setViewportSize({ width, height });
                    const tracker = await openApp(page, { theme, sidebar, role });
                    await openTelephony(page, role);
                    expect(await page.evaluate(() => document.documentElement.classList.contains("dark"))).toBe(theme === "dark");

                    // ── the card and Buy Now ──
                    expect(await probe(page, "horizontalScroll"), "horizontal scroll").toBe(false);
                    const card = page.locator(CARD);
                    // The gap the Telephony cards keep between themselves (24px). Read with the pointer off the page's
                    // cards: a Card lifts 2px while the pointer is on it (components/ui/card.tsx:13).
                    await page.mouse.move(1, 1);
                    await page.waitForTimeout(250);
                    expect(await probe(page, "gapAbove", CARD), "gap above the card").toBe(24);
                    await expect(card.locator("p")).toHaveText(CARD_LINES);
                    expectClean(await probe(page, "staticChecks", CARD), "card");
                    const lineBottom = await card.locator("p").last().evaluate((el) => el.getBoundingClientRect().bottom);
                    const buttonTop = await card.getByRole("button", { name: "Buy Now" }).evaluate((el) => el.getBoundingClientRect().top);
                    expect(buttonTop, "Buy Now below both lines").toBeGreaterThanOrEqual(lineBottom);
                    // Last in the tab.
                    expect(await card.evaluate((el) => el.nextElementSibling === null && el.parentElement?.nextElementSibling === null)).toBe(true);
                    if (role === "tenant_admin") {
                        const sectionGap = await page.locator(`${PANEL} h2`, { hasText: "Your PBX" }).first().evaluate((heading) => {
                            const pbxCard = heading.closest(".rounded-xl") as HTMLElement;
                            return Math.round(pbxCard.getBoundingClientRect().top - (pbxCard.previousElementSibling as HTMLElement).getBoundingClientRect().bottom);
                        });
                        expect(sectionGap, "gap between the existing Telephony cards").toBe(24);
                    } else {
                        expect(await card.evaluate((el, note) => el.previousElementSibling?.textContent === note, NOTE), "directly after the note").toBe(true);
                        await expect(page.getByRole("heading", { name: "Your PBX", exact: true })).toHaveCount(0);
                    }
                    // The whole tab with the card in it. The closed "Shared trunk account" select shortens its own label: as before this change.
                    expectClean(await probe(page, "staticChecks", PANEL), "Telephony tab", /^span:— None \(use my own trunk\) —/);
                    expect(await probe(page, "endOfPage", PANEL), "covered at the end of the page").toEqual([]);
                    await expect(page.getByText(TITLE)).toHaveCount(0);

                    // ── the window, empty ──
                    // The card is put in the middle of the screen first. The message is the app's own toast, fixed at
                    // the top of the screen: it lies over whatever is scrolled there, so where the card is matters.
                    await card.evaluate((el) => el.scrollIntoView({ block: "center" }));
                    tracker.recording = true;
                    const buy = page.getByRole("button", { name: "Buy Now" });
                    await buy.focus();
                    await page.keyboard.press("Enter");
                    await expect(page.getByRole("dialog", { name: TITLE })).toBeVisible();
                    await page.waitForTimeout(600);
                    await expect(page.getByRole("dialog").getByText(EMPTY)).toHaveCount(4);
                    const empty = await expectWindowFits(page, width, "empty");

                    // ── the window, a 190-character file name in all four boxes ──
                    const inputs = page.getByRole("dialog").locator('input[type="file"]');
                    for (let i = 0; i < 4; i++) await inputs.nth(i).setInputFiles(pdf(`${i + 1}${LONG_NAME.slice(1)}`));
                    await page.waitForTimeout(250);
                    const filled = await expectWindowFits(page, width, "filled");
                    expect(LONG_NAME.length).toBe(190);
                    console.log(`[window] ${role} ${width}x${height} ${theme} ${sidebar}: ${filled.layout.arrangement}; empty ${JSON.stringify(empty.dialog.rect)} scrolls=${empty.dialog.scrollsInside}; filled ${JSON.stringify(filled.dialog.rect)} scrolls=${filled.dialog.scrollsInside}`);

                    // change, remove, drop
                    const [chooser] = await Promise.all([page.waitForEvent("filechooser"), page.getByRole("dialog").getByRole("button", { name: "Choose another" }).nth(1).click()]);
                    await chooser.setFiles(pdf("changed.pdf"));
                    await expect(page.getByRole("dialog").locator("[data-file-name]").nth(1)).toHaveText("changed.pdf");
                    await page.getByRole("dialog").getByRole("button", { name: "Remove" }).nth(3).click();
                    await expect(page.getByRole("dialog").getByText(EMPTY)).toHaveCount(1);
                    const transfer = await page.evaluateHandle(() => { const data = new DataTransfer(); data.items.add(new File(["x"], "dropped.pdf", { type: "application/pdf" })); return data; });
                    await page.getByRole("dialog").locator("[data-upload-box]").nth(3).dispatchEvent("drop", { dataTransfer: transfer });
                    await expect(page.getByRole("dialog").locator("[data-file-name]").nth(3)).toHaveText("dropped.pdf");

                    // ── Submit: one POST is sent, the window closes only after the server answered 201, the message is shown ──
                    await page.getByRole("region", { name: "1. Business Use" }).getByRole("button", { name: "Submit" }).click();
                    await expect(page.getByRole("dialog", { name: TITLE })).toHaveCount(0);
                    await expect(page.getByRole("status", { name: "Success notification" })).toBeVisible();
                    await page.waitForTimeout(700);
                    const toast = await probe(page, "toastChecks");
                    tracker.recording = false;
                    expect(toast.missing).toBe(false);
                    expect(toast.text, "message word for word").toBe(MESSAGE);
                    expect(toast.parts, "one message: a title with no second line").toBe(1);
                    // One line where the sentence fits the title line, wrapped onto the next line where it does not.
                    if (toast.needs <= toast.room) expect(toast.lines, `one line (needs ${toast.needs}px, room ${toast.room}px)`).toBe(1);
                    else expect(toast.lines, `wrapped (needs ${toast.needs}px, room ${toast.room}px)`).toBeGreaterThanOrEqual(2);
                    expect(toast.count, "no other message on screen").toBe(1);
                    expect(toast.inside, "message inside the device window").toBe(true);
                    expect(toast.titleCut, "message text cut off").toBe(false);
                    expect(toast.onTop, "message not covered").toBe(true);
                    expect(toast.overlapsCard, "message over the card (card in the middle of the screen)").toBe(false);
                    expect(await probe(page, "horizontalScroll"), "horizontal scroll with the message").toBe(false);
                    console.log(`[message] ${role} ${width}x${height} ${theme} ${sidebar}: ${toast.lines} line(s), sentence ${toast.needs}px, room ${toast.room}px; controls under the message: ${toast.controlsUnder.join(" | ") || "none"}`);
                    expect(await buy.evaluate((el) => document.activeElement === el), "focus back on Buy Now").toBe(true);
                    // The only write while files were chosen, changed, removed, dropped and submitted: exactly one POST of the two Business documents.
                    expect(tracker.all.filter((entry) => !entry.startsWith("GET /api/v1/")), "requests from the card and window").toEqual(["POST /api/v1/telephony/number-requests"]);
                    expect(tracker.writes, "write requests no mock answered").toEqual([]);
                    expect(tracker.posts.length, "one POST").toBe(1);
                    const posted = tracker.posts[0];
                    expect(posted.body, "use_type field").toContain('name="use_type"');
                    expect(posted.body.indexOf('name="use_type"'), "use_type first").toBeLessThan(posted.body.indexOf('name="business_registration"'));
                    expect(posted.body, "business_registration field").toContain('name="business_registration"');
                    expect(posted.body, "business_photo_id field").toContain('name="business_photo_id"');
                    expect(posted.body, "no Personal field").not.toContain('name="utility_bill"');
                    expect(posted.headers["idempotency-key"], "Idempotency-Key").toMatch(/^[A-Za-z0-9_-]{8,128}$/);

                    // ── closes like the app's other windows ──
                    for (const close of ["Cancel", "Escape", "outside"] as const) {
                        await buy.click();
                        await expect(page.getByRole("dialog", { name: TITLE })).toBeVisible();
                        await expect(page.getByRole("dialog").getByText(EMPTY), "a reopened window starts empty").toHaveCount(4);
                        if (close === "Cancel") await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
                        else if (close === "Escape") await page.keyboard.press("Escape");
                        else await page.mouse.click(Math.max(1, Math.floor((empty.dialog.rect[0] ?? 12) / 2)), Math.floor(height / 2));
                        await expect(page.getByRole("dialog", { name: TITLE }), close).toHaveCount(0);
                        expect(await buy.evaluate((el) => document.activeElement === el), `${close}: focus back on Buy Now`).toBe(true);
                    }

                    // ── every other floating panel in the section (admin only: other roles have none) ──
                    if (role === "tenant_admin") {
                        await page.getByRole("status", { name: "Success notification" }).getByRole("button", { name: "Dismiss notification" }).click({ timeout: 1500 }).catch(() => undefined);
                        // The pointer is left where that button was; once the message is gone it rests on the page. Park it.
                        await page.mouse.move(1, 1);

                        const pool = page.getByRole("combobox", { name: "Shared trunk account" });
                        await pool.scrollIntoViewIfNeeded();
                        await pool.click();
                        await page.waitForTimeout(300);
                        expect(await probe(page, "listbox"), "Shared trunk account list").toMatchObject({ missing: false, inside: true, onTop: true });
                        expect(await probe(page, "horizontalScroll")).toBe(false);
                        await escape(page);

                        await page.getByRole("button", { name: "Connect PBX" }).click();
                        await page.waitForTimeout(450);
                        expect(await probe(page, "dialogChecks"), "Connect PBX window").toMatchObject({ missing: false, inside: true, unreachable: [], noHorizontalScroll: true });
                        await page.getByRole("button", { name: "Advanced settings" }).click();
                        await page.waitForTimeout(200);
                        expect(await probe(page, "dialogChecks"), "Connect PBX window, advanced").toMatchObject({ missing: false, inside: true, unreachable: [], noHorizontalScroll: true });
                        expectClean(await probe(page, "staticChecks", DIALOG), "Connect PBX window");
                        for (const name of ["Transport", "DTMF mode"]) {
                            const select = page.getByRole("dialog").getByRole("combobox", { name });
                            await select.scrollIntoViewIfNeeded();
                            await select.click();
                            await page.waitForTimeout(300);
                            expect(await probe(page, "listbox"), `${name} list`).toMatchObject({ missing: false, inside: true, onTop: true });
                            await escape(page);
                            await expect(page.getByRole("dialog"), `${name}: Escape closes only the list`).toBeVisible();
                        }
                        await escape(page);

                        for (const [button, name] of [["Edit Main line", "Edit window"], ["Delete Main line", "Delete line window"]] as const) {
                            await page.getByRole("button", { name: button }).click();
                            await page.waitForTimeout(450);
                            expect(await probe(page, "dialogChecks"), name).toMatchObject({ missing: false, inside: true, unreachable: [], noHorizontalScroll: true });
                            expectClean(await probe(page, "staticChecks", DIALOG), name);
                            await escape(page);
                        }

                        const forget = page.locator(PANEL).locator("button:has(svg.lucide-trash-2)").first();
                        await forget.scrollIntoViewIfNeeded();
                        await forget.click();
                        await page.waitForTimeout(450);
                        expect(await probe(page, "dialogChecks"), "Forget credentials window").toMatchObject({ missing: false, inside: true, unreachable: [], noHorizontalScroll: true });
                        expectClean(await probe(page, "staticChecks", DIALOG), "Forget credentials window");
                        await escape(page);

                        // The Test result panel, with the line test answered here.
                        await page.getByRole("button", { name: "Test Main line" }).click();
                        const result = page.getByRole("region", { name: "Test result for Main line" });
                        await expect(result).toBeVisible();
                        await page.waitForTimeout(300);
                        expectClean(await probe(page, "staticChecks", '[aria-label="Test result for Main line"]'), "Test result panel");
                        expectClean(await probe(page, "staticChecks", "[data-line-id]"), "line card with its test result");
                        expect(await probe(page, "horizontalScroll"), "horizontal scroll with the test result").toBe(false);
                        expectClean(await probe(page, "staticChecks", CARD), "card, after the other panels");
                    }

                    expect(tracker.unanswered, "requests no mock answered").toEqual([]);
                });
            }
        }
    }
}

// ───────────────────────── sending, a server refusal and a refusal in the browser, every width ─────────────────────────

for (const [width, height] of SIZES) {
    test(`admin ${width}x${height}: the window while sending, after a server refusal and after a refusal in the browser`, async ({ page }) => {
        await page.setViewportSize({ width, height });
        const sidebar = sidebarsFor(width)[0];
        const tracker = await openApp(page, { theme: "light", sidebar, role: "tenant_admin" });
        await openTelephony(page, "tenant_admin");
        await page.locator(CARD).evaluate((el) => el.scrollIntoView({ block: "center" }));
        await page.getByRole("button", { name: "Buy Now" }).click();
        const dialog = page.getByRole("dialog", { name: TITLE });
        await expect(dialog).toBeVisible();
        await page.waitForTimeout(500);
        const inputs = dialog.locator('input[type="file"]');
        const submits = dialog.getByRole("button", { name: "Submit" });
        const cancel = dialog.getByRole("button", { name: "Cancel" });
        const measure = async (what: string) => {
            const fit = await probe(page, "dialogChecks");
            expect(fit.missing, `${what}: window present`).toBe(false);
            expect(fit.inside, `${what}: window inside the device window ${JSON.stringify(fit.rect)}`).toBe(true);
            expect(fit.unreachable, `${what}: every control reachable`).toEqual([]);
            expect(fit.noHorizontalScroll, `${what}: no horizontal scroll`).toBe(true);
            expectClean(await probe(page, "staticChecks", DIALOG), what, /^p:/);
            expect(await probe(page, "horizontalScroll"), `${what}: horizontal scroll`).toBe(false);
        };

        // ── a refusal in the browser: not taken, the server's sentence under the box, nothing sent ──
        await inputs.nth(0).setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("x") });
        const fileError = dialog.locator("[data-file-error]");
        await expect(fileError).toHaveText("Business registration must be a PDF, JPG or PNG file.");
        await expect(dialog.getByText(EMPTY)).toHaveCount(4);
        await measure("browser refusal");
        expect(tracker.posts.length, "nothing sent for a refused file").toBe(0);

        // ── the two Business files, then Submit with the answer held: the sending state ──
        await inputs.nth(0).setInputFiles(pdf("registration.pdf"));
        await inputs.nth(1).setInputFiles(pdf("photo-id.pdf"));
        await expect(fileError).toHaveCount(0);
        tracker.answer = "hold";
        await submits.first().click();
        await expect.poll(() => tracker.posts.length).toBe(1);
        await expect(submits.first()).toBeDisabled();
        await expect(submits.nth(1)).toBeDisabled();
        await expect(cancel).toBeDisabled();
        // The lock covers the upload boxes of both cards too: the hidden inputs and every Choose another / Remove / upload button.
        await expect(inputs.first()).toBeDisabled();
        await expect(dialog.getByRole("button", { name: "Remove" }).first()).toBeDisabled();
        await expect(dialog.getByRole("button", { name: "Choose another" }).first()).toBeDisabled();
        await expect(dialog.getByRole("button", { name: EMPTY }).first()).toBeDisabled();
        await expect(dialog.locator("[data-document-grid]")).toHaveAttribute("aria-busy", "true");
        await page.keyboard.press("Escape");
        await page.waitForTimeout(350);
        await expect(dialog, "Escape does not close the window while sending").toBeVisible();
        await expect(page.getByRole("status", { name: "Success notification" })).toHaveCount(0);
        await measure("sending");

        // ── the server refuses: its message in that card, both files still there ──
        tracker.release();
        const submitError = dialog.locator("[data-submit-error]");
        await expect(submitError).toHaveText(REFUSAL);
        await expect(submitError, "keyboard focus moves to the card's message").toBeFocused();
        await expect(dialog.locator("[data-file-name]")).toHaveCount(2);
        await expect(submits.first()).toBeEnabled();
        await expect(page.getByRole("status", { name: "Success notification" })).toHaveCount(0);
        await measure("server refusal");

        // ── a retry with the same files re-uses the key and is answered 201 ──
        tracker.answer = "created";
        await submits.first().click();
        await expect.poll(() => tracker.posts.length, { message: "the retry POST was sent", timeout: 30_000 }).toBe(2);
        await expect(page.getByRole("dialog", { name: TITLE })).toHaveCount(0, { timeout: 30_000 });
        await expect(page.getByRole("status", { name: "Success notification" })).toBeVisible();
        expect(tracker.posts.length, "two POSTs in all").toBe(2);
        expect(tracker.posts[1].headers["idempotency-key"], "same key on the retry").toBe(tracker.posts[0].headers["idempotency-key"]);
        for (const posted of tracker.posts) {
            expect(posted.body.indexOf('name="use_type"'), "use_type first").toBeGreaterThanOrEqual(0);
            expect(posted.body.indexOf('name="use_type"')).toBeLessThan(posted.body.indexOf('name="business_registration"'));
            expect(posted.body).toContain('name="business_photo_id"');
        }
        expect(tracker.writes, "write requests no mock answered").toEqual([]);
        expect(tracker.unanswered, "requests no mock answered").toEqual([]);
    });
}

// ───────────────────────── the card hover lift does not flicker ─────────────────────────

/*
 * A Card lifts 2px while the pointer is on it (src/components/ui/card.tsx). With the pointer resting
 * about 3px above a card's bottom edge the card used to flip between lifted and not on every frame,
 * and everything inside it moved with it: Playwright's "wait until the element stops moving" then
 * never passed (the timeout seen on 20 November 2026). These tests rest the pointer at every half
 * pixel across that edge and require the card to stay still, with the lift and the shadow as before.
 */
type CardReading = { top: number; bottom: number; inParent: number; x: number; translate: string; shadow: string; hovered: boolean };
type Stillness = { positions: number; hovered: boolean; translate: string };
const SHIMMER_SIZES: Array<[number, number]> = [[320, 568], [768, 1024], [1280, 800]];
const SHIMMER_CARDS = ["Your PBX", "Twilio", "Buy Now"] as const;

for (const [width, height] of SHIMMER_SIZES) {
    for (const theme of THEMES) {
        test(`card hover ${width}x${height} ${theme}: lifts 2px, keeps its shadow and stays still with the pointer near its bottom edge`, async ({ page }) => {
            await page.setViewportSize({ width, height });
            await openApp(page, { theme, sidebar: width >= 1024 ? "expanded" : "drawer", role: "tenant_admin" });
            await openTelephony(page, "tenant_admin");
            // Mark the three cards: two by their heading, the Buy Now card by itself.
            await page.evaluate(() => {
                const lifted = (el: Element | null) => { let node = el; while (node && !(typeof node.className === "string" && node.className.includes("hover:-translate-y-0.5") && node.className.includes("rounded-xl"))) node = node.parentElement; return node; };
                for (const name of ["Your PBX", "Twilio"]) {
                    const heading = Array.from(document.querySelectorAll('[role="tabpanel"] h2')).find((h) => (h.textContent ?? "").trim() === name);
                    lifted(heading ?? null)?.setAttribute("data-shimmer-card", name);
                }
                document.querySelector("[data-telephony-buy-card]")?.setAttribute("data-shimmer-card", "Buy Now");
            });
            for (const name of SHIMMER_CARDS) {
                const card = page.locator(`[data-shimmer-card="${name}"]`);
                await expect(card, name).toHaveCount(1);
                const read = () => card.evaluate((el): CardReading => {
                    const r = el.getBoundingClientRect();
                    const parent = (el.parentElement as HTMLElement).getBoundingClientRect();
                    const cs = getComputedStyle(el);
                    return { top: r.top, bottom: r.bottom, inParent: Math.round((r.top - parent.top) * 100) / 100, x: Math.round(r.left + r.width / 2), translate: cs.translate, shadow: cs.boxShadow, hovered: el.matches(":hover") };
                });
                // Bring the card's bottom edge to the middle of the screen, without the page's smooth scrolling.
                await page.mouse.move(1, 1);
                await card.evaluate((el) => {
                    const main = document.getElementById("main-content");
                    const want = () => el.getBoundingClientRect().bottom - Math.round(innerHeight / 2);
                    main?.scrollBy({ top: want(), behavior: "instant" });
                    window.scrollBy({ top: want(), behavior: "instant" });
                });
                await page.waitForTimeout(400);
                const rest = await read();
                expect(rest.hovered, `${name}: at rest`).toBe(false);
                expect(rest.translate, `${name}: no lift at rest`).toBe("none");
                expect(rest.bottom, `${name}: bottom edge on screen`).toBeLessThan(height - 20);

                // The pointer just below the card. A card that holds this one (the Telephony cards sit inside the
                // "Account & Security" card) lifts under the pointer too, so this is where the bottom edge really is.
                await page.mouse.move(rest.x, rest.bottom + 12, { steps: 3 });
                await page.waitForTimeout(400);
                const beside = await read();
                expect(beside.hovered, `${name}: not hovered from just below`).toBe(false);
                const edge = beside.bottom;

                // The lift and the shadow, with the pointer well inside the card.
                await page.mouse.move(rest.x, edge - 20, { steps: 5 });
                await page.waitForTimeout(450);
                const lifted = await read();
                expect(lifted.hovered, `${name}: hovered`).toBe(true);
                expect(lifted.translate, `${name}: lifts 2px`).toBe("0px -2px");
                expect(Math.round((beside.inParent - lifted.inParent) * 100) / 100, `${name}: moved up 2px`).toBe(2);
                expect(lifted.shadow, `${name}: hover shadow`).not.toBe(rest.shadow);
                expect(lifted.shadow, `${name}: hover shadow`).not.toBe("none");

                // The pointer resting at every half pixel from 6px above the bottom edge to the edge itself.
                const still = () => card.evaluate(async (el): Promise<Stillness> => {
                    const tops = new Set<string>();
                    for (let i = 0; i < 45; i++) { tops.add(el.getBoundingClientRect().top.toFixed(2)); await new Promise((r) => requestAnimationFrame(r)); }
                    return { positions: tops.size, hovered: el.matches(":hover"), translate: getComputedStyle(el).translate };
                });
                for (let above = 6; above >= 0.5; above -= 0.5) {
                    await page.mouse.move(rest.x, edge + 12, { steps: 2 });
                    await page.waitForTimeout(250);
                    await page.mouse.move(rest.x, edge - above - 30);
                    await page.mouse.move(rest.x, edge - above, { steps: 6 });
                    await page.waitForTimeout(350);
                    const reading = await still();
                    expect(reading.positions, `${name}: pointer ${above}px above the bottom edge: the card stays still`).toBe(1);
                }
                // What Playwright itself says, in the exact setup that timed out: pointer 3px above the edge.
                await page.mouse.move(rest.x, edge - 33);
                await page.mouse.move(rest.x, edge - 3, { steps: 6 });
                await card.locator("button, a").last().scrollIntoViewIfNeeded({ timeout: 4000 });

                // Just below the card nothing is taken over: the card is not hovered and is not what the pointer is on.
                for (const below of [1, 2.5]) {
                    await page.mouse.move(rest.x, edge - 20, { steps: 3 });
                    await page.waitForTimeout(250);
                    await page.mouse.move(rest.x, edge + below, { steps: 4 });
                    await page.waitForTimeout(350);
                    const under = await card.evaluate((el, [x, y]) => ({ hovered: el.matches(":hover"), onCard: el.contains(document.elementFromPoint(x, y)), translate: getComputedStyle(el).translate }), [rest.x, edge + below] as const);
                    expect(under, `${name}: ${below}px below the card`).toEqual({ hovered: false, onCard: false, translate: "none" });
                }
                // And it comes back to exactly where it was.
                await page.mouse.move(1, 1);
                await page.waitForTimeout(400);
                const after = await read();
                expect({ top: after.top, bottom: after.bottom, translate: after.translate, shadow: after.shadow }, `${name}: back at rest`).toEqual({ top: rest.top, bottom: rest.bottom, translate: rest.translate, shadow: rest.shadow });
            }
        });
    }
}

// ───────────────────────── Enter and Space on an upload box ─────────────────────────

/*
 * An earlier measurement lost the file picker on the first Enter press in 3 of
 * 38 runs. The page counts, in itself, every click that reaches the hidden
 * file input (installProbe): that click is what asks the browser for the
 * picker. Diagnostic runs on 20 November 2026 (240 fresh pages) found two
 * causes, neither of them in the card:
 *
 *   A. A script that focuses a box in the same instant the window opens can be
 *      overtaken by the shared Modal, which moves focus to the window one timer
 *      tick after it opens (src/components/ui/modal.tsx:116-124). The key then
 *      lands on the window, not on the box. A person cannot Tab to a box inside
 *      that tick. These tests wait until the window has focus, as a person does.
 *   B. A test that starts listening for the picker in the same instant it
 *      presses can miss it: the page did ask (the count below is 1), the test
 *      was not listening yet. The first test listens before the page loads and
 *      must see every press; the second listens late on purpose, asserts the
 *      page-side count and only reports what the late listener missed.
 */
const openWindowByKeyboard = async (page: Page) => {
    await page.getByRole("button", { name: "Buy Now" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog", { name: TITLE })).toBeFocused();
};
const PICKER_ROUNDS = 45;
const pickerContext = (round: number) => ({ baseURL: ORIGIN, viewport: { width: [375, 1366, 1920][round % 3], height: 800 }, reducedMotion: "reduce" as const });

test("Enter and Space open the file picker on the first press, every time", async ({ browser }) => {
    test.setTimeout(900_000);
    let seen = 0;
    for (let round = 0; round < PICKER_ROUNDS; round++) {
        const key = round % 2 === 0 ? "Enter" : "Space";
        const context = await browser.newContext(pickerContext(round));
        const page = await context.newPage();
        let choosers = 0;
        page.on("filechooser", () => { choosers += 1; });
        await openApp(page, { theme: "light", sidebar: "expanded", role: "user" });
        await openTelephony(page, "user");
        await openWindowByKeyboard(page);
        await page.getByRole("dialog").locator("[data-upload-box] button").nth(round % 4).focus();
        await page.keyboard.press(key);
        await expect.poll(() => choosers, { timeout: 5000, message: `round ${round + 1}: ${key}` }).toBe(1);
        expect(await pickerClicks(page), `round ${round + 1}: the page asked for the picker once`).toBe(1);
        seen += choosers;
        await context.close();
    }
    console.log(`[picker] listening first: the picker opened on ${seen}/${PICKER_ROUNDS} first presses`);
    expect(seen).toBe(PICKER_ROUNDS);
});

test("the page asks for the file picker on every first press, even when the test listens late", async ({ browser }) => {
    test.setTimeout(900_000);
    let pageAsked = 0;
    let testSaw = 0;
    const missed: string[] = [];
    for (let round = 0; round < PICKER_ROUNDS; round++) {
        const trigger = (["Enter", "Space", "click"] as const)[round % 3];
        const context = await browser.newContext(pickerContext(round));
        const page = await context.newPage();
        await openApp(page, { theme: "light", sidebar: "expanded", role: "user" });
        await openTelephony(page, "user");
        await openWindowByKeyboard(page);
        const box = page.getByRole("dialog").locator("[data-upload-box] button").first();
        await box.focus();
        const waiting = page.waitForEvent("filechooser", { timeout: 3000 }).then(() => true, () => false);
        const act = trigger === "click" ? box.click() : page.keyboard.press(trigger);
        const [saw] = await Promise.all([waiting, act]);
        const asked = await pickerClicks(page);
        pageAsked += asked === 1 ? 1 : 0;
        if (saw) testSaw += 1; else missed.push(`round ${round + 1} (${trigger})`);
        expect(asked, `round ${round + 1} (${trigger}): the page asked for the picker exactly once`).toBe(1);
        await context.close();
    }
    console.log(`[picker] listening late: the page asked ${pageAsked}/${PICKER_ROUNDS}; the late listener saw ${testSaw}/${PICKER_ROUNDS}; not seen: ${missed.join(", ") || "none"}`);
    expect(pageAsked).toBe(PICKER_ROUNDS);
});

// ───────────────────────── the privacy sentence and the docs part ─────────────────────────

type TextBlock = { found: number; inside: boolean; cut: boolean; overlaps: boolean };

/** The elements matched by `selector` (optionally only those holding `text`): inside the width, not cut, not overlapping their neighbours. */
function textBlocks(page: Page, selector: string, text: string | null) {
    return page.evaluate(([sel, wanted]) => {
        const list = Array.from(document.querySelectorAll(sel as string)).filter((el) => !wanted || (el.textContent ?? "").includes(wanted as string));
        const result: TextBlock = { found: list.length, inside: true, cut: false, overlaps: false };
        for (const el of list) {
            const r = el.getBoundingClientRect();
            if (r.left < -1 || r.right > innerWidth + 1 || r.width < 2) result.inside = false;
            if (el.scrollWidth > el.clientWidth + 1) result.cut = true;
            for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
                const s = getComputedStyle(a);
                const ar = a.getBoundingClientRect();
                if (/(hidden|clip)/.test(s.overflowX) && (r.right > ar.right + 1.5 || r.left < ar.left - 1.5)) result.cut = true;
            }
            for (const other of [el.previousElementSibling, el.nextElementSibling]) {
                if (!other) continue;
                const o = other.getBoundingClientRect();
                if (o.width < 2 || o.height < 2) continue;
                if (Math.min(r.right, o.right) - Math.max(r.left, o.left) > 2 && Math.min(r.bottom, o.bottom) - Math.max(r.top, o.top) > 2) result.overlaps = true;
            }
        }
        return result;
    }, [selector, text] as const);
}

async function publicPage(page: Page, theme: Theme, path: string) {
    await page.addInitScript((value) => { try { window.localStorage.setItem("talklee.theme", value); } catch { /* storage unavailable */ } }, theme);
    // Signed out: the session reads answer 401 and nothing else is asked of a backend.
    await page.route((url) => /\/api\/v1(?:\/|$)/.test(url.pathname), (route) =>
        route.request().resourceType() === "document" ? route.fallback() : route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Not authenticated" }) }));
    await page.goto(path, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(400);
    expect(await page.evaluate(() => document.documentElement.classList.contains("dark"))).toBe(theme === "dark");
}

for (const [width, height] of SIZES) {
    for (const theme of THEMES) {
        test(`privacy page ${width}x${height} ${theme}: the documents sentence and the dates`, async ({ page }) => {
            await page.setViewportSize({ width, height });
            await publicPage(page, theme, "/privacy");
            await expect(page.getByText(PRIVACY)).toHaveCount(1);
            await expect(page.getByText("national identity documents")).toHaveCount(0);
            await expect(page.getByText(/^Effective Date:/)).toHaveText(new RegExp(`Effective Date: ${printed(PRIVACY_POLICY_DATES.effectiveDate)}\\s+\\|\\s+Last Updated: ${printed(PRIVACY_POLICY_DATES.lastUpdated)}`));
            await page.getByText(PRIVACY).scrollIntoViewIfNeeded();
            expect(await textBlocks(page, "article li", "If you buy a phone number in Telephony")).toEqual({ found: 1, inside: true, cut: false, overlaps: false });
            expect(await textBlocks(page, "article p", "Effective Date:")).toEqual({ found: 1, inside: true, cut: false, overlaps: false });
            expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), "horizontal scroll").toBe(false);
        });

        test(`docs page ${width}x${height} ${theme}: the Buy Now part of Settings → Telephony`, async ({ page }) => {
            await page.setViewportSize({ width, height });
            await publicPage(page, theme, "/docs/settings-and-security");
            const heading = page.getByRole("heading", { name: "Buy Now", exact: true });
            await expect(heading).toHaveCount(1);
            await heading.scrollIntoViewIfNeeded();
            // Mark everything from the "Buy Now" heading up to the next heading.
            const marked = await heading.evaluate((start) => {
                let count = 0;
                for (let el: Element | null = start; el && (el === start || !/^H[1-6]$/.test(el.tagName)); el = el.nextElementSibling) {
                    el.setAttribute("data-buy-now-docs", "");
                    el.querySelectorAll("li,p,strong").forEach((inner) => inner.setAttribute("data-buy-now-docs", ""));
                    count += 1;
                }
                return count;
            });
            expect(marked, "heading, first lines, intro, card text, steps, closing line").toBeGreaterThanOrEqual(7);
            // The first lines sit directly under the heading, in full.
            const firstLines = heading.locator("xpath=following-sibling::*[1]");
            await expect(firstLines).toHaveText(DOCS_LINES);
            expect(await firstLines.evaluate((el) => el.tagName)).toBe("P");
            await expect(firstLines.locator("strong")).toHaveText("Buy Now");
            expect(await firstLines.evaluate((el) => {
                const r = el.getBoundingClientRect();
                const range = document.createRange();
                range.selectNodeContents(el);
                const text = range.getBoundingClientRect();
                return { inside: r.left >= -1 && r.right <= innerWidth + 1, textInside: text.left >= r.left - 1 && text.right <= r.right + 1 && text.top >= r.top - 1 && text.bottom <= r.bottom + 1, cut: el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1 };
            }), "first lines fully visible").toEqual({ inside: true, textInside: true, cut: false });
            const part = page.locator("[data-buy-now-docs]");
            for (const text of [...CARD_LINES, TITLE, "1. Business Use", "A) Business registration", "B) Photo ID matching the business registration", "2. Personal Use", "A) Utility bill (proof of address)", "B) Photo ID matching the utility bill", EMPTY, "Choose another", "Remove", MESSAGE]) {
                expect(await part.filter({ hasText: text }).count(), text).toBeGreaterThan(0);
            }
            const blocks = await textBlocks(page, "h4[data-buy-now-docs], p[data-buy-now-docs], ul[data-buy-now-docs], ol[data-buy-now-docs], li[data-buy-now-docs]", null);
            expect(blocks.found).toBeGreaterThanOrEqual(15);
            expect({ inside: blocks.inside, cut: blocks.cut }).toEqual({ inside: true, cut: false });
            // Sibling blocks of the part must not overlap each other (list items contain their own nested lists, so only top-level blocks are compared).
            expect((await textBlocks(page, ":is(h4, p, ul, ol)[data-buy-now-docs]:not(li *)", null)).overlaps, "overlap").toBe(false);
            expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), "horizontal scroll").toBe(false);
        });
    }
}
