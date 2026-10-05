# AG02 normalization investigation controls

Declared before running the new controls or changing application code.
Baseline: `33360ddf42c1089e78f9ee9918ba0d770f4347c7`.

The existing 60-question gold corpus, its 45 answerable annotations, ranking,
coverage threshold and historical artifacts remain unchanged. These controls
test a general lexical boundary; they do not qualify a campaign or a model.

Positive inflection controls use both directions of these independently chosen
pairs: account/accounts, fee/fees, booking/bookings, delivery/deliveries,
batch/batches and invoice/invoices. The actual pinned retriever and shared
evidence boundary should retain the same source and complete authored text.
Full PostgreSQL English-stemmer parity is not assumed.

Negative controls distinguish fee/free, new/news, policy/police, and form/from;
retain an unmatched rare term in coverage; and reject an unrelated orbital
launch question. Fuzzy retrieval alone must never turn these into matched facts.
An exclusion control must preserve `not supported` and its adjacent notice
condition even if inflection normalization finds the relevant passage.

The existing Q14 weekend/payout discrepancy is retained separately as an exact
regression observation, not substituted into the independent control inventory.
Unknown words must not be discarded to increase a coverage score. No synonym
list, embeddings, new provider/dependency or lower threshold is authorized.

Initial investigation only: application repair needs a separately reviewed
proposal. No PostgreSQL, provider or telephone calls are part of this first run.

Before any normalizer experiment, independent review added one long-source
control: singular query, plural-only answer at the end of an oversized source,
with an adjacent exclusion. It must retain both answer and qualification. This
exercises passage selection as well as coverage. Additional negative pairs are
model500/model500s, status/statue and series/species. Product identifiers must
retain their exact identity. These controls were added after the initial
12-failure/7-pass baseline, which remains retained unchanged.

The expanded baseline measured 13 failures / 10 passes. A later wheel-only
blanket Snowball probe repaired ten inflection cases but newly admitted
derivationally unrelated evidence. That proposal was rejected before any app
edit. The observed Universes/Universities, Universe courses/University course
and Organ memberships/Organization membership controls are retained as
regressions, plus business/bus and a long unknown-token denominator control.

Before running the next prototype, the parent authorized assessing only a
direct surface plural relation (`s`, `es`, `y`/`ies`) AND agreement from the
established English Snowball implementation. All other tokens remain exact.
This is a pairwise relation, never equality of arbitrary stems. No exception
words or question aliases are permitted. The same comparison must apply at
candidate overlap, coverage and oversized-passage selection. Candidate and
relative ranking can change from additional lexical overlap; numeric scoring
weights, fuzzy rules, gold source text, annotations and thresholds cannot change.
No application integration or dependency addition is authorized by this probe.
