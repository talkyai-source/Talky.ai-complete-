"""Independent evidence-gate checks. No providers, sockets or DB effects."""

import copy
import json
import socket
import sys
from types import SimpleNamespace

import pytest

from scripts import evaluate_ag04_conversations as gate


def replay_row(*, engine="traditional", semantic="unreviewed"):
    return {
        "scenario_id": "ag04.prior_question_rephrase",
        "engine": engine,
        "profile": {
            "provider": "groq" if engine == "traditional" else "openai",
            "model": "synthetic-profile",
            "voice": "synthetic-no-audio",
            "settings": {"temperature": 0.2},
            "mode": "offline_replay",
        },
        "provenance": {"output_origin": "authored_control"},
        "source_facts": [],
        "requests": [{"messages": [{"role": "user", "content": "Synthetic question."}]}],
        "raw_output": ["Synthetic answer."],
        "submitted_speech": ["Synthetic answer."],
        "history": [{"role": "assistant", "content": "Synthetic answer."}],
        "end": {"requested": False, "shutdown_count": 0, "dnc_flag": False, "dnc_effect_count": 0},
        "effects": {"attempts": [], "accepted": 0},
        "findings": {
            "control": [{"id": "submission_and_effect_contract", "pass": True}],
            "semantic": [{"id": "actual_question", "status": semantic}],
        },
        "scope": "Synthetic runner evidence; no model or audio measurement.",
        "limitations": ["No current human/provider/acoustic qualification."],
    }


def identity(row):
    return {key: copy.deepcopy(row[key]) for key in ("scenario_id", "engine", "profile")}


def provenance():
    return {
        "blocked_network_attempts": [],
        "runner_errors": [],
        "provider_calls": 0,
        "telephone_calls": 0,
    }


def test_green_offline_controls_never_become_profile_approval():
    row = replay_row()
    report = gate.summarize([row], [identity(row)], provenance())
    assert report["runtime_control_status"] == "passed"
    assert report["status"] == "incomplete"
    assert report["production_approved"] is False
    assert report["live_profile_qualification"] == "not_run"
    assert report["human_rubric_approval"] == "not_run"
    assert report["telephone_audio_hearing_and_latency"] == "not_run"
    assert gate.exit_code(report) == 2


def test_captured_semantic_failure_blocks_even_when_every_control_passes():
    row = replay_row(semantic="fail")
    row["provenance"]["output_origin"] = "captured_historical_failure"
    report = gate.summarize([row], [identity(row)], provenance())
    assert report["runtime_control_status"] == "passed"
    assert report["semantic_status"] == "failed"
    assert report["counts"]["captured_or_reviewed_semantic_failures"] == 1
    assert report["failed_semantics"][0]["scenario_id"] == row["scenario_id"]
    assert report["production_approved"] is False and gate.exit_code(report) == 1


def test_authored_semantic_pass_still_has_no_live_or_human_approval():
    row = replay_row(semantic="pass")
    report = gate.summarize([row], [identity(row)], provenance())
    assert report["semantic_status"] == "not_run"
    assert not report["production_approved"] and gate.exit_code(report) == 2
    report.update(status="approved", production_approved=True)
    assert gate.exit_code(report) != 0


@pytest.mark.parametrize(
    "defect", ["empty", "missing", "duplicate_row", "duplicate_inventory", "unexpected"]
)
def test_invalid_inventory_is_failed_evidence_not_a_smaller_successful_sample(defect):
    row = replay_row()
    rows, inventory = [row], [identity(row)]
    if defect == "empty":
        rows, inventory = [], []
    elif defect == "missing":
        rows = []
    elif defect == "duplicate_row":
        rows.append(copy.deepcopy(row))
    elif defect == "duplicate_inventory":
        inventory.append(copy.deepcopy(inventory[0]))
    else:
        rows[0]["scenario_id"] = "ag04.unexpected"
    report = gate.summarize(rows, inventory, provenance())
    assert report["evidence_errors"]
    assert report["status"] == "failed" and gate.exit_code(report) == 1


def test_engine_profile_copies_do_not_inflate_distinct_scenario_count():
    rows = [replay_row(), replay_row(engine="native")]
    report = gate.summarize(rows, [identity(row) for row in rows], provenance())
    assert report["counts"]["observed_rows"] == 2
    assert report["counts"]["distinct_replayed_scenario_ids"] == 1
    assert report["production_approved"] is False


def proposed_matrix():
    return {
        "status": "proposed_unratified",
        "scenarios": [{"id": "ag04.prior_question_rephrase"}, {"id": "ag04.unclear_name"}],
        "ratification": {"scenario_rubric": "pending"},
        "intended_profiles": [],
    }


