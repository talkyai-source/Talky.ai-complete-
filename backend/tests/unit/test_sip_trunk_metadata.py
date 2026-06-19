"""Unit tests for the advanced SIP-trunk metadata validation/normalisation.

These exercise the pure ``normalize_trunk_metadata`` helper that backs the
caller-ID + advanced options (DTMF, registration, SRTP, outbound proxy) the
settings UI writes into the trunk's free-form ``metadata`` JSON.
"""
from __future__ import annotations

import pytest

from app.api.v1.endpoints.telephony_sip.schemas import (
    SIPTrunkCreateRequest,
    SIPTrunkUpdateRequest,
    normalize_trunk_metadata,
)


def test_none_and_empty_metadata_normalise_to_empty_dict():
    assert normalize_trunk_metadata(None) == {}
    assert normalize_trunk_metadata({}) == {}


def test_valid_full_metadata_passes_through():
    meta = {
        "caller_id": " +15551234567 ",
        "outbound_proxy": " proxy.example.com:5060 ",
        "auth_realm": "sip.example.com",
        "register": True,
        "register_interval": 1800,
        "dtmf_mode": "rfc2833",
        "srtp": True,
    }
    out = normalize_trunk_metadata(meta)
    # Strings are stripped.
    assert out["caller_id"] == "+15551234567"
    assert out["outbound_proxy"] == "proxy.example.com:5060"
    assert out["register"] is True
    assert out["register_interval"] == 1800
    assert out["dtmf_mode"] == "rfc2833"
    assert out["srtp"] is True


def test_unknown_keys_are_preserved():
    out = normalize_trunk_metadata({"caller_id": "+15551230000", "some_other_feature": {"x": 1}})
    assert out["some_other_feature"] == {"x": 1}


def test_blank_optional_strings_are_dropped():
    out = normalize_trunk_metadata({"caller_id": "   ", "outbound_proxy": "", "auth_realm": None})
    assert "caller_id" not in out
    assert "outbound_proxy" not in out
    assert "auth_realm" not in out


@pytest.mark.parametrize(
    "meta",
    [
        {"caller_id": "not-a-number!"},
        {"caller_id": "a" * 65},
        {"dtmf_mode": "rfc9999"},
        {"register": "yes"},
        {"srtp": 1},
        {"register_interval": 10},        # below floor
        {"register_interval": 999999},    # above ceiling
        {"register_interval": True},      # bool is not an int here
        {"register_interval": "1800"},    # string not allowed
    ],
)
def test_invalid_values_raise(meta):
    with pytest.raises(ValueError):
        normalize_trunk_metadata(meta)


def test_create_request_normalises_metadata():
    req = SIPTrunkCreateRequest(
        trunk_name="primary-pbx",
        sip_domain="pbx.example.com",
        metadata={"caller_id": " +15550001111 ", "dtmf_mode": "sip-info"},
    )
    assert req.metadata["caller_id"] == "+15550001111"
    assert req.metadata["dtmf_mode"] == "sip-info"


def test_create_request_rejects_bad_metadata():
    with pytest.raises(ValueError):
        SIPTrunkCreateRequest(
            trunk_name="primary-pbx",
            sip_domain="pbx.example.com",
            metadata={"dtmf_mode": "bogus"},
        )


def test_update_request_leaves_unset_metadata_as_none():
    req = SIPTrunkUpdateRequest(trunk_name="renamed")
    assert req.metadata is None


def test_update_request_normalises_metadata_when_present():
    req = SIPTrunkUpdateRequest(metadata={"srtp": True, "register": True, "register_interval": 600})
    assert req.metadata["srtp"] is True
    assert req.metadata["register_interval"] == 600
