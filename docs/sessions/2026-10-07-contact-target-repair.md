# Earlier contact correction and withdrawal

Base: `1750341b9425a994da2f43e27b2ecfb2fa7e5f04`. Source commit is recorded in the [manifest](artifacts/contact-target-repair/manifest.json).

After `record_contact(add)` moved confirmed contact A into the earlier-contact tuple and made B current, all operations and save acknowledgements still selected B. Earlier snapshots and prompt facts also assumed A stayed confirmed forever, including after an accepted source revision. This was an offline reproduction, not a diagnosed live customer incident.

The existing tool now accepts optional `field_key` (`email`, `email_2`, `phone`, etc.). Omitted/null preserves current-contact behavior. Set, confirm and withdraw target exactly that existing entry; `expected_value` still compares the selected value. Add remains current-only. Withdrawn entries retain their positions, so later keys and current action recipients do not shift. Current caller quote, canonical source, turn identity, syntax and later-confirmation requirements remain enforced. The model still interprets intent; quote attribution does not prove that interpretation correct.

Current and earlier entries now share truthful lead-row projection, revision demotion and revocation handling. A correction is pending; a withdrawal is a null cancelled row. Save success requires the selected row's exact acknowledged fingerprint, never another contact's acknowledgement. Prompt facts and tool results expose stable keys and actual status. No schema, service, parser, action-recipient policy or native older-event admission change was added.

Verification (overlapping, not additive):

- Initial reproduction: **8 failed / 1 passed**, covering both contact kinds, both native providers and archived value/confirmation revisions. Preserved in [baseline.txt](artifacts/contact-target-repair/baseline.txt).
- First repair check: **90 passed** across 3 modules.
- Final focused module: **25 passed**.
- Final affected selection: **237 passed / 0 failed / 0 skipped**, 12 modules, 179 warnings, 11.71 seconds. [Final output](artifacts/contact-target-repair/final.txt). Includes the 25 new controls. Offline guard recorded 330 internal socketpairs and 0 prohibited calls. All five changed source/test raw hashes match before/after.
- Repo Ruff F selection with F401/F841 ignored and `git diff --check` passed. Independent source/test review by `/root/account_review` was clear; reviewer did not execute tests.

Controls exercise the actual shared tool, traditional TurnRunner, OpenAI/xAI native event paths and LeadCaptureService validation/SQL construction using synthetic ports. They cover exact selected SQL payloads and revocation-before-pending writes, unchanged B/action recipient, partial values, later confirmation, invalid selectors, duplicate values, concurrency, failed/conflicting saves, retry deduplication and source revisions during writes. This does not prove SQL engine/RLS behavior, deployed UI, live providers, audio comprehension or model-language quality.

[Exact commands and environment](artifacts/contact-target-repair/commands.txt) preserve the baseline fixture limitation and missing initial test-byte snapshot explicitly. Existing shared venv was used; no fresh dependency install was qualified. Production-readiness gates and the wider acceptance freeze are unchanged.
