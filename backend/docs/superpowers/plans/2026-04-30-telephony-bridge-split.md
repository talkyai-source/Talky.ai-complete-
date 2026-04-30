# Telephony Bridge Split — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the 2355-line `backend/app/api/v1/endpoints/telephony_bridge.py` into a focused package, with caller-speaks-first and agent-speaks-first logic in dedicated mode files. No behavior change.

**Architecture:** Domain logic moves to `backend/app/domain/services/telephony/`, organized into `config.py`, `lifecycle.py`, `recording.py`, and `modes/{agent_first,user_first}.py` plus a dispatcher. The endpoint file shrinks to FastAPI routes only and re-exports moved symbols for backward compatibility (tests and other modules import private helpers from it).

**Tech Stack:** Python 3.12, FastAPI, pytest. No new dependencies.

**Spec:** `backend/docs/superpowers/specs/2026-04-30-telephony-bridge-split-design.md`

**Working directory for all commands:** `/home/ai-lab/Desktop/Talky.ai-complete-/backend`

---

## Pre-flight: identify all external imports

External code that imports private symbols from `telephony_bridge`:

| Importer | Symbols used |
|---|---|
| `app/main.py:178` | `telephony_bridge as _tb` (lifecycle hook — verify exact attribute access) |
| `app/api/v1/routes.py:38` | `router` (public) |
| `app/core/telephony_observability.py:401` | `telephony_bridge` (verify exact attribute access) |
| `tests/unit/test_freeswitch_transfer_api.py` | `TransferPayload`, `transfer_blind`, `transfer_attended` |
| `tests/unit/test_telephony_bridge_first_speaker.py` | `_outbound_first_speaker`, `_user_first_open_seconds`, `_user_first_fallback_enabled` |

**Every symbol moved out must be re-exported from `telephony_bridge.py`** so these importers keep working. The plan adds re-exports as part of each extraction task.

---

## Task 1: Create the package skeleton + smoke import test

**Files:**
- Create: `backend/app/domain/services/telephony/__init__.py` (empty)
- Create: `backend/app/domain/services/telephony/modes/__init__.py` (empty)
- Create: `backend/tests/unit/test_telephony_package_imports.py`

- [ ] **Step 1: Create empty package directories**

```bash
mkdir -p backend/app/domain/services/telephony/modes
touch backend/app/domain/services/telephony/__init__.py
touch backend/app/domain/services/telephony/modes/__init__.py
```

- [ ] **Step 2: Write smoke import test**

Create `backend/tests/unit/test_telephony_package_imports.py`:

```python
"""Smoke test: telephony package imports without cycles or errors."""


def test_package_imports():
    from app.domain.services import telephony  # noqa: F401
    from app.domain.services.telephony import modes  # noqa: F401
```

- [ ] **Step 3: Run the test**

```bash
cd backend && pytest tests/unit/test_telephony_package_imports.py -v
```

Expected: PASS (1 passed).

- [ ] **Step 4: Verify external importers still pass**

```bash
cd backend && pytest tests/unit/test_telephony_bridge_first_speaker.py tests/unit/test_freeswitch_transfer_api.py -v
```

Expected: all PASS — no behavior changed yet.

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/services/telephony/ backend/tests/unit/test_telephony_package_imports.py
git commit -m "refactor(telephony): create empty package skeleton for bridge split"
```

---

## Task 2: Extract `config.py`

Move three small leaf helpers: `_outbound_first_speaker`, `_build_telephony_session_config`, `_build_outbound_greeting`.

**Files:**
- Create: `backend/app/domain/services/telephony/config.py`
- Modify: `backend/app/api/v1/endpoints/telephony_bridge.py` (lines 135–183 region — remove definitions, replace with re-exports)

- [ ] **Step 1: Create `config.py` with moved helpers**

Open `backend/app/api/v1/endpoints/telephony_bridge.py` and copy these three function definitions verbatim:

- `_outbound_first_speaker` (around line 135)
- `_build_telephony_session_config` (around line 148)
- `_build_outbound_greeting` (around line 168)

Paste into a new file `backend/app/domain/services/telephony/config.py`. Add the imports those functions need at the top:

```python
"""Configuration helpers for the telephony bridge.

Leaf module — no dependencies on other telephony submodules. Owns:
- env-driven first-speaker default
- session-config builder
- greeting builder
"""
from __future__ import annotations

