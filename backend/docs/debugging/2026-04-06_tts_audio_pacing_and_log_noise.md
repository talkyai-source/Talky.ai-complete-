# Debugging Log — 2026-04-06
## TTS Audio Pacing, Barge-In Stickiness, and Log Noise

---

## Bug 14 — Per-Packet DEBUG Logging Floods Logs During TTS (~275 lines/second)

### Symptom

Production logs for any telephony call contained hundreds of lines per second during
TTS playback, making the logs nearly unreadable and overwhelming any real diagnostic signal.

Example burst (repeated ~25×/second):

```
[TelephonyGW] send_audio: received 640 bytes for f2a5fbe5-..., format=s16le
[TelephonyGW] Converting Int16 → μ-law
[TelephonyGW] Converted to 320 bytes PCMU
[TelephonyGW] Sending 160-byte packet to adapter for pbx_call_id=talky-out-c3
[AsteriskAdapter] send_tts_audio: 160 bytes to session ...
[AsteriskAdapter] ✅ Gateway accepted 160 bytes
[TelephonyGW] ✅ Sent 2 packets (320 bytes) to adapter (buffered: 0 bytes)
```

At 8kHz PCMU, 640 bytes = 40ms of audio. TTS streams at near real-time so chunks arrive
at ~25Hz → **~175 DEBUG lines/second + 25 INFO lines/second = ~200 log lines/second**
during every TTS utterance. Over a 60-second call with 3 AI turns averaging 4s each,
this produces ~2400 lines of zero-diagnostic log noise.

### Root Cause

`TelephonyMediaGateway.send_audio()` and `AsteriskAdapter.send_tts_audio()` logged at
`DEBUG` (or `INFO` for the `✅ Sent N packets` summary) for every single 160-byte
PCMU packet sent to the C++ Voice Gateway. These are hot-path operations — invoked
50+ times per second during TTS — with no state worth logging per invocation.

### Fix

Removed all per-packet log lines from `send_audio()`:
- `send_audio: received N bytes` — removed
- `Converting Int16 → μ-law` / `Converting Float32 → Int16 → μ-law` — removed
- `Converted to N bytes PCMU` — removed
- `Sending 160-byte packet to adapter` — removed
- `✅ Sent N packets (N bytes) to adapter` — downgraded INFO → DEBUG

Also removed from `AsteriskAdapter.send_tts_audio()`:
- `send_tts_audio: N bytes to session` — removed
- `✅ Gateway accepted N bytes` — removed

Error paths (`send_tts_audio failed`, `TTS encode failed`) retain their WARNING/ERROR
level — these are exceptional and must remain visible.

### Files Changed

| File | Change |
|---|---|
| `backend/app/infrastructure/telephony/telephony_media_gateway.py` | Removed 5 per-packet log lines from `send_audio()`; demoted `✅ Sent N packets` from INFO to DEBUG |
| `backend/app/infrastructure/telephony/asterisk_adapter.py` | Removed 2 per-packet log lines from `send_tts_audio()` |

---

## Bug 15 — C++ Gateway Continues Playing After Barge-In (Stale Audio Buffer)

### Symptom

After the user interrupted the AI (barge-in), the caller could still hear the AI
speaking for 0.5–2 seconds after barge-in had been detected and TTS synthesis had
stopped. This created the perception of audio that was "stuck" — the AI continuing
to talk even though it should have stopped.

Additionally, logs confirmed barge-in fired correctly (`barge_in_detected` event, TTS
loop exited) but the caller still heard trailing audio. The new AI response then arrived
and felt "aggressive" — an abrupt speech burst after a momentary silence once the
stale buffer finally drained.

### Root Cause

`TelephonyMediaGateway.clear_output_buffer()` only cleared `session.tts_buffer` — the
local accumulator of ≤159 bytes of PCMU awaiting packetisation. This buffer is almost
always empty (`buffered: 0 bytes` in logs), so this operation was effectively a no-op.

It did **not** call `AsteriskAdapter.interrupt_tts()`, which posts to
`/v1/sessions/tts/interrupt` on the C++ Voice Gateway. The C++ gateway has its own
internal audio queue — packets already forwarded to it via `send_tts_audio` continue
to play until their queue is drained at 8kHz real-time rate.

For a 3-sentence AI response (~4s of audio), if barge-in fires at the 1-second mark:
- 1s of packets remain in C++ gateway buffer → caller hears 1 more second of AI speech
- Python-side TTS loop has stopped; gateway drains independently

`interrupt_tts()` existed in `AsteriskAdapter` and connected to the C++ gateway's
interrupt endpoint but was never wired to `clear_output_buffer()`.

### Fix

Added `interrupt_tts()` call inside `TelephonyMediaGateway.clear_output_buffer()`:

```python
async def clear_output_buffer(self, call_id: str) -> None:
    session = self._sessions.get(call_id)
    if not session or not session.is_active:
        return
    session.tts_buffer = b""
    # Tell the C++ gateway to discard its buffered TTS queue immediately.
    if hasattr(session.adapter, "interrupt_tts"):
        try:
            await session.adapter.interrupt_tts(session.pbx_call_id)
        except Exception as exc:
            logger.debug("clear_output_buffer: interrupt_tts failed: %s", exc)
```

`clear_output_buffer()` is called from two places on barge-in:
1. `VoicePipelineService.handle_barge_in()` — immediately when `StartOfTurn` fires
2. `synthesize_and_send_audio()` finally block — when TTS loop exits due to interruption

Both now also clear the C++ gateway's internal queue.

### Files Changed

| File | Change |
|---|---|
| `backend/app/infrastructure/telephony/telephony_media_gateway.py` | `clear_output_buffer()` now calls `session.adapter.interrupt_tts()` after clearing local buffer |

---

## Bug 16 — `gateway_target_buffer_ms` Has No Effect on Telephony Path

### Symptom

`gateway_target_buffer_ms=40` is set in `_build_telephony_session_config()` and logged
as part of the `VoiceSessionConfig`, but TTS audio is still burst-sent to the C++
gateway at maximum speed — every 160-byte PCMU packet is forwarded immediately as an
individual HTTP POST with no pacing.

Logs confirm: `buffered: 0 bytes` on every `✅ Sent N packets` line. The
`target_buffer_ms` setting is read from the config dict but silently ignored.

### Root Cause

`TelephonyMediaGateway.initialize()` reads `sample_rate`, `channels`, `bit_depth`, and
`tts_source_format` from the config dict, but does **not** read `target_buffer_ms`.

`BrowserMediaGateway.initialize()` does read `target_buffer_ms` and uses it to coalesce
output audio before sending over WebSocket. The telephony gateway was written without
implementing the equivalent pacing mechanism — the config key is plumbed through the
orchestrator but silently dropped when `TelephonyMediaGateway.initialize()` is called.

### Impact

Audio packets arrive at the C++ gateway in a rapid burst (all packets for one TTS chunk
in under 10ms). The C++ gateway's internal jitter buffer absorbs this burst and plays
at real-time rate. For normal speech this works fine. However:

- During barge-in, already-queued packets kept playing (see Bug 15 above — now fixed).
- The burst pattern creates uneven fill-and-drain cycles in the gateway's jitter buffer,
  which can cause small gaps in playout when the burst ends but the next TTS chunk
  hasn't arrived yet (mid-sentence silence).

### Note

This is a known limitation. Implementing real-time metering in `TelephonyMediaGateway`
(pace each 160-byte packet 20ms apart) would add complexity and is lower priority now
that Bug 15 (interrupt on barge-in) is fixed. The C++ gateway's internal buffer handles
the burst adequately for continuous speech. Document for future sprint.
