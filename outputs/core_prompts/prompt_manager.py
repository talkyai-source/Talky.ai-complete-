"""
app/domain/services/prompt_manager.py

Updated to integrate the CorePromptEngine.

Priority order for system prompt selection:
  1. If AgentConfig.context has "system_prompt" key → use it as-is (legacy / custom)
  2. If AgentConfig.context has "prompt_type" key → compile via CorePromptEngine
  3. If AgentConfig.goal matches a known PromptType → auto-select and compile
  4. Fall back to the original base_system template (backward compatible)

This means all existing campaigns keep working, and new campaigns
automatically get the core prompt treatment.
"""
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
from jinja2 import Environment, BaseLoader
import logging

from app.domain.models.conversation_state import ConversationState
from app.domain.models.agent_config import AgentConfig
from app.domain.services.core_prompts import (
    CorePromptEngine,
    CompileRequest,
    PromptType,
    get_core_prompt_engine,
)

logger = logging.getLogger(__name__)


class PromptTemplate(BaseModel):
    """Single prompt template."""
    name: str = Field(..., description="Template name")
    template: str = Field(..., description="Jinja2 template string")
    variables: List[str] = Field(default_factory=list)
    examples: List[Dict[str, str]] = Field(default_factory=list)
    language: str = Field(default="en")

    def render(self, **kwargs) -> str:
        env = Environment(loader=BaseLoader())
        tmpl = env.from_string(self.template)
        return tmpl.render(**kwargs)


