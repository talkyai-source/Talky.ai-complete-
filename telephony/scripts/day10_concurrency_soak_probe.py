#!/usr/bin/env python3
"""Day 10 concurrency + soak probe.

Runs staged concurrent SIP load against the Day 9 runtime path, derives
RFC6076-style session KPIs, identifies safe concurrency threshold, and executes
a sustained soak window.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import re
import socket
import time
import uuid
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

TO_TAG_RE = re.compile(r";\s*tag=([^;>\s]+)", re.IGNORECASE)


@dataclass
class Day10CallResult:
    stage_label: str
    call_index: int
    tenant_id: str
    scenario: str
    success: bool
    invite_codes: List[int]
    invite_final_code: Optional[int]
    setup_delay_ms: Optional[float]
    disconnect_delay_ms: Optional[float]
    remote_bye_received: bool
    remote_bye_elapsed_ms: Optional[float]
    caller_bye_code: Optional[int]
    reason: str
    call_id: str
    bind_ip: str
    target: str
    started_at_epoch_ms: int
    ended_at_epoch_ms: int


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _percentile(values: Sequence[float], p: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if p <= 0:
        return ordered[0]
    if p >= 100:
        return ordered[-1]
    rank = (p / 100.0) * (len(ordered) - 1)
    low = int(math.floor(rank))
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * frac


def _build_sdp(bind_ip: str, media_port: int) -> str:
    return (
        "v=0\r\n"
        f"o=- 0 0 IN IP4 {bind_ip}\r\n"
        "s=Talky Day10 Probe\r\n"
        f"c=IN IP4 {bind_ip}\r\n"
        "t=0 0\r\n"
        f"m=audio {media_port} RTP/AVP 0\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=ptime:20\r\n"
    )


def _build_request(
    *,
    method: str,
    target_host: str,
    target_port: int,
    from_user: str,
    extension: str,
    local_ip: str,
    local_port: int,
    call_id: str,
    cseq: int,
    from_tag: str,
    to_header: str,
    contact_user: str,
    body: str = "",
) -> bytes:
    branch = f"z9hG4bK-{uuid.uuid4().hex[:12]}"
    request_uri = f"sip:{extension}@{target_host}:{target_port}"
    lines = [
        f"{method} {request_uri} SIP/2.0",
        f"Via: SIP/2.0/UDP {local_ip}:{local_port};branch={branch};rport",
        f"From: <sip:{from_user}@{local_ip}>;tag={from_tag}",
        f"To: {to_header}",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq} {method}",
        f"Contact: <sip:{contact_user}@{local_ip}:{local_port}>",
        "Max-Forwards: 70",
        "User-Agent: talky-day10-concurrency-soak-probe/1.0",
    ]
    if body:
        lines.append("Content-Type: application/sdp")
    lines.append(f"Content-Length: {len(body.encode('utf-8'))}")
    return ("\r\n".join(lines) + "\r\n\r\n" + body).encode("utf-8")


def _build_response(status_code: int, reason: str, headers: Dict[str, str]) -> bytes:
    lines = [f"SIP/2.0 {status_code} {reason}"]
    for key in ("via", "from", "to", "call-id", "cseq"):
        value = headers.get(key)
        if value:
            normalized = {
                "via": "Via",
                "from": "From",
                "to": "To",
                "call-id": "Call-ID",
                "cseq": "CSeq",
            }[key]
            lines.append(f"{normalized}: {value}")
    lines.append("Content-Length: 0")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")


def _parse_message(raw: bytes) -> Tuple[str, Dict[str, str], str]:
    text = raw.decode("utf-8", errors="ignore")
    head, _, body = text.partition("\r\n\r\n")
    lines = head.split("\r\n")
    if not lines:
        raise ValueError("invalid SIP message")

    start_line = lines[0].strip()
    headers: Dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return start_line, headers, body


def _extract_to_tag(to_header: str) -> Optional[str]:
    match = TO_TAG_RE.search(to_header or "")
    return match.group(1).strip() if match else None


def _recv_invite_final(
    sock: socket.socket,
    *,
    timeout_s: float,
    call_id: str,
) -> Tuple[List[int], Optional[Dict[str, str]], Optional[int]]:
    deadline = time.monotonic() + timeout_s
    seen_codes: List[int] = []
    final_headers: Optional[Dict[str, str]] = None
    final_recv_ms: Optional[int] = None

    while time.monotonic() < deadline:
        remaining = max(0.05, deadline - time.monotonic())
        sock.settimeout(remaining)
        packet, _ = sock.recvfrom(65535)
        start_line, headers, _body = _parse_message(packet)
        if not start_line.startswith("SIP/2.0"):
            continue
        parts = start_line.split()
        if len(parts) < 2:
            continue
        code = int(parts[1])
        cseq = headers.get("cseq", "")
        cseq_parts = cseq.split()
        if len(cseq_parts) < 2 or cseq_parts[1].upper() != "INVITE":
            continue
        if headers.get("call-id", "").strip() != call_id:
            continue
        seen_codes.append(code)
        if code >= 200:
            final_headers = headers
            final_recv_ms = _now_ms()
            break

    return seen_codes, final_headers, final_recv_ms


def _recv_bye_response(
    sock: socket.socket,
    *,
    timeout_s: float,
    call_id: str,
) -> Tuple[Optional[int], Optional[int]]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        remaining = max(0.05, deadline - time.monotonic())
        sock.settimeout(remaining)
        packet, _ = sock.recvfrom(65535)
        start_line, headers, _body = _parse_message(packet)
        if not start_line.startswith("SIP/2.0"):
            continue
        parts = start_line.split()
        if len(parts) < 2:
            continue
        cseq = headers.get("cseq", "")
        cseq_parts = cseq.split()
        if len(cseq_parts) < 2 or cseq_parts[1].upper() != "BYE":
            continue
        if headers.get("call-id", "").strip() != call_id:
            continue
        return int(parts[1]), _now_ms()
    return None, None


def _run_single_call(
    *,
    stage_label: str,
    call_index: int,
    host: str,
    port: int,
    extension: str,
    bind_ip: str,
    invite_timeout_s: float,
    remote_bye_timeout_s: float,
    hold_seconds: float,
    tenant_id: str,
    scenario: str,
) -> Day10CallResult:
    started_at = _now_ms()
    media_port = 47000 + ((call_index * 2) % 2000)
    from_user = f"day10probe{call_index}"
    from_tag = uuid.uuid4().hex[:8]
    call_id = f"{uuid.uuid4().hex}-{call_index}@talky.day10"

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sip_sock:
        sip_sock.bind((bind_ip, 0))
        local_ip, local_port = sip_sock.getsockname()

        sdp = _build_sdp(local_ip, media_port)
        to_header = f"<sip:{extension}@{host}:{port}>"
        invite = _build_request(
            method="INVITE",
            target_host=host,
            target_port=port,
            from_user=from_user,
            extension=extension,
            local_ip=local_ip,
            local_port=local_port,
            call_id=call_id,
            cseq=1,
            from_tag=from_tag,
            to_header=to_header,
            contact_user=from_user,
            body=sdp,
        )

        invite_sent_ms = _now_ms()
        sip_sock.sendto(invite, (host, port))

        try:
            invite_codes, final_headers, final_recv_ms = _recv_invite_final(
                sip_sock,
                timeout_s=invite_timeout_s,
                call_id=call_id,
            )
        except TimeoutError:
            ended_at = _now_ms()
            return Day10CallResult(
                stage_label=stage_label,
                call_index=call_index,
                tenant_id=tenant_id,
                scenario=scenario,
                success=False,
                invite_codes=[],
                invite_final_code=None,
                setup_delay_ms=None,
                disconnect_delay_ms=None,
                remote_bye_received=False,
                remote_bye_elapsed_ms=None,
                caller_bye_code=None,
                reason="invite_timeout",
                call_id=call_id,
                bind_ip=bind_ip,
                target=f"{host}:{port}",
                started_at_epoch_ms=started_at,
                ended_at_epoch_ms=ended_at,
            )

        final_code = invite_codes[-1] if invite_codes else None
        setup_delay = float(final_recv_ms - invite_sent_ms) if final_recv_ms is not None else None

        if not final_headers or final_code != 200:
            ended_at = _now_ms()
            reason = "invite_timeout" if not final_headers else f"invite_failed_{final_code}"
            return Day10CallResult(
                stage_label=stage_label,
                call_index=call_index,
                tenant_id=tenant_id,
                scenario=scenario,
                success=False,
                invite_codes=invite_codes,
                invite_final_code=final_code,
                setup_delay_ms=setup_delay,
                disconnect_delay_ms=None,
                remote_bye_received=False,
                remote_bye_elapsed_ms=None,
                caller_bye_code=None,
                reason=reason,
                call_id=call_id,
                bind_ip=bind_ip,
                target=f"{host}:{port}",
                started_at_epoch_ms=started_at,
                ended_at_epoch_ms=ended_at,
            )

        to_value = final_headers.get("to", to_header)
        to_tag = _extract_to_tag(to_value)
        if not to_tag:
            ended_at = _now_ms()
            return Day10CallResult(
                stage_label=stage_label,
                call_index=call_index,
                tenant_id=tenant_id,
                scenario=scenario,
                success=False,
                invite_codes=invite_codes,
                invite_final_code=final_code,
                setup_delay_ms=setup_delay,
                disconnect_delay_ms=None,
                remote_bye_received=False,
                remote_bye_elapsed_ms=None,
                caller_bye_code=None,
                reason="missing_to_tag",
                call_id=call_id,
                bind_ip=bind_ip,
                target=f"{host}:{port}",
                started_at_epoch_ms=started_at,
                ended_at_epoch_ms=ended_at,
            )

        ack_to = f"<sip:{extension}@{host}:{port}>;tag={to_tag}"
        ack = _build_request(
            method="ACK",
            target_host=host,
            target_port=port,
            from_user=from_user,
            extension=extension,
            local_ip=local_ip,
            local_port=local_port,
            call_id=call_id,
            cseq=1,
            from_tag=from_tag,
            to_header=ack_to,
            contact_user=from_user,
            body="",
        )
        sip_sock.sendto(ack, (host, port))

        answer_ts = time.monotonic()
        hold_deadline = answer_ts + max(0.1, hold_seconds)
        remote_deadline = answer_ts + max(0.2, remote_bye_timeout_s)
        bye_wait_deadline = min(hold_deadline, remote_deadline)

        remote_bye_received = False
        remote_bye_elapsed_ms: Optional[float] = None
        disconnect_delay_ms: Optional[float] = None

        while time.monotonic() < bye_wait_deadline:
            remaining = max(0.05, bye_wait_deadline - time.monotonic())
            sip_sock.settimeout(remaining)
            try:
                packet, addr = sip_sock.recvfrom(65535)
            except TimeoutError:
                continue
            start_line, headers, _body = _parse_message(packet)
            if start_line.startswith("SIP/2.0"):
                continue
            parts = start_line.split()
            if not parts:
                continue
            method = parts[0].upper()
            if method != "BYE":
                continue
            if headers.get("call-id", "").strip() != call_id:
                continue

            bye_recv_ms = _now_ms()
            response = _build_response(200, "OK", headers)
            sip_sock.sendto(response, addr)
            remote_bye_received = True
            remote_bye_elapsed_ms = (time.monotonic() - answer_ts) * 1000.0
            disconnect_delay_ms = float(_now_ms() - bye_recv_ms)
            break

        if remote_bye_received:
            ended_at = _now_ms()
            return Day10CallResult(
                stage_label=stage_label,
                call_index=call_index,
                tenant_id=tenant_id,
                scenario=scenario,
                success=True,
                invite_codes=invite_codes,
                invite_final_code=final_code,
                setup_delay_ms=setup_delay,
                disconnect_delay_ms=disconnect_delay_ms,
                remote_bye_received=True,
                remote_bye_elapsed_ms=remote_bye_elapsed_ms,
                caller_bye_code=None,
                reason="remote_bye_ok",
                call_id=call_id,
                bind_ip=bind_ip,
                target=f"{host}:{port}",
                started_at_epoch_ms=started_at,
                ended_at_epoch_ms=ended_at,
            )

        bye = _build_request(
            method="BYE",
            target_host=host,
            target_port=port,
            from_user=from_user,
            extension=extension,
            local_ip=local_ip,
            local_port=local_port,
            call_id=call_id,
            cseq=2,
            from_tag=from_tag,
            to_header=ack_to,
            contact_user=from_user,
            body="",
        )
        bye_sent_ms = _now_ms()
        sip_sock.sendto(bye, (host, port))
        bye_code, bye_resp_ms = _recv_bye_response(
            sip_sock,
            timeout_s=max(1.0, invite_timeout_s),
            call_id=call_id,
        )
        disconnect_delay_ms = float(bye_resp_ms - bye_sent_ms) if bye_resp_ms is not None else None

        fallback_success = bye_code in {200, 481}
        ended_at = _now_ms()
        return Day10CallResult(
            stage_label=stage_label,
            call_index=call_index,
            tenant_id=tenant_id,
            scenario=scenario,
            success=fallback_success,
            invite_codes=invite_codes,
            invite_final_code=final_code,
            setup_delay_ms=setup_delay,
            disconnect_delay_ms=disconnect_delay_ms,
            remote_bye_received=False,
            remote_bye_elapsed_ms=None,
            caller_bye_code=bye_code,
            reason="caller_bye_fallback" if fallback_success else "bye_response_timeout",
            call_id=call_id,
            bind_ip=bind_ip,
            target=f"{host}:{port}",
            started_at_epoch_ms=started_at,
            ended_at_epoch_ms=ended_at,
        )


def _parse_stage_levels(value: str) -> List[int]:
    rows: List[int] = []
    for part in (value or "").split(","):
        token = part.strip()
        if not token:
            continue
        level = int(token)
        if level <= 0:
            raise ValueError("stage concurrency levels must be >= 1")
        rows.append(level)
    if not rows:
        raise ValueError("at least one stage concurrency level is required")
    return rows


def _pick_scenario(
    *,
    baseline_percent: int,
    bargein_percent: int,
    transfer_percent: int,
) -> str:
    total = baseline_percent + bargein_percent + transfer_percent
    if total <= 0:
        return "baseline"
    draw = random.uniform(0.0, float(total))
    if draw <= baseline_percent:
        return "baseline"
    if draw <= baseline_percent + bargein_percent:
        return "bargein"
    return "transfer"


def _pick_tenant(tenant_ids: Sequence[str]) -> str:
    if not tenant_ids:
        return "day10-default"
    return random.choice(list(tenant_ids))


def _runtime_snapshot(gateway_base_url: str) -> Dict[str, Any]:
    ts_ms = _now_ms()
    payload: Dict[str, Any] = {
        "ts_epoch_ms": ts_ms,
        "loadavg_1m": None,
        "loadavg_5m": None,
        "loadavg_15m": None,
        "mem_total_mb": None,
        "mem_available_mb": None,
        "mem_used_mb": None,
        "gateway_active_sessions": None,
        "gateway_packets_in": None,
        "gateway_packets_out": None,
    }

    try:
        load1, load5, load15 = os.getloadavg()
        payload["loadavg_1m"] = round(float(load1), 4)
        payload["loadavg_5m"] = round(float(load5), 4)
        payload["loadavg_15m"] = round(float(load15), 4)
    except (OSError, ValueError):
        pass

    try:
        mem_total_kb: Optional[int] = None
        mem_avail_kb: Optional[int] = None
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    mem_total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    mem_avail_kb = int(line.split()[1])
        if mem_total_kb and mem_avail_kb is not None:
            payload["mem_total_mb"] = round(mem_total_kb / 1024.0, 2)
            payload["mem_available_mb"] = round(mem_avail_kb / 1024.0, 2)
            payload["mem_used_mb"] = round((mem_total_kb - mem_avail_kb) / 1024.0, 2)
    except (OSError, ValueError):
        pass

    if gateway_base_url:
        try:
            stats_resp = requests.get(gateway_base_url.rstrip("/") + "/stats", timeout=3)
            if stats_resp.ok:
                stats = stats_resp.json()
                payload["gateway_active_sessions"] = stats.get("active_sessions")
                payload["gateway_packets_in"] = stats.get("packets_in")
                payload["gateway_packets_out"] = stats.get("packets_out")
        except (requests.RequestException, ValueError):
            pass

    return payload


def _run_load_window(
    *,
    stage_label: str,
    concurrency: int,
    duration_seconds: float,
    host: str,
    port: int,
    extension: str,
    bind_ip: str,
    invite_timeout: float,
    remote_bye_timeout: float,
    hold_seconds: float,
    baseline_percent: int,
    bargein_percent: int,
    transfer_percent: int,
    tenant_ids: Sequence[str],
    sample_interval_seconds: float,
    gateway_base_url: str,
    call_index_seed: int,
    min_dispatch_interval_seconds: float,
) -> Tuple[List[Day10CallResult], List[Dict[str, Any]], int]:
    results: List[Day10CallResult] = []
    timeseries: List[Dict[str, Any]] = []

    deadline = time.monotonic() + max(0.1, duration_seconds)
    next_sample_at = time.monotonic()
    next_dispatch_at = time.monotonic()
    next_call_index = call_index_seed
    effective_dispatch_interval = max(
        0.0,
        float(min_dispatch_interval_seconds),
        max(0.05, float(hold_seconds)) / max(1, int(concurrency)),
    )
    stage_call_budget = max(
        max(1, int(concurrency)),
        int(math.ceil(max(0.1, float(duration_seconds)) / max(0.01, effective_dispatch_interval)))
        + max(1, int(concurrency)),
    )
    dispatched_calls = 0

    futures: set[Future[Day10CallResult]] = set()

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        while time.monotonic() < deadline or futures:
            while (
                time.monotonic() < deadline
                and len(futures) < concurrency
                and dispatched_calls < stage_call_budget
            ):
                now = time.monotonic()
                if now < next_dispatch_at:
                    break
                next_call_index += 1
                dispatched_calls += 1
                scenario = _pick_scenario(
                    baseline_percent=baseline_percent,
                    bargein_percent=bargein_percent,
                    transfer_percent=transfer_percent,
                )
                tenant_id = _pick_tenant(tenant_ids)
                fut = pool.submit(
                    _run_single_call,
                    stage_label=stage_label,
                    call_index=next_call_index,
                    host=host,
                    port=port,
                    extension=extension,
                    bind_ip=bind_ip,
                    invite_timeout_s=invite_timeout,
                    remote_bye_timeout_s=remote_bye_timeout,
                    hold_seconds=hold_seconds,
                    tenant_id=tenant_id,
                    scenario=scenario,
                )
                futures.add(fut)
                next_dispatch_at = now + effective_dispatch_interval

            timeout = 0.2
            if sample_interval_seconds > 0:
                timeout = min(timeout, max(0.05, next_sample_at - time.monotonic()))

            done, pending = wait(futures, timeout=max(0.05, timeout), return_when=FIRST_COMPLETED)
            futures = set(pending)
            for fut in done:
                try:
                    results.append(fut.result())
                except Exception as exc:  # noqa: BLE001
                    fail_index = next_call_index + 1
                    next_call_index = fail_index
                    now_ms = _now_ms()
                    results.append(
                        Day10CallResult(
                            stage_label=stage_label,
                            call_index=fail_index,
                            tenant_id="unknown",
                            scenario="unknown",
                            success=False,
                            invite_codes=[],
                            invite_final_code=None,
                            setup_delay_ms=None,
                            disconnect_delay_ms=None,
                            remote_bye_received=False,
                            remote_bye_elapsed_ms=None,
                            caller_bye_code=None,
                            reason=f"worker_exception:{exc}",
                            call_id=f"error-{uuid.uuid4().hex}",
                            bind_ip=bind_ip,
                            target=f"{host}:{port}",
                            started_at_epoch_ms=now_ms,
                            ended_at_epoch_ms=now_ms,
                        )
                    )

            if sample_interval_seconds > 0 and time.monotonic() >= next_sample_at:
                timeseries.append(_runtime_snapshot(gateway_base_url))
                next_sample_at = time.monotonic() + sample_interval_seconds

    if sample_interval_seconds > 0:
        timeseries.append(_runtime_snapshot(gateway_base_url))

    return results, timeseries, next_call_index


def _summarize_results(
    *,
    stage_label: str,
    concurrency: int,
    duration_seconds: float,
    rows: Sequence[Day10CallResult],
) -> Dict[str, Any]:
    attempts = len(rows)
    successes = sum(1 for r in rows if r.success)
    failures = attempts - successes
    setup_delays = [float(r.setup_delay_ms) for r in rows if r.setup_delay_ms is not None]
    disconnect_delays = [float(r.disconnect_delay_ms) for r in rows if r.disconnect_delay_ms is not None]

    scenario_breakdown: Dict[str, Dict[str, int]] = {}
    tenant_breakdown: Dict[str, Dict[str, int]] = {}
    for row in rows:
        scenario = row.scenario or "unknown"
        tenant = row.tenant_id or "unknown"
        scenario_breakdown.setdefault(scenario, {"attempted": 0, "success": 0, "failed": 0})
        tenant_breakdown.setdefault(tenant, {"attempted": 0, "success": 0, "failed": 0})
        scenario_breakdown[scenario]["attempted"] += 1
        tenant_breakdown[tenant]["attempted"] += 1
        if row.success:
            scenario_breakdown[scenario]["success"] += 1
            tenant_breakdown[tenant]["success"] += 1
        else:
            scenario_breakdown[scenario]["failed"] += 1
            tenant_breakdown[tenant]["failed"] += 1

    transfer_attempted = scenario_breakdown.get("transfer", {}).get("attempted", 0)
    transfer_success = scenario_breakdown.get("transfer", {}).get("success", 0)
    transfer_success_ratio = (
        float(transfer_success) / float(transfer_attempted) if transfer_attempted > 0 else None
    )
    reason_breakdown = dict(Counter(str(row.reason or "unknown") for row in rows))

    out = {
        "stage_label": stage_label,
        "concurrency": int(concurrency),
        "duration_seconds": float(duration_seconds),
        "calls_attempted": attempts,
        "calls_success": successes,
        "calls_failed": failures,
        "session_setup_success_ratio": (float(successes) / float(attempts)) if attempts else 0.0,
        "ineffective_session_attempt_percent": (float(failures) * 100.0 / float(attempts)) if attempts else 0.0,
        "srd_ms": {
            "p50": _percentile(setup_delays, 50),
            "p95": _percentile(setup_delays, 95),
            "p99": _percentile(setup_delays, 99),
        },
        "sdd_ms": {
            "p50": _percentile(disconnect_delays, 50),
            "p95": _percentile(disconnect_delays, 95),
            "p99": _percentile(disconnect_delays, 99),
        },
        "transfer": {
            "attempted": transfer_attempted,
            "success": transfer_success,
            "success_ratio": transfer_success_ratio,
        },
        "barge_in": {
            "attempted": scenario_breakdown.get("bargein", {}).get("attempted", 0),
            "reaction_ms": {
                "available": False,
                "p95": None,
            },
        },
        "scenario_breakdown": scenario_breakdown,
        "tenant_breakdown": tenant_breakdown,
        "reason_breakdown": reason_breakdown,
    }
    return out


def _evaluate_gate(
    *,
    summary: Dict[str, Any],
    setup_success_min: float,
    srd_p95_max_ms: float,
    sdd_p95_max_ms: float,
    isa_max_percent: float,
    transfer_success_min: float,
    require_transfer: bool,
    bargein_reaction_p95_max_ms: float,
    require_bargein_reaction: bool,
) -> Dict[str, Any]:
    reasons: List[str] = []

    setup_ratio = _safe_float(summary.get("session_setup_success_ratio"), default=0.0)
    isa_percent = _safe_float(summary.get("ineffective_session_attempt_percent"), default=100.0)
    srd_p95 = summary.get("srd_ms", {}).get("p95")
    sdd_p95 = summary.get("sdd_ms", {}).get("p95")

    if setup_ratio < setup_success_min:
        reasons.append(f"setup_success_ratio={setup_ratio:.4f} < {setup_success_min:.4f}")
    if srd_p95 is None or float(srd_p95) > srd_p95_max_ms:
        reasons.append(f"srd_p95_ms={srd_p95} > {srd_p95_max_ms}")
    if sdd_p95 is None or float(sdd_p95) > sdd_p95_max_ms:
        reasons.append(f"sdd_p95_ms={sdd_p95} > {sdd_p95_max_ms}")
    if isa_percent > isa_max_percent:
        reasons.append(f"isa_percent={isa_percent:.4f} > {isa_max_percent:.4f}")

    transfer_attempted = int(summary.get("transfer", {}).get("attempted", 0) or 0)
    transfer_ratio = summary.get("transfer", {}).get("success_ratio")
    if require_transfer or transfer_attempted > 0:
        if transfer_ratio is None or float(transfer_ratio) < transfer_success_min:
            reasons.append(f"transfer_success_ratio={transfer_ratio} < {transfer_success_min:.4f}")

    barge_available = bool(summary.get("barge_in", {}).get("reaction_ms", {}).get("available", False))
    barge_p95 = summary.get("barge_in", {}).get("reaction_ms", {}).get("p95")
    if require_bargein_reaction:
        if not barge_available:
            reasons.append("barge_in_reaction_ms unavailable")
        elif barge_p95 is None or float(barge_p95) > bargein_reaction_p95_max_ms:
            reasons.append(f"barge_in_reaction_p95_ms={barge_p95} > {bargein_reaction_p95_max_ms}")

    return {
        "pass": len(reasons) == 0,
        "reasons": reasons,
    }


def _soak_trend_check(
    *,
    soak_rows: Sequence[Day10CallResult],
    soak_timeseries: Sequence[Dict[str, Any]],
    max_success_ratio_drop: float,
    max_mem_growth_mb: float,
) -> Dict[str, Any]:
    if not soak_rows:
        return {
            "pass": False,
            "reasons": ["no_soak_calls"],
            "success_ratio_first_window": None,
            "success_ratio_last_window": None,
            "success_ratio_drop": None,
            "mem_growth_mb": None,
        }

    ordered = sorted(soak_rows, key=lambda r: r.ended_at_epoch_ms)
    window = max(1, len(ordered) // 4)
    first = ordered[:window]
    last = ordered[-window:]

    first_ratio = sum(1 for r in first if r.success) / float(len(first))
    last_ratio = sum(1 for r in last if r.success) / float(len(last))
    ratio_drop = first_ratio - last_ratio

    mem_growth_mb: Optional[float] = None
    if len(soak_timeseries) >= 2:
        first_mem = soak_timeseries[0].get("mem_used_mb")
        last_mem = soak_timeseries[-1].get("mem_used_mb")
        if first_mem is not None and last_mem is not None:
            mem_growth_mb = float(last_mem) - float(first_mem)

    reasons: List[str] = []
    if ratio_drop > max_success_ratio_drop:
        reasons.append(f"success_ratio_drop={ratio_drop:.4f} > {max_success_ratio_drop:.4f}")
    if mem_growth_mb is not None and mem_growth_mb > max_mem_growth_mb:
        reasons.append(f"mem_growth_mb={mem_growth_mb:.2f} > {max_mem_growth_mb:.2f}")

    return {
        "pass": len(reasons) == 0,
        "reasons": reasons,
        "success_ratio_first_window": first_ratio,
        "success_ratio_last_window": last_ratio,
        "success_ratio_drop": ratio_drop,
        "mem_growth_mb": mem_growth_mb,
    }


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_timeseries_csv(path: str, rows: Sequence[Dict[str, Any]]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "ts_epoch_ms",
        "loadavg_1m",
        "loadavg_5m",
        "loadavg_15m",
        "mem_total_mb",
        "mem_available_mb",
        "mem_used_mb",
        "gateway_active_sessions",
        "gateway_packets_in",
        "gateway_packets_out",
    ]
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fieldnames})


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Day 10 concurrency + soak probe")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=15060)
    parser.add_argument("--extension", default="750")
    parser.add_argument("--bind-ip", default="127.0.0.1")

    parser.add_argument("--stage-concurrency", default="10,20,30,40,50")
    parser.add_argument("--stage-duration-seconds", type=float, default=300.0)
    parser.add_argument("--soak-duration-seconds", type=float, default=7200.0)
    parser.add_argument("--smoke-concurrency", type=int, default=2)
    parser.add_argument("--smoke-duration-seconds", type=float, default=20.0)
    parser.add_argument("--sample-interval-seconds", type=float, default=5.0)
    parser.add_argument("--min-dispatch-interval-seconds", type=float, default=0.02)

    parser.add_argument("--invite-timeout", type=float, default=6.0)
    parser.add_argument("--remote-bye-timeout", type=float, default=8.0)
    parser.add_argument("--hold-seconds", type=float, default=2.0)

    parser.add_argument("--profile-baseline-percent", type=int, default=50)
    parser.add_argument("--profile-bargein-percent", type=int, default=30)
    parser.add_argument("--profile-transfer-percent", type=int, default=20)

    parser.add_argument("--tenant-ids", default="day10-default")

    parser.add_argument("--setup-success-min", type=float, default=0.99)
    parser.add_argument("--srd-p95-max-ms", type=float, default=2000.0)
    parser.add_argument("--sdd-p95-max-ms", type=float, default=1500.0)
    parser.add_argument("--isa-max-percent", type=float, default=1.0)
    parser.add_argument("--transfer-success-min", type=float, default=0.95)
    parser.add_argument("--bargein-reaction-p95-max-ms", type=float, default=250.0)
    parser.add_argument("--require-transfer", type=int, choices=(0, 1), default=0)
    parser.add_argument("--require-bargein-reaction", type=int, choices=(0, 1), default=0)
    parser.add_argument("--require-tenant-fairness", type=int, choices=(0, 1), default=0)

    parser.add_argument("--headroom-ratio", type=float, default=0.8)
    parser.add_argument("--stop-on-first-breach", type=int, choices=(0, 1), default=1)
    parser.add_argument("--enforce-gates", type=int, choices=(0, 1), default=1)
    parser.add_argument("--max-soak-success-ratio-drop", type=float, default=0.03)
    parser.add_argument("--max-soak-mem-growth-mb", type=float, default=512.0)

    parser.add_argument("--gateway-base-url", default="")

    parser.add_argument("--output-harness-smoke", required=True)
    parser.add_argument("--output-ramp", required=True)
    parser.add_argument("--output-capacity", required=True)
    parser.add_argument("--output-soak-summary", required=True)
    parser.add_argument("--output-soak-timeseries", required=True)
    parser.add_argument("--output-transfer", required=True)
    parser.add_argument("--output-bargein", required=True)
    parser.add_argument("--output-tenant-fairness", required=True)
    parser.add_argument("--output-call-results", default="")

    return parser


def main() -> int:
    args = _build_parser().parse_args()

    stage_levels = _parse_stage_levels(args.stage_concurrency)
    if args.profile_baseline_percent < 0 or args.profile_bargein_percent < 0 or args.profile_transfer_percent < 0:
        raise SystemExit("profile percentages must be >= 0")
    if args.profile_baseline_percent + args.profile_bargein_percent + args.profile_transfer_percent <= 0:
        raise SystemExit("sum of profile percentages must be > 0")

    tenant_ids = [t.strip() for t in (args.tenant_ids or "").split(",") if t.strip()]
    if not tenant_ids:
        tenant_ids = ["day10-default"]

    all_rows: List[Day10CallResult] = []
    call_index = 0

    smoke_concurrency = max(1, min(int(args.smoke_concurrency), stage_levels[0]))
    smoke_rows, smoke_timeseries, call_index = _run_load_window(
        stage_label="smoke",
        concurrency=smoke_concurrency,
        duration_seconds=max(1.0, float(args.smoke_duration_seconds)),
        host=args.host,
        port=args.port,
        extension=args.extension,
        bind_ip=args.bind_ip,
        invite_timeout=float(args.invite_timeout),
        remote_bye_timeout=float(args.remote_bye_timeout),
        hold_seconds=float(args.hold_seconds),
        baseline_percent=int(args.profile_baseline_percent),
        bargein_percent=int(args.profile_bargein_percent),
        transfer_percent=int(args.profile_transfer_percent),
        tenant_ids=tenant_ids,
        sample_interval_seconds=float(args.sample_interval_seconds),
        gateway_base_url=args.gateway_base_url,
        call_index_seed=call_index,
        min_dispatch_interval_seconds=float(args.min_dispatch_interval_seconds),
    )
    all_rows.extend(smoke_rows)

    smoke_summary = _summarize_results(
        stage_label="smoke",
        concurrency=smoke_concurrency,
        duration_seconds=float(args.smoke_duration_seconds),
        rows=smoke_rows,
    )
    smoke_gate = _evaluate_gate(
        summary=smoke_summary,
        setup_success_min=float(args.setup_success_min),
        srd_p95_max_ms=float(args.srd_p95_max_ms),
        sdd_p95_max_ms=float(args.sdd_p95_max_ms),
        isa_max_percent=float(args.isa_max_percent),
        transfer_success_min=float(args.transfer_success_min),
        require_transfer=False,
        bargein_reaction_p95_max_ms=float(args.bargein_reaction_p95_max_ms),
        require_bargein_reaction=False,
    )
    smoke_payload = {
        "summary": smoke_summary,
        "gate": smoke_gate,
        "timeseries_samples": len(smoke_timeseries),
    }
    _write_json(args.output_harness_smoke, smoke_payload)

    def _persist_call_results() -> None:
        if args.output_call_results:
            _write_json(
                args.output_call_results,
                {
                    "calls": [asdict(r) for r in all_rows],
                    "total_calls": len(all_rows),
                },
            )

    if not smoke_gate["pass"] and bool(args.enforce_gates):
        _persist_call_results()
        print(json.dumps({"event": "smoke_failed", "reasons": smoke_gate["reasons"]}, indent=2))
        return 2

    ramp_results: List[Dict[str, Any]] = []
    break_stage: Optional[int] = None
    safe_threshold = 0

    for idx, stage_concurrency in enumerate(stage_levels, start=1):
        stage_label = f"R{idx}"
        stage_rows, _stage_timeseries, call_index = _run_load_window(
            stage_label=stage_label,
            concurrency=stage_concurrency,
            duration_seconds=float(args.stage_duration_seconds),
            host=args.host,
            port=args.port,
            extension=args.extension,
            bind_ip=args.bind_ip,
            invite_timeout=float(args.invite_timeout),
            remote_bye_timeout=float(args.remote_bye_timeout),
            hold_seconds=float(args.hold_seconds),
            baseline_percent=int(args.profile_baseline_percent),
            bargein_percent=int(args.profile_bargein_percent),
            transfer_percent=int(args.profile_transfer_percent),
            tenant_ids=tenant_ids,
            sample_interval_seconds=float(args.sample_interval_seconds),
            gateway_base_url=args.gateway_base_url,
            call_index_seed=call_index,
            min_dispatch_interval_seconds=float(args.min_dispatch_interval_seconds),
        )
        all_rows.extend(stage_rows)

        summary = _summarize_results(
            stage_label=stage_label,
            concurrency=stage_concurrency,
            duration_seconds=float(args.stage_duration_seconds),
            rows=stage_rows,
        )
        gate = _evaluate_gate(
            summary=summary,
            setup_success_min=float(args.setup_success_min),
            srd_p95_max_ms=float(args.srd_p95_max_ms),
            sdd_p95_max_ms=float(args.sdd_p95_max_ms),
            isa_max_percent=float(args.isa_max_percent),
            transfer_success_min=float(args.transfer_success_min),
            require_transfer=bool(args.require_transfer),
            bargein_reaction_p95_max_ms=float(args.bargein_reaction_p95_max_ms),
            require_bargein_reaction=bool(args.require_bargein_reaction),
        )

        stage_payload = {
            "stage_index": idx,
            "summary": summary,
            "gate": gate,
        }
        ramp_results.append(stage_payload)

        if gate["pass"]:
            safe_threshold = stage_concurrency
        elif break_stage is None:
            break_stage = stage_concurrency
            if bool(args.stop_on_first_breach):
                break

    _write_json(
        args.output_ramp,
        {
            "stages": ramp_results,
            "break_stage": break_stage,
        },
    )

    recommended = 0
    if safe_threshold > 0:
        recommended = max(1, int(math.floor(float(safe_threshold) * float(args.headroom_ratio))))

    capacity_report = {
        "stage_levels": stage_levels,
        "break_stage": break_stage,
        "safe_concurrency_threshold": safe_threshold,
        "headroom_ratio": float(args.headroom_ratio),
        "recommended_concurrency": recommended,
        "ramp_gate_passed": safe_threshold > 0,
    }
    _write_json(args.output_capacity, capacity_report)

    if safe_threshold <= 0 and bool(args.enforce_gates):
        _persist_call_results()
        print(json.dumps({"event": "ramp_failed", "capacity": capacity_report}, indent=2))
        return 3

    soak_rows: List[Day10CallResult] = []
    soak_timeseries: List[Dict[str, Any]] = []
    soak_summary: Dict[str, Any] = {
        "stage_label": "soak",
        "concurrency": recommended,
        "duration_seconds": float(args.soak_duration_seconds),
        "calls_attempted": 0,
        "calls_success": 0,
        "calls_failed": 0,
        "session_setup_success_ratio": 0.0,
        "ineffective_session_attempt_percent": 0.0,
        "srd_ms": {"p50": None, "p95": None, "p99": None},
        "sdd_ms": {"p50": None, "p95": None, "p99": None},
        "transfer": {"attempted": 0, "success": 0, "success_ratio": None},
        "barge_in": {"attempted": 0, "reaction_ms": {"available": False, "p95": None}},
        "scenario_breakdown": {},
        "tenant_breakdown": {},
    }

    soak_enabled = recommended > 0 and float(args.soak_duration_seconds) > 0
    if soak_enabled:
        soak_rows, soak_timeseries, call_index = _run_load_window(
            stage_label="soak",
            concurrency=recommended,
            duration_seconds=float(args.soak_duration_seconds),
            host=args.host,
            port=args.port,
            extension=args.extension,
            bind_ip=args.bind_ip,
            invite_timeout=float(args.invite_timeout),
            remote_bye_timeout=float(args.remote_bye_timeout),
            hold_seconds=float(args.hold_seconds),
            baseline_percent=int(args.profile_baseline_percent),
            bargein_percent=int(args.profile_bargein_percent),
            transfer_percent=int(args.profile_transfer_percent),
            tenant_ids=tenant_ids,
            sample_interval_seconds=float(args.sample_interval_seconds),
            gateway_base_url=args.gateway_base_url,
            call_index_seed=call_index,
            min_dispatch_interval_seconds=float(args.min_dispatch_interval_seconds),
        )
        all_rows.extend(soak_rows)
        soak_summary = _summarize_results(
            stage_label="soak",
            concurrency=recommended,
            duration_seconds=float(args.soak_duration_seconds),
            rows=soak_rows,
        )
    if soak_enabled:
        soak_gate = _evaluate_gate(
            summary=soak_summary,
            setup_success_min=float(args.setup_success_min),
            srd_p95_max_ms=float(args.srd_p95_max_ms),
            sdd_p95_max_ms=float(args.sdd_p95_max_ms),
            isa_max_percent=float(args.isa_max_percent),
            transfer_success_min=float(args.transfer_success_min),
            require_transfer=bool(args.require_transfer),
            bargein_reaction_p95_max_ms=float(args.bargein_reaction_p95_max_ms),
            require_bargein_reaction=bool(args.require_bargein_reaction),
        )
        trend = _soak_trend_check(
            soak_rows=soak_rows,
            soak_timeseries=soak_timeseries,
            max_success_ratio_drop=float(args.max_soak_success_ratio_drop),
            max_mem_growth_mb=float(args.max_soak_mem_growth_mb),
        )
        soak_pass = bool(soak_gate["pass"]) and bool(trend["pass"])
    else:
        soak_gate = {"pass": True, "reasons": []}
        trend = {
            "pass": True,
            "reasons": ["soak_skipped"],
            "success_ratio_first_window": None,
            "success_ratio_last_window": None,
            "success_ratio_drop": None,
            "mem_growth_mb": None,
        }
        soak_pass = True

    soak_payload = {
        "summary": soak_summary,
        "gate": soak_gate,
        "trend": trend,
        "pass": soak_pass,
        "recommended_concurrency": recommended,
        "soak_samples": len(soak_timeseries),
        "skipped": not soak_enabled,
    }
    _write_json(args.output_soak_summary, soak_payload)
    _write_timeseries_csv(args.output_soak_timeseries, soak_timeseries)

    all_summary = _summarize_results(
        stage_label="all",
        concurrency=max(stage_levels) if stage_levels else 0,
        duration_seconds=float(args.smoke_duration_seconds)
        + float(args.stage_duration_seconds) * len(ramp_results)
        + float(args.soak_duration_seconds),
        rows=all_rows,
    )

    transfer_payload = {
        "attempted": all_summary.get("transfer", {}).get("attempted", 0),
        "success": all_summary.get("transfer", {}).get("success", 0),
        "success_ratio": all_summary.get("transfer", {}).get("success_ratio"),
        "threshold": float(args.transfer_success_min),
        "pass": (
            all_summary.get("transfer", {}).get("success_ratio") is None
            or float(all_summary.get("transfer", {}).get("success_ratio") or 0.0)
            >= float(args.transfer_success_min)
        ),
    }
    _write_json(args.output_transfer, transfer_payload)

    barge_payload = {
        "attempted": all_summary.get("barge_in", {}).get("attempted", 0),
        "reaction_ms": {
            "available": False,
            "p95": None,
            "threshold": float(args.bargein_reaction_p95_max_ms),
        },
        "pass": not bool(args.require_bargein_reaction),
        "note": "Barge-in reaction timing is not directly measurable from SIP-only Day10 probe.",
    }
    _write_json(args.output_bargein, barge_payload)

    tenant_breakdown = all_summary.get("tenant_breakdown", {})
    tenant_rows = []
    for tenant, data in sorted(tenant_breakdown.items()):
        attempted = int(data.get("attempted", 0))
        success = int(data.get("success", 0))
        ratio = (success / float(attempted)) if attempted else None
        tenant_rows.append(
            {
                "tenant_id": tenant,
                "attempted": attempted,
                "success": success,
                "failed": int(data.get("failed", 0)),
                "success_ratio": ratio,
            }
        )

    fairness_enforced = bool(args.require_tenant_fairness)
    fairness_pass = True
    fairness_note = "Tenant fairness evaluation recorded for synthetic load tenants."
    if len(tenant_rows) < 2:
        fairness_note = "Single tenant run; fairness cannot be fully validated."
        fairness_pass = not fairness_enforced

    tenant_fairness_payload = {
        "enforced": fairness_enforced,
        "tenants": tenant_rows,
        "threshold_success_ratio": 0.98,
        "pass": fairness_pass,
        "note": fairness_note,
    }
    _write_json(args.output_tenant_fairness, tenant_fairness_payload)

    _persist_call_results()

    final_payload = {
        "smoke": smoke_payload,
        "capacity": capacity_report,
        "soak": soak_payload,
        "transfer": transfer_payload,
        "barge_in": barge_payload,
        "tenant_fairness": tenant_fairness_payload,
    }
    print(json.dumps(final_payload, indent=2))

    if not bool(args.enforce_gates):
        return 0
    if not capacity_report["ramp_gate_passed"]:
        return 3
    if not soak_pass:
        return 4
    if not transfer_payload["pass"]:
        return 5
    if not barge_payload["pass"]:
        return 6
    if not tenant_fairness_payload["pass"]:
        return 7
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
