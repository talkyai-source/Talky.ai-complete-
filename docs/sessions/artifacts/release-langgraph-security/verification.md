# Patched LangGraph dependency qualification — 2026-10-08

The backend requirements now use `langgraph==1.2.4`, `langgraph-sdk==0.4.4`, `langgraph-prebuilt==1.1.0`, and `langchain-core==1.4.0`. The existing `langchain-groq==1.1.2` and `langsmith==0.8.18` pins remain unchanged. This is a dependency-only security repair; no application behavior was edited in this slice.

## Why these versions

[GHSA-fvww-7h3r-vfhp](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-fvww-7h3r-vfhp) affects LangGraph SDK resource authorization registration through 0.4.3 and identifies 0.4.4 as patched. The prior parent pin 1.1.10 only accepts SDK versions below 0.4. A repository search found no application use of the affected SDK authorization decorators; that observation did not waive the dependency gate.

Captured [PyPI parent metadata](https://pypi.org/pypi/langgraph/1.2.4/json) shows 1.2.4 is the first non-yanked compatible parent examined. Versions 1.2.0–1.2.2 still exclude SDK 0.4; 1.2.3 accepts it but is yanked for an unintended merging-strategy regression. That rejected candidate and resolver log remain separate evidence. Parent 1.2.4 requires core at least 1.4.0 and prebuilt at least 1.1.0. The exact minimum compatible pins passed the complete requirements resolver. No new provider or application capability was added.

## Observed checks

| Check | Actual result |
| --- | --- |
| Full `backend/requirements.txt` clean pip resolution | 156 resolved packages, exit 0 |
| Audit of all 156 exact resolved packages | Zero known vulnerabilities; no ignored vulnerabilities or skipped package entries |
| Isolated LangGraph/LangChain closure installation | 37 packages; every installed version matches the full resolver |
| Actual module locations | `langgraph.graph`, `langgraph_sdk`, `langchain_core`, and `langgraph.prebuilt` imported from the new isolated target |
| Assistant and graph regression | 327 passed, zero skipped, 65 warnings, 6.78 seconds |
| Offline network guard | Zero blocked network attempts during regression |

`commands.json` records exact arguments and synthetic environment values. `resolver.json`, `resolved-requirements.txt`, `audit.json`, `installed.json`, and `regression.txt` retain machine-readable and command-output evidence. Audit used `--no-deps --disable-pip` on the **complete already-resolved 156-package list**, so it did not omit transitive dependencies. Pip-audit emitted its recommendation to hash pinned requirements; no audit suppression was used.

The three additional artifact-local tests exercise the application's real compiled graph: plain `ainvoke` message reduction; an actual model-node → tool-node → model-node loop retaining tenant/user/conversation and tool-call identity; and `astream` provider failure producing the existing safe error with no tool effect. Only the model and tool effect ports are synthetic. The remaining tests exercise existing assistant tools, plans, service/API/WebSocket behavior, authorization, receipt recovery, and reviewed-account selection.

## Setup failures and rejected environments

- The first installation command used requirements containing extras as pip constraints. Pip rejected that constraints syntax before installation. `install.txt` retains it.
- A first narrow closure install using only direct constraints chose tenacity 9.2.1, conflicting with another requirement's `<9.2.0` ceiling. That target was rejected without running application tests. `install-final.txt` is this rejected attempt, despite its historical filename.
- The qualified target is **`backend/tmp/langgraph-security-exact-packages`**, installed with every full-resolver pin as a constraint. `install-exact.txt` is the successful installation; tenacity is 9.1.4. Do not use the earlier `langgraph-security-packages` target.
- An initial import-evidence script used Windows' default text encoding to read the UTF-8 pip report and failed before testing. Explicit UTF-8 decoding fixed that script; this was not an application regression.

## Source and environment limits

The application tree is the merged release application at d99ebcad. HEAD advanced to b8345ee9 during this work solely for an unrelated existing infrastructure test fixture; the tested application tree did not change. The release owner subsequently committed the reviewed requirements as **602a263a**. The manifest records canonical LF hashes and Git application-tree identities. No staging or commit was performed by the dependency agent.

This is Windows/Python qualification of the 37-package upgraded closure layered ahead of the existing local test dependencies, plus a complete 156-package resolution and audit. It is **not** a clean installation and execution of all 156 packages, a Linux deployment check, a provider/model semantic test, database acceptance, or evidence of production activation. No shared virtual environment was modified. The older root regression used the previous dependencies and is not counted here. Root's broader upgraded regression is separate evidence.

The offline guard rejects external socket/DNS/HTTP activity and designated local database/Redis ports, while allowing loopback ephemeral sockets needed by the Windows event loop. Zero attempts were observed. No actual email, call, model, or database effects were requested.

LLM agent performed a read-only dependency/compatibility review: no material defect found in the selected pin set or the proposed actual-graph coverage. That review did not run independent installs, tests, or provider calls.
