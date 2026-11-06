# Prepared CI quality gates (F16-02)

The repository changes prepare checks; GitHub branch protection is **not applied**
by editing these files. No workflow run, GitHub setting mutation, provider call,
preview deployment or synthetic phone call was performed as part of preparation.

The existing canonical backend, frontend, Admin, schema/PG16.14 parity, secret
and image checks remain. Overall backend coverage remains 20%. A separate PR job,
`Changed-line coverage`, requires 80% of new/changed executable Python lines in
`backend/app`, measured from the existing backend coverage XML against the PR
base. Missing changed files or executable lines within a present file, unreadable
XML or unavailable Git history fail. The guard independently parses/compiles
changed source metadata without importing or executing application code;
comment-only/removed-only changes have zero executable lines. This does not
measure frontend coverage or prove that a regression test was run before a fix.

`Quarantine policy` refuses new exact identities compared with the PR base
(including equal-size replacements), and any entry older than 14 days. It uses
the earliest appearance/date in full Git history; rewriting a header, moving
lines or removing/re-adding an entry does not refresh it. Shallow/unknown history
fails closed. It only reads `backend/tests/unit/release_quarantine_20261008.txt`.
The list and `tests/unit/conftest.py` belong to Claude and remain unchanged.
Historical stale entries intentionally fail the new check until Claude fixes
or updates the affected behavior/tests and removes the corresponding quarantine
entries. Do not add exceptions, reset dates, or treat this policy failure as a
reason to bypass required checks. Fixture-only unit tests do not assert that the
current protected list is healthy.

`Nightly security scans` runs daily at 02:17 UTC and supports manual dispatch.
Separate jobs report gitleaks, pip-audit, npm audit for both frontend projects
(including development tools), and Trivy filesystem vulnerability/secret/config
results. Any scanner failure fails that job; existing vulnerabilities may make
the first run red. The workflow does not fix dependencies or suppress findings.
CI-only audit tools are installed on the runner; runtime dependencies are
unchanged. Repository owners must enable/watch Actions failure notifications and
confirm delivery on a controlled failure. Organization licensing requirements
for the existing gitleaks action, if applicable, remain owner configuration.

`Optional PR browser checks` runs the existing smoke and dashboard visual specs
in Chromium, on the local 3100 development server with loopback API URL, no live
credentials, and production-audit mode disabled. It always uploads available
failure screenshots/HTML. It is intentionally nonrequired at first; visible
step/test failures are not browser acceptance. Existing fixture/UI or platform
snapshot differences may fail and need triage. No snapshots are regenerated.
This is local CI coverage, not testing an authenticated preview deployment or
all 20 journeys/30 screens from the original QA target.

Schemathesis is not enabled: no reviewed isolated authenticated app fixture or
owner-approved staging target was supplied for mutation fuzzing. Scheduled
billable outbound/inbound synthetic calls are also not configured here; owned
numbers, carrier approval, isolated policy and notification verification remain
separate owner work. Do not point an unreviewed fuzz/call workflow at production.

## Owner apply and verify

Run these only from the reviewed repository checkout after the workflows have
run on a PR and Claude's stale quarantine entries have been resolved. Use a GitHub
identity authorized to administer this repository. The JSON deliberately has no
bypass actors and requires a PR, at least one review, last-push approval, resolved
threads, strict status checks, and no deletion/force-push of `main`.

```bash
# Resolve the repository from this checkout; do not paste an unrelated owner.
repo="$(gh repo view --json nameWithOwner --jq '.nameWithOwner')"
test -n "$repo"
gh api "repos/$repo/rulesets"
gh api "repos/$repo/branches/main/protection"
cat .github/main-ruleset.json

# Verify actual check-run names on the reviewed PR before requiring them.
# Replace PR_NUMBER with that PR's numeric ID.
gh pr checks PR_NUMBER --repo "$repo"

# Owner action: create only the reviewed ruleset, retaining its returned ID.
mkdir -p tmp/qa-ruleset-owner
gh api --method POST "repos/$repo/rulesets" --input .github/main-ruleset.json --jq '.id' > tmp/qa-ruleset-owner/created-id
ruleset_id="$(cat tmp/qa-ruleset-owner/created-id)"
gh api "repos/$repo/rulesets/$ruleset_id" > tmp/qa-ruleset-owner/created.json
cat tmp/qa-ruleset-owner/created.json
gh api "repos/$repo/rules/branches/main"
```

A 404 from the protection read can mean no legacy branch-protection rule; inspect
rulesets too. If a relevant ruleset already exists, review its overlap and update
that specific rule deliberately instead of blindly creating duplicates. Required
check names in the JSON match this repository's current jobs; optional browser
and nightly jobs are excluded. Confirm the actual GitHub context names before
application. Confirm a disposable PR with a deliberately failing required check
cannot merge and an unapproved PR cannot bypass review. Do not test force-push or
branch deletion on production `main`.

Manual read-only policy commands from repository root:

```bash
python backend/scripts/check_release_quarantine.py --base-ref origin/main
python backend/scripts/check_diff_coverage.py --base-ref origin/main --coverage backend/coverage.xml --threshold 80
```

The quarantine command needs full history and may correctly return1 for the
current stale list. Diff coverage needs the actual XML from the same checkout;
an old report is not valid release evidence. Owner notification checks and the
first scheduled/browser workflow receipts remain to be recorded after merge.

## Owner rollback

Do not disable gates merely to admit known failures. If the newly created ruleset
has an incorrect target/context and an authorized rollback is needed, remove
only the ID returned by this creation; preserve pre-existing rulesets:

```bash
repo="$(gh repo view --json nameWithOwner --jq '.nameWithOwner')"
ruleset_id="$(cat tmp/qa-ruleset-owner/created-id)"
gh api "repos/$repo/rulesets/$ruleset_id"
# After confirming name/target/ID against the saved creation receipt:
gh api --method DELETE "repos/$repo/rulesets/$ruleset_id"
gh api "repos/$repo/rulesets"
```

Workflow/code rollback is a reviewed Git revert, not a live automatic deployment.
No remote setting rollback was executed during this work.

## Reference contracts

- [GitHub ruleset REST API](https://docs.github.com/en/rest/repos/rules): create,
  inspect and delete a repository ruleset with explicit branch conditions.
- [GitHub repository rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository):
  administrative authority and required-check selection.
- [Trivy official action](https://github.com/aquasecurity/trivy-action): `fs`
  scan mode and action inputs.
- [npm audit](https://docs.npmjs.com/cli/v11/commands/npm-audit/): audit exit
  behavior and dependency scope. No `audit fix` is used.
