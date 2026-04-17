"""
Parse first-turn latency timestamps from backend logs and emit a P50/P95
report in the format required by §6.2 of outbound_user_first_latency_plan.md.

Usage:
    python backend/scripts/parse_first_turn_baseline.py <logfile> [--out OUT.md]

Input: any text stream that contains the structured log lines emitted by the
instrumentation added in §6.1, plus the C++ gateway's `event=rtp_tx` lines.
Lines are matched by substring on the field name (no JSON parser required —
the logger uses key=value formatting in the backend).

The script groups events by call_id and computes:
  - t_first_rtp_tx - t_stt_first_final  (user-perceived first-interaction)
  - t_stt_ws_ready - t_answer           (STT handshake race)
  - ari_setup_ms, cartesia_ws_handshake_ms, stt_ws_handshake_ms, groq_connect_ms
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


TIMESTAMP_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.,]\d+)"
)
CALL_ID_RE = re.compile(r"call_id=(?P<cid>[A-Za-z0-9_\-]+)")

EVENT_MARKERS = [
    "t_answer",
    "t_ari_setup_done",
    "ari_setup_ms",
    "t_stt_ws_ready",
    "stt_ws_handshake_ms",
    "t_stt_first_final",
    "t_llm_first_token",
    "cartesia_ws_handshake_ms",
    "t_tts_first_audio",
    "t_first_rtp_tx",
    "event=rtp_tx",
    "groq_connect_ms",
]


def parse_timestamp(line: str) -> Optional[float]:
    m = TIMESTAMP_RE.match(line)
    if not m:
        return None
    raw = m.group("ts").replace(",", ".").replace(" ", "T")
    try:
        return datetime.fromisoformat(raw).timestamp() * 1000.0
    except ValueError:
        return None


def extract_number(line: str, key: str) -> Optional[float]:
    m = re.search(rf"{re.escape(key)}[=:\s]+([0-9]+\.?[0-9]*)", line)
    return float(m.group(1)) if m else None


def parse_log(path: Path) -> Dict[str, Dict[str, float]]:
    calls: Dict[str, Dict[str, float]] = defaultdict(dict)
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            marker = next((m for m in EVENT_MARKERS if m in line), None)
            if not marker:
                continue
            cid_m = CALL_ID_RE.search(line)
            if not cid_m:
                continue
            cid = cid_m.group("cid")
            ts_ms = parse_timestamp(line)
            # store the wall-clock timestamp for "t_*" markers
            if marker.startswith("t_") and ts_ms is not None:
                calls[cid].setdefault(marker, ts_ms)
            elif marker == "event=rtp_tx" and ts_ms is not None:
                calls[cid].setdefault("t_first_rtp_tx", ts_ms)
            # store inline numeric fields verbatim
            for field in ("ari_setup_ms", "stt_ws_handshake_ms",
                          "cartesia_ws_handshake_ms", "groq_connect_ms"):
                val = extract_number(line, field)
                if val is not None:
                    calls[cid].setdefault(field, val)
    return calls


def percentile(values: List[float], pct: float) -> Optional[float]:
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def summarise(calls: Dict[str, Dict[str, float]]) -> str:
    deltas = {
        "user_perceived_ms": [],       # t_first_rtp_tx - t_stt_first_final
        "stt_handshake_race_ms": [],   # t_stt_ws_ready - t_answer (negative = good)
    }
    raw = defaultdict(list)
    for cid, ev in calls.items():
        if "t_first_rtp_tx" in ev and "t_stt_first_final" in ev:
            deltas["user_perceived_ms"].append(
                ev["t_first_rtp_tx"] - ev["t_stt_first_final"])
        if "t_stt_ws_ready" in ev and "t_answer" in ev:
            deltas["stt_handshake_race_ms"].append(
                ev["t_stt_ws_ready"] - ev["t_answer"])
        for k in ("ari_setup_ms", "stt_ws_handshake_ms",
                  "cartesia_ws_handshake_ms", "groq_connect_ms"):
            if k in ev:
                raw[k].append(ev[k])

    lines = ["# First-Turn Latency Baseline", "",
             f"Calls parsed: **{len(calls)}**", "",
             "| Metric | N | P50 (ms) | P95 (ms) | Mean (ms) |",
             "|---|---:|---:|---:|---:|"]
    for name, vals in {**deltas, **raw}.items():
        if not vals:
            lines.append(f"| {name} | 0 | — | — | — |")
            continue
        lines.append(
            f"| {name} | {len(vals)} | {percentile(vals, 0.50):.1f} | "
            f"{percentile(vals, 0.95):.1f} | {statistics.mean(vals):.1f} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    if not args.logfile.exists():
        print(f"error: {args.logfile} does not exist", file=sys.stderr)
        return 2
    calls = parse_log(args.logfile)
    report = summarise(calls)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
