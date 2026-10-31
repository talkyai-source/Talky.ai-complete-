# Explicit customer-denial admission follow-up

This separate repair follows mechanical AG07 commit
`eaebd8fe13b027427296193020ba1c98c0341f2a`. It was authorized after a new actual
`TurnRunner → TurnStreamer` regression exposed the exact baseline failure:

- Caller: “I am not your customer.”
- Prepared typed state: `customer_relationship=denied`.
- Synthetic candidate output: “As our existing customer, your account is ready.”
- Before repair: that text reached the synthetic TTS submission port unchanged.

The issue was output grammar, not missing state or a provider setting. The existing
matcher recognized “You are a customer” but lacked the sentence-leading “As our
customer” form. It also matched existing-customer wording inside a quoted correction.
The new guard adds only this direct sentence-leading form and reuses the existing
assertion filter for quoted, reported, negated and hypothetical context. It does
not infer another relationship, call another model, change prompts, or add another
classification layer. Ordinary product text and unknown/affirmed relationship state
retain their previous admission behavior.

The original strict expected failure from the mechanical commit is now an ordinary
passing test. Four direct customer/client/merchant examples and the existing quoted
statement produced **5 failed, 10 passed** before repair. That artifact is preserved
as `docs/sessions/artifacts/ag07/relationship-grammar-before.txt`. The first combined
follow-up batch gave **119 passed**, recorded in `relationship-grammar-after.txt`.

The final tests additionally drive the same exact phrase through both existing
OpenAI and xAI native parsers and the actual bridge admission gate, using the AG04
offline harness. Both retain raw candidate output and submit no audio. These are
synthetic parser/bridge checks, not provider calls or human-hearing evidence.

Final command from `backend`, using the original backend venv:

```text
python -m pytest -q tests/unit/test_customer_claim_admission.py tests/unit/test_voice_pipeline_llm_response.py tests/unit/test_voice_pipeline_runtime.py tests/unit/test_live_structured_state.py tests/unit/test_kb_injection_budget.py tests/unit/test_two_model_pipeline.py tests/unit/test_voice_pipeline_service.py tests/integration/test_voice_pipeline_conversation.py tests/unit/test_kb_evidence_contract.py tests/unit/test_ag03_cascaded_relationship.py tests/unit/test_ag03_caller_dispatch_order.py tests/unit/test_ag03_relationship_acceptance.py tests/unit/test_ag03_relationship_state.py tests/unit/test_live_capability_offers.py tests/unit/test_ag04_traditional_qualification.py tests/unit/test_ag04_native_qualification.py --disable-warnings
```

Result: **302 passed, no skips or expected failures, 1,470 warnings**, 11.25 seconds.
Exact CI Ruff command `python -m ruff check app/ --select F --extend-ignore F401,F841`
passed. Logs: `docs/sessions/artifacts/ag07/followup-final.txt` and
`followup-ruff.txt`. Captured pytest text has trailing whitespace normalized only.

No provider calls, DB operations, pushes or deployment occurred. This fixes the
observed grammar and tested controls; it is not a claim to recognize every possible
paraphrase or to close the live/human semantic qualification gaps recorded by AG04.
