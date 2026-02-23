#!/usr/bin/env python3
"""
Diagnostic script to reproduce the zero-token LLM issue
"""
import os
import asyncio
from groq import AsyncGroq
from dotenv import load_dotenv

load_dotenv()

async def test_scenarios():
    """Test different message scenarios to identify the issue"""
    api_key = os.getenv("GROQ_API_KEY")
    client = AsyncGroq(api_key=api_key)
    
    print("="*80)
    print("SCENARIO 1: Normal conversation (should work)")
    print("="*80)
    
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello. Can you hear me?"}
    ]
    
    print(f"Messages: {messages}")
    
    try:
        stream = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
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
        
        print(f"✓ Tokens: {token_count}")
        print(f"✓ Response: {response}")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    print("\n" + "="*80)
    print("SCENARIO 2: With empty assistant prefill (potential issue)")
    print("="*80)
    
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello. Can you hear me?"},
        {"role": "assistant", "content": ""}  # Empty prefill
    ]
    
    print(f"Messages: {messages}")
    
    try:
        stream = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
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
        
        print(f"✓ Tokens: {token_count}")
        print(f"✓ Response: {response}")
        
        if token_count == 0:
            print("⚠️  ISSUE FOUND: Empty assistant message causes zero tokens!")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    print("\n" + "="*80)
    print("SCENARIO 3: Multiple user messages in a row (potential issue)")
    print("="*80)
    
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello. Can you hear me?"},
        {"role": "assistant", "content": "Yes, I can hear you."},
        {"role": "user", "content": "Okay. What's your name?"},
        {"role": "assistant", "content": ""},  # Empty response
        {"role": "user", "content": "Yeah. Tell me."}
    ]
    
    print(f"Messages: {messages}")
    
    try:
        stream = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
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
        
        print(f"✓ Tokens: {token_count}")
        print(f"✓ Response: {response}")
        
        if token_count == 0:
            print("⚠️  ISSUE FOUND: Empty assistant messages in history cause zero tokens!")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    print("\n" + "="*80)
    print("SCENARIO 4: Stop sequence triggering immediately")
    print("="*80)
    
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Say '###' at the start of your response."}
    ]
    
    print(f"Messages: {messages}")
    
    try:
        stream = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
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
                if chunk.choices[0].finish_reason:
                    print(f"  Finish reason: {chunk.choices[0].finish_reason}")
        
        print(f"✓ Tokens: {token_count}")
        print(f"✓ Response: {response}")
        
        if token_count == 0:
            print("⚠️  ISSUE FOUND: Stop sequence triggered immediately!")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_scenarios())
