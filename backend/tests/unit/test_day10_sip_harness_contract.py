"""Fail-closed contracts for the Day 10 SIP-only release harness."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[3]
PROBE = ROOT / "telephony" / "scripts" / "day10_concurrency_soak_probe.py"
VERIFIER = ROOT / "telephony" / "scripts" / "verify_day10_concurrency_soak.sh"
TELEPHONY_SCRIPTS = ROOT / "telephony" / "scripts"


def _load_probe_module():
    spec = importlib.util.spec_from_file_location("day10_sip_harness_probe", PROBE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _required_output_args(tmp_path: Path) -> list[str]:
    names = (
        "harness",
        "ramp",
        "capacity",
        "soak",
        "timeseries",
        "transfer",
        "bargein",
        "fairness",
    )
    flags = (
        "--output-harness-smoke",
        "--output-ramp",
        "--output-capacity",
        "--output-soak-summary",
        "--output-soak-timeseries",
        "--output-transfer",
        "--output-bargein",
        "--output-tenant-fairness",
    )
    args: list[str] = []
    for flag, name in zip(flags, names):
        args.extend((flag, str(tmp_path / f"{name}.json")))
    return args


def test_sip_only_probe_rejects_bargein_profile_before_network(tmp_path: Path):
    proc = subprocess.run(
        [
            sys.executable,
            str(PROBE),
            "--stage-concurrency",
            "1",
            "--profile-baseline-percent",
            "99",
            "--profile-bargein-percent",
            "1",
            "--profile-transfer-percent",
            "0",
            *_required_output_args(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert proc.returncode != 0
    assert "cannot generate RTP media or execute/measure barge-in" in (
        proc.stdout + proc.stderr
    )
    assert not any(tmp_path.iterdir())


def test_transfer_forbidden_gate_rejects_any_scenario_activity():
    probe = _load_probe_module()
    result = probe._evaluate_gate(
        summary={
            "session_setup_success_ratio": 1.0,
            "ineffective_session_attempt_percent": 0.0,
            "srd_ms": {"p95": 1.0},
            "sdd_ms": {"p95": 1.0},
            "transfer": {"attempted": 1, "success": 1, "success_ratio": 1.0},
            "barge_in": {"reaction_ms": {"available": False, "p95": None}},
        },
        setup_success_min=0.99,
        srd_p95_max_ms=2000.0,
        sdd_p95_max_ms=1500.0,
        isa_max_percent=1.0,
        transfer_success_min=0.95,
        require_transfer=False,
        forbid_transfer=True,
        bargein_reaction_p95_max_ms=250.0,
        require_bargein_reaction=False,
    )

    assert result["pass"] is False
    assert any(
        "transfer scenario activity is forbidden" in reason
        for reason in result["reasons"]
    )


def test_verifier_disables_transfer_and_does_not_turn_unmeasured_bargein_into_pass():
    shell = VERIFIER.read_text(encoding="utf-8")
    probe = PROBE.read_text(encoding="utf-8")

    assert 'PROFILE_BASELINE_PERCENT="${DAY10_PROFILE_BASELINE_PERCENT:-100}"' in shell
    assert 'PROFILE_BARGEIN_PERCENT="${DAY10_PROFILE_BARGEIN_PERCENT:-0}"' in shell
    assert 'PROFILE_TRANSFER_PERCENT="${DAY10_PROFILE_TRANSFER_PERCENT:-0}"' in shell
    assert '--blind-transfer-enabled "$blind_transfer_enabled"' in shell
    assert "--blind-transfer-enabled 1" not in shell
    assert 'start_controller 0 0 "$BLIND_TRANSFER_ENABLED"' in shell
    assert "start_controller 20 1 0" in shell
    assert 'et.startswith("transfer_")' in shell
    assert "transfer activity detected while blind transfer is disabled" in shell
    assert '"status": "not_measured"' in probe
    assert '"pass": None' in probe
    assert '"bargein_pass"' not in shell
    assert "production_release_verdict" in shell
    assert "not_determined_by_this_harness" in shell


def test_gateway_verifiers_use_process_only_secrets_and_authenticated_clients():
    helper = (TELEPHONY_SCRIPTS / "gateway_test_env.sh").read_text(encoding="utf-8")
    assert "/dev/urandom" in helper
    assert 'INTERNAL_SERVICE_TOKEN="$internal_token"' in helper
    assert 'VOICE_GATEWAY_AUTH_TOKEN="$gateway_token"' in helper
    assert '[[ "$internal_token" == "$gateway_token" ]]' in helper
    assert 'echo "$internal_token"' not in helper
    assert 'echo "$gateway_token"' not in helper
    assert ".env" not in "\n".join(
        line for line in helper.splitlines() if not line.lstrip().startswith("#")
    )

    for day in range(4, 11):
        verifier = next(TELEPHONY_SCRIPTS.glob(f"verify_day{day}_*.sh"))
        source = verifier.read_text(encoding="utf-8")
        assert "provision_voice_gateway_test_env" in source
        assert 'echo "$INTERNAL_SERVICE_TOKEN"' not in source
        assert 'echo "$VOICE_GATEWAY_AUTH_TOKEN"' not in source
        assert "print(os.environ['VOICE_GATEWAY_AUTH_TOKEN'])" not in source

    for name in (
        "day4_rtp_probe.py",
        "day5_ari_external_media_controller.py",
        "day6_media_resilience_probe.py",
        "day8_tts_bargein_probe.py",
    ):
        source = (TELEPHONY_SCRIPTS / name).read_text(encoding="utf-8")
        assert '"Authorization"' in source
        assert "VOICE_GATEWAY_AUTH_TOKEN" in source
        assert "print(token" not in source