def test_native_control_mapping_and_profile_copies_count_one_proposed_condition():
    native = replay_row(engine="native")
    native["scenario_id"] = "native.prior_question_control"
    native["semantic_ids"] = ["ag04.prior_question_rephrase"]
    other_profile = copy.deepcopy(native)
    other_profile["profile"]["provider"] = "xai"
    report = gate.matrix_coverage(proposed_matrix(), [native, other_profile, replay_row()])
    assert report["catalog_scenarios"] == 2
    assert report["distinct_catalog_ids_replayed_anywhere"] == 1
    assert report["missing_catalog_ids_everywhere"] == ["ag04.unclear_name"]
    assert len(report["observed_offline_configurations"]) == 3
    assert report["catalog_status"] == "proposed_unratified"
    assert report["intended_sale_profiles"] == []


def test_unmapped_control_is_not_fabricated_semantic_coverage():
    native = replay_row(engine="native")
    native["scenario_id"] = "native.unmapped_control"
    report = gate.matrix_coverage(proposed_matrix(), [native])
    assert report["distinct_catalog_ids_replayed_anywhere"] == 0
    assert len(report["missing_catalog_ids_everywhere"]) == 2
    assert report["observed_offline_configurations"][0]["additional_control_ids"] == [
        "native.unmapped_control"
    ]


@pytest.mark.parametrize("value", ["true", 1, None])
def test_control_truth_requires_a_boolean(value):
    row = replay_row()
    row["findings"]["control"][0]["pass"] = value
    report = gate.summarize([row], [identity(row)], provenance())
    assert report["evidence_errors"] and report["status"] == "failed"


@pytest.mark.parametrize(
    "field",
    ["end", "effects", "provenance", "history", "requests", "raw_output", "submitted_speech"],
)
def test_null_observations_cannot_claim_passed_runtime_evidence(field):
    row = replay_row()
    row[field] = None
    report = gate.summarize([row], [identity(row)], provenance())
    assert report["runtime_control_status"] == "failed"
    assert report["evidence_errors"] and gate.exit_code(report) == 1


@pytest.mark.parametrize("field,value", [("voice", []), ("settings", {"bad": object()})])
def test_malformed_profile_is_reported_without_losing_other_evidence(field, value):
    good = replay_row()
    bad = copy.deepcopy(good)
    bad["profile"][field] = value
    report = gate.summarize([good, bad], [identity(good)], provenance())
    assert report["status"] == "failed" and report["evidence_errors"]
    assert good in report["rows"]


def test_swallowed_connection_error_is_still_a_gate_failure():
    observed = provenance()
    with gate.offline_network_guard(observed["blocked_network_attempts"]):
        try:
            socket.create_connection(("synthetic.invalid", 443))
        except RuntimeError:
            pass
    row = replay_row()
    report = gate.summarize([row], [identity(row)], observed)
    assert len(observed["blocked_network_attempts"]) == 1
    assert report["status"] == "failed" and gate.exit_code(report) == 1


def test_udp_send_is_denied_before_even_an_intercepted_transport(monkeypatch):
    reached_transport = []

    def intercepted_sendto(_socket, *args):
        reached_transport.append(args)
        return 0

    # Replacing the OS method first guarantees the regression cannot send a
    # real datagram even if the offline guard is incomplete.
    monkeypatch.setattr(socket.socket, "sendto", intercepted_sendto)
    attempts = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        with gate.offline_network_guard(attempts):
            with pytest.raises(RuntimeError, match="offline"):
                sock.sendto(b"synthetic", ("127.0.0.1", 9))
    assert reached_transport == [] and len(attempts) == 1


def test_runner_failure_preserves_other_engine_and_main_writes_failed_report(monkeypatch, tmp_path):
    row = replay_row()

    async def succeeded(_root):
        return [copy.deepcopy(row)]

    async def failed(_root):
        raise TimeoutError("synthetic failure body must not enter the report")

    modules = {
        "tests.qualification.ag04_traditional": SimpleNamespace(
            case_inventory=lambda _root: [identity(row)], run_cases=succeeded
        ),
        "tests.qualification.ag04_native": SimpleNamespace(
            case_inventory=lambda _root: [identity(replay_row(engine="native"))], run_cases=failed
        ),
    }
    original_import = gate.importlib.import_module
    monkeypatch.setattr(
        gate.importlib, "import_module", lambda name: modules.get(name) or original_import(name)
    )
    monkeypatch.setattr(gate, "source_provenance", lambda _root: provenance())
    output = tmp_path / "result.json"
    monkeypatch.setattr(sys, "argv", ["evaluate_ag04_conversations", "--output", str(output)])
    assert gate.main() == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["rows"] == [row]
    assert report["provenance"]["runner_errors"] == ["native: TimeoutError"]
    assert "synthetic failure body" not in output.read_text(encoding="utf-8")
    assert report["counts"]["observed_rows"] == 1 and not report["production_approved"]
