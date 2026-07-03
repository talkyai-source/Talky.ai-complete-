"""Unit tests for the namespaced per-tenant PJSIP config generator (Phase B).

Offline only — the pure render, the atomic file write/remove, the row→
projection mapping, and the secrets-safety guarantees (decrypted password
lands in the file but never in logs; newline injection is rejected). Nothing
here executes Asterisk.
"""
from __future__ import annotations

import logging
import os
import stat

import pytest

from app.infrastructure.telephony.pjsip_config_generator import (
    TrunkConfigInput,
    apply_trunk_config,
    build_trunk_config_input,
    remove_trunk_config,
    render_trunk_conf,
    trunk_conf_path,
    write_trunk_file,
)

TRUNK_ID = "abc-123"
TENANT_ID = "tenant-777"


def _input(**over):
    base = dict(
        trunk_id=TRUNK_ID,
        tenant_id=TENANT_ID,
        trunk_name="acme-byo",
        sip_domain="sip.acme.example",
        port=5060,
        transport="udp",
        auth_username="acmeuser",
        auth_password="s3cr3t-pass",
    )
    base.update(over)
    return TrunkConfigInput(**base)


# --- pure render -------------------------------------------------------

def test_render_produces_namespaced_objects():
    conf = render_trunk_conf(_input())
    assert f"[trunk-{TRUNK_ID}]" in conf
    assert f"[trunk-{TRUNK_ID}-auth]" in conf
    assert f"[trunk-{TRUNK_ID}-aor]" in conf
    assert f"[trunk-{TRUNK_ID}-identify]" in conf
    # endpoint wiring
    assert "type=endpoint" in conf
    assert f"context=from-tenant-{TENANT_ID}" in conf
    assert f"outbound_auth=trunk-{TRUNK_ID}-auth" in conf
    assert f"aors=trunk-{TRUNK_ID}-aor" in conf
    assert "transport=transport-udp" in conf
    # aor contact + identify match default to the sip_domain
    assert "contact=sip:sip.acme.example:5060" in conf
    assert "match=sip.acme.example" in conf
    assert f"endpoint=trunk-{TRUNK_ID}" in conf


def test_render_includes_decrypted_password_in_auth_section():
    conf = render_trunk_conf(_input(auth_password="my-plain-pw"))
    assert "password=my-plain-pw" in conf
    assert "username=acmeuser" in conf


def test_render_without_register_omits_registration_object():
    conf = render_trunk_conf(_input(register=False))
    assert f"[trunk-{TRUNK_ID}-reg]" not in conf
    assert "type=registration" not in conf


def test_render_with_register_includes_registration_object():
    conf = render_trunk_conf(_input(register=True, register_interval=1800))
    assert f"[trunk-{TRUNK_ID}-reg]" in conf
    assert "type=registration" in conf
    assert "server_uri=sip:sip.acme.example:5060" in conf
    assert "client_uri=sip:acmeuser@sip.acme.example" in conf
    assert "retry_interval=1800" in conf


def test_render_register_block_has_resilience_params():
    # telephony-audit #4: a BYO registration must self-heal, not die on a
    # >10-retry outage or a single 403.
    conf = render_trunk_conf(_input(register=True, register_interval=1800))
    assert "auth_rejection_permanent=no" in conf
    assert "max_retries=10000" in conf
    assert "fatal_retry_interval=30" in conf
    assert "forbidden_retry_interval=60" in conf
    # retry_interval + expiration still track the configured interval.
    assert "retry_interval=1800" in conf
    assert "expiration=1800" in conf


def test_render_caller_id_and_source_host_overrides():
    conf = render_trunk_conf(
        _input(caller_id="+15551234567", source_host="203.0.113.9")
    )
    assert "callerid=<+15551234567>" in conf
    assert "match=203.0.113.9" in conf  # identify uses the explicit source host


def test_render_dtmf_rfc2833_maps_to_valid_pjsip_rfc4733():
    # PJSIP rejects the legacy chan_sip name 'rfc2833'; an unmapped value fails
    # the whole endpoint ("Error parsing dtmf_mode=rfc2833"). Must become rfc4733.
    conf = render_trunk_conf(_input(dtmf_mode="rfc2833"))
    assert "dtmf_mode=rfc4733" in conf
    assert "dtmf_mode=rfc2833" not in conf


def test_render_drops_unknown_dtmf_mode_rather_than_break_endpoint():
    conf = render_trunk_conf(_input(dtmf_mode="bogus"))
    assert "dtmf_mode=" not in conf


def test_render_auth_has_default_realm():
    # Mirrors the working primary (realm=asterisk); a missing/wrong realm 403s.
    conf = render_trunk_conf(_input())
    assert "realm=asterisk" in conf


def test_render_auth_realm_override():
    conf = render_trunk_conf(_input(auth_realm="sip.acme.example"))
    assert "realm=sip.acme.example" in conf


def test_render_endpoint_has_nat_traversal_settings():
    conf = render_trunk_conf(_input())
    assert "rtp_symmetric=yes" in conf
    assert "force_rport=yes" in conf
    assert "rewrite_contact=yes" in conf


def test_render_registers_the_number_not_login_when_caller_id_set():
    # Mirror the primary: register the DID/number as the identity, auth with the login.
    conf = render_trunk_conf(_input(register=True, caller_id="+442046132300"))
    assert "client_uri=sip:+442046132300@sip.acme.example" in conf
    assert "contact_user=+442046132300" in conf
    assert "from_user=+442046132300" in conf


def test_render_falls_back_to_login_identity_without_caller_id():
    conf = render_trunk_conf(_input(register=True))
    assert "client_uri=sip:acmeuser@sip.acme.example" in conf
    assert "contact_user=acmeuser" in conf


