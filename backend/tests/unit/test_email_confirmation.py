"""Historical email parser compatibility and current neutral prompt state.

The parser is no longer the live contact workflow. Model-tool confirmation and
persistence are qualified in test_model_contact_recording; exact scripted
readback gates were intentionally retired."""
from __future__ import annotations

from app.services.scripts.call_state_tracker import CallState
from app.services.scripts.prompt_builder import compose_system_prompt

BASE = "You are Alex. Be brief."


# ── tracker: capture → pending → confirmed/rejected ──────────────────────────


# ── regressions caught by the re-audit: incidental words must NOT flip a core value


# ── re-audit round 2: the read-back GATE must be robust ──────────────────────


# ── issue #4: a bare DOMAIN mention is NOT a read-back of the LOCAL part ──────


# ── issue #2: an assembled multi-word email enters the gate via the agent read-back


# ── bounded-attempts safety net (re-audit cf #6): the read-back can't loop forever


# ── reliability re-audit: affirm/reject classifier edge cases ────────────────


# ── hybrid: a pre-computed verdict (from the LLM fallback) overrides the regex ─


# ── prompt: pending email demands a read-back, not a "confirmed" fact ─────────

def test_prompt_unconfirmed_email_reports_pending_not_confirmed():
    out = compose_system_prompt(BASE, CallState(email="bob@acme.com", email_confirmed=False))
    assert "bob@acme.com" in out and '"status": "awaiting_confirmation"' in out
    assert "say exactly" not in out.lower() and '"status": "confirmed"' not in out


def test_prompt_confirmed_email_is_a_captured_fact():
    out = compose_system_prompt(BASE, CallState(email="bob@acme.com", email_confirmed=True))
    assert "CONTACT CONTEXT" in out and '"status": "confirmed"' in out
    assert "bob@acme.com" in out and "do not re-ask" not in out.lower()


# ── issue #5: inject the EXACT deterministic spoken read-back ─────────────────
