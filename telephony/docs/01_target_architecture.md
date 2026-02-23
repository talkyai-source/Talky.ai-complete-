# Target Architecture

## Logical Flow

1. Customer PBX/SIP trunk sends INVITE.
2. Kamailio authenticates and routes to FreeSWITCH.
3. FreeSWITCH anchors media and triggers AI media bridge.
4. Audio is streamed bidirectionally between FreeSWITCH and Python backend.
5. Python backend runs STT -> LLM -> TTS pipeline.
6. TTS audio returns to FreeSWITCH and is played to caller.
7. All call events and metadata are persisted to PostgreSQL.

## Capability Map

### Kamailio
- Multi-tenant SIP routing and policy
- Per-tenant trunk auth and ACL
- Rate limiting and anti-fraud guards

### rtpengine
- RTP relay and NAT traversal
- Media anchoring for stable audio path
- Codec negotiation support via SIP control plane

### FreeSWITCH
- Call control (answer, bridge, transfer, hangup)
- DTMF handling
- Audio stream fork to AI bridge
- Playback and prompts

### Python Backend
- Session lifecycle and orchestration
- STT/LLM/TTS integration
- Barge-in and turn-taking
- Guardrails and business logic
- Tenant-aware policy enforcement

## Transfer Patterns

### Blind transfer
- Triggered by rule or user request
- FreeSWITCH executes immediate transfer (`uuid_transfer`)
- Backend logs transfer intent and outcome

### Attended transfer
- Create consult leg
- Confirm destination availability
- Bridge original leg to destination
- Tear down AI leg once bridge is successful

## Security Boundaries

- SIP ingress only via SBC.
- Media bridge endpoints restricted by network ACL and token auth.
- Tenant IDs propagated in signaling metadata and validated at Python edge.
