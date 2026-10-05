"""Runner checks never equate canned safety controls with model acceptance."""

import json
import socket
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.qualification.ag04_traditional import _load, _run_case, case_inventory

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
async def offline_only():
    # Start after the event loop's own Windows self-pipe is constructed.
    with (
        patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")),
        patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden")),
    ):
        yield


def test_inventory_is_input_declared_and_not_inferred_from_successful_runs():
    rows = case_inventory(ROOT)
    assert len(rows) == 136
    assert len({(r["scenario_id"], r["profile"]["provider"]) for r in rows}) == 136
    assert len({r["scenario_id"] for r in rows}) == 34  # repetitions are not new human scenarios


@pytest.mark.parametrize("provider", ["groq", "cerebras", "openai", "gemini"])
async def test_accepted_email_is_not_delivered_and_followup_does_not_resend(provider):
    data = _load(ROOT)
    case = next(c for c in data["cases"] if c["id"] == "ag04.accepted_not_delivered")
    spec = next(p for p in data["profiles"] if p["provider"] == provider)
    row = await _run_case(ROOT, data, case, spec)
    assert all(c["pass"] for c in row["findings"]["control"]), row["findings"]["control"]
    assert row["raw_output"][0] == "The email was delivered to your inbox."
    assert row["turns"][0]["submitted_speech"] == [
        "The provider accepted it for sending, but I can't confirm delivery."]
    assert row["turns"][1]["submitted_speech"] == [case["turns"][1]["chunks"][0]]
    assert len(row["effects"]["attempts"]) == row["effects"]["accepted"] == 1
    receipt = row["effects"]["attempts"][0]["receipt"]
    assert receipt["message_id"] == "synthetic-remote-accepted-1"
    assert receipt["provider"] == "synthetic-email" and receipt["status"] == "provider_accepted"
    assert row["effects"]["recorded_results"]["send_email"] == receipt
    assert row["findings"]["semantic"][0]["status"] == "unreviewed"


@pytest.mark.parametrize("provider", ["groq", "cerebras", "openai", "gemini"])
async def test_exact_captured_failure_survives_real_pipeline_and_actual_request_adapter(provider):
    data = _load(ROOT)
    case = next(c for c in data["cases"] if c["id"] == "ag04.prior_question_rephrase")
    spec = next(p for p in data["profiles"] if p["provider"] == provider)
    row = await _run_case(ROOT, data, case, spec)
    archive = json.loads((ROOT / case["captured"]["artifact"]).read_text(encoding="utf-8"))
    captured = archive["followup_phases"]["groq_actual_sdk_request_verification"]["model_results"][
        0
    ]["cases"][0]["turns"][0]
    assert row["raw_output"] == [captured["raw_provider_text"]]
    assert row["submitted_speech"] == captured["completed_submissions"]
    assert all(check["pass"] for check in row["findings"]["control"])
    assert len(row["requests"]) == 1
    assert row["requests"][0]["model"] == spec["model"]
    assert row["requests"][0]["instructions_sha256"]
    assert case["history"][-1]["content"] in json.dumps(row["requests"][0]["messages"])
    # Other adapters receive the captured output as a CONTROL, never as a
    # fabricated observation of their real model quality.
    assert row["findings"]["semantic"][0]["status"] == (
        "fail" if provider == "groq" else "unreviewed"
    )


@pytest.mark.parametrize(
    "id",
    [
        "relationship_reversal",
        "long_relationship_memory",
        "quoted_relationship",
        "decline_email_topic",
        "thanks_not_goodbye",
        "quoted_dnc",
        "negated_dnc",
        "failed_action",
        "provider_zero_output",
        "provider_partial_eof",
        "provider_partial_length",
        "transport_disconnect",
        "continuation_during_goodbye",
    ],
)
async def test_runtime_control_results_include_effects_history_and_unreviewed_semantics(id):
    data = _load(ROOT)
    case = next(c for c in data["cases"] if c["id"] == "ag04." + id)
    row = await _run_case(ROOT, data, case, data["profiles"][0])
    assert all(check["pass"] for check in row["findings"]["control"]), row["findings"]["control"]
    assert row["findings"]["semantic"][0]["status"] == "unreviewed"
    assert row["effects"]["accepted"] == 0
    assert row["end"]["shutdown_count"] == 0


@pytest.mark.parametrize("id", ["dnc_with_question", "unsupported_price"])
async def test_discovered_runtime_boundaries(id):
    data = _load(ROOT)
    case = next(c for c in data["cases"] if c["id"] == "ag04." + id)
    row = await _run_case(ROOT, data, case, data["profiles"][0])
    assert all(check["pass"] for check in row["findings"]["control"]), row["findings"]["control"]


