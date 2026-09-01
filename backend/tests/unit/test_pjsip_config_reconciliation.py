"""Release-gate tests for deterministic, fail-closed PJSIP reconciliation."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import reconcile_pjsip_configs as reconcile

TRUNK_A = "11111111-1111-4111-8111-111111111111"
TRUNK_B = "22222222-2222-4222-8222-222222222222"
TRUNK_C = "33333333-3333-4333-8333-333333333333"
TENANT_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def _base_pjsip(context: str = "from-tenant-manual") -> bytes:
    return (
        "[global]\r\n"
        "type=global\r\n"
        "\r\n"
        "[blazedigitel-endpoint]\r\n"
        "type=endpoint\r\n"
        f" context = {context}\r\n"
        "disallow=all\r\n"
        "\r\n"
        "[blazedigitel-identify]\r\n"
        "type=identify\r\n"
    ).encode()


def test_shared_carrier_context_transform_is_byte_preserving_except_context():
    source = _base_pjsip()

    rendered = reconcile.render_shared_endpoint_base_pjsip(source)

    assert rendered == source.replace(
        b" context = from-tenant-manual\r\n",
        b" context=from-talky-inbound\r\n",
    )


@pytest.mark.parametrize(
    "source",
    [
        b"[global]\ntype=global\n",
        _base_pjsip() + _base_pjsip(),
        _base_pjsip().replace(
            b"disallow=all", b"context=from-another-place\r\ndisallow=all"
        ),
    ],
)
def test_shared_carrier_context_transform_rejects_ambiguous_base_config(source):
    with pytest.raises(reconcile.PJSIPReconciliationError, match="shared carrier"):
        reconcile.render_shared_endpoint_base_pjsip(source)


@pytest.fixture
def chmod_calls(monkeypatch):
    """Record requested POSIX modes even though Windows does not expose them."""
    calls: list[tuple[Path, int]] = []
    real_chmod = os.chmod

    def recording_chmod(path, mode, *args, **kwargs):
        calls.append((Path(path), mode))
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(reconcile.os, "chmod", recording_chmod)
    return calls


def _row(
    trunk_id: str,
    *,
    direction: str = "both",
    username: str = "250001",
    caller_id: str = "+442046132300",
    inbound_did: str | None = "+442046132300",
    encrypted_password: str = "encrypted-password",
) -> dict:
    metadata: dict[str, object] = {
        "register": True,
        "caller_id": caller_id,
    }
    if inbound_did is not None:
        metadata["inbound_did"] = inbound_did
    return {
        "id": trunk_id,
        "tenant_id": TENANT_A,
        "trunk_name": f"tenant-{trunk_id[:4]}",
        "sip_domain": "sip3.example.invalid",
        "port": 5060,
        "transport": "udp",
        "direction": direction,
        "auth_username": username,
        "auth_password_encrypted": encrypted_password,
        "metadata": metadata,
    }


def _assignment_row(
    trunk_id: str = TRUNK_A,
    *,
    account: str = "150001",
    did: str = "+442046132300",
    trunk_tenant_id: str = TENANT_A,
    assignment_tenant_id: str = TENANT_A,
    status: str = "active",
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> dict:
    row = _row(
        trunk_id,
        username=account,
        inbound_did=None,
    )
    row.update(
        {
            "tenant_id": trunk_tenant_id,
            "assignment_id": "44444444-4444-4444-8444-444444444444",
            "assignment_tenant_id": assignment_tenant_id,
            "assignment_trunk_id": trunk_id,
            "assignment_did": did,
            "assignment_status": status,
            "assignment_valid_from": valid_from or (NOW - timedelta(days=1)),
            "assignment_valid_to": valid_to,
            "phone_status": "verified",
            "verified_did": did,
        }
    )
    return row


def test_account_route_is_derived_from_same_tenant_active_assignment_not_metadata(
    tmp_path,
):
    row = _assignment_row()
    row["metadata"]["inbound_did"] = "+442046132399"

    candidate = reconcile.build_candidate_set(
        [row],
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "secret",
        observed_at=NOW,
    )

    dialplan = candidate.files["extensions.d/talky-inbound.conf"].decode()
    assert "exten => 150001,1," in dialplan
    assert "+442046132300" in dialplan
    assert "+442046132399" not in dialplan
    assert "exten => _.,1," in dialplan


def test_same_account_conflict_across_tenants_is_rejected(tmp_path):
    other = _assignment_row(
        TRUNK_B,
        trunk_tenant_id=TENANT_B,
        assignment_tenant_id=TENANT_B,
    )
    other["assignment_id"] = "55555555-5555-4555-8555-555555555555"

    with pytest.raises(reconcile.UnsafeInboundMappingError, match="duplicate account"):
        reconcile.build_candidate_set(
            [_assignment_row(), other],
            candidate_dir=tmp_path / "candidate",
            decrypt_password=lambda _: "secret",
            observed_at=NOW,
        )


def test_same_account_conflict_across_two_trunks_of_one_tenant_is_rejected(tmp_path):
    other = _assignment_row(TRUNK_B)
    other["assignment_id"] = "55555555-5555-4555-8555-555555555555"

    with pytest.raises(reconcile.UnsafeInboundMappingError, match="duplicate account"):
        reconcile.build_candidate_set(
            [_assignment_row(), other],
            candidate_dir=tmp_path / "candidate",
            decrypt_password=lambda _: "secret",
            observed_at=NOW,
        )


def test_duplicate_verified_account_trunk_cannot_borrow_another_trunks_assignment(
    tmp_path,
):
    second_without_assignment = _row(
        TRUNK_B,
        username="150001",
        inbound_did=None,
    )

    with pytest.raises(reconcile.UnsafeInboundMappingError, match="duplicate verified"):
        reconcile.build_candidate_set(
            [_assignment_row(), second_without_assignment],
            candidate_dir=tmp_path / "candidate",
            decrypt_password=lambda _: "secret",
            observed_at=NOW,
        )


@pytest.mark.parametrize(
    "row",
    [
        _assignment_row(status="paused"),
        _assignment_row(valid_to=NOW - timedelta(seconds=1)),
    ],
)
def test_inactive_or_stale_assignment_never_renders_an_account_route(tmp_path, row):
    row["auth_username"] = "250001"
    candidate = reconcile.build_candidate_set(
        [row],
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "secret",
        observed_at=NOW,
        verified_account_dids={},
    )

    dialplan = candidate.files["extensions.d/talky-inbound.conf"].decode()
    assert "exten => 250001,1," not in dialplan
    assert "exten => _.,1," in dialplan


def test_cross_tenant_assignment_is_rejected_even_if_database_constraints_drift(
    tmp_path,
):
    with pytest.raises(reconcile.UnsafeInboundMappingError, match="tenant conflict"):
        reconcile.build_candidate_set(
            [_assignment_row(assignment_tenant_id=TENANT_B)],
            candidate_dir=tmp_path / "candidate",
            decrypt_password=lambda _: "secret",
            observed_at=NOW,
        )


def test_unverified_phone_assignment_never_renders_an_account_route(tmp_path):
    row = _assignment_row()
    row["auth_username"] = "250001"
    row["phone_status"] = "pending"

    candidate = reconcile.build_candidate_set(
        [row],
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "secret",
        observed_at=NOW,
        verified_account_dids={},
    )

    dialplan = candidate.files[reconcile.DIALPLAN_NAME].decode()
    assert "exten => 250001,1," not in dialplan
    assert "exten => _.,1," in dialplan


def test_only_reviewed_production_account_mapping_is_in_the_inventory():
    assert reconcile.load_verified_account_dids(
        reconcile.DEFAULT_VERIFIED_ACCOUNT_MAP
    ) == {"150001": "+442046132300"}


@pytest.mark.parametrize(
    "body",
    [
        '{"150001":"+442046132300","150001":"+15551234567"}',
        '{"150001":"+442046132300"," 150001 ":"+15551234567"}',
    ],
)
def test_verified_inventory_rejects_duplicate_accounts(tmp_path, body):
    inventory = tmp_path / "inventory.json"
    inventory.write_text(body, encoding="utf-8")

    with pytest.raises(reconcile.PJSIPReconciliationError, match="duplicate"):
        reconcile.load_verified_account_dids(inventory)


def test_verified_carrier_map_conflicting_with_assignment_blocks_candidate(tmp_path):
    row = _assignment_row(did="+17789249977")
    candidate_dir = tmp_path / "candidate"

    with pytest.raises(reconcile.UnsafeInboundMappingError, match="conflicts"):
        reconcile.build_candidate_set(
            [row],
            candidate_dir=candidate_dir,
            decrypt_password=lambda _: "secret",
            observed_at=NOW,
        )
    assert not candidate_dir.exists(), (
        "a conflicted carrier map must block before a candidate can replace "
        "the working live context"
    )


def test_verified_carrier_account_without_active_assignment_blocks_candidate(tmp_path):
    row = _row(TRUNK_A, username="150001", inbound_did=None)

    with pytest.raises(reconcile.UnsafeInboundMappingError, match="lacks"):
        reconcile.build_candidate_set(
            [row],
            candidate_dir=tmp_path / "candidate",
            decrypt_password=lambda _: "secret",
            observed_at=NOW,
        )


def test_active_assignment_on_outbound_only_trunk_is_rejected(tmp_path):
    row = _assignment_row()
    row["direction"] = "outbound"

    with pytest.raises(reconcile.UnsafeInboundMappingError, match="non-inbound"):
        reconcile.build_candidate_set(
            [row],
            candidate_dir=tmp_path / "candidate",
            decrypt_password=lambda _: "secret",
            observed_at=NOW,
        )


def test_shared_carrier_host_routes_by_request_uri_not_endpoint_set_var(tmp_path):
    first = _assignment_row()
    second = _assignment_row(
        TRUNK_B,
        account="250002",
        did="+15551234567",
    )
    second["assignment_id"] = "55555555-5555-4555-8555-555555555555"

    candidate = reconcile.build_candidate_set(
        [first, second],
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "secret",
        observed_at=NOW,
        verified_account_dids={
            "150001": "+442046132300",
            "250002": "+15551234567",
        },
    )

    dialplan = candidate.files["extensions.d/talky-inbound.conf"].decode()
    assert "exten => 150001,1," in dialplan
    assert "exten => 250002,1," in dialplan
    pjsip = b"\n".join(
        content
        for name, content in candidate.files.items()
        if name.startswith("pjsip.d/")
    )
    assert b"set_var=TALKY_INBOUND_DID" not in pjsip


@pytest.mark.asyncio
async def test_fetch_joins_only_same_tenant_current_verified_assignment(monkeypatch):
    calls: dict[str, object] = {}

    class FakeConnection:
        async def fetch(self, query, *args):
            calls["query"] = " ".join(query.split())
            calls["args"] = args
            return [{"id": TRUNK_A}]

    @asynccontextmanager
    async def fake_acquire(pool, tenant_id):
        calls["pool"] = pool
        calls["tenant_id"] = tenant_id
        yield FakeConnection()

    monkeypatch.setattr(reconcile, "acquire_with_tenant", fake_acquire)
    pool = object()

    rows = await reconcile.fetch_reconciliation_rows(
        pool, platform_trunk_name="platform-default"
    )

    assert rows == [{"id": TRUNK_A}]
    assert calls["pool"] is pool
    assert calls["tenant_id"] is None
    assert "FROM tenant_sip_trunks" in calls["query"]
    assert "FROM inbound_did_assignments" in calls["query"]
    assert "JOIN tenant_phone_numbers" in calls["query"]
    assert "pn.tenant_id = a.tenant_id" in calls["query"]
    assert "a.assignment_tenant_id = st.tenant_id" in calls["query"]
    assert "a.status = 'active'" in calls["query"]
    assert "a.valid_from <= CURRENT_TIMESTAMP" in calls["query"]
    assert "pn.status = 'verified'" in calls["query"]
    assert "st.is_active = TRUE" in calls["query"]
    assert "lower(btrim(st.trunk_name)) <> lower($1)" in calls["query"]
    assert calls["args"] == ("platform-default",)


def test_missing_assignment_renders_only_fail_closed_catch_all(tmp_path):
    row = _row(TRUNK_A, inbound_did=None)

    candidate = reconcile.build_candidate_set(
        [row],
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "do-not-print-this-password",
    )

    dialplan = candidate.files[reconcile.DIALPLAN_NAME].decode()
    assert "exten => 150001,1," not in dialplan
    assert "exten => _.,1," in dialplan
    assert b"set_var=TALKY_INBOUND_DID" not in candidate.files[
        f"pjsip.d/trunk-{TRUNK_A}.conf"
    ]


def test_active_assignment_without_reviewed_carrier_map_is_not_guessed(tmp_path):
    row = _assignment_row(account="250099", did="+15551234567")

    candidate = reconcile.build_candidate_set(
        [row],
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "secret",
        observed_at=NOW,
    )

    dialplan = candidate.files[reconcile.DIALPLAN_NAME].decode()
    assert "exten => 250099,1," not in dialplan
    assert "+15551234567" not in dialplan
    assert "exten => _.,1," in dialplan


@pytest.mark.parametrize("bad_did", ["442046132300", "+150001", "+0000000000", "not-a-did"])
def test_assignment_did_must_be_strict_e164(tmp_path, bad_did):
    row = _assignment_row(did=bad_did)
    row["verified_did"] = bad_did
    with pytest.raises(reconcile.UnsafeInboundMappingError, match="invalid verified"):
        reconcile.build_candidate_set(
            [row],
            candidate_dir=tmp_path / "candidate",
            decrypt_password=lambda _: "secret",
            observed_at=NOW,
        )


def test_same_routable_numeric_identity_does_not_require_a_map(tmp_path):
    row = _row(
        TRUNK_A,
        username="442046132300",
        caller_id="+44 20 4613 2300",
        inbound_did=None,
    )

    candidate = reconcile.build_candidate_set(
        [row],
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "secret",
    )

    assert set(candidate.files) == {
        reconcile.DIALPLAN_NAME,
        f"pjsip.d/trunk-{TRUNK_A}.conf",
    }
    assert b"set_var=TALKY_INBOUND_DID" not in candidate.files[
        f"pjsip.d/trunk-{TRUNK_A}.conf"
    ]


def test_outbound_only_registration_does_not_require_an_inbound_map(tmp_path):
    candidate = reconcile.build_candidate_set(
        [_row(TRUNK_A, direction="outbound", inbound_did=None)],
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "secret",
    )

    assert set(candidate.files) == {
        reconcile.DIALPLAN_NAME,
        f"pjsip.d/trunk-{TRUNK_A}.conf",
    }


def test_candidates_are_deterministic_secured_and_digest_names_plus_content(
    tmp_path,
    chmod_calls,
):
    decrypted: list[str] = []

    def decrypt(value: str) -> str:
        decrypted.append(value)
        return {
            "encrypted-a": "plain-secret-a",
            "encrypted-b": "plain-secret-b",
        }[value]

    candidate_dir = tmp_path / "candidate"
    candidate = reconcile.build_candidate_set(
        [
            _row(TRUNK_B, direction="outbound", encrypted_password="encrypted-b"),
            _row(TRUNK_A, encrypted_password="encrypted-a"),
        ],
        candidate_dir=candidate_dir,
        decrypt_password=decrypt,
    )

    expected_names = [
        reconcile.DIALPLAN_NAME,
        f"pjsip.d/trunk-{TRUNK_A}.conf",
        f"pjsip.d/trunk-{TRUNK_B}.conf",
    ]
    assert list(candidate.files) == expected_names
    assert decrypted == ["encrypted-a", "encrypted-b"]
    assert (candidate_dir, 0o700) in chmod_calls
    for name in expected_names:
        candidate_path = candidate_dir / name
        assert candidate_path.read_bytes() == candidate.files[name]
        assert (candidate_path, 0o600) in chmod_calls
    if os.name != "nt":
        assert stat.S_IMODE(candidate_dir.stat().st_mode) == 0o700
        assert all(
            stat.S_IMODE((candidate_dir / name).stat().st_mode) == 0o600
            for name in expected_names
        )

    assert candidate.digest == reconcile.compute_candidate_digest(candidate.files)
    renamed = dict(candidate.files)
    renamed[f"pjsip.d/trunk-{TRUNK_C}.conf"] = renamed.pop(
        f"pjsip.d/trunk-{TRUNK_B}.conf"
    )
    assert reconcile.compute_candidate_digest(renamed) != candidate.digest
    changed = dict(candidate.files)
    changed[f"pjsip.d/trunk-{TRUNK_A}.conf"] += b"; one-byte-change\n"
    assert reconcile.compute_candidate_digest(changed) != candidate.digest


def test_candidate_hashes_and_secures_the_shared_base_endpoint_context(
    tmp_path,
    chmod_calls,
):
    base = tmp_path / "pjsip.conf"
    base.write_bytes(_base_pjsip())
    candidate_dir = tmp_path / "candidate"

    candidate = reconcile.build_candidate_set(
        [_row(TRUNK_A, direction="outbound")],
        candidate_dir=candidate_dir,
        decrypt_password=lambda _: "secret",
        base_pjsip_source=base,
    )

    assert candidate.files[reconcile.BASE_PJSIP_NAME] == _base_pjsip(
        "from-talky-inbound"
    ).replace(b" context = ", b" context=")
    candidate_path = candidate_dir / reconcile.BASE_PJSIP_NAME
    assert candidate_path.read_bytes() == candidate.files[reconcile.BASE_PJSIP_NAME]
    assert (candidate_path, 0o600) in chmod_calls
    assert candidate.digest == reconcile.compute_candidate_digest(candidate.files)


def test_safe_drift_summary_names_files_but_never_exposes_content(tmp_path):
    secret = "candidate-password-that-must-not-leak"
    candidate = reconcile.build_candidate_set(
        [
            _assignment_row(),
            _row(TRUNK_B, direction="outbound"),
        ],
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: secret,
    )
    live = tmp_path / "live"
    live.mkdir()
    (live / f"trunk-{TRUNK_A}.conf").write_text("password=old-live-secret\n", encoding="utf-8")
    (live / f"trunk-{TRUNK_C}.conf").write_text("password=unexpected-secret\n", encoding="utf-8")

    summary = reconcile.compare_live(candidate, live_dir=live)
    safe_json = json.dumps(summary.to_safe_dict(), sort_keys=True)

    assert summary.changed == (f"pjsip.d/trunk-{TRUNK_A}.conf",)
    assert summary.missing == (
        reconcile.DIALPLAN_NAME,
        f"pjsip.d/trunk-{TRUNK_B}.conf",
    )
    assert summary.unexpected == (f"pjsip.d/trunk-{TRUNK_C}.conf",)
    assert secret not in safe_json
    assert "old-live-secret" not in safe_json
    assert "unexpected-secret" not in safe_json
    assert "150001" not in safe_json
    assert "+442046132300" not in safe_json


def _runner(
    candidate=None,
    *,
    active_channels: int = 0,
    pjsip_reload_results: list[int] | None = None,
    dialplan_reload_results: list[int] | None = None,
):
    commands: list[str] = []
    pjsip_results = list(pjsip_reload_results or [0])
    dialplan_results = list(dialplan_reload_results or [0])

    def run(command: str) -> reconcile.CommandResult:
        commands.append(command)
        if command == "core show channels count":
            return reconcile.CommandResult(0, f"{active_channels} active channels\n", "")
        if command == "pjsip reload":
            rc = pjsip_results.pop(0) if pjsip_results else 0
            return reconcile.CommandResult(
                rc,
                "Module 'res_pjsip.so' reloaded successfully." if rc == 0 else "",
                "reload failed" if rc else "",
            )
        if command == "dialplan reload":
            rc = dialplan_results.pop(0) if dialplan_results else 0
            return reconcile.CommandResult(
                rc,
                "Dialplan reloaded." if rc == 0 else "",
                "reload failed" if rc else "",
            )
        if command == "pjsip show endpoint blazedigitel-endpoint":
            return reconcile.CommandResult(
                0,
                "Endpoint: blazedigitel-endpoint Context: from-talky-inbound",
                "",
            )
        if command.startswith("pjsip show endpoint trunk-"):
            endpoint = command.removeprefix("pjsip show endpoint ")
            return reconcile.CommandResult(
                0,
                f"Endpoint: {endpoint} Context: from-talky-inbound",
                "",
            )
        if command == "dialplan show from-talky-inbound":
            routes = ""
            if candidate is not None:
                routes = " ".join(
                    f"{route.account} {route.did}" for route in candidate.routes
                )
            return reconcile.CommandResult(
                0,
                f"Context from-talky-inbound _. Stasis {routes}",
                "",
            )
        raise AssertionError(f"unexpected Asterisk command: {command}")

    return run, commands


def test_apply_requires_an_exact_expected_digest_before_touching_asterisk(tmp_path):
    candidate = reconcile.build_candidate_set(
        [_row(TRUNK_A)],
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "secret",
    )
    runner, commands = _runner(candidate)

    with pytest.raises(reconcile.CandidateDigestMismatch):
        reconcile.apply_candidate_set(
            candidate,
            live_dir=tmp_path / "live",
            expected_digest="0" * 64,
            lock_path=tmp_path / "apply.lock",
            run_asterisk=runner,
        )

    assert commands == []
    assert not (tmp_path / "live").exists()


def test_apply_fails_closed_when_asterisk_has_active_channels(tmp_path):
    candidate = reconcile.build_candidate_set(
        [_row(TRUNK_A)],
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "secret",
    )
    live = tmp_path / "live"
    live.mkdir()
    original = b"original-live-content\n"
    (live / f"trunk-{TRUNK_A}.conf").write_bytes(original)
    runner, commands = _runner(candidate, active_channels=1)

    with pytest.raises(reconcile.ActiveChannelsError, match="1 active"):
        reconcile.apply_candidate_set(
            candidate,
            live_dir=live,
            expected_digest=candidate.digest,
            lock_path=tmp_path / "apply.lock",
            run_asterisk=runner,
        )

    assert commands == ["core show channels count"]
    assert (live / f"trunk-{TRUNK_A}.conf").read_bytes() == original


def test_apply_fails_closed_when_channel_count_cannot_be_proven(tmp_path):
    candidate = reconcile.build_candidate_set(
        [_row(TRUNK_A)],
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "secret",
    )

    def runner(command: str) -> reconcile.CommandResult:
        assert command == "core show channels count"
        return reconcile.CommandResult(0, "Asterisk is fully booted", "")

    with pytest.raises(reconcile.ActiveChannelsError, match="could not prove"):
        reconcile.apply_candidate_set(
            candidate,
            live_dir=tmp_path / "live",
            expected_digest=candidate.digest,
            lock_path=tmp_path / "apply.lock",
            run_asterisk=runner,
        )


def test_external_base_edit_after_candidate_build_is_not_overwritten(tmp_path):
    base = tmp_path / "pjsip.conf"
    base.write_bytes(_base_pjsip())
    candidate = reconcile.build_candidate_set(
        [_row(TRUNK_A, direction="outbound")],
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "secret",
        base_pjsip_source=base,
    )
    external_edit = _base_pjsip().replace(b"disallow=all", b"disallow=ulaw")
    commands: list[str] = []

    def runner(command: str) -> reconcile.CommandResult:
        commands.append(command)
        if command == "core show channels count":
            base.write_bytes(external_edit)
            return reconcile.CommandResult(0, "0 active channels\n", "")
        raise AssertionError(f"mutation should have been blocked before {command}")

    with pytest.raises(reconcile.CandidateDigestMismatch, match="base PJSIP"):
        reconcile.apply_candidate_set(
            candidate,
            live_dir=tmp_path / "pjsip.d",
            base_pjsip_live_path=base,
            expected_digest=candidate.digest,
            lock_path=tmp_path / "apply.lock",
            run_asterisk=runner,
        )

    assert base.read_bytes() == external_edit
    assert commands == ["core show channels count"]


def test_reload_failure_restores_every_original_file_and_reloads_rollback(tmp_path):
    candidate = reconcile.build_candidate_set(
        [
            _row(TRUNK_A),
            _row(TRUNK_B, direction="outbound"),
        ],
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "new-secret",
    )
    live = tmp_path / "live"
    live.mkdir()
    original = {
        f"trunk-{TRUNK_A}.conf": b"password=old-a\n",
        f"trunk-{TRUNK_C}.conf": b"password=old-unexpected\n",
    }
    for name, content in original.items():
        (live / name).write_bytes(content)
    runner, commands = _runner(candidate, pjsip_reload_results=[1, 0])

    with pytest.raises(reconcile.PJSIPReloadFailed, match="restored"):
        reconcile.apply_candidate_set(
            candidate,
            live_dir=live,
            expected_digest=candidate.digest,
            lock_path=tmp_path / "apply.lock",
            run_asterisk=runner,
        )

    restored = {path.name: path.read_bytes() for path in live.glob("trunk-*.conf")}
    assert restored == original
    assert not (live.parent / "extensions.d" / "talky-inbound.conf").exists()
    assert commands == [
        "core show channels count",
        "pjsip reload",
        "pjsip reload",
        "dialplan reload",
    ]
    assert not list(live.glob(".*.tmp"))


def test_reload_failure_restores_shared_base_pjsip_in_same_transaction(tmp_path):
    base = tmp_path / "pjsip.conf"
    original_base = _base_pjsip()
    base.write_bytes(original_base)
    candidate = reconcile.build_candidate_set(
        [_row(TRUNK_A, direction="outbound")],
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "new-secret",
        base_pjsip_source=base,
    )
    live = tmp_path / "pjsip.d"
    live.mkdir()
    inner_runner, _commands = _runner(candidate, pjsip_reload_results=[1, 0])
    reload_attempts = 0

    def runner(command: str) -> reconcile.CommandResult:
        nonlocal reload_attempts
        if command == "pjsip reload":
            expected = (
                candidate.files[reconcile.BASE_PJSIP_NAME]
                if reload_attempts == 0
                else original_base
            )
            assert base.read_bytes() == expected
            reload_attempts += 1
        return inner_runner(command)

    with pytest.raises(reconcile.PJSIPReloadFailed, match="restored"):
        reconcile.apply_candidate_set(
            candidate,
            live_dir=live,
            base_pjsip_live_path=base,
            expected_digest=candidate.digest,
            lock_path=tmp_path / "apply.lock",
            run_asterisk=runner,
        )

    assert base.read_bytes() == original_base
    assert reload_attempts == 2
    assert not list(tmp_path.glob(".pjsip.conf.*.tmp"))


def test_runtime_context_proof_failure_restores_prior_files(tmp_path):
    candidate = reconcile.build_candidate_set(
        [_row(TRUNK_A, direction="outbound")],
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "new-secret",
    )
    live = tmp_path / "live"
    live.mkdir()
    original = b"original-live-content\n"
    (live / f"trunk-{TRUNK_A}.conf").write_bytes(original)
    normal_runner, commands = _runner(candidate)

    def missing_context(command: str) -> reconcile.CommandResult:
        if command == "pjsip show endpoint blazedigitel-endpoint":
            commands.append(command)
            return reconcile.CommandResult(
                0,
                "Endpoint: blazedigitel-endpoint Context: from-blazedigitel",
                "",
            )
        return normal_runner(command)

    with pytest.raises(reconcile.PJSIPReloadFailed, match="restored"):
        reconcile.apply_candidate_set(
            candidate,
            live_dir=live,
            expected_digest=candidate.digest,
            lock_path=tmp_path / "apply.lock",
            run_asterisk=missing_context,
        )

    assert (live / f"trunk-{TRUNK_A}.conf").read_bytes() == original
    assert not (live.parent / "extensions.d" / "talky-inbound.conf").exists()
    assert commands[-2:] == ["pjsip reload", "dialplan reload"]


def test_successful_apply_exactly_reconciles_managed_files_atomically(
    tmp_path,
    chmod_calls,
):
    candidate = reconcile.build_candidate_set(
        [
            _row(TRUNK_A),
            _row(TRUNK_B, direction="outbound"),
        ],
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "new-secret",
    )
    live = tmp_path / "live"
    live.mkdir()
    (live / f"trunk-{TRUNK_A}.conf").write_text("old", encoding="utf-8")
    (live / f"trunk-{TRUNK_C}.conf").write_text("unexpected", encoding="utf-8")
    runner, commands = _runner(candidate)

    result = reconcile.apply_candidate_set(
        candidate,
        live_dir=live,
        expected_digest=candidate.digest,
        lock_path=tmp_path / "apply.lock",
        run_asterisk=runner,
    )

    actual = {
        f"pjsip.d/{path.name}": path.read_bytes()
        for path in live.glob("trunk-*.conf")
    }
    dialplan_path = live.parent / "extensions.d" / "talky-inbound.conf"
    actual[reconcile.DIALPLAN_NAME] = dialplan_path.read_bytes()
    assert actual == dict(candidate.files)
    assert all(
        (live / name.removeprefix("pjsip.d/"), 0o640) in chmod_calls
        for name in actual
        if name.startswith("pjsip.d/")
    )
    assert (dialplan_path, 0o644) in chmod_calls
    if os.name != "nt":
        assert all(
            stat.S_IMODE((live / name.removeprefix("pjsip.d/")).stat().st_mode)
            == 0o640
            for name in actual
            if name.startswith("pjsip.d/")
        )
        assert stat.S_IMODE(dialplan_path.stat().st_mode) == 0o644
    assert result.changed == (f"pjsip.d/trunk-{TRUNK_A}.conf",)
    assert result.missing == (
        reconcile.DIALPLAN_NAME,
        f"pjsip.d/trunk-{TRUNK_B}.conf",
    )
    assert result.unexpected == (f"pjsip.d/trunk-{TRUNK_C}.conf",)
    assert commands == [
        "core show channels count",
        "pjsip reload",
        "dialplan reload",
        "pjsip show endpoint blazedigitel-endpoint",
        f"pjsip show endpoint trunk-{TRUNK_A}",
        f"pjsip show endpoint trunk-{TRUNK_B}",
        "dialplan show from-talky-inbound",
    ]


def test_no_drift_apply_still_reloads_and_proves_runtime_after_possible_crash(tmp_path):
    candidate = reconcile.build_candidate_set(
        [_row(TRUNK_A, direction="outbound")],
        candidate_dir=tmp_path / "candidate",
        decrypt_password=lambda _: "secret",
    )
    live = tmp_path / "live"
    live.mkdir()
    (live / f"trunk-{TRUNK_A}.conf").write_bytes(
        candidate.files[f"pjsip.d/trunk-{TRUNK_A}.conf"]
    )
    dialplan = live.parent / "extensions.d" / "talky-inbound.conf"
    dialplan.parent.mkdir()
    dialplan.write_bytes(candidate.files[reconcile.DIALPLAN_NAME])
    runner, commands = _runner(candidate)

    result = reconcile.apply_candidate_set(
        candidate,
        live_dir=live,
        expected_digest=candidate.digest,
        lock_path=tmp_path / "apply.lock",
        run_asterisk=runner,
    )

    assert result.drifted is False
    assert commands == [
        "core show channels count",
        "pjsip reload",
        "dialplan reload",
        "pjsip show endpoint blazedigitel-endpoint",
        f"pjsip show endpoint trunk-{TRUNK_A}",
        "dialplan show from-talky-inbound",
    ]


def test_apply_lock_fails_fast_for_a_second_reconciler(tmp_path, chmod_calls):
    lock_path = tmp_path / "apply.lock"

    with reconcile.exclusive_apply_lock(lock_path):
        with pytest.raises(reconcile.ReconciliationLocked):
            with reconcile.exclusive_apply_lock(lock_path):
                pass

    assert (lock_path, 0o600) in chmod_calls
    if os.name != "nt":
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


def test_cli_is_check_only_by_default_and_apply_requires_digest():
    check = reconcile.parse_args([])
    assert check.apply is False
    assert check.expected_digest is None

    with pytest.raises(SystemExit):
        reconcile.parse_args(["--apply"])


def test_cli_can_be_executed_by_path_from_backend_directory():
    backend_root = Path(__file__).resolve().parents[2]
    script = backend_root / "scripts" / "reconcile_pjsip_configs.py"

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=backend_root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--expected-digest" in result.stdout
