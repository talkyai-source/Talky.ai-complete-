"""Unit tests for the outbound "who you're calling" (CALL-TARGET) feature.

Covers:
  * build_call_target_block — the pure block builder (name present / absent /
    company-only / greet-name choice).
  * build_telephony_session_config — the block is prepended to the composed
    system prompt when a lead name is threaded, and the prompt is BYTE-FOR-BYTE
    today's blind-dial prompt when no name is threaded.
  * MakeCallRequest + DialerJob — the new lead-identity fields are accepted and
    survive a Redis round-trip (the transport that carries them to the worker).
"""
from app.domain.services.voice_orchestrator import Direction


# ── build_call_target_block ───────────────────────────────────────
def _block(**kw):
    from app.domain.services.telephony_session_config import build_call_target_block
    return build_call_target_block(**kw)


def test_block_present_with_full_name_and_company():
    b = _block(first_name="Jane", last_name="Doe", company="Acme Roofing")
    assert "PERSON YOU'RE CALLING: Jane Doe, from Acme Roofing." in b
    assert 'is this Jane?' in b            # greets by FIRST name
    assert "not a confirmed fact" in b     # framed as expectation, not CAPTURED


def test_block_first_name_only():
    b = _block(first_name="Jane")
    assert "PERSON YOU'RE CALLING: Jane." in b
    assert "from" not in b.split("\n")[0]  # no dangling company clause


def test_block_absent_without_name():
    # No name at all → empty block (blind dial degrade).
    assert _block() == ""
    assert _block(first_name="", last_name="  ") == ""


def test_block_company_only_is_empty():
    # A company with no name is not enough to greet by name → empty.
    assert _block(company="Acme Roofing") == ""


def test_block_greet_name_prefers_first_then_full():
    # Only a last name given → greet with the full (last) name, not blank.
    b = _block(last_name="Doe")
    assert "PERSON YOU'RE CALLING: Doe." in b
    assert "is this Doe?" in b


# ── build_telephony_session_config integration ────────────────────
def _cfg(**kw):
    from app.domain.services.telephony_session_config import (
        build_telephony_session_config,
    )
    return build_telephony_session_config(
        gateway_type="telephony",
        campaign=None,
        agent_name_override="John",   # deterministic → stable system_prompt
        direction=Direction.OUTBOUND,
        **kw,
    )


def test_prompt_gets_call_target_block_when_named():
    cfg = _cfg(lead_first_name="Jane", lead_last_name="Doe", lead_company="Acme Roofing")
    assert cfg.system_prompt.startswith("PERSON YOU'RE CALLING: Jane Doe, from Acme Roofing.")
    # The persona/guardrails still follow the block.
    assert "HARD RULES" in cfg.system_prompt
    # Callee identity is also carried on the config for the realtime path.
    assert cfg.callee_first_name == "Jane"
    assert cfg.callee_company == "Acme Roofing"


def test_prompt_is_byte_for_byte_identical_when_blind():
    """No lead threaded → the composed prompt must equal today's blind-dial
    prompt exactly, and the named prompt must be the block + that same tail."""
    blind = _cfg()
    named = _cfg(lead_first_name="Jane", lead_last_name="Doe")

    assert "PERSON YOU'RE CALLING" not in blind.system_prompt
    assert blind.callee_first_name is None
    # The named prompt is exactly the block, a newline, then the blind prompt.
    from app.domain.services.telephony_session_config import build_call_target_block
    block = build_call_target_block("Jane", "Doe", None)
    assert named.system_prompt == block + "\n" + blind.system_prompt


def test_company_stored_none_when_blank_threaded():
    cfg = _cfg(lead_first_name="Jane", lead_company="   ")
    assert cfg.callee_company is None
    assert "from" not in cfg.system_prompt.split("\n")[0]


# ── transport: MakeCallRequest + DialerJob ────────────────────────
def test_make_call_request_accepts_lead_fields():
    from app.api.v1.endpoints.telephony_bridge import MakeCallRequest
    req = MakeCallRequest(
        destination="+14155550000",
        lead_first_name="Jane",
        lead_last_name="Doe",
        lead_company="Acme Roofing",
    )
    assert req.lead_first_name == "Jane"
    assert req.lead_last_name == "Doe"
    assert req.lead_company == "Acme Roofing"


def test_make_call_request_lead_fields_optional():
    from app.api.v1.endpoints.telephony_bridge import MakeCallRequest
    req = MakeCallRequest(destination="+14155550000")
    assert req.lead_first_name is None
    assert req.lead_last_name is None
    assert req.lead_company is None


def test_dialer_job_round_trips_lead_identity():
    from app.domain.models.dialer_job import DialerJob
    job = DialerJob(
        job_id="j1", campaign_id="c1", lead_id="l1", tenant_id="t1",
        phone_number="+14155550000",
        lead_first_name="Jane", lead_last_name="Doe", lead_company="Acme Roofing",
    )
    restored = DialerJob.from_redis_dict(job.to_redis_dict())
    assert restored.lead_first_name == "Jane"
    assert restored.lead_last_name == "Doe"
    assert restored.lead_company == "Acme Roofing"
