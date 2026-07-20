"""Capture mode: email-ask detection + Flux Configure wiring."""
import asyncio

import pytest

from app.domain.services.voice_pipeline import capture_mode
from app.infrastructure.stt.deepgram_flux import (
    DeepgramFluxSTTProvider,
    CAPTURE_EOT_TIMEOUT_MS,
    CAPTURE_EOT_THRESHOLD,
)


# ── detection ────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "What's your email?",
    "Can I grab your email address?",
    "Could you spell that for me?",
    "And your e-mail?",
    "Could you say that again slowly?",
    "What is your email so I can send the quote?",
])
def test_detects_email_ask(text):
    assert capture_mode.detect_email_ask(text) is True


@pytest.mark.parametrize("text", [
    "I'll send that over to the email you gave me.",
    "Great, you're all booked for Tuesday.",
    "We emailed the brochure already.",
    "",
    None,
])
def test_ignores_non_asks(text):
    assert capture_mode.detect_email_ask(text) is False


# ── Flux Configure payloads ──────────────────────────────────
def _init(**cfg):
    p = DeepgramFluxSTTProvider()
    asyncio.run(p.initialize({"api_key": "k", **cfg}))
    return p


def test_enter_capture_builds_relaxed_configure():
    p = _init(keyterms=["gmail.com"])
    p.enter_capture_mode("call-1")
    payload = p._pending_config["call-1"]
    assert payload["type"] == "Configure"
    assert payload["thresholds"]["eot_timeout_ms"] == CAPTURE_EOT_TIMEOUT_MS
    assert payload["thresholds"]["eot_threshold"] == CAPTURE_EOT_THRESHOLD
    assert payload["keyterms"] == ["gmail.com"]


def test_reset_capture_restores_session_defaults():
    # eager EOT disabled for this session (no eager_eot_threshold in config,
    # so self._eager_eot_threshold is a *legitimate* None). Regression guard
    # for F-04(e): reset_capture_mode must still send an explicit
    # eager_eot_threshold (== eot_threshold, i.e. disabled) rather than
    # silently dropping the key and leaving the live connection stuck at
    # capture mode's relaxed 0.9 threshold forever.
    p = _init(eot_threshold=0.6, eot_timeout_ms=800)
    assert p._eager_eot_threshold is None
    p.reset_capture_mode("call-1")
    th = p._pending_config["call-1"]["thresholds"]
    assert th["eot_timeout_ms"] == 800
    assert th["eot_threshold"] == 0.6
    assert "eager_eot_threshold" in th
    assert th["eager_eot_threshold"] == 0.6


def test_reset_capture_restores_real_eager_threshold():
    # eager EOT enabled for this session -> reset_capture_mode must pass the
    # original float through unchanged.
    p = _init(eot_threshold=0.6, eot_timeout_ms=800, eager_eot_threshold=0.5)
    assert p._eager_eot_threshold == 0.5
    p.reset_capture_mode("call-1")
    th = p._pending_config["call-1"]["thresholds"]
    assert th["eager_eot_threshold"] == 0.5


def test_request_configure_noop_without_call_id():
    p = _init()
    p.request_configure("", eot_timeout_ms=3000)
    assert p._pending_config == {}


# ── controller enter/exit against a fake provider ────────────
class _FakeFlux:
    def __init__(self):
        self.entered = []
        self.reset = []

    def enter_capture_mode(self, call_id):
        self.entered.append(call_id)

    def reset_capture_mode(self, call_id):
        self.reset.append(call_id)


def test_controller_enter_then_exit_once():
    capture_mode.clear("c1")
    fake = _FakeFlux()
    capture_mode.maybe_enter(fake, "c1", "What's your email?")
    assert fake.entered == ["c1"]
    # second ask while already active -> no double enter
    capture_mode.maybe_enter(fake, "c1", "Your email again?")
    assert fake.entered == ["c1"]
    capture_mode.maybe_exit(fake, "c1")
    assert fake.reset == ["c1"]
    # exit when not active -> no-op
    capture_mode.maybe_exit(fake, "c1")
    assert fake.reset == ["c1"]


def test_controller_resolves_wrapped_primary():
    capture_mode.clear("c2")

    class _Resilient:
        def __init__(self, primary):
            self._primary = primary

    fake = _FakeFlux()
    wrapped = _Resilient(fake)
    capture_mode.maybe_enter(wrapped, "c2", "Can I get your email address?")
    assert fake.entered == ["c2"]


def test_controller_silent_when_unsupported():
    capture_mode.clear("c3")
    # provider with no capture methods -> no crash, no state
    capture_mode.maybe_enter(object(), "c3", "What's your email?")
    assert "c3" not in capture_mode._active_calls


# ── F-04b: post-failover capture mode must target _active, not the ──
# ── orphaned _primary. ───────────────────────────────────────────────
def test_controller_targets_active_after_failover():
    """After a mid-call STT failover, _active points at the secondary
    while _primary is orphaned. Capture mode must follow _active."""
    capture_mode.clear("c4")

    class _Resilient:
        def __init__(self, primary, secondary):
            self._primary = primary
            self._secondary = secondary
            self._active = primary

    primary = _FakeFlux()
    secondary = _FakeFlux()
    wrapped = _Resilient(primary, secondary)
    wrapped._active = wrapped._secondary  # simulate post-failover state

    capture_mode.maybe_enter(wrapped, "c4", "What's your email?")
    assert secondary.entered == ["c4"]
    assert primary.entered == []


def test_controller_no_op_when_active_lacks_capture_mode():
    """_active is a Nova-like fake with no enter_capture_mode, and no
    other candidate (primary included) supports it either. Per the
    documented contract, this must be a silent no-op — no exception,
    no state added — NOT a fall-through to an unrelated provider."""
    capture_mode.clear("c5")

    class _NovaLike:
        pass

    class _Resilient:
        def __init__(self, primary, active):
            self._primary = primary
            self._active = active

    wrapped = _Resilient(_NovaLike(), _NovaLike())

    capture_mode.maybe_enter(wrapped, "c5", "What's your email?")
    assert "c5" not in capture_mode._active_calls
