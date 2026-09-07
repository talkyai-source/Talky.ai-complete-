/**
 * Frontend-owned view models for the Inbound section.
 *
 * NOTHING in this file is a backend contract. Every name here is chosen by
 * the frontend and is listed in docs/inbound/OPEN-QUESTIONS.md against the
 * endpoint that will eventually supply it. When the backend contract lands,
 * the mapping changes inside `inbound-data.ts` only — components keep these
 * names.
 *
 * Spec: docs/inbound/SPEC-inbound-v1.0.0-FROZEN.md
 */

/* ------------------------------------------------------------------ */
/*  Named states (frozen spec §5, §7, §9)                              */
/* ------------------------------------------------------------------ */

/** Inbound list view + inbound detail panel. */
export type InboundSectionState =
    | "loading"
    | "empty"
    | "populated"
    | "error"
    | "no-permission";

/** Call-history view, including its row-detail panel. */
export type CallHistoryState =
    | "loading"
    | "empty"
    | "empty-after-filter"
    | "populated"
    | "partial-load-error"
    | "row-detail-loading"
    | "row-detail-error";

/** One wizard step. Held per step, not per wizard. */
export type WizardStepState =
    | "idle"
    | "validating"
    | "blocked-by-validation"
    | "saving"
    | "save-failed"
    | "complete";

/* ------------------------------------------------------------------ */
/*  Error kinds — removed 2026-09-01                                   */
/*                                                                     */
/*  A four-value failure union, an error subclass carrying it, and the */
/*  reader that pulled it back out lived here. The reader returned a   */
/*  value only for that subclass, which was thrown solely by the       */
/*  fixture data module deleted alongside the /inbound routes, so the  */
/*  three had no consumer left. The reader's name also collided with a */
/*  live export of lib/inbound-api.ts that returned a different union. */
/*                                                                     */
/*  `inboundStateForError` below is the single error mapper now. It    */
/*  reads the real server code and status, so it supersedes both.      */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/*  Operation states — added at 2.0.0                                  */
/*                                                                     */
/*  The four error kinds above were written when no inbound backend    */
/*  existed. The real one produces failure modes none of them can      */
/*  name — above all conflict, which is not an edge case here but the  */
/*  primary interaction model: every mutation carries expected_version */
/*  and 18 distinct 409 codes exist.                                   */
/*                                                                     */
/*  Spec: SPEC-inbound-v2.0.0-FROZEN.md §5.4                           */
/*  Codes: BACKEND-CONTRACT-EXTRACT.md §10                             */
/* ------------------------------------------------------------------ */

export type InboundOperationState =
    /** 409/412. The record changed elsewhere. Never auto-retry. */
    | "conflict"
    /** 422, or a 400 naming a field. The server refused the values. */
    | "rejected-by-server"
    /** readiness.ready === false. Activation is gated, not failed. */
    | "activation-blocked"
    /**
     * 503 authorization_unavailable. The permission LOOKUP failed — the
     * user may well be permitted. Rendering this as `no-permission` tells
     * them something false about their own access.
     */
    | "authorization-unavailable"
    /**
     * The idempotency key is older than the server's 24h claim window.
     * Replaying it risks acceptance as a brand-new create.
     */
    | "retry-window-expired";

export const INBOUND_OPERATION_STATES: readonly InboundOperationState[] = [
    "conflict",
    "rejected-by-server",
    "activation-blocked",
    "authorization-unavailable",
    "retry-window-expired",
];

/* ------------------------------------------------------------------ */
/*  Error code -> state                                                */
/*                                                                     */
/*  Every code the tenant-facing inbound surface can emit maps to      */
/*  EXACTLY ONE state. Sources are cited per group; the full list with */
/*  line numbers is BACKEND-CONTRACT-EXTRACT.md §10.3.                 */
/* ------------------------------------------------------------------ */

/** 409 — the record moved under us. `not_ready` is handled separately. */
const CONFLICT_CODES = [
    "version_conflict",
    "did_assignment_conflict",
    "pause_before_edit",
    "pause_before_archive",
    "campaign_archived",
    "config_already_exists",
    "campaign_direction_conflict",
    "assignment_quarantined",
    "assignment_archived",
    "assignment_state_conflict",
    "assignment_workflow_required",
    "campaign_change_forbidden",
    "campaign_not_inbound",
    "campaign_terminal",
    "idempotency_race",
    "idempotency_mismatch",
    "idempotency_in_progress",
    "inbound_runtime_restart_required",
] as const;

