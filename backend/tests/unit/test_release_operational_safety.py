"""Static and pure-function guards for fail-closed release operations."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

BACKEND = Path(__file__).parents[2]
ROOT = BACKEND.parent
RESTORE_SMOKE = BACKEND / "scripts" / "verify_restore_compatibility.py"
DRAIN_MANIFEST = BACKEND / "scripts" / "verify_deploy_drain_manifest.py"


def _load_restore_module():
    spec = importlib.util.spec_from_file_location("verify_restore_compatibility", RESTORE_SMOKE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_drain_manifest_module():
    spec = importlib.util.spec_from_file_location("verify_deploy_drain_manifest", DRAIN_MANIFEST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_drain_manifest(candidate_sha: str) -> dict:
    return {
        "schema_version": 1,
        "manifest_id": "drain-test-001",
        "candidate_sha": candidate_sha,
        "environment": "production",
        "issued_at": "2026-08-29T10:00:00Z",
        "expires_at": "2026-08-29T10:20:00Z",
        "traffic": {
            "ingress_disabled": True,
            "outbound_origination_disabled": True,
            "active_counts": {
                "gateway_sessions": 0,
                "asterisk_legs": 0,
                "redis_leases": 0,
                "db_live_calls": 0,
            },
        },
        "evidence": {
            "topology_ref": "topology:prod-asterisk-001",
            "change_ref": "change:CAB-001",
        },
        "approvers": [
            {
                "principal": "telephony-owner@example.com",
                "role": "telephony-owner",
                "approval_ref": "approval:CAB-001-a",
            },
            {
                "principal": "release-manager@example.com",
                "role": "release-manager",
                "approval_ref": "approval:CAB-001-b",
            },
        ],
    }


def _write_manifest(path: Path, value: dict) -> str:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_deploy_uses_one_frozen_sha_and_never_pulls_a_moving_tip():
    source = (ROOT / "deploy_to_server.sh").read_text(encoding="utf-8")

    assert 'DEPLOY_REF="${TALKY_DEPLOY_SHA:-HEAD}"' in source
    assert 'DEPLOY_SHA="$(git rev-parse --verify "${DEPLOY_REF}^{commit}")"' in source
    assert "git checkout --detach '${DEPLOY_SHA}'" in source
    assert "if [ \\\"\\$deployed_sha\\\" != '${DEPLOY_SHA}' ]" in source
    assert "git merge-base --is-ancestor '${DEPLOY_SHA}' FETCH_HEAD" in source
    assert "git pull" not in source
    assert "^[A-Za-z0-9][A-Za-z0-9._/-]*$" in source
    assert source.count("set -euo pipefail") >= 2
    assert "git checkout ${BRANCH}" not in source
    assert "git status --porcelain" in source


def test_deploy_and_rollback_require_fail_closed_health_probes():
    deploy = (ROOT / "deploy_to_server.sh").read_text(encoding="utf-8")
    deployment_doc = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")

    for endpoint in ("/health", "/api/v1/healthz/ready", "/api/v1/healthz/deep"):
        assert f"curl -fsS --max-time 10 http://127.0.0.1:8000{endpoint}" in deploy
        assert f"curl -fsS --max-time 10 http://127.0.0.1:8000{endpoint}" in deployment_doc

    rollback_doc = deployment_doc[deployment_doc.index("## Rollback") :]
    freeze_at = rollback_doc.index("Freeze and durably disable new inbound attempts")
    checkout_at = rollback_doc.index("git checkout --detach")
    probe_at = rollback_doc.index("# 4. Fail closed")
    assert freeze_at < checkout_at < probe_at
    assert "rollback is blocked and must not continue by guessing" in rollback_doc
    assert "does **not** yet contain an approved" in rollback_doc
    assert "Ingress-disable/rejection evidence ID" in rollback_doc
    assert "ACTIVE_CALLS_ZERO" in rollback_doc


def test_deploy_builds_tests_and_restarts_exact_gateway_before_backend():
    deploy = (ROOT / "deploy_to_server.sh").read_text(encoding="utf-8")
    builder = (BACKEND / "scripts" / "build_voice_gateway_release.sh").read_text(
        encoding="utf-8"
    )
    unit = (BACKEND / "systemd" / "talky-voice-gateway.service").read_text(
        encoding="utf-8"
    )
    bridge = (
        BACKEND / "app" / "api" / "v1" / "endpoints" / "telephony_bridge.py"
    ).read_text(encoding="utf-8")
    gateway_main = (ROOT / "services" / "voice-gateway-cpp" / "src" / "main.cpp").read_text(
        encoding="utf-8"
    )
    gateway_http = (
        ROOT / "services" / "voice-gateway-cpp" / "src" / "http_server.cpp"
    ).read_text(encoding="utf-8")
    gateway_gate = (
        ROOT / "services" / "voice-gateway-cpp" / "tests" / "run_gate.sh"
    ).read_text(encoding="utf-8")

    assert "build_voice_gateway_release.sh" in deploy
    assert "for required_tool in bash cmake ctest c++ make" in deploy
    candidate_build_at = deploy.index("build_voice_gateway_release.sh")
    checkout_at = deploy.index("git checkout --detach '${DEPLOY_SHA}'")
    assert candidate_build_at < checkout_at
    assert "git worktree add --detach" in deploy
    assert "TALKY_DEPLOY_DRAIN_MANIFEST" in deploy
    assert "TALKY_DEPLOY_DRAIN_EVIDENCE_ID" not in deploy
    assert "TALKY_DEPLOY_DRAIN_ASSERTION" not in deploy
    assert "verify_deploy_drain_manifest.py" in deploy
    verifier_at = deploy.index("verify_deploy_drain_manifest.py")
    assert deploy.index('DEPLOY_SHA="$(git rev-parse') < verifier_at
    assert deploy.index('git merge-base --is-ancestor "$DEPLOY_SHA"') < verifier_at
    assert verifier_at < deploy.index('ssh -t -i "$KEY"')
    assert 'get(\\"active_sessions\\")' in deploy
    assert deploy.count('get(\\"active_sessions\\")') >= 3
    assert "Gateway still owns" in deploy
    assert "sudo systemctl restart talky-voice-gateway" in deploy
    assert deploy.index("sudo systemctl restart talky-voice-gateway") < deploy.index(
        "sudo systemctl restart talky-api"
    )
    assert "cmake --build" in builder
    assert "ctest --test-dir" in builder
    assert '-DVOICE_GATEWAY_BUILD_SHA="${build_sha}"' in builder
    assert '[[ ! "${build_sha}" =~ ^[0-9a-f]{40,64}$ ]]' in builder
    assert "timeout 5s env -u INTERNAL_SERVICE_TOKEN" in builder
    assert '[[ "${missing_token_rc}" -ne 2 ]]' in builder
    assert "EnvironmentFile=/opt/talky/backend/.env" in unit
    assert "ExecStart=/opt/talky/runtime/bin/voice_gateway" in unit
    assert 'if not is_internal_service_request(request):' in bridge
    assert "_gateway_audio_auth_enforced" not in bridge
    assert "validate_gateway_security_environment(security_error)" in gateway_main
    assert "INTERNAL_SERVICE_TOKEN is missing or invalid" in gateway_http
    assert "VOICE_GATEWAY_AUTH_TOKEN is missing or invalid" in gateway_http
    assert "INTERNAL_SERVICE_TOKEN and VOICE_GATEWAY_AUTH_TOKEN must be distinct" in gateway_http
    assert "BACKEND_INTERNAL_URL must be an exact plain http numeric loopback origin" in gateway_http
    assert "VOICE_GATEWAY_CALLBACK_HOST must exactly match" in gateway_http
    assert 'kAudioPathPrefix = "/api/v1/sip/telephony/audio/"' in gateway_http
    assert "if (!audio_callback_url_is_allowed(url))" in gateway_http
    assert "audio_callback_url_is_allowed(audio_callback_url.value(), config.session_id)" in gateway_http
    post_at = gateway_http.index("bool http_post")
    sender_guard_at = gateway_http.index("if (!audio_callback_url_is_allowed(url))", post_at)
    internal_header_at = gateway_http.index('req << "X-Internal-Service-Token: "', post_at)
    socket_at = gateway_http.index("candidate.fd = socket", post_at)
    assert post_at < sender_guard_at < internal_header_at < socket_at
    gateway_fixes = (
        ROOT / "services" / "voice-gateway-cpp" / "tests" / "test_gateway_fixes.cpp"
    ).read_text(encoding="utf-8")
    assert "origin_wrong_loopback_port_rejected" in gateway_fixes
    assert "origin_reused_secrets_rejected" in gateway_fixes
    assert "provision_voice_gateway_test_env" in gateway_gate
    assert 'request->path == "/health" || request->path == "/ready" || request->path == "/stats"' in gateway_http
    assert "http://127.0.0.1:18080/ready" in deploy
    assert 'p.get(\\"protocol_version\\") == 2' in deploy
    assert 'p.get(\\"build_sha\\") == sys.argv[1]' in deploy
    assert "VOICE_GATEWAY_BUILD_SHA" in gateway_http


def test_trunk_status_one_shot_cannot_abort_an_otherwise_healthy_deploy():
    deploy = (ROOT / "deploy_to_server.sh").read_text(encoding="utf-8")

    assert "if ! sudo systemctl start talky-trunk-status.service; then" in deploy
    assert "timer remains active and will retry" in deploy


def test_drain_manifest_binds_candidate_external_state_and_is_single_use(tmp_path):
    verifier = _load_drain_manifest_module()
    candidate = "a" * 40
    manifest_path = tmp_path / "drain.json"
    replay_dir = tmp_path / "used"
    digest = _write_manifest(manifest_path, _valid_drain_manifest(candidate))
    now = datetime(2026, 8, 29, 10, 5, tzinfo=UTC)

    assert verifier.verify_and_consume_manifest(
        manifest_path=manifest_path,
        candidate_sha=candidate,
        expected_sha256=digest,
        replay_dir=replay_dir,
        now=now,
    ) == "drain-test-001"
    marker = replay_dir / "drain-test-001.used"
    assert marker.is_file()
    assert f"sha256={digest}" in marker.read_text(encoding="ascii")

    with pytest.raises(verifier.ManifestError, match="already been consumed"):
        verifier.verify_and_consume_manifest(
            manifest_path=manifest_path,
            candidate_sha=candidate,
            expected_sha256=digest,
            replay_dir=replay_dir,
            now=now,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(candidate_sha="b" * 40), "candidate_sha"),
        (lambda value: value.update(environment="staging"), "environment"),
        (lambda value: value.update(issued_at="2026-08-29T09:00:00Z"), "older than"),
        (lambda value: value.update(expires_at="2026-08-29T10:04:59Z"), "expired"),
        (
            lambda value: value["traffic"].update(ingress_disabled=False),
            "ingress_disabled",
        ),
        (
            lambda value: value["traffic"].update(outbound_origination_disabled=False),
            "outbound_origination_disabled",
        ),
        (
            lambda value: value["traffic"]["active_counts"].update(asterisk_legs=1),
            "asterisk_legs",
        ),
        (
            lambda value: value["traffic"]["active_counts"].update(redis_leases=False),
            "redis_leases",
        ),
        (
            lambda value: value["approvers"][1].update(
                principal="telephony-owner@example.com"
            ),
            "distinct principals",
        ),
    ],
)
def test_drain_manifest_rejects_mismatch_staleness_nonzero_and_collusion(
    tmp_path, mutation, message
):
    verifier = _load_drain_manifest_module()
    candidate = "a" * 40
    value = _valid_drain_manifest(candidate)
    mutation(value)
    manifest_path = tmp_path / "drain.json"
    digest = _write_manifest(manifest_path, value)

    with pytest.raises(verifier.ManifestError, match=message):
        verifier.verify_and_consume_manifest(
            manifest_path=manifest_path,
            candidate_sha=candidate,
            expected_sha256=digest,
            replay_dir=tmp_path / "used",
            now=datetime(2026, 8, 29, 10, 5, tzinfo=UTC),
        )


def test_drain_manifest_rejects_digest_tampering_and_duplicate_keys(tmp_path):
    verifier = _load_drain_manifest_module()
    candidate = "a" * 40
    manifest_path = tmp_path / "drain.json"
    _write_manifest(manifest_path, _valid_drain_manifest(candidate))

    with pytest.raises(verifier.ManifestError, match="SHA-256"):
        verifier.verify_and_consume_manifest(
            manifest_path=manifest_path,
            candidate_sha=candidate,
            expected_sha256="0" * 64,
            replay_dir=tmp_path / "used-a",
        )

    raw = manifest_path.read_text(encoding="utf-8")
    duplicate = raw[:-1] + ',"environment":"production"}'
    manifest_path.write_text(duplicate, encoding="utf-8")
    duplicate_digest = hashlib.sha256(duplicate.encode()).hexdigest()
    with pytest.raises(verifier.ManifestError, match="duplicate JSON key"):
        verifier.verify_and_consume_manifest(
            manifest_path=manifest_path,
            candidate_sha=candidate,
            expected_sha256=duplicate_digest,
            replay_dir=tmp_path / "used-b",
        )


def test_drain_manifest_id_cannot_escape_replay_ledger(tmp_path):
    verifier = _load_drain_manifest_module()
    candidate = "a" * 40
    value = _valid_drain_manifest(candidate)
    value["manifest_id"] = "../outside"
    manifest_path = tmp_path / "drain.json"
    digest = _write_manifest(manifest_path, value)

    with pytest.raises(verifier.ManifestError, match="without separators"):
        verifier.verify_and_consume_manifest(
            manifest_path=manifest_path,
            candidate_sha=candidate,
            expected_sha256=digest,
            replay_dir=tmp_path / "used",
            now=datetime(2026, 8, 29, 10, 5, tzinfo=UTC),
        )
    assert not (tmp_path / "outside.used").exists()


def test_deployment_docs_keep_external_attestation_and_toctou_limit_explicit():
    deployment_doc = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "repository code does not yet read" in deployment_doc
    assert "carrier, Asterisk legs, Redis leases, or database live-call state" in deployment_doc
    assert "externally attested" in deployment_doc
    assert "neither the manifest nor a phrase eliminates TOCTOU" in deployment_doc
    assert "closes the check/restart race" not in deployment_doc


def test_backup_and_restore_examples_are_fail_fast_and_isolated():
    runbook = (ROOT / "docs" / "RUNBOOK.md").read_text(encoding="utf-8")

    assert runbook.count("set -Eeuo pipefail") >= 2
    assert "refusing to overwrite existing backup artifact" in runbook
    assert 'test -s "${partial}"' in runbook
    assert 'checksum_partial="${checksum_file}.partial"' in runbook
    assert '[[ "$digest" =~ ^[0-9a-f]{64}$ ]]' in runbook
    assert 'if [[ "$rc" -ne 0 && "${published:-0}" -eq 1 ]]' in runbook
    assert "trap cleanup_partial EXIT" in runbook
    assert "trap cleanup_restore EXIT" in runbook
    assert 'restore_db="talky_restore_' in runbook
    assert 'dropdb -U talky --if-exists --force "${restore_db}"' in runbook
    assert "verify_restore_compatibility.py" in runbook
    assert '--database "${restore_db}"' in runbook
    assert "never use the production `talky`" in runbook


def test_restore_smoke_rewrites_only_to_explicit_isolated_database():
    module = _load_restore_module()
    source = "postgresql+asyncpg://talky:secret@127.0.0.1:5432/talky?sslmode=require"

    target, source_database = module._target_dsn(source, "talky_restore_20260829T010203Z")
    assert source_database == "talky"
    assert target.startswith("postgresql://talky:secret@127.0.0.1:5432/")
    assert "/talky_restore_20260829T010203Z?sslmode=require" in target

    for unsafe in ("talky", "talkyai", "postgres", "other_restore", "talky_restore_bad-name"):
        with pytest.raises(ValueError):
            module._target_dsn(source, unsafe)


def test_restore_smoke_is_executable_read_only_and_migration_exact():
    source = RESTORE_SMOKE.read_text(encoding="utf-8")
    assert "conn.transaction(readonly=True)" in source
    assert "restored_heads != expected_heads" in source
    assert "LIMIT 0" in source
    assert 'importlib.import_module("app.main")' in source
    assert "alembic upgrade" not in source

    result = subprocess.run(
        [sys.executable, str(RESTORE_SMOKE), "--help"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--database" in result.stdout


def test_redis_credentials_are_not_documented_as_process_arguments():
    runbook = (ROOT / "docs" / "RUNBOOK.md").read_text(encoding="utf-8")
    assert "redis-cli -a" not in runbook
    assert "redis-cli --pass" not in runbook
    assert "redis-cli --password" not in runbook
    assert "-e REDISCLI_AUTH redis redis-cli" in runbook


def test_prometheus_custom_header_is_file_backed_not_tracked_literal():
    prom = (ROOT / "telephony" / "observability" / "prometheus" / "prometheus.yml").read_text(
        encoding="utf-8"
    )
    compose = (
        ROOT / "telephony" / "deploy" / "docker" / "docker-compose.observability.yml"
    ).read_text(encoding="utf-8")
    readme = (ROOT / "telephony" / "observability" / "README.md").read_text(encoding="utf-8")

    assert "X-Metrics-Token:" in prom
    assert "files:" in prom
    assert "/etc/prometheus/secrets/talky_metrics_token" in prom
    assert "\n        values:" not in prom
    assert "replace-with-real-token" not in prom
    assert "TELEPHONY_METRICS_TOKEN_FILE:?" in compose
    assert "PROMETHEUS_SECRET_GID:?" in compose
    assert "group_add:" in compose
    assert "TELEPHONY_METRICS_TOKEN_FILE" in readme
    assert "does not install a production Prometheus service" in readme
