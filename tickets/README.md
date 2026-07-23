# Talky.ai — V2 Pre-Launch Readiness · Ticket Board

Derived from **"TALKY.AI V2 Pre-Launch Readiness Document" (20 July 2026)**, 42 tasks / 4 phases.
Re-scoped, re-sequenced and re-estimated against the **actual state of the repo and production on 2026-07-23**.

> Read **[00-PLAN-REVISIONS.md](./00-PLAN-REVISIONS.md) first.** It lists every place the original
> document's assumptions were wrong, with the evidence, and what changed as a result. Nine of the
> 42 tasks could not be executed as written.

---

## Window

**Start 2026-07-23 (Thu) · End 2026-08-21 (Fri) · 30 calendar days · 22 working days.**

Weekends are **buffer days**, deliberately unallocated. They are the schedule's slack: spillover,
sickness, an incident, a fix that turns out to be twice the size. A 30-day plan with zero slack is a
30-day plan that fails on day 4. If a buffer day passes unused, that is the plan working.

## Sprints

| Sprint | Days | Dates | Output |
|---|---|---|---|
| **1 · Code stabilisation & ground truth** | 01–13 | Jul 23 – Aug 04 | Original Phase 3. Moved to the **front** — see revision R-1. Ends at a tagged code freeze. |
| **2 · Written documentation** | 14–21 | Aug 05 – Aug 12 | Original Phase 1. 12 canonical docs, written against frozen code. |
| **3 · Recorded walkthroughs** | 22–28 | Aug 13 – Aug 19 | Original Phase 2. 10 recordings, each scripted from its Sprint-2 doc. |
| **4 · Final review & sign-off** | 29–30 | Aug 20 – Aug 21 | Original Phase 4. Confirmation passes only — review is *rolling*, see revision R-9. |

## Day index

| Day | Date | Sprint | Tickets | Status |
|---|---|---|---|---|
| 01 | Thu 2026-07-23 | 1 | TKT-001 ground truth · TKT-002 baseline freeze | ⬜ |
| 02 | Fri 2026-07-24 | 1 | TKT-003 deploy-path reconciliation · TKT-004 live validation call | ⬜ |
| 03 | Sat 2026-07-25 | — | **BUFFER** | ⬜ |
| 04 | Sun 2026-07-26 | — | **BUFFER** | ⬜ |
| 05 | Mon 2026-07-27 | 1 | TKT-005 STT connection lifecycle (build) | ⬜ |
| 06 | Tue 2026-07-28 | 1 | TKT-006 STT lifecycle ship · TKT-007 Flux concurrency guard | ⬜ |
| 07 | Wed 2026-07-29 | 1 | TKT-008 Nova parity + email truncation · TKT-009 RLS `SET LOCAL` audit | ⬜ |
| 08 | Thu 2026-07-30 | 1 | TKT-010 PgBouncer · TKT-011 Twilio/Vonage session registry | ⬜ |
| 09 | Fri 2026-07-31 | 1 | TKT-012 Stripe live mode · TKT-013 debt sweep | ⬜ |
| 10 | Sat 2026-08-01 | — | **BUFFER** | ⬜ |
| 11 | Sun 2026-08-02 | — | **BUFFER** | ⬜ |
| 12 | Mon 2026-08-03 | 1 | TKT-014 split `lifecycle.py` · TKT-015 split `voice_orchestrator.py` | ⬜ |
| 13 | Tue 2026-08-04 | 1 | TKT-016 split `call_guard.py` · TKT-017 **CODE FREEZE** | ⬜ |
| 14 | Wed 2026-08-05 | 2 | DOC-01 call flow · DOC-02 architecture | ⬜ |
| 15 | Thu 2026-08-06 | 2 | DOC-03 C++ gateway · DOC-04 telephony stack | ⬜ |
| 16 | Fri 2026-08-07 | 2 | DOC-05 audio pipeline · DOC-08 AI providers | ⬜ |
| 17 | Sat 2026-08-08 | — | **BUFFER** | ⬜ |
| 18 | Sun 2026-08-09 | — | **BUFFER** | ⬜ |
| 19 | Mon 2026-08-10 | 2 | DOC-06 environment variables · DOC-07 database schema | ⬜ |
| 20 | Tue 2026-08-11 | 2 | DOC-10 deployment runbook · DOC-11 incident runbook | ⬜ |
| 21 | Wed 2026-08-12 | 2 | DOC-12 third-party services · DOC-09 known issues & tech debt | ⬜ |
| 22 | Thu 2026-08-13 | 3 | VID-13 codebase walkthrough | ⬜ |
| 23 | Fri 2026-08-14 | 3 | VID-14 live call trace · VID-19 AI pipeline deep dive | ⬜ |
| 24 | Sat 2026-08-15 | — | **BUFFER** | ⬜ |
| 25 | Sun 2026-08-16 | — | **BUFFER** | ⬜ |
| 26 | Mon 2026-08-17 | 3 | VID-17 telephony config · VID-18 database walkthrough | ⬜ |
| 27 | Tue 2026-08-18 | 3 | VID-15 deployment end-to-end · VID-16 debugging a failed call | ⬜ |
| 28 | Wed 2026-08-19 | 3 | VID-20 campaign management · VID-21 observability walkthrough | ⬜ |
| 29 | Thu 2026-08-20 | 4 | VID-22 emergency procedures · REV-39 verify 24 fixes · REV-37/38 completeness | ⬜ |
| 30 | Fri 2026-08-21 | 4 | REV-40 gap analysis · REV-41 architecture review · REV-42 **SIGN-OFF** | ⬜ |

