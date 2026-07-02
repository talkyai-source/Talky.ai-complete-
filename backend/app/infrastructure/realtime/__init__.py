"""OpenAI gpt-realtime-2 speech-to-speech bridge (Realtime pipeline mode).

Phase 1: a self-contained WebSocket session that collapses STT+LLM+TTS into
one OpenAI Realtime connection. Kept 100% separate from the cascaded pipeline
and its prompt machinery. See `openai_realtime.OpenAIRealtimeSession`.
"""
