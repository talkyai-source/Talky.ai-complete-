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
