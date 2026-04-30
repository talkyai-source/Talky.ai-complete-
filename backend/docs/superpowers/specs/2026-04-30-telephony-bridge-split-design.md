# Telephony Bridge Split — Design Spec

**Date:** 2026-04-30
**Status:** Design approved, awaiting implementation plan
**Owner:** allestateestimation@gmail.com

## Problem

`backend/app/api/v1/endpoints/telephony_bridge.py` has grown to 2355 lines. It mixes:

- FastAPI route handlers (start/stop/status, make_call, hangup, transfers, audio HTTP+WS)
- Two divergent call-flow modes (agent-speaks-first, user-speaks-first) interleaved
- Call-lifecycle orchestration (ringing → new_call → end, watchdog, pipeline callback)
- Stereo-WAV recording pipeline (~244 lines)
- Session config + greeting builders
- Module-level state (ringing warmup cache, watchdog task)

The two first-speaker modes are the most tangled concern. The user reported the file is "too big" and asked to extract caller-speaks-first and agent-speaks-first into separate files.

## Non-goals

- **No behavior change.** This is a refactor only.
- **No new features.** Per-campaign `first_speaker` selection already works end-to-end (campaign API → `campaign_service` → `dialer_job` → `dialer_worker` → `make_call?first_speaker=…` query param → `voice_session._first_speaker`).
- **No env-var removal.** `TELEPHONY_FIRST_SPEAKER` stays as a fallback default for calls without a per-call override.
- **No DB schema changes.** `first_speaker` field already lives on the campaign-start payload.

## Goals

1. Each file ≤600 lines (matches existing `backend/app/services/scripts/` convention).
2. The two mode-handlers live in dedicated, focused files.
3. FastAPI route handlers stay where they are (URL-stable), but the file becomes thin.
4. Existing tests (`tests/unit/test_telephony_bridge_first_speaker.py`, `tests/unit/test_voice_pipeline_runtime.py`) keep passing without modification beyond import-path updates.
5. Each new module has a one-pager in `backend/docs/script/telephony/` matching the project's docs convention.

## Target layout

```
backend/app/api/v1/endpoints/
  telephony_bridge.py                 # ~250 lines: FastAPI routes only.
                                      # start/stop/status, make_call, hangup,
                                      # transfer_blind/attended/deflect,
                                      # receive_gateway_audio (HTTP),
                                      # telephony_audio_websocket (WS).
                                      # All handlers delegate to domain layer.

backend/app/domain/services/telephony/
  __init__.py                         # public re-exports for callers
  config.py                           # ~80 lines:
                                      #   _outbound_first_speaker() env default,
                                      #   _build_telephony_session_config(),
                                      #   _build_outbound_greeting().
  modes/
    __init__.py                       # dispatcher: choose handler based on
                                      # voice_session._first_speaker, falling
                                      # back to _outbound_first_speaker().
    agent_first.py                    # ~250 lines:
                                      #   send_outbound_greeting() — fast path
                                      #   (pre-synth chunks pumped into media
                                      #   gateway) and realtime fallback,
                                      #   plus barge-in spoken-text persistence.
                                      #   pre-originate greeting synth helper
                                      #   (currently inline in make_call).
    user_first.py                     # ~250 lines:
                                      #   _user_first_open_seconds(),
                                      #   _user_first_fallback_enabled(),
                                      #   _handle_user_first_silence().
  lifecycle.py                        # ~400 lines:
                                      #   _on_ringing, _on_new_call,
                                      #   _on_call_ended, _session_watchdog,
                                      #   _pipeline_done_cb,
                                      #   _on_ws_session_start,
                                      #   ringing-warmup module state,
                                      #   _reject_overcap_call.
  recording.py                        # ~250 lines:
                                      #   _save_call_recording — stereo WAV
                                      #   build, calls.row resolve/insert,
                                      #   recording_s3 row insert, S3 upload.

backend/docs/script/telephony/
  README.md                           # index + cross-link to other script docs
  agent_first.md                      # one-pager: trigger, fast vs realtime
                                      # path, barge-in handling, env knobs.
  user_first.md                       # one-pager: when used, fallback timing,
                                      # env knobs, interaction with VAD.
  lifecycle.md                        # one-pager: ringing → new_call → end,
                                      # watchdog, warmup cache.
  recording.md                        # one-pager: recording pipeline,
                                      # tenant-id resolution, fallback to disk.
```

## Dispatcher contract

`telephony.modes.dispatch_first_speaker(voice_session) -> Literal["agent", "user"]`

Resolution order, mirroring today's logic at `telephony_bridge.py:1200`:

