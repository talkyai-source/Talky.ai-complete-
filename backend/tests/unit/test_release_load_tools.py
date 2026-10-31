"""Behavioral contracts for the release load driver and soak orchestrator."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
LOADTEST_PATH = BACKEND / "scripts" / "loadtest_calls.py"
SOAK_PATH = BACKEND / "scripts" / "soak_runner.sh"
RELEASE_GATE = ROOT / "docs" / "INBOUND_CALLING_RELEASE_GATE.md"


def _load_loadtest_module():
    spec = importlib.util.spec_from_file_location("release_loadtest_calls", LOADTEST_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


loadtest = _load_loadtest_module()


class _FakeResponse:
    def __init__(self, status: int, payload: object | None = None, body: str = "") -> None:
        self.status = status
        self._payload = payload
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def text(self) -> str:
        return self._body

    async def json(self, *, content_type=None):
        del content_type
        if self._payload is None:
            raise ValueError("no JSON payload")
        return self._payload


class _RecordingSession:
    def __init__(self, status: int = 200, payload: object | None = None) -> None:
        self.status = status
        self.payload = payload or {"status": "calling", "call_id": "call-1"}
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse(self.status, self.payload)


@pytest.mark.asyncio
async def test_legacy_loadtest_origination_is_retired_before_network():
    session = _RecordingSession()
    stats = loadtest.Stats()

    with pytest.raises(RuntimeError, match="durable dialer jobs"):
        await loadtest._originate_one(
            session,
            "https://staging.example.test/",
            "+15551234567",
            "11111111-1111-1111-1111-111111111111",
            "+15557654321",
            "internal-test-token",
            stats,
        )

    assert session.calls == []


@pytest.mark.asyncio
async def test_queued_response_is_not_counted_as_an_origination():
    session = _RecordingSession(status=202, payload={"status": "queued"})
    stats = loadtest.Stats()

    with pytest.raises(RuntimeError, match="durable dialer jobs"):
        await loadtest._originate_one(
            session,
            "https://staging.example.test/",
            "+15551234567",
            "11111111-1111-1111-1111-111111111111",
            "+15557654321",
            "internal-test-token",
            stats,
        )

    assert stats.queued == 0
    assert stats.originated == 0
    assert stats.originated_call_ids == []


@pytest.mark.asyncio
async def test_worker_pool_enforces_requested_concurrency(monkeypatch):
    active = 0
    maximum = 0
    attempts = 0

    async def fake_originate(*_args):
        nonlocal active, maximum, attempts
        active += 1
        attempts += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0.02)
        finally:
            active -= 1

    monkeypatch.setattr(loadtest, "_originate_one", fake_originate)

    async def zero_live(_session, _base_url, _token, stats):
        stats.live_samples += 1
        stats.final_live_calls = 0
        return 0

    monkeypatch.setattr(loadtest, "_sample_live_count", zero_live)

    await loadtest._ramp_and_hold(
        base_url="http://unused.test",
        request_workers=3,
        duration_s=0.3,
        tenant_id="tenant-test",
        destination="9999",
        caller_id="1001",
        internal_token="test-token",
        rps=1000,
        stats=loadtest.Stats(),
        status_poll_s=0.005,
        drain_timeout_s=0.1,
    )

    assert attempts > 3
    assert maximum == 3
    assert active == 0


def _passing_stats(*, originated: int = 300, peak: int = 25) -> object:
    return loadtest.Stats(
        originated=originated,
        baseline_live_calls=0,
        max_live_calls=peak,
        final_live_calls=0,
        live_samples=10,
        drain_complete=True,
        originated_call_ids=[f"call-{index}" for index in range(originated)],
    )


@pytest.mark.parametrize(
    ("stats", "expected"),
    [
        (_passing_stats(), 0),
        (_passing_stats(originated=299), 1),
        (_passing_stats(peak=24), 1),
        (loadtest.Stats(queued=1000), 1),
    ],
)
def test_result_requires_300_originations_and_proven_live_peak(stats, expected):
    assert (
        loadtest._result_exit_code(
            stats,
            minimum_originated=300,
            required_peak_live=25,
        )
        == expected
    )


@pytest.mark.parametrize(
    "mutation",
    [
        {"errors": 1},
        {"rejected_other": 1},
        {"status_errors": 1},
        {"baseline_live_calls": 1},
        {"final_live_calls": 1},
        {"drain_complete": False},
        {"live_samples": 2},
    ],
)
def test_result_rejects_untrustworthy_live_evidence(mutation):
    stats = _passing_stats()
    for name, value in mutation.items():
        setattr(stats, name, value)
    assert (
        loadtest._result_exit_code(
            stats,
            minimum_originated=300,
            required_peak_live=25,
        )
        == 1
    )


def test_live_count_uses_redis_backed_cluster_value_not_request_or_pod_count():
    payload = {
        "status": "running",
        "connected": True,
        "healthy": True,
        "active_sessions": 2,
        "capacity": {
            "current": 2,
            "max": 10,
            "global_current": 17,
            "global_max": 50,
        },
    }
    assert loadtest._live_count_from_status(payload) == 17

    payload["capacity"]["global_current"] = None
    with pytest.raises(ValueError, match="global_current"):
        loadtest._live_count_from_status(payload)

    payload["capacity"]["global_current"] = 17
    payload["healthy"] = False
    with pytest.raises(ValueError, match="healthy and running"):
        loadtest._live_count_from_status(payload)


def test_evidence_records_unique_call_ids_and_refuses_overwrite(tmp_path):
    target = tmp_path / "evidence.json"
    stats = _passing_stats()

    loadtest._write_evidence(
        target,
        stats=stats,
        minimum_originated=300,
        required_peak_live=25,
        duration_s=14400,
        request_workers=10,
        rps=2.0,
        started_at_epoch=1.0,
        finished_at_epoch=2.0,
        exit_code=0,
        driver_error=None,
    )

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["observed"]["originated"] == 300
    assert len(payload["observed"]["originated_call_ids"]) == 300
    assert payload["observed"]["max_live_calls"] == 25
    assert payload["observed"]["max_request_inflight"] == 0
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        loadtest._write_evidence(
            target,
            stats=stats,
            minimum_originated=300,
            required_peak_live=25,
            duration_s=14400,
            request_workers=10,
            rps=2.0,
            started_at_epoch=1.0,
            finished_at_epoch=2.0,
            exit_code=0,
            driver_error=None,
        )


def test_cli_rejects_one_call_floor_and_removed_concurrent_alias(tmp_path):
    base = [
        sys.executable,
        str(LOADTEST_PATH),
        "--tenant-id",
        "11111111-1111-1111-1111-111111111111",
        "--minimum-originated",
        "1",
        "--required-peak-live",
        "1",
        "--evidence-json",
        str(tmp_path / "evidence.json"),
        "--duration",
        "1",
    ]
    env = {**os.environ, "INTERNAL_SERVICE_TOKEN": "test-internal-token"}
    one_call = subprocess.run(
        base,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        check=False,
    )
    assert one_call.returncode == 2
    assert "durable dialer jobs" in one_call.stderr

    removed_alias = subprocess.run(
        [*base, "--concurrent", "50"],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        check=False,
    )
    assert removed_alias.returncode == 2
    assert "unrecognized arguments: --concurrent" in removed_alias.stderr


def test_soak_and_runbook_never_conflate_http_workers_with_live_calls():
    load_source = LOADTEST_PATH.read_text(encoding="utf-8")
    soak_source = SOAK_PATH.read_text(encoding="utf-8")
    release_gate = RELEASE_GATE.read_text(encoding="utf-8")
    normalized_release_gate = re.sub(r"\s+", " ", release_gate)

    assert '"--concurrent"' not in load_source
    assert "PEAK_CONCURRENT" not in soak_source
    assert "REQUIRED_ORIGINATIONS=300" in soak_source
    assert '--request-workers "$REQUEST_WORKERS"' in soak_source
    assert '--minimum-originated "$REQUIRED_ORIGINATIONS"' in soak_source
    assert '--required-peak-live "$REQUIRED_PEAK_LIVE_CALLS"' in soak_source
    assert "validate_load_evidence" in soak_source
    assert 'mkdir -p -- "$RESULTS_ROOT"' in soak_source
    assert 'mkdir -- "$OUT"' in soak_source
    assert 'mkdir -p "$OUT"' not in soak_source
    assert 'ln -- "$partial" "$snapshot"' in soak_source
    assert 'mv -- "$partial" "$snapshot"' not in soak_source

    for marker in (
        "it is not evidence of live-call concurrency",
        "any queued HTTP 202 response fails evidence",
        "capacity.global_current",
        "A single accepted request",
        "does **not** prove",
        "carrier-delivered inbound batch",
    ):
        assert marker in normalized_release_gate


def _find_bash() -> str | None:
    if os.name == "nt":
        for candidate in (
            Path(r"C:\Program Files\Git\bin\bash.exe"),
            Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
        ):
            if candidate.exists():
                return str(candidate)
    from shutil import which

    return which("bash")


def _shell_path(path: Path) -> str:
    resolved = str(path.resolve()).replace("\\", "/")
    if os.name == "nt" and len(resolved) >= 3 and resolved[1:3] == ":/":
        return f"/{resolved[0].lower()}{resolved[2:]}"
    return resolved


def _make_soak_harness(
    tmp_path: Path,
    *,
    prometheus_result: list[dict] | None = None,
    load_exit: int = 0,
    apply_fails: bool = False,
) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    kubectl_log = tmp_path / "kubectl.log"

    kubectl = fake_bin / "kubectl"
    kubectl.write_text(
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_KUBECTL_LOG"
if [[ "${1:-}" == "apply" && "${FAKE_APPLY_FAIL:-0}" == "1" ]]; then
  exit 9
fi
exit 0
""",
        encoding="utf-8",
        newline="\n",
    )

    curl = fake_bin / "curl"
    curl.write_text(
        """#!/usr/bin/env bash
output=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output|-o)
      shift
      output="${1:-}"
      ;;
  esac
  shift || true
done
[[ -n "$output" ]] || exit 8
printf '%s' "$FAKE_PROM_RESPONSE" > "$output"
if [[ "${FAKE_SNAPSHOT_RACE:-0}" == "1" ]]; then
  snapshot="${output%.partial.*}"
  printf 'pre-existing evidence' > "$snapshot"
fi
""",
        encoding="utf-8",
        newline="\n",
    )
    os.chmod(kubectl, 0o755)
    os.chmod(curl, 0o755)

    load_script = tmp_path / "fake_load.py"
    load_script.write_text(
        (
            "import os, pathlib, sys, time\n"
            "if os.getenv('FAKE_LOAD_EVIDENCE'):\n"
            "    target = pathlib.Path(sys.argv[sys.argv.index('--evidence-json') + 1])\n"
            "    target.write_text(os.environ['FAKE_LOAD_EVIDENCE'], encoding='utf-8')\n"
            "time.sleep(0.05)\n"
            f"raise SystemExit({load_exit})\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    if prometheus_result is None:
        prometheus_result = [
            {
                "metric": {"__name__": "talky_telephony_calls_setup_attempts"},
                "value": [1_788_000_000, "3"],
            }
        ]
    response = {
        "status": "success",
        "data": {"resultType": "vector", "result": prometheus_result},
    }

    python_bin = Path(sys.executable)
    env = {
        **os.environ,
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
        "SOAK_PYTHON_BIN": _shell_path(python_bin),
        "SOAK_LOADTEST_SCRIPT": _shell_path(load_script),
        "SOAK_RESULTS_DIR": _shell_path(tmp_path / "results"),
        "SOAK_POLL_INTERVAL_SEC": "1",
        "SOAK_METRICS_INTERVAL_SEC": "10",
        "DURATION_SEC": "30",
        "REQUIRED_PEAK_LIVE_CALLS": "2",
        "INTERNAL_SERVICE_TOKEN": "test-internal-token",
        "LOADTEST_TENANT_ID": "11111111-1111-1111-1111-111111111111",
        "FAKE_KUBECTL_LOG": _shell_path(kubectl_log),
        "SOAK_TEST_FAKE_BIN": _shell_path(fake_bin),
        "FAKE_PROM_RESPONSE": json.dumps(response),
        "FAKE_SNAPSHOT_RACE": "0",
        "FAKE_APPLY_FAIL": "1" if apply_fails else "0",
        "FAKE_LOAD_EVIDENCE": "",
    }
    return env, kubectl_log


def _run_soak(env: dict[str, str], *, timeout: int = 20) -> subprocess.CompletedProcess:
    bash = _find_bash()
    if not bash:
        pytest.skip("Bash is required for soak-runner behavior tests")
    return subprocess.run(
        [
            bash,
            "-c",
            'export PATH="$1:$PATH"; exec bash "$2"',
            "bash",
            env["SOAK_TEST_FAKE_BIN"],
            _shell_path(SOAK_PATH),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_soak_rejects_empty_prometheus_result_before_applying_chaos(tmp_path):
    env, kubectl_log = _make_soak_harness(tmp_path, prometheus_result=[])

    result = _run_soak(env)

    assert result.returncode != 0
    assert "Prometheus result is empty" in result.stderr
    assert "initial Prometheus snapshot failed" in result.stderr
    assert not kubectl_log.exists()


def test_soak_refuses_to_reuse_a_same_stamp_result_directory(tmp_path):
    env, kubectl_log = _make_soak_harness(tmp_path)
    fixed_stamp = "20260829T120000Z"
    existing = tmp_path / "results" / fixed_stamp
    existing.mkdir(parents=True)
    marker = existing / "preserve.txt"
    marker.write_text("original", encoding="utf-8")

    fake_date = tmp_path / "bin" / "date"
    fake_date.write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' '{fixed_stamp}'\n",
        encoding="utf-8",
        newline="\n",
    )
    os.chmod(fake_date, 0o755)

    result = _run_soak(env)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "refusing to reuse existing soak result directory" in result.stderr
    assert marker.read_text(encoding="utf-8") == "original"
    assert not kubectl_log.exists()


def test_soak_snapshot_publication_refuses_a_racing_destination(tmp_path):
    env, kubectl_log = _make_soak_harness(tmp_path)
    env["FAKE_SNAPSHOT_RACE"] = "1"

    result = _run_soak(env)

    assert result.returncode != 0
    assert "refusing to overwrite Prometheus snapshot" in result.stderr
    snapshots = list((tmp_path / "results").glob("*/snap_0.json"))
    assert len(snapshots) == 1
    assert snapshots[0].read_text(encoding="utf-8") == "pre-existing evidence"
    assert not kubectl_log.exists()


def test_soak_requires_an_explicit_approved_live_call_peak_before_chaos(tmp_path):
    env, kubectl_log = _make_soak_harness(tmp_path)
    env.pop("REQUIRED_PEAK_LIVE_CALLS")

    result = _run_soak(env)

    assert result.returncode == 2
    assert "REQUIRED_PEAK_LIVE_CALLS must be a positive integer" in result.stderr
    assert not kubectl_log.exists()


def test_soak_reaps_failed_load_immediately_and_cleans_both_experiments(tmp_path):
    env, kubectl_log = _make_soak_harness(tmp_path, load_exit=7)

    started = time.monotonic()
    result = _run_soak(env)
    elapsed = time.monotonic() - started

    assert result.returncode == 7, result.stdout + result.stderr
    assert elapsed < 6
    assert "load driver exited before monitor deadline rc=7" in result.stderr
    kubectl_calls = kubectl_log.read_text(encoding="utf-8")
    assert "delete -f infra/chaos/pod-kill.yaml" in kubectl_calls
    assert "delete -f infra/chaos/redis-partition.yaml" in kubectl_calls


def test_uncertain_first_chaos_apply_still_deletes_both_experiments(tmp_path):
    env, kubectl_log = _make_soak_harness(tmp_path, apply_fails=True)

    result = _run_soak(env)

    assert result.returncode == 9, result.stdout + result.stderr
    kubectl_calls = kubectl_log.read_text(encoding="utf-8")
    assert "apply -f infra/chaos/pod-kill.yaml" in kubectl_calls
    assert "delete -f infra/chaos/pod-kill.yaml" in kubectl_calls
    assert "delete -f infra/chaos/redis-partition.yaml" in kubectl_calls


def _soak_evidence(*, originated: int, peak: int = 2, duration: int = 1) -> dict:
    call_ids = [f"call-{index}" for index in range(originated)]
    return {
        "schema_version": 1,
        "passed": True,
        "exit_code": 0,
        "driver_error": None,
        "requirements": {
            "minimum_originated": 300,
            "required_peak_live": 2,
            "zero_live_baseline": True,
            "zero_live_drain": True,
        },
        "configuration": {
            "duration_seconds": duration,
            "request_workers": 10,
            "requests_per_second": 2.0,
            "started_at_epoch": 1.0,
            "finished_at_epoch": 2.0,
        },
        "observed": {
            "originated": originated,
            "queued": 0,
            "rejected_503": 0,
            "rejected_other": 0,
            "request_errors": 0,
            "live_status_errors": 0,
            "max_request_inflight": 10,
            "baseline_live_calls": 0,
            "max_live_calls": peak,
            "final_live_calls": 0,
            "live_samples": 10,
            "drain_complete": True,
            "originated_call_ids": call_ids,
        },
        "failures": [],
    }


def test_soak_rejects_exit_zero_without_machine_readable_load_evidence(tmp_path):
    env, kubectl_log = _make_soak_harness(tmp_path, load_exit=0)
    env["DURATION_SEC"] = "1"

    result = _run_soak(env)

    assert result.returncode != 0
    assert "invalid load evidence" in result.stderr
    assert "missing, contradictory, or below" in result.stderr
    kubectl_calls = kubectl_log.read_text(encoding="utf-8")
    assert "delete -f infra/chaos/pod-kill.yaml" in kubectl_calls
    assert "delete -f infra/chaos/redis-partition.yaml" in kubectl_calls


def test_soak_rejects_one_originated_call_as_300_call_evidence(tmp_path):
    env, _kubectl_log = _make_soak_harness(tmp_path, load_exit=0)
    env["DURATION_SEC"] = "1"
    env["FAKE_LOAD_EVIDENCE"] = json.dumps(_soak_evidence(originated=1))

    result = _run_soak(env)

    assert result.returncode != 0
    assert "fewer than 300 calls were actually originated" in result.stderr


def test_soak_accepts_only_matching_300_call_peak_and_drain_evidence(tmp_path):
    env, _kubectl_log = _make_soak_harness(tmp_path, load_exit=0)
    env["DURATION_SEC"] = "1"
    env["FAKE_LOAD_EVIDENCE"] = json.dumps(_soak_evidence(originated=300))

    result = _run_soak(env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "evidence_rc=0" in result.stdout
