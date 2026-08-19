#!/usr/bin/env python3
"""Reconcile what the CALLER HEARD with what the logs SAY, around a barge-in.

WHY THIS EXISTS
---------------
Every latency number the pipeline reports about an interruption is measured
inside Python: `detect_ms` (caller's acoustic onset -> our decision),
`speech_to_stop_ms` (onset -> teardown finished), `dropped_ms` (what the C++
gateway still held when we asked it to stop).

None of those is what the caller experienced. `tts_active = False` means Python
stopped WRITING audio; the gateway holds its own queue and paces it out in real
time, so the caller keeps hearing the agent for some time after Python believes
it has stopped. Interrupts that DID ask the gateway routinely found 240-300ms
still sitting in it.

The recording is the only artefact that contains the caller's actual experience.
It is stereo — one leg per channel — so the agent's audio and the caller's audio
can be measured separately, and the moment the agent's audio truly ceases can be
compared against the moment Python thought it had.

WHAT IT PRODUCES
----------------
Per interrupt: the caller's acoustic onset, the last agent audio the caller
could actually hear, and the overhang between them — alongside the logged
figures, so the two accounts can be checked against each other.

TIME ALIGNMENT (and its error bar)
----------------------------------
The recording has no absolute clock. It is anchored by matching the FIRST agent
audio onset in the waveform to the first TTS send in the journal for that call.
Everything else follows from that single offset. The anchor is good to about one
frame (20ms) plus whatever the gateway's own start latency is, so treat
sub-50ms differences as noise and read the hundreds-of-ms figures.
"""
from __future__ import annotations

import argparse
import re
import struct
import subprocess
import sys
import wave
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

FRAME_MS = 20.0
# Same 500 the rest of the audio path uses (resilient_stt._SPEECH_RMS_THRESHOLD,
# audio_ingest._VOICE_ONSET_RMS) so one number moves them all.
SPEECH_RMS = 500.0
# A gap shorter than this inside one utterance is a breath, not the end of it.
RUN_GAP_MS = 300.0


# ── waveform ────────────────────────────────────────────────────────────────

@dataclass
class Envelope:
    """Per-frame RMS for one leg of the call."""
    frame_ms: float
    rms: list[float] = field(default_factory=list)

    def at(self, t_s: float) -> float:
        i = int(t_s * 1000.0 / self.frame_ms)
        return self.rms[i] if 0 <= i < len(self.rms) else 0.0

    def first_onset_s(self) -> Optional[float]:
        for i, r in enumerate(self.rms):
            if r >= SPEECH_RMS:
                return i * self.frame_ms / 1000.0
        return None

    def last_voiced_before(self, t_s: float) -> Optional[float]:
        """Last frame at or before t_s that carried real audio."""
        end = min(int(t_s * 1000.0 / self.frame_ms), len(self.rms) - 1)
        for i in range(end, -1, -1):
            if self.rms[i] >= SPEECH_RMS:
                return i * self.frame_ms / 1000.0
        return None

    def next_silence_after(self, t_s: float, quiet_ms: float = 200.0) -> Optional[float]:
        """First moment after t_s with `quiet_ms` of continuous silence.

        This is the honest answer to "when did the caller stop hearing the
        agent" — not the first quiet frame, which a pause between words would
        satisfy.
        """
        need = int(quiet_ms / self.frame_ms)
        start = max(int(t_s * 1000.0 / self.frame_ms), 0)
        run = 0
        for i in range(start, len(self.rms)):
            if self.rms[i] < SPEECH_RMS:
                run += 1
                if run >= need:
                    return (i - run + 1) * self.frame_ms / 1000.0
            else:
                run = 0
        return None

    def voiced_seconds(self) -> float:
        return sum(1 for r in self.rms if r >= SPEECH_RMS) * self.frame_ms / 1000.0


