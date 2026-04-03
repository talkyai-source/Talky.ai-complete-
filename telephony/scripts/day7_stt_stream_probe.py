#!/usr/bin/env python3
"""Day 7 STT streaming probe.

Runs SIP calls over the Day 5/Day 6 telephony route, captures echoed PCMU RTP,
converts it to PCM16/16k, streams to Deepgram Flux, and verifies transcript
integrity against call_id + talklee_call_id bindings.
"""

from __future__ import annotations

import argparse
import asyncio
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
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
import sys

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.models.conversation import AudioChunk
from app.domain.models.voice_contract import generate_talklee_call_id
from app.domain.services.transcript_service import TranscriptService
from app.infrastructure.stt.deepgram_flux import (
    DeepgramFluxSTTProvider,
    FLUX_OPTIMAL_CHUNK_BYTES,
    FLUX_OPTIMAL_CHUNK_MS,
)
from app.utils.audio_utils import pcm_to_ulaw, ulaw_to_pcm

TO_TAG_RE = re.compile(r";\s*tag=([^;>\s]+)", re.IGNORECASE)


@dataclass
class ProbeCallResult:
    batch_index: int
    call_index: int
    sip_call_id: str
    call_id: str
    talklee_call_id: str
    sip_success: bool
    transcript_success: bool
    sent_rtp_packets: int
    received_rtp_packets: int
    received_ulaw_bytes: int
    transcript_text: str
    transcript_event_count: int
    stt_first_transcript_ms: Optional[float]
    stt_stop_reason: str
    reason: str


class SipProbeError(RuntimeError):
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

    frame_size = 160  # 20ms at 8kHz, 8-bit PCMU
    payloads: List[bytes] = []
    for idx in range(0, len(ulaw), frame_size):
        payload = ulaw[idx : idx + frame_size]
        if len(payload) < frame_size:
            payload += bytes(frame_size - len(payload))
        payloads.append(payload)

    if not payloads:
        payloads.append(bytes([0xFF] * frame_size))

    return payloads


def _build_sdp(bind_ip: str, media_port: int) -> str:
    return (
        "v=0\r\n"
        f"o=- 0 0 IN IP4 {bind_ip}\r\n"
        "s=Talky Day7 Probe\r\n"
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
        "User-Agent: talky-day7-stt-probe/1.0",
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


def _run_sip_call_capture(
    *,
    host: str,
    port: int,
    extension: str,
    bind_ip: str,
    timeout_s: float,
    call_index: int,
    payloads: Iterable[bytes],
) -> Tuple[str, int, int, bytes]:
    media_port = 44000 + ((call_index * 2) % 2000)
    from_user = f"day7probe{call_index}"
    from_tag = uuid.uuid4().hex[:8]
    sip_call_id = f"{uuid.uuid4().hex}-{call_index}@talky.day7"

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sip_sock:
        sip_sock.bind((bind_ip, 0))
        local_ip, local_port = sip_sock.getsockname()

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as rtp_sock:
            rtp_sock.bind((local_ip, media_port))
            rtp_sock.settimeout(0.01)

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
                sip_sock,
                timeout_s=timeout_s,
                expected_method="INVITE",
                call_id=sip_call_id,
            )
            if not final_headers:
                raise SipProbeError(f"call {call_index}: invite timeout codes={invite_codes}")

            if invite_codes[-1] != 200:
                raise SipProbeError(f"call {call_index}: invite failed code={invite_codes[-1]}")

            to_tag = _extract_to_tag(final_headers.get("to", to_header))
            if not to_tag:
                raise SipProbeError(f"call {call_index}: missing to-tag")

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

            sent_rtp = 0
            recv_rtp = 0
            received_payloads: List[bytes] = []

            seq = random.randint(0, 65535)
            ts = random.randint(0, 2**32 - 1)
            ssrc = random.randint(1, 2**32 - 1)
            next_send = time.monotonic()

            for payload in payloads:
                while time.monotonic() < next_send:
                    try:
                        data, _ = rtp_sock.recvfrom(2048)
                        echoed = _extract_ulaw_payload(data)
                        if echoed:
                            received_payloads.append(echoed)
                            recv_rtp += 1
                    except TimeoutError:
                        pass

                packet = _build_rtp_packet(seq=seq, ts=ts, ssrc=ssrc, payload=payload)
                rtp_sock.sendto(packet, (remote_media_ip, remote_media_port))
                sent_rtp += 1
                seq = (seq + 1) & 0xFFFF
                ts = (ts + 160) & 0xFFFFFFFF
                next_send += 0.02

            tail_deadline = time.monotonic() + 1.0
            while time.monotonic() < tail_deadline:
                try:
                    data, _ = rtp_sock.recvfrom(2048)
                    echoed = _extract_ulaw_payload(data)
                    if echoed:
                        received_payloads.append(echoed)
                        recv_rtp += 1
                except TimeoutError:
                    break

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

            return sip_call_id, sent_rtp, recv_rtp, b"".join(received_payloads)


