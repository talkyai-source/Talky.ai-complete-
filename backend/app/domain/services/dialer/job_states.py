"""Canonical ``dialer_jobs.status`` vocabulary.

Single source of truth for which statuses mean a job is still "in the pipeline"
versus finished. The per-lead dedup index, the stuck-job reaper, and the
campaign/lead job-lifecycle all import from here so their notions of
"active" can never drift apart — which is exactly how the double-dial /
zombie-"dialing" bugs crept in.

Keep this list in lockstep with the partial unique index predicate in the
migration ``20260612_dialer_job_dedup.sql``.
"""
from __future__ import annotations

# A job is ACTIVE while it still holds a place in the pipeline for its lead.
# Invariant: at most ONE active job may exist per lead (enforced by a partial
# unique index on dialer_jobs(lead_id) WHERE status IN (ACTIVE_STATUSES)).
ACTIVE_STATUSES: tuple[str, ...] = (
    "pending",
    "queued",
    "retry_scheduled",
    "processing",
    "calling",
)

# Of the active ones, these mean "a call is supposedly happening right now".
# The reaper times these out — if one sits here too long the originate hung
# and nothing finalized it (a zombie).
IN_FLIGHT_STATUSES: tuple[str, ...] = ("processing", "calling")

# Terminal — the job is done and will not dial again on its own.
#   completed / goal_achieved : success outcomes
#   failed                    : an attempt failed (may be retried via a NEW job)
#   skipped                   : a pre-dial gate skipped it (window, minutes, …)
#   cancelled                 : removed from the pipeline on purpose
#                               (campaign stopped, lead removed, dedup)
TERMINAL_STATUSES: tuple[str, ...] = (
    "completed",
    "goal_achieved",
    "failed",
    "skipped",
    "cancelled",
    # Terminal too, and previously missing from this "single source of truth":
    # blocked      : a pre-dial guard refused the call outright (call guard)
    # non_retryable: the disposition policy decided never to dial again
    # Both are written in production (dialer_worker / call_service) and neither
    # is active, so is_terminal() returned False for two genuinely terminal
    # states. Harmless today only because nothing calls is_terminal() on them.
    "blocked",
    "non_retryable",
)

# ---------------------------------------------------------------------------
# calls.status — a DIFFERENT vocabulary from dialer_jobs.status
# ---------------------------------------------------------------------------
# A call is LIVE while it occupies a line. Used for "active calls" counting and
# live-call views.
#
# WHY THIS LIVES HERE (2026-07-31): three copies of this list had drifted, and
# the admin copy was simply wrong:
#
#   tenant  app/api/v1/endpoints/calls.py      queued dialing ringing answered
#                                              in_call initiated          (correct)
#   admin   admin/calls.py, admin/base.py,     in_progress ringing queued
#           admin/health/queues.py             initiated                  (WRONG)
#
# `in_progress` is written by NOTHING in this codebase, and the admin list omits
# `dialing`, `answered` and `in_call` — the three statuses a call spends nearly
# all of its live time in. So every admin "active calls" figure counted only
# calls that were ringing or queued, and read near-zero while real conversations
# were in progress.
LIVE_CALL_STATUSES: tuple[str, ...] = (
    "queued",
    "dialing",
    "ringing",
    "answered",
    "in_call",
    "initiated",
    # A stale DB row has been removed from the dialer's batch count, but the
    # PBX owner has not yet proved every leg absent. Keep it operator-visible
    # and eligible for an explicit confirmation-aware termination retry.
    "termination_pending",
)

# The subset that proves a conversation is genuinely under way. Deliberately
# EXCLUDES "initiated": that means a call row exists but the provider never
# confirmed a channel, which is precisely the hung-origination case the stuck
# reaper must still be allowed to reap. Do not merge this with the list above.
CONVERSATION_LIVE_CALL_STATUSES: tuple[str, ...] = (
    "dialing",
    "ringing",
    "answered",
    "in_call",
)


def is_active(status: str | None) -> bool:
    """True if the status keeps the job in the pipeline (occupies the lead)."""
    return (status or "") in ACTIVE_STATUSES


def is_terminal(status: str | None) -> bool:
    return (status or "") in TERMINAL_STATUSES
