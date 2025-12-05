"""
Prompt Template System
Manages prompt templates and rendering for conversation states
"""
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
from jinja2 import Template, Environment, BaseLoader
import logging

from app.domain.models.conversation_state import ConversationState
from app.domain.models.agent_config import AgentConfig

logger = logging.getLogger(__name__)


class PromptTemplate(BaseModel):
    """Single prompt template"""
    name: str = Field(..., description="Template name")
    template: str = Field(..., description="Jinja2 template string")
    variables: List[str] = Field(default_factory=list, description="Required variables")
    examples: List[Dict[str, str]] = Field(default_factory=list, description="Few-shot examples")
    language: str = Field(default="en", description="Language code")
    
    def render(self, **kwargs) -> str:
        """Render template with provided variables"""
        env = Environment(loader=BaseLoader())
        template = env.from_string(self.template)
        return template.render(**kwargs)


class PromptManager:
    """
    Manages prompt templates and rendering
    Provides context-aware prompts for each conversation state
    """
    
    def __init__(self):
        """Initialize prompt manager with default templates"""
        self.templates: Dict[str, PromptTemplate] = {}
        self._load_default_templates()
    
    def _load_default_templates(self):
        """Load default prompt templates"""
        
        # Base system prompt template - Following Groq's official prompting guidelines
        # Structure: Role -> Instructions -> Context -> Expected Output
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
{% for rule in do_not_say_rules %}
- {{ rule }}
{% endfor %}

### Response Format
- Direct and concise
- Natural speech (not robotic)
- No repetition or unnecessary details
- No greetings like "Sure!" or "Of course!" at the start""",
            variables=["agent_name", "company_name", "business_type", "goal_description", "tone", "max_sentences", "do_not_say_rules"]
        )
        
        # Greeting state template
        self.templates["greeting_state"] = PromptTemplate(
            name="greeting_state",
            template="""CURRENT STATE: GREETING

Your task: Start the conversation warmly and state your purpose clearly.

Example greeting:
"Hi, this is {{ agent_name }} from {{ company_name }}. {{ greeting_context }}"

Keep it brief and friendly. Wait for their response.""",
            variables=["agent_name", "company_name", "greeting_context"],
            examples=[
                {
                    "user": "Hello?",
                    "agent": "Hi! This is Sarah from Bright Smile Dental. I'm calling to confirm your appointment tomorrow at 2 PM. Is this still a good time for you?"
                }
            ]
        )
        
        # Qualification state template
        self.templates["qualification_state"] = PromptTemplate(
            name="qualification_state",
            template="""CURRENT STATE: QUALIFICATION

Your task: {{ qualification_instruction }}

Ask ONE specific question to move forward.
Listen carefully to their response.

{% if context_info %}
Context: {{ context_info }}
{% endif %}""",
            variables=["qualification_instruction"],
            examples=[
                {
                    "user": "Yes, I'm interested",
                    "agent": "Great! What time works best for you - morning or afternoon?"
                }
            ]
        )
        
        # Objection handling template
        self.templates["objection_handling_state"] = PromptTemplate(
            name="objection_handling_state",
            template="""CURRENT STATE: OBJECTION HANDLING

The user expressed: {{ user_concern }}

Your task: Address their concern in ONE sentence, then ask ONE short question.
- Maximum 2 sentences total
- Be empathetic but brief

This is attempt {{ objection_count }} of {{ max_objections }}.

{% if objection_count >= max_objections %}
If still uncertain, offer to call back later.
{% endif %}""",
            variables=["user_concern", "objection_count", "max_objections"],
            examples=[
                {
                    "user": "I'm not sure if I can make it",
                    "agent": "I understand schedules can be tricky. Would a different time work better for you?"
                }
            ]
        )
        
        # Closing state template
        self.templates["closing_state"] = PromptTemplate(
            name="closing_state",
            template="""CURRENT STATE: CLOSING

Your task: Confirm the next steps clearly and end positively.

{% if confirmation_details %}
Confirm these details: {{ confirmation_details }}
{% endif %}

Thank them briefly and wish them well.
Keep it short - 1-2 sentences maximum.""",
            variables=[],
            examples=[
                {
                    "user": "Yes, that works",
                    "agent": "Perfect! Your appointment is confirmed for tomorrow at 2 PM. See you then!"
                }
            ]
        )
        
        # Transfer state template
        self.templates["transfer_state"] = PromptTemplate(
            name="transfer_state",
            template="""CURRENT STATE: TRANSFER

The user requested to speak with a human.

Your task: Acknowledge politely and confirm transfer.
- Thank them for their time
- Confirm you're transferring them now
- Keep it very brief (1 sentence)""",
            variables=[],
            examples=[
                {
                    "user": "I want to talk to a person",
                    "agent": "Of course! Let me transfer you to a team member right now. One moment please."
                }
            ]
        )
        
        # Goodbye state template
        self.templates["goodbye_state"] = PromptTemplate(
            name="goodbye_state",
            template="""CURRENT STATE: GOODBYE

Your task: End the conversation politely.
- Thank them for their time
- Wish them well
- Keep it VERY brief (1 sentence)

{% if reason == 'declined' %}
Be gracious even though they declined.
{% endif %}""",
            variables=[],
            examples=[
                {
                    "user": "No thanks",
                    "agent": "No problem! Have a great day!"
                },
                {
                    "user": "Goodbye",
                    "agent": "Thank you for your time. Take care!"
                }
            ]
        )
    
    def render_system_prompt(
        self,
        agent_config: AgentConfig,
        state: ConversationState,
        **kwargs
    ) -> str:
        """
        Render complete system prompt for current state
        
        Args:
            agent_config: Agent configuration
            state: Current conversation state
            **kwargs: Additional context variables
        
        Returns:
            Rendered system prompt
        """
        # Render base system prompt
        base_prompt = self.templates["base_system"].render(
            agent_name=agent_config.agent_name,
            company_name=agent_config.company_name,
            business_type=agent_config.business_type,
            goal_description=agent_config.get_goal_description(),
            tone=agent_config.tone,
            max_sentences=agent_config.response_max_sentences,
            do_not_say_rules=agent_config.rules.do_not_say_rules
        )
        
        # Render state-specific prompt
        state_template_name = f"{state.value}_state"
        if state_template_name in self.templates:
            state_prompt = self.templates[state_template_name].render(**kwargs)
        else:
            state_prompt = f"CURRENT STATE: {state.value.upper()}\n\nContinue the conversation naturally."
        
        # Combine prompts
        full_prompt = f"{base_prompt}\n\n{state_prompt}"
        
        logger.debug(f"Rendered system prompt for state={state.value}, length={len(full_prompt)}")
        
        return full_prompt
    
    def render_state_instruction(
        self,
        state: ConversationState,
        **kwargs
    ) -> str:
        """
        Render instruction for specific state
        
        Args:
            state: Conversation state
            **kwargs: Context variables
        
        Returns:
            Rendered instruction
        """
        state_template_name = f"{state.value}_state"
        if state_template_name in self.templates:
            return self.templates[state_template_name].render(**kwargs)
        return f"Continue conversation in {state.value} state."
    
    def add_template(self, template: PromptTemplate):
        """Add or update a template"""
        self.templates[template.name] = template
        logger.info(f"Added template: {template.name}")
    
    def get_template(self, name: str) -> Optional[PromptTemplate]:
        """Get template by name"""
        return self.templates.get(name)
    
    def list_templates(self) -> List[str]:
        """List all template names"""
        return list(self.templates.keys())
