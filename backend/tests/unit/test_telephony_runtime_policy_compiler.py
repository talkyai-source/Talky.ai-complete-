from __future__ import annotations

from copy import deepcopy

import pytest

from app.domain.services.telephony_runtime_policy import (
    PolicyCompilationError,
    compile_tenant_runtime_policy,
)


def _sample_snapshot():
    trunks = [
        {
            "id": "00000000-0000-0000-0000-000000000101",
            "trunk_name": "primary-outbound",
            "sip_domain": "sip1.example.com",
            "port": 5060,
            "transport": "udp",
            "direction": "both",
            "is_active": True,
            "metadata": {},
        }
    ]
    codecs = [
        {
            "id": "00000000-0000-0000-0000-000000000201",
            "policy_name": "default-codec",
            "allowed_codecs": ["PCMU", "PCMA"],
            "preferred_codec": "PCMU",
            "sample_rate_hz": 8000,
            "ptime_ms": 20,
            "max_bitrate_kbps": None,
            "jitter_buffer_ms": 60,
            "is_active": True,
            "metadata": {},
        }
    ]
    routes = [
        {
            "id": "00000000-0000-0000-0000-000000000301",
            "policy_name": "outbound-default",
            "route_type": "outbound",
            "priority": 100,
            "match_pattern": r"^\+?[1-9]\d{7,14}$",
            "target_trunk_id": "00000000-0000-0000-0000-000000000101",
            "codec_policy_id": "00000000-0000-0000-0000-000000000201",
            "strip_digits": 0,
            "prepend_digits": None,
            "is_active": True,
            "metadata": {},
        }
    ]
    trust_policies = [
        {
            "id": "00000000-0000-0000-0000-000000000401",
            "policy_name": "edge-allow",
            "allowed_source_cidrs": ["10.0.0.0/24"],
            "blocked_source_cidrs": ["10.0.0.99/32"],
            "kamailio_group": 1,
            "priority": 100,
            "is_active": True,
            "metadata": {},
        }
    ]
    return trunks, codecs, routes, trust_policies


def test_compiler_is_deterministic_for_identical_input():
    trunks, codecs, routes, trust_policies = _sample_snapshot()
    result_a = compile_tenant_runtime_policy(
        tenant_id="00000000-0000-0000-0000-000000000001",
        trunks=deepcopy(trunks),
        codec_policies=deepcopy(codecs),
        route_policies=deepcopy(routes),
        trust_policies=deepcopy(trust_policies),
    )
    result_b = compile_tenant_runtime_policy(
        tenant_id="00000000-0000-0000-0000-000000000001",
        trunks=deepcopy(trunks),
        codec_policies=deepcopy(codecs),
        route_policies=deepcopy(routes),
        trust_policies=deepcopy(trust_policies),
    )
    assert result_a.source_hash == result_b.source_hash
    assert result_a.artifact == result_b.artifact
    assert result_a.input_snapshot == result_b.input_snapshot


def test_compiler_rejects_missing_target_trunk():
    trunks, codecs, routes, trust_policies = _sample_snapshot()
    routes[0]["target_trunk_id"] = "00000000-0000-0000-0000-000000009999"
    with pytest.raises(PolicyCompilationError) as exc:
        compile_tenant_runtime_policy(
            tenant_id="00000000-0000-0000-0000-000000000001",
            trunks=trunks,
            codec_policies=codecs,
            route_policies=routes,
            trust_policies=trust_policies,
        )
    codes = {issue.code for issue in exc.value.issues}
    assert "missing_target_trunk" in codes


def test_compiler_rejects_invalid_regex():
    trunks, codecs, routes, trust_policies = _sample_snapshot()
    routes[0]["match_pattern"] = r"(unclosed"
    with pytest.raises(PolicyCompilationError) as exc:
        compile_tenant_runtime_policy(
            tenant_id="00000000-0000-0000-0000-000000000001",
            trunks=trunks,
            codec_policies=codecs,
            route_policies=routes,
            trust_policies=trust_policies,
        )
    codes = {issue.code for issue in exc.value.issues}
    assert "invalid_regex" in codes


