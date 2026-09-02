"""Fast source-contract guards for the separately compiled C++ voice gateway."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[3]
GATEWAY = ROOT / "services" / "voice-gateway-cpp"


def test_rtp_source_binding_has_no_removed_first_packet_latches():
    """Configured-peer pinning must not reference the retired first-packet state."""

    source = (GATEWAY / "src" / "session.cpp").read_text(encoding="utf-8")

    for retired_member in (
        "rtp_source_locked_",
        "locked_source_ip_",
        "locked_source_port_",
    ):
        assert retired_member not in source

    assert "from.sin_addr.s_addr != expected_source_address.s_addr" in source
    assert "ntohs(from.sin_port) != config_.remote_port" in source
