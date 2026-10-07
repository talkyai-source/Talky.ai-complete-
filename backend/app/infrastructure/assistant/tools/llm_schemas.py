"""
Groq LLM tool schemas for the assistant agent.

Extracted from agent.py to keep that module under 600 lines.
The 10 original schemas are listed first (verbatim), followed by
the 6 campaign-admin tools added in feat/assistant-campaign-tools.
"""

from copy import deepcopy

from app.services.scripts.knowledge.sections import SECTION_TOOL_PARAMETERS

_CAMPAIGN_SECTION_PARAMETERS = deepcopy(SECTION_TOOL_PARAMETERS)
_CAMPAIGN_SECTION_PARAMETERS["properties"]["campaign_id"] = {"type": "string", "description": "Campaign UUID"}
_CAMPAIGN_SECTION_PARAMETERS["required"] = ["campaign_id"]

GROQ_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_dashboard_stats",
            "description": "Get today's call statistics - total calls, success rate, active campaigns",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date in YYYY-MM-DD format, defaults to today"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_usage_info",
            "description": "Get plan usage - minutes allocated, used, remaining, subscription status",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_leads",
            "description": "Get leads/contacts list with optional filters. Set only_leads=true to return just the qualified leads. Each lead includes is_lead and follow_up_note.",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string", "description": "Filter by campaign ID"},
                    "status": {"type": "string", "description": "Filter by status"},
                    "only_leads": {"type": ["boolean", "string"], "description": "Only return contacts flagged as qualified leads", "default": False},
                    "limit": {"type": "integer", "description": "Max leads to return (max 100)", "default": 25}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_lead_followup",
            "description": "Get the follow-up for ONE lead — its follow-up note/tips and the qualified call's summary. Resolve by lead_id, phone_number, or name. Use when the user asks how to follow up with a specific person or wants that lead's call summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lead_id": {"type": "string", "description": "Lead/contact id"},
                    "phone_number": {"type": "string", "description": "Lead phone number (exact or partial)"},
                    "name": {"type": "string", "description": "Lead name (first or last, partial match)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_qualified_leads",
            "description": "Recently-qualified leads (newest first) WITH phone numbers and follow-up notes. Use for 'any new leads?', 'who qualified today/during this campaign?', or to alert the client about qualified leads — always read out name + number + follow-up.",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string", "description": "Optional: only leads from this campaign"},
                    "limit": {"type": "integer", "description": "Max leads to return (default 10)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_emails",
            "description": "List recent emails (subject, sender, snippet, id) from the connected Gmail inbox. Optional Gmail search `query` (e.g. 'from:jane@acme.com', 'subject:demo', 'newer_than:2d') and unread_only. Read-only — then call read_email for a full message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Gmail search string (optional)"},
                    "unread_only": {"type": ["boolean", "string"], "description": "Only unread emails"},
                    "max_results": {"type": "integer", "description": "How many to list (default 10, max 25)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_email",
            "description": "Read ONE email's full body by its message_id (obtained from read_emails). Read-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "The email's message id from read_emails"}
                },
                "required": ["message_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "drive_list_files",
            "description": "List/search files in the connected Google Drive (name, type, link, id). Optional `query` matches file names. Read-only — then call drive_read_file to read a text file's contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term matched against file names (optional)"},
                    "max_results": {"type": "integer", "description": "How many to list (default 20, max 50)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "drive_read_file",
            "description": "Read a text-like Drive file's contents by file_id (from drive_list_files). Non-text/oversized files return a link instead of content. Read-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "The file id from drive_list_files"}
                },
                "required": ["file_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_calendar_events",
            "description": (
                "List upcoming events from the connected calendar (now to +days_ahead). "
                "Use for 'any meetings today/right now/this week?'. Returns title, start/end "
                "time, location, attendees. Timed events only. Read-only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days_ahead": {"type": ["integer", "string"], "description": "How many days ahead to look (default 7, max 31). Use 1 for 'today/right now'."},
                    "max_results": {"type": ["integer", "string"], "description": "Max events to return (default 10, max 25)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_campaigns",
            "description": "Get all campaigns with status and progress",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Filter by status (draft, running, completed)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_calls",
            "description": "Get recent calls with outcomes",
            "parameters": {
                "type": "object",
                "properties": {
                    "today_only": {"type": ["boolean", "string"], "description": "Only show today's calls", "default": True},
                    "limit": {"type": "integer", "default": 10}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_actions_today",
            "description": "Get assistant actions performed today (emails, SMS, calls triggered)",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Email someone. Call with confirm=false first to PREVIEW — the user sees the email with Apply/Reject buttons that send it; do NOT set confirm=true yourself. To email a LEAD/contact, omit 'to' and pass lead_id or phone_number (their email is resolved automatically). Supports templates: meeting_confirmation, follow_up, reminder. Uses Gmail if connected, else SMTP.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "array", "items": {"type": "string"}, "description": "Recipient email addresses. Omit when emailing a lead — use lead_id/phone_number."},
                    "lead_id": {"type": "string", "description": "Resolve the recipient from this lead/contact id"},
                    "phone_number": {"type": "string", "description": "Resolve the recipient from this lead's phone number"},
                    "subject": {"type": "string", "description": "Email subject (ignored if using template)"},
                    "body": {"type": "string", "description": "Email body (ignored if using template)"},
                    "template_name": {"type": "string", "description": "Template to use: meeting_confirmation, follow_up, or reminder"},
                    "template_context": {"type": "object", "description": "Variables for template (e.g., attendee_name, title, date, time)"},
                    "confirm": {"type": ["boolean", "string"], "description": "false = preview only (default); the Apply button sends. Do not set true.", "default": False}
                },
                "required": ["subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_sms",
            "description": "Preview SMS for the user to review and apply; never claim sent from a preview.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "array", "items": {"type": "string"}, "description": "Phone numbers"},
                    "message": {"type": "string"},
                    "confirm": {"type": "boolean", "description": "Keep false; the user's Apply action authorizes sending."}
                },
                "required": ["to", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "report_issue",
            "description": "Prepare a technical-issue report to support for review. Tenant id, account email and timestamp are included. The user must apply the preview to send.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Clear description of the problem in the user's words plus any specifics/error text"},
                    "category": {"type": "string", "description": "calls | voice | billing | login | dashboard | other"},
                    "severity": {"type": "string", "description": "low | normal | high"},
                    "contact_email": {"type": "string", "description": "Reporter email for follow-up; omit to use the account email on file"},
                    "confirm": {"type": "boolean", "description": "Keep false; the user applies the preview to send."}
                },
                "required": ["description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "initiate_call",
            "description": "Start an outbound call to a phone number",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_number": {"type": "string"},
                    "campaign_id": {"type": "string", "description": "Optional campaign context"}
                },
                "required": ["phone_number"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "start_campaign",
            "description": "Start or resume a campaign",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string"}
                },
                "required": ["campaign_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_campaign",
            "description": (
                "Create a NEW campaign. IMPORTANT: collect the fields from the user "
                "ONE AT A TIME (ask for the next only after the previous is answered): "
                "1) name, 2) goal, 3) type (lead_gen | customer_support | receptionist), "
                "4) company_name (the company the agent represents), 5) agent_names "
                "(the name the AI agent uses on calls), and for lead_gen ONLY also "
                "6) industry, 7) services_description (what it offers). These are the "
                "ONLY questions — NEVER invent extra ones (audience, coverage, script "
                "details); the rest of the script is auto-filled with editable "
                "defaults shown in the draft. customer_support/receptionist campaigns "
                "are created knowledge-driven automatically — no extra questions. If "
                "the user volunteers extra detail, put it in additional_instructions. "
                "Do NOT ask for a voice — it defaults to the tenant's configured "
                "voice. Every NEW campaign request starts a FRESH collection: never "
                "reuse answers given for a PREVIOUS campaign earlier in this "
                "conversation — re-ask from the name onward. The moment the last "
                "field is known you MUST immediately call this tool with "
                "confirm=false (a JSON boolean, not a quoted string) — the card "
                "shows the FULL DRAFT with Create campaign / Cancel buttons; never "
                "describe the draft in words instead of calling. The user's Create "
                "button re-calls with confirm=true. If the preview includes a "
                "duplicate warning, tell the user in ONE short sentence that a "
                "matching campaign already exists and that the card offers Create "
                "anyway / Overwrite existing / Cancel — the buttons decide, never "
                "you."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Campaign name"},
                    "goal": {"type": "string", "description": "What the campaign is trying to achieve"},
                    "persona_type": {
                        "type": "string",
                        "enum": ["lead_gen", "customer_support", "receptionist"],
                        "description": "Campaign type / agent persona",
                    },
                    "company_name": {"type": "string", "description": "Company the agent represents"},
                    "agent_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "One or more names the AI agent may use on calls",
                    },
                    "industry": {
                        "type": "string",
                        "description": "lead_gen only: the industry the company operates in",
                    },
                    "services_description": {
                        "type": "string",
                        "description": "lead_gen only: what products/services the company offers",
                    },
                    "additional_instructions": {
                        "type": "string",
                        "description": "Optional extra guidance for the agent's script",
                    },
                    "confirm": {
                        "type": ["boolean", "string"],
                        "description": "false to preview a confirm card; true to actually create",
                    },
                },
                "required": ["name", "goal", "persona_type", "company_name", "agent_names"],
            }
        }
    },
    # -------------------------------------------------------------------------
    # Campaign-admin READ tools
    # -------------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "get_campaign_detail",
            "description": (
                "Read a campaign's full config (persona, company, voice, knowledge mode, "
                "script_config) by id or name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string", "description": "Campaign UUID"},
                    "name": {"type": "string", "description": "Campaign name (partial match)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_knowledge_tree",
            "description": (
                "Inspect campaign knowledge metadata for editing; summaries are generated metadata, not factual evidence. Use retrieve_knowledge to read authored source."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string", "description": "Campaign UUID"}
                },
                "required": ["campaign_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_knowledge",
            "description": (
                "Browse campaign roots with catalog_parent='', then browse a section's children "
                "with its section ID or read section_ids for authored facts. Use next_offset "
                "for catalog pages and next_source_offset for incomplete source pages. Read "
                "all contiguous source parts with the same digest, including conditions. "
                "Headings and generated metadata are navigation, not answers. Available means "
                "source was read, not that it answers the original question. If references "
                "become unavailable after an edit, list the catalog again."
            ),
            "parameters": _CAMPAIGN_SECTION_PARAMETERS
        }
    },
    # -------------------------------------------------------------------------
    # Campaign-admin EDIT tools (confirm-gated)
    # -------------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "update_campaign_config",
            "description": (
                "Propose/apply edits to a campaign's basics. "
                "ALWAYS call with confirm=false first to preview; "
                "only call with confirm=true after the user approves."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string", "description": "Campaign UUID"},
                    "changes": {
                        "type": "object",
                        "description": (
                            "Fields to change: persona_type, company_name, agent_names, "
                            "additional_instructions, name, goal"
                        )
                    },
                    "confirm": {
                        "type": ["boolean", "string"],
                        "description": "false = preview only; true = apply after user approval"
                    }
                },
                "required": ["campaign_id", "changes"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_knowledge_node",
            "description": (
                "Propose/apply an edit to one knowledge node. "
                "confirm=false previews; confirm=true applies (after user approval)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string", "description": "Campaign UUID"},
                    "node_id": {"type": "string", "description": "Knowledge node UUID"},
                    "changes": {
                        "type": "object",
                        "description": (
                            "Fields to change: heading, content, enabled, priority, "
                            "summary, voice_answer"
                        )
                    },
                    "confirm": {
                        "type": ["boolean", "string"],
                        "description": "false = preview only; true = apply after user approval"
                    }
                },
                "required": ["campaign_id", "node_id", "changes"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_lead",
            "description": (
                "Add, remove (soft-delete), or update an existing lead/contact in a campaign. "
                "Use action='add' to create a new lead, action='remove' to soft-delete (lead_id required), "
                "or action='update' to edit an existing lead's phone number, name, or email (lead_id required). "
                "confirm=false previews; confirm=true applies (after user approval)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string", "description": "Campaign UUID"},
                    "action": {
                        "type": "string",
                        "enum": ["add", "remove", "update"],
                        "description": "add, remove (soft-delete), or update an existing lead"
                    },
                    "name": {"type": "string", "description": "Lead full name (for add)"},
                    "phone_number": {"type": "string", "description": "Phone number (required for add; optional for update)"},
                    "first_name": {"type": "string", "description": "First name (for add or update)"},
                    "last_name": {"type": "string", "description": "Last name (for add or update)"},
                    "email": {"type": "string", "description": "Email address (for add or update)"},
                    "lead_id": {"type": "string", "description": "Lead UUID (required for remove and update)"},
                    "confirm": {
                        "type": ["boolean", "string"],
                        "description": "false = preview only; true = apply after user approval"
                    }
                },
                "required": ["campaign_id", "action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "apply_campaign_voice",
            "description": "Set the TTS voice + provider (the per-campaign AI options) for one or more campaigns. The voice is validated against the provider. ALWAYS call with confirm=false first to preview, then confirm=true after the user approves.",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_ids": {"type": "array", "items": {"type": "string"}, "description": "Campaign ids to apply to"},
                    "tts_provider": {"type": "string", "description": "TTS provider, e.g. google, elevenlabs, cartesia, deepgram"},
                    "voice_id": {"type": "string", "description": "Voice name OR id (e.g. 'Orus', 'andromeda', 'Sarah', or a full id). A name is resolved to the id; call list_voices first if unsure."},
                    "confirm": {"type": ["boolean", "string"], "description": "false = preview only; true = apply after user approval"}
                },
                "required": ["campaign_ids", "tts_provider", "voice_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_voices",
            "description": "List available TTS voices (name + id) for a provider (google, elevenlabs, cartesia, deepgram). Use this to find a voice id from a name before calling apply_campaign_voice.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tts_provider": {"type": "string", "description": "google | elevenlabs | cartesia | deepgram"}
                },
                "required": ["tts_provider"]
            }
        }
    },
]