Status legend: ⬜ not started · 🟡 in progress · 🟢 done & verified · 🔴 blocked · ⬛ dropped (with reason)

**A ticket is only marked 🟢 when every checklist box is ticked and every test case passes.**
99% is ⬜. This is the standing rule on this project and it applies here without exception.

---

## Mapping — original 42 tasks → tickets

| Original | Ticket | Note |
|---|---|---|
| 1 call flow doc | DOC-01 | Corrected: `post_call_analyzer.py` is **dead code** (R-3) |
| 2 architecture overview | DOC-02 | Depends on TKT-001 ground truth |
| 3 C++ gateway | DOC-03 | Scope depends on TKT-001: is it running at all? (R-4) |
| 4 telephony stack | DOC-04 | Rewritten: live vs scaffold (R-5) |
| 5 audio pipeline | DOC-05 | |
| 6 env variables | DOC-06 | Re-scoped: 245 vars, not "the .env file" (R-6) |
| 7 database schema | DOC-07 | Re-scoped: ~70 tables, not 36 (R-7) |
| 8 AI providers | DOC-08 | |
| 9 known issues / tech debt | DOC-09 | Moved last in Sprint 2 — it synthesises the other 11 |
| 10 deployment runbook | DOC-10 | Blocked on TKT-003 (two contradictory deploy paths, R-2) |
| 11 incident runbook | DOC-11 | |
| 12 third-party services | DOC-12 | |
| 13–22 videos | VID-13 … VID-22 | 21 rewritten (R-8) |
| 23 fixes 1–3 sample rate | ⬛ **DROPPED** | Premise refuted with 14 days of production evidence (R-10) |
| 24 fixes 4–5 barge-in | 🟢 **already done** | Fixed properly; the doc's 3-second grace was rejected (R-10) |
| 25 fix 6 soxr | 🟢 **already done** | |
| 26 fixes 7–8 heartbeat | 🟢 **already done** | Alert delivered as healthwatch timer, not Prometheus (R-8) |
| 27 fixes 9–10 Stripe | TKT-012 | Fix 10 done; fix 9 needs the live key |
| 28 fixes 11–12 tenancy | 🟢 **already done** | |
| 29 fix 13 PgBouncer | TKT-009 → TKT-010 | Gated behind an RLS audit (R-11) |
| 30 fixes 14–15 | TKT-011 | Fix 14 done; fix 15 redesigned (R-12) |
| 31 fix 16 circular import | 🟢 **already done** | Locked by an AST test |
| 32 fixes 17–18 systemd | 🟢 **already done** | SIGKILL recovery proven live |
| 33 fix 19 CI blocking | 🟢 **already done** | Doc pointed at a dead file (R-13) |
| 34 fixes 20–21 | 🟢 **already done** | DNC caching deliberately rejected — compliance |
| 35 fixes 22–24 splits | TKT-014/015/016 | 5,230 lines → three days, not one (R-14) |
| 36 full suite passing | TKT-017 | |
| 37 read every doc | REV-37 | Rolling, not batched (R-9) |
| 38 watch every recording | REV-38 | Rolling |
| 39 verify 24 fixes live | REV-39 | |
| 40 gap analysis | REV-40 | |
| 41 architecture review | REV-41 | |
| 42 sign-off | REV-42 | |

**19 of the 24 code fixes are already done and deployed** (prod `80f6cdab`). Sprint 1 is the
remaining 5 plus their real prerequisites — not a re-run of the whole fix document.

---

## Ground rules (from the original document, kept)

1. All documentation is written by the engineering team, not auto-generated and shipped unread.
2. All walkthroughs are screen-recorded and saved to the shared team drive.
3. Nothing moves to v2 development until every item here is 🟢.
4. Documentation is a first-class deliverable — not optional, not rushed, not abbreviated.
5. Every doc is reviewed by at least one other team member before being marked complete.

## Ground rules (added, project-specific)

6. **Production is live and calls real people.** Every ticket carries a Don't list. Read it.
   The master safety list is `HANDOFF-NEXT-AGENT.md` §3 and it overrides anything here.
7. **No claim without evidence.** "Verified" means a command was run and its output pasted into
   the ticket. A subagent's report is not evidence. A green test is evidence.
8. **Docs reference modules and functions, never line numbers.** Line numbers rot within a week —
   the god-file splits in Sprint 1 would invalidate every one of them.
9. **Anything found to be false during a ticket gets written into DOC-09**, immediately, not
   remembered. DOC-09 is the honest-assessment doc and it is only honest if fed continuously.

---

## Conventions

- **One file per calendar day**, named `YYYY-MM-DD_DAY-NN.md`.
- Each ticket has: Goal · Why · Depends on · Blocks · Plan · Do · Don't · Checklist · Test cases · Deliverable.
- Deliverable docs land in `docs/v2/`. Recordings land on the shared drive with the exact title
  given in the ticket.
- Update the **Status** column in this file at the end of each day. That is the single source of
  truth for progress.
