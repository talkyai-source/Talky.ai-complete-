# Production acceptance — 2026-09-10

Status: incomplete; no production mutation in this acceptance run.

## Execution board

Owner: this agent. Shared root changes are excluded; candidate testing uses an isolated worktree.

| Requirement | Acceptance evidence | State |
|---|---|---|
| Corrected Asterisk reconciliation | Supported deployment output; candidate/live digest convergence; runtime routing checks | Operator access and drain approval required |
| SIP registrations | Fresh privileged `pjsip show registrations`, every expected registration successful | Previous operator report says seven; not independently verified live |
| Latest gateway | Official builder tests, missing-token startup rejection, running `/ready` SHA equals frozen candidate | Current gateway healthy; exact candidate not deployed |
| Inbound greeting | Controlled carrier call ID plus received audio matching configured greeting | Test assignment paused |
| Transfer success and failure | Controlled destinations, two-leg evidence, failure recovery, teardown and settlement | Production capability closed; scoped staging proof required |
| Recording/transcript/summary/billing | Consent delivered; stored recording accessible; transcript and summary correspond to call; duration/settlement reconciled | Recording disabled, no consent wording; test not run |
| Outbound regression | Approved test destination, durable call/job IDs, two-way audio, terminal outcome and billing | Destination not provided |
| Freeze and rollback | Exact source/config/binary hashes, preserved rollback artifacts, executable supported recovery | Source identifiers captured; privileged config backup and rollback proof pending |

## Fresh baseline

- Candidate: `ce91f02bfdd5d25a740d8b1b06fbd0aaeb98f539` (`origin/main`).
- Production: `dca612a68840a05101b879de0ab36a881dde488b`; `git status --porcelain` empty.
- Gateway `/ready`: `ready=true`, protocol 2, PCMU, build `ca8a136a691f0432f774ff27394759c5667c2360`.
- `git diff ca8a136a..origin/main -- services/voice-gateway-cpp` empty. Source equivalence does not satisfy exact deployed build identity.
- API health, readiness and deep health: HTTP 200.
- ARI authenticated queries: zero channels and bridges. Gateway active sessions: zero. This is not proof ingress is disabled.
- Alembic verifier: `ALEMBIC HEAD CHECK PASSED: database=['0045_refresh_session_binding'] repository=['0045_refresh_session_binding']`.
- `sudo -n true`: `sudo: a password is required`.
- Unprivileged Asterisk CLI cannot access its config/control socket; not evidence that the active Asterisk service is down.
- Platform controls: inbound enabled; outbound not paused; recording and transfer disabled; inbound settlement enabled.
- Test DID `+442046132300`: current config/assignment paused; opening mode agent-first; recording off, no consent message, no transfer destination.
- Config `2ab6f5b3-f216-46ac-98ec-d9367d1e39c8` checksum: `ce8c6d234ed75984814a2c6feafa45f7eec714fac8946a592eecc9c31f5ecab2`.
- Greeting: “Thanks for calling All State Estimation, this is Sarah. How can I help you today?” Config presence is not audible proof.

## Pre-mortem / stop conditions

- A zero-call snapshot can race new ingress: require the existing approved, candidate-bound drain procedure; do not invent approvers or claim traffic is disabled.
- Reconcile while the assignment is paused renders no active account-to-DID route. Resume must be explicit, followed by reconciliation and runtime route proof before calling.
- Do not enable production transfer by flipping its code-owned guard. Use the existing scoped staging acceptance procedure and controlled destinations first.
- Do not enable recording without approved consent and verification that the notice is spoken.
- Do not dial arbitrary leads for regression testing; use an owned, approved test endpoint.
- Do not equate API success, generated text, TTS requests or sent RTP with speech actually received by the caller.
- Do not equate a matching database migration with correct billing; reconcile actual answered/ended timing and settlement for each test leg.
- Do not claim immediate rollback merely from knowing an old SHA. Preserve compatible binary/configuration and prove traffic control before any restart.

## Pending external prerequisites

Operator-assisted sudo; fresh independent drain approvals; approved test campaign resume; controlled outbound and transfer destinations; recording consent wording; staging transfer proof environment.

No deployments, configuration writes, restarts, recordings or calls were performed in this acceptance run.

## Candidate verification and reproduced gateway blocker

Testing uses `C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-acceptance-ce91f02b`.
Source changes are confined to the gateway HTTP handler and its regression/CMake registration; no shared-root application edits are included.

