# Future Changes — Telephony Estimation Agent

This file documents every value that is currently hardcoded in the telephony
estimation agent and the exact production steps to make each one dynamic.

All hardcoded values in the source are tagged `# TODO(production)` so you can
find them instantly with:
```bash
grep -rn "TODO(production)" backend/app/
```

---

## What Is Hardcoded Right Now

### 1. Company Name
| Detail | Value |
|--------|-------|
| **Current value** | `"All States Estimation"` |
| **File** | `backend/app/domain/services/telephony_session_config.py` — `TELEPHONY_COMPANY_NAME` |
| **Used in** | System prompt + greeting (via `build_telephony_session_config`) |

### 2. Agent Name Pool
| Detail | Value |
|--------|-------|
| **Current value** | 20 generic English first-names in `AGENT_NAMES` list |
| **File** | `backend/app/domain/services/telephony_session_config.py` — `AGENT_NAMES` |
| **Used in** | System prompt + greeting — one name picked randomly per session at creation time, baked in for the full call |

### 3. Voice Selection
| Detail | Value |
|--------|-------|
| **Current value** | Falls through to `get_global_config().tts_voice_id` (global AI options) |
| **File** | `build_telephony_session_config()` — `tts_voice_id = global_config.tts_voice_id` |
| **Note** | `campaigns.voice_id` column already exists in DB — it just isn't being read yet |

### 4. Estimation System Prompt
| Detail | Value |
|--------|-------|
| **Current value** | `TELEPHONY_ESTIMATION_SYSTEM_PROMPT` hardcoded in `telephony_session_config.py` |
| **File** | `backend/app/domain/services/telephony_session_config.py` |
| **Note** | `campaigns.system_prompt` column already exists in DB — it just isn't being read yet |

### 5. Greeting Template
| Detail | Value |
|--------|-------|
| **Current value** | `build_telephony_greeting()` returns a hardcoded English template |
| **File** | `backend/app/domain/services/telephony_session_config.py` |
| **Note** | `campaigns.prompt_config.greeting_override` JSONB field exists for this |

---

## Production Migration Steps

### Step 1 — Add campaign → call_id mapping in `telephony_bridge.py`

The bridge receives `campaign_id` at call-origination time (the `/call` endpoint)
but `_on_ringing` / `_on_new_call` only have the PBX `call_id`. Bridge them with
a module-level dict:

```python
# In telephony_bridge.py — add near _telephony_sessions (line ~51)
_call_to_campaign_id: dict[str, str] = {}
```

In the `make_call` endpoint, after `call_id = await _adapter.originate_call(...)`:
```python
if campaign_id:
    _call_to_campaign_id[call_id] = campaign_id
```

In `_on_call_ended(call_id)` — clean up to prevent memory leak:
```python
_call_to_campaign_id.pop(call_id, None)
```

### Step 2 — Make `build_telephony_session_config` accept `campaign_id` and fetch from DB

```python
# In telephony_session_config.py
from typing import Optional

def build_telephony_session_config(
    gateway_type: str = "telephony",
    campaign_id: Optional[str] = None,
    campaign=None,
) -> VoiceSessionConfig:
    ...
    if campaign_id:
        from app.core.container import get_container
        db = get_container().db_client
        row = (
            db.table("campaigns")
            .select("voice_id, system_prompt, script_config")
            .eq("id", campaign_id)
            .single()
            .execute()
        )
        if row.data:
            data = row.data
            if data.get("voice_id"):
                tts_voice_id = data["voice_id"]
            if data.get("system_prompt"):
                system_prompt_override = data["system_prompt"]
            if data.get("script_config"):
                sc = data["script_config"]
                company_name = sc.get("company_name", company_name)
                campaign_names = sc.get("agent_names")
                if campaign_names:
                    agent_name = random.choice(campaign_names)
```

### Step 3 — Pass `campaign_id` from `_on_ringing` and `_on_new_call`

```python
# In telephony_bridge.py — both warmup paths

# In _on_ringing(call_id):
campaign_id = _call_to_campaign_id.get(call_id)
config = build_telephony_session_config(gateway_type="telephony", campaign_id=campaign_id)

# In _on_new_call(call_id) slow path:
campaign_id = _call_to_campaign_id.get(call_id)
config = build_telephony_session_config(gateway_type=gateway_type, campaign_id=campaign_id)
```

Also update the thin shim `_build_telephony_session_config` to accept and forward
`campaign_id` — or remove the shim entirely once all call sites are updated.

### Step 4 — Add campaign UI fields

These DB columns already exist — only the UI campaign creation/edit forms need
to expose them:

| DB column | Campaign UI field | Used for |
|-----------|-------------------|----------|
| `campaigns.voice_id` | Voice selector | TTS voice per campaign |
| `campaigns.system_prompt` | System prompt textarea | LLM behaviour |
| `campaigns.script_config.company_name` | Company name input | Greeting + prompt identity |
| `campaigns.script_config.agent_names` | Agent names (list) | Per-call random name pool |
| `campaigns.prompt_config.greeting_override` | Greeting text | Custom opener spoken at answer |

### Step 5 — Remove hardcoded fallbacks (optional)

Once all campaigns are guaranteed to have company name and system prompt set via
the UI, `TELEPHONY_COMPANY_NAME` and `TELEPHONY_ESTIMATION_SYSTEM_PROMPT` can be
removed from `telephony_session_config.py`. Or keep them as last-resort fallbacks
with a `logger.warning(...)` so misconfigured campaigns surface immediately in logs.

---

## Recording

No changes needed. `_save_call_recording()` in `telephony_bridge.py` runs
automatically on every call end — it mixes caller + agent audio into a stereo WAV
and persists it to storage + DB. Estimation agent calls are recorded identically
to all other telephony calls.
