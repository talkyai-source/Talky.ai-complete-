"""
Test Groq LLM Integration with Prompt Templates
Validates that prompts produce fast, direct responses without unnecessary thinking
"""
import asyncio
import os
import time
from dotenv import load_dotenv

from app.domain.services.conversation_engine import ConversationEngine
from app.domain.models.conversation_state import ConversationState, ConversationContext
from app.domain.models.agent_config import (
    AgentConfig,
    AgentGoal,
    ConversationRule,
    ConversationFlow
)
from app.domain.models.conversation import Message, MessageRole
from app.infrastructure.llm.groq import GroqLLMProvider

load_dotenv()


async def test_prompt_with_groq():
    """Test that prompts produce fast, direct responses"""
    
    print("\n" + "="*70)
    print("  TESTING PROMPT TEMPLATES WITH GROQ LLM")
    print("  Validating: Speed, Brevity, Directness")
    print("="*70 + "\n")
    
    # Setup agent configuration
    agent_config = AgentConfig(
        goal=AgentGoal.APPOINTMENT_CONFIRMATION,
        business_type="dental clinic",
        agent_name="Sarah",
        company_name="Bright Smile Dental",
        rules=ConversationRule(
            do_not_say_rules=[
                "Never provide medical advice",
                "Never discuss pricing or discounts",
                "Never use filler words like 'um', 'uh', 'well'",
                "Never think out loud or explain your reasoning"
            ],
            max_follow_up_questions=2
        ),
        flow=ConversationFlow(
            on_yes="closing",
            on_no="goodbye",
            on_uncertain="objection_handling",
            max_objection_attempts=2
        ),
        tone="warm, professional, direct",
        max_conversation_turns=10,
        response_max_sentences=2  # Enforce brevity
    )
    
    # Initialize components
    conversation_engine = ConversationEngine(agent_config)
    
    # Initialize LLM
    llm = GroqLLMProvider()
    try:
        await llm.initialize({
            "api_key": os.getenv("GROQ_API_KEY"),
            "model": "llama-3.1-8b-instant",
            "temperature": 0.7,
            "max_tokens": 100
        })
    except Exception as e:
        print(f"❌ Failed to initialize Groq: {e}")
        print("Please check your GROQ_API_KEY in .env file")
        return []
    
    # Test scenarios
    test_scenarios = [
        {
            "name": "Greeting State",
            "state": ConversationState.GREETING,
            "user_input": "Hello?",
            "context": {"greeting_context": "I'm calling to confirm your dental appointment tomorrow at 2 PM."}
        },
        {
            "name": "Qualification State - User Says Yes",
            "state": ConversationState.QUALIFICATION,
            "user_input": "Yes, I'm available",
            "context": {"qualification_instruction": "Confirm the appointment time"}
        },
        {
            "name": "Objection Handling - User Uncertain",
            "state": ConversationState.OBJECTION_HANDLING,
            "user_input": "I'm not sure if I can make it",
            "context": {
                "user_concern": "uncertain about timing",
                "objection_count": 1,
                "max_objections": 2
            }
        },
        {
            "name": "Closing State",
            "state": ConversationState.CLOSING,
            "user_input": "Yes, that works for me",
            "context": {"confirmation_details": "tomorrow at 2 PM"}
        }
    ]
    
    results = []
    
    for scenario in test_scenarios:
        print(f"\n{'─'*70}")
        print(f"SCENARIO: {scenario['name']}")
        print(f"{'─'*70}")
        print(f"State: {scenario['state'].value}")
        print(f"User: \"{scenario['user_input']}\"")
        print()
        
        # Self-contained system prompt (was PromptManager.render_system_prompt,
        # now removed — the live path uses compose_prompt/build_turn_prompt).
        system_prompt = (
            f"You are {getattr(agent_config, 'agent_name', 'Assistant')}, a voice "
            f"assistant for {getattr(agent_config, 'company_name', 'the company')}. "
            f"Tone: {getattr(agent_config, 'tone', 'warm, professional')}. "
            f"Keep replies to {getattr(agent_config, 'response_max_sentences', 2)} sentences. "
            f"Current state: {scenario['state'].value}."
        )
        
        # Create conversation history
        messages = [Message(role=MessageRole.USER, content=scenario['user_input'])]
        
        # Measure response time
        start_time = time.time()
        
        # Get LLM response
        response_text = ""
        token_count = 0
        first_token_time = None
        
        print("Agent: ", end="", flush=True)
        
        async for token in llm.stream_chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=0.7,
            max_tokens=100
        ):
            if first_token_time is None:
                first_token_time = time.time()
            response_text += token
            token_count += 1
            print(token, end="", flush=True)
        
        end_time = time.time()
        
        print("\n")
        
        # Calculate metrics
        total_time = (end_time - start_time) * 1000  # ms
        first_token_latency = (first_token_time - start_time) * 1000 if first_token_time else 0  # ms
        sentence_count = response_text.count('.') + response_text.count('!') + response_text.count('?')
        word_count = len(response_text.split())
        
        # Validate response quality
        issues = []
        
        # Check for thinking/reasoning phrases
        thinking_phrases = [
            "let me think", "i think", "well,", "um,", "uh,", 
            "you see,", "basically,", "actually,", "to be honest",
            "in my opinion", "i believe", "i would say"
        ]
        for phrase in thinking_phrases:
            if phrase in response_text.lower():
                issues.append(f"Contains thinking phrase: '{phrase}'")
        
        # Check brevity (should be <= max_sentences)
        if sentence_count > agent_config.response_max_sentences:
            issues.append(f"Too long: {sentence_count} sentences (max: {agent_config.response_max_sentences})")
        
        # Check for empty or too short response
        if word_count < 5:
            issues.append(f"Too short: {word_count} words")
        
        # Check for forbidden content
        for rule in agent_config.rules.do_not_say_rules:
            # Simple check for medical/pricing keywords
            if "medical" in rule.lower() and any(word in response_text.lower() for word in ["diagnose", "treatment", "medication"]):
                issues.append(f"Violated rule: {rule}")
            if "pricing" in rule.lower() and any(word in response_text.lower() for word in ["cost", "price", "discount", "$"]):
                issues.append(f"Violated rule: {rule}")
        
        # Print metrics
        print(f"📊 METRICS:")
        print(f"   ⏱️  Total time: {total_time:.0f}ms")
        print(f"   ⚡ First token: {first_token_latency:.0f}ms")
        print(f"   📝 Sentences: {sentence_count}")
        print(f"   📏 Words: {word_count}")
        print(f"   🎯 Tokens: {token_count}")
        
        # Print validation
        if issues:
            print(f"\n⚠️  ISSUES FOUND:")
            for issue in issues:
                print(f"   ❌ {issue}")
            status = "FAILED"
        else:
            print(f"\n✅ VALIDATION: PASSED")
            status = "PASSED"
        
        results.append({
            "scenario": scenario['name'],
            "status": status,
            "total_time_ms": total_time,
            "first_token_ms": first_token_latency,
            "sentences": sentence_count,
            "words": word_count,
            "issues": issues
        })
    
    # Summary
    print(f"\n{'='*70}")
    print("  SUMMARY")
    print(f"{'='*70}\n")
    
    passed = sum(1 for r in results if r['status'] == 'PASSED')
    failed = sum(1 for r in results if r['status'] == 'FAILED')
    avg_latency = sum(r['first_token_ms'] for r in results) / len(results)
    avg_total_time = sum(r['total_time_ms'] for r in results) / len(results)
    
    print(f"Total Scenarios: {len(results)}")
    print(f"✅ Passed: {passed}")
    print(f"❌ Failed: {failed}")
    print(f"⚡ Avg First Token Latency: {avg_latency:.0f}ms")
    print(f"⏱️  Avg Total Time: {avg_total_time:.0f}ms")
    
    print(f"\n{'='*70}\n")
    
    # Cleanup
    await llm.cleanup()
    
    return results


