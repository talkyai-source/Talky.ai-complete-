# Telephony Workspace

This folder is the dedicated home for telecom/media infrastructure and related docs.

Goal:
- Keep C/C++ telecom layers separate from Python application code.
- Migrate safely from the current mixed setup to a production-ready voice system.
- Maintain clear operational docs and runbooks inside `telephony/docs`.

## Scope

In scope:
- SBC/SIP edge configuration (Kamailio/OpenSIPS style)
- RTP relay/media anchoring (rtpengine)
- B2BUA/media app config (FreeSWITCH)
- Custom C/C++ modules and patches (if needed)
- Deployment manifests (docker/helm/terraform)
- Migration/cutover documentation

Out of scope:
- Python AI business logic (stays under `backend/`)
- Frontend dashboard/UI code

## Active Plan Authority

1. Frozen plan (single source of truth):
   - `telephony/docs/phase_3/19_talk_lee_frozen_integration_plan.md`
2. Current status tracker:
   - `telephony/docs/phase_3/20_status_against_frozen_talk_lee_plan.md`
3. Phase-definition alignment map:
   - `telephony/docs/phase_3/21_phase_definition_map_and_alignment.md`

Legacy WS-based docs are retained as historical evidence only.

## Folder Layout

- `docs/` - architecture, migration plan, cutover checklists, runbooks
- `opensips/` - active SBC configuration and scripts
- `asterisk/` - primary B2BUA/media runtime (active)
- `kamailio/` - backup-only SBC snapshot (non-active)
- `rtpengine/` - RTP relay configuration and scripts
- `freeswitch/` - backup B2BUA runtime (non-active by default)
- `modules/` - custom C/C++ code (if/when required)
- `deploy/` - docker/helm/terraform deployment artifacts
- `scripts/` - validation and bootstrap scripts
- `tests/` - telephony-level integration and smoke tests
- `../services/voice-gateway-cpp` - C++ gateway target path for Day 4+

Current active SIP edge runtime: `opensips/`.
Current active media/B2BUA runtime: `asterisk/`.
FreeSWITCH backup runtime is opt-in via compose profile `backup`.

## Migration Principle

1. Add new telephony stack in parallel.
2. Route a small percentage of traffic.
3. Validate quality and reliability metrics.
4. Cut over gradually.
5. Remove legacy paths only after stability window.

See `docs/phase_1/02_migration_plan.md` for the detailed plan.

## Execution Rule

1. Execute only by frozen day plan sequence (Day 0 -> Day 10).
2. Do not start the next day until current-day acceptance is closed.
3. Keep OpenSIPS and Asterisk as primary runtime.
4. Keep Kamailio and FreeSWITCH as backup-only runtime.