class PromptManager:
    """
    Manages prompt templates and rendering.

    Now delegates to CorePromptEngine for campaigns that have a
    prompt_type set. Falls back to the original template system
    for backward compatibility.
    """

    def __init__(self):
        self.templates: Dict[str, PromptTemplate] = {}
        self._engine: CorePromptEngine = get_core_prompt_engine()
        self._load_default_templates()

    # ── Primary entry point used by VoicePipelineService ──────────────────

    def get_system_prompt(self, agent_config: Optional[AgentConfig]) -> str:
        """
        Get the compiled system prompt for an agent config.

        This is the single method called by the voice pipeline.
        It routes to the right prompt source automatically.
        """
        if agent_config is None:
            return self._fallback_prompt()

        ctx = agent_config.context or {}

        # Route 1: explicit system_prompt in context (legacy / fully custom)
        if "system_prompt" in ctx and ctx["system_prompt"].strip():
            return ctx["system_prompt"].strip()

        # Route 2: prompt_type in context → compile via CorePromptEngine
        if "prompt_type" in ctx:
            return self._compile_from_context(agent_config, ctx)

        # Route 3: agent goal maps to a known prompt type → auto-compile
        goal_str = agent_config.goal if isinstance(agent_config.goal, str) else agent_config.goal.value
        if goal_str in ("appointment_confirmation", "callback_scheduling",
                        "lead_qualification", "reminder",
                        "information_gathering", "survey"):
            prompt_type = self._engine.get_prompt_type_for_goal(goal_str)
            return self._compile_from_config(agent_config, prompt_type, ctx)

        # Route 4: original template fallback
        return self._legacy_render(agent_config)

    # ── CorePromptEngine compilation paths ─────────────────────────────────

    def _compile_from_context(self, agent_config: AgentConfig, ctx: dict) -> str:
        """Compile using explicit prompt_type from context."""
        try:
            req = CompileRequest(
                prompt_type=PromptType(ctx["prompt_type"]),
                business_name=agent_config.company_name,
                agent_name=agent_config.agent_name,
                client_description=ctx.get(
                    "client_description",
                    f"{agent_config.business_type} — {agent_config.get_goal_description()}"
                ),
                client_custom_rules=agent_config.rules.do_not_say_rules or [],
                context={k: v for k, v in ctx.items()
                         if k not in ("system_prompt", "prompt_type", "client_description")},
            )
            prompt = self._engine.compile(req)
            logger.debug(f"Compiled core prompt type={ctx['prompt_type']} for {agent_config.company_name}")
            return prompt
        except Exception as exc:
            logger.warning(f"Core prompt compile failed, falling back: {exc}")
            return self._legacy_render(agent_config)

    def _compile_from_config(
        self,
        agent_config: AgentConfig,
        prompt_type: PromptType,
        ctx: dict,
    ) -> str:
        """Auto-compile from AgentConfig fields when no explicit prompt_type is set."""
        try:
            req = CompileRequest(
                prompt_type=prompt_type,
                business_name=agent_config.company_name,
                agent_name=agent_config.agent_name,
                client_description=ctx.get(
                    "client_description",
                    f"{agent_config.business_type} — {agent_config.get_goal_description()}"
                ),
                client_custom_rules=agent_config.rules.do_not_say_rules or [],
                context={k: v for k, v in ctx.items()
                         if k not in ("system_prompt", "prompt_type", "client_description")},
            )
            prompt = self._engine.compile(req)
            logger.debug(f"Auto-compiled core prompt type={prompt_type} for {agent_config.company_name}")
            return prompt
        except Exception as exc:
            logger.warning(f"Auto core prompt compile failed, falling back: {exc}")
            return self._legacy_render(agent_config)

    # ── Original template rendering (backward compatible) ──────────────────

    def _legacy_render(self, agent_config: AgentConfig) -> str:
        """Original base_system template rendering — used as fallback."""
        return self.templates["base_system"].render(
            agent_name=agent_config.agent_name,
            company_name=agent_config.company_name,
            business_type=agent_config.business_type,
            goal_description=agent_config.get_goal_description(),
            tone=agent_config.tone,
            max_sentences=agent_config.response_max_sentences,
            do_not_say_rules=agent_config.rules.do_not_say_rules,
        )

    def _fallback_prompt(self) -> str:
        return "You are a professional voice assistant. Be concise, helpful, and natural. Maximum 2 sentences per reply."

    # ── State-specific rendering (unchanged from original) ─────────────────

    def render_system_prompt(
        self,
        agent_config: AgentConfig,
        state: ConversationState,
        **kwargs,
    ) -> str:
        """Render complete system prompt for current state."""
        base_prompt = self.get_system_prompt(agent_config)
        state_template_name = f"{state.value}_state"
        if state_template_name in self.templates:
            state_prompt = self.templates[state_template_name].render(**kwargs)
            return f"{base_prompt}\n\n{state_prompt}"
        return base_prompt

    def render_state_instruction(self, state: ConversationState, **kwargs) -> str:
        state_template_name = f"{state.value}_state"
        if state_template_name in self.templates:
            return self.templates[state_template_name].render(**kwargs)
        return f"Continue conversation in {state.value} state."

    def add_template(self, template: PromptTemplate):
        self.templates[template.name] = template

    def get_template(self, name: str) -> Optional[PromptTemplate]:
        return self.templates.get(name)

    def list_templates(self) -> List[str]:
        return list(self.templates.keys())

    # ── Default templates (unchanged) ──────────────────────────────────────

    def _load_default_templates(self):
        self.templates["base_system"] = PromptTemplate(
            name="base_system",
            template="""### Role
You are {{ agent_name }}, a professional voice assistant for {{ company_name }} ({{ business_type }}).

### Instructions
1. Your purpose: {{ goal_description }}
2. Respond in {{ max_sentences }} sentences or fewer
3. Use natural, conversational speech patterns
4. Be {{ tone }}
5. Never use filler words (um, uh, well, like, actually, basically)
6. Never think out loud or explain reasoning
7. Get straight to the point

### Strict Rules
{% for rule in do_not_say_rules %}- {{ rule }}
{% endfor %}

### Response Format
- Direct and concise
- Natural speech (not robotic)
- No repetition or unnecessary details
- No greetings like "Sure!" or "Of course!" at the start""",
            variables=["agent_name", "company_name", "business_type", "goal_description",
                       "tone", "max_sentences", "do_not_say_rules"],
        )

        self.templates["greeting_state"] = PromptTemplate(
            name="greeting_state",
            template="""CURRENT STATE: GREETING
Your task: Start the conversation warmly and state your purpose clearly.
Example: "Hi, this is {{ agent_name }} from {{ company_name }}. {{ greeting_context }}"
Keep it brief and friendly. Wait for their response.""",
            variables=["agent_name", "company_name", "greeting_context"],
        )

        self.templates["qualification_state"] = PromptTemplate(
            name="qualification_state",
            template="""CURRENT STATE: QUALIFICATION
Your task: {{ qualification_instruction }}
Ask ONE specific question to move forward. Listen carefully to their response.
{% if context_info %}Context: {{ context_info }}{% endif %}""",
            variables=["qualification_instruction"],
        )

        self.templates["objection_handling_state"] = PromptTemplate(
            name="objection_handling_state",
            template="""CURRENT STATE: OBJECTION HANDLING
The user expressed: {{ user_concern }}
Your task: Address their concern in ONE sentence, then ask ONE short question.
This is attempt {{ objection_count }} of {{ max_objections }}.
{% if objection_count >= max_objections %}If still uncertain, offer to call back later.{% endif %}""",
            variables=["user_concern", "objection_count", "max_objections"],
        )

        self.templates["closing_state"] = PromptTemplate(
            name="closing_state",
            template="""CURRENT STATE: CLOSING
Your task: Confirm the next steps clearly and end positively.
{% if confirmation_details %}Confirm these details: {{ confirmation_details }}{% endif %}
Thank them briefly and wish them well. 1-2 sentences maximum.""",
            variables=[],
        )

        self.templates["transfer_state"] = PromptTemplate(
            name="transfer_state",
            template="""CURRENT STATE: TRANSFER
The user requested to speak with a human.
Acknowledge politely and confirm transfer. 1 sentence maximum.""",
            variables=[],
        )

        self.templates["goodbye_state"] = PromptTemplate(
            name="goodbye_state",
            template="""CURRENT STATE: GOODBYE
End the conversation politely. Thank them. Wish them well. 1 sentence maximum.
{% if reason == 'declined' %}Be gracious even though they declined.{% endif %}""",
            variables=[],
        )
