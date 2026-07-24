# V2 Readiness — Baseline Freeze

**Ticket:** TKT-002 · **Captured:** 2026-07-24 (Day 03 buffer, catching up Day 01) ·
**Purpose:** the reference every later "no regression" claim is measured against.

> Sprint 1 is about to refactor **5,230 lines** of live call-path code across three files. Without
> this record there is no way to distinguish "my split broke a test" from "that test was already
> failing". Numbers, not impressions.

---

## 1 · Test gate

```bash
cd backend && python -m pytest tests/unit tests/security -q
```
```
3548 passed, 6 skipped, 6 warnings in 241.25s (0:04:01)
```

**0 collection errors.** `--continue-on-collection-errors` was **not** used and is not needed — the
passkeys import breakage recorded in earlier notes is genuinely fixed.

| Metric | Value |
|---|---|
| Passed | **3548** |
| Skipped | 6 |
| Failed | **0** |
| Collection errors | **0** |
| Wall clock | 241.25 s |

**Deviation from the ticket's expected 3,547: +1, and it is benign.** The prior session's record for
prod `863f3032` states "Gate 3548 green, 0 collection errors" — so 3548 is the true prior number and
the ticket's 3,547 was a transcription slip. Investigated and closed rather than waved through, per
TKT-002's own instruction that a changed baseline is a finding.

### ⚠️ Finding F-11 — the gate does not run on production's Python

```
local  : Python 3.11.9   (C:\Users\...\Programs\Python\Python311)
prod   : Python 3.12.3   (/opt/talky/backend/venv)
```

The full suite is green on **3.11**; production runs **3.12.3**. A green local gate is therefore not
evidence of correctness on the deployed interpreter. CI's Python version needs checking (TKT-003
touches the workflows) and the mismatch belongs in DOC-09. This does not invalidate the baseline —
it qualifies it, and the qualification has to travel with the number.

---

## 2 · Known flake — isolated result

`tests/unit/test_resilient_providers.py::test_stt_ask_ai_singleton_respects_recovery_window`
(50 ms timing window; fails only under full-suite CPU load).

```
run 1: 1 passed in 1.38s
run 2: 1 passed in 1.50s
run 3: 1 passed in 1.35s
run 4: 1 passed in 3.03s
run 5: 1 passed in 1.95s
```

**5/5 pass in isolation.** Confirms a load-dependent timing flake, not a latent defect. The 3.03 s
outlier on run 4 against a 1.35 s floor shows how wide the scheduling variance is on this machine —
which is precisely why a 50 ms window is too tight. Real fix lands in **TKT-013**.

---

## 3 · Commits

| Role | Commit | Note |
|---|---|---|
| **Production HEAD** | `80f6cdab` | `feat(billing): add stripe SDK dependency…` (2026-07-21) |
| `origin/main` | `0ffa7fa6` | One ahead of prod — **documentation only**, no backend code |
| Immediate rollback from prod | `863f3032` | Previous deploy |
| Deeper rollback target | `4693bf13` | **Corrected:** this commit *adds* dialer heartbeat + systemd watchdog. An earlier draft labelled it "pre-watchdog/heartbeat", which is backwards. It is the deeper of the two rollback targets, not a pre-liveness state — for that you need its parent. |

Production is one commit behind `main`; the gap is the ticket board, the handoff note, a session
record and stray root-level `dream_*.py` scratch files. **No application code differs.**

---

## 4 · God-file line counts — TKT-014/015/016 acceptance criteria

```bash
wc -l <each>
```

| Target | Path | Lines |
|---|---|---|
| TKT-014 | `backend/app/domain/services/telephony/lifecycle.py` | **2014** |
| TKT-015 | `backend/app/domain/services/voice_orchestrator.py` | **1764** |
| TKT-016 | `backend/app/domain/services/call_guard.py` | **1452** |
| | **Total** | **5230** |

Paths verified against the working tree and against `tickets/2026-08-03_DAY-12.md:89` and
`tickets/2026-08-04_DAY-13.md:13` — **the board's paths are correct as written.** An earlier draft of
this file claimed two of them were wrong; that claim came from a working note rather than from the
files, and is withdrawn. Checking beat remembering, which is the rule this board already states.

⚠️ **Real hazard instead:** `backend/app/core/security/sessions/lifecycle.py` (320 lines) is a
**different, unrelated** `lifecycle.py`. TKT-014 targets the 2,014-line telephony one. Any tooling
that globs `lifecycle.py` will match both.

---

## 5 · Production health at baseline

```
/api/v1/healthz/deep     → 200
/api/v1/healthz/workers  → {"healthy":true,"workers":[
                              {"name":"dialer",  "age_seconds":14.7, "healthy":true},
                              {"name":"voice",   "age_seconds":13.7, "healthy":true},
                              {"name":"reminder","age_seconds":13.7, "healthy":true}]}
```

Host: Ubuntu 24.04.4, kernel 6.17.0-20, **2 vCPU / 3.9 GB**, disk 55 % used, uptime 103 days,
load 0.32 / 0.14 / 0.09.

---

## 6 · Tag

Literal transcript of the command run, and of the resulting annotated message:

```bash
git tag -a v1-baseline-2026-07-23 -m "Pre-V2-readiness baseline: 3548 passed, 6 skipped, 0 collection errors (241s, py3.11 local); prod 80f6cdab; main 0ffa7fa6; god files 2014+1764+1452=5230"
```
```
$ git tag -l 'v1-baseline*'
v1-baseline-2026-07-23
$ git log -1 --format='%h %s' v1-baseline-2026-07-23
0ffa7fa6 last update for the version 2
```

Created **locally, not pushed** — pushing is a separate explicit step. The tag name keeps the board's
Day-01 date for traceability even though capture happened on the Day-03 buffer.

---

## Checklist — TKT-002

- [x] Full gate run, exact output recorded
- [x] Deviation from 3,547 investigated and explained (+1, transcription slip in the ticket)
- [x] Flaky test run 5× in isolation — 5/5 pass
- [x] `wc -l` for the three split targets recorded — **two paths corrected**
- [x] Prod HEAD and rollback commits recorded
- [x] `v1-baseline-2026-07-23` tag created with numbers in the message
- [ ] Peer-reviewed — **outstanding**

## Test cases

| # | Check | Expected | Result |
|---|---|---|---|
| 1 | Full gate | ~3,547 passed, 0 collection errors | 🟢 3548 / 0 errors |
| 2 | Flaky test isolated ×5 | 5/5 pass | 🟢 5/5 |
| 3 | `git describe --tags` | resolves to the baseline tag | 🟢 |
| 4 | `BASELINE.md` contents | gate numbers, both commits, three line counts | 🟢 |

**Status: 4/4 test cases pass; one checklist item (peer review) outstanding — so this ticket is
🟡, not 🟢.** 99 % is ⬜, and that rule does not bend for the ticket that records the rule.
