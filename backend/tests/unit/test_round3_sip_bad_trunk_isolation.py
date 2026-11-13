"""F4-10: reject raw controls and isolate invalid persisted trunk config."""
from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.core import log_redact

from app.api.v1.endpoints.telephony_sip.schemas import SIPTrunkCreateRequest, SIPTrunkUpdateRequest
from scripts import reconcile_pjsip_configs as reconcile
from tests.unit.test_pjsip_config_reconciliation import (
    NOW, TENANT_A, TENANT_B, TRUNK_A, TRUNK_B, _assignment_row, _base_pjsip, _row,
)


def payload(**changes):
    values = {"trunk_name": "Synthetic carrier", "sip_domain": "sip.example.invalid",
              "auth_username": "synthetic-user", "auth_password": "synthetic-password"}
    values.update(changes)
    return values


@pytest.mark.parametrize("model", [SIPTrunkCreateRequest, SIPTrunkUpdateRequest])
@pytest.mark.parametrize("field", ["trunk_name", "auth_username", "auth_password", "sip_domain"])
@pytest.mark.parametrize("control", ["\n", "\r", "\t", "\x00", "\x1f", "\x7f", "\x85"])
def test_create_and_update_reject_raw_control_characters(model, field, control):
    original = payload()[field]
    with pytest.raises(ValidationError):
        model.model_validate(payload(**{field: original + control + "injected"}))


@pytest.mark.parametrize("model", [SIPTrunkCreateRequest, SIPTrunkUpdateRequest])
@pytest.mark.parametrize("control", ["\n", "\r", "\t"])
def test_host_validation_cannot_strip_away_a_raw_control(model, control):
    with pytest.raises(ValidationError):
        model.model_validate(payload(sip_domain=control + "sip.example.invalid" + control))


@pytest.mark.parametrize("model", [SIPTrunkCreateRequest, SIPTrunkUpdateRequest])
def test_valid_credentials_keep_exact_spaces_unicode_and_punctuation(model):
    secret = "  synthetic;pa\\ss-Ω!  "
    parsed = model.model_validate(payload(trunk_name="Synthetic Montréal", auth_password=secret))
    assert parsed.auth_password == secret
    assert parsed.trunk_name == "Synthetic Montréal"


def test_update_optional_auth_fields_remain_optional():
    parsed = SIPTrunkUpdateRequest.model_validate({"auth_username": None, "auth_password": None, "clear_auth": True})
    assert parsed.auth_username is None and parsed.auth_password is None and parsed.clear_auth


def build(rows, tmp_path, *, decrypt=None, inventory=None):
    return reconcile.build_candidate_set(
        rows, candidate_dir=tmp_path / "candidate",
        decrypt_password=decrypt or (lambda _: "synthetic-safe-secret"),
        observed_at=NOW,
        verified_account_dids=inventory or {"150001": "+442046132300", "150002": "+442046132301"},
    )


@pytest.mark.parametrize("field,value", [
    ("trunk_name", "private-name\n[forged-endpoint]"),
    ("auth_username", "private-user\n[forged-auth]"),
    ("sip_domain", "private-host\n[forged-host]"),
    ("port", 70000),
])
def test_invalid_persisted_trunk_does_not_abort_good_config_or_its_route(tmp_path, caplog, monkeypatch, field, value):
    # A valid UUID with hex letters stays distinct from phone-like digit runs.
    bad_id = "abcdeabc-abcd-4abc-8abc-abcdefabcdef"
    bad = _assignment_row(bad_id, account="150001")
    bad[field] = value
    good = _assignment_row(TRUNK_B, account="150002", did="+442046132301",
                           trunk_tenant_id=TENANT_B, assignment_tenant_id=TENANT_B)
    # Exercise the actual privacy filter even in an isolated file run.
    monkeypatch.setattr(log_redact, "pii_redaction_enabled", lambda: True)
    pii_filter = log_redact.PIIRedactionFilter()
    caplog.handler.addFilter(pii_filter)
    try:
        candidate = build([bad, good], tmp_path)
    finally:
        caplog.handler.removeFilter(pii_filter)
    assert f"pjsip.d/trunk-{TRUNK_B}.conf" in candidate.files
    assert f"pjsip.d/trunk-{bad_id}.conf" not in candidate.files
    assert [route.trunk_id for route in candidate.routes] == [TRUNK_B]
    assert candidate.invalid_trunks == (f"trunk-{bad_id}:invalid_configuration",)
    assert str(value) not in caplog.text
    assert "synthetic-safe-secret" not in caplog.text
    assert f"trunk-{bad_id}" in caplog.text


def test_decrypted_password_value_error_is_isolated_without_secret_logging(tmp_path, caplog):
    bad = _row(TRUNK_A, encrypted_password="bad-ciphertext")
    good = _row(TRUNK_B, encrypted_password="good-ciphertext")
    def decrypt(value):
        return "private-password\n[forged-auth]" if value == "bad-ciphertext" else "private-safe-password"
    candidate = build([bad, good], tmp_path, decrypt=decrypt)
    assert f"pjsip.d/trunk-{TRUNK_B}.conf" in candidate.files
    assert f"pjsip.d/trunk-{TRUNK_A}.conf" not in candidate.files
    assert candidate.invalid_trunks == (f"trunk-{TRUNK_A}:invalid_configuration",)
    for secret in ("bad-ciphertext", "good-ciphertext", "private-password", "private-safe-password"):
        assert secret not in caplog.text


