# Voice Gateway Codec Decision

Decision date: 2026-08-30
Current production contract: callback/control protocol v2, PCMU, 8 kHz, mono,
20 ms frames (160 bytes), static RTP payload type 0.

## Decision

Keep the Asterisk ExternalMedia gateway PCMU-only for the current inbound and
outbound PSTN path. Do not label or implement a "WhatsApp codec" here.

WhatsApp is a calling product/transport, not a codec name that can be substituted
into this RTP parser. Opus is a possible codec for Internet calling, but it is a
different RTP contract: dynamic payload type, 48 kHz RTP clock, variable encoded
payload sizes, and negotiated SDP parameters. It must enter through a provider
adapter that has actually negotiated Opus, not through an unverified gateway
configuration toggle.

## Why PCMU is correct now

- The Asterisk ARI ExternalMedia API returns exactly the requested format and
  explicitly performs no negotiation. The repository requests `format=ulaw`.
- The caller/carrier path is normally narrowband PSTN. Re-encoding its 8 kHz
  G.711 audio as Opus cannot restore frequencies the carrier never delivered.
- The gateway, callback validator, STT input, TTS output, RTP timestamp step,
  jitter checks, and frame accounting all share the same 160-byte/20 ms PCMU
  invariant. Changing only one layer creates corruption, false liveness, or
  dropped audio.
- Asterisk's current troubleshooting guidance warns that needless transcoding
  consumes substantially more CPU and specifically recommends avoiding Opus on
  an AI leg when the caller is ulaw/alaw and the agent accepts narrowband audio.

Primary references:

- Asterisk ExternalMedia contract:
  https://docs.asterisk.org/Development/Reference-Information/Asterisk-Framework-and-API-Examples/External-Media-and-ARI/
- Asterisk transcoding guidance:
  https://docs.asterisk.org/Deployment/Troubleshooting/Troubleshooting-High-CPU-Utilization/
- IETF Opus RTP payload format (RFC 7587):
  https://datatracker.ietf.org/doc/html/rfc7587

## When Opus is justified

Open a separate codec project only for a real wideband source such as a
negotiated WebRTC/SIP endpoint or a documented WhatsApp calling adapter that
hands the platform Opus without a PSTN narrowband conversion. It must satisfy
all gates below before `codecs` in `/health` advertises `opus`:

1. Provider contract proves codec, RTP clock, channels, payload type, ptime,
   DTX/FEC, encryption/transport, and renegotiation behavior.
2. Asterisk/provider integration proves Opus passthrough or measures the exact
   transcoding path and CPU budget under peak concurrent calls.
3. Gateway adds a real Opus decoder/encoder with pinned dependency versions,
   license review, fuzz tests, malformed-packet limits, PLC/FEC policy, and
   sanitizer coverage.
4. Session configuration becomes a negotiated codec profile; payload type,
   timestamp step, sample rate, frame duration, and size checks stop being PCMU
   constants.
5. Backend callback protocol carries the negotiated profile and transports
   decoded PCM or a codec-specific payload without ambiguous `audio_base64`
   fallback. STT/TTS capabilities are checked before admission.
6. Load tests compare PCMU and Opus at production concurrency: CPU, RSS, packet
   loss, jitter, callback backlog, STT word error rate, first-turn latency, and
   perceived MOS. The release needs explicit pass/fail thresholds.
7. Rollout is canary-only, capability-negotiated per call, and instantly
   reversible to PCMU. A mixed or unsupported version must fail session start,
   never guess or transcode silently.

Until those gates pass, PCMU is the quality-maximizing choice for this path
because it avoids an unnecessary lossy transcode and preserves one exact,
well-tested media invariant end to end.
