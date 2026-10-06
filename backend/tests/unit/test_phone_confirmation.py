"""Historical phone parser compatibility and current neutral prompt state.

These parser utilities do not establish live model interpretation. Current
model-tool confirmation/persistence is covered in test_model_contact_recording."""
from __future__ import annotations

from app.services.scripts.call_state_tracker import CallState
from app.services.scripts.prompt_builder import compose_system_prompt

BASE = "You are Alex. Be brief."


# ── tracker: capture → pending → confirmed/rejected ──────────────────────────


# ── read-back gate (turn_runner) ─────────────────────────────────────────────


# ── prompt surfacing ─────────────────────────────────────────────────────────

def test_prompt_unconfirmed_phone_reports_pending():
    out = compose_system_prompt(BASE, CallState(phone="5551234567", phone_confirmed=False))
    assert "5551234567" in out and '"status": "awaiting_confirmation"' in out
    assert "say exactly" not in out.lower() and "did i get that right" not in out.lower()


def test_prompt_confirmed_phone_is_a_captured_fact():
    out = compose_system_prompt(BASE, CallState(phone="5551234567", phone_confirmed=True))
    assert "CONTACT CONTEXT" in out and '"status": "confirmed"' in out
    assert "5551234567" in out
