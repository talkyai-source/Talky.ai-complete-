# AG04 accepted receipts and phone correction: offline evidence

Source candidate: `4878fe835c291681540c4cb6a5af7cf60cce5f6c`, based on `43180118cd2f7affc201be0b1d0dac94b7729f18`. This is a test-only extension of the existing common replay. Application code, prompts, evaluator, proposed human matrix and thresholds are unchanged. The source commit contains only the two qualification runners, their two JSON corpora and their two unit modules.

## Observed boundaries

- **Accepted email, delivery unconfirmed:** the synthetic connected executor returns one accepted receipt with the saved provider message ID `synthetic-remote-accepted-1`. The real traditional speech guard replaces an unsupported inbox-delivery claim with an accepted-only statement; the native guard withholds the unsupported candidate and allows the subsequent honest statement. All six adapter profiles retain the receipt and make exactly one synthetic effect attempt. Neither a resend nor recipient delivery is inferred.
- **Corrected phone digits:** both native parsers process the original number, its completed matching readback and bare `Yes`, then a last-four-digit correction. The new number is pending and carries the new source item/order/digest. A late old confirmation and a new bare `Yes` without a new owned readback cannot restore the old number or confirm the correction. A matching completed `transport_played` receipt followed by a new confirmation confirms only the corrected number. Stale, unknown, transmitted-only and actually interrupted corrected readbacks remain pending.
- **Intermediate ownership is checked by the common runner:** detached contact checkpoints expose the original confirmation, correction, ignored old final and unowned bare confirmation. A negative unit control removes a required checkpoint while retaining the correct final contact; the common control fails. Value and confirmation provenance remain separate.

The accepted receipt and media receipts are explicitly synthetic. The connected executor is intercepted at its existing port; the parser, bridge/TurnRunner, action result recording and speech/contact guards are actual application methods. This does not exercise a real provider acceptance, durable action claim, database contact write, recipient delivery, or caller hearing.

## Initial investigation

`initial-inspection-probe.py` and `initial-phone-receipt-probe.py` preserve the bounded baseline probes and their JSON observations. They were executed from `backend` at the base revision with socket/DNS blocking. The probes found the application boundaries already handled these authored cases; no application repair was required. They exposed an inaccurate harness accepted-effect count: `provider_accepted` was omitted from accepted statuses, producing zero despite the recorded successful accepted receipt. The runner now derives the count from the explicit accepted status and preserves the result/message ID.

The initial phone diagnostic cleared its fake gateway submission list to arm the old first-send blocking fixture. That diagnostic is not the final receipt-history proof. The committed runner uses a one-shot block for the later corrected readback and retains the completed original readback and its receipt. `new-controls-observed.json` contains the final observations.

## Commands and results

Windows Python 3.12.12 was the existing repository interpreter at `C:/Users/AL AZIZ TECH/Desktop/Talky.ai-complete-/backend/.venv/Scripts/python.exe`. Working directory was the isolated worktree's `backend`. Test environment used `ENVIRONMENT=test` and the intentionally unavailable synthetic `DATABASE_URL=postgresql://test:test@127.0.0.1:1/unavailable_test`; no PostgreSQL server or provider was contacted.

```text
python -m pytest tests/unit/test_ag04_native_qualification.py tests/unit/test_ag04_traditional_qualification.py tests/unit/test_ag04_qualification_gate.py -q -o addopts=
114 passed, 0 skipped; 1300 existing datetime.utcnow deprecation warnings

python -m pytest tests/unit/test_ag05_native_contact_revision.py tests/unit/test_customer_claim_admission.py -q -o addopts=
44 passed, 0 skipped; 38 existing datetime.utcnow deprecation warnings

python -m ruff check tests/qualification/ag04_native.py tests/qualification/ag04_traditional.py tests/unit/test_ag04_native_qualification.py tests/unit/test_ag04_traditional_qualification.py --select F
All checks passed

python -m scripts.evaluate_ag04_conversations --output ../tmp/ag04-common-report.json
Exit 1, as required for the preserved captured semantic failure
```

The first focused run was 113 passes before adding the missing-checkpoint negative control; `focused-initial.txt` is retained, not an additional independent count. Final logs are `focused-final.txt`, `reused-runner-regression.txt`, `ruff.txt` and `common-cli.txt`. `git diff --check` was clean before source commit.

The common CLI ran against the committed source with no uncommitted source changes: **206/206 declared rows, 1580 control checks, zero control failures, one preserved captured Groq semantic failure, 205 unreviewed semantic findings**. Provider calls, telephone calls and blocked network attempts were all zero. `common-summary.json` preserves the report summary and exact source revision. `new-controls-observed.json` preserves all 16 new rows: four traditional accepted-email profiles, two native accepted-email profiles and five phone conditions through each of two native parsers. The full 14.6 MB report remains in ignored `tmp/ag04-common-report.json` and can be regenerated; the redundant source-file hash map is not committed.

## Qualification limits and independent review

There are 32 traditional scenarios repeated across four adapters and 39 native scenarios repeated across two parsers. These are 206 replay rows and 71 distinct engine/control identifiers, not 206 or 71 human scenarios. The unchanged proposed matrix has 50 canonical scenarios; 41 now have some mapped replay coverage, with nine still unmapped. Native phone correction does not provide traditional phone-correction coverage.

The historical Groq failure remains unchanged and still fails the gate. Authored output is unreviewed semantic evidence. No live profile, human rubric, telephone acoustics/latency, external effect or production approval was obtained. `production_approved` remains false. These results do not complete AG04 or lift the feature freeze.

The independent LLM agent read-reviewed the six-file delta and the final checkpoint/message-ID change and found no material defect. It confirmed the actual guard boundaries, one-attempt receipt accounting, preserved old readback history and missing-checkpoint failure. It did not run an independent suite, database or provider check.
