### Day 5: LLM Agent Logic, Conversation Engine & Prompt Templates

## Overview

**Date:** Week 1, Day 5  
**Goal:** Build the conversation state machine and dynamic prompt system for controlling agent behavior.

This document covers the conversation state machine, intent detection, state transitions, prompt template system, and agent configuration models.

---

## Table of Contents

1. [Conversation State Machine Design](#1-conversation-state-machine-design)
2. [Intent Detection System](#2-intent-detection-system)
3. [State Transition Logic](#3-state-transition-logic)
4. [Prompt Template System](#4-prompt-template-system)
5. [Agent Configuration Model](#5-agent-configuration-model)
6. [Test Results & Verification](#6-test-results--verification)
7. [Rationale Summary](#7-rationale-summary)

---

## 1. Conversation State Machine Design

### 1.1 State Definitions

The conversation follows a finite state machine with six states:

**File: `app/domain/models/conversation_state.py`**

```python
class ConversationState(str, Enum):
    """Conversation states for the agent"""
    GREETING = "greeting"           # Initial contact
    QUALIFICATION = "qualification" # Asking qualifying questions
    OBJECTION_HANDLING = "objection_handling"  # Addressing concerns
    CLOSING = "closing"             # Confirming next steps
    TRANSFER = "transfer"           # Handing off to human
    GOODBYE = "goodbye"             # Ending conversation
```

### 1.2 State Flow Diagram

```
                    +----------------+
                    |   GREETING     |
                    +-------+--------+
                            |
        +--------+----------+----------+--------+
        |        |                     |        |
     [YES]   [GREETING]           [UNCERTAIN] [REQUEST_HUMAN]
        |        |                     |        |
        v        v                     v        v
+-------+--------+--------+   +--------+--------+   +----------+
|   QUALIFICATION         |   | OBJECTION_HANDLING|   | TRANSFER  |
+-------+--------+--------+   +--------+--------+---+   +----------+
        |        |                     |
     [YES]    [NO]                  [YES]
        |        |                     |
        v        v                     v
+-------+--------+   +---------+   +-------+
|    CLOSING     |   | GOODBYE |   | CLOSING|
+-------+--------+   +---------+   +-------+
        |
     [YES/NO]
        |
        v
    +---------+
    | GOODBYE |
    +---------+
```

### 1.3 User Intent Definitions

```python
class UserIntent(str, Enum):
    """Detected user intents"""
    YES = "yes"               # Affirmative response
    NO = "no"                 # Negative response
    UNCERTAIN = "uncertain"   # Hesitation, maybe
    OBJECTION = "objection"   # Concern or pushback
    REQUEST_HUMAN = "request_human"  # Wants real person
    REQUEST_INFO = "request_info"    # Asking questions
    GREETING = "greeting"     # Hello, hi
    GOODBYE = "goodbye"       # Bye, talk later
    UNKNOWN = "unknown"       # Could not determine
```

---

## 2. Intent Detection System

### 2.1 Pattern-Based Detection

**File: `app/domain/services/conversation_engine.py`**

```python
def _build_intent_patterns(self) -> Dict[UserIntent, List[str]]:
    """Build regex patterns for intent detection"""
    return {
        UserIntent.YES: [
            r'\b(yes|yeah|yep|okay|ok|absolutely|definitely|confirm)\b',
            r'\b(sounds good|that works|perfect|great)\b',
            r'\b(i can do that|i will do that)\b',
        ],
        UserIntent.NO: [
            r"\b(no|nope|nah|not really)\b",
            r"\b(i can't|i cannot|i won't)\b",
            r"\b(don't want|don't need|not interested)\b",
        ],
        UserIntent.UNCERTAIN: [
            r"\b(maybe|perhaps|possibly|hmm+|uh+)\b",
            r"\b(not sure|i'm not sure|i'm not certain)\b",
            r"\b(let me (think|check))\b",
        ],
        UserIntent.OBJECTION: [
            r'\b(too (expensive|costly|much))\b',
            r"\b(don't have (time|money))\b",
            r'\bnot (right )?now\b',
        ],
        UserIntent.REQUEST_HUMAN: [
            r'\b(speak to|talk to|transfer|human|person|agent)\b',
            r'\b(real person|actual person)\b',
        ],
    }
```

### 2.2 Intent Detection Logic

```python
def _detect_intent(self, user_text: str) -> UserIntent:
    """Detect user intent from text using pattern matching"""
    user_text_lower = user_text.lower().strip()
    
    # Check intents in priority order (most specific first)
    intent_priority = [
        UserIntent.REQUEST_HUMAN,  # Highest - user wants human
        UserIntent.GOODBYE,        # User wants to end
        UserIntent.NO,             # Explicit rejection
        UserIntent.UNCERTAIN,      # Hesitation
        UserIntent.OBJECTION,      # Concerns
        UserIntent.GREETING,       # Greetings
        UserIntent.YES,            # Affirmative (checked last)
    ]
    
    for intent in intent_priority:
        patterns = self.intent_patterns.get(intent, [])
        for pattern in patterns:
            if re.search(pattern, user_text_lower, re.IGNORECASE):
                return intent
    
    return UserIntent.UNKNOWN
```

**Why Priority Order:**
- `REQUEST_HUMAN` checked first because "yes, transfer me to a person" should trigger transfer, not YES
- `NO` checked before `YES` because "no, I said yes earlier" should be NO
- Prevents false positives from broader patterns matching first

---

## 3. State Transition Logic

### 3.1 Transition Definition

```python
class StateTransition(BaseModel):
    """Defines a state transition"""
    from_state: ConversationState
    to_state: ConversationState
    trigger: UserIntent
    priority: int = 0  # Higher = checked first
```

### 3.2 Transition Map

```python
def _build_transition_map(self) -> List[StateTransition]:
    """Build state transition map"""
    transitions = [
        # GREETING transitions
        StateTransition(
            from_state=ConversationState.GREETING,
            to_state=ConversationState.QUALIFICATION,
            trigger=UserIntent.YES,
            priority=10
        ),
        StateTransition(
            from_state=ConversationState.GREETING,
            to_state=ConversationState.GOODBYE,
            trigger=UserIntent.NO,
            priority=10
        ),
        StateTransition(
            from_state=ConversationState.GREETING,
            to_state=ConversationState.OBJECTION_HANDLING,
            trigger=UserIntent.UNCERTAIN,
            priority=8
        ),
        # ... more transitions
    ]
    return sorted(transitions, key=lambda t: t.priority, reverse=True)
```

### 3.3 Transition Execution

```python
def _transition_state(self, current_state, intent, context) -> ConversationState:
    """Determine next state based on current state, intent, and context"""
    
    # Handle max objection limit
    if current_state == ConversationState.OBJECTION_HANDLING:
        if context.objection_count >= self.agent_config.flow.max_objection_attempts:
            if intent in [UserIntent.UNCERTAIN, UserIntent.OBJECTION]:
                return ConversationState.GOODBYE
    
    # Find matching transition
    for transition in self.state_transitions:
        if transition.from_state == current_state and transition.trigger == intent:
            return transition.to_state
    
    # No match found, stay in current state
    return current_state
```

### 3.4 Conversation Context

```python
class ConversationContext(BaseModel):
    """Context information for conversation state"""
    objection_count: int = 0        # Number of objections handled
    follow_up_count: int = 0        # Number of follow-up questions
    user_confirmed: bool = False    # User has confirmed action
    transfer_requested: bool = False # User requested transfer
    
    def increment_objection(self) -> int:
        self.objection_count += 1
        return self.objection_count
```

---

## 4. Prompt Template System

### 4.1 Template Structure

**File: `app/domain/services/prompt_manager.py`**

```python
class PromptTemplate(BaseModel):
    """Single prompt template"""
    name: str
    template: str          # Jinja2 template string
    variables: List[str]   # Required variables
    examples: List[Dict]   # Few-shot examples
    
    def render(self, **kwargs) -> str:
        """Render template with provided variables"""
        env = Environment(loader=BaseLoader())
        return env.from_string(self.template).render(**kwargs)
```

### 4.2 Base System Prompt

```python
self.templates["base_system"] = PromptTemplate(
    name="base_system",
    template="""### Role
You are {{ agent_name }}, a professional voice assistant for {{ company_name }}.

### Instructions
1. Your purpose: {{ goal_description }}
2. Respond in {{ max_sentences }} sentences or fewer
3. Use natural, conversational speech patterns
4. Be {{ tone }}
5. Never use filler words (um, uh, well, like)
6. Get straight to the point

### Strict Rules
{% for rule in do_not_say_rules %}
- {{ rule }}
{% endfor %}

### Response Format
- Direct and concise
- Natural speech (not robotic)
- No greetings like "Sure!" or "Of course!" at the start""",
    variables=["agent_name", "company_name", "goal_description", "tone", "max_sentences"]
)
```

### 4.3 State-Specific Templates

```python
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
    variables=["user_concern", "objection_count", "max_objections"]
)
```

### 4.4 System Prompt Rendering

```python
def render_system_prompt(self, agent_config, state, **kwargs) -> str:
    """Render complete system prompt for current state"""
    
    # Render base system prompt
    base_prompt = self.templates["base_system"].render(
        agent_name=agent_config.agent_name,
        company_name=agent_config.company_name,
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
        state_prompt = f"CURRENT STATE: {state.value.upper()}"
    
    return f"{base_prompt}\n\n{state_prompt}"
```

---

## 5. Agent Configuration Model

### 5.1 Agent Configuration

**File: `app/domain/models/agent_config.py`**

```python
class AgentConfig(BaseModel):
    """Complete agent configuration for a campaign"""
    
    # Identity
    goal: AgentGoal           # APPOINTMENT_CONFIRMATION, LEAD_QUALIFICATION, etc.
    business_type: str        # "dental clinic", "insurance agency", etc.
    agent_name: str           # "Sarah", "Alex", etc.
    company_name: str         # "Bright Smile Dental"
    
    # Behavior
    rules: ConversationRule   # Allowed/forbidden phrases
    flow: ConversationFlow    # State transition config
    
    # Style
    tone: str = "polite, professional, conversational"
    personality_traits: List[str] = ["friendly", "helpful", "concise"]
    
    # Constraints
    max_conversation_turns: int = 10
    response_max_sentences: int = 2
```

### 5.2 Conversation Rules

```python
class ConversationRule(BaseModel):
    """Rules and constraints for agent behavior"""
    allowed_phrases: List[str] = []     # Encouraged phrases
    forbidden_phrases: List[str] = []   # Phrases to avoid
    do_not_say_rules: List[str] = []    # Explicit rules
    max_follow_up_questions: int = 2
    require_confirmation: bool = True
```

### 5.3 Conversation Flow

```python
class ConversationFlow(BaseModel):
    """Defines conversation flow based on user responses"""
    on_yes: str = "closing"
    on_no: str = "goodbye"
    on_uncertain: str = "objection_handling"
    on_objection: str = "objection_handling"
    on_request_human: str = "transfer"
    max_objection_attempts: int = 2
```

### 5.4 Goal Descriptions

```python
class AgentGoal(str, Enum):
    APPOINTMENT_CONFIRMATION = "appointment_confirmation"
    LEAD_QUALIFICATION = "lead_qualification"
    CALLBACK_SCHEDULING = "callback_scheduling"
    INFORMATION_GATHERING = "information_gathering"
    SURVEY = "survey"
    REMINDER = "reminder"

def get_goal_description(self) -> str:
    goal_descriptions = {
        AgentGoal.APPOINTMENT_CONFIRMATION: "confirm an appointment",
        AgentGoal.LEAD_QUALIFICATION: "qualify a potential lead",
        AgentGoal.CALLBACK_SCHEDULING: "schedule a callback",
    }
    return goal_descriptions.get(self.goal, "assist you")
```

---

## 6. Test Results & Verification

### 6.1 Intent Detection Tests

```python
class TestIntentDetection:
    def test_detect_yes_intent(self, conversation_engine):
        test_cases = ["yes", "yeah sure", "sounds good", "absolutely"]
        for text in test_cases:
            intent = conversation_engine._detect_intent(text)
            assert intent == UserIntent.YES
    
    def test_detect_no_intent(self, conversation_engine):
        test_cases = ["no", "not interested", "I can't make it"]
        for text in test_cases:
            intent = conversation_engine._detect_intent(text)
            assert intent == UserIntent.NO
    
    def test_detect_request_human_intent(self, conversation_engine):
        test_cases = ["speak to a person", "transfer me", "real person"]
        for text in test_cases:
            intent = conversation_engine._detect_intent(text)
            assert intent == UserIntent.REQUEST_HUMAN
```

### 6.2 State Transition Tests

```python
class TestStateTransitions:
    def test_greeting_to_qualification_on_yes(self, conversation_engine):
        context = ConversationContext()
        new_state = conversation_engine._transition_state(
            ConversationState.GREETING,
            UserIntent.YES,
            context
        )
        assert new_state == ConversationState.QUALIFICATION
    
    def test_max_objections_forces_goodbye(self, conversation_engine):
        context = ConversationContext()
        context.objection_count = 2  # Max is 2
        
        new_state = conversation_engine._transition_state(
            ConversationState.OBJECTION_HANDLING,
            UserIntent.UNCERTAIN,
            context
        )
        assert new_state == ConversationState.GOODBYE
```

### 6.3 End Conversation Tests

```python
class TestShouldEndConversation:
    def test_should_end_on_goodbye_state(self, conversation_engine):
        context = ConversationContext()
        should_end, reason = conversation_engine.should_end_conversation(
            state=ConversationState.GOODBYE,
            turn_count=3,
            context=context
        )
        assert should_end is True
        assert "terminal_state" in reason
    
    def test_should_end_on_max_turns(self, conversation_engine):
        context = ConversationContext()
        should_end, reason = conversation_engine.should_end_conversation(
            state=ConversationState.QUALIFICATION,
            turn_count=10,
            context=context
        )
        assert should_end is True
        assert "max_turns" in reason
```

### 6.4 Test Execution Results

```
tests/unit/test_conversation_engine.py

TestIntentDetection
  test_detect_yes_intent PASSED
  test_detect_no_intent PASSED
  test_detect_uncertain_intent PASSED
  test_detect_objection_intent PASSED
  test_detect_request_human_intent PASSED
  test_detect_greeting_intent PASSED
  test_detect_unknown_intent PASSED

TestStateTransitions
  test_greeting_to_qualification_on_yes PASSED
  test_greeting_to_goodbye_on_no PASSED
  test_greeting_to_objection_on_uncertain PASSED
  test_qualification_to_closing_on_yes PASSED
  test_objection_handling_to_closing_on_yes PASSED
  test_any_state_to_transfer_on_request_human PASSED
  test_max_objections_forces_goodbye PASSED

TestConversationContext
  test_context_initialization PASSED
  test_increment_objection PASSED
  test_increment_follow_up PASSED
  test_reset_objection_tracking PASSED

TestHandleUserInput
  test_handle_yes_from_greeting PASSED
  test_handle_no_from_greeting PASSED
  test_handle_uncertain_increments_context PASSED
  test_handle_request_human_sets_transfer_flag PASSED

TestShouldEndConversation
  test_should_end_on_goodbye_state PASSED
  test_should_end_on_transfer_state PASSED
  test_should_end_on_max_turns PASSED
  test_should_end_on_user_confirmed PASSED
  test_should_not_end_in_active_conversation PASSED

==================== 26 passed in 0.58s ====================
```

---

## 7. Rationale Summary

### Key Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **State Machine** | Finite state machine | Predictable, testable, easy to debug |
| **Intent Detection** | Regex patterns | Fast, no ML inference latency, deterministic |
| **Priority Order** | Specific intents first | Prevents false positives from broad patterns |
| **Templates** | Jinja2 | Flexible, supports conditionals, industry standard |
| **Configuration** | Pydantic models | Type safety, validation, JSON serialization |

### Prompt Engineering Best Practices

| Practice | Implementation |
|----------|----------------|
| **Role Definition** | Clear persona in system prompt header |
| **Structured Instructions** | Numbered list of rules |
| **Few-Shot Examples** | Examples in state templates |
| **Output Constraints** | Max sentences, no filler words |
| **Negative Examples** | "do_not_say_rules" list |

### Files Created/Modified

| File | Purpose |
|------|---------|
| `app/domain/models/conversation_state.py` | State and intent enums |
| `app/domain/models/agent_config.py` | Agent configuration model |
| `app/domain/services/conversation_engine.py` | State machine and transitions |
| `app/domain/services/prompt_manager.py` | Prompt template system |
| `tests/unit/test_conversation_engine.py` | Comprehensive unit tests |

### State Machine Benefits

| Benefit | Explanation |
|---------|-------------|
| **Predictability** | Same input always produces same state transition |
| **Testability** | Each transition can be unit tested in isolation |
| **Debuggability** | State history can be logged for analysis |
| **Maintainability** | New states/transitions added without breaking existing ones |
| **Configurability** | Behavior changes via config, not code |

---

*Document Version: 1.0*  
*Last Updated: Day 5 of Development Sprint*
