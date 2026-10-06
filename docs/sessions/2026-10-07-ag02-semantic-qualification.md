# AG02 bounded semantic qualification — prepared, not executed

The new harness exercises the existing Groq serializer, bounded two-round tool
continuation and actual pinned knowledge lookup. It asks the unchanged 60 gold
questions plus 20 predeclared synthetic holdouts. No provider call has been made.
No application file, original gold question, retrieval threshold or provider
configuration was changed by this harness work.

The proposed run uses the existing `openai/gpt-oss-20b` model, `retrieve` profile,
temperature 0.4 and configured answer target 256. The existing serializer adds
1024 reasoning tokens, so the request ceiling is 1280 completion tokens. Limits
are 160 actual HTTP requests, including retries; 204800 reserved completion
tokens; 65536 request-body bytes per request and 10485760 cumulatively; 30 seconds
per case and 40 minutes overall. These are ceilings, not predicted usage, price
or response time. Missing provider usage remains unknown.

Only `POST https://api.groq.com/openai/v1/chat/completions` is permitted through
the bounded transport. No redirects, environment proxies, model-list requests,
warmup requests, telephony, database access or action tools are involved.
The script reads no `.env` file and uses only an explicitly supplied
`AG02_GROQ_API_KEY` process variable for execution. The designated key overrides
ambient key pools under the existing adapter contract. Credentials and headers
are not included in request evidence.

## Before execution

Integrate the harness with the reviewed current application prompt/tool changes,
then regenerate the dry plan so its hashes identify the actual candidate. The
owner's dry plan was made against base `76ba91ea`; it is not a live-run approval
for a later candidate. The user must designate the existing Groq account/key
source before execution. Do not ask them to paste the key into chat.

From `backend`, using the selected existing Python environment:

```text
python -B -m scripts.qualify_ag02_semantic --endpoint https://api.groq.com/openai/v1/chat/completions --model openai/gpt-oss-20b --knowledge-mode retrieve --max-requests 160 --max-total-completion-tokens 204800 --output NEW_DRY_PLAN.json
```

This command reads no credential and makes no provider request. Only after the
designated credential is supplied and the concrete run is authorized, use the
same command with a new output path and `--execute-provider`. Existing output
files cannot be overwritten. Budget exhaustion or a failed/incomplete response
remains explicit; do not silently rerun missed cases or switch accounts/models.

## Evidence interpretation

The model receives the original caller question and any supplied previous caller
question, plus the current application tool instructions. It receives no answer
labels, expected source IDs, required fragments, grading rubric or hand-authored
query substitutions. Retrieve mode supplies no knowledge map before the lookup.

The report retains original questions, exact model queries, source/version
evidence, tool results, serialized requests, response streams, final model output,
timing, observed usage and input/source hashes. The unchanged raw lexical matrix
is reported separately. Matching the rewritten query or selecting an expected
source does **not** mean the answer addresses the original question correctly.
`semantic_quality_pass` remains null and human answer fidelity remains pending.

Review every answer for product identity, negation/corrections, region, time,
eligibility, payer/payee direction, ambiguity and preserved conditions/exclusions.
Holdout rubrics are review-only. Zero unsupported critical claims must be checked
against actual answers; the harness does not award this gate automatically.
This tests the tool subsystem, not complete voice prompts, STT/TTS, native audio,
customer knowledge or paid-user acceptance.

## Offline validation

The final scoped run passed **83 tests**, including 30 new harness controls and
the existing knowledge/tool-continuation modules. The new controls send synthetic
HTTP/SSE through the actual Groq adapter; socket/async transports are blocked.
They cover exact wire settings, endpoint and budget refusal, original-question
preservation, hidden grading labels, designated key binding, no-credential dry
run, `.env` refusal, truncation and a deliberately wrong answer that still cannot
receive an automatic semantic pass. Scoped Ruff `F` and diff checks passed.

Preserved draft failures include an overbroad Windows socketpair test guard, an
SDK base-URL assumption rejected by the endpoint guard, and a response-capture
iterator that needed explicit closure when the SDK stopped at `[DONE]`. These
were harness/fixture defects, not measured provider-quality failures. Final
Python/Groq/httpx versions and input hashes are recorded in the dry plan; this
uses the existing environment, not an exact-requirements qualification.
