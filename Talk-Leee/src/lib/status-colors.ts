// Shared call/lead status → colour, so GREEN/RED mean the same thing across
// the whole app (call history, call detail, contacts, live panel).
//   GREEN  = success / lead / goal achieved / answered / qualified
//   RED    = failure / no-answer / rejected / disqualified
//   BLUE   = in progress
//   AMBER  = pending / ringing / needs follow-up
//   MUTED  = neutral / unknown

export type StatusTone = "green" | "red" | "blue" | "amber" | "muted";

const TONE_PILL: Record<StatusTone, string> = {
    green: "bg-background text-emerald-800 border border-emerald-700/50 dark:text-emerald-300 dark:border-emerald-400/50",
    red: "bg-background text-red-800 border border-red-700/50 dark:text-red-300 dark:border-red-400/50",
    blue: "bg-background text-blue-800 border border-blue-700/50 dark:text-blue-300 dark:border-blue-400/50",
    amber: "bg-background text-amber-800 border border-amber-700/50 dark:text-amber-300 dark:border-amber-400/50",
    muted: "bg-background text-muted-foreground border border-border",
};

const GREEN = new Set([
    "answered", "completed", "goal_achieved", "qualified",
    "customer_hung_up", "agent_hung_up", "in_call",
]);
const RED = new Set([
    "failed", "no_answer", "busy", "rejected", "unreachable",
    "network_failure", "goal_not_achieved", "disqualified", "no_interest",
]);
const BLUE = new Set(["in_progress", "dialing", "initiated"]);
const AMBER = new Set(["ringing", "queued", "pending", "voicemail", "callback"]);

/** Coarse tone for a call status OR a call outcome OR a lead result string. */
export function toneFor(value?: string | null): StatusTone {
    const v = (value || "").trim().toLowerCase();
    if (!v) return "muted";
    if (GREEN.has(v)) return "green";
    if (RED.has(v)) return "red";
    if (BLUE.has(v)) return "blue";
    if (AMBER.has(v)) return "amber";
    return "muted";
}

/** Tailwind classes for a status/outcome pill — drop-in for the old getStatusStyle. */
export function statusPillClass(value?: string | null): string {
    return TONE_PILL[toneFor(value)];
}
