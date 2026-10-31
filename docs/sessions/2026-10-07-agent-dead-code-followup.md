# Dead-code cleanup verification follow-up

Source repair `95f57653` restores the original UTF-8 em dash in the live contact-context marker. Independent review caught that accidental encoding change; the original initial log and manifest remain intact.

Tests-only `a99dcba9` completes the remaining retained-contract migration against the exact application tree from integrated `4cb20458`. All seven test/fixture working hashes match the committed Git bytes after LF normalization; the application Git tree is identical to that integrated source. The local dependency merge is not another root deliverable.

The first dependency-qualified selection preserved **439 passed / 39 failed / 215 warnings**. The remaining failures concerned retired callback scripts, inferred state, filler and keyword-selected action tools, the old native query contract, and absent historical relationship state. The final six-module selection passed **92 tests, zero failed/skipped, 57 warnings** in 3.26 seconds. Its guard recorded **0 prohibited attempts** and 125 internal loop pairs. Counts overlap earlier selections and are not additive. Root owns the final combined regression.

Useful native source, fence and delivered-history controls remain. A local deep copy of the historical case supplies the real exact-section catalog/arguments and available status; the checked-in corpus is unchanged. Missing source, source outside the data fence and removed delivered history still fail controls. An unavailable historical observation now explicitly fails instead of raising KeyError. The old absent-link speech-repair assertion is retired: these fixtures do not qualify model wording.

Capability tests preserve actual transfer admission gates and allow the separate end_call tool. Callback tests expose availability and requested-unscheduled facts without a script. Action tests preserve actual result feeding, argument dispatch and uniqueness while matching model-selected tools. No application behavior, permissions, source admission, persistence or effect execution changed in this final test migration.

[Exact commands, dependency provenance and results](artifacts/agent-dead-code-cleanup/followup-result.json); [final log](artifacts/agent-dead-code-cleanup/migration-first.txt); [intermediate failure log](artifacts/agent-dead-code-cleanup/affected-first.txt). Existing venv and synthetic ports only; no provider, database, browser, human listening or live-model acceptance ran.