def test_compiler_rejects_route_direction_mismatch():
    trunks, codecs, routes, trust_policies = _sample_snapshot()
    trunks[0]["direction"] = "inbound"
    with pytest.raises(PolicyCompilationError) as exc:
        compile_tenant_runtime_policy(
            tenant_id="00000000-0000-0000-0000-000000000001",
            trunks=trunks,
            codec_policies=codecs,
            route_policies=routes,
            trust_policies=trust_policies,
        )
    codes = {issue.code for issue in exc.value.issues}
    assert "trunk_direction_mismatch" in codes


def test_compiler_emits_trunk_sip_options_with_defaults():
    """A trunk with no advanced metadata still yields complete, default
    sip_options in the dispatcher entry (deterministic artifact)."""
    trunks, codecs, routes, trust_policies = _sample_snapshot()
    compiled = compile_tenant_runtime_policy(
        tenant_id="00000000-0000-0000-0000-000000000001",
        trunks=trunks,
        codec_policies=codecs,
        route_policies=routes,
        trust_policies=trust_policies,
    )
    entry = compiled.artifact["opensips"]["dispatcher"]["sets"]["1"][0]
    assert entry["sip_options"] == {
        "dtmf_mode": "rfc2833",
        "srtp": False,
        "register": False,
    }


def test_compiler_propagates_trunk_advanced_metadata():
    """caller_id / DTMF / SRTP / registration / proxy set on the trunk land in
    both the OpenSIPS dispatcher options and the FreeSWITCH dialplan."""
    trunks, codecs, routes, trust_policies = _sample_snapshot()
    trunks[0]["metadata"] = {
        "caller_id": "+15551230000",
        "outbound_proxy": "proxy.example.com:5060",
        "auth_realm": "sip.example.com",
        "register": True,
        "register_interval": 1800,
        "dtmf_mode": "sip-info",
        "srtp": True,
    }
    compiled = compile_tenant_runtime_policy(
        tenant_id="00000000-0000-0000-0000-000000000001",
        trunks=trunks,
        codec_policies=codecs,
        route_policies=routes,
        trust_policies=trust_policies,
    )

    # OpenSIPS dispatcher carries the structured options.
    opts = compiled.artifact["opensips"]["dispatcher"]["sets"]["1"][0]["sip_options"]
    assert opts["caller_id"] == "+15551230000"
    assert opts["outbound_proxy"] == "proxy.example.com:5060"
    assert opts["auth_realm"] == "sip.example.com"
    assert opts["register"] is True
    assert opts["register_interval"] == 1800
    assert opts["dtmf_mode"] == "sip-info"
    assert opts["srtp"] is True

    # FreeSWITCH dialplan applies caller ID, SRTP, and proxy routing.
    xml = compiled.artifact["freeswitch"]["xml_curl"]["dialplan_xml"]
    assert "effective_caller_id_number=+15551230000" in xml
    assert "rtp_secure_media=mandatory" in xml
    assert "fs_path=sip:proxy.example.com:5060" in xml


def test_compiler_omits_register_interval_when_not_registering():
    trunks, codecs, routes, trust_policies = _sample_snapshot()
    trunks[0]["metadata"] = {"register": False, "register_interval": 1800}
    compiled = compile_tenant_runtime_policy(
        tenant_id="00000000-0000-0000-0000-000000000001",
        trunks=trunks,
        codec_policies=codecs,
        route_policies=routes,
        trust_policies=trust_policies,
    )
    opts = compiled.artifact["opensips"]["dispatcher"]["sets"]["1"][0]["sip_options"]
    assert opts["register"] is False
    assert "register_interval" not in opts


def test_compiler_maps_trust_policies_into_kamailio_permissions():
    trunks, codecs, routes, trust_policies = _sample_snapshot()
    compiled = compile_tenant_runtime_policy(
        tenant_id="00000000-0000-0000-0000-000000000001",
        trunks=trunks,
        codec_policies=codecs,
        route_policies=routes,
        trust_policies=trust_policies,
    )
    rules = compiled.artifact["kamailio"]["permissions"]["rules"]
    assert len(rules) == 1
    assert rules[0]["policy_name"] == "edge-allow"
    assert rules[0]["group"] == 1
    assert rules[0]["allow"] == ["10.0.0.0/24"]