import logging
import os

from app.domain.services.telephony_session_config import (
    build_telephony_session_config,
    build_telephony_greeting,
)

logger = logging.getLogger(__name__)
```

Then paste the three function bodies. Keep names identical (leading underscore preserved) so re-exports are trivial.

- [ ] **Step 2: Replace the originals in `telephony_bridge.py` with re-exports**

In `telephony_bridge.py`, delete the three function definitions and replace them with a single import block at the top of the file (after the existing imports, around line 49):

```python
# Re-exports kept for backward compatibility with tests and other modules
# that import private helpers from this endpoint module. The implementations
# live in app.domain.services.telephony.config.
from app.domain.services.telephony.config import (
    _outbound_first_speaker,
    _build_telephony_session_config,
    _build_outbound_greeting,
)
```

- [ ] **Step 3: Run the targeted tests**

```bash
cd backend && pytest tests/unit/test_telephony_bridge_first_speaker.py tests/unit/test_telephony_package_imports.py -v
```

Expected: all PASS — `_outbound_first_speaker` resolves through the re-export.

- [ ] **Step 4: Run the full unit test suite**

```bash
cd backend && pytest tests/unit/ -x --ignore=tests/unit/test_voice_pipeline_runtime.py -q 2>&1 | tail -30
```

Expected: no new failures vs. the pre-refactor baseline. If `test_voice_pipeline_runtime.py` is currently passing locally, include it; if it was already failing pre-refactor, that's a separate issue.

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/services/telephony/config.py backend/app/api/v1/endpoints/telephony_bridge.py
git commit -m "refactor(telephony): extract config helpers to domain/services/telephony/config.py"
```

---

## Task 3: Extract `modes/user_first.py`

Move user-first silence handling. The functions are around lines 361–660 of `telephony_bridge.py`.

**Files:**
- Create: `backend/app/domain/services/telephony/modes/user_first.py`
- Modify: `backend/app/api/v1/endpoints/telephony_bridge.py` (remove user-first block, add re-exports)

- [ ] **Step 1: Create `modes/user_first.py`**

Copy these three definitions verbatim from `telephony_bridge.py`:

- `_user_first_open_seconds` (around line 361)
- `_user_first_fallback_enabled` (around line 390)
- `_handle_user_first_silence` (around line 406)

Paste into `backend/app/domain/services/telephony/modes/user_first.py` with this header:

```python
"""User-first (caller-speaks-first) call-flow handler.

Used when the campaign owner selects ``first_speaker = "user"``. The bridge
does NOT play a greeting; instead it waits for the callee to speak first
and arms a silence watchdog that fires a fallback prompt if the callee
stays silent past ``USER_FIRST_OPEN_SECONDS``.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)
```

Add any other imports the moved code needs (inspect the `_handle_user_first_silence` body — likely `app.domain.services.voice_pipeline_service` or similar; copy whatever it actually references from the top of the original file).

- [ ] **Step 2: Add re-exports in `telephony_bridge.py`**

Delete the three functions from `telephony_bridge.py`. Add to the re-export block created in Task 2:

```python
from app.domain.services.telephony.modes.user_first import (
    _user_first_open_seconds,
    _user_first_fallback_enabled,
    _handle_user_first_silence,
)
```

- [ ] **Step 3: Run the targeted test**

```bash
cd backend && pytest tests/unit/test_telephony_bridge_first_speaker.py -v
```

Expected: all PASS — the test imports `_user_first_open_seconds` and `_user_first_fallback_enabled` from `telephony_bridge`, which now resolve through the re-export.

- [ ] **Step 4: Run the unit suite**

```bash
cd backend && pytest tests/unit/ -x -q 2>&1 | tail -15
```

