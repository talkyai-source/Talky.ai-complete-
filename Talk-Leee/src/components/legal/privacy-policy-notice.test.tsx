/**
 * The Privacy Policy change notice: who sees it, when, its exact words, and
 * that Dismiss is remembered per person and per policy version.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { notificationSeenKey, notificationsStore } from "@/lib/notifications";
import { PRIVACY_NOTICE_DAYS, PRIVACY_POLICY_DATES, PRIVACY_POLICY_START_DATE, PRIVACY_POLICY_VERSION, addDaysToIsoDate, formatPolicyDate, privacyNoticeInWindow } from "@/lib/privacy-policy-dates";

type Person = { id: string; email: string; role: string; tenant_id?: string | null };
let signedIn: Person | null = null;

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.endsWith("lib/auth-context")) return { useAuth: () => ({ user: signedIn, notificationSyncAvailable: true, status: signedIn ? "authenticated" : "unauthenticated", refreshUser: async () => {} }), useSignedInUser: () => signedIn };
    return originalLoad.call(this, id, ...args);
};
let PrivacyPolicyNotice: React.ComponentType<{ now?: number; scopeWaitMs?: number }>;
let SOURCE: string;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stub above
    ({ PrivacyPolicyNotice, PRIVACY_NOTICE_SEEN_SOURCE: SOURCE } = require("./privacy-policy-notice"));
} finally {
    loader._load = originalLoad;
}

afterEach(() => {
    cleanup();
    notificationsStore.setIdentity(null);
    window.localStorage.clear();
    signedIn = null;
});

const DAY = 24 * 60 * 60 * 1000;
const START = Date.parse(`${PRIVACY_POLICY_DATES.noticeStart}T00:00:00Z`);
const EFFECTIVE = Date.parse(`${PRIVACY_POLICY_DATES.effectiveDate}T00:00:00Z`);
const INSIDE = START + 5 * DAY;
/** "2026-12-20" -> "20 December 2026", worked out here without the code under test. */
const printed = (isoDate: string) => new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(`${isoDate}T00:00:00Z`));
// The notice's exact words, with the Effective Date read from the shared dates (never typed in here).
const TEXT = `We're updating our Privacy Policy on ${printed(PRIVACY_POLICY_DATES.effectiveDate)}.`;

let people = 0;
/** A new person each time: dismissals kept in memory are per person. */
function signIn(role = "user", tenant: string | null = "tenant-1"): Person {
    people += 1;
    signedIn = { id: `person-${people}`, email: `person-${people}@example.test`, role, tenant_id: tenant };
    notificationsStore.setIdentity(tenant ? { tenantId: tenant, userId: signedIn.id } : null);
    return signedIn;
}
const notice = () => document.querySelector<HTMLElement>("[data-privacy-policy-notice]");
const shown = () => notice() !== null;
const mount = (now = INSIDE) => render(<PrivacyPolicyNotice now={now} />);

test("the notice start and the last update are the one start date", () => {
    assert.match(PRIVACY_POLICY_START_DATE, /^\d{4}-\d{2}-\d{2}$/);
    assert.equal(PRIVACY_POLICY_DATES.noticeStart, PRIVACY_POLICY_START_DATE);
    assert.equal(PRIVACY_POLICY_DATES.lastUpdated, PRIVACY_POLICY_START_DATE);
});

test("the effective date is exactly 30 days after the start date", () => {
    assert.equal(PRIVACY_NOTICE_DAYS, 30);
    assert.equal(EFFECTIVE - Date.parse(`${PRIVACY_POLICY_START_DATE}T00:00:00Z`), 30 * DAY);
    assert.equal(PRIVACY_POLICY_VERSION, PRIVACY_POLICY_DATES.effectiveDate);
});

test("adding days to a date crosses months, years and leap days, and dates print as on the legal pages", () => {
    assert.equal(addDaysToIsoDate("2026-11-20", 30), "2026-12-20");
    assert.equal(addDaysToIsoDate("2026-12-15", 30), "2027-01-14");
    assert.equal(addDaysToIsoDate("2028-02-01", 30), "2028-03-02");
    assert.equal(addDaysToIsoDate("2027-03-10", 0), "2027-03-10");
    assert.equal(formatPolicyDate("2027-01-05"), "5 January 2027");
    assert.equal(formatPolicyDate("2026-12-20"), "20 December 2026");
    for (const date of Object.values(PRIVACY_POLICY_DATES)) assert.equal(formatPolicyDate(date), printed(date));
});

