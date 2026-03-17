# Operations Runbook

## On-Call Priorities

P1:
- Widespread call setup failures
- One-way/no audio
- Transfer failures across tenants

P2:
- Elevated latency or jitter for subset of routes
- DTMF mismatch

P3:
- Non-critical degraded analytics/metadata lag

## First 10 Minutes Checklist

1. Check SBC health and SIP registration status.
2. Check FreeSWITCH call channels and event socket health.
3. Check RTP relay packet flow and port availability.
4. Check Python voice pipeline queue depth and worker saturation.
5. Confirm database and Redis health.

## Standard Debug Artifacts

- SIP traces (invite/answer/bye)
- RTP stats (packet loss/jitter)
- FreeSWITCH event logs
- Python call/session logs by call_id
- Transfer operation logs and destination response codes

## Transfer Incident Playbook

1. Identify transfer type (blind vs attended).
2. Confirm destination SIP URI/extension validity.
3. Validate bridge/transfer command response.
4. Check if destination leg was answered.
5. If failed, route call back to fallback queue or human operator.

## Low-Latency Tuning Playbook

1. Keep 8k or 16k path consistent end-to-end by route type.
2. Use smaller audio frame sizes for bridge streaming.
3. Ensure STT turn detection config is route-specific.
4. Profile queue pressure in Python pipeline.
5. Watch for transcoding hotspots in media path.
