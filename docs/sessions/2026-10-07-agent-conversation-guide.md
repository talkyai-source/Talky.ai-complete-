# Natural conversation guide — 2026-10-07

The platform prompt previously imposed staged sales/support scripts, exact contact readbacks and commands appended to caller messages. The text cleaner also erased ordinary parentheses and polite phrases, including meaningful qualifications such as “excluding VAT”. This slice implements the user's revised model-led conversation approach; it does not claim model fidelity or production acceptance.

Source: `0c385e86b5da360c5095aeb35e765dde1945a0e0`, based on `0270d44981a4218e16619d7a65b4ffb7dc79a8ef`. Twelve app files and ten tests. The root independently reviewed the source. Streamer/tool/native-bridge wiring belongs to separate changes.

The three traditional personas now supply a concise guide and campaign context. Identity, direction, operator guidance, field validation, company-source rules, privacy and action-result truth remain. Neutral JSON exposes contact values/status, incomplete caller quotes, previous confirmed contacts, unconfirmed line and unscheduled follow-up requests without prescribing the next sentence. No caller-message command is appended. Native realtime retains its separate prompt builder. Generic parenthetical and polite-opener removal is retired; explicit reasoning/markdown/audio artifact cleanup remains.

Prompt versions: `lead_gen@14`, `customer_support@12`, `receptionist@12`, `realtime@9`; historic immutable hash entries are unchanged.

| Representative base prompt | Before words | After words |
|---|---:|---:|
| Sales, campaign slots | 1,931 | 679 |
| Support, campaign slots | 2,467 | 648 |
| Receptionist, campaign slots | 2,427 | 641 |
| Sales, knowledge-driven | 1,790 | 598 |
| Support, knowledge-driven | 1,264 | 550 |
| Receptionist, knowledge-driven | 1,254 | 540 |
| Native realtime | 970 | 511 |

These are whitespace word counts of actual composed base prompts using Ava/Northwind and the saved fixture slots. They exclude runtime/catalog additions and do not measure tokens or latency. Exact character counts/output hashes are in the before/after JSON artifacts.

Qualification: the original first13 guide controls produced **12 failures/1 pass** on the old source. A partial raw contact control was added later, giving14 new controls. An initial affected run preserved68 failures/166 passes: those failures required old scripted wording, old prompt versions or removed cleanup behavior. Tests now assert preserved identity, direction, campaign fields, caller-text integrity, tool-result truth, state and ordinary qualification text. Final affected13modules: **234 passed,33warnings,4.01s**. Existing datetime deprecation warnings remain.

After that final run, comments were corrected and the already-empty model-specific addendum was simplified to return an empty string directly. Follow-up39 composer/version tests passed in1.74s; scoped RuffF and diff checks passed. This is overlapping follow-up evidence, not273 independent controls. No subsequent spoken prompt text changed.

No provider/model, customer, audio, database or browser calls were made for this slice. Composition tests cannot establish whether a model chooses the right source, interprets contacts correctly or reports tool outcomes faithfully. The unchanged legacy validator's unit checks do not mean it is still a live speech judge. The production freeze and remaining acceptance gates remain open.

Evidence: [checks and exact commands](artifacts/agent-conversation-guide/checks.json), [final affected log](artifacts/agent-conversation-guide/final.txt), [baseline](artifacts/agent-conversation-guide/baseline.txt), [before size](artifacts/agent-conversation-guide/prompt-size-before.json), [after size](artifacts/agent-conversation-guide/prompt-size-after.json).
