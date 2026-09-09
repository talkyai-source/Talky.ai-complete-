# 2026-09-09 — Telephony edge audit: Asterisk PJSIP, C++ media gateway, gateway↔agent link

Owner: "inspect the pjsip sip pbx media gateway and its connection with the agent … structure,
features, stability is the core." Read-only audit; evidence from the live host (`admins@` shell,
no sudo), the repo, and two independent code reviews. Nothing was changed on prod.

## Live state (prod, 2026-09-09)

| Component | Evidence |
|---|---|
| Asterisk 20.6.0 (`asterisk.service`) | up since **2026-07-03**, `NRestarts=0`, 27 MB RSS, 0.4 % CPU. SIP UDP 5060 bound on `0.0.0.0` (public); ARI 8088 loopback only. |
| Media gateway (`talky-voice-gateway.service`, `/opt/talky/runtime/bin/voice_gateway --host 127.0.0.1 --port 18080`) | up since **2026-09-02**, `NRestarts=0`, 4 MB RSS, loopback only. `/health` ok, `io_loop_healthy`, protocol v2, codec pcmu. `/stats`: 19 sessions started = 19 stopped, 0 reaped, 0 timeouts, 0 drops. |
| Host | load 0.1, 149 days up, RAM 3.9 GB (2.5 GB available), disk 60 %. |
| Gateway journal, 7 days | only the Sep 2 restart lines — the gateway emits **no per-call log lines** (stderr line logs, rate-limited failures only). |
| Backend audio-health signals, 7 days | 180 `barge_in`, 99 `barge_in_detected`, 154 `gateway_fmt`, 33 `rtp_port`, 1 `gateway_audio_rejected`; **0** underrun / jitter / RTP-gap / STT-watchdog events. |
| Asterisk `messages.log` | 603 MB (May 14 → Sep 6) + 5.5 GB `messages.log.0` (→ May 14). 99.8 % of lines are SIP scanner noise (`REGISTER`/`INVITE … No matching endpoint` from **418** internet IPs). Current rate is tiny (1–39 lines/day in Aug–Sep); the flood was earlier. `logrotate` covers `messages`/`full`/`*_log` but **not** `messages.log`, which is why one 5.5 GB file exists. fail2ban runs with only the `sshd` jail. |

Asterisk config files are `asterisk:asterisk 640` — not readable without sudo, so the live
PJSIP/RTP settings are taken from the repo's `setup-asterisk.sh` (the documented source of the
prod config): UDP transport `0.0.0.0:5060`, `external_media_address` = server IP, RTP 10000–20000,
`disallow=all / allow=ulaw,alaw`, `direct_media=no`, `rtp_symmetric`, `force_rport`,
`rewrite_contact`, `qualify_frequency=60`. Memory notes a known drift (A5) between the file on
disk and the script; a sudo read would settle the exact live values.

## Structure of the edge (from code, file:line in the two review sections below)

Asterisk (ARI ExternalMedia, UNICASTRTP) ⇄ **RTP/UDP, G.711 µ-law 8 kHz 20 ms** ⇄ C++ gateway
(3 threads per call: receiver / transmitter / watchdog, plus one callback-sender thread) ⇄
**HTTP** ⇄ Python backend. There is **no WebSocket** between gateway and backend: caller audio is
POSTed in batches to `/api/v1/sip/telephony/audio/{session_id}` over a reused connection with
`X-Internal-Service-Token`; TTS comes back as POSTs into the gateway (`/v1/sessions/tts/play`,
`/tts/interrupt`). The backend resamples for STT/TTS; the gateway does no resampling.

## Findings, ranked by consequence