def _make_optional_params_nullable(schemas: list[dict]) -> list[dict]:
    """Allow `null` for every NON-required tool parameter.

    Strict tool-call validation (Groq — especially the larger models like
    gpt-oss-120b / kimi-k2 / qwen, less so llama-3.3-70b) rejects a tool call
    whose param is `null` when the schema types it as a single concrete type
    (e.g. {"status": null} against {"type": "string"} → 400 tool_use_failed).
    Models routinely emit `null` for an optional param they want to leave unset.
    So for each property NOT in `required`, widen its type to also accept null.
    The tool functions already treat a missing/None optional arg as "no filter".
    """
    for schema in schemas:
        params = schema.get("function", {}).get("parameters", {})
        props = params.get("properties", {})
        required = set(params.get("required", []))
        for name, prop in props.items():
            if name in required or not isinstance(prop, dict):
                continue
            t = prop.get("type")
            if isinstance(t, str) and t != "null":
                prop["type"] = [t, "null"]
            elif isinstance(t, list) and "null" not in t:
                prop["type"] = [*t, "null"]
    return schemas


# Execution tools derive their schemas from the same typed inputs as the
# registry. A registered action must not disappear from the model's tool list.
from app.infrastructure.assistant.tools import ACTION_TOOLS

_TYPED_EXECUTION_TOOLS = {
    "send_email", "send_sms", "report_issue", "check_availability", "book_meeting",
    "update_meeting", "cancel_meeting", "schedule_reminder", "execute_action_plan", "initiate_call",
}
GROQ_TOOL_SCHEMAS = [schema for schema in GROQ_TOOL_SCHEMAS
                     if schema["function"]["name"] not in _TYPED_EXECUTION_TOOLS]
for _name in sorted(_TYPED_EXECUTION_TOOLS):
    _entry = ACTION_TOOLS[_name]
    GROQ_TOOL_SCHEMAS.append({"type": "function", "function": {
        "name": _name, "description": _entry["description"],
        "parameters": _entry["input_schema"].model_json_schema(),
    }})

# Applied once at import: optional params become nullable so a model passing
# `null` for an unset optional arg doesn't fail Groq's tool-call validation.
GROQ_TOOL_SCHEMAS = _make_optional_params_nullable(GROQ_TOOL_SCHEMAS)