@pytest.mark.parametrize(
    "caller,closes,dnc",
    [
        ("Do not call me again, but what are your opening hours?", False, True),
        ("Do not call me again, but first I need help with my account.", False, True),
        ("Do not call me again, what are your hours? Actually, goodbye.", True, True),
        ('The script says "do not call me again", but what are your hours?', False, False),
        ("I am not saying do not call me again. What are your hours?", False, False),
        ("Yesterday he said do not call me again. What are your hours?", False, False),
    ],
)
async def test_optout_is_separate_from_current_caller_close_authority(caller, closes, dnc):
    data = _load(ROOT)
    case = deepcopy(next(c for c in data["cases"] if c["id"] == "ag04.dnc_with_question"))
    case["turns"][0]["caller"] = caller
    # A generated sentinel cannot overrule the caller's continued request.
    case["turns"][0]["chunks"] = ["Our office opens at nine. [END_CALL]"]
    case["expected"].update(end=closes, dnc=dnc)
    row = await _run_case(ROOT, data, case, data["profiles"][0])
    assert all(c["pass"] for c in row["findings"]["control"]), row["findings"]["control"]
    assert bool(row["requests"]) is not closes


async def test_failed_optout_keeps_flag_and_retries_on_later_authorized_close():
    data = _load(ROOT)
    case = deepcopy(next(c for c in data["cases"] if c["id"] == "ag04.dnc_with_question"))
    case["turns"][0]["dnc_result"] = False
    first = await _run_case(ROOT, data, case, data["profiles"][0])
    assert first["end"]["dnc_flag"] is True
    assert first["end"]["shutdown_count"] == 0
    assert first["effects"]["attempts"] == [
        {"kind": "dnc", "status": "failed", "origin": "synthetic_external_port"}
    ]
    case["turns"].append({"caller": "Do not call me again. Goodbye.", "chunks": ["Goodbye."]})
    row = await _run_case(ROOT, data, case, data["profiles"][0])
    assert row["end"]["dnc_flag"] is True
    assert row["end"]["shutdown_count"] == 1
    assert [e["status"] for e in row["effects"]["attempts"]] == ["failed", "accepted"]


async def test_canonical_explicit_email_correction_parity():
    data = _load(ROOT)
    case = next(c for c in data["cases"] if c["id"] == "ag04.email_correction")
    row = await _run_case(ROOT, data, case, data["profiles"][0])
    assert all(c["pass"] for c in row["findings"]["control"]), row["findings"]["control"]


@pytest.mark.parametrize("media", [None, "interrupt", "disconnect", "exception"])
async def test_first_turn_clarification_records_only_completed_submission(media):
    data = _load(ROOT)
    case = deepcopy(next(c for c in data["cases"] if c["id"] == "ag04.unclear_email"))
    if media:
        case["turns"][0]["media"] = media
    row = await _run_case(ROOT, data, case, data["profiles"][0])
    saved = [m["content"] for m in row["turns"][0]["history_added"] if m["role"] == "assistant"]
    assert saved == row["submitted_speech"]
    assert bool(saved) is (media is None)
    # The rejected caller recognition is not promoted into model context.
    assert not any(m["role"] == "user" for m in row["history"])
    assert row["requests"] == []
    assert row["state"]["email_confirmed"] is False
    assert row["end"]["shutdown_count"] == 0


@pytest.mark.parametrize("provider", ["groq", "cerebras", "openai", "gemini"])
async def test_historical_dnc_recollection_does_not_authorize_current_optout_or_close(provider):
    data = _load(ROOT)
    case = next(c for c in data["cases"] if c["id"] == "ag04.historical_dnc_recollection")
    spec = next(p for p in data["profiles"] if p["provider"] == provider)
    row = await _run_case(ROOT, data, case, spec)
    assert all(c["pass"] for c in row["findings"]["control"]), row["findings"]["control"]
    assert row["end"]["dnc_flag"] is False and row["end"]["shutdown_count"] == 0
    assert row["effects"]["attempts"] == []
    assert row["requests"] and row["submitted_speech"]
    assert "[[END_CALL]]" in row["raw_output"][0]
    assert all("END_CALL" not in text for text in row["submitted_speech"])
    assert all(f["status"] == "unreviewed" for f in row["findings"]["semantic"])


@pytest.mark.parametrize("provider", ["groq", "cerebras", "openai", "gemini"])
@pytest.mark.parametrize("status", ["unknown", "in_progress"])
async def test_unresolved_action_preserves_receipt_and_never_speaks_false_failure_or_resend(provider, status):
    data = _load(ROOT)
    case = next(c for c in data["cases"] if c["id"] == "ag04.action_" + status)
    spec = next(p for p in data["profiles"] if p["provider"] == provider)
    row = await _run_case(ROOT, data, case, spec)
    assert all(c["pass"] for c in row["findings"]["control"]), row["findings"]["control"]
    assert [" ".join(turn["submitted_speech"]) for turn in row["turns"]] == case["expected_turn_speech"]
    original = {"version": 1, "action": "send_email", **case["turns"][0]["tool_result"]}
    attempts = row["effects"]["attempts"]
    assert len(attempts) == 1 and attempts[0]["receipt"] == original
    assert row["effects"]["recorded_results"]["send_email"] == original
    assert original["request_id"] == "synthetic-" + status + "-request-1"
    assert original["status"] == status and original["message_id"] is None
    assert row["effects"]["accepted"] == 0
    assert row["semantic_ids"] == ["ag04.action_unknown_outcome"]
    assert all(f["status"] == "unreviewed" for f in row["findings"]["semantic"])
