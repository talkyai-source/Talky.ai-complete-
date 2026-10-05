# AG04 native failed-action and contact parity

Source candidate: `9516c2fe352724d48f9c761991a812065115669c` (test-only), following the separately committed guard repair and evidence.

This test-only extension adds five authored conditions through each actual OpenAI/xAI event parser: a definitive failed email action, literal and reported third-party email, and invalid and incomplete phone digits. It changes only the native qualification runner, native JSON corpus and native qualification unit module. The synthetic executor/media ports remain explicit; no provider, telephone, database or durable connector operation occurs.

## Initial observations and repair dependency

`initial-probe.py` and `initial-observed.json` preserve the original actual-method observations at base `be44bf7267ee2aa96ca851c8f0acdbc9e5733502`, before application or corpus changes. For reproduction, place the script under the worktree's ignored `backend/tmp/parity_probe.py` and run `python -m tmp.parity_probe` from `backend` using the test environment below. The probe blocks socket/DNS operations; blocked attempts were zero. A first direct-file launch without the module path failed to import `scripts`; the corrected module invocation produced these observations and exited zero.

All four contact sequences already behaved correctly. Both native parsers rejected the exact honest failure sentence `The email could not be sent.` after correctly blocking an unsupported success claim. That defect was repaired separately by source `5a6fa48ec706a5bf2df017a3b4e88517b3f9cf0e`; its preserved red controls, rejected draft and final 224-test verification are recorded in [the guard evidence](../ag04-passive-email-failure/verification.md). This extension retains that exact sentence and depends on the guard repair; it does not substitute easier wording.

## What the common controls establish

- **Definitive failed action:** one synthetic connected executor invocation produces the original failed receipt, provider label and request ID, with no message ID and no confirmation permission. The receipt reaches the actual tool response and session result store unchanged. The unsupported `sent` claim never reaches submitted speech/history; one repair submits only the honest failure. The common report checks the complete saved receipt, not just an accepted-effect count.
- **Third-party email:** literal colleague data and quoted first-person data attributed to another speaker produce no caller email, value/confirmation/status provenance or owned readback. A subsequent bare `Yes` cannot promote them. Later explicit caller-owned email remains pending until its own matching synthetic completed readback and a fresh confirmation, with distinct source and confirmation item/order/digest.
- **Unclear phone:** `+123` and the existing incomplete last-digits wording remain null, unconfirmed and `needs_clarification`, retaining their actual caller source. Bare `Yes` supplies neither digits nor confirmation. A later complete international caller number is accepted pending its own readback, then confirmed using new confirmation provenance. No missing digits or country code are guessed.

The common checkpoint assertions cover these intermediate states as well as the final positive controls. No connected effect, call end or DNC is introduced by the contact cases. The other contact field stays empty. The completed readback receipts are synthetic transport evidence, not proof of caller hearing. The failed-action case proves one invocation in this authored sequence, not durable prevention of a repeated request or an ambiguous real-provider retry.

## Commands

Working directory: `tmp/ag04-native-parity-20261005/backend`. Interpreter: original repository `backend/.venv/Scripts/python.exe`, Python 3.12.12. Environment: `ENVIRONMENT=test`, `DATABASE_URL=postgresql://test:test@127.0.0.1:1/unavailable_test` (synthetic unreachable port).

```text
python -m pytest tests/unit/test_ag04_native_qualification.py tests/unit/test_ag04_traditional_qualification.py tests/unit/test_ag04_qualification_gate.py tests/unit/test_ag05_native_contact_revision.py tests/unit/test_customer_claim_admission.py -q -o addopts=
168 passed, 0 skipped; 1426 existing datetime.utcnow deprecation warnings

python -m ruff check backend/tests/qualification/ag04_native.py backend/tests/unit/test_ag04_native_qualification.py --select F
All checks passed (run from worktree root)
```

Logs: `final.txt`, `ruff.txt`. The canonical human matrix, thresholds, old Groq sample, prompts and application defaults remain unchanged. These are authored runtime controls; model semantic judgment, actual audio, human review, external delivery and production approval remain unproven.

## Exact committed-source common replay

```text
python -m scripts.evaluate_ag04_conversations --output ../tmp/ag04-common-parity.json
Exit 1: preserved captured semantic failure; production_approved=false
```

At the source candidate above, with no uncommitted source changes: **216/216 declared rows, 1942 control checks, zero control failures, one captured Groq semantic failure, 215 unreviewed semantic findings**. Provider calls, telephone calls and blocked network attempts were zero. No evidence-schema errors or runner errors occurred. `common-cli.txt` and `common-summary.json` retain the result; `new-controls-observed.json` retains all ten new observed rows. The 15.3 MB full report remains ignored under `tmp/ag04-common-parity.json`, reproducible with the command above; redundant source-file hash maps are omitted from the committed summary.

The inventory is 44 native conditions through two parsers plus 32 traditional conditions through four adapters. The 216 rows and 76 engine/control IDs are not human scenario counts. The global mapping remains **41/50 canonical scenarios**, with the same nine missing everywhere: unknown action outcome, background speech, echo reentry, non-price grounded answer, hold music, sensitive-data offer, silence without a request, unclear name and unrelated request. The change fills native parity for three IDs already mapped by traditional controls; it does not claim newly completed global scenarios or semantic approval.

The independent LLM agent reviewed the complete test-only delta and found no material defect. Review confirmed that common findings check the saved failed receipt and intermediate contact authority as well as final state. The reviewer ran no independent suite, provider or database check. AG04 remains open, with its historical failure and live/human/audio gates intact.