Expected: no new failures.

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/services/telephony/modes/user_first.py backend/app/api/v1/endpoints/telephony_bridge.py
git commit -m "refactor(telephony): extract user-first mode handler"
```

---

## Task 4: Extract `modes/agent_first.py`

Move the agent-first greeting send path. Functions are around lines 186–326 of `telephony_bridge.py`.

**Files:**
- Create: `backend/app/domain/services/telephony/modes/agent_first.py`
- Modify: `backend/app/api/v1/endpoints/telephony_bridge.py`

- [ ] **Step 1: Create `modes/agent_first.py`**

Copy `_send_outbound_greeting` (around line 186) verbatim into a new file with this header:

```python
"""Agent-first (assistant-speaks-first) call-flow handler.

Used when the campaign owner selects ``first_speaker = "agent"`` (the default).
On answer, the bridge plays a pre-synthesized greeting (fast path) or falls
back to realtime synthesis. Handles barge-in: if the callee speaks during
the greeting, only the spoken portion is persisted to conversation history.
"""
from __future__ import annotations

import asyncio
import logging

from app.domain.services.telephony.config import _build_outbound_greeting

logger = logging.getLogger(__name__)
```

Add any other imports the function body needs (TTS service, voice_session helpers — copy whatever the original referenced).

- [ ] **Step 2: Add re-export in `telephony_bridge.py`**

Delete `_send_outbound_greeting` from `telephony_bridge.py`. Append to the re-export block:

```python
from app.domain.services.telephony.modes.agent_first import (
    _send_outbound_greeting,
)
```

- [ ] **Step 3: Run targeted tests**

```bash
cd backend && pytest tests/unit/test_telephony_bridge_first_speaker.py tests/unit/test_telephony_package_imports.py -v
```

Expected: all PASS.

- [ ] **Step 4: Run the unit suite**

```bash
cd backend && pytest tests/unit/ -x -q 2>&1 | tail -15
```

Expected: no new failures.

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/services/telephony/modes/agent_first.py backend/app/api/v1/endpoints/telephony_bridge.py
git commit -m "refactor(telephony): extract agent-first mode handler"
```

---

## Task 5: Create the mode dispatcher

A single function that picks the right mode handler based on `voice_session._first_speaker`. Replaces the inline conditional at `telephony_bridge.py:1200`.

**Files:**
- Modify: `backend/app/domain/services/telephony/modes/__init__.py`
- Create: `backend/tests/unit/test_telephony_mode_dispatcher.py`
- Modify: `backend/app/api/v1/endpoints/telephony_bridge.py:1197-1205` (replace inline conditional)

- [ ] **Step 1: Write the failing dispatcher test**

Create `backend/tests/unit/test_telephony_mode_dispatcher.py`:

```python
"""Dispatcher resolves first_speaker per voice_session, falling back to env."""
import os
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.services.telephony.modes import resolve_first_speaker


def test_per_call_attr_wins():
    sess = SimpleNamespace(_first_speaker="user")
    with patch.dict(os.environ, {"TELEPHONY_FIRST_SPEAKER": "agent"}):
        assert resolve_first_speaker(sess) == "user"


def test_falls_back_to_env_when_attr_missing():
    sess = SimpleNamespace()
    with patch.dict(os.environ, {"TELEPHONY_FIRST_SPEAKER": "user"}):
        assert resolve_first_speaker(sess) == "user"


def test_falls_back_to_env_when_attr_none():
    sess = SimpleNamespace(_first_speaker=None)
    with patch.dict(os.environ, {"TELEPHONY_FIRST_SPEAKER": "user"}):
        assert resolve_first_speaker(sess) == "user"


def test_unknown_value_clamps_to_agent():
    sess = SimpleNamespace(_first_speaker="bogus")
    with patch.dict(os.environ, {}, clear=True):
        assert resolve_first_speaker(sess) == "agent"
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd backend && pytest tests/unit/test_telephony_mode_dispatcher.py -v
```

