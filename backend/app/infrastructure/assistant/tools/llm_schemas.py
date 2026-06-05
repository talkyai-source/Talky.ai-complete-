"""
Groq LLM tool schemas for the assistant agent.

Extracted from agent.py to keep that module under 600 lines.
The 10 original schemas are listed first (verbatim), followed by
the 6 campaign-admin tools added in feat/assistant-campaign-tools.
"""

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
            "description": "Get leads list with optional filters",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string", "description": "Filter by campaign ID"},
                    "status": {"type": "string", "description": "Filter by status"},
                    "limit": {"type": "integer", "description": "Max leads to return", "default": 10}
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
                    "today_only": {"type": "boolean", "description": "Only show today's calls", "default": True},
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
            "description": "Send an email to recipients. Supports templates: meeting_confirmation, follow_up, reminder. Uses Gmail if connected, SMTP fallback otherwise.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "array", "items": {"type": "string"}, "description": "Email addresses"},
                    "subject": {"type": "string", "description": "Email subject (ignored if using template)"},
                    "body": {"type": "string", "description": "Email body (ignored if using template)"},
                    "template_name": {"type": "string", "description": "Template to use: meeting_confirmation, follow_up, or reminder"},
                    "template_context": {"type": "object", "description": "Variables for template (e.g., attendee_name, title, date, time)"}
                },
                "required": ["to", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_sms",
            "description": "Send SMS to phone numbers",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "array", "items": {"type": "string"}, "description": "Phone numbers"},
                    "message": {"type": "string"}
                },
                "required": ["to", "message"]
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
                "List a campaign's knowledge-tree nodes (headings, summaries, enabled, hit_count)."
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
                "Run the LIVE knowledge retriever for a caller-style question and show exactly "
                "what the agent would pull from the tree. Use to test knowledge quality."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string", "description": "Campaign UUID"},
                    "query": {"type": "string", "description": "The caller question to test"}
                },
                "required": ["campaign_id", "query"]
            }
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
                        "type": "boolean",
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
                        "type": "boolean",
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
                        "type": "boolean",
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
                    "voice_id": {"type": "string"},
                    "confirm": {"type": "boolean", "description": "false = preview only; true = apply after user approval"}
                },
                "required": ["campaign_ids", "tts_provider", "voice_id"]
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


# Applied once at import: optional params become nullable so a model passing
# `null` for an unset optional arg doesn't fail Groq's tool-call validation.
GROQ_TOOL_SCHEMAS = _make_optional_params_nullable(GROQ_TOOL_SCHEMAS)
