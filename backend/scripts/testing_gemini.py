"""
Test the GeminiLLMProvider against the live Gemini API.

First-time setup probe. Requires:
  - pip install google-genai
  - GEMINI_API_KEY set in .env or exported in the shell

Usage:
    python backend/scripts/testing_gemini.py

Prints the first-token latency, total latency, token count, and full response
text. Compare numbers against the Groq probe for an apples-to-apples feel.
"""
import os
import sys
import asyncio
import time

sys.path.insert(0, ".")

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("❌ GEMINI_API_KEY not set in .env!")
    sys.exit(1)

print(f"🔑 API Key: {api_key[:8]}...{api_key[-4:]}")

from app.domain.models.conversation import Message, MessageRole  # noqa: E402
from app.infrastructure.llm.gemini import GeminiLLMProvider  # noqa: E402


async def main() -> None:
    print("=" * 50)
    print("🧠 Gemini 2.5 Flash Probe")
    print("=" * 50)

    provider = GeminiLLMProvider()
    await provider.initialize({
        "api_key": api_key,
        "model": "gemini-2.5-flash",
        "temperature": 0.6,
        "max_tokens": 150,
    })
    print("✅ Provider initialized")

    messages = [
        Message(
            role=MessageRole.USER,
            content="In one sentence, what is the capital of France?",
        )
    ]
    system_prompt = "You are a helpful voice agent. Reply in one short sentence."

    print("\n→ Streaming response:\n")
    t0 = time.monotonic()
    first_token_at = None
    tokens: list[str] = []

    async for token in provider.stream_chat(
        messages=messages,
        system_prompt=system_prompt,
        temperature=0.6,
        max_tokens=150,
    ):
        if first_token_at is None:
            first_token_at = time.monotonic()
        tokens.append(token)
        sys.stdout.write(token)
        sys.stdout.flush()

    total_ms = (time.monotonic() - t0) * 1000.0
    ttft_ms = ((first_token_at or t0) - t0) * 1000.0

    print("\n")
    print("=" * 50)
    print(f"⏱  TTFT: {ttft_ms:.0f} ms")
    print(f"⏱  Total: {total_ms:.0f} ms")
    print(f"🔢 Chunks: {len(tokens)}")
    print(f"📝 Response length: {sum(len(t) for t in tokens)} chars")
    print("=" * 50)

    await provider.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