async def test_different_models():
    """Test with different Groq models to compare performance"""
    
    print("\n" + "="*70)
    print("  TESTING DIFFERENT GROQ MODELS")
    print("="*70 + "\n")
    
    # Current production models from Groq (Dec 2025)
    # See: https://console.groq.com/docs/models
    models = [
        "llama-3.1-8b-instant",      # 560 t/s - Fastest, ideal for real-time voice
        "llama-3.3-70b-versatile",   # 280 t/s - Larger, more capable
        "meta-llama/llama-4-scout-17b-16e-instruct",  # 750 t/s - Preview, very fast
        "qwen/qwen3-32b",            # 400 t/s - Good balance of speed and quality
    ]
    
    agent_config = AgentConfig(
        goal=AgentGoal.APPOINTMENT_CONFIRMATION,
        business_type="dental clinic",
        agent_name="Sarah",
        company_name="Bright Smile Dental",
        rules=ConversationRule(
            do_not_say_rules=["Never think out loud", "Never use filler words"]
        ),
        tone="direct, professional",
        response_max_sentences=2
    )
    
    system_prompt = (
        f"You are {getattr(agent_config, 'agent_name', 'Assistant')}, a voice "
        f"assistant for {getattr(agent_config, 'company_name', 'the company')}. "
        f"Tone: {getattr(agent_config, 'tone', 'warm, professional')}. "
        f"Keep replies to {getattr(agent_config, 'response_max_sentences', 2)} sentences. "
        f"You are confirming an appointment."
    )
    
    messages = [Message(role=MessageRole.USER, content="Hello?")]
    
    for model in models:
        print(f"\n📦 Testing Model: {model}")
        print(f"{'─'*70}")
        
        llm = GroqLLMProvider()
        await llm.initialize({
            "api_key": os.getenv("GROQ_API_KEY"),
            "model": model,
            "temperature": 0.7,
            "max_tokens": 100
        })
        
        start_time = time.time()
        first_token_time = None
        response = ""
        
        print("Response: ", end="", flush=True)
        
        async for token in llm.stream_chat(messages=messages, system_prompt=system_prompt):
            if first_token_time is None:
                first_token_time = time.time()
            response += token
            print(token, end="", flush=True)
        
        end_time = time.time()
        
        print("\n")
        print(f"⚡ First token: {(first_token_time - start_time) * 1000:.0f}ms")
        print(f"⏱️  Total time: {(end_time - start_time) * 1000:.0f}ms")
        print(f"📏 Words: {len(response.split())}")
        
        await llm.cleanup()
    
    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    print("\n🚀 Starting Groq LLM Integration Tests...\n")
    
    # Run main test
    asyncio.run(test_prompt_with_groq())
    
    # Run model comparison
    print("\n" + "="*70)
    input("Press Enter to test different models (or Ctrl+C to exit)...")
    asyncio.run(test_different_models())
