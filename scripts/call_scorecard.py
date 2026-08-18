#!/usr/bin/env python3
"""One row per call, across the dimensions a voice agent actually fails on.

WHY
---
After the 2026-08-13 canary, reading forty calls meant grepping a 90,000-line
journal by hand and holding the joins in your head. That is fine once and
useless as a routine, so the day's findings were only as good as whichever
greps somebody happened to think of. Two of the five defects that run found
were noticed by accident.

This turns the journal into a table. It answers the same ten questions of every
call, in the same way, whether the run is four calls or four hundred — so the
next regression is found because it was checked for, not because it was
striking enough to notice.

WHAT IT WILL NOT DO
-------------------
It reports only what the logs actually contain. Where a dimension cannot be
answered from the journal it prints ``?`` rather than a plausible number:

  * Caller speech is REDACTED in logs (``transcript='[redacted chars=21
    sha=...]'``), by design. So "did the agent answer a direct question" cannot
    be scored from here — the agent's side is in the clear, the caller's is not.
    Pass --transcripts to read the caller side from the saved transcript
    instead (a read-only query, off by default).
  * "Genuine audible barge-in" depends on ``speech_to_stop_ms`` and
    ``interrupt_audio_audit``, which only exist on calls placed after
    2026-08-18. Older calls score ``?`` on those columns, not 0.

A blank is a statement that we do not know. That distinction is the entire
lesson of the 2026-08-13 run, where a signal that could not represent the
failure reported success three separate times.

USAGE
-----
    journalctl -u talky-api -u talky-voice-worker --since today --no-pager \
        | python3 scripts/call_scorecard.py

    python3 scripts/call_scorecard.py /tmp/day.txt --since 13:00 --until 16:00
    python3 scripts/call_scorecard.py /tmp/day.txt --detail 83ed010e
    python3 scripts/call_scorecard.py /tmp/day.txt --format csv > scorecard.csv
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Dict, List, Optional

# ── line shape ───────────────────────────────────────────────────────────────
# Aug 13 13:24:26 host talky-api[123]: 13:24:26 INFO [logger] [req=..] [call=..] msg
_LINE = re.compile(
    r"^\w{3}\s+\d+\s(?P<ts>\d\d:\d\d:\d\d)\s\S+\s\S+?\[\d+\]:\s+"
    r"(?:\d\d:\d\d:\d\d\s+)?(?P<level>[A-Z]+)\s+"
    r"(?:\[(?P<logger>[^\]]*)\]\s*)?"
    r"(?:\[req=[^\]]*\]\s*)?"
    r"(?:\[call=(?P<call>[^\]]*)\]\s*)?"
    r"(?P<msg>.*)$"
)

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _num(pattern: str, text: str, cast=float) -> Optional[float]:
    m = re.search(pattern, text)
    if not m:
        return None
    try:
        return cast(m.group(1))
    except (TypeError, ValueError):
        return None


def _pct(part: int, whole: int) -> str:
    return "?" if not whole else f"{100.0 * part / whole:.0f}%"


def _p50(values: List[float]) -> Optional[float]:
    return median(values) if values else None


def _fmt(value: Optional[float], suffix: str = "", nd: int = 0) -> str:
    if value is None:
        return "?"
    return f"{value:.{nd}f}{suffix}"


# ── per-call accumulator ─────────────────────────────────────────────────────

@dataclass
class Call:
    call_id: str
    first_seen: str = ""
    last_seen: str = ""
    campaign: str = ""

    # 1. STT availability
    stt_wrapper: str = ""
    stt_silent: int = 0
    stt_failed_over_to: str = ""
    stt_buffered_chunks: Optional[int] = None
    stt_secondary_silent: int = 0
    stt_fault_injected: bool = False
    audio_chunks: Optional[int] = None
    transcripts: int = 0
    voiced_windows: int = 0
    peak_rms: int = 0

    # 2. nudging over speech
    nudges: List[str] = field(default_factory=list)
    nudge_audit: Optional[tuple] = None      # (spoken, suppressed)
    suppress_events: int = 0

    # 3. prompt cache
    cache_hits: int = 0
    cache_calls: int = 0
    cached_tokens: int = 0
    prompt_tokens: int = 0
    prompt_times: List[float] = field(default_factory=list)
    models: set = field(default_factory=set)
    # Prompt SIZE, first turn -> last turn. Promoted to a first-class column on
    # 2026-08-17: caching is unavailable on this model (proven), so prefill cost
    # is governed by size alone and size is what has to be watched.
    ptok_first: Optional[int] = None
    ptok_last: Optional[int] = None

    # TTS returning nothing
    tts_empty_streams: int = 0
    tts_empty_after_retry: int = 0

    # 4. latency
    speech_to_audio_ms: Optional[float] = None
    ttft_ms: List[float] = field(default_factory=list)
    tts_first_ms: List[float] = field(default_factory=list)
    slow_turns: int = 0

    # 5. prompt / state
    prompt_composed: int = 0
    knowledge_injected: int = 0
    zero_token_turns: int = 0

    # 6. replies
    replies: List[str] = field(default_factory=list)
    silent_turns: int = 0

    # 7. lead name
    field_sanitized: List[str] = field(default_factory=list)
    field_dropped: List[str] = field(default_factory=list)
    opener: str = ""

    # 9. transfer
    transfer_events: int = 0

    # 10. barge-in
    barge_in_detected: int = 0
    interrupts_real: int = 0
    interrupts_noop: int = 0
    interrupts_deduped: int = 0
    interrupt_failed: int = 0
    dropped_ms: List[float] = field(default_factory=list)
    speech_to_stop: List[float] = field(default_factory=list)
    detect_ms: List[float] = field(default_factory=list)
    audio_audit: Optional[tuple] = None      # (interrupts, resumed, rejected, verdict)
    resumed_events: int = 0

    # misc
    audio_gaps: int = 0
    gap_ratios: List[float] = field(default_factory=list)
    disclosure_interrupted: int = 0
    disclosure_suppressed: int = 0
    outcome: str = ""

    # ── derived verdicts ────────────────────────────────────────────────
    @property
    def short(self) -> str:
        return self.call_id[:8]

    def stt_verdict(self) -> str:
        if self.stt_secondary_silent:
            return "BOTH-DEAF"
        if self.stt_failed_over_to:
            return f"FAILOVER>{self.stt_failed_over_to.split('-')[0][:6]}"
        if self.stt_silent:
            return "SILENT"
        # THE 2026-08-13 SIGNATURE, restated as a query: voiced audio went in
        # and no final came back. Both halves are required — "no transcripts"
        # alone is the description of a quiet room, which is exactly the
        # ambiguity that let two dead streams pass for silent callers.
        if self.transcripts == 0 and (self.audio_chunks or 0) > 100 and self.peak_rms > 500:
            return "DEAF!"
        if self.transcripts == 0:
            return "no-speech"
        return "ok"

    def cache_ratio(self) -> Optional[float]:
        if not self.prompt_tokens:
            return None
        return self.cached_tokens / self.prompt_tokens

    def nudge_verdict(self) -> str:
        if self.nudge_audit is not None:
            spoken, suppressed = self.nudge_audit
            return f"{spoken}/{suppressed}"
        if self.nudges:
            return f"{len(self.nudges)}/?"
        return "0/?"

    def barge_verdict(self) -> str:
        total = self.interrupts_real + self.interrupts_noop
        if not total:
            return "-"
        return f"{self.interrupts_real}/{total}"

    def resume_verdict(self) -> str:
        if self.audio_audit is None:
            return "?"
        _, resumed, rejected, verdict = self.audio_audit
        return "RESUMED" if resumed else f"clean({rejected})"

    def short_reply_verdict(self) -> str:
        """Shortest reply the agent actually spoke. The 2026-08-13 defect
        deleted every reply under six characters, so a call whose shortest
        spoken reply is 'Yes.' is positive evidence the fix holds."""
        if not self.replies:
            return "-"
        shortest = min(self.replies, key=len)
        return f"{len(shortest)}c" + ("!" if self.silent_turns else "")


# ── parsing ──────────────────────────────────────────────────────────────────

class Scorecard:
    def __init__(self) -> None:
        self.calls: Dict[str, Call] = {}
        self.alias: Dict[str, str] = {}       # 12-char prefix -> full id
        self.global_notes: List[str] = []
        self.wrapper_lines = 0
        self.wrapper_config = ""

    def _resolve(self, token: str) -> Optional[str]:
        """Map whatever id form a line used onto a canonical call id."""
        if not token or token == "-":
            return None
        if _UUID.fullmatch(token):
            return token
        if token in self.alias:
            return self.alias[token]
        # A truncated id (call=83ed010e-c60) — match against known calls.
        for full in self.calls:
            if full.startswith(token):
                self.alias[token] = full
                return full
        return None

    def _call(self, call_id: str, ts: str) -> Call:
        c = self.calls.get(call_id)
        if c is None:
            c = Call(call_id=call_id, first_seen=ts)
            self.calls[call_id] = c
        c.last_seen = ts
        return c

    def feed(self, line: str) -> None:
        m = _LINE.match(line)
        if not m:
            return
        ts, msg = m.group("ts"), m.group("msg")
        raw_call = (m.group("call") or "").strip()

        # Prefer the structured [call=] field; fall back to an id in the body.
        call_id = None
        if raw_call and raw_call != "-":
            if _UUID.fullmatch(raw_call):
                call_id = raw_call
            elif not raw_call.startswith("talky-out-"):
                call_id = self._resolve(raw_call)
        if call_id is None:
            body = re.search(r"\bcall(?:_id)?=([0-9a-f][0-9a-f-]{7,})", msg)
            if body:
                call_id = self._resolve(body.group(1))
        if call_id is None:
            self._feed_global(ts, msg)
            return

        c = self._call(call_id, ts)
        self._feed_call(c, ts, msg)

    # ── lines with no call id ───────────────────────────────────────────
    def _feed_global(self, ts: str, msg: str) -> None:
        if msg.startswith("stt_resilient_wrapper_active"):
            self.wrapper_lines += 1
            self.wrapper_config = msg.split(" ", 1)[1] if " " in msg else msg
        elif "stt_fault_injection_armed" in msg:
            self.global_notes.append(f"{ts} {msg[:120]}")
        elif "audio_stream_started" in msg:
            for cid in _UUID.findall(msg):
                self._call(cid, ts)

    # ── lines with a call id ────────────────────────────────────────────
    def _feed_call(self, c: Call, ts: str, msg: str) -> None:
        # Learn the short form so later truncated lines join correctly.
        self.alias.setdefault(c.call_id[:12], c.call_id)
        self.alias.setdefault(c.call_id[:8], c.call_id)

        # 1 ── STT availability
        if "resilient_stt_stream_silent" in msg:
            c.stt_silent += 1
        elif "resilient_stt_failed_over_to=" in msg:
            mm = re.search(r"resilient_stt_failed_over_to=(\S+)", msg)
            if mm:
                c.stt_failed_over_to = mm.group(1)
            n = _num(r"buffered_chunks=(\d+)", msg, int)
            if n is not None:
                c.stt_buffered_chunks = int(n)
        elif "resilient_stt_secondary_also_silent" in msg:
            c.stt_secondary_silent += 1
        elif "stt_fault_injection_active" in msg or "stt_fault_injection_armed" in msg:
            c.stt_fault_injected = True
        elif msg.startswith("audio_stream_ended"):
            n = _num(r"chunks_yielded=(\d+)", msg, int)
            if n is not None:
                c.audio_chunks = int(n)
        elif msg.startswith("audio_level"):
            rms = _num(r"\brms=(\d+)", msg, int)
            peak = _num(r"\bpeak=(\d+)", msg, int)
            if rms is not None and rms > 500:
                c.voiced_windows += 1
            if peak is not None:
                c.peak_rms = max(c.peak_rms, int(peak))
        elif msg.startswith("t_stt_first_final") or msg.startswith("llm_response"):
            pass  # handled below / not an STT availability signal

        # 2 ── nudging
        if "nudge_audit" in msg:
            spoken = _num(r"nudges=(\d+)", msg, int)
            supp = _num(r"suppressed=(\d+)", msg, int)
            if spoken is not None and supp is not None:
                c.nudge_audit = (int(spoken), int(supp))
        elif "nudge SUPPRESSED" in msg:
            c.suppress_events += 1
        elif "nudging:" in msg:
            mm = re.search(r"silence \((\w+)\), nudging: (.*)$", msg)
            c.nudges.append(mm.group(2) if mm else msg[-40:])

        # 3 ── prompt cache
        if msg.startswith("llm_usage"):
            partial = re.search(r"partial=(\w+)", msg)
            if not partial or partial.group(1).lower() != "true":
                c.cache_calls += 1
                pt = _num(r"prompt_tokens=(\d+)", msg, int) or 0
                ct = _num(r"cached_tokens=(\d+)", msg, int) or 0
                c.prompt_tokens += int(pt)
                c.cached_tokens += int(ct)
                if pt:
                    if c.ptok_first is None:
                        c.ptok_first = int(pt)
                    c.ptok_last = int(pt)
                if ct:
                    c.cache_hits += 1
                ptime = _num(r"prompt_time=([\d.]+)", msg)
                if ptime is not None:
                    c.prompt_times.append(ptime * 1000.0)
                ttft = _num(r"client_ttft_ms=(-?[\d.]+)", msg)
                if ttft is not None and ttft >= 0:
                    c.ttft_ms.append(ttft)
                mm = re.search(r"model=(\S+)", msg)
                if mm:
                    c.models.add(mm.group(1))

        # 4 ── latency
        if msg.startswith("first_turn_latency"):
            v = _num(r"speech_to_audio_ms=(\d+)", msg)
            if v is not None:
                c.speech_to_audio_ms = v
            v = _num(r"tts_first_chunk_ms=(\d+)", msg)
            if v is not None:
                c.tts_first_ms.append(v)
        elif msg.startswith("voice_slow_turn"):
            c.slow_turns += 1

        # 5 ── prompt / state
        if msg.startswith("telephony_prompt_composed"):
            c.prompt_composed += 1
            mm = re.search(r"campaign=(\S+)", msg)
            if mm:
                c.campaign = mm.group(1)
        elif msg.startswith("campaign_knowledge_injected"):
            c.knowledge_injected += 1
        elif msg.startswith("zero_token_turn"):
            c.zero_token_turns += 1

        # 6 ── replies
        if msg.startswith("llm_response"):
            mm = re.search(r"said=(['\"])(.*)\1\s*$", msg)
            said = mm.group(2) if mm else ""
            if said:
                c.replies.append(said)
                if not c.opener:
                    c.opener = said
        elif msg.startswith("turn_silent_reason"):
            c.silent_turns += 1
        elif msg.startswith("tts_empty_stream_after_retry"):
            c.tts_empty_after_retry += 1
        elif msg.startswith("tts_empty_stream"):
            c.tts_empty_streams += 1

        # 7 ── lead-name handling
        if msg.startswith("call_target_field_sanitized"):
            mm = re.search(r"field=(\S+)", msg)
            out = _num(r"out_chars=(\d+)", msg, int)
            if mm and out == 0:
                c.field_sanitized.append(mm.group(1))
        elif msg.startswith("call_target_field_dropped"):
            mm = re.search(r"field=(\S+)", msg)
            rr = re.search(r"reason=(\S+)", msg)
            if mm:
                c.field_dropped.append(f"{mm.group(1)}:{rr.group(1) if rr else '?'}")

        # 9 ── transfer
        if "transfer" in msg.lower():
            c.transfer_events += 1

        # 10 ── barge-in
        if msg.startswith("barge_in_detected"):
            c.barge_in_detected += 1
        elif "interrupt_step=" in msg:
            if "interrupt_step=nothing_playing" in msg:
                c.interrupts_noop += 1
                v = _num(r"detect_ms=([\d.]+)", msg)
                if v is not None:
                    c.detect_ms.append(v)
            elif "interrupt_step=deduped" in msg:
                c.interrupts_deduped += 1
        elif msg.startswith("interrupt_complete"):
            payload = msg[len("interrupt_complete"):].strip()
            try:
                data = ast.literal_eval(payload)
            except (ValueError, SyntaxError):
                data = None
            if isinstance(data, dict):
                c.interrupts_real += 1
                if data.get("gw_ms"):
                    c.dropped_ms.append(float(data["gw_ms"]))
                s2s = data.get("speech_to_stop_ms")
                if isinstance(s2s, (int, float)):
                    c.speech_to_stop.append(float(s2s))
                det = data.get("detect_ms")
                if isinstance(det, (int, float)):
                    c.detect_ms.append(float(det))
        elif msg.startswith("interrupt_FAILED"):
            c.interrupt_failed += 1
        elif msg.startswith("interrupt_audio_audit"):
            c.audio_audit = (
                int(_num(r"interrupts=(\d+)", msg, int) or 0),
                int(_num(r"resumed_chunks=(\d+)", msg, int) or 0),
                int(_num(r"stale_rejected=(\d+)", msg, int) or 0),
                (re.search(r"verdict=(\S+)", msg) or [None, "?"])[1]
                if re.search(r"verdict=(\S+)", msg) else "?",
            )
        elif msg.startswith("tts_resumed_after_interrupt"):
            c.resumed_events += 1

        # misc context
        if msg.startswith("telephony_audio_gap"):
            c.audio_gaps += 1
            v = _num(r"arrived_ratio=([\d.]+)", msg)
            if v is not None:
                c.gap_ratios.append(v)
        elif "recording_disclosure_interrupted" in msg:
            c.disclosure_interrupted += 1
        elif "recording_suppressed_no_disclosure" in msg:
            c.disclosure_suppressed += 1
        elif msg.startswith("call_outcome_persisted"):
            mm = re.search(r"outcome=(\S+)", msg)
            if mm:
                c.outcome = mm.group(1)
        # Evidence that SOME engine produced a transcript for this caller.
        #
        # Corrected 2026-08-18: this counted only `t_stt_first_final`, which is
        # emitted by the FLUX provider. Every call that failed over to Nova
        # therefore read as `stt-ok=0` — untranscribed — while Nova was in fact
        # transcribing normally, and the report said calls had been lost that
        # had merely changed engine. `transcript_received` comes from the
        # pipeline's transcript_handler and is provider-agnostic, so it answers
        # the question actually being asked.
        if "t_stt_first_final" in msg or msg.startswith("transcript_received"):
            c.transcripts += 1


# ── rendering ────────────────────────────────────────────────────────────────

_COLUMNS = [
    ("call", 9), ("start", 9), ("stt", 14), ("stt-ok", 6), ("nudge s/x", 10),
    ("prompt tok", 12), ("m2e ms", 7), ("ttft", 6), ("reply", 7), ("name", 12),
    ("barge r/t", 10), ("stop ms", 8), ("resume", 11), ("gaps", 6),
]


def _prompt_growth(c: Call) -> str:
    """First turn -> last turn. One number would hide the growth, which is the
    half of the cost that compounds over a long call."""
    if c.ptok_first is None:
        return "?"
    if c.ptok_last is None or c.ptok_last == c.ptok_first:
        return f"{c.ptok_first}"
    return f"{c.ptok_first}>{c.ptok_last}"


def _row(c: Call) -> List[str]:
    return [
        c.short,
        c.first_seen,
        c.stt_verdict(),
        str(c.transcripts or 0),
        c.nudge_verdict(),
        _prompt_growth(c),
        _fmt(c.speech_to_audio_ms),
        _fmt(_p50(c.ttft_ms)),
        c.short_reply_verdict(),
        (",".join(c.field_dropped) or ",".join(c.field_sanitized) or "-")[:12],
        c.barge_verdict(),
        _fmt(_p50(c.speech_to_stop)),
        c.resume_verdict(),
        str(c.audio_gaps),
    ]


def render_table(calls: List[Call]) -> str:
    out = []
    header = "  ".join(name.ljust(w) for name, w in _COLUMNS)
    out.append(header)
    out.append("-" * len(header))
    for c in calls:
        cells = _row(c)
        out.append("  ".join(
            str(v)[:w].ljust(w) for v, (_, w) in zip(cells, _COLUMNS)
        ))
    return "\n".join(out)


def render_csv(calls: List[Call]) -> str:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([name for name, _ in _COLUMNS])
    for c in calls:
        w.writerow(_row(c))
    return buf.getvalue()


def render_summary(sc: Scorecard, calls: List[Call]) -> str:
    n = len(calls)
    if not n:
        return "no calls found in this window"

    prompt_tokens = sum(c.prompt_tokens for c in calls)
    cached_tokens = sum(c.cached_tokens for c in calls)
    cache_calls = sum(c.cache_calls for c in calls)
    cache_hits = sum(c.cache_hits for c in calls)
    deaf = [c for c in calls if c.stt_verdict() in ("DEAF!", "SILENT", "BOTH-DEAF")]
    failed_over = [c for c in calls if c.stt_failed_over_to]
    audited = [c for c in calls if c.nudge_audit is not None]
    suppressed = sum(c.nudge_audit[1] for c in audited)
    spoken = sum(c.nudge_audit[0] for c in audited)
    stops = [v for c in calls for v in c.speech_to_stop]
    detects = [v for c in calls for v in c.detect_ms]
    m2e = [c.speech_to_audio_ms for c in calls if c.speech_to_audio_ms is not None]
    resumed = [c for c in calls if c.audio_audit and c.audio_audit[1]]
    audit_seen = [c for c in calls if c.audio_audit is not None]
    dropped_names = [c for c in calls if c.field_dropped]

    lines = [
        "",
        f"CALLS                     {n}",
        f"  STT resilient wrapper   {sc.wrapper_lines} activations  {sc.wrapper_config[:60]}",
        f"  STT lost/deaf           {len(deaf)}  ({_pct(len(deaf), n)})",
        f"  STT failed over         {len(failed_over)}"
        + (f"  -> {failed_over[0].stt_failed_over_to}" if failed_over else ""),
        "",
        f"NUDGES (audited calls {len(audited)}/{n})",
        f"  spoken                  {spoken}",
        f"  suppressed over speech  {suppressed}"
        + ("   <- the acoustic guard doing its job" if suppressed else ""),
        "",
        "PROMPT SIZE  (the dominant term in time-to-first-token)",
        f"  turn-0 prompt p50       "
        f"{_fmt(_p50([c.ptok_first for c in calls if c.ptok_first]), ' tok')}",
        f"  largest prompt seen     "
        f"{_fmt(max([c.ptok_last for c in calls if c.ptok_last], default=None), ' tok')}",
        f"  prompt_time p50         {_fmt(_p50([v for c in calls for v in c.prompt_times]), 'ms')}"
        "   <- prefill; NOT recoverable by caching on this model",
        f"  cache hits              {cache_hits}/{cache_calls} turns"
        f"  ({_pct(cached_tokens, prompt_tokens)} of {prompt_tokens} tokens)",
        "",
        "LATENCY",
        f"  first-turn mouth-to-ear p50   {_fmt(_p50(m2e), 'ms')}"
        + (f"   (n={len(m2e)})" if m2e else ""),
        f"  LLM TTFT p50                  "
        f"{_fmt(_p50([v for c in calls for v in c.ttft_ms]), 'ms')}",
        f"  slow turns                    {sum(c.slow_turns for c in calls)}",
        "",
        "INTERRUPTION — WHAT THE CALLER HEARD",
        f"  real interrupts         {sum(c.interrupts_real for c in calls)}",
        f"  no-ops (ordinary turn)  {sum(c.interrupts_noop for c in calls)}",
        f"  failed interrupts       {sum(c.interrupt_failed for c in calls)}",
        f"  detect p50              {_fmt(_p50(detects), 'ms')}"
        "    caller starts talking -> we begin stopping",
        f"  speech->audible stop p50 {_fmt(_p50(stops), 'ms')}"
        "   (floor: excludes one RTP frame + jitter buffer)",
        f"  cancelled audio resumed {len(resumed)} of {len(audit_seen)} audited calls"
        if audit_seen else
        "  cancelled audio resumed ?  (no call in this window carries the audit line)",
        "",
        "OTHER",
        f"  implausible lead names dropped  {len(dropped_names)}",
        f"  audio gap warnings              {sum(c.audio_gaps for c in calls)}",
        f"  recordings lost to disclosure   "
        f"{sum(c.disclosure_suppressed for c in calls)}",
        f"  silent turns (no audio spoken)  {sum(c.silent_turns for c in calls)}",
        f"  TTS empty streams (retried)     {sum(c.tts_empty_streams for c in calls)}",
        f"  TTS empty after retry           {sum(c.tts_empty_after_retry for c in calls)}",
    ]
    if sc.global_notes:
        lines += ["", "FAULT INJECTION ACTIVE:"] + [f"  {x}" for x in sc.global_notes[:5]]
    return "\n".join(lines)


def render_detail(c: Call) -> str:
    ratio = c.cache_ratio()
    out = [
        f"call {c.call_id}",
        f"  window            {c.first_seen} -> {c.last_seen}",
        f"  campaign          {c.campaign or '?'}",
        f"  outcome           {c.outcome or '?'}",
        "",
        f"  1 STT             {c.stt_verdict()}  transcripts={c.transcripts} "
        f"audio_chunks={c.audio_chunks} peak_rms={c.peak_rms} "
        f"voiced_windows={c.voiced_windows}",
    ]
    if c.stt_failed_over_to:
        out.append(f"                    failed over to {c.stt_failed_over_to}, "
                   f"replayed {c.stt_buffered_chunks} buffered chunks")
    if c.stt_fault_injected:
        out.append("                    FAULT INJECTED (primary deliberately deaf)")
    out += [
        f"  2 nudges          spoken={c.nudge_verdict().split('/')[0]} "
        f"suppressed={c.nudge_audit[1] if c.nudge_audit else '?'} "
        f"episodes={c.suppress_events}",
    ]
    for phrase in c.nudges[:6]:
        out.append(f"                    -> {phrase}")
    out += [
        f"  3 prompt cache    {'?' if ratio is None else f'{ratio*100:.1f}%'} "
        f"({c.cached_tokens}/{c.prompt_tokens} tokens over {c.cache_calls} turns) "
        f"prompt_time p50={_fmt(_p50(c.prompt_times), 'ms')}",
        f"                    models={','.join(sorted(c.models)) or '?'}",
        f"  4 latency         first-turn mouth-to-ear={_fmt(c.speech_to_audio_ms, 'ms')} "
        f"ttft p50={_fmt(_p50(c.ttft_ms), 'ms')} slow_turns={c.slow_turns}",
        f"  5 prompt/state    composed={c.prompt_composed} "
        f"knowledge={c.knowledge_injected} zero_token_turns={c.zero_token_turns}",
        f"  6 replies         {len(c.replies)} spoken, shortest="
        f"{c.short_reply_verdict()}, silent_turns={c.silent_turns}",
    ]
    if c.replies:
        shortest = sorted(c.replies, key=len)[:3]
        for r in shortest:
            out.append(f"                    ({len(r)}c) {r[:80]}")
    out += [
        f"  7 lead name       opener={c.opener[:60]!r}",
        f"                    dropped={c.field_dropped or '-'} "
        f"emptied={c.field_sanitized or '-'}",
        f"  8 direct question ?  (caller speech is redacted in logs; use --transcripts)",
        f"  9 transfer        {c.transfer_events} mentions",
        f" 10 barge-in        real={c.interrupts_real} noop={c.interrupts_noop} "
        f"deduped={c.interrupts_deduped} failed={c.interrupt_failed}",
        f"                    detect p50={_fmt(_p50(c.detect_ms), 'ms')} "
        f"speech->stop p50={_fmt(_p50(c.speech_to_stop), 'ms')} "
        f"binned p50={_fmt(_p50(c.dropped_ms), 'ms')}",
        f"                    resumed audio: {c.resume_verdict()}"
        + (f"  ({c.resumed_events} chunk warnings)" if c.resumed_events else ""),
        f"     audio gaps     {c.audio_gaps}"
        + (f" arrived_ratio p50={_fmt(_p50(c.gap_ratios), '', 3)}" if c.gap_ratios else ""),
    ]
    if c.disclosure_suppressed:
        out.append("     RECORDING      lost — caller barged over the disclosure")
    return "\n".join(out)


# ── entry point ──────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", help="journal file (default: stdin)")
    ap.add_argument("--since", help="HH:MM:SS lower bound on the log timestamp")
    ap.add_argument("--until", help="HH:MM:SS upper bound")
    ap.add_argument("--detail", help="print the full breakdown for one call id prefix")
    ap.add_argument("--format", choices=("table", "csv"), default="table")
    ap.add_argument("--min-turns", type=int, default=0,
                    help="hide calls with fewer than N agent replies (ring-outs)")
    args = ap.parse_args(argv)

    stream = open(args.path, errors="replace") if args.path else sys.stdin
    sc = Scorecard()
    try:
        for line in stream:
            if args.since or args.until:
                m = _LINE.match(line)
                if m:
                    ts = m.group("ts")
                    if args.since and ts < args.since:
                        continue
                    if args.until and ts > args.until:
                        continue
            sc.feed(line)
    finally:
        if args.path:
            stream.close()

    calls = sorted(sc.calls.values(), key=lambda c: (c.first_seen, c.call_id))
    calls = [c for c in calls if len(c.replies) >= args.min_turns]

    if args.detail:
        matches = [c for c in calls if c.call_id.startswith(args.detail)]
        if not matches:
            print(f"no call matching {args.detail!r}", file=sys.stderr)
            return 1
        for c in matches:
            print(render_detail(c))
            print()
        return 0

    if args.format == "csv":
        print(render_csv(calls), end="")
        return 0

    print(render_table(calls))
    print(render_summary(sc, calls))
    print()
    print("? = not answerable from the journal. Never read a ? as a zero.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