1. **The deployed gateway binary is four fixes behind the source.** `/health` reports
   `build_sha=a4aa0c56` (built Sep 2). Commits `1df87f5b`, `de3373c1`, `3f8bc744`, `faf59b51`
   (Sep 6, +397/−92 across 6 files, all with tests) are not in it. They matter: `faf59b51`
   wires callback-delivery health into the dead-media watchdog (today a backend outage leaves the
   call up while caller speech is silently discarded — the #1 risk in the code review), `de3373c1`
   makes TTS queue admission atomic, `1df87f5b` keeps `/stats` counters across session retirement
   (which is why every media counter reads 0 today despite 19 calls). Remedy: run the gateway
   release build + `run_gate.sh` and deploy the binary via `deploy_to_server.sh`.
2. **Silent STT loss on backend outage** (source at `a4aa0c56`): RTP session stays `Active`
   while callbacks fail; the 256-frame caller queue (~5 s) overflows and drops oldest; no
   escalation to the call. Fixed in source by `faf59b51` (see 1).
3. **Media-stall watchdog drops the call after 8 s without RTP** (`active_no_rtp_timeout_ms`).
   Deliberate, but a transient RTP stall ends a live call with no recovery path. No such event in
   7 days (`timeout_events_total=0`).
4. **Asterisk is directly on the public internet with no SIP jail.** Scanner traffic reached
   ~2.9 M rejected requests from 418 IPs; today it is quiet, but nothing bans a determined
   brute-force. The trunk is authenticated, so this is noise and CPU/log risk, not access.
   Remedy: fail2ban `asterisk` jail on `messages.log`, or restrict 5060 to the carrier's IPs.
5. **Log rotation gap**: `messages.log` is not in the logrotate stanza; a 5.5 GB single file
   exists. One-line fix in `/etc/logrotate.d/asterisk` (sudo).
6. **Thread-per-request HTTP control plane capped at 256 handlers, hard-reject above.** A
   start storm degrades new-call setup rather than queueing. Irrelevant at today's volume.
7. **2-hour hard session ceiling** silently ends any longer call.
8. **Credentials are read once** (`INTERNAL_SERVICE_TOKEN`, gateway token): rotation requires a
   gateway restart.

What is **good**: zero restarts on both services; bounded queues everywhere (TTS 400 frames,
callback 256, reorder window 4); exception containment on every thread; ASan/TSan/UBSan gated
CI (`tests/run_gate.sh`); RTP source enforcement on in prod; SSRC-restart epoch flush; barge-in
with a send-completion barrier; build identity printed at startup and served on `/health`;
control plane and ARI bound to loopback only.

## Review A — C++ media gateway (`services/voice-gateway-cpp/`)

(see the reviewer's report, reproduced in the session transcript; key anchors:
`src/session.cpp:722,1032,1254` threads · `src/http_server.cpp:822-900,1125-1309` callback
sender · `session.h:70-75` codec/timeouts · `session.cpp:734-788` STT reorder ·
`session.cpp:673-691` interrupt barrier · `http_server.cpp:1766-1798` health/stats ·
`CMakeLists.txt:4-14` build SHA · `tests/run_gate.sh` gate.)

## Review B — backend side of the link (`backend/app/`)

- **Two channels.** ARI drives call control from `AsteriskAdapter` (`asterisk_adapter.py:1188-1229`: `/v1/sessions/start|stop|tts/play|tts/interrupt`); caller audio arrives at `POST /api/v1/sip/telephony/audio/{session_id}` (`telephony_bridge.py:3452`). Audio ingress auth is fail-closed on `INTERNAL_SERVICE_TOKEN` (`internal_auth.py:52-64`); the backend→gateway bearer (`VOICE_GATEWAY_AUTH_TOKEN`) is **omitted silently if unset** (`asterisk_adapter.py:1197-1204`) — asymmetric, worth making fail-closed.
- **Framing:** PCMU 8 kHz, 20 ms/160 B, protocol v2 with strict sequence/frame_count/ptime checks (`telephony_bridge.py:3497-3604`), gap/duplicate metrics. Resampling 8↔16 kHz for Deepgram Flux in `telephony_media_gateway.py:453-461`; egress hygiene before µ-law (`egress_audio_hygiene.py:43-72`).
- **Lifecycle:** inbound stays unanswered until admission commits (`telephony_bridge.py:453-467`); outbound pre-warms STT/TTS/LLM and refuses to ring on failure (`prewarm.py:529-553`); teardown order = cancel tasks → transcript → outcome + billing settlement (raises if not durable) → DNC purge → recording → media teardown (`lifecycle.py:4842-5309`).
- **Stability points (code review):** STT death is surfaced by `TerminalSTTError` (`audio_ingest.py:240-258`) — the only thing standing between a provider failure and 300 s of dead air; `provider_empty_stream` retried once, a second empty stream ends the turn with no fallback line (`tts_playback.py:260-315`); STT input queue drops oldest at 200 (`telephony_media_gateway.py:531-548`); one explicit TODO — a live ExternalMedia leg with no local session is not reconciled (`lifecycle.py:941-960`); `interrupt_tts` returns `ok=False` on double failure and callers must honour it (`asterisk_adapter.py:5222-5290`).
- **Observability:** `talky_gateway_audio_callback_batches_total`, `…missing_batches_total`, `…media_reconciliation_total`; log lines `gateway_audio_rejected`, `gateway_audio_sequence_gap`, `telephony_audio_gap`, `stt_input_queue_overrun`, `tts_empty_stream`, `tts_inter_chunk_timeout`. The backend checks gateway protocol/codec compatibility (`asterisk_adapter.py:970-1018`) but does not log the gateway `build_sha` — adding that one field would have made finding #1 visible in the journal.
- **Doc drift:** `backend/docs/websocket_protocol.md` describes a Vonage-style PCM16/16 kHz WebSocket with SESSION_START/PING frames that the Asterisk + C++ gateway path does not use. Marked as legacy in this commit.
- Tests: `test_asterisk_gateway_session_protocol`, `test_telephony_gateway_audio_validation`, `test_telephony_bridge_auth`, `test_interrupt_gateway_drain`, `test_telephony_media_gateway`, `test_telephony_media_reconcile`, `test_telephony_lifecycle_watchdog_hangup`, `test_telephony_zombie_reconcile`, `test_telephony_orphan_recovery_confirmation`, `test_voice_gateway_cpp_source_contract`.

## Recommended actions (owner decision; each restarts or touches prod)

1. Build and deploy the gateway at HEAD (`deploy_to_server.sh` builds it when the source changed; the Python-only hotfix path does not). Restarting the gateway drops any live call — do it in a quiet window.
2. `sudo`: add `/var/log/asterisk/messages.log` to `/etc/logrotate.d/asterisk`; add a fail2ban `asterisk` jail (or restrict UDP 5060 to the carrier's addresses).
3. Code (small): fail-closed on a missing `VOICE_GATEWAY_AUTH_TOKEN`; log `build_sha` from the gateway health payload at startup.

## Implementation (owner: "go ahead and implement them")

| Item | How | Status |
|---|---|---|
| Fail-closed gateway control auth | `AsteriskAdapter._gateway` raises `RuntimeError` when `VOICE_GATEWAY_AUTH_TOKEN` is unset instead of sending an unauthenticated request (prod_gate already required the token at startup; now the call site agrees). | code, this commit |
| Gateway build identity in the journal | `AsteriskAdapter._note_gateway_build` logs `voice_gateway build_sha=… protocol_version=… codecs=…` once per distinct build seen by `health_check`. | code, this commit |
| Tests | `tests/unit/test_asterisk_gateway_control_auth.py` (3): no request without token; bearer carried when set; build line logged once per change and never raises. | this commit |
| Gateway binary at HEAD | `ops_edge_0909.sh` step 4: worktree at the target commit → `build_voice_gateway_release.sh` (cmake, ctest, fail-closed startup proof) → refuse to swap if `active_sessions>0` → atomic install → restart → verify `/health` `build_sha` equals the target. | host script, owner runs |
| `messages.log` rotation | `/etc/logrotate.d/asterisk-messages-log`: weekly or 200 MB, keep 4, compress, `logger-reload`. Historical 5.5 GB file left in place. | host script |
| SIP jail | `/etc/fail2ban/jail.d/asterisk.local`: filter `asterisk` on `messages.log` (dry-run matched 193 952 of 199 981 scanner lines), 5 failures / 10 min → 1 h ban, `ignoreip` = localhost, this host, carrier `sip3.blazedigitel.com` (144.76.17.155). | host script |
| Config drift review | step 5 dumps the live `pjsip.conf` / `rtp.conf` / `logger.conf` / `pjsip.d` / dialplan (passwords filtered) for comparison with `setup-asterisk.sh`. | host script output |

Not done: the 2-hour session ceiling and 8-second stall watchdog are design choices, left as they
are; the `lifecycle.py:941` inverse-zombie reconcile TODO is a separate piece of work.

```text
backend/.venv/Scripts/python -m pytest tests/unit tests/security -q → 8910 passed, 8 skipped in 601.16s
ruff check app/ --select F --extend-ignore F401,F841 → All checks passed!
```

## Follow-up (same day): two new carrier accounts, and the reconciler that could never apply

Owner supplied two new PBX accounts on `sip3.blazedigitel.com` (users 940001 / 940002) for
the AllStateEstimation tenant (`1845a165`), to be visible and manageable from Settings.

1. **Trunks added through the API's own path** — `backend/scripts/add_sip_trunk.py` (new,
   tested) validates with `SIPTrunkCreateRequest`, encrypts with the same service, runs the
   endpoint's INSERT under the tenant RLS context and the endpoint's activate UPDATE. Rows
   `blaze-pbx-940001` / `blaze-pbx-940002`: active, registration on, status `checking`.
2. **First reconcile attempt blocked** ("Asterisk runtime verification failed; prior files were
   restored"). Cause 1: prod's `extensions.conf` never had `#include "extensions.d/*.conf"`,
   which `setup-asterisk.sh` requires and refuses to add. Added (backup kept:
   `extensions.conf.bak-20260909T071807Z`).
3. **Second attempt blocked identically — and left the runtime wrong.** On-disk `pjsip.conf`
   was already restored to `context=from-blazedigitel`, yet `pjsip show endpoint` still said
   `from-talky-inbound` five minutes later; a fresh `pjsip reload` fixed it (inbound context
   intact, 2 Stasis lines, all registrations up). Root cause in the reconciler:
   - `pjsip reload` returns before the reload completes; the proof ran in the same instant on
     the old state and "failed";
   - the rollback then issued a second `pjsip reload` while the first was still running;
     Asterisk answers "A module reload request is already in progress; please be patient" and
     drops it, and `_RELOAD_FAILURE_RE` (error/failed/unable/…) did not recognise that as a
     failure — so the reconciler reported a rollback it had not achieved;
   - the specific failing check was never surfaced (generic JSON).
4. **Fix** (`reconcile_pjsip_configs.py`): busy reloads are retried until accepted; the runtime
   proof is polled (up to `TALKY_ASTERISK_PROOF_TIMEOUT_S`, default 20 s) and the LAST failing
   check is the error; a rollback is proven by polling the shared endpoint back to the
   snapshot's prior context before "prior files were restored" may be claimed; the blocked JSON
   carries `cause` (fixed content-free strings). Tests: a stateful fake Asterisk whose runtime
   lags the CLI and drops busy reloads — proof waits, busy reload retried, rollback proven and
   named, un-landed rollback reported as such. Module: 47 passed.
5. Remaining for the owner: run `ops_reconcile_0909b.sh <sha> <prev>` (deploy + reconcile +
   read-back); then one inbound test call to +442046132300.

```text
after the reconciler fix: backend/.venv/Scripts/python -m pytest tests/unit tests/security -q -> 8918 passed, 8 skipped in 333.47s
ruff -> All checks passed!
```
