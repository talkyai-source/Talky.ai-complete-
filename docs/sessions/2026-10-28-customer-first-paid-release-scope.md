# Customer dashboard scope release — 28 October 2026

The customer-only release is live at `https://talkleeai.com`.
[PR 27](https://github.com/talkyai-source/Talky.ai-complete-/pull/27) merged at
11:28:32 UTC as `80b52743d8073d0b3ed07fb260c82add50088d6e`. Automatic Vercel
production deployment and nine anonymous HTTP checks were verified at
11:32:36 UTC. This section supersedes the earlier preparation-only status.

The user explicitly excluded reseller operations and staff invitations from
the first paid release. They remain future work. This change makes that scope
visible in the reseller workspace and partner routes while retaining the
existing server authentication, role gates and cosmetic branding preview.
Tenant administration, nested agent settings, reseller billing, overview and
analytics routes show an unavailable notice instead of browser-only mutations,
invented paid invoices or operational totals. This does not implement those
excluded capabilities or close the wider production-readiness plan.

The isolated release branch started at `origin/main`
`b8f7488fefa75928acca158bf3d27dcfbdaa9830`. Its 12 changed `Talk-Leee` paths are
copied exactly from reviewed source
`9c06d8c6510b288b190a20a6a17354259c8bb6b6`: the containment implementation and
tests, plus the existing lockfile update from `qs` 6.16.0 to 6.16.1.
Backend, database, installer and updater files remained at the main-branch
base. This release required no new API contracts and performed no backend
activation.

All ten reported PR checks passed in
[CI run 40677108477](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40677108477).
The customer frontend passed lint, type checking, the Next 16.4.1 production
build and 1,331 tests, with two existing DB-dependent session tests skipped.
The production dependency audit (`npm audit --omit=dev --audit-level=high`)
reported zero vulnerabilities. Admin passed 943 tests; the backend coverage
suite passed 15,628 with 174 skips and 72.54% coverage. The serial Admin
PostgreSQL gate passed 701 tests. Counts overlap and are not summed. Container
build/scan, SQL, ingress and secret checks also passed.

Candidate `49d26b79ea73030f70e773dc5cc9c2fc7ae55f11`, tested merge
`e77516c61cacb15a497b01ee8ef81d683c26030a`, and the actual main merge all have
tree `f022788aaf2647073ea7ebf98a9e2b99024f831d`.
[CI receipt](artifacts/customer-scope-release-20261028/ci.json) and
[source binding](artifacts/customer-scope-release-20261028/source-binding.json)
retain the check links and candidate identity.

Vercel project `talkleeai` uses repository directory `Talk-Leee` and Git
production branch `main`. The public alias now resolves to READY production
deployment `dpl_3CuQmP8NmyUwEGryhmbJ9kCpsdYR`; both `gitSource.sha` and
`meta.githubCommitSha` match the exact main merge above. The production public
API setting remains `https://api.talkleeai.com/api/v1`, verified through a
read-only configuration query. The previous production deployment was
`dpl_AvnseM28ovfDuoQuT7BogK4PfpW4`, source `b8f7488f`.

The live homepage, login and branding-preview routes returned HTTP 200.
Workspace, tenant list, nested agent settings, reseller billing, overview and
analytics routes each returned HTTP 307 to `/auth/login` without an app
session. The tenant list and nested editor had returned HTTP 200 in the
before-release observation; the new server boundary now requires login.
No response bodies or owner credentials were retained.

[Production receipt](artifacts/customer-scope-release-20261028/production.json)
SHA-256: `3f4b95311e28d6ff262227d0e90095953025a469be03ee41a87eec3f72ba58de`.
[Public-configuration receipt](artifacts/customer-scope-release-20261028/production-public-config.json)
SHA-256: `de28564d5f73f82b7bd39a3bd4746992ea6581a5d6e30f2647caadfbf70b5ca3`.

These live checks establish source identity and anonymous route gates. They do
not establish signed-in owner workflows, paid billing, real calls, backend
activation or completion of the wider production-readiness plan. Reseller
operations and staff invitations remain explicitly excluded future work.
