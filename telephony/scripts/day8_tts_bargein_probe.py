#!/usr/bin/env python3
"""Day 8 TTS + barge-in probe.

Runs SIP calls over the Day 5/6/7 telephony route, injects TTS playback frames
through the C++ gateway, triggers controlled barge-in interruption, and records
reaction-time evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import random
import re
import socket
import struct
import time
import uuid
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import numpy as np
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
import sys

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.models.conversation import AudioChunk, BargeInSignal
from app.infrastructure.stt.deepgram_flux import (
    DeepgramFluxSTTProvider,
    FLUX_OPTIMAL_CHUNK_BYTES,
    FLUX_OPTIMAL_CHUNK_MS,
)
from app.infrastructure.tts.deepgram_tts import DeepgramTTSProvider
from app.utils.audio_utils import pcm_to_ulaw, ulaw_to_pcm

TO_TAG_RE = re.compile(r";\s*tag=([^;>\s]+)", re.IGNORECASE)


@dataclass
class Day8CallResult:
    batch_index: int
    call_index: int
    scenario: str
    sip_call_id: str
    session_id: str
    sip_success: bool
    tts_playback_success: bool
    barge_in_success: bool
    start_of_turn_detected: bool
    tts_first_packet_ms: Optional[float]
    barge_in_reaction_ms: Optional[float]
    tts_packets_total: int
    tts_packets_after_trigger: int
    interrupt_reason: str
    gateway_tts_stop_reason: str
    reason: str


class ProbeError(RuntimeError):
    pass


def _read_wav_pcm16(path: Path) -> Tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sample_rate = wf.getframerate()
        sample_width = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())

    if sample_width != 2:
        raise ValueError(f"Unsupported WAV sample width: {sample_width}")

    samples = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return samples, sample_rate


def _resample_pcm16(samples: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    if from_rate == to_rate:
        return samples.astype(np.int16, copy=False)
    if samples.size == 0:
        return samples.astype(np.int16, copy=False)

    src_times = np.arange(samples.shape[0], dtype=np.float64) / float(from_rate)
    duration = src_times[-1] if src_times.size > 1 else 0.0
    dst_len = max(1, int(round((samples.shape[0] / float(from_rate)) * float(to_rate))))
    dst_times = np.linspace(0.0, duration, num=dst_len, endpoint=(src_times.size == 1))
    resampled = np.interp(dst_times, src_times, samples.astype(np.float64))
    return np.clip(resampled, -32768, 32767).astype(np.int16)


def _prepare_ulaw_payloads(audio_path: Path) -> List[bytes]:
    samples, sample_rate = _read_wav_pcm16(audio_path)
    samples_8k = _resample_pcm16(samples, sample_rate, 8000)
    ulaw = pcm_to_ulaw(samples_8k.tobytes())

    frame_size = 160  # 20 ms @ 8 kHz PCMU
    payloads: List[bytes] = []
    for idx in range(0, len(ulaw), frame_size):
        payload = ulaw[idx : idx + frame_size]
        if len(payload) < frame_size:
            payload += bytes(frame_size - len(payload))
        payloads.append(payload)

    if not payloads:
        payloads.append(bytes([0xFF] * frame_size))
    return payloads


def _pcm16_8k_to_ulaw_frames(pcm16_8k: bytes) -> List[bytes]:
    ulaw = pcm_to_ulaw(pcm16_8k)
    frame_size = 160
    frames: List[bytes] = []
    for idx in range(0, len(ulaw), frame_size):
        payload = ulaw[idx : idx + frame_size]
        if len(payload) < frame_size:
            payload += bytes(frame_size - len(payload))
        frames.append(payload)
    if not frames:
        frames.append(bytes([0xFF] * frame_size))
    return frames


def _ulaw_to_pcm16_16k(ulaw_audio: bytes) -> bytes:
    pcm_8k = ulaw_to_pcm(ulaw_audio)
    samples_8k = np.frombuffer(pcm_8k, dtype=np.int16)
    samples_16k = _resample_pcm16(samples_8k, 8000, 16000)
    return samples_16k.tobytes()


def _build_sdp(bind_ip: str, media_port: int) -> str:
    return (
        "v=0\r\n"
        f"o=- 0 0 IN IP4 {bind_ip}\r\n"
        "s=Talky Day8 Probe\r\n"
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
        "User-Agent: talky-day8-tts-bargein-probe/1.0",
    ]
    if body:
        lines.append("Content-Type: application/sdp")
    lines.append(f"Content-Length: {len(body.encode('utf-8'))}")
    return ("\r\n".join(lines) + "\r\n\r\n" + body).encode("utf-8")


def _parse_response(raw: bytes) -> Tuple[int, Dict[str, str], str]:
    text = raw.decode("utf-8", errors="ignore")
    head, _, body = text.partition("\r\n\r\n")
    lines = head.split("\r\n")
    if not lines:
        raise ValueError("invalid SIP response")
    first = lines[0].split()
    if len(first) < 2 or first[0] != "SIP/2.0":
        raise ValueError("invalid SIP response start-line")

    code = int(first[1])
    headers: Dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return code, headers, body


def _recv_until_final(
    sock: socket.socket,
    *,
    timeout_s: float,
    expected_method: str,
    call_id: str,
) -> Tuple[List[int], Optional[Dict[str, str]], str]:
    deadline = time.monotonic() + timeout_s
    seen_codes: List[int] = []
    final_headers: Optional[Dict[str, str]] = None
    final_body = ""

    while time.monotonic() < deadline:
        remaining = max(0.05, deadline - time.monotonic())
        sock.settimeout(remaining)
        data, _ = sock.recvfrom(65535)
        code, headers, body = _parse_response(data)

        cseq_value = headers.get("cseq", "")
        cseq_parts = cseq_value.split()
        if len(cseq_parts) < 2:
            continue
        if cseq_parts[1].upper() != expected_method.upper():
            continue
        if headers.get("call-id", "").strip() != call_id:
            continue

        seen_codes.append(code)
        if code >= 200:
            final_headers = headers
            final_body = body
            break

    return seen_codes, final_headers, final_body


def _extract_to_tag(to_header: str) -> Optional[str]:
    match = TO_TAG_RE.search(to_header)
    return match.group(1).strip() if match else None


def _parse_sdp_target(default_host: str, sdp_body: str) -> Tuple[str, int]:
    target_ip = default_host
    target_port: Optional[int] = None
    for raw_line in sdp_body.splitlines():
        line = raw_line.strip()
        if line.startswith("c=IN IP4 "):
            target_ip = line.split()[-1]
        elif line.startswith("m=audio "):
            parts = line.split()
            if len(parts) >= 2:
                target_port = int(parts[1])
    if not target_port or target_port <= 0:
        raise ValueError("missing media port in SDP answer")
    return target_ip, target_port


def _build_rtp_packet(*, seq: int, ts: int, ssrc: int, payload: bytes) -> bytes:
    header = struct.pack("!BBHII", 0x80, 0x00, seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc & 0xFFFFFFFF)
    return header + payload


def _extract_ulaw_payload(packet: bytes) -> Optional[bytes]:
    if len(packet) < 12:
        return None
    version = (packet[0] >> 6) & 0x03
    payload_type = packet[1] & 0x7F
    if version != 2 or payload_type != 0:
        return None
    return packet[12:]


def _gateway_get(base_url: str, path: str) -> Dict[str, Any]:
    resp = requests.get(base_url.rstrip("/") + path, timeout=5)
    resp.raise_for_status()
    return resp.json() if resp.text else {}


def _gateway_post(base_url: str, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    resp = requests.post(base_url.rstrip("/") + path, json=payload, timeout=5)
    resp.raise_for_status()
    return resp.json() if resp.text else {}


def _wait_for_active_session(base_url: str, timeout_s: float, *, exclude_session_ids: Optional[Set[str]] = None) -> str:
    excluded = exclude_session_ids or set()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        data = _gateway_get(base_url, "/v1/sessions")
        sessions = data.get("sessions") or []
        active = [
            str(s.get("session_id") or "")
            for s in sessions
            if str(s.get("state") or "") != "stopped"
        ]
        candidates = [sid for sid in active if sid and sid not in excluded]
        if len(candidates) == 1:
            return candidates[0]
        time.sleep(0.1)
    raise ProbeError("active gateway session not found")


def _gateway_play_tts(base_url: str, session_id: str, ulaw_audio: bytes, clear_existing: bool = True) -> Dict[str, Any]:
    b64 = base64.b64encode(ulaw_audio).decode("ascii")
    return _gateway_post(
        base_url,
        "/v1/sessions/tts/play",
        {
            "session_id": session_id,
            "pcmu_base64": b64,
            "clear_existing": bool(clear_existing),
        },
    )


def _gateway_interrupt_tts(base_url: str, session_id: str, reason: str) -> Dict[str, Any]:
    return _gateway_post(
        base_url,
        "/v1/sessions/tts/interrupt",
        {"session_id": session_id, "reason": reason},
    )


def _percentile(values: List[float], p: int) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if p <= 0:
        return ordered[0]
    if p >= 100:
        return ordered[-1]
    rank = (p / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * frac


async def _synthesize_tts_ulaw(api_key: str, voice: str, text: str) -> bytes:
    provider = DeepgramTTSProvider()
    try:
        await provider.initialize({"api_key": api_key, "voice_id": voice, "sample_rate": 8000})
        pcm16 = await provider.synthesize_raw(text=text, voice_id=voice, sample_rate=8000)
        return pcm_to_ulaw(pcm16)
    finally:
        await provider.cleanup()


async def _detect_start_of_turn(api_key: str, ulaw_audio: bytes) -> bool:
    provider = DeepgramFluxSTTProvider()
    await provider.initialize(
        {
            "api_key": api_key,
            "model": "flux-general-en",
            "sample_rate": 16000,
            "encoding": "linear16",
            "eot_threshold": 0.7,
            "eot_timeout_ms": 5000,
        }
    )
    pcm16_16k = _ulaw_to_pcm16_16k(ulaw_audio)
    chunks = [
        pcm16_16k[i : i + FLUX_OPTIMAL_CHUNK_BYTES]
        for i in range(0, len(pcm16_16k), FLUX_OPTIMAL_CHUNK_BYTES)
        if pcm16_16k[i : i + FLUX_OPTIMAL_CHUNK_BYTES]
    ]
    if not chunks:
        await provider.cleanup()
        return False

    async def audio_stream():
        for chunk in chunks:
            yield AudioChunk(data=chunk, sample_rate=16000, channels=1)
            await asyncio.sleep(FLUX_OPTIMAL_CHUNK_MS / 1000.0)

    detected = False
    try:
        async def _collect():
            nonlocal detected
            async for item in provider.stream_transcribe(audio_stream(), call_id=f"day8-{uuid.uuid4().hex[:8]}"):
                if isinstance(item, BargeInSignal):
                    detected = True
                    break

        await asyncio.wait_for(_collect(), timeout=20.0)
    except asyncio.TimeoutError:
        detected = False
    finally:
        await provider.cleanup()
    return detected


def _run_single_call(
    *,
    host: str,
    port: int,
    extension: str,
    bind_ip: str,
    timeout_s: float,
    call_index: int,
    batch_index: int,
    scenario: str,
    gateway_base_url: str,
    tts_ulaw: bytes,
    user_payloads: List[bytes],
    start_of_turn_detected: bool,
    barge_after_ms: int,
    playback_idle_ms: int,
    post_barge_idle_ms: int,
    max_barge_reaction_ms: float,
    known_session_ids: Set[str],
    trace_sink: List[Dict[str, Any]],
) -> Day8CallResult:
    media_port = 45000 + ((call_index * 2) % 2000)
    from_user = f"day8probe{call_index}"
    from_tag = uuid.uuid4().hex[:8]
    sip_call_id = f"{uuid.uuid4().hex}-{call_index}@talky.day8"
    session_id = ""
    interrupt_reason = ""
    gateway_tts_stop_reason = ""

    sip_success = False
    tts_playback_success = False
    barge_in_success = scenario != "barge_in"
    reason = "ok"
    tts_first_packet_ms: Optional[float] = None
    barge_in_reaction_ms: Optional[float] = None
    tts_packets_total = 0
    tts_packets_after_trigger = 0

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sip_sock:
        sip_sock.bind((bind_ip, 0))
        local_ip, local_port = sip_sock.getsockname()

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as rtp_sock:
            rtp_sock.bind((local_ip, media_port))
            rtp_sock.settimeout(0.02)

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
                call_id=sip_call_id,
                cseq=1,
                from_tag=from_tag,
                to_header=to_header,
                contact_user=from_user,
                body=sdp,
            )
            sip_sock.sendto(invite, (host, port))

            invite_codes, final_headers, final_body = _recv_until_final(
                sip_sock, timeout_s=timeout_s, expected_method="INVITE", call_id=sip_call_id
            )
            if not final_headers or invite_codes[-1] != 200:
                raise ProbeError(f"call {call_index}: invite failed codes={invite_codes}")

            to_tag = _extract_to_tag(final_headers.get("to", to_header))
            if not to_tag:
                raise ProbeError(f"call {call_index}: missing to-tag")

            remote_media_ip, remote_media_port = _parse_sdp_target(host, final_body)
            ack_to = f"<sip:{extension}@{host}:{port}>;tag={to_tag}"
            ack = _build_request(
                method="ACK",
                target_host=host,
                target_port=port,
                from_user=from_user,
                extension=extension,
                local_ip=local_ip,
                local_port=local_port,
                call_id=sip_call_id,
                cseq=1,
                from_tag=from_tag,
                to_header=ack_to,
                contact_user=from_user,
                body="",
            )
            sip_sock.sendto(ack, (host, port))
            sip_success = True

            session_id = _wait_for_active_session(
                gateway_base_url,
                timeout_s=6.0,
                exclude_session_ids=known_session_ids,
            )
            _gateway_play_tts(gateway_base_url, session_id, tts_ulaw, clear_existing=True)

            trace_sink.append(
                {
                    "ts": time.time(),
                    "event": "tts_play_queued",
                    "call_index": call_index,
                    "scenario": scenario,
                    "session_id": session_id,
                }
            )

            call_start = time.monotonic()
            trigger_ts: Optional[float] = None
            first_rx_ts: Optional[float] = None
            last_rx_ts: Optional[float] = None
            barge_sent = False

            user_seq = random.randint(0, 65535)
            user_ts = random.randint(0, 2**32 - 1)
            user_ssrc = random.randint(1, 2**32 - 1)

            while (time.monotonic() - call_start) < 12.0:
                now = time.monotonic()

                if scenario == "barge_in" and first_rx_ts and not barge_sent:
                    since_first_ms = (now - first_rx_ts) * 1000.0
                    if since_first_ms >= float(barge_after_ms):
                        for payload in user_payloads[:20]:
                            pkt = _build_rtp_packet(seq=user_seq, ts=user_ts, ssrc=user_ssrc, payload=payload)
                            rtp_sock.sendto(pkt, (remote_media_ip, remote_media_port))
                            user_seq = (user_seq + 1) & 0xFFFF
                            user_ts = (user_ts + 160) & 0xFFFFFFFF
                            time.sleep(0.02)
                        trigger_ts = time.monotonic()
                        interrupt_reason = "barge_in_start_of_turn"
                        _gateway_interrupt_tts(gateway_base_url, session_id, interrupt_reason)
                        barge_sent = True
                        trace_sink.append(
                            {
                                "ts": time.time(),
                                "event": "tts_interrupt",
                                "call_index": call_index,
                                "scenario": scenario,
                                "session_id": session_id,
                                "reason": interrupt_reason,
                            }
                        )

                try:
                    data, _ = rtp_sock.recvfrom(2048)
                except TimeoutError:
                    if first_rx_ts and last_rx_ts:
                        idle_ms = (time.monotonic() - last_rx_ts) * 1000.0
                        if scenario == "baseline" and idle_ms >= float(playback_idle_ms):
                            break
                        if scenario == "barge_in" and barge_sent and idle_ms >= float(post_barge_idle_ms):
                            break
                    continue

                echoed = _extract_ulaw_payload(data)
                if not echoed:
                    continue
                tts_packets_total += 1
                pkt_ts = time.monotonic()
                if first_rx_ts is None:
                    first_rx_ts = pkt_ts
                    tts_first_packet_ms = (pkt_ts - call_start) * 1000.0
                last_rx_ts = pkt_ts
                if trigger_ts and pkt_ts >= trigger_ts:
                    tts_packets_after_trigger += 1

            tts_playback_success = tts_packets_total > 0

            if scenario == "barge_in":
                if trigger_ts is None:
                    reason = "barge_trigger_missing"
                    barge_in_success = False
                else:
                    if last_rx_ts and last_rx_ts >= trigger_ts:
                        barge_in_reaction_ms = (last_rx_ts - trigger_ts) * 1000.0
                    else:
                        barge_in_reaction_ms = 0.0
                    barge_in_success = (
                        start_of_turn_detected
                        and barge_in_reaction_ms <= max_barge_reaction_ms
                    )
                    if not start_of_turn_detected:
                        reason = "start_of_turn_not_detected"
                    elif barge_in_reaction_ms > max_barge_reaction_ms:
                        reason = "barge_reaction_too_slow"

            if not tts_playback_success and reason == "ok":
                reason = "no_tts_packets"

            if session_id:
                try:
                    stats = _gateway_get(gateway_base_url, f"/v1/sessions/{session_id}/stats")
                    gateway_tts_stop_reason = str(stats.get("tts_last_stop_reason") or "")
                except Exception:
                    gateway_tts_stop_reason = ""

            bye_to = f"<sip:{extension}@{host}:{port}>;tag={to_tag}"
            bye = _build_request(
                method="BYE",
                target_host=host,
                target_port=port,
                from_user=from_user,
                extension=extension,
                local_ip=local_ip,
                local_port=local_port,
                call_id=sip_call_id,
                cseq=2,
                from_tag=from_tag,
                to_header=bye_to,
                contact_user=from_user,
                body="",
            )
            sip_sock.sendto(bye, (host, port))
            _recv_until_final(sip_sock, timeout_s=timeout_s, expected_method="BYE", call_id=sip_call_id)

    if scenario == "barge_in" and gateway_tts_stop_reason and gateway_tts_stop_reason != "barge_in_start_of_turn":
        if reason == "ok":
            reason = f"unexpected_tts_stop_reason:{gateway_tts_stop_reason}"
        barge_in_success = False

    return Day8CallResult(
        batch_index=batch_index,
        call_index=call_index,
        scenario=scenario,
        sip_call_id=sip_call_id,
        session_id=session_id,
        sip_success=sip_success,
        tts_playback_success=tts_playback_success,
        barge_in_success=barge_in_success,
        start_of_turn_detected=start_of_turn_detected,
        tts_first_packet_ms=tts_first_packet_ms,
        barge_in_reaction_ms=barge_in_reaction_ms,
        tts_packets_total=tts_packets_total,
        tts_packets_after_trigger=tts_packets_after_trigger,
        interrupt_reason=interrupt_reason,
        gateway_tts_stop_reason=gateway_tts_stop_reason,
        reason=reason,
    )


def run_probe(args: argparse.Namespace) -> int:
    if not args.deepgram_api_key:
        raise RuntimeError("DEEPGRAM_API_KEY is required for Day 8 probe")
    if not Path(args.audio_file).exists():
        raise RuntimeError(f"Audio fixture not found: {args.audio_file}")

    user_payloads = _prepare_ulaw_payloads(Path(args.audio_file))
    user_ulaw_audio = b"".join(user_payloads)
    start_of_turn_detected = asyncio.run(_detect_start_of_turn(args.deepgram_api_key, user_ulaw_audio))
    tts_ulaw = asyncio.run(_synthesize_tts_ulaw(args.deepgram_api_key, args.tts_voice, args.tts_text))

    results: List[Day8CallResult] = []
    trace_events: List[Dict[str, Any]] = []
    stop_reasons: Dict[str, int] = {}
    reaction_values: List[float] = []
    known_session_ids: Set[str] = set()

    total_calls = args.batches * args.calls_per_batch
    call_index = 0

    for batch in range(1, args.batches + 1):
        for _ in range(args.calls_per_batch):
            call_index += 1
            scenario = "barge_in" if (call_index % 2 == 0) else "baseline"
            result = _run_single_call(
                host=args.host,
                port=args.port,
                extension=args.extension,
                bind_ip=args.bind_ip,
                timeout_s=args.timeout,
                call_index=call_index,
                batch_index=batch,
                scenario=scenario,
                gateway_base_url=args.gateway_base_url,
                tts_ulaw=tts_ulaw,
                user_payloads=user_payloads,
                start_of_turn_detected=start_of_turn_detected,
                barge_after_ms=args.barge_after_ms,
                playback_idle_ms=args.playback_idle_ms,
                post_barge_idle_ms=args.post_barge_idle_ms,
                max_barge_reaction_ms=args.max_barge_reaction_ms,
                known_session_ids=known_session_ids,
                trace_sink=trace_events,
            )
            results.append(result)
            if result.session_id:
                known_session_ids.add(result.session_id)
            if result.gateway_tts_stop_reason:
                stop_reasons[result.gateway_tts_stop_reason] = stop_reasons.get(result.gateway_tts_stop_reason, 0) + 1
            if result.barge_in_reaction_ms is not None:
                reaction_values.append(float(result.barge_in_reaction_ms))

    passed = sum(
        1
        for r in results
        if r.sip_success and r.tts_playback_success and (r.scenario != "barge_in" or r.barge_in_success)
    )
    failed = total_calls - passed

    batch_output = {
        "calls": total_calls,
        "passed": passed,
        "failed": failed,
        "start_of_turn_detected": start_of_turn_detected,
        "results": [asdict(r) for r in results],
    }

    reaction_output = {
        "calls": total_calls,
        "barge_calls": sum(1 for r in results if r.scenario == "barge_in"),
        "barge_in_reaction_ms": {
            "p50": _percentile(reaction_values, 50),
            "p95": _percentile(reaction_values, 95),
            "p99": _percentile(reaction_values, 99),
            "values": reaction_values,
        },
        "max_allowed_p95_ms": args.max_barge_reaction_ms,
        "pass": (_percentile(reaction_values, 95) or 0.0) <= args.max_barge_reaction_ms if reaction_values else False,
    }

    stop_reason_output = {
        "calls": total_calls,
        "stop_reasons": stop_reasons,
    }

    Path(args.output_results).write_text(json.dumps(batch_output, indent=2), encoding="utf-8")
    Path(args.output_reaction).write_text(json.dumps(reaction_output, indent=2), encoding="utf-8")
    Path(args.output_stop_reasons).write_text(json.dumps(stop_reason_output, indent=2), encoding="utf-8")
    with Path(args.output_trace).open("w", encoding="utf-8") as fh:
        for event in trace_events:
            fh.write(json.dumps(event, ensure_ascii=True) + "\n")

    if failed != 0:
        return 1
    if not start_of_turn_detected:
        return 1
    if not reaction_output["pass"]:
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Day 8 TTS + barge-in probe")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=15060)
    parser.add_argument("--extension", default="750")
    parser.add_argument("--bind-ip", default="127.0.0.1")
    parser.add_argument("--timeout", type=float, default=6.0)
    parser.add_argument("--audio-file", default=str(REPO_ROOT / "backend" / "tests" / "fixtures" / "test_greeting.wav"))
    parser.add_argument("--gateway-base-url", required=True)
    parser.add_argument("--batches", type=int, default=3)
    parser.add_argument("--calls-per-batch", type=int, default=2)
    parser.add_argument("--tts-text", default="Hello, this is Talky AI speaking. You can interrupt me at any time.")
    parser.add_argument("--tts-voice", default="aura-2-andromeda-en")
    parser.add_argument("--barge-after-ms", type=int, default=350)
    parser.add_argument("--playback-idle-ms", type=int, default=400)
    parser.add_argument("--post-barge-idle-ms", type=int, default=500)
    parser.add_argument("--max-barge-reaction-ms", type=float, default=250.0)
    parser.add_argument("--deepgram-api-key", default="")
    parser.add_argument("--output-results", required=True)
    parser.add_argument("--output-reaction", required=True)
    parser.add_argument("--output-stop-reasons", required=True)
    parser.add_argument("--output-trace", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.deepgram_api_key:
        args.deepgram_api_key = os.environ.get("DEEPGRAM_API_KEY", "")
    return run_probe(args)


if __name__ == "__main__":
    raise SystemExit(main())