def _ulaw_to_pcm16_16k(ulaw_audio: bytes) -> bytes:
    pcm_8k = ulaw_to_pcm(ulaw_audio)
    samples_8k = np.frombuffer(pcm_8k, dtype=np.int16)
    samples_16k = _resample_pcm16(samples_8k, 8000, 16000)
    return samples_16k.tobytes()


async def _transcribe_echo_audio(
    *,
    call_id: str,
    talklee_call_id: str,
    pcm16_16k: bytes,
    transcript_service: TranscriptService,
    deepgram_api_key: str,
    deepgram_model: str,
    trace_sink: list[dict[str, Any]],
) -> Tuple[str, int, Optional[float], str]:
    provider = DeepgramFluxSTTProvider()
    await provider.initialize(
        {
            "api_key": deepgram_api_key,
            "model": deepgram_model,
            "sample_rate": 16000,
            "encoding": "linear16",
            "eot_threshold": 0.7,
            "eot_timeout_ms": 5000,
        }
    )

    transcript_service.bind_call_identity(call_id, talklee_call_id)

    chunks: List[bytes] = [
        pcm16_16k[i : i + FLUX_OPTIMAL_CHUNK_BYTES]
        for i in range(0, len(pcm16_16k), FLUX_OPTIMAL_CHUNK_BYTES)
        if pcm16_16k[i : i + FLUX_OPTIMAL_CHUNK_BYTES]
    ]

    if not chunks:
        await provider.cleanup()
        return "", 0, None, "stt_internal_error"

    async def audio_stream():
        for chunk in chunks:
            yield AudioChunk(data=chunk, sample_rate=16000, channels=1)
            await asyncio.sleep(FLUX_OPTIMAL_CHUNK_MS / 1000.0)

    final_text = ""
    latest_text = ""
    event_count = 0
    first_transcript_ms: Optional[float] = None
    started = time.monotonic()

    async def _collect() -> None:
        nonlocal final_text, latest_text, event_count, first_transcript_ms
        async for chunk in provider.stream_transcribe(audio_stream(), call_id=call_id):
            if not hasattr(chunk, "text"):
                continue

            text = (chunk.text or "").strip()
            metadata = chunk.metadata or {}
            if not text:
                continue

            if first_transcript_ms is None:
                first_transcript_ms = (time.monotonic() - started) * 1000.0

            event_type = "update"
            include_in_plaintext = False
            if metadata.get("eager"):
                event_type = "eager_end_of_turn"
            if chunk.is_final:
                event_type = "end_of_turn"
                include_in_plaintext = True
                final_text = text
            else:
                latest_text = text

            transcript_service.accumulate_turn(
                call_id=call_id,
                role="user",
                content=text,
                confidence=chunk.confidence,
                talklee_call_id=talklee_call_id,
                turn_index=0,
                event_type=event_type,
                is_final=chunk.is_final,
                include_in_plaintext=include_in_plaintext,
                metadata=metadata,
            )
            event_count += 1
            trace_sink.append(
                {
                    "ts": time.time(),
                    "call_id": call_id,
                    "talklee_call_id": talklee_call_id,
                    "event_type": event_type,
                    "text": text,
                    "is_final": bool(chunk.is_final),
                    "confidence": chunk.confidence,
                }
            )

    try:
        await asyncio.wait_for(_collect(), timeout=45.0)
    finally:
        stats = provider.get_stream_stats(call_id)
        stop_reason = str(stats.get("stt_stop_reason") or "stt_stream_closed")
        await provider.cleanup()

    if not final_text and latest_text:
        final_text = latest_text

    return final_text, event_count, first_transcript_ms, stop_reason


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