def envelopes(path: Path) -> tuple[Envelope, Envelope, int]:
    """Return (channel A, channel B, sample rate) as per-frame RMS."""
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2:
            raise SystemExit(f"expected 16-bit PCM, got {w.getsampwidth()*8}-bit")
        nch, rate, n = w.getnchannels(), w.getframerate(), w.getnframes()
        if nch != 2:
            raise SystemExit(
                f"{path.name} is {nch}-channel. Separating the agent from the "
                "caller needs the stereo (one leg per channel) recording."
            )
        raw = w.readframes(n)

    per = int(rate * FRAME_MS / 1000.0)
    a, b = Envelope(FRAME_MS), Envelope(FRAME_MS)
    step = per * 2                       # 2 channels
    for off in range(0, n - per, per):
        chunk = struct.unpack_from(f"<{step}h", raw, off * 4)
        sa = sb = 0.0
        for i in range(0, step, 2):
            sa += chunk[i] * chunk[i]
            sb += chunk[i + 1] * chunk[i + 1]
        a.rms.append((sa / per) ** 0.5)
        b.rms.append((sb / per) ** 0.5)
    return a, b, rate


# ── journal ─────────────────────────────────────────────────────────────────

TS = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+)')


@dataclass
class Ev:
    t: datetime
    kind: str
    detail: str = ""
    iid: str = ""          # interrupt_id, for pairing begin <-> complete


# Only ~1 interrupt in 6 actually stops audio. The rest take the
# `nothing_playing` shortcut and return WITHOUT logging interrupt_complete, so
# pairing a begin to "the next complete in time" silently attributes one real
# interrupt's numbers to a dozen no-ops. (It did exactly that on the first run
# of this script: detect_ms=492.3 appeared identically on twenty rows.) Pair on
# the id or not at all.
IID = re.compile(r"interrupt_id[=':\s]+'?(\w+)")


def journal(call_prefix: str, since: str) -> list[Ev]:
    out = subprocess.run(
        ["journalctl", "-u", "talky-api", "--no-pager", "-o", "short-precise",
         "--since", since],
        capture_output=True, text=True, errors="replace",
    ).stdout
    evs: list[Ev] = []
    for line in out.splitlines():
        if call_prefix not in line:
            continue
        m = TS.match(line)
        if not m:
            # short-precise renders "Aug 19 14:03:11.123456"; normalise
            m2 = re.match(r'^(\w{3} \d{2} \d{2}:\d{2}:\d{2}\.\d+)', line)
            if not m2:
                continue
            year = datetime.now().year
            t = datetime.strptime(f"{year} {m2.group(1)}", "%Y %b %d %H:%M:%S.%f")
        else:
            t = datetime.strptime(m.group(1)[:26], "%Y-%m-%dT%H:%M:%S.%f")

        kind = detail = None
        iid = ""
        if "TTS_FMT_DEBUG" in line:
            kind = "tts_first_send"
        elif "interrupt_step=begin" in line:
            kind = "interrupt_begin"
            m3 = IID.search(line)
            iid = detail = m3.group(1) if m3 else ""
        elif "interrupt_step=cpp_interrupt" in line:
            kind = "cpp_interrupt"
            m3 = IID.search(line)
            iid = m3.group(1) if m3 else ""
        elif "interrupt_complete" in line:
            kind = "interrupt_complete"
            detail = line[line.find("{"):][:500]
            m3 = IID.search(detail)
            iid = m3.group(1) if m3 else ""
        elif "turn_complete" in line:
            kind = "turn_complete"
        elif "pre_tts_hold_released" in line:
            kind = "hold_released"
            detail = line[line.find("pre_tts_hold_released"):][:160]
        elif "pre_tts_hold_timeout" in line:
            kind = "hold_timeout"
            detail = line[line.find("pre_tts_hold_timeout"):][:160]
        if kind:
            evs.append(Ev(t, kind, detail or "", iid))
    evs.sort(key=lambda e: e.t)
    return evs


def resolve_voice_session(calls_id: str, since: str) -> Optional[str]:
    """Recordings are named after the dialer's `calls.id`; every pipeline log
    line is keyed on the voice-session id instead. `bind_telephony_call` is the
    one line that carries both."""
    out = subprocess.run(
        ["journalctl", "-u", "talky-api", "--no-pager", "--since", since],
        capture_output=True, text=True, errors="replace",
    ).stdout
    m = re.search(
        rf"bind_telephony_call voice_session=(\w+) -> calls\.id={calls_id[:8]}",
        out,
    )
    return m.group(1) if m else None


def num(blob: str, key: str) -> Optional[float]:
    m = re.search(rf"'{key}':\s*([0-9.]+)", blob) or \
        re.search(rf"\b{key}=([0-9.]+)", blob)
    return float(m.group(1)) if m else None


