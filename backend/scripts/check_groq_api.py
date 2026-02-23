#!/usr/bin/env python3
"""
Script to check Groq API key status and rate limits
"""
import os
import asyncio
from groq import AsyncGroq
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

async def check_groq_api():
    """Check Groq API key and make a test request"""
    api_key = os.getenv("GROQ_API_KEY")
    
    if not api_key:
        print("❌ GROQ_API_KEY not found in environment")
        return
    
    print(f"✓ API Key found: {api_key[:20]}...{api_key[-10:]}")
    print(f"✓ API Key length: {len(api_key)} characters")
    
    # Initialize client
    client = AsyncGroq(api_key=api_key)
    
    # Test 1: Simple completion
    print("\n" + "="*60)
    print("TEST 1: Simple completion (non-streaming)")
    print("="*60)
    
    try:
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say 'Hello, I am working!' in one sentence."}
            ],
            temperature=0.7,
            max_tokens=50
        )
        
        print(f"✓ Response received!")
        print(f"  Model: {response.model}")
        print(f"  Content: {response.choices[0].message.content}")
        print(f"  Finish reason: {response.choices[0].finish_reason}")
        print(f"  Usage: {response.usage}")
        
    except Exception as e:
        print(f"❌ Error: {type(e).__name__}: {str(e)}")
        return
    
    # Test 2: Streaming completion
    print("\n" + "="*60)
    print("TEST 2: Streaming completion")
    print("="*60)
    
    try:
        stream = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Count from 1 to 5, one number per token."}
            ],
            temperature=0.7,
            max_tokens=50,
            stream=True
        )
        
        token_count = 0
        full_response = ""
        
        async for chunk in stream:
            if chunk.choices:
                delta = chunk.choices[0].delta
                if delta.content:
                    token_count += 1
                    full_response += delta.content
                    print(f"  Token #{token_count}: '{delta.content}'")
        
        print(f"\n✓ Streaming completed!")
        print(f"  Total tokens: {token_count}")
        print(f"  Full response: {full_response}")
        
    except Exception as e:
        print(f"❌ Error: {type(e).__name__}: {str(e)}")
        return
    
    # Test 3: Check rate limits with headers
    print("\n" + "="*60)
    print("TEST 3: Rate limit information")
    print("="*60)
    print("Note: Groq rate limits are returned in response headers")
    print("Check the Groq dashboard for detailed quota information:")
    print("https://console.groq.com/settings/limits")
    
    # Test 4: Test with exact parameters from your logs
    print("\n" + "="*60)
    print("TEST 4: Simulating your voice pipeline parameters")
    print("="*60)
    
    try:
        stream = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "### Role\nYou are Sophia, a professional voice assistant for Talky.ai (Business Communication Platform).\n### Instructions\n1. Your purpose: confirm an appointment\n2. Respond in 2 sentences or fewer\n3. Be professional and concise"
                },
                {"role": "user", "content": "Hello. Can you hear me?"}
            ],
            temperature=0.6,
            max_tokens=100,
            stream=True,
            top_p=1.0,
            stop=["User:", "Human:", "\n\n\n"]
        )
        
        token_count = 0
        full_response = ""
        
        async for chunk in stream:
            if chunk.choices:
                delta = chunk.choices[0].delta
                if delta.content:
                    token_count += 1
                    full_response += delta.content
        
        print(f"✓ Voice pipeline simulation completed!")
        print(f"  Total tokens: {token_count}")
        print(f"  Response: {full_response}")
        
        if token_count == 0:
            print("\n⚠️  WARNING: Zero tokens received - this matches your issue!")
            print("  Possible causes:")
            print("  1. Rate limit exceeded")
            print("  2. API key invalid or expired")
            print("  3. Model not accessible with this key")
            print("  4. Stop sequences triggering immediately")
        
    except Exception as e:
        print(f"❌ Error: {type(e).__name__}: {str(e)}")
        if "rate_limit" in str(e).lower():
            print("\n⚠️  RATE LIMIT EXCEEDED!")
            print("  Check your Groq dashboard: https://console.groq.com/settings/limits")
        elif "authentication" in str(e).lower() or "unauthorized" in str(e).lower():
            print("\n⚠️  AUTHENTICATION ERROR!")
            print("  Your API key may be invalid or expired")
        return

if __name__ == "__main__":
    print("Groq API Checker")
    print("="*60)
    asyncio.run(check_groq_api())