Expected: FAIL with `ImportError: cannot import name 'resolve_first_speaker'`.

- [ ] **Step 3: Implement the dispatcher**

Replace the contents of `backend/app/domain/services/telephony/modes/__init__.py` with:

```python
"""First-speaker dispatcher.

Resolves which mode handler to use for a given voice_session:

1. Per-call: ``voice_session._first_speaker`` (set by ``make_call`` from the
   campaign's ``first_speaker`` value, which travels via the dialer worker's
   query param).
2. Fallback: env var ``TELEPHONY_FIRST_SPEAKER`` (handled by
   ``config._outbound_first_speaker``).
3. Clamp to ``{"agent", "user"}``; anything else → ``"agent"``.
"""
from __future__ import annotations

from typing import Literal

from app.domain.services.telephony.config import _outbound_first_speaker


def resolve_first_speaker(voice_session) -> Literal["agent", "user"]:
    raw = getattr(voice_session, "_first_speaker", None) or _outbound_first_speaker()
    value = (raw or "").strip().lower()
    return "user" if value == "user" else "agent"
```

- [ ] **Step 4: Run the test to confirm it passes**

```bash
cd backend && pytest tests/unit/test_telephony_mode_dispatcher.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Replace the inline conditional in `telephony_bridge.py`**

In `telephony_bridge.py`, find the block around line 1197–1205 that currently reads:

```python
first_speaker = getattr(voice_session, "_first_speaker", None) or _outbound_first_speaker()
if first_speaker == "agent":
    ...
```

Replace the resolution line with:

```python
from app.domain.services.telephony.modes import resolve_first_speaker
first_speaker = resolve_first_speaker(voice_session)
if first_speaker == "agent":
    ...
```

(Move the `import` to the top of the file with the other imports, not inline.)

- [ ] **Step 6: Run the full unit suite**

```bash
cd backend && pytest tests/unit/ -x -q 2>&1 | tail -15
```

Expected: no new failures. The first-speaker tests still pass because they exercise `_outbound_first_speaker` directly.

- [ ] **Step 7: Commit**

```bash
git add backend/app/domain/services/telephony/modes/__init__.py backend/tests/unit/test_telephony_mode_dispatcher.py backend/app/api/v1/endpoints/telephony_bridge.py
git commit -m "refactor(telephony): add mode dispatcher and route on_new_call through it"
```

---

## Task 6: Extract `recording.py`

Move `_save_call_recording` (around lines 1371–1615 of `telephony_bridge.py` — ~244 lines).

**Files:**
- Create: `backend/app/domain/services/telephony/recording.py`
- Modify: `backend/app/api/v1/endpoints/telephony_bridge.py`

- [ ] **Step 1: Create `recording.py`**

Copy `_save_call_recording` verbatim into the new file with this header:

```python
"""Stereo-WAV recording pipeline for telephony calls.

Builds a stereo WAV (caller left / agent right), resolves the canonical
calls.row tenant_id, inserts the recording_s3 row, and uploads to S3 if
configured. Falls back to disk-only save when DB context is unavailable.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
```

Copy the imports the function uses from the top of `telephony_bridge.py` (recording_service, S3 client, DB session, etc.).

- [ ] **Step 2: Re-export from `telephony_bridge.py`**

Delete `_save_call_recording` from `telephony_bridge.py`. Append to the re-export block:

```python
from app.domain.services.telephony.recording import _save_call_recording
```

- [ ] **Step 3: Run tests**

```bash
cd backend && pytest tests/unit/ -x -q 2>&1 | tail -15
```

Expected: no new failures.

- [ ] **Step 4: Commit**

```bash
git add backend/app/domain/services/telephony/recording.py backend/app/api/v1/endpoints/telephony_bridge.py
git commit -m "refactor(telephony): extract recording pipeline to dedicated module"
```

---

## Task 7: Extract `lifecycle.py`

The biggest move. Targets in `telephony_bridge.py`:

| Symbol | Approx line |
|---|---|
| `_pop_ringing_warmup` | 105 |
| `_get_orchestrator` | 130 |
| `_session_watchdog` | 661 |
| `_pipeline_done_cb` | 759 |
| `_on_ringing` | 779 |
| `_reject_overcap_call` | 940 |
| `_on_new_call` | 961 |
| `_on_audio_received` | 1271 |
| `_on_call_ended` | 1284 |
| `_on_ws_session_start` | 1616 |

Plus module-level state: `_ringing_warmups`, `_ringing_warmup_created_at`, `_RINGING_MAX_AGE_S`, `_early_audio_buffers`, `_EARLY_AUDIO_MAX_CHUNKS`, `_gateway_session_to_call_id`, `_telephony_sessions`, `_MAX_TELEPHONY_SESSIONS`, `_watchdog_task`, `_adapter`.

**Files:**
- Create: `backend/app/domain/services/telephony/lifecycle.py`
- Modify: `backend/app/api/v1/endpoints/telephony_bridge.py`

- [ ] **Step 1: Create `lifecycle.py` with module state and all listed handlers**

Header:

```python
"""Telephony call-lifecycle orchestration.