- Official builder against unchanged `ce91f02b`: **failed**, `67% tests passed, 1 tests failed out of 3`; `voice_gateway_fixes_tests` aborted after 68.79 s. It did not publish a candidate binary.
- A debug build reproduced SIGABRT. Stack: `std::thread::~thread` → `HttpServer::HandlerSlot::~HandlerSlot` → `HttpServer::spawn_handler`; the main thread was joining the accept loop in `test_truncated_request_no_hang`. Buffered output's last VG18 line did not identify the actual crashing test.
- Root cause: slot insertion is mutex-protected, but thread construction/assignment was outside that critical section. Shutdown could remove and destroy the slot before its child thread was attached. The remaining raw pointer then refers to freed storage.
- Fix: keep slot admission, child-thread publication, and construction-failure rollback under the existing handler mutex. Joins remain outside the mutex. Failed admission still returns 503 and closes its socket.
- A first 300-iteration socket stress test passed old code and was rejected as insufficient regression proof. The replacement test interposes `pthread_create` in the **test executable only**, pauses at the actual unpublished-thread boundary, and invokes real `HttpServer::stop`. No production test hooks were added.
- Regression before fix: `FAIL: stop returned before handler ownership was published`; 0/1 passed.
- Regression after fix: ten consecutive runs passed; each proves stop waits for publication and finishes after release.
- Full post-fix debug C++ suite: `100% tests passed, 0 tests failed out of 4`, 140.29 s.
- Full ASan/UBSan C++ suite: `100% tests passed, 0 tests failed out of 4`, 129.51 s.
- Official release helper against frozen fix commit `45368a7a76fe150bf6d7d0990854be24d9d638de`: exited 0; `100% tests passed, 0 tests failed out of 4`, 144.09 s. The helper also enforces missing-token startup exit 2 before publishing its temporary output.
- Candidate binary: `/tmp/talky-acceptance-45368a7a.ePCqex/voice_gateway`; SHA-256 `6074d33be72c210122c8a5e873c32e7ddcee546c9e3c37830d5fd988dc8f03dc`.
- Isolated runtime smoke test used temporary random credentials and an ephemeral loopback port, not the live service environment. `/ready` returned `ready=true`, build `45368a7a76fe150bf6d7d0990854be24d9d638de`, protocol 2 and PCMU; SIGTERM teardown exited 0. No sessions or calls were originated by this smoke test.
- Talk-Leee: typecheck and lint exited 0; `tests 468`, `pass 466`, `fail 0`, `skipped 2`.
- Admin: lint exited 0; `tests 13`, `pass 13`, `fail 0`; Vite production build exited 0, 1764 modules transformed.
- Ruff: `All checks passed!`.
- Backend first full run: `1 failed, 8926 passed, 8 skipped, 1476 warnings in 745.20s`. Failure: existing soak cleanup test observed 8.937 s against its unchanged `<6 s` limit. Its isolated rerun passed (`1 passed in 5.78s`). This is timing-sensitive evidence, not proof of its underlying cause; no assertion was loosened.
- Full backend rerun: **`8927 passed, 8 skipped, 1476 warnings in 704.86s (0:11:44)`**. Command: `backend/.venv/Scripts/python -m pytest tests/unit tests/security -q --disable-warnings` using the established venv from the isolated candidate's backend directory. `--disable-warnings` suppresses the warning listing, not tests/assertions. Backend, Talk-Leee and Admin content is unchanged between base `ce91f02b` and gateway fix `45368a7a` (`git diff` empty for those trees).

## Additional live evidence

- All 17 active tenant trunk rows reported `registered`, checked 14 seconds before the query at 2026-09-09 21:35 UTC. They represent seven distinct trunk names; repeated `blaze-primary` rows share the platform registration. This is fresh timer-derived evidence, not seventeen independent carrier registrations.
- Running gateway SHA-256: `32e92446c0002cea77e06e5a421907944fe4683ea528001dc748eff591033cf6`.
- Unit ExecStart is `/opt/talky/runtime/bin/voice_gateway --host 127.0.0.1 --port 18080`; PID remains `847595`, restarts `0`.
- Inbound synthetic timer is inactive; trunk-status timer is active.
- Production source still `dca612a6`; sudo still requires an operator password.
- Build/debug artifacts are isolated at `/tmp/talky-acceptance-ce91.viooW5`; they are not installed production artifacts. Temporary debug build identity is not a released commit claim.
- Preserved `/tmp/talky-acceptance-ce91.viooW5/rollback-voice_gateway-ca8a136a`; `cmp` matched the live binary, and both SHA-256 values were `32e92446c0002cea77e06e5a421907944fe4683ea528001dc748eff591033cf6`. A temporary binary copy alone is not a complete/durable rollback bundle: protected live configuration and approved ingress control remain missing.
- Copied candidate, rollback binary and exact source archive into `/home/admins/talky-acceptance-45368a7a/`, owned by admins with directory mode `0700`, to survive temporary-directory cleanup. Candidate and rollback hashes match the values above. Source archive SHA-256: `c305a50893648f0a868f38775b7e0b6c259e0520a929cfcdcbd005e20d7393bd`. These are acceptance/backup artifacts only; the supported deploy must still perform its own build and gates.
- The isolated worktree reports `telephony/deploy/keepalived/notify.sh` as modified by line-ending normalization, but `git hash-object --no-filters` exactly equals its index blob (`7b85fd8f3503782323e8ac361115b70f9bb99194`). Its committed bytes are CRLF despite the LF attribute. This unrelated file was not edited or included in the gateway commit.

