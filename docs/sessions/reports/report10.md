# Report 10 — 2026-08-22

**Production moved `e00dfa2e` → `5a007900`.** Five commits, four migrations applied
(`0012`…`0015`), 15 working hours.

---

## Contents

| § | |
|---|---|
| 1 | [Hours](#1-hours) |
| 2 | [What shipped](#2-what-shipped) |
| 3 | [Voice feedback: verified end to end, and one finding it exposed](#3-voice-feedback-verified-end-to-end) |
| 4 | [goals.md and the honest gap analysis](#4-goalsmd-and-the-gap-analysis) |
| 5 | [Prompt identity — P0 #2](#5-prompt-identity--p0-2) |
| 6 | [RLS was decorative](#6-rls-was-decorative) |
| 7 | [Admin media controls and a pause that works](#7-admin-media-controls-and-a-pause-that-works) |
| 8 | [Super admin, sessions, role boundaries](#8-super-admin-sessions-role-boundaries) |
| 9 | [Conversation reviews — P0 #3](#9-conversation-reviews--p0-3) |
| 10 | [Things I got wrong today](#10-things-i-got-wrong-today) |
| 11 | [Open items](#11-open-items) |
| 12 | [The schedule, plainly](#12-the-schedule-plainly) |

---

## 1. Hours

| Stream | Hours | Outcome |
|---|---:|---|
| Voice feedback end-to-end verification | 2.0 | 19/21, RLS hole found |
| Report 9 | 0.5 | 1,181 lines |
| `goals.md` + codebase gap analysis | 1.5 | 11 P0s scored against real code |
| Prompt version + hash (#81, #90) | 2.0 | P0 #2 closed |
| RLS audit + Stage 1 (#80) | 2.5 | 12/18 → 18/20 |
| Admin media controls + pause (`0014`) | 2.0 | deployed, 19/19 |
| Super admin + session verification | 1.5 | 25/25 |
| Conversation reviews (#83) | 3.0 | P0 #3 closed, 64 checks |
| **Total** | **15.0** | |

---

## 2. What shipped

| Commit | |
|---|---|
| `8cec4dd6` | Call feedback voice notes (backend, migration `0012`) |
| `2a111ae5` | WhatsApp-style recorder (frontend) |
| `cc19971a` | Prompt version + hash |
| `c5f2559a` | Canonical RLS policies (migration `0013`) |
| `37a63bc7` | Admin media + persistent pause (migration `0014`) |
| `5a007900` | Conversation reviews (migration `0015`) |

**CORRECTION (added after review).** I first wrote "two P0 items closed, 1/11 →
3/11". That was overstated and the evidence contradicts it. Audited strictly:

| P0 | Claim | Reality |
|---|---|---|
| #1 generic lead-gen prompt in runtime | done | **done** — and was done before today |
| #2 prompt version + hash in call logs | done | **logging proven; persistence NEVER EXECUTED** |
| #3 per-conversation review storage | done | **storage/API/panel done; §3's admin-filter acceptance criterion not built** |

```
calls total: 1000   with prompt_version: 0
newest call: 2026-08-20   calls placed in the last 6h: 0
```

`_persist_prompt_identity` is deployed and has never run, because **no call has
been placed since 2026-08-20**. The code path is unexercised in production.

And §3 states as an acceptance criterion: *"Admin can filter results by campaign,
prompt version, rating and tag."* There is no admin review listing at all — a
grep for `conversation_reviews` outside its own endpoint file returns nothing.

Honest score at the time of writing: **1 fully done, 2 at roughly 80%.**

**UPDATE (2026-08-23, commit `a7219989`).** The #3 gap is now closed. The admin
review listing and aggregation were built, deployed and verified 13/13 live:
all four filter axes (campaign, prompt version, rating, tag), the Safe
Improvement Loop's two grouped queries, an unknown tag refused with 422 rather
than silently ignored, and a plain `user` role blocked from both endpoints with
403. All six of §3's acceptance criteria are now met.

**P0 #3 is done.** #2 remains at ~80% — its persistence path still has never
executed, because no call has been placed since 2026-08-20. Board: **2/11**.

One caveat that applies to both new UIs: neither the review panel nor the voice
recorder has ever been rendered in a browser. They typecheck, lint and build, and
their APIs are verified — but nobody has looked at them.

---

## 3. Voice feedback: verified end to end

Yesterday's feature was deployed but unproven. Today it was tested against real
Deepgram, real storage and real Postgres, using genuine WebM/Opus cut from an
actual call recording with ffmpeg — not synthesised audio.

```
PASS  audio file exists on disk
PASS  sha256 matches uploaded bytes
PASS  DEEPGRAM TRANSCRIBED IT       status=done
PASS  transcript is real text       'Quality and training purposes. Oh, hi. Hello?…'
PASS  same bytes again -> same row, no second transcription
PASS  different audio without replace -> 409
PASS  replace -> new row, SUPERSEDED FILE DELETED
PASS  still exactly one note        count=1
PASS  bytes round-trip identically  29487 bytes
```

19 of 21. The two non-passes were **not defects**:

- Retry didn't increment attempts — correct. `retry_transcription` short-circuits
  on `transcript_status == "done"`; there is nothing to retry, and it declines to
  spend another Deepgram call. My assertion was wrong.
- A cross-tenant row was visible under a bogus tenant GUC — which turned out to
  be §6.

### The prod storage decision

The service refused local-disk storage whenever `ENVIRONMENT=production`, on the
reasoning that *"container-local disk has no persistent volume in the production
compose topology."*

There is no compose topology. The API is `talky-api.service`, a **systemd unit on
the host**, and `/opt/talky/backend/recordings` held **428 recordings** across
every rollout. Production has **no `S3_*` configuration at all** — only
`DEEPGRAM_API_KEY` is set. As written, every upload would have returned 503.

Resolved with `CALL_FEEDBACK_ALLOW_LOCAL_STORAGE`, default **false** — a container
without a volume must still refuse, because acknowledging a note you are about to
lose is worse than declining it. S3 supersedes it automatically once configured.

---

## 4. `goals.md` and the gap analysis

The delivery checklist was saved to `goals.md` (851 lines, committed) and scored
against the actual codebase rather than assumed.

| § | P0 item | State | Evidence |
|---|---|---|---|
| 6 | Generic lead-gen prompt in runtime | done | `composer.py:251` |
| 6 | Prompt version + hash in logs | **closed today** | `cc19971a` |
| 11 | Expanded contact fields | ~14/40 | E.164, DNC, CSV import exist |
| 7 | Structured lead capture | 0 | zero hits |
| 3 | Per-conversation review | **closed today** | `5a007900` |
| 4 | Security in main sidebar | ~12/22 | all 10 pages exist |
| 5 | Inbound campaign MVP | 3/31 | `campaign_type` = 0 hits |
| 8 | Tooltips | 3/22 | component exists, content missing |
| 9 | Billing top-up | 1/21 | zero hits |
| 12 | 200-tenant validation | blocked | see §6 |

Roughly **48 of 419** feature checkboxes at the start of the day.

**The uncomfortable observation:** three of the day's four largest deliverables —
RLS, admin media/pause, the super admin — appear **nowhere in `goals.md`**. They
were real work and, in the RLS case, a live security hole, but they moved the
delivery checklist by zero.

---

## 5. Prompt identity — P0 #2

`telephony_prompt_composed` logged persona, agent, company, campaign and
`prompt_chars`. Everything except which instructions ran. Two calls on completely
different prompts were indistinguishable, so three release-gate lines were
uncheckable: *"every call has a prompt version"*, *"no old prompt used in any test
call"*, *"freeze one prompt version for every test batch."*

### Two identifiers, because they answer different questions

`prompt_version` is a name a human rolls back to. On its own it is a promise
nobody keeps — someone edits a persona, forgets to bump it, and every later call
is mislabelled with no signal at all.

`prompt_hash` is derived from the composed text. Unreadable, but it cannot go
stale. The registry pins them together and `test_prompt_versions.py` recomputes
each hash from the live prompt modules, so *"versions are immutable once used"* is
enforced rather than hoped.

**That mechanism was exercised on its first day.** Another session changed the
personas and correctly bumped to `lead_gen@2` / `customer_support@2` /
`receptionist@2` with new pinned hashes.

### Where identity is computed, and why it matters

After the compose `try`/`except`, not beside the compose call. Strict mode raises
before it (no prompt, no call); the `PromptCompositionError` path re-composes in
knowledge-driven mode and produces **genuinely different text**. Identifying
inside the `try` would attribute every retried call to a prompt it did not run.

### Proven to vary

```
Northwind Systems              hash=649abbfe1c46f760
Totally Different Ltd          hash=bd3be728fe14d363
Northwind + one slot changed   hash=d636bef6fd6d4411
Northwind + lead Sarah Khan    hash=649abbfe1c46f760   <- unchanged
```

One changed campaign slot moves the hash; threading a lead name does not. A hash
that varied per call would answer *"same prompt?"* with *"no"* forever — as
useless as one that never varies.

### Already satisfied, now pinned

Strict mode refuses to compose with missing slots and no unresolved
`{{placeholder}}` survives composition. Both are `goals.md` §6 requirements that
were already true; both now have tests.

---

## 6. RLS was decorative

Preparing §12 — whose headline criterion is *"zero successful cross-tenant
data-access attempts"* — surfaced two findings.

**1. Every policy was inert.** The app role `talkyai` has `usesuper=True` **and**
`rolbypassrls=True`. A superuser bypasses row security unconditionally, including
`FORCE`. Under a deliberately bogus `app.current_tenant_id`:

```
calls        1000 rows visible
campaigns      24 rows visible
```

**2. Worse — 34 tables carry `tenant_id` and have no RLS at all.** Not bypassed,
absent: `audit_logs`, `security_events`, `secret_access_log`, `invoices`,
`subscriptions`, `usage_records`, `recordings`, `transcripts`, `user_profiles`,
`tenant_secrets`, `connector_accounts`, `dnc_entries`… Fixing the role protects
none of them.

Tenant isolation in production rests **entirely on explicit `AND tenant_id = $n`
in application SQL**. That held for call feedback — all three cross-tenant service
calls refused with `CallNotFoundError` — but any query relying on RLS alone is
unprotected.

### Why the whole layer could be fixed at once

A superuser bypasses RLS *including* `FORCE`, so every policy change is provably a
**no-op until the role is switched**. Build it, prove it, then flip — and the flip
rolls back with one env var and a restart.

### Stage 1 (migration `0013`)

| Defect | Before | After |
|---|---|---|
| Policies / distinct shapes | 65 / 9 | **29 / 2** |
| Honouring `bypass_rls` | 11 of 29 | **29 of 29** |
| Raw `::uuid` cast (raises on empty GUC) | 14 | **0** |
| No `WITH CHECK` | 32 | **0** |
| With `FORCE` | 12 | **29** |

The `WITH CHECK` gap was unanticipated: 32 policies constrained reads but not
writes, so a tenant could have inserted rows into another tenant's scope while
reads looked isolated.

Canonical shape — **the `NULLIF` is load-bearing**:

```sql
COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)
OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid
```

Verified two ways before committing: the migration executed in a **rolled-back
transaction** (65/9/12/45/14/32 → 29/2/29/0/0/0, then restored), and the policy
expression exercised against a real `NOSUPERUSER NOBYPASSRLS` role — 11/11,
including the empty-GUC cast not raising, an uppercase UUID matching where text
comparison would silently fail, and `WITH CHECK` blocking a tenant-moving UPDATE.

`scripts/verify_rls.py` goes **12/18 → 18/20**. The two remaining failures are
stages 2 and 3.

---

## 7. Admin media controls and a pause that works

Migration `0014` plus 39 files of Admin panel work from another session, audited
and deployed.

**The pause was the substantive change.** It was a variable inside one process: a
second API worker never saw it, dialer workers never saw it, and a restart forgot
it. "Pause outbound calls" changed a label while the dialer kept dialling.

`platform_runtime_controls` is one row every process reads, and `call_guard.py:425`
checks it before placing a call. Pause once, every worker stops, and it survives
restarts.

**Route ordering was checked, not assumed:** `base_router` registers before
`calls_router`, so `GET /calls/pause-status` resolves ahead of `/calls/{call_id}`
rather than being parsed as a call id.

Verification: **19/19** — revision `0014`, singleton row `id=1 paused=False`,
`recordings_s3` and `call_feedback` both `rls=True force=True` with canonical
policies carrying `bypass_rls`, `WITH CHECK` and the `NULLIF` guard.

---

## 8. Super admin, sessions, role boundaries

Created `superadmin@talkleeai.com` — argon2id, `platform_admin`, no tenant. The
password was generated locally, written to a file **outside the git repo**, never
passed as a command argument, and every working copy shredded afterwards.

**Admin session — 14/14 over the public internet:**

```
login                    200, talky_sid/talky_at/talky_rt cookies + bearer
/auth/me                 correct principal, role platform_admin
forged token             401
no token                 401
/admin/users             12 users
/admin/tenants           10 tenants
/admin/recordings        total=422
/admin/calls/history     total=1000
```

**User session and role separation — 11/11**, tested by creating one:

```
admin creates user       201
user logs in             own token, role 'user'
blocked from /admin/*    403  (authenticated but not authorised)
deactivated              401 on re-login
```

Two facts worth carrying: `admin@talkleeai.com` is a **second full-access account
with no MFA and no sign-in since 2026-06-29**, and **the Admin panel is not
hosted** — no `vercel.json`, no `dist` on the server, nginx serves only
`api.talkleeai.com`. It runs solely as a local Vite dev server proxying to
production.

---

## 9. Conversation reviews — P0 #3

### The dependency that would have gutted it

§3 requires reviews to record the prompt version, and the Safe Improvement Loop is
built on *"aggregate reviews by prompt version and failure category."* But
`prompt_version` was only ever **logged**, never stored on the call. Every review
would have recorded `NULL` for the exact field the feature aggregates on — a
review panel that worked perfectly and told you nothing.

So `calls` gained `prompt_template` / `prompt_version` / `prompt_hash`, written at
session start and matched on **`talklee_call_id`** — the session's `call_id` is a
different UUID from the dialer's `calls.id`. Getting that wrong updates zero rows
and looks fine, so an `UPDATE 0` logs a warning.

### Design

Reviews **snapshot** that identity rather than joining it. A prompt gets
re-versioned tomorrow; this review must keep pointing at what the agent actually
ran on.

One review per **user** per call — deliberately unlike the voice note on the same
page, so two reviewers can rate the same conversation independently.

The properties §3 cares about are **constraints, not code**:

| Constraint | Guarantees |
|---|---|
| `UNIQUE (call_id, user_id)` | one review each; edits are an UPDATE |
| `UNIQUE (review_id)` on the ledger | an award happens once — editing never re-credits |
| `CHECK (tags <@ ARRAY[…])` | a typo cannot become a twelfth category and split the aggregation |

A future refactor cannot quietly undo any of them.

**Rewards ship disabled.** Review storage is P0; reward points are P1 in the same
document. The ledger, daily cap and "an empty review earns nothing" rule are built
and tested — enabling is an env var, not a deploy. Balance is `SUM(points)` over
the ledger, never a stored number that can drift from its own transactions.

### Verification — 64 checks

- **32 unit tests**, including one that reads the migration file and fails if the
  service's tag list drifts from the database `CHECK`.
- **20/20 constraint probe** against production, inside a rolled-back
  transaction: same user refused a second review, a different user allowed, edit
  reusing the same row, unknown tag rejected, ratings 0 and 6 rejected, second
  award for the same review refused, RLS + FORCE + canonical policy on both new
  tables, nothing persisted.
- **12/12 API round-trip** over `https://api.talkleeai.com`: submit → read →
  edit-in-place → one review not two, unknown tag 422, rating 9 422, foreign call
  **404 and never 403** (403 would confirm the call exists).

The 666 completed calls that predate this show `prompt_version = NULL`. That is
honest — the prompt they ran on was never recorded and cannot be reconstructed.

---

## 10. Things I got wrong today

**The smoke test that would have been a false pass.** All five admin endpoints
returned 401 unauthenticated, which looked like proof they existed. Then the
control path `/api/v1/admin/calls/definitely-not-real-xyz` returned **401 too** —
middleware gates `/api/v1/admin/*` before routing, so 401 proved the auth wall and
nothing about the routes. I'd have reported a pass. Settled by introspecting the
deployed route table instead.

**"RLS enabled, 1 policy" reported as isolation.** It wasn't. I checked the table
flags without checking the role, and a superuser bypasses everything. Corrected
the same session, but I stated it before verifying it.

**A dry run whose extractor was blind.** My migration-`0015` dry run reported
`rls=False policies=0`. That was my regex catching only triple-quoted SQL blocks
and missing the f-string RLS loop — not the migration. Reported as my blind spot
rather than a finding, and settled after applying.

**Two tests wrong, not the code.** Retry-attempts (short-circuits on `done`) and
the review API test attached to a tenant with no calls.

**The recurring shape.** Three separate times today a result looked correct and
wasn't: 401-as-proof, RLS-flags-as-isolation, dry-run-as-verification. Each was
caught by asking *what would this look like if it were broken?* and finding the
answer was **identical**.

---

## 11. Open items

| # | Item |
|---|---|
| 80 | RLS stages 2 (34 unprotected tables) and 3 (the role switch) |
| 79 | httpx 0.28 vs starlette 0.35 — 11 tests cannot execute |
| 63 | STT failover still ~21% of calls |
| 43 | Prompt prefill p50 ~629ms of ~641ms TTFT |
| 44 | Recordings lost when the caller barges over the disclosure |
| 54 | `CredentialResolver` cache keyed on `id(db_pool)` |
| 59 | 5 tenant configs on the dead `llama-3.1-8b-instant` |
| — | **P0 #2 unproven**: no call since 2026-08-20, so prompt persistence has never run |
| — | **P0 #3 gap**: no admin review listing/filtering (§3 acceptance criterion) |
| — | **Admin panel has no hosting** |
| — | `admin@talkleeai.com`: full access, no MFA, idle since June |
| — | Super admin password is plain text on the Desktop |

---

## 12. The schedule, plainly

19 days to September 10. **11 working days of build time** each for two
developers, after the freeze and validation days — roughly **22 developer-days**.

Estimated remaining: **34–47**.

Three untouched heavyweights — inbound campaigns, billing top-up, the 200-tenant
harness — are each multi-day and each at zero. Today advanced two P0s to roughly 80% and consumed a
full day, three-quarters of it on work that is not on the checklist. It closed
none outright.

The cuts flagged this morning now need **deciding**, not considering:

1. **Drop Salesforce.** P1, untouched, ~5 days.
2. **Cut §12 to its security core** — 200 seeded tenants, the isolation matrix,
   billing reconciliation. Defer the soak, the performance battery and the
   failure-recovery drills.
3. **Reviews without rewards** — already the shipped state.

And two things that are not in the plan but will bite: **RLS stage 3 is a
prerequisite for §12 meaning anything**, and the **30 controlled calls are human
hours**, five speakers and two accents, which have to be booked rather than
absorbed.