def run_probe(args: argparse.Namespace) -> int:
    if not (Path(BACKEND_ROOT) / "app").exists():
        raise RuntimeError("backend app package not found")

    if not args.deepgram_api_key:
        raise RuntimeError("DEEPGRAM_API_KEY is required for Day 7 probe")
    if not Path(args.audio_file).exists():
        raise RuntimeError(f"Audio fixture not found: {args.audio_file}")

    payloads = _prepare_ulaw_payloads(Path(args.audio_file))
    transcript_service = TranscriptService()
    trace_events: list[dict[str, Any]] = []

    results: list[ProbeCallResult] = []
    integrity_reports: list[dict[str, Any]] = []
    batch_p95: list[Optional[float]] = []

    total_calls = args.batches * args.calls_per_batch
    call_seq = 0

    for batch_index in range(args.batches):
        batch_latencies: list[float] = []

        for _ in range(args.calls_per_batch):
            call_seq += 1
            call_id = str(uuid.uuid4())
            talklee_call_id = generate_talklee_call_id()
            reason = "ok"
            sip_success = False
            transcript_success = False
            sent = 0
            recv = 0
            ulaw_echo = b""
            sip_call_id = ""
            transcript_text = ""
            transcript_events = 0
            first_ms: Optional[float] = None
            stop_reason = "stt_stream_closed"

            try:
                sip_call_id, sent, recv, ulaw_echo = _run_sip_call_capture(
                    host=args.host,
                    port=args.port,
                    extension=args.extension,
                    bind_ip=args.bind_ip,
                    timeout_s=args.timeout,
                    call_index=call_seq,
                    payloads=payloads,
                )
                sip_success = recv > 0
                if not sip_success:
                    reason = "no_echo_rtp"
                else:
                    pcm16_16k = _ulaw_to_pcm16_16k(ulaw_echo)
                    transcript_text, transcript_events, first_ms, stop_reason = asyncio.run(
                        _transcribe_echo_audio(
                            call_id=call_id,
                            talklee_call_id=talklee_call_id,
                            pcm16_16k=pcm16_16k,
                            transcript_service=transcript_service,
                            deepgram_api_key=args.deepgram_api_key,
                            deepgram_model=args.deepgram_model,
                            trace_sink=trace_events,
                        )
                    )
                    transcript_success = bool(transcript_text.strip())
                    if not transcript_success:
                        reason = "empty_transcript"
                    elif first_ms is not None:
                        batch_latencies.append(first_ms)
            except Exception as exc:  # noqa: BLE001
                reason = str(exc)

            integrity = transcript_service.build_integrity_report(
                call_id=call_id,
                expected_talklee_call_id=talklee_call_id,
            )
            integrity_reports.append(integrity)
            transcript_service.clear_buffer(call_id)

            results.append(
                ProbeCallResult(
                    batch_index=batch_index + 1,
                    call_index=call_seq,
                    sip_call_id=sip_call_id,
                    call_id=call_id,
                    talklee_call_id=talklee_call_id,
                    sip_success=sip_success,
                    transcript_success=transcript_success,
                    sent_rtp_packets=sent,
                    received_rtp_packets=recv,
                    received_ulaw_bytes=len(ulaw_echo),
                    transcript_text=transcript_text,
                    transcript_event_count=transcript_events,
                    stt_first_transcript_ms=first_ms,
                    stt_stop_reason=stop_reason,
                    reason=reason,
                )
            )

        batch_p95.append(_percentile(batch_latencies, 95))

    latency_values = [r.stt_first_transcript_ms for r in results if r.stt_first_transcript_ms is not None]
    latency_values_f = [float(v) for v in latency_values if v is not None]
    overall_p95 = _percentile(latency_values_f, 95)

    stable = False
    spread_pct: Optional[float] = None
    numeric_batch_p95 = [v for v in batch_p95 if v is not None]
    if len(numeric_batch_p95) >= 2:
        hi = max(numeric_batch_p95)
        lo = min(numeric_batch_p95)
        spread_pct = 0.0 if hi <= 0 else ((hi - lo) / hi) * 100.0
        stable = spread_pct <= args.max_p95_spread_pct

    passed_calls = sum(1 for r in results if r.sip_success and r.transcript_success)
    failed_calls = total_calls - passed_calls

    batch_output = {
        "calls": total_calls,
        "passed": passed_calls,
        "failed": failed_calls,
        "results": [asdict(r) for r in results],
    }

    integrity_output = {
        "calls": total_calls,
        "invalid_calls": sum(1 for r in integrity_reports if not r.get("is_valid")),
        "reports": integrity_reports,
    }

    latency_output = {
        "calls": total_calls,
        "stt_first_transcript_ms": {
            "p50": _percentile(latency_values_f, 50),
            "p95": overall_p95,
            "p99": _percentile(latency_values_f, 99),
            "values": latency_values_f,
        },
        "batch_p95": batch_p95,
        "p95_spread_pct": spread_pct,
        "stable": stable,
        "max_allowed_spread_pct": args.max_p95_spread_pct,
    }

    Path(args.output_results).write_text(json.dumps(batch_output, indent=2), encoding="utf-8")
    Path(args.output_integrity).write_text(json.dumps(integrity_output, indent=2), encoding="utf-8")
    Path(args.output_latency).write_text(json.dumps(latency_output, indent=2), encoding="utf-8")

    with Path(args.output_trace).open("w", encoding="utf-8") as fh:
        for event in trace_events:
            fh.write(json.dumps(event, ensure_ascii=True) + "\n")

    if failed_calls != 0:
        return 1
    if integrity_output["invalid_calls"] != 0:
        return 1
    if not stable:
        return 1
    if overall_p95 is None:
        return 1

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Day 7 STT streaming probe")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=15060)
    parser.add_argument("--extension", default="750")
    parser.add_argument("--bind-ip", default="127.0.0.1")
    parser.add_argument("--timeout", type=float, default=6.0)
    parser.add_argument("--audio-file", default=str(REPO_ROOT / "backend" / "tests" / "fixtures" / "test_greeting.wav"))
    parser.add_argument("--batches", type=int, default=3)
    parser.add_argument("--calls-per-batch", type=int, default=2)
    parser.add_argument("--deepgram-model", default="flux-general-en")
    parser.add_argument("--max-p95-spread-pct", type=float, default=20.0)
    parser.add_argument("--deepgram-api-key", default="")
    parser.add_argument("--output-results", required=True)
    parser.add_argument("--output-integrity", required=True)
    parser.add_argument("--output-latency", required=True)
    parser.add_argument("--output-trace", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.deepgram_api_key:
        args.deepgram_api_key = os.environ.get("DEEPGRAM_API_KEY", "")

    return run_probe(args)


if __name__ == "__main__":
    raise SystemExit(main())