def test_invalid_first_row_cannot_steal_verified_account_from_good_tenant(tmp_path):
    bad = _assignment_row(TRUNK_A, account="150001")
    bad["trunk_name"] = "private-invalid\n[forged]"
    good = _assignment_row(TRUNK_B, account="150001", trunk_tenant_id=TENANT_B, assignment_tenant_id=TENANT_B)
    good["assignment_id"] = "55555555-5555-4555-8555-555555555555"
    candidate = build([bad, good], tmp_path)
    assert [route.trunk_id for route in candidate.routes] == [TRUNK_B], "Bad trunk poisoned the shared-carrier route map"
    assert not any(f"trunk-{TRUNK_B}:" in value for value in candidate.unrouted_conflict_trunks)


def test_duplicate_bad_join_rows_produce_one_safe_invalid_marker(tmp_path):
    bad = _row(TRUNK_A)
    bad["trunk_name"] = "private-name\n[bad]"
    candidate = build([bad, deepcopy(bad), _row(TRUNK_B)], tmp_path)
    assert candidate.invalid_trunks == (f"trunk-{TRUNK_A}:invalid_configuration",)
    assert f"pjsip.d/trunk-{TRUNK_B}.conf" in candidate.files


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
def test_unexpected_decrypt_infrastructure_error_still_aborts(tmp_path, error_type):
    def decrypt(_):
        raise error_type("synthetic decryption infrastructure failure")
    with pytest.raises(error_type, match="synthetic decryption infrastructure failure"):
        build([_row(TRUNK_A)], tmp_path, decrypt=decrypt)


def test_unsafe_assignment_proof_still_fails_closed(tmp_path):
    bad = _assignment_row(TRUNK_A, trunk_tenant_id=TENANT_A, assignment_tenant_id=TENANT_B)
    with pytest.raises(reconcile.UnsafeInboundMappingError):
        build([bad], tmp_path)


def test_valid_only_candidate_preserves_exact_renderer_bytes_and_digest(tmp_path):
    row = _row(TRUNK_B)
    candidate = build([row], tmp_path)
    clean = dict(row)
    clean["metadata"] = dict(row["metadata"])
    clean["metadata"].pop("inbound_did", None)
    projection = reconcile.build_trunk_config_input(clean, decrypted_password="synthetic-safe-secret")
    expected = reconcile.render_trunk_conf(projection).encode("utf-8")
    assert candidate.files[f"pjsip.d/trunk-{TRUNK_B}.conf"] == expected
    assert candidate.digest == reconcile.compute_candidate_digest(candidate.files)
    assert candidate.invalid_trunks == ()


@pytest.mark.asyncio
async def test_safe_check_json_exposes_invalid_trunk_markers_without_row_values(tmp_path, monkeypatch, capsys):
    # Exercise the real check command using only synthetic rows and tmp paths;
    # never connect to DB, lock /run, inspect production files or invoke CLI.
    bad = _row(TRUNK_A)
    bad["trunk_name"] = "private-name\n[bad]"
    pool = SimpleNamespace(close=AsyncMock())
    monkeypatch.setenv("DATABASE_URL", "postgresql://synthetic@localhost/synthetic-never-connected")
    monkeypatch.setattr(reconcile.asyncpg, "create_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(reconcile, "fetch_reconciliation_rows", AsyncMock(return_value=[bad, _row(TRUNK_B)]))
    monkeypatch.setattr(reconcile, "exclusive_apply_lock", lambda _: nullcontext())
    monkeypatch.setattr(reconcile, "get_encryption_service", lambda: SimpleNamespace(decrypt=lambda _: "private-safe-secret"))
    monkeypatch.setattr(reconcile, "load_verified_account_dids", lambda _: {})
    monkeypatch.setattr(reconcile, "_run_asterisk", lambda _: pytest.fail("Check attempted Asterisk command"))
    base = tmp_path / "base.conf"
    base.write_bytes(_base_pjsip())
    args = reconcile.parse_args([
        "--candidate-dir", str(tmp_path / "candidate"), "--live-dir", str(tmp_path / "live"),
        "--dialplan-live-path", str(tmp_path / "extensions.conf"),
        "--base-pjsip-live-path", str(base), "--lock-path", str(tmp_path / "lock"),
    ])
    assert await reconcile._execute(args) == 1  # safe candidate differs from empty fake live dir
    output = capsys.readouterr().out
    parsed = json.loads(output)
    assert parsed["invalid_trunks"] == [f"trunk-{TRUNK_A}:invalid_configuration"]
    for private in ("private-name", "encrypted-password", "private-safe-secret"):
        assert private not in output
    pool.close.assert_awaited_once()