## Handoff

- Gateway fix pushed to `origin/main`: `45368a7a76fe150bf6d7d0990854be24d9d638de`, independently read back with `git ls-remote`.
- Commit author/committer: `tooti12 <umar.jwork@gmail.com>`; subject and one body line, no trailers. Only three named gateway files committed; this report and shared-root changes were not pushed.
- This is **not a production deployment**. No live greeting, transfer, recording, transcript, summary, billing or outbound regression acceptance has been claimed.
- Next operator step remains the supported release path with fresh drain approvals and sudo, plus the test campaign/destination/consent choices listed above. Do not substitute the temporary candidate or a bespoke deployment script to bypass those gates.

## GitHub release-gate audit (following push)

These checks add evidence beyond the local unit/build commands; the release is not globally green.

- Commit under test: `45368a7a76fe150bf6d7d0990854be24d9d638de`.
- C++ CMake production job `102661634745`: completed successfully in run `34409896926`; `100% tests passed, 0 tests failed out of 4`, 141.83 s. Separate warning/TSan/ASan/UBSan/shutdown job `102661635096` also completed successfully at 2026-09-09 22:08:47 UTC. Its log ends `GATE: PASS`. Entire gateway workflow conclusion: success.
- General CI run `34409896970`: **failure**. Secret scans, SQL schema validation and telephony ingress safety succeeded; frontend dependency audits and the backend preserved-baseline integration job failed. Docker build/scan was skipped, not passed.
- Admin dependency audit: `2 vulnerabilities (1 moderate, 1 high)`; high finding is `js-yaml` GHSA-2883-xcg3-v3hh. Audit exited 1, so CI lint/test/build steps were skipped (the earlier local checks did run).
- Talk-Leee audit: `9 vulnerabilities (1 low, 5 moderate, 3 high)`; high findings include `js-yaml`, `nodemailer`, and `sharp`. This is the audit's reported inventory, not proof of application exploitability. No broad `npm audit fix --force`, package upgrades or audit bypass was performed.
- Backend integration failure: `test_current_head_cli_downgrade_refuses_without_moving_marker` expected a refused downgrade, but `alembic downgrade -1` returned 0 and moved from `0045_refresh_session_binding` to `0044_webhook_null_tenant_rls` on the disposable CI database. Result: `1 failed, 12 passed, 1 skipped in 4.49s`. Source confirms 0045 drops the refresh-session index and column in its downgrade. This is a real source/test contract disagreement; no production downgrade occurred and no assertion was relaxed. Resolving the migration policy needs a separately scoped decision, not a silent test edit in a gateway release.
- Independent backend voice workflow `34409896923`: fast voice pipeline job succeeded; full unit/security job **failed** with `23 failed, 8898 passed, 5 skipped, 1478 warnings, 9 errors, 18 subtests passed in 253.60s`. All reported failures/errors are CRM/Salesforce test calls or fixtures raising `RuntimeError: There is no current event loop in thread 'MainThread'`. This contradicts a blanket cross-environment green claim despite the successful local rerun. No claim is made that these fixture failures prove a production CRM outage; their environment/lifecycle cause remains outside this gateway fix.
- Third goal-turn production recheck: source remains `dca612a6`, live gateway remains `ca8a136a`, API/gateway active, synthetic timer inactive, sudo still requires a password. Test DID is still paused; recording/transfer disabled; no consent or transfer destination configured. No drain-manifest environment variables or new operator/test inputs were supplied.

Links: [general CI](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/34409896970), [gateway CI](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/34409896926), [voice tests](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/34409896923).

## Blocked acceptance outcome

- Three consecutive goal turns confirmed the missing operator-assisted sudo/drain prerequisites. Controlled destinations, consent wording, test campaign resume and scoped transfer proof inputs remain missing. No safe live deployment or complete call acceptance can be performed without those external inputs.
- All observed CI runs for `45368a7a` are terminal: gateway success; general CI and independent backend suite failure. Their failures were not bypassed or silently edited away.
- While these runs completed, another contributor advanced main to `39da72cf5f8048666d45566a2f2292490738b8b9` (session middleware and database-backup work). It contains `45368a7a` and has no additional C++ gateway changes. Its new backend/systemd changes were **not verified or deployed by this agent**. The frozen, tested gateway artifact remains `45368a7a`; do not relabel it as `39da72cf`.
- Final readback: production source `dca612a68840a05101b879de0ab36a881dde488b`, gateway `ca8a136a691f0432f774ff27394759c5667c2360`, sudo password still required. No live configuration or service mutation in this acceptance run.
- Goal is blocked, not complete. Resume requires operator coordination and controlled-call choices; the newly observed dependency, migration-contract and async-test CI failures also require scoped remediation before claiming an end-to-end green release.