/** 422 and field-naming 400s — the values were refused, not the version. */
const REJECTED_CODES = [
    "invalid_did",
    "invalid_name",
    "incomplete_after_hours",
    "missing_recording_consent",
    "expected_version_required",
    "invalid_identifier",
    "invalid_timezone",
    "invalid_status",
    "invalid_idempotency_key",
    "controls_missing",
    "did_not_verified",
    "trunk_not_ready",
    // Surface the server's own message for these three: the reason a
    // transfer gate is shut is operational, and a generic string hides it.
    "transfer_runtime_unavailable",
    "transfer_platform_disabled",
    "transfer_staging_scope_mismatch",
] as const;

const CONFLICT_SET: ReadonlySet<string> = new Set(CONFLICT_CODES);
const REJECTED_SET: ReadonlySet<string> = new Set(REJECTED_CODES);

/** Every mapped code, for the parity test. No code appears twice. */
export const INBOUND_MAPPED_ERROR_CODES: readonly string[] = [
    "permission_denied",
    "authorization_unavailable",
    "not_found",
    "not_ready",
    ...CONFLICT_CODES,
    ...REJECTED_CODES,
];

/**
 * The single place a backend failure becomes a named state.
 *
 * `status` wins over `code` only where the code is absent: a 403 with no
 * body is still a permission problem. Anything unrecognised falls through
 * to the generic states rather than being guessed at.
 */
export function inboundStateForError(
    status: number | undefined,
    code: string | null | undefined,
): InboundOperationState | "no-permission" | "error" | "not-found" {
    const normalized = typeof code === "string" ? code.trim() : "";

    if (normalized === "authorization_unavailable" || status === 503) {
        return "authorization-unavailable";
    }
    if (normalized === "permission_denied" || status === 403) {
        return "no-permission";
    }
    if (normalized === "not_ready") {
        return "activation-blocked";
    }
    if (normalized === "not_found" || status === 404) {
        return "not-found";
    }
    if (CONFLICT_SET.has(normalized) || status === 409 || status === 412) {
        return "conflict";
    }
    if (REJECTED_SET.has(normalized) || status === 422) {
        return "rejected-by-server";
    }
    return "error";
}

/* ------------------------------------------------------------------ */
/*  View models                                                        */
/*                                                                     */
/*  Six fixture-era view models were removed on 2026-09-01:            */
/*  InboundConfigSummaryVM, InboundConfigDetailVM, InboundCallRowVM,   */
/*  InboundCallTurnVM, InboundCallDetailVM and InboundPageVM.          */
/*                                                                     */
/*  They were frontend-owned camelCase shapes invented while no        */
/*  inbound contract existed, mapped from placeholder fixtures. The    */
/*  contract now exists, so the wire models in `lib/inbound-api.ts`    */
/*  — InboundCampaign, InboundReadiness, InboundPhoneNumber — are the  */
/*  campaign shapes, and `Call` in `lib/dashboard-api.ts` is the call  */
/*  shape. Keeping a second hand-maintained set alongside them would   */
/*  drift from the contract the moment either changed.                 */
/*                                                                     */
/*  Three more went on 2026-09-02: InboundFilterOptionVM,              */
/*  InboundCallQuery and STATUS_FILTER_ALL. They described the         */
/*  call-history filter and list query, which live on /calls as a      */
/*  direction filter — not on this route — and no module imported      */
/*  any of them. The state unions above are NOT in that category:      */
/*  inbound-state-parity.test.ts asserts all four against the frozen   */
/*  register, so they stay whether or not a component reads them.      */
/*                                                                     */
/*  What this file still owns is the taxonomy above: the named states  */
/*  and the error-code mapping, neither of which the wire models       */
/*  carry. Register: SPEC-inbound-v2.0.0-FROZEN.md §5.5.               */
/* ------------------------------------------------------------------ */
