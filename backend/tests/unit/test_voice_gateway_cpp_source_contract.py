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


def test_direct_gate_defines_and_exercises_build_identity():
    """The non-CMake gate must carry the same immutable identity contract."""

    gate = (GATEWAY / "tests" / "run_gate.sh").read_text(encoding="utf-8")
    runtime_tests = (GATEWAY / "tests" / "test_gateway_fixes.cpp").read_text(
        encoding="utf-8"
    )

    assert 'BUILD_SHA="${VOICE_GATEWAY_BUILD_SHA:-dev}"' in gate
    assert 'BUILD_IDENTITY="-DVOICE_GATEWAY_BUILD_SHA=\\\"${BUILD_SHA}\\\""' in gate
    assert '"$BUILD_IDENTITY"' in gate
    assert "std::string(VOICE_GATEWAY_BUILD_SHA)" in runtime_tests


def test_non_echo_receive_can_transition_directly_from_starting_to_active():
    """The state guard must admit the transition requested by the RTP loop."""

    source = (GATEWAY / "src" / "session.cpp").read_text(encoding="utf-8")
    runtime_tests = (GATEWAY / "tests" / "test_gateway_fixes.cpp").read_text(
        encoding="utf-8"
    )
    transitions = source.split("bool RtpSession::can_transition", 1)[1]
    starting_case = transitions.split("case SessionState::Starting:", 1)[1].split(
        "case SessionState::Buffering:", 1
    )[0]

    assert "to == SessionState::Active" in starting_case
    assert "active_deadline" in runtime_tests
    assert "session.snapshot().packets_in == 1" in runtime_tests
