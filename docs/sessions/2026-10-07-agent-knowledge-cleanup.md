# Knowledge remnants cleanup

Base `458c6798`; source/tests `55895906bd9d7f0bcf48cd75f3e7095dda33b2eb`.

The dashboard assistant's existing `retrieve_knowledge` tool now browses a catalog (`catalog_offset`) and reads selected exact `section_ids` using the same authored-section reader as calls. Its schema, registry and prompt agree. Each invocation acquires the existing actor/tenant/campaign read lease and loads the current published source; `current_read` distinguishes this from a pinned call snapshot. Changed source references fail instead of selecting a replacement implicitly. Returned passages retain source/version proof and include full governing context once. Availability is not proof that a section answers the user's question.

Removed the no-op recovery/mode/addendum functions and unused sentinels from `knowledge_tool.py`, the 423-line superseded query qualification runner, and its 130-line obsolete test module. The [deletion inventory](artifacts/agent-knowledge-cleanup/deletion-inventory.json) records names and retained boundaries. Manual `/knowledge/test` source search, its pure diagnostic tests, the shared scoped loader, gold data and historical evidence remain. `get_knowledge_tree` remains metadata inspection, explicitly not factual evidence. Root separately removes the streamer no-op and the retired-CLI assertion in its model-turn test.

Focused synthetic verification:

| Run | Result |
|---|---|
| New assistant boundary controls before implementation | 6 failed, 28 passed, 1 warning; 5.79s |
| First affected eight-module run | 156 passed, 1 failed, 4 warnings; 8.37s |
| Final frozen source | **157 passed, 4 warnings; 5.09s** |

The intermediate failure counted a closing-tag mention in the native data-only note as a source fence; the test now counts inside the actual fenced body. No application change was needed for it. Security tests retain poisoned-source, role-marker, heading, atomic-selection and fence checks using current adapters; retired per-turn SQL timeout fixtures were removed. Existing setup timeout coverage remains elsewhere.

The [exact commands](artifacts/agent-knowledge-cleanup/commands.json), outputs and result records preserve all runs, selected source hashes, interpreter and installed versions. Network guards recorded zero prohibited attempts. No provider or database ran. `git diff --check` passed. Ruff F checks found only 16 existing unused imports in `assistant/agent.py`; its executable AST outside the changed prompt is identical to the base. Other edited surviving Python paths had no F diagnostics.

This is source/permission/adapter evidence, not live model quality or paid-use acceptance. No new provider, service, feature or acceptance gate was introduced.