def test_render_without_auth_omits_auth_and_outbound_auth():
    conf = render_trunk_conf(_input(auth_username=None, auth_password=None))
    assert f"[trunk-{TRUNK_ID}-auth]" not in conf
    assert "outbound_auth=" not in conf
    # aor + identify still rendered
    assert f"[trunk-{TRUNK_ID}-aor]" in conf
    assert f"[trunk-{TRUNK_ID}-identify]" in conf


def test_render_rejects_newline_injection_in_password():
    with pytest.raises(ValueError):
        render_trunk_conf(_input(auth_password="pw\n[evil]\ntype=endpoint"))


def test_render_rejects_bad_transport():
    with pytest.raises(ValueError):
        render_trunk_conf(_input(transport="sctp"))


# --- row → projection mapping ------------------------------------------

def test_build_input_pulls_metadata_fields():
    row = {
        "id": TRUNK_ID,
        "tenant_id": TENANT_ID,
        "trunk_name": "acme-byo",
        "sip_domain": "sip.acme.example",
        "port": 5061,
        "transport": "tcp",
        "auth_username": "u",
        "metadata": {
            "caller_id": "+15550000000",
            "register": True,
            "register_interval": 900,
            "dtmf_mode": "rfc2833",
            "source_host": "198.51.100.5",
        },
    }
    inp = build_trunk_config_input(row, decrypted_password="pw")
    assert inp.caller_id == "+15550000000"
    assert inp.register is True
    assert inp.register_interval == 900
    assert inp.dtmf_mode == "rfc2833"
    assert inp.source_host == "198.51.100.5"
    assert inp.auth_password == "pw"


def test_build_input_tolerates_json_string_metadata():
    row = {
        "id": TRUNK_ID, "tenant_id": TENANT_ID, "trunk_name": "b",
        "sip_domain": "h", "port": 5060, "transport": "udp",
        "auth_username": "u",
        "metadata": '{"caller_id": "+1999"}',
    }
    inp = build_trunk_config_input(row, decrypted_password="pw")
    assert inp.caller_id == "+1999"


# --- file apply / remove (atomic) --------------------------------------

def test_write_then_remove_file(tmp_path):
    conf = render_trunk_conf(_input())
    path = write_trunk_file(TRUNK_ID, conf, base_dir=tmp_path)
    assert path.exists()
    assert path == trunk_conf_path(TRUNK_ID, base_dir=tmp_path)
    assert "type=endpoint" in path.read_text(encoding="utf-8")

    from app.infrastructure.telephony.pjsip_config_generator import remove_trunk_file
    assert remove_trunk_file(TRUNK_ID, base_dir=tmp_path) is True
    assert not path.exists()
    # idempotent second remove
    assert remove_trunk_file(TRUNK_ID, base_dir=tmp_path) is False


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes only")
def test_write_file_mode_is_0640_group_readable(tmp_path):
    # asterisk (group member, not owner) must be able to read the file, so
    # the mode is 0640 — NOT 0600 (which asterisk can't read → trunk silently
    # never loads). Proven live on the box.
    path = write_trunk_file(TRUNK_ID, "x", base_dir=tmp_path)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o640


def test_write_is_full_file_render_not_append(tmp_path):
    write_trunk_file(TRUNK_ID, "FIRST", base_dir=tmp_path)
    write_trunk_file(TRUNK_ID, "SECOND", base_dir=tmp_path)
    content = trunk_conf_path(TRUNK_ID, base_dir=tmp_path).read_text(encoding="utf-8")
    assert content == "SECOND"
    assert "FIRST" not in content


@pytest.mark.asyncio
async def test_apply_writes_file_without_reload_and_never_logs_password(tmp_path, caplog):
    row = {
        "id": TRUNK_ID, "tenant_id": TENANT_ID, "trunk_name": "acme-byo",
        "sip_domain": "sip.acme.example", "port": 5060, "transport": "udp",
        "auth_username": "acmeuser", "metadata": {"register": True},
    }
    secret = "TOP-SECRET-PW-9182"
    with caplog.at_level(logging.DEBUG):
        path = await apply_trunk_config(
            row, decrypted_password=secret, base_dir=tmp_path, reload=False,
        )
    # file contains the secret ...
    assert secret in path.read_text(encoding="utf-8")
    # ... but no log record does.
    assert all(secret not in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_remove_trunk_config_removes_file(tmp_path):
    write_trunk_file(TRUNK_ID, "x", base_dir=tmp_path)
    removed = await remove_trunk_config(TRUNK_ID, base_dir=tmp_path, reload=False)
    assert removed is True
    assert not trunk_conf_path(TRUNK_ID, base_dir=tmp_path).exists()


@pytest.mark.asyncio
async def test_apply_decrypts_via_mock_and_writes(tmp_path):
    # Simulate the trunks.py hook path: encryption service decrypt is mocked,
    # its plaintext output must reach the file and nowhere else.
    class _FakeEnc:
        def decrypt(self, blob):
            assert blob == "ENC(blob)"
            return "decrypted-pw-abc"

    row = {
        "id": TRUNK_ID, "tenant_id": TENANT_ID, "trunk_name": "acme-byo",
        "sip_domain": "sip.acme.example", "port": 5060, "transport": "udp",
        "auth_username": "acmeuser", "metadata": {},
    }
    plaintext = _FakeEnc().decrypt("ENC(blob)")
    path = await apply_trunk_config(
        row, decrypted_password=plaintext, base_dir=tmp_path, reload=False,
    )
    assert "password=decrypted-pw-abc" in path.read_text(encoding="utf-8")
