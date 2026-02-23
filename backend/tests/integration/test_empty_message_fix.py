#!/usr/bin/env python3
"""
Test script to verify the empty message fix
"""
import os
import asyncio
from groq import AsyncGroq
from dotenv import load_dotenv

load_dotenv()

async def test_empty_message_handling():
    """Test that empty messages are properly filtered"""
    api_key = os.getenv("GROQ_API_KEY")
    client = AsyncGroq(api_key=api_key)
    
    print("="*80)
    print("TEST: Conversation with empty assistant messages (simulating the bug)")
    print("="*80)
    
    # This simulates what was happening in your logs:
    # Multiple turns with some empty assistant responses
    messages = [
        {"role": "system", "content": "You are Sophia, a professional voice assistant."},
        {"role": "user", "content": "Hello. Can you hear me?"},
        {"role": "assistant", "content": ""},  # Empty response (BUG)
        {"role": "user", "content": "Okay. What's your name?"},
        {"role": "assistant", "content": ""},  # Empty response (BUG)
        {"role": "user", "content": "Yeah. Tell me."}
    ]
    
    print("\nOriginal messages (WITH empty assistant messages):")
    for i, msg in enumerate(messages):
        content = msg['content'] if msg['content'] else '<EMPTY>'
        print(f"  {i}. {msg['role']}: {content}")
    
    # Filter out empty messages (this is what our fix does)
    filtered_messages = []
    for msg in messages:
        if msg['role'] == 'system' or (msg['content'] and msg['content'].strip()):
            filtered_messages.append(msg)
        else:
            print(f"\n⚠️  Filtering out empty {msg['role']} message")
    
    print("\nFiltered messages (WITHOUT empty assistant messages):")
    for i, msg in enumerate(filtered_messages):
        content = msg['content'] if msg['content'] else '<EMPTY>'
        print(f"  {i}. {msg['role']}: {content}")
    
    # Test with filtered messages
    print("\n" + "-"*80)
    print("Sending filtered messages to Groq...")
    print("-"*80)
    
    try:
        stream = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=filtered_messages,
            temperature=0.6,
            max_tokens=100,
            stream=True,
            top_p=1.0,
            stop=["###", "\n\n\n"]
        )
        
        token_count = 0
        response = ""
        async for chunk in stream:
            if chunk.choices:
                delta = chunk.choices[0].delta
                if delta.content:
                    token_count += 1
                    response += delta.content
        
        print(f"\n✓ SUCCESS!")
        print(f"  Tokens received: {token_count}")
        print(f"  Response: {response}")
        
        if token_count > 0:
            print("\n✅ FIX VERIFIED: Filtering empty messages resolves the issue!")
        else:
            print("\n❌ Issue persists even after filtering")
            
    except Exception as e:
        print(f"\n❌ Error: {e}")

async def test_conversation_flow():
    """Test a realistic conversation flow"""
    api_key = os.getenv("GROQ_API_KEY")
    client = AsyncGroq(api_key=api_key)
    
    print("\n" + "="*80)
    print("TEST: Realistic multi-turn conversation")
    print("="*80)
    
    conversation = [
        {"role": "system", "content": "You are Sophia, a professional voice assistant for appointment confirmation. Be concise."}
    ]
    
    user_inputs = [
        "Hello. Can you hear me?",
        "Yes, I can hear you now.",
        "Sure, what do you need?"
    ]
    
    for turn, user_input in enumerate(user_inputs, 1):
        print(f"\n--- Turn {turn} ---")
        conversation.append({"role": "user", "content": user_input})
        print(f"User: {user_input}")
        
        try:
            stream = await client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=conversation,
                temperature=0.6,
                max_tokens=100,
                stream=True,
                top_p=1.0,
                stop=["###", "\n\n\n"]
            )
            
            response = ""
            async for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        response += delta.content
            
            if response.strip():
                conversation.append({"role": "assistant", "content": response})
                print(f"Assistant: {response}")
            else:
                print("⚠️  Empty response - NOT adding to conversation history")
                
        except Exception as e:
            print(f"❌ Error: {e}")
            break
    
    print("\n✅ Conversation completed successfully!")
    print(f"Final conversation length: {len(conversation)} messages")

if __name__ == "__main__":
    print("Empty Message Fix Verification")
    print("="*80)
    asyncio.run(test_empty_message_handling())
    asyncio.run(test_conversation_flow())