Owns: ringing-phase warmup, on-answer session creation, audio routing,
call-end teardown, watchdog GC, and the active-session registry. The
FastAPI endpoint module delegates all per-call hooks here.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from app.domain.interfaces.call_control_adapter import CallControlAdapter
from app.domain.services.telephony.config import (
    _build_telephony_session_config,
)
from app.domain.services.telephony.modes import resolve_first_speaker
from app.domain.services.telephony.modes.agent_first import (
    _send_outbound_greeting,
)
from app.domain.services.telephony.modes.user_first import (
    _handle_user_first_silence,
    _user_first_fallback_enabled,
)
from app.domain.services.telephony.recording import _save_call_recording

logger = logging.getLogger(__name__)
```

Copy the module-level state declarations from `telephony_bridge.py` lines 57–102 (everything from `_adapter` through `_RINGING_MAX_AGE_S`). Then copy each function listed in the table verbatim.

- [ ] **Step 2: Replace originals in `telephony_bridge.py` with re-exports**

Delete the moved state and functions. Append to the re-export block:

```python
from app.domain.services.telephony import lifecycle as _lifecycle
from app.domain.services.telephony.lifecycle import (
    _pop_ringing_warmup,
    _get_orchestrator,
    _session_watchdog,
    _pipeline_done_cb,
    _on_ringing,
    _reject_overcap_call,
    _on_new_call,
    _on_audio_received,
    _on_call_ended,
    _on_ws_session_start,
)

# Module-state passthroughs — some external code (main.py, observability)
# reads these by attribute access. Forward via __getattr__ so reads always
# see the live value in lifecycle, not a stale snapshot.
def __getattr__(name: str):
    if name in {
        "_adapter", "_telephony_sessions", "_watchdog_task",
        "_gateway_session_to_call_id", "_early_audio_buffers",
        "_ringing_warmups", "_ringing_warmup_created_at",
        "_MAX_TELEPHONY_SESSIONS", "_EARLY_AUDIO_MAX_CHUNKS",
        "_RINGING_MAX_AGE_S",
    }:
        return getattr(_lifecycle, name)
    raise AttributeError(name)
