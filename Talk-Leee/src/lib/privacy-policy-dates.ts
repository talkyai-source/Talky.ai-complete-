/**
 * The Privacy Policy's dates, in one place. The privacy page prints them and
 * the dashboard notice (components/legal/privacy-policy-notice.tsx) shows from
 * the notice start until the effective date, so the two cannot disagree.
 *
 * There is ONE date to set: PRIVACY_POLICY_START_DATE, the day the updated
 * policy is published. Everything else is calculated from it:
 *   - the notice starts that day;
 *   - "Last Updated" is that day;
 *   - the Effective Date is PRIVACY_NOTICE_DAYS (30) days later, and the notice
 *     stops showing then.
 * Changing that one line moves all three dates and the date in the notice's
 * text together. A new effective date is a new policy version, so the notice
 * shows again for people who dismissed the previous one.
 */
export const PRIVACY_POLICY_START_DATE = "2026-11-23";

/** How long the notice runs before the updated policy takes effect. */
export const PRIVACY_NOTICE_DAYS = 30;

const DAY_MS = 86_400_000;

const startOfUtcDay = (isoDate: string): number => Date.parse(`${isoDate}T00:00:00Z`);

/** "2026-11-20" + 30 -> "2026-12-20" (calendar days, counted in UTC). */
export function addDaysToIsoDate(isoDate: string, days: number): string {
    return new Date(startOfUtcDay(isoDate) + days * DAY_MS).toISOString().slice(0, 10);
}

export const PRIVACY_POLICY_DATES = {
    /** First day the dashboard notice shows (ISO date, UTC): the start date. */
    noticeStart: PRIVACY_POLICY_START_DATE,
    /** The day the updated policy takes effect; the notice stops showing then (ISO date, UTC): the start date + 30 days. */
    effectiveDate: addDaysToIsoDate(PRIVACY_POLICY_START_DATE, PRIVACY_NOTICE_DAYS),
    /** The day the policy text was last changed (ISO date): the start date. */
    lastUpdated: PRIVACY_POLICY_START_DATE,
} as const;

/** One policy version per effective date: what a dismissal is remembered against. */
export const PRIVACY_POLICY_VERSION: string = PRIVACY_POLICY_DATES.effectiveDate;

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** "2026-12-20" -> "20 December 2026", the format the legal pages print. */
export function formatPolicyDate(isoDate: string): string {
    const [year, month, day] = isoDate.split("-").map(Number);
    return `${day} ${MONTHS[month - 1]} ${year}`;
}

/** True from the start of the notice start day until the start of the effective day (UTC). */
export function privacyNoticeInWindow(nowMs: number): boolean {
    return nowMs >= startOfUtcDay(PRIVACY_POLICY_DATES.noticeStart) && nowMs < startOfUtcDay(PRIVACY_POLICY_DATES.effectiveDate);
}
