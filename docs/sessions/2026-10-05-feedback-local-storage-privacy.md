# Local feedback creation and missing storage roots

Source `4bb26dab6c7f0b6b9218d5e2db05ce195f7cd178`, based on `0b2a7e13`.
This is a bounded OP11/OP07 follow-up to the
[permission inventory](2026-10-05-op11-permission-boundaries.md).

The enabled local feedback writer used ordinary directory creation and exclusive
`open(..., "xb")`. Its file and directory permissions depended on the process
umask. Unlike the recording writer, it did not request owner-only creation modes.
The reproduced boundary requested 0777 for new directories and 0666 for a new
file. These are creation requests, not a measurement of deployed permissions.

The writer now requests 0700 for every newly created directory and 0600 for a
new file. Exclusive creation and binary byte preservation remain: it cannot
overwrite a note already at the chosen filename. Existing directory/file modes
are not changed, and the process-wide umask is not modified. Production's
explicit local-storage opt-in, S3 preference, database flow and read/delete
authority are unchanged.

Independent review caught a missing-root loop in the first draft: when a drive
or share root is unavailable, ascending parents eventually stops making progress.
The same defect existed in the recording writer. Both now raise an `OSError`
instead of revisiting that root indefinitely. Synthetic unavailable-root probes
reproduced the loop in each actual writer with a bounded assertion; no drive was
disconnected and no host path was modified.

## Verification

Artifacts are under [feedback-local-privacy](artifacts/feedback-local-privacy/).
The [manifest](artifacts/feedback-local-privacy/verification.json) binds the final
source hashes to the source commit.

- [Initial controls](artifacts/feedback-local-privacy/initial.txt): two failed
  permission-request checks, eight passed, four explicit POSIX skips, before
  application changes.
- [First draft](artifacts/feedback-local-privacy/focused.txt): 44 passed and eight
  POSIX skips. This did not include the missing-root review controls.
- [Feedback root review](artifacts/feedback-local-privacy/root-review-red.txt):
  one failed, ten passed, four POSIX skips. The test stopped after 64 probes,
  demonstrating the repeated-root behavior without hanging the test process.
- [Recording root review](artifacts/feedback-local-privacy/recording-root-red.txt):
  one failed, eight passed, four POSIX skips on the existing recording writer.
- [Final related checks](artifacts/feedback-local-privacy/final.txt): **129 passed,
  zero failed, nine skipped**, with 66 warnings in 15.23 seconds across ten
  modules. Eight skips require POSIX mode semantics; one requires FFmpeg.
  Earlier counts overlap and are not additive. The final source stayed unchanged.
- CI-rule Ruff F checks (existing F401/F841 exclusions) and `git diff --check`
  passed for the four source/test files.

Commands used Python 3.12 from the original repository's virtual environment,
with the existing exact direct-requirements overlay and synthetic credentials.
Each test command/environment scope is recorded beside its output. Tests used
temporary synthetic audio, real local file I/O and injected failures. They check
owner read/delete, exclusive collisions, concurrent directory creation, denied
creation, production opt-in, unchanged existing modes/umask behavior and missing
root termination. POSIX permission checks remain explicitly platform-gated.

`/root/llm_audit` found the missing-root issue during source review.
`/root/audio_audit` independently reread the final two writers and their controls
and found no material defect. Neither reviewer claimed an additional test run or
deployed permission verification.

## Remaining boundaries

Trusted, persistent storage parents and deliberate cross-process access remain
operator prerequisites. This is not protection against hostile directory swaps,
a Windows ACL audit, reconciliation of existing permissions, a new retention
policy or proof that local disk survives deployment. No host setting, account,
unit, existing retained audio or supplier resource was changed. No provider,
PostgreSQL, push or deployment was performed. OP11, OP07 and release gates remain
open; the three deferred packages remain deferred.
