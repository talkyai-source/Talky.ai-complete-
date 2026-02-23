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

## Folder Layout

- `docs/` - architecture, migration plan, cutover checklists, runbooks
- `kamailio/` - SBC configuration and scripts
- `rtpengine/` - RTP relay configuration and scripts
- `freeswitch/` - FreeSWITCH configs and dialplan
- `modules/` - custom C/C++ code (if/when required)
- `deploy/` - docker/helm/terraform deployment artifacts
- `scripts/` - validation and bootstrap scripts
- `tests/` - telephony-level integration and smoke tests

## Migration Principle

1. Add new telephony stack in parallel.
2. Route a small percentage of traffic.
3. Validate quality and reliability metrics.
4. Cut over gradually.
5. Remove legacy paths only after stability window.

See `docs/02_migration_plan.md` for the detailed plan.

## WS-A/WS-B Quick Start

1. Copy env template:
   - `cp telephony/deploy/docker/.env.telephony.example telephony/deploy/docker/.env.telephony`
2. Start and verify WS-A:
   - `bash telephony/scripts/verify_ws_a.sh telephony/deploy/docker/.env.telephony`
3. Apply WS-B security baseline and verify:
   - `bash telephony/scripts/verify_ws_b.sh telephony/deploy/docker/.env.telephony`
4. Apply WS-C call-control baseline and verify:
   - `bash telephony/scripts/verify_ws_c.sh telephony/deploy/docker/.env.telephony`
5. Apply WS-D media/latency baseline and verify:
   - `bash telephony/scripts/verify_ws_d.sh telephony/deploy/docker/.env.telephony`
6. Apply WS-E canary/rollback baseline and verify:
   - `bash telephony/scripts/verify_ws_e.sh telephony/deploy/docker/.env.telephony`
7. Review gated status:
   - `telephony/docs/07_phase_one_gated_checklist.md`
8. Review WS-B implementation log:
   - `telephony/docs/08_ws_b_security_signaling_implementation.md`
9. Review WS-C plan:
   - `telephony/docs/10_ws_c_call_control_transfer_plan.md`
10. Review WS-C implementation log:
   - `telephony/docs/11_ws_c_call_control_transfer_implementation.md`
11. Review WS-D execution plan:
   - `telephony/docs/12_ws_d_media_bridge_latency_plan.md`
12. Review WS-D baseline report:
   - `telephony/docs/phase1_baseline_latency.md`
13. Review WS-E canary/rollback plan:
   - `telephony/docs/13_ws_e_canary_rollback_plan.md`
14. Review WS-E implementation evidence:
   - `telephony/docs/14_ws_e_canary_rollback_implementation.md`
