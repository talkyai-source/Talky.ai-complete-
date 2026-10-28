# Current-source Groq section qualification

The first bounded real-provider run completed six synthetic text/tool cases but
**failed Q43's no-invented-follow-up requirement**. It does not establish paid-use
readiness, spoken behavior, connector delivery or approval of any customer campaign.

## Original run, retained unchanged

- Source: `ccd45f10d70f91d5284b47eab49c0e6e9b13d1fe`, clean before and after.
- Profile: Groq `openai/gpt-oss-20b`, temperature 0.4, requested visible output 256;
  actual wire ceiling 1,280 tokens, reasoning effort `low`, `include_reasoning=false`.
  This is one existing selectable profile, not the default for every campaign.
- Cases: Q01, Q43, Q60, H04, H11 and H13, unchanged original questions and sources.
- Actual HTTP attempts: 11, all HTTP 200; six completed cases. Provider-reported
  usage: 22,918 prompt + 537 completion = 23,455 tokens. Reserved output: 14,080.
  No dollar cost was observed or inferred.
- Limits: 24 attempts, 30,720 reserved output tokens, 35 seconds per case and a
  hard 1,200-second outer timeout. Six-case elapsed time was 6.233 seconds; this
  text-only timing is not a voice latency measurement.
- Credential provenance: the existing protected production `GROQ_API_KEY`, read
  only into a private remote process. Account/project identifiers remain unknown.
  No key was transferred, logged, committed or placed on command-line arguments.
- Isolation: a new private checkout and virtual environment matching all 157
  packages in the candidate's Linux Python 3.12 lock. The production application,
  environment, virtual environment and services were unchanged by this test.
  Only synthetic repository knowledge and in-memory tool state were used; no
  telephone, audio, email, calendar, CRM or database action was executed.

The [original raw result](artifacts/groq-sections-20261028/original-ccd45f10-result.json)
contains serialized provider requests/responses, final answers, source evidence,
all fixture/source hashes and pending semantic-review fields. Its SHA-256 is
`f55a23bf443cd8238cc32a9d230a5f60d0050424da092302101191cbdbbfc45e`.
The [execution receipt](artifacts/groq-sections-20261028/original-ccd45f10-receipt.json)
records actual limits, source binding, package verification and usage. The
credential-output guard required zero redactions. `production_approved` remains
false and `human_approval` remains null.

## Reproduced failure and bounded correction

Q43 asks for an enterprise volume discount percentage absent from its available
source. The model correctly said it could not confirm a percentage, then offered:

> If you'd like, I can pass your question on to a specialist who can look into the exact discount rates for your situation.

The request exposed only `lookup_company_knowledge` and `end_call`. Its capability
section explicitly said an unlisted transfer or team follow-up route was
unavailable. However, the customer-support persona also unconditionally instructed
the model to "escalate or take a message when you cannot fully resolve something."
The correction removes that conflicting clause; the existing capability and
source policies remain authoritative. It adds no scripted response, model-answer
rewrite, keyword judge or artificial source fact.

The evaluator also built an inbound Alex/Fictional demonstration company guide
without setting the session's actual identity or direction. Consequently the
request's live-state block said `agent=unknown`, `company=unknown` and
`direction=outbound`. The fixture now supplies its declared `AgentConfig` and
inbound session direction through the same fields read by the current streamer.

Two request-level regressions reproduced both defects before the changes. They
check the assembled guide against the actual tool list and identity/direction;
they do not score or prescribe the model's answer. The focused evaluator,
conversation-guide, composer, direction, callback-policy and campaign-preview
suite passed all **101 tests** after the changes. It reported existing datetime
deprecation warnings and a Gemini SDK async-cleanup warning. A corrected real-provider run
must use a new output location and exact committed source. This original failure
must remain available alongside any subsequent result. Passing these request
checks alone cannot establish that the model always follows its instructions.

Q01, Q60, H04, H11 and H13 returned answers consistent with their principal source
or refusal requirements on inspection. H11 additionally offered generic scheduling
help, which should remain visible for capability review. This preliminary review
does not award a six-case pass or close the broader AG02/AG04 acceptance gates.
