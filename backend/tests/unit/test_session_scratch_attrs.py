"""Per-call scratch state must survive being written to a REAL CallSession.

THE BUG THIS EXISTS TO PREVENT (found 2026-08-17, in production, twice).

``CallSession`` is a pydantic v2 ``BaseModel`` and its ``model_config`` does not
set ``extra="allow"``. Assigning an undeclared attribute therefore RAISES:

    >>> session.last_audio_rms = 3504.0
    ValueError: "CallSession" object has no field "last_audio_rms"

Private names — anything starting with an underscore — are accepted, because
pydantic routes them to its private-attribute store instead of field
validation. So ``session._foo = 1`` works and ``session.foo = 1`` does not, and
the difference is invisible at the call site.

Both of the writes below were originally spelled WITHOUT the underscore, and
both were wrapped in ``try/except Exception: pass`` because "a measurement must
never cost a call". The exception was swallowed on every single frame of every
single call, so:

  * the acoustic nudge guard shipped on 2026-08-13 never suppressed one nudge —
    ``_audio_active`` read False forever, because ``last_audio_rms`` was never
    stored. Report 5 described that fix as live. It had never once executed.
  * the caller voice-onset anchor shipped on 2026-08-17 never recorded an
    onset, so every interrupt logged ``detect_ms: None`` and
    ``speech_to_stop_ms: None`` — the caller-experience measurement the whole
    change existed to provide.

The unit tests for both features passed throughout, because both used
``types.SimpleNamespace`` as the session double. SimpleNamespace accepts any
attribute. The tests were green against a fake with the one property the real
object does not have.

So these tests use the REAL model. That is the entire point of the file: a
double that is more permissive than production cannot prove production works.
"""
from __future__ import annotations

import pytest

from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline.audio_ingest import (
    note_voice_activity,
    voice_onset_age_s,
)


def _session() -> CallSession:
    return CallSession(
        call_id="b3350aee-76aa-4248-89fb-acc13d8ceddd",
        campaign_id="c2b6734d-8992-4038-aaf5-b54a885e7abe",
        lead_id="lead-1",
        provider_call_id="talky-out-1",
        system_prompt="sp",
        voice_id="v1",
    )


def _sum_sq(rms: float, n: int = 640) -> float:
    return rms * rms * n


# ── the guard rail ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "_caller_voice_onset_at",
    "_caller_voice_last_at",
    "_last_audio_rms",
    "_last_audio_rms_at",
    "_last_audio_peak",
    "_nudges_spoken",
    "_nudges_suppressed",
    "_tts_fallback_attempted",
    "_tts_failure_clip_count",
    "_tts_terminal_failure_hangup",
])
def test_scratch_attribute_is_writable_on_the_real_model(name):
    """Every per-call scratch field the pipeline sets, asserted against the
    real CallSession. Rename one of these without its underscore and this
    fails immediately instead of silently disabling a feature in production."""
    s = _session()
    setattr(s, name, 1.0)
    assert getattr(s, name) == 1.0, f"{name} did not survive the write"


def test_a_non_underscore_name_still_raises():
    """Pins WHY the underscore matters, so the convention is not mistaken for
    style. If a future pydantic config makes this permissive, this test failing
    is good news and the comment above can be relaxed."""
    s = _session()
    with pytest.raises(ValueError, match="has no field"):
        s.last_audio_rms = 3504.0


# ── the two features, end to end, on the real model ─────────────────────────

def test_the_voice_onset_anchor_records_against_a_real_session():
    """The 2026-08-17 regression: this returned None on every production
    interrupt because the write silently failed."""
    s = _session()
    note_voice_activity(s, _sum_sq(3504), 640)

    age = voice_onset_age_s(s)
    assert age is not None, "onset was not recorded on a real CallSession"
    assert 0 <= age < 1.0


def test_a_quiet_frame_still_records_nothing():
    s = _session()
    note_voice_activity(s, _sum_sq(12), 640)
    assert voice_onset_age_s(s) is None


def test_one_utterance_keeps_one_onset_on_the_real_model():
    s = _session()
    note_voice_activity(s, _sum_sq(3504), 640)
    first = s._caller_voice_onset_at
    for _ in range(25):
        note_voice_activity(s, _sum_sq(3504), 640)
    assert s._caller_voice_onset_at == first


def test_the_acoustic_nudge_guard_can_read_what_the_ingest_wrote():
    """The 2026-08-13 regression. The silence monitor reads these two names;
    if the ingest cannot write them, the guard is inert and every nudge lands
    on a caller who may be mid-sentence."""
    import time

    # The monitor's thresholds are locals inside the silence-monitor coroutine
    # (VOICE_AUDIO_ACTIVE_RMS / VOICE_AUDIO_ACTIVE_MAX_AGE_S), so they are
    # restated here rather than imported. The values matter less than the
    # question: can the reader see what the writer wrote?
    _ACTIVE_RMS, _MAX_AGE_S = 500.0, 2.0

    s = _session()
    s._last_audio_rms = 3504.0        # the RMS the 2026-08-13 lost calls logged
    s._last_audio_rms_at = time.monotonic()

    rms_at = getattr(s, "_last_audio_rms_at", None)
    assert rms_at is not None, "the monitor cannot see the freshness stamp"
    assert (time.monotonic() - rms_at) <= _MAX_AGE_S
    assert float(getattr(s, "_last_audio_rms", 0.0)) >= _ACTIVE_RMS