test("the notice shows its words exactly, with the date from the shared dates, a link to /privacy and a Dismiss button", () => {
    signIn();
    mount();
    const root = notice() as HTMLElement;
    assert.equal(root !== null, true);
    assert.equal(root.querySelector("p")?.textContent, TEXT);
    assert.equal(root.querySelector("p")?.textContent, `We're updating our Privacy Policy on ${formatPolicyDate(PRIVACY_POLICY_DATES.effectiveDate)}.`);
    const link = screen.getByRole("link", { name: "Read the Privacy Policy" });
    assert.equal(link.getAttribute("href"), "/privacy");
    assert.equal(screen.getAllByRole("button", { name: "Dismiss" }).length, 1);
    // Nothing else: these words, in this order, no heading and no icon.
    assert.equal(root.textContent, `${TEXT}Read the Privacy PolicyDismiss`);
    assert.equal(root.querySelectorAll("svg,img,h1,h2,h3,h4,h5,h6,[role=heading]").length, 0);
});

test("the notice shows from the start date until the effective date, and not before or after", () => {
    assert.equal(privacyNoticeInWindow(START - 1), false);
    assert.equal(privacyNoticeInWindow(START), true);
    assert.equal(privacyNoticeInWindow(EFFECTIVE - 1), true);
    assert.equal(privacyNoticeInWindow(EFFECTIVE), false);
    for (const [now, expected] of [[START - 1, false], [START, true], [INSIDE, true], [EFFECTIVE - 1, true], [EFFECTIVE, false], [EFFECTIVE + 40 * DAY, false]] as const) {
        signIn();
        mount(now);
        assert.equal(shown(), expected, new Date(now).toISOString());
        cleanup();
    }
});

test("Dismiss hides the notice and it stays hidden for that person", () => {
    const person = signIn();
    mount();
    assert.equal(shown(), true);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    assert.equal(shown(), false);
    // Remembered with the existing "seen" markers, under this person's account and this policy version.
    const stored = window.localStorage.getItem(notificationSeenKey(notificationsStore.getSnapshot().scopeKey as string, SOURCE));
    assert.deepEqual(JSON.parse(stored as string), [PRIVACY_POLICY_VERSION]);
    // A later page, and a later day inside the 30 days.
    cleanup();
    mount(INSIDE + 10 * DAY);
    assert.equal(shown(), false);
    // The same person signing in again.
    cleanup();
    notificationsStore.setIdentity(null);
    notificationsStore.setIdentity({ tenantId: person.tenant_id as string, userId: person.id });
    mount();
    assert.equal(shown(), false);
});

test("one person's Dismiss does not hide the notice for another person", () => {
    signIn();
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    assert.equal(shown(), false);
    cleanup();
    signIn();
    mount();
    assert.equal(shown(), true);
});

test("the notice shows again for a new policy version", () => {
    signIn();
    // This person dismissed an earlier version's notice.
    notificationsStore.markSeen(SOURCE, ["2026-01-01"]);
    mount();
    assert.equal(shown(), true, "a dismissal of another version does not hide this one");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    assert.equal(shown(), false);
    assert.deepEqual([...(notificationsStore.readSeen(SOURCE) ?? [])].sort(), ["2026-01-01", PRIVACY_POLICY_VERSION].sort());
});

for (const role of ["user", "readonly", "agent", "billing_user", "campaign_manager", "tenant_admin", "admin", "owner", "partner_admin", "platform_admin", "super_admin"]) {
    test(`the notice shows for the role ${role}`, () => {
        signIn(role);
        mount();
        assert.equal(shown(), true);
        assert.equal(notice()?.querySelector("p")?.textContent, TEXT);
    });
}

test("a person with no account scope (no tenant) still sees it, and Dismiss hides it", () => {
    signIn("platform_admin", null);
    mount();
    assert.equal(shown(), true);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    assert.equal(shown(), false);
    cleanup();
    mount();
    assert.equal(shown(), false, "still hidden on the next page");
});

test("nothing shows to someone who is not signed in", () => {
    signedIn = null;
    mount();
    assert.equal(shown(), false);
});

test("the notice waits for the person's account scope instead of flashing at someone who dismissed it", () => {
    people += 1;
    signedIn = { id: `person-${people}`, email: "late@example.test", role: "user", tenant_id: "tenant-1" };
    // Signed in, but the notification identity has not arrived yet.
    notificationsStore.setIdentity(null);
    mount();
    assert.equal(shown(), false);
});

test("if the account scope never arrives, the notice shows after the wait and Dismiss still hides it", async () => {
    people += 1;
    signedIn = { id: `person-${people}`, email: "closed-storage@example.test", role: "user", tenant_id: "tenant-1" };
    notificationsStore.setIdentity(null);
    render(<PrivacyPolicyNotice now={INSIDE} scopeWaitMs={20} />);
    assert.equal(shown(), false);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 80)); });
    assert.equal(shown(), true);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    assert.equal(shown(), false);
});