```

- [ ] **Step 3: Verify the external importers still work**

Inspect what `app/main.py:178` and `app/core/telephony_observability.py:401` actually access on the module:

```bash
cd backend && grep -nA3 "telephony_bridge" app/main.py app/core/telephony_observability.py | head -40
```

For every attribute they read, confirm it's either a re-exported function or covered by the `__getattr__` passthrough. If any attribute is missing, add it to the `__getattr__` set.

- [ ] **Step 4: Run the unit suite**

```bash
cd backend && pytest tests/unit/ -x -q 2>&1 | tail -15
```

Expected: no new failures.

- [ ] **Step 5: Run the full test suite**

```bash
cd backend && pytest tests/ -x -q 2>&1 | tail -20
```

Expected: no new failures (or only pre-existing failures unrelated to this refactor).

- [ ] **Step 6: Commit**

```bash
git add backend/app/domain/services/telephony/lifecycle.py backend/app/api/v1/endpoints/telephony_bridge.py
git commit -m "refactor(telephony): extract lifecycle orchestration to dedicated module"
```

---

## Task 8: Verify slim `telephony_bridge.py`

`telephony_bridge.py` should now contain only:
- Module docstring
- Stdlib + framework imports
- `router = APIRouter(...)`
- The re-export block from Tasks 2–7
- FastAPI route handlers: `start_telephony`, `stop_telephony`, `telephony_status`, `make_call`, `hangup_call`, `TransferPayload`, `transfer_blind`, `transfer_attended`, `transfer_deflect`, `receive_gateway_audio`, `telephony_audio_websocket`
- The `__getattr__` shim from Task 7

**Files:**
- Modify: `backend/app/api/v1/endpoints/telephony_bridge.py` (cleanup only)

- [ ] **Step 1: Check current line count**

```bash
cd backend && wc -l app/api/v1/endpoints/telephony_bridge.py
```

Expected: under 600 lines. If over, identify what's left and split it (e.g., if `make_call` is still 400 lines, split its pre-originate greeting prep into `agent_first.prepare_pre_originate_greeting` and call from `make_call`).

- [ ] **Step 2: Confirm no orphaned helpers**

```bash
cd backend && grep -nE "^(async def|def) _" app/api/v1/endpoints/telephony_bridge.py
```

Expected: zero results, or only helpers that are genuinely route-local. If any remain that belong in the domain layer, move them.

- [ ] **Step 3: Confirm all four telephony submodules are ≤600 lines**

```bash
cd backend && wc -l app/domain/services/telephony/*.py app/domain/services/telephony/modes/*.py
```

Expected: every line count under 600.

- [ ] **Step 4: Run the full unit suite**

```bash
cd backend && pytest tests/unit/ -x -q 2>&1 | tail -15
```

Expected: no new failures.

- [ ] **Step 5: Commit (only if cleanup was needed)**

```bash
git add backend/app/api/v1/endpoints/telephony_bridge.py
git commit -m "refactor(telephony): final cleanup of slimmed bridge endpoint"
```

If nothing changed, skip the commit.

---

## Task 9: Write docs one-pagers

Match the existing convention seen in `backend/docs/script/`.

**Files:**
- Create: `backend/docs/script/telephony/README.md`
- Create: `backend/docs/script/telephony/agent_first.md`
- Create: `backend/docs/script/telephony/user_first.md`
- Create: `backend/docs/script/telephony/lifecycle.md`
- Create: `backend/docs/script/telephony/recording.md`
- Create: `backend/docs/script/telephony/config.md`

- [ ] **Step 1: Write `README.md` (index)**

Contents:

```markdown
# Telephony package

Domain logic for the telephony bridge endpoint. The FastAPI routes live at
`backend/app/api/v1/endpoints/telephony_bridge.py` and delegate to this
package.

## Modules

- [`config.md`](config.md) — env defaults, session-config builder, greeting builder
- [`modes/agent_first.md`](agent_first.md) — agent-speaks-first greeting flow
- [`modes/user_first.md`](user_first.md) — user-speaks-first silence fallback
- [`lifecycle.md`](lifecycle.md) — call-lifecycle orchestration (ringing → end)
- [`recording.md`](recording.md) — stereo-WAV recording pipeline

## Mode selection

The campaign owner picks `first_speaker = "agent" | "user"` at campaign creation
(`app/api/v1/endpoints/campaigns.py`). The value travels through the dialer
worker as a `make_call?first_speaker=…` query param and is stashed on
`voice_session._first_speaker`. The dispatcher in `modes/__init__.py`
(`resolve_first_speaker`) reads it per call. Env var `TELEPHONY_FIRST_SPEAKER`
is the fallback default.
```

- [ ] **Step 2: Write `agent_first.md`**

Contents:

```markdown
# `modes/agent_first.py` — Agent-speaks-first handler

## When used

`voice_session._first_speaker == "agent"` (the default, and the value picked
by most campaigns).

## Flow

1. On answer, `_send_outbound_greeting(voice_session)` is invoked from
   `lifecycle._on_new_call`.
2. **Fast path:** if `voice_session._presynth_greeting_audio` is populated
   (synthesized during the ringing phase), pump it into the media gateway
   directly. First audio reaches the callee with no TTS round-trip.
3. **Realtime path:** otherwise, build the greeting via
   `config._build_outbound_greeting` and synthesize on the spot.
4. **Barge-in:** if the callee speaks during playback, only the spoken
   portion of the greeting is persisted to the conversation history so the
   LLM doesn't echo the unspoken tail on the next turn.

## Knobs

- `TELEPHONY_FIRST_SPEAKER=agent` — global default (override via campaign).
- Greeting text comes from `telephony_session_config.build_telephony_greeting`
  using the agent name and tenant business name.
```

- [ ] **Step 3: Write `user_first.md`**

Contents:

```markdown
# `modes/user_first.py` — User-speaks-first handler

## When used

`voice_session._first_speaker == "user"`. Picked by campaigns where the
callee should drive the opening turn (e.g., the agent is responding to an
inbound greeting like "Hello?").

## Flow

1. On answer, no greeting is played.
2. `_handle_user_first_silence(voice_session, pbx_call_id)` arms a silence
   watchdog: if no speech is detected within
   `_user_first_open_seconds()` and `_user_first_fallback_enabled()` is
   true, the agent speaks a fallback prompt to keep the call alive.
3. Once the callee speaks, the watchdog disarms and the normal STT → LLM →
   TTS turn loop takes over.

## Knobs

- `USER_FIRST_OPEN_SECONDS` — fallback timer (default in
  `_user_first_open_seconds`).
- `USER_FIRST_FALLBACK_ENABLED` — toggle the fallback prompt.
- `TELEPHONY_FIRST_SPEAKER=user` — global default (override via campaign).
```

- [ ] **Step 4: Write `lifecycle.md`**

Contents:

```markdown
# `lifecycle.py` — Call-lifecycle orchestration

## Responsibilities

- **Ringing phase:** `_on_ringing` pre-warms a `VoiceSession` while the
  callee's phone is still ringing, so STT/TTS handshakes are done by the
  time the callee answers. Pre-synthesized greeting (agent-first) lands
  here too, in `voice_session._presynth_greeting_audio`.
- **Answer:** `_on_new_call` drains the ringing-warmup cache, registers
  the session, calls `resolve_first_speaker`, and dispatches to either
  `agent_first._send_outbound_greeting` or
  `user_first._handle_user_first_silence`.
- **Audio:** `_on_audio_received` routes inbound audio chunks from the
  C++ gateway to the right session, with an early-audio buffer for chunks
  that arrive before `_on_new_call` has registered the mapping.
- **End:** `_on_call_ended` triggers `recording._save_call_recording`,
  ends the voice session, and removes the entry from
  `_telephony_sessions`.
- **Watchdog:** `_session_watchdog` GCs orphaned ringing-warmup entries
  (callee never answered, no terminal event) older than
  `_RINGING_MAX_AGE_S = 180s`.

## Module state (singletons per process)

- `_telephony_sessions: dict[call_id, VoiceSession]`
- `_ringing_warmups: dict[call_id, (VoiceSession, connect_task)]`
- `_ringing_warmup_created_at: dict[call_id, float]`
- `_early_audio_buffers: dict[gateway_session_id, list[bytes]]`
- `_gateway_session_to_call_id: dict[gateway_session_id, call_id]`
- `_adapter: CallControlAdapter | None`
- `_watchdog_task: asyncio.Task | None`
```

- [ ] **Step 5: Write `recording.md`**

Contents:

```markdown
# `recording.py` — Stereo-WAV recording pipeline

## What it does

`_save_call_recording(voice_session, call_id)` builds a stereo WAV
(caller left, agent right) from the session's per-channel PCM buffers
and persists it.

## Persistence layers (in order)

1. **Disk:** WAV is always written to
   `backend/recordings/{call_id}.wav`. This is the source of truth.
2. **`calls` row resolution:** look up the canonical row by
   `external_call_uuid`. If missing, attempt a stub-row insert; if the
   call has no campaign and no session tenant, skip the DB step and
   keep the disk-only save.
3. **`recording_s3` row:** insert metadata (path, duration, size,
   tenant_id, call_id) for the dashboard player.
4. **S3 upload:** if configured, push the WAV and update the row with
   the S3 key.

## Known issues (out of scope for this refactor)

- Stub-row insert fails when neither campaign nor session tenant is
  available — falls back to disk-only with a warning.
- `recording_s3` insert can fail with `badly formed hexadecimal UUID
  string` for certain channel-name shapes. Tracked separately.
```

- [ ] **Step 6: Write `config.md`**

Contents:

```markdown
# `config.py` — Telephony config helpers

Leaf module — no dependencies on other telephony submodules.

## Functions

- `_outbound_first_speaker() -> str`
  Reads `TELEPHONY_FIRST_SPEAKER` env var, defaults to `"agent"`.
  Used as the fallback when a call has no per-call override.

- `_build_telephony_session_config(...)`
  Wraps `domain.services.telephony_session_config.build_telephony_session_config`
  with telephony-specific defaults.

- `_build_outbound_greeting(session) -> str`
  Wraps `telephony_session_config.build_telephony_greeting` so the greeting
  text and the system prompt stay in sync (same `agent_name`, same
  `company` value).
```

- [ ] **Step 7: Commit**

```bash
git add backend/docs/script/telephony/
git commit -m "docs(telephony): one-pagers for split package modules"
```

---

## Task 10: Final verification

- [ ] **Step 1: Full test suite**

```bash
cd backend && pytest tests/ -q 2>&1 | tail -20
```

Expected: same pass/fail count as before this refactor began (no new failures introduced).

- [ ] **Step 2: Line-count audit**

```bash
cd backend && wc -l app/api/v1/endpoints/telephony_bridge.py app/domain/services/telephony/*.py app/domain/services/telephony/modes/*.py
```

Expected: every file ≤600 lines.

- [ ] **Step 3: Smoke-test imports from external call sites**

```bash
cd backend && python -c "
from app.api.v1.endpoints import telephony_bridge as tb
assert callable(tb._outbound_first_speaker)
assert callable(tb._user_first_open_seconds)
assert callable(tb._user_first_fallback_enabled)
assert callable(tb.transfer_blind)
assert tb.TransferPayload is not None
assert tb.router is not None
print('OK: all backward-compat re-exports resolve')
"
```

Expected: `OK: all backward-compat re-exports resolve`.

- [ ] **Step 4: Live call test (manual)**

Start the backend and run one campaign in each mode:

1. Create a campaign with `first_speaker = "agent"`. Trigger one call. Confirm the greeting plays.
2. Create a campaign with `first_speaker = "user"`. Trigger one call. Stay silent past the fallback timer. Confirm the agent's fallback prompt fires.

Both must behave identically to pre-refactor behavior.

- [ ] **Step 5: Final commit (only if anything changed during verification)**

```bash
git status
# If clean, no commit needed.
```

---

## Acceptance checklist

- [ ] All telephony files ≤600 lines
- [ ] `pytest tests/unit/test_telephony_bridge_first_speaker.py` passes
- [ ] `pytest tests/unit/test_telephony_mode_dispatcher.py` passes
- [ ] `pytest tests/unit/test_telephony_package_imports.py` passes
- [ ] `pytest tests/unit/test_freeswitch_transfer_api.py` passes
- [ ] Full unit suite passes (no new failures)
- [ ] Live agent-first call works
- [ ] Live user-first call works
- [ ] Each domain module has a one-pager in `backend/docs/script/telephony/`
- [ ] No import cycles (smoke test passes)
