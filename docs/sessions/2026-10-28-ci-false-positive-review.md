# CI finding review — 28 October 2026

Candidate `3dcd2f0fed131fcdc96bcac94ed31691dc2e5aff` was tested as merge
`45eb607f37972e790e97be525daac4f031befc0a`. The main CI run
[40671709297](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40671709297)
stopped at gitleaks before the Backend and billing PostgreSQL tests could run.
This checkpoint does not claim those skipped checks passed.

## Exact secret-scan exceptions

The 40 findings were reviewed without printing their matched values. The
exceptions added to `.gitleaksignore` pin the original commit, artifact path,
rule and line; no directory, file class or credential rule is disabled.

- Five findings in `tls-recovery-0075.json` are source-file SHA-256 fields.
  Each was recomputed from its recorded source commit `226ff9d6` and matched.
- Five findings in `tls-recovery-0082.json` are the same kind of evidence.
  Each was recomputed from recorded source merge `5a5d3aae` and matched.
- Twenty-nine findings in `original-ccd45f10-result.json` are LF-normalized
  source-file SHA-256 fields. Each was recomputed from its recorded `ccd45f10`
  source and matched. They include the source digest whose filename triggered
  the Sentry token rule. Request authorization headers are not serialized in
  that artifact.
- The `test_install.py:459` finding at commit `585682c1` is the literal
  `--property=Environment` argument in
  `test_systemctl_diagnostic_rejects_unreviewed_actions_units_and_options`.
  The test checks that an unreviewed systemctl diagnostic request is refused;
  the argument is not a credential.

All original evidence files remain byte-for-byte unchanged. The two TLS
artifact SHA-256 values are respectively
`ded7edd828df9f6d49fb5ffcae02f730eb85729d6a864038591e76d70581325a` and
`24c8383ef23c42a574e506c0fac091620914456d1e7ef81dee0eb2e773a0ac15`.
The retained failed-job log SHA-256 is
`08d1f6c68c13d161662e7c0b6dea6ab75cab6ffdeae99cb4d286c6f1e2102572`.

Local gitleaks **8.24.3**, matching the failed CI job, then replayed that job's
exact `--no-merges --first-parent 5fff9a79^..3dcd2f0f` range with redacted
output: **27 commits, no leaks found, exit 0**. This result covers those
historical findings. New artifacts and new commits still require scanning;
it does not assert that an unrelated or future credential is safe.

## Other checks on that source

[Canonical database TLS recovery run 40671709491](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40671709491)
passed full-schema recovery and all 16 runtime-grants/first-admin checks.
Both receipts identify merge `45eb607f`, report no production access and
confirm removal of their owned resources. The runtime-grants receipt SHA-256 is
`3ed59d6e6e7ee5648f9b3cd99ff9fcc85c4d068652da147fec7ab28f6fed58ad`;
the recovery receipt is
`0605d94df65c4998dfb5d04ccdc1413c9cf1659fe92abd9297a38a356878290c`.

In [Ubuntu installer run 40671709332](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40671709332),
the installer, gateway and exact Linux runtime-lock audit passed. The audit
contains 156 packages and zero reported vulnerabilities; its receipt SHA-256 is
`c4f1426952b7b3da7b46b9a682172b615f09ba52556e559a226397db27442ddd`.
The customer frontend compiled, then failed TypeScript because the containment
test's throwing `UnsafeEditor` function was inferred as returning `void`.
An explicit `never` return annotation fixes that test declaration without
changing product behavior. The 11 focused containment tests passed locally
after the fix, using the existing local Next 16.3.3 dependencies. The committed
Next 16.4.1 production build still requires a new Linux result after the fix.

These results do not constitute deployed operating acceptance or complete the
production-readiness plan.
