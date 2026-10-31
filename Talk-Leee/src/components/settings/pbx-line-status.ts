/**
 * Plain-language status of a saved PBX line, for the card's status pill.
 *
 * Vocabulary (owner brief 2026-10-14): Connected, Connecting, Needs attention,
 * Off, Not checked.
 *
 * Safety rule carried over from the old table: only CURRENT runtime proof earns
 * "Connected". The server computes `runtime_ready` / `inbound_runtime_ready`
 * from fresh Asterisk evidence (registered, or an IP-auth contact that answered
 * a qualify) for an active line. A saved test or a registration seen once does
 * not count, so a line that is registered but not ready is never shown as
 * Connected.
 */
import type { SipTrunkRow } from "@/lib/telephony-api";

export type LineStatusKind = "connected" | "connecting" | "attention" | "off" | "unchecked";

export const LINE_STATUS_LABEL: Record<LineStatusKind, string> = {
    connected: "Connected",
    connecting: "Connecting",
    attention: "Needs attention",
    off: "Off",
    unchecked: "Not checked",
};

export interface LineStatus {
    kind: LineStatusKind;
    label: string;
    /** One plain sentence about what is going on; null when there is nothing to add. */
    detail: string | null;
    /** The server's own wording, kept small, only for lines that need attention. */
    technical: string | null;
    /** Raw SIP response code such as "403", when the data carries one. */
    sipCode: string | null;
    /** When this status was last confirmed (ISO); null for lines that are off. */
    checkedAt: string | null;
}

/** States that mean "waiting for proof", not "something is wrong". */
const WAITING = new Map<string, string>([
    ["checking", "Connecting to your provider."],
    ["registering", "Signing in to your provider."],
    ["inactive", "Waiting for the first status check."],
    ["unknown", "Waiting for your provider to answer."],
    ["loaded", "Waiting for your provider to answer."],
    ["status_missing", "Waiting for the first status check."],
    ["status_stale", "The last status check is out of date. Checking again."],
    ["status_clock_skew", "The last status check is out of date. Checking again."],
]);

/** The server could not prove the line works; each says what to look at. */
const PROBLEM = new Map<string, string>([
    ["rejected", "Your provider rejected the username or password."],
    ["failed", "Your provider rejected the sign-in."],
    ["unregistered", "We couldn't sign in to your provider."],
    ["stopped", "Signing in to your provider has stopped."],
    ["unreachable", "Your provider didn't answer."],
    ["missing_config", "This line isn't loaded yet. Try switching it off and on."],
]);

/** Evidence too old to trust; it wins over whatever status it last recorded. */
const STALE = new Set(["status_missing", "status_stale", "status_clock_skew"]);

function normalize(value: string | null | undefined): string {
    return String(value ?? "").trim().toLowerCase().replace(/^runtime_/, "");
}

function clip(text: string, max = 200): string {
    return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function sipCodeOf(trunk: SipTrunkRow): string | null {
    const live = /^\s*(\d{3})\b/.exec(trunk.live_status_detail ?? "");
    if (live) return live[1];
    // A saved FAILED test can explain a problem; a saved success says nothing about now.
    const saved = trunk.last_test_result;
    if (saved && saved.ok === false && saved.sip_code !== undefined && saved.sip_code !== null) {
        const code = String(saved.sip_code).trim();
        if (code) return code;
    }
    return null;
}

export function deriveLineStatus(trunk: SipTrunkRow): LineStatus {
    if (!trunk.is_active) {
        return {
            kind: "off",
            label: LINE_STATUS_LABEL.off,
            detail: "Switched off. Turn it on to use this line for calls.",
            technical: null,
            sipCode: null,
            checkedAt: null,
        };
    }

    const checkedAt = trunk.live_status_checked_at ?? trunk.last_tested_at ?? null;
    const inboundOnly = trunk.direction === "inbound";
    const ready = inboundOnly ? trunk.inbound_runtime_ready === true : trunk.runtime_ready === true;
    if (ready) {
        return { kind: "connected", label: LINE_STATUS_LABEL.connected, detail: null, technical: null, sipCode: null, checkedAt };
    }

    const live = normalize(trunk.live_registration_status);
    const runtime = normalize(trunk.runtime_status_code);
    if (!live) {
        return {
            kind: "unchecked",
            label: LINE_STATUS_LABEL.unchecked,
            detail: "Nothing has been checked yet. Select Test to check this line.",
            technical: null,
            sipCode: null,
            checkedAt,
        };
    }

    const known = [STALE.has(runtime) ? runtime : "", live, runtime].find(
        (state) => state !== "" && (WAITING.has(state) || PROBLEM.has(state)),
    );
    const sipCode = sipCodeOf(trunk);
    if (known !== undefined && WAITING.has(known)) {
        return {
            kind: "connecting",
            label: LINE_STATUS_LABEL.connecting,
            detail: WAITING.get(known) ?? null,
            technical: null,
            sipCode,
            checkedAt,
        };
    }

    const detail = (known !== undefined ? PROBLEM.get(known) : undefined) ?? "This line isn't ready for calls.";
    const raw = (trunk.live_status_detail ?? "").trim() || (trunk.runtime_status_detail ?? "").trim();
    return {
        kind: "attention",
        label: LINE_STATUS_LABEL.attention,
        detail,
        technical: raw && raw.toLowerCase() !== detail.toLowerCase() ? clip(raw) : null,
        sipCode,
        checkedAt,
    };
}

/** "just now", "5 minutes ago"... or null when the time or the clock is unknown. */
export function formatRelativeTime(iso: string | null | undefined, now: number): string | null {
    if (!iso || now <= 0) return null;
    const then = Date.parse(iso);
    if (Number.isNaN(then)) return null;
    const seconds = Math.max(0, Math.round((now - then) / 1000));
    if (seconds < 30) return "just now";
    if (seconds < 60) return `${seconds} seconds ago`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
    const days = Math.floor(hours / 24);
    return `${days} day${days === 1 ? "" : "s"} ago`;
}