# ── report ──────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("recording", type=Path)
    ap.add_argument("--call", default=None,
                    help="voice-session id. Omit to resolve it from the journal: "
                         "recordings are named after the dialer's calls.id, which "
                         "is a DIFFERENT uuid from the voice session every "
                         "pipeline log line is keyed on.")
    ap.add_argument("--since", default="2026-08-19",
                    help="journalctl --since window")
    args = ap.parse_args()

    if not args.recording.exists():
        print(f"no such recording: {args.recording}", file=sys.stderr)
        return 2

    if not args.call:
        args.call = resolve_voice_session(args.recording.stem, args.since)
        if not args.call:
            print("could not resolve the voice session for "
                  f"{args.recording.stem[:8]} — pass --call explicitly",
                  file=sys.stderr)
            return 2
        print(f"resolved: recording {args.recording.stem[:8]} (dialer calls.id) "
              f"-> voice session {args.call}")

    ch_a, ch_b, rate = envelopes(args.recording)
    evs = journal(args.call[:8], args.since)

    print("=" * 78)
    print(f"  {args.recording.name}")
    print(f"  {rate} Hz stereo · {len(ch_a.rms) * FRAME_MS / 1000.0:.1f}s · "
          f"{FRAME_MS:.0f}ms frames · speech at RMS >= {SPEECH_RMS:.0f}")
    print("=" * 78)

    # Which leg is the agent? The agent speaks first (disclosure + greeting) and
    # speaks more, so it is the channel whose first onset is earliest with a
    # substantial voiced total. Stated rather than assumed, and printed so a
    # wrong guess is visible instead of silently inverting the whole report.
    oa, ob = ch_a.first_onset_s(), ch_b.first_onset_s()
    print(f"\nchannel 0: first onset {oa if oa is None else f'{oa:6.2f}s'} · "
          f"voiced {ch_a.voiced_seconds():6.1f}s")
    print(f"channel 1: first onset {ob if ob is None else f'{ob:6.2f}s'} · "
          f"voiced {ch_b.voiced_seconds():6.1f}s")
    if oa is None and ob is None:
        print("\nboth channels silent — nothing to reconcile")
        return 1
    agent_is_a = (ob is None) or (oa is not None and oa <= ob)
    agent, caller = (ch_a, ch_b) if agent_is_a else (ch_b, ch_a)
    print(f"=> AGENT = channel {0 if agent_is_a else 1}, "
          f"CALLER = channel {1 if agent_is_a else 0} "
          "(agent speaks first: disclosure, then greeting)")

    first_tts = next((e for e in evs if e.kind == "tts_first_send"), None)
    agent_onset = agent.first_onset_s()
    if not first_tts or agent_onset is None:
        print("\ncannot anchor the clock: need a TTS send in the journal AND "
              "agent audio in the recording")
        return 1
    origin = first_tts.t - timedelta(seconds=agent_onset)
    print(f"\nanchor: first agent audio at {agent_onset:.2f}s in the file "
          f"== {first_tts.t.strftime('%H:%M:%S.%f')[:-3]} in the journal")
    print(f"        recording t=0 == {origin.strftime('%H:%M:%S.%f')[:-3]} "
          "(+/- one 20ms frame)")

    def rel(t: datetime) -> float:
        return (t - origin).total_seconds()

    begins = [e for e in evs if e.kind == "interrupt_begin"]
    completes = {e.iid: e for e in evs if e.kind == "interrupt_complete" and e.iid}
    if not begins:
        print("\nno interrupts in this call")
        return 0

    real = [b for b in begins if b.iid and b.iid in completes]
    noop = len(begins) - len(real)
    print(f"\n{len(begins)} interrupt events: {len(real)} reached the gateway, "
          f"{noop} took the nothing_playing shortcut")
    print("  A shortcut interrupt had no audio to stop, so it cannot have talked\n"
          "  over anyone and is not reconciled here.")
    if not real:
        print("\n  Nothing to reconcile — place a call and interrupt the agent "
              "MID-SENTENCE.")
        return 0

    print()
    hdr = ("  id            caller     agent    heard-after   caller     "
           "logged      gateway")
    hdr2 = ("                onset      stopped   decision    talked-over  "
            "spch->stop  discarded")
    print(hdr)
    print(hdr2)
    print("  " + "-" * 76)

    unaccounted = []
    for b in real:
        t_rel = rel(b.t)
        blob = completes[b.iid].detail
        detect = num(blob, "detect_ms")
        s2s = num(blob, "speech_to_stop_ms")
        gw_ms = num(blob, "gw_ms")
        gw_frames = num(blob, "gw_frames")

        # What the caller DID: walk back to the start of the voiced run that was
        # in progress when we reacted.
        c_on = None
        cand = caller.last_voiced_before(t_rel)
        if cand is not None and (t_rel - cand) <= 4.0:
            i = int(cand * 1000 / FRAME_MS)
            while i > 0 and caller.rms[i - 1] >= SPEECH_RMS:
                i -= 1
            c_on = i * FRAME_MS / 1000.0

        # Was the agent even audible when we decided to stop? If the leg is
        # already quiet the caller heard nothing more, and hunting forward for
        # the next silence would measure the agent's NEXT reply instead — which
        # is how the first draft of this script reported a 2.4s "overhang" on an
        # interrupt where the gateway queue was empty.
        agent_rms = agent.at(t_rel)
        audible = agent_rms >= SPEECH_RMS

        if audible:
            # A 200ms gap is longer than any pause inside a TTS sentence and
            # shorter than the space before the next reply.
            a_off = agent.next_silence_after(t_rel, quiet_ms=200.0)
            if a_off is not None and (a_off - t_rel) > 8.0:
                a_off = None                 # agent simply never stopped
        else:
            a_off = t_rel                    # already silent at the decision

        heard_after = (a_off - t_rel) * 1000.0 if a_off is not None else None
        talked_over = ((a_off - c_on) * 1000.0
                       if (a_off is not None and c_on is not None) else None)

        def f(v, unit="ms", w=9):
            return f"{v:.0f}{unit}".rjust(w) if v is not None else "-".rjust(w)

        print(f"  {b.iid:12s} {f(c_on, 's', 8) if c_on is None else ('%7.2fs' % c_on)} "
              f"{('%8.2fs' % a_off) if a_off is not None else '       -':>9s} "
              f"{f(heard_after):>11s} {f(talked_over):>12s} "
              f"{f(s2s):>11s} {f(gw_ms):>11s}")

        # THE RECONCILIATION.
        #
        # gw_ms is audio the gateway THREW AWAY — audio the caller was spared,
        # not audio they heard. So a working stop shows heard-after ~= 0 WITH a
        # non-zero gw_ms: the queue existed, and we killed it before it played.
        # A large heard-after is the failure: the caller kept hearing us.
        if not audible:
            print(f"               agent leg already silent at the decision "
                  f"(rms {agent_rms:.0f}) — nothing was playing to stop")
        elif heard_after is not None:
            unaccounted.append(heard_after)
            verdict = ("STOPPED CLEANLY" if heard_after <= 60 else
                       f"caller kept hearing the agent for {heard_after:.0f}ms")
            print(f"               agent audible at rms {agent_rms:.0f}; gateway "
                  f"discarded {gw_ms or 0:.0f}ms ({gw_frames or 0:.0f} frames) "
                  f"-> {verdict}")
        else:
            print(f"               agent audible at rms {agent_rms:.0f} and never "
                  "stopped within 8s — the interrupt did not take effect")

    if unaccounted:
        u = sorted(unaccounted)
        clean = sum(1 for x in u if x <= 60)
        print(f"\n  audible tail after the stop decision: min {u[0]:+.0f}ms  "
              f"median {u[len(u)//2]:+.0f}ms  max {u[-1]:+.0f}ms  (n={len(u)})")
        print(f"  {clean}/{len(u)} stopped within one frame of the decision.")
        print("  gw_ms is audio the gateway DISCARDED — what the caller was "
              "spared,\n  not what they heard. A clean stop is a small tail "
              "WITH a non-zero gw_ms.")

    holds = [e for e in evs if e.kind in ("hold_released", "hold_timeout")]
    if holds:
        print("\npre-TTS holds")
        for h in holds:
            print(f"  {rel(h.t):7.2f}s  {h.detail}")

    print("\nNOTE  'agent audio' is the last moment the CALLER could hear the "
          "agent,\n      measured from the recording — not when Python stopped "
          "sending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
