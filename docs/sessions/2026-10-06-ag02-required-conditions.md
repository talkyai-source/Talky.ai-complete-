# AG02: preserve required terms when selecting a short passage

Source commit: `15c6a7c0`. The previous selector could return a price from a long
source while dropping its adjacent obligation. For example, it admitted
“Starter costs £19 per month” as matched evidence while omitting “A twelve-month
contract is required.” The existing grouping recognized exclusions and sentences
starting with “this,” but not these ordinary obligation markers.

The narrow repair adds `requir*`, `minimum`, `must` and `mandatory` to that existing
grouping. The complete price/requirement group is returned when it fits, otherwise
it is withheld. No ranking, confidence threshold, candidate window, budget,
provider, dependency, prompt or gold fixture changed.

Fourteen new controls exercise each marker with enough space, insufficient space
and the shared voice-evidence boundary; they also retain complete short sources,
unconditional prices and chained requirements. Before the repair, **13 failed and
1 passed**. Afterward, **143 tests passed across ten affected modules**, with ten
warnings, in 6.70 seconds. The fourteen controls are included in that total.
Scoped Ruff `F` and source diff checks passed. Independent read-only review found
no blocker; all sixteen recorded source hashes matched before/after/current.

The guarded runs admitted only standard-library internal socketpairs and recorded
zero prohibited socket or async-transport attempts. The selected existing Python
venv and `PYTHONPATH=.` were used, with an unavailable synthetic database URL;
this is not a claim of exact-requirements, actual database or provider acceptance.
The final run qualified the uncommitted patch on `c01175c1`, subsequently committed
unchanged as `15c6a7c0`; no post-commit test rerun is implied.

This is bounded English marker handling, not a general semantic guarantee that
every possible qualifier is recognized. It does not establish improved recall,
model answer fidelity, human listening or customer acceptance. The unchanged raw
AG02 knowledge-quality failure remains open.

Evidence: [manifest](artifacts/ag02-required-conditions/manifest.json),
[baseline](artifacts/ag02-required-conditions/baseline.txt),
[final result](artifacts/ag02-required-conditions/final.json),
[final log](artifacts/ag02-required-conditions/final.txt).
