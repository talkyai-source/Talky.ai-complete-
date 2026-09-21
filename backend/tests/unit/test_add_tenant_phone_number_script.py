"""The ops tool that registers a number on a tenant.

It goes through TenantPhoneNumberService rather than writing the table, so it
inherits the normaliser, the idempotency on (tenant_id, e164), the
platform-admin check on verification and the audit trail. These tests pin the
argument contract and the guard that stops an unaudited verification.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "add_tenant_phone_number.py"
)
_spec = importlib.util.spec_from_file_location("add_tenant_phone_number", _SCRIPT)
addnum = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(addnum)


def test_verifying_without_a_proof_reference_is_refused():
    # Verification is a caller-ID attestation. It must never happen without
    # something recorded that says who asked and why.
    with pytest.raises(SystemExit):
        addnum.main([
            "--tenant-id", "t", "--e164", "+442046132300", "--actor-user-id", "u",
        ])


def test_register_only_needs_no_proof_reference(monkeypatch):
    seen = {}

    async def _fake_run(args):
        seen["no_verify"] = args.no_verify
        seen["e164"] = args.e164
        return 0

    monkeypatch.setattr(addnum, "_run", _fake_run)
    rc = addnum.main([
        "--tenant-id", "t", "--e164", "+442046132300",
        "--actor-user-id", "u", "--no-verify",
    ])
    assert rc == 0
    assert seen == {"no_verify": True, "e164": "+442046132300"}


def test_tenant_and_number_and_actor_are_all_required():
    for argv in (
        ["--e164", "+1", "--actor-user-id", "u", "--no-verify"],
        ["--tenant-id", "t", "--actor-user-id", "u", "--no-verify"],
        ["--tenant-id", "t", "--e164", "+1", "--no-verify"],
    ):
        with pytest.raises(SystemExit):
            addnum.main(argv)


def test_it_never_writes_the_table_directly():
    # Guard: the whole point is to inherit the service's rules. An INSERT or
    # UPDATE here would bypass the normaliser and the audit trail.
    source = _SCRIPT.read_text(encoding="utf-8")
    body = source.split('"""', 2)[-1]  # drop the module docstring
    for banned in ("INSERT INTO", "UPDATE tenant_phone_numbers", "DELETE FROM"):
        assert banned not in body, banned


def test_it_states_that_registering_does_not_reroute_the_carrier():
    # The trap this tool exists next to: a number registered on an account does
    # not change where the carrier delivers its calls.
    source = _SCRIPT.read_text(encoding="utf-8")
    assert "does not" in source and "carrier" in source
