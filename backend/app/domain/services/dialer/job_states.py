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
)


def is_active(status: str | None) -> bool:
    """True if the status keeps the job in the pipeline (occupies the lead)."""
    return (status or "") in ACTIVE_STATUSES


def is_terminal(status: str | None) -> bool:
    return (status or "") in TERMINAL_STATUSES
