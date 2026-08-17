"""Tests for the measuring instrument itself.

The scorecard is what every claim about a canary run will rest on, so a bug in
it is worse than a bug in most of the code it measures: it would not produce a
wrong call, it would produce a wrong belief about all of them.

The properties pinned here are the ones that would let a real failure be read
as a success:

  * the 2026-08-13 deaf-stream signature is recognised, and is NOT confused
    with the quiet-room case that produces the identical transcript count;
  * an unmeasurable dimension renders as ``?`` and never as ``0``;
  * a resumed-audio verdict is only claimed when the audit line was present.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts" / "call_scorecard.py"
)

if not _SCRIPT.exists():  # pragma: no cover — layout guard
    pytest.skip(f"scorecard script not found at {_SCRIPT}", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("call_scorecard", _SCRIPT)
cs = importlib.util.module_from_spec(_spec)
# Register BEFORE exec: @dataclass resolves annotations via
# sys.modules[cls.__module__], which is None for a module that is only being
# built, and fails with a confusing AttributeError.
sys.modules["call_scorecard"] = cs
_spec.loader.exec_module(cs)


CALL = "83ed010e-c60c-42a5-8d9d-ef28057c7a63"


def _line(msg: str, *, ts: str = "13:24:40", call: str = CALL,
          level: str = "INFO", logger: str = "app.x") -> str:
    return (
        f"Aug 13 {ts} Blaze-VoIP-API talky-api[2424308]: {ts} {level}     "
        f"[{logger}] [req=-] [call={call}] {msg}"
    )


def _score(lines):
    sc = cs.Scorecard()
    for ln in lines:
        sc.feed(ln)
    return sc


def _one(lines) -> "cs.Call":
    sc = _score(lines)
    assert len(sc.calls) == 1, f"expected one call, got {list(sc.calls)}"
    return next(iter(sc.calls.values()))


# ── the line parser ─────────────────────────────────────────────────────────

def test_the_real_production_line_shape_parses():
    c = _one([_line("audio_stream_ended call_id=%s chunks_yielded=412 stt_active=True" % CALL)])
    assert c.call_id == CALL
    assert c.audio_chunks == 412
    assert c.first_seen == "13:24:40"


def test_lines_without_a_call_id_do_not_invent_one():
    sc = _score([_line("stt_resilient_wrapper_active primary=flux secondary=nova-3",
                       call="-")])
    assert sc.calls == {}
    assert sc.wrapper_lines == 1


def test_a_truncated_call_id_joins_to_the_full_one():
    """Half the pipeline logs `call=83ed010e-c60`. If those rows landed under a
    separate key, every per-call figure would be split across two rows."""
    sc = _score([
        _line("audio_stream_ended call_id=%s chunks_yielded=10 stt_active=True" % CALL),
        _line("voice_slow_turn call_id=83ed010e-c60 turn_id=0 response_start_ms=1622.3",
              call="-"),
    ])
    assert len(sc.calls) == 1
    assert sc.calls[CALL].slow_turns == 1


# ── dimension 1: the failure that started all this ──────────────────────────

def test_the_deaf_stream_signature_is_recognised():
    """Voiced audio in, no final out. This is the 2026-08-13 call verbatim."""
    c = _one([
        _line("audio_level call_id=%s window_s=1.0 chunks=25 rms=3504 peak=28988 samples=16000" % CALL),
        _line("audio_stream_ended call_id=%s chunks_yielded=412 stt_active=True" % CALL),
    ])
    assert c.stt_verdict() == "DEAF!"


def test_a_quiet_caller_is_not_called_deaf():
    """THE distinction the old safety nets could not make. Same zero
    transcripts; different acoustics; must not be the same verdict."""
    c = _one([
        _line("audio_level call_id=%s window_s=1.0 chunks=25 rms=9 peak=41 samples=16000" % CALL),
        _line("audio_stream_ended call_id=%s chunks_yielded=412 stt_active=True" % CALL),
    ])
    assert c.stt_verdict() == "no-speech"


def test_a_short_call_is_not_called_deaf():
    """A ring-out delivers a handful of frames. Flagging it would bury a real
    deaf stream in false positives."""
    c = _one([
        _line("audio_level call_id=%s window_s=1.0 chunks=2 rms=3504 peak=28988 samples=900" % CALL),
        _line("audio_stream_ended call_id=%s chunks_yielded=8 stt_active=True" % CALL),
    ])
    assert c.stt_verdict() == "no-speech"


def test_a_working_call_reads_ok():
    c = _one([
        _line("audio_level call_id=%s window_s=1.0 chunks=25 rms=3504 peak=28988 samples=16000" % CALL),
        _line("audio_stream_ended call_id=%s chunks_yielded=412 stt_active=True" % CALL),
        _line("t_stt_first_final call_id=%s ms=716" % CALL),
    ])
    assert c.stt_verdict() == "ok"


def test_a_failover_is_reported_as_a_rescue_not_a_loss():
    c = _one([
        _line("resilient_stt_stream_silent provider=deepgram_flux voiced_s=6.0"),
        _line("resilient_stt_failed_over_to=deepgram_nova buffered_chunks=12"),
        _line("t_stt_first_final call_id=%s ms=900" % CALL),
    ])
    assert c.stt_verdict().startswith("FAILOVER")
    assert c.stt_buffered_chunks == 12


def test_both_engines_deaf_outranks_a_plain_failover():
    c = _one([
        _line("resilient_stt_failed_over_to=deepgram_nova buffered_chunks=12"),
        _line("resilient_stt_secondary_also_silent provider=deepgram_nova voiced_s=6.0"),
    ])
    assert c.stt_verdict() == "BOTH-DEAF"


# ── dimension 2: nudging over live speech ───────────────────────────────────

def test_the_nudge_audit_is_used_when_present():
    c = _one([_line("[SilenceMonitor] 83ed010e-c60 — nudge_audit nudges=2 suppressed=5")])
    assert c.nudge_audit == (2, 5)
    assert c.nudge_verdict() == "2/5"


def test_suppression_is_unknown_on_older_calls_not_zero():
    """Calls placed before the audit line existed must not report 'suppressed=0'
    — that would read as 'the guard never helped'."""
    c = _one([_line("[SilenceMonitor] 83ed010e-c60 — silence (mid), nudging: 'Still there?'")])
    assert c.nudge_verdict().endswith("/?")


# ── dimension 3: the prompt cache ───────────────────────────────────────────

def test_cache_ratio_is_token_weighted():
    c = _one([
        _line("llm_usage model=qwen partial=False prompt_tokens=1000 cached_tokens=512 "
              "cache_hit_ratio=0.51 prompt_time=0.100"),
        _line("llm_usage model=qwen partial=False prompt_tokens=1000 cached_tokens=0 "
              "cache_hit_ratio=0.00 prompt_time=0.500"),
    ])
    assert c.cache_ratio() == pytest.approx(512 / 2000)
    assert c.cache_calls == 2
    assert c.cache_hits == 1


def test_partial_usage_lines_are_not_double_counted():
    """Streaming emits an interim usage line; counting it would inflate the
    denominator and quietly halve the reported hit ratio."""
    c = _one([
        _line("llm_usage model=qwen partial=True prompt_tokens=1000 cached_tokens=0 "
              "cache_hit_ratio=0.00 prompt_time=0.100"),
        _line("llm_usage model=qwen partial=False prompt_tokens=1000 cached_tokens=512 "
              "cache_hit_ratio=0.51 prompt_time=0.100"),
    ])
    assert c.cache_calls == 1
    assert c.cache_ratio() == pytest.approx(0.512)


def test_a_call_with_no_llm_turns_reports_unknown_cache():
    c = _one([_line("audio_stream_ended call_id=%s chunks_yielded=8 stt_active=True" % CALL)])
    assert c.cache_ratio() is None


# ── dimension 6: short replies ──────────────────────────────────────────────

def test_the_shortest_spoken_reply_is_surfaced():
    """'Yes.' surviving is the positive evidence that the six-character rule is
    gone. A call whose shortest reply is 40 characters proves nothing."""
    c = _one([
        _line("llm_response turn=0 said='Hi, is this Sarah?'"),
        _line("llm_response turn=1 said='Yes.'"),
    ])
    assert c.short_reply_verdict() == "4c"


def test_a_silent_turn_is_flagged_on_the_reply_column():
    c = _one([
        _line("llm_response turn=0 said='Okay.'"),
        _line("turn_silent_reason call=%s reason=empty_after_clean" % CALL),
    ])
    assert c.short_reply_verdict().endswith("!")


# ── dimension 10: what the caller heard ─────────────────────────────────────

def test_a_real_interrupt_and_a_no_op_are_counted_apart():
    """139 of 200 interrupts on 2026-08-13 were ordinary turns. Lumping them
    together made 'barge_in_detected' meaningless as a talked-over count."""
    c = _one([
        _line("interrupt_step=nothing_playing interrupt_id=abc call=83ed010e-c60 "
              "reason=barge_in elapsed_ms=0.2 detect_ms=210.0"),
        _line("interrupt_complete {'interrupt_id': 'x', 'ok': True, 'deduped': False, "
              "'task_cancelled': True, 'local_bytes': 0, 'gw_frames': 12, 'gw_ms': 240, "
              "'gw_segments': 1, 'gw_attempts': 1, 'elapsed_ms': 1.2, "
              "'detect_ms': 300.0, 'speech_to_stop_ms': 301.2, 'errors': []}"),
    ])
    assert c.interrupts_noop == 1
    assert c.interrupts_real == 1
    assert c.barge_verdict() == "1/2"
    assert c.speech_to_stop == [301.2]
    assert sorted(c.detect_ms) == [210.0, 300.0]


def test_an_old_interrupt_line_leaves_the_caller_figure_unmeasured():
    """Pre-2026-08-18 payloads have no speech_to_stop_ms. The column must stay
    '?' rather than defaulting to zero, which would read as an instant stop."""
    c = _one([
        _line("interrupt_complete {'interrupt_id': 'x', 'ok': True, 'deduped': False, "
              "'task_cancelled': True, 'local_bytes': 0, 'gw_frames': 12, 'gw_ms': 240, "
              "'gw_segments': 1, 'gw_attempts': 1, 'elapsed_ms': 1.2, 'errors': []}"),
    ])
    assert c.interrupts_real == 1
    assert c.speech_to_stop == []
    assert cs._fmt(cs._p50(c.speech_to_stop)) == "?"


def test_resumed_audio_is_only_claimed_when_the_audit_ran():
    missing = _one([_line("barge_in_detected")])
    assert missing.resume_verdict() == "?", "absence of the audit is not a clean bill"

    clean = _one([
        _line("interrupt_audio_audit call=83ed010e-c60 interrupts=3 resumed_chunks=0 "
              "stale_rejected=5 verdict=clean"),
    ])
    assert clean.resume_verdict() == "clean(5)"

    bad = _one([
        _line("interrupt_audio_audit call=83ed010e-c60 interrupts=1 resumed_chunks=7 "
              "stale_rejected=0 verdict=AUDIO_RESUMED"),
    ])
    assert bad.resume_verdict() == "RESUMED"


# ── the report never turns an unknown into a number ─────────────────────────

def test_unmeasured_values_render_as_question_marks():
    assert cs._fmt(None) == "?"
    assert cs._fmt(None, "ms") == "?"
    assert cs._pct(0, 0) == "?"
    assert cs._pct(0, 10) == "0%", "a real zero must still render as zero"


def test_an_empty_run_does_not_crash_the_summary():
    sc = cs.Scorecard()
    assert "no calls" in cs.render_summary(sc, [])


def test_a_full_row_renders_for_a_bare_call():
    """Every column must survive a call that logged almost nothing — the
    ring-outs are exactly the rows most likely to have missing fields."""
    c = _one([_line("audio_stream_ended call_id=%s chunks_yielded=3 stt_active=True" % CALL)])
    row = cs._row(c)
    assert len(row) == len(cs._COLUMNS)
    assert cs.render_table([c]).count("\n") == 2  # header, rule, one row