1. `getattr(voice_session, "_first_speaker", None)` — set per-call by `make_call` from the query param.
2. `config._outbound_first_speaker()` — env default (`TELEPHONY_FIRST_SPEAKER`, defaults to `agent`).
3. Final clamp to `{"agent", "user"}`; anything else → `"agent"`.

The two mode modules each export a single async entrypoint:

- `agent_first.send_greeting(voice_session) -> None` (replaces `_send_outbound_greeting`)
- `user_first.handle_silence(voice_session, pbx_call_id: str) -> None` (replaces `_handle_user_first_silence`)

`lifecycle.on_new_call` calls the dispatcher to pick which entrypoint to schedule. No conditional logic remains in the lifecycle code beyond the dispatch.

## Module-state ownership

| State | Today | After |
|---|---|---|
| `_RINGING_WARMUP` cache | `telephony_bridge.py` module-level | `lifecycle.py` module-level |
| Session watchdog task handle | `telephony_bridge.py` module-level | `lifecycle.py` module-level |
| Pre-synth greeting attrs on `voice_session` | set in `make_call`, read in `_send_outbound_greeting` | set in `lifecycle.on_ringing`, read in `agent_first.send_greeting` |

No state is shared across mode files — each owns its own helpers.

## Import graph

```
endpoints/telephony_bridge.py
  → domain/services/telephony/lifecycle.py
  → domain/services/telephony/recording.py
  → domain/services/telephony/config.py

domain/services/telephony/lifecycle.py
  → domain/services/telephony/modes/__init__.py  (dispatcher)
  → domain/services/telephony/config.py
  → domain/services/telephony/recording.py

domain/services/telephony/modes/__init__.py
  → domain/services/telephony/modes/agent_first.py
  → domain/services/telephony/modes/user_first.py
  → domain/services/telephony/config.py

domain/services/telephony/modes/agent_first.py
  → domain/services/telephony/config.py

domain/services/telephony/modes/user_first.py
  → domain/services/telephony/config.py
```

No cycles. `config.py` is leaf.

## Test strategy

- Existing tests update only their import paths. No assertion changes.
- `test_telephony_bridge_first_speaker.py` becomes the integration test for the dispatcher; the underlying call-flow assertions stay the same.
- Add a smoke-level import test (`from app.domain.services.telephony import lifecycle, recording, config; from app.domain.services.telephony.modes import agent_first, user_first`) to catch import-cycle regressions cheaply.
- No new behavioral tests — this is a pure refactor.

## Risks

1. **Hidden coupling.** The 2355-line file may have implicit cross-references (closures, shared local helpers) that look mode-agnostic but aren't. Mitigation: extract one module at a time and run the full test suite after each extraction.
2. **`_on_new_call` is ~310 lines.** Close to the 600-line cap but well under, so it fits in `lifecycle.py`. If lifecycle grows past 600, split `on_new_call` into a `call_setup.py` later — not now.
3. **`_save_call_recording` is ~244 lines.** Same — fits, but worth flagging for a future split if S3 logic expands.
4. **Module-level state migration.** The `_RINGING_WARMUP` cache and watchdog handle move from one module to another. Any code path that imports them by name from `telephony_bridge` will break. Mitigation: grep for cross-module access before moving and update all references in the same commit.
5. **Public re-exports.** Some routes/helpers may be imported by tests or other modules from `app.api.v1.endpoints.telephony_bridge`. Mitigation: keep the public symbols re-exported from `telephony_bridge.py` for one release cycle so external imports keep working.

## Out of scope

- Replacing the `TELEPHONY_FIRST_SPEAKER` env-var default with a tenant-level setting.
- Reworking the recording pipeline's tenant-id fallback (the `stub_calls_row_insert_failed` warning seen in production logs is a real bug but a separate fix).
- Fixing the `badly formed hexadecimal UUID string` error in `recording_s3` insert (also a separate fix).
- Frontend changes to expose `first_speaker` on the campaign creation form (already exposed via the API; UI work is its own task).

## Acceptance criteria

- [ ] All files in `app/domain/services/telephony/` and the slimmed `telephony_bridge.py` are ≤600 lines.
- [ ] `pytest backend/tests/unit/test_telephony_bridge_first_speaker.py backend/tests/unit/test_voice_pipeline_runtime.py` passes with no behavioral changes.
- [ ] `pytest backend/tests/` full suite passes.
- [ ] One real call in each mode (agent-first and user-first) completes end-to-end against the running backend with the same observable behavior as before (greeting plays / silence handler fires at the configured timing).
- [ ] Each new module has a corresponding one-pager in `backend/docs/script/telephony/`.
- [ ] No import cycles (verified by smoke import test).
