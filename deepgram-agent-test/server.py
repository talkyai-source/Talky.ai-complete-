#!/usr/bin/env python3
"""
Deepgram Voice Agent API — test bridge server (port 8001).

Bridges the frontend Ask AI WebSocket to the Deepgram Voice Agent API.
100% isolated from the main backend. Nothing else imports or uses this.

To run:
    cd deepgram-agent-test
    pip install -r requirements.txt
    python server.py

To revert the frontend: swap the two commented lines in helix-hero.tsx (~line 662).
To delete everything: rm -rf deepgram-agent-test/ and un-comment the original line in helix-hero.tsx.
"""

import asyncio
import json
import os
import websockets
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
DEEPGRAM_AGENT_URL = "wss://agent.deepgram.com/v1/agent/converse"
OUTPUT_SAMPLE_RATE = 24000

app = FastAPI(title="Deepgram Voice Agent Test Bridge")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def build_settings() -> str:
    return json.dumps({
        "type": "Settings",
        "audio": {
            "input": {
                "encoding": "linear16",
                "sample_rate": 16000,   # frontend AudioContext is forced to 16 kHz
            },
            "output": {
                "encoding": "linear16",
                "sample_rate": OUTPUT_SAMPLE_RATE,
                "container": "none",    # raw PCM — no WAV header prefix
            },
        },
        "agent": {
            "language": "en",
            "listen": {
                "provider": {
                    "type": "deepgram",
                    "version": "v2",            # matches playground: listenVersion=v2
                    "model": "flux-general-en", # Flux model — same as main backend
                },
            },
            "think": {
                "provider": {
                    "type": "groq",
                    "model": "llama-3.3-70b-versatile",
                    "temperature": 0.7,
                },
                "endpoint": {
                    "url": "https://api.groq.com/openai/v1/chat/completions",
                    "headers": {
                        "authorization": f"Bearer {GROQ_API_KEY}",
                    },
                },
                "prompt": (
                    "You are Sophia, a friendly AI voice assistant for Talky.ai. "
                    "Keep responses short and conversational — 1 to 3 sentences max. "
                    "You help users explore AI-powered voice technology."
                ),
            },
            "speak": {
                "provider": {
                    "type": "deepgram",
                    "model": "aura-2-thalia-en",
                },
            },
            "greeting": "Hello! I'm Sophia. How can I help you today?",
        },
    })


async def bridge_session(frontend_ws: WebSocket):
    await frontend_ws.accept()
    print("[bridge] frontend connected")

    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

    try:
        async with websockets.connect(DEEPGRAM_AGENT_URL, extra_headers=headers) as dg_ws:
            print("[bridge] connected to Deepgram Voice Agent API")

            # Set once Deepgram confirms SettingsApplied — audio forwarding begins after this
            ready = asyncio.Event()
            # Tracks whether the agent is currently sending audio.
            # UserStartedSpeaking fires on ANY user speech — including after the agent
            # has already finished. We only send barge_in when the agent is mid-speech.
            agent_speaking = False

            async def dg_to_frontend():
                nonlocal agent_speaking
                audio_chunks_sent = 0
                try:
                    async for raw in dg_ws:
                        # Binary = PCM16 audio from TTS — forward directly to frontend
                        if isinstance(raw, bytes):
                            if ready.is_set():
                                await frontend_ws.send_bytes(raw)
                                audio_chunks_sent += 1
                                if audio_chunks_sent == 1:
                                    # Check first 4 bytes: WAV starts with "RIFF", raw PCM is audio data
                                    sig = raw[:4]
                                    fmt = "WAV" if sig == b"RIFF" else f"raw({sig.hex()})"
                                    print(f"[dg→frontend] FIRST audio chunk ({len(raw)} bytes) format={fmt}")
                            else:
                                print(f"[dg→frontend] audio chunk DROPPED (ready not set yet) {len(raw)} bytes")
                            continue

                        event = json.loads(raw)
                        etype = event.get("type", "")
                        # Log full event for anything unexpected
                        if etype not in ("Welcome", "SettingsApplied", "ConversationText", "History",
                                         "AgentStartedSpeaking", "AgentAudioDone", "UserStartedSpeaking",
                                         "AgentThinking", "FunctionCallRequest", "KeepAlive"):
                            print(f"[dg→frontend] UNKNOWN event: {raw[:200]}")
                        else:
                            print(f"[dg→frontend] {etype}")

                        if etype == "Welcome":
                            # Deepgram is ready — send our settings
                            await dg_ws.send(build_settings())

                        elif etype == "SettingsApplied":
                            # Tell frontend we're ready; frontend starts mic + enters speaking state for greeting
                            await frontend_ws.send_text(
                                json.dumps({"type": "ready", "sample_rate": OUTPUT_SAMPLE_RATE})
                            )
                            ready.set()

                        elif etype == "ConversationText":
                            role = event.get("role", "")
                            content = event.get("content", "")
                            if role == "user":
                                # User transcript → show processing state on frontend
                                await frontend_ws.send_text(json.dumps({
                                    "type": "transcript",
                                    "is_final": True,
                                    "text": content,
                                }))
                            elif role == "assistant":
                                # Agent text ready — unblock audio player BEFORE audio chunks arrive.
                                # ConversationText fires before/as audio streams, so this resets
                                # dropIncomingAudio=false in time for the first audio frame.
                                agent_speaking = True
                                await frontend_ws.send_text(json.dumps({"type": "llm_response"}))

                        elif etype == "AgentStartedSpeaking":
                            # Also fires in some flows — mark speaking and unblock audio
                            agent_speaking = True
                            await frontend_ws.send_text(json.dumps({"type": "llm_response"}))

                        elif etype == "UserStartedSpeaking":
                            # Only treat as barge-in if agent is currently mid-speech.
                            # Deepgram fires this event even when the agent is silent (user
                            # starts their turn normally). Sending barge_in in that case sets
                            # dropIncomingAudio=true on the frontend and kills the next response.
                            if agent_speaking:
                                print("[bridge] barge-in: agent was speaking")
                                await frontend_ws.send_text(json.dumps({"type": "barge_in"}))
                                agent_speaking = False

                        elif etype == "AgentAudioDone":
                            # Agent finished speaking — allow next UserStartedSpeaking to be
                            # treated as a normal turn, not a barge-in
                            print(f"[dg→frontend] AgentAudioDone — total audio chunks this turn: {audio_chunks_sent}")
                            audio_chunks_sent = 0
                            agent_speaking = False
                            await frontend_ws.send_text(json.dumps({"type": "tts_audio_complete"}))
                            await frontend_ws.send_text(json.dumps({"type": "turn_complete"}))

                except Exception as e:
                    print(f"[dg→frontend] error: {e}")
                finally:
                    try:
                        await frontend_ws.close()
                    except Exception:
                        pass

            async def keepalive():
                """Send KeepAlive to Deepgram every 8s to prevent idle timeout."""
                try:
                    await ready.wait()
                    while True:
                        await asyncio.sleep(8)
                        try:
                            await dg_ws.send(json.dumps({"type": "KeepAlive"}))
                        except Exception:
                            break
                except Exception:
                    pass

            async def frontend_to_dg():
                try:
                    # Wait until Deepgram is configured before forwarding mic audio
                    await ready.wait()
                    print("[bridge] mic audio forwarding started")

                    while True:
                        msg = await frontend_ws.receive()

                        if msg["type"] == "websocket.disconnect":
                            break

                        if "bytes" in msg and msg["bytes"]:
                            # Raw PCM16 mic audio → Deepgram
                            await dg_ws.send(msg["bytes"])

                        elif "text" in msg and msg["text"]:
                            data = json.loads(msg["text"])
                            mtype = data.get("type")

                            if mtype == "end_call":
                                print("[bridge] end_call received")
                                break

                            # playback_complete is ignored here —
                            # the Deepgram Agent API manages its own VAD and turn timing

                except Exception as e:
                    print(f"[frontend→dg] error: {e}")
                finally:
                    await dg_ws.close()

            await asyncio.gather(dg_to_frontend(), frontend_to_dg(), keepalive())

    except Exception as e:
        print(f"[bridge] session error: {e}")
        try:
            await frontend_ws.send_text(json.dumps({"type": "error", "message": str(e)}))
            await frontend_ws.close()
        except Exception:
            pass

    print("[bridge] session ended")


@app.websocket("/ws/ask-ai/{session_id}")
async def ws_endpoint(ws: WebSocket, session_id: str):
    print(f"[bridge] new session: {session_id}")
    await bridge_session(ws)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "deepgram_key_set": bool(DEEPGRAM_API_KEY),
        "groq_key_set": bool(GROQ_API_KEY),
    }


if __name__ == "__main__":
    if not DEEPGRAM_API_KEY:
        print("WARNING: DEEPGRAM_API_KEY not set")
    if not GROQ_API_KEY:
        print("WARNING: GROQ_API_KEY not set")
    print("Starting Deepgram Voice Agent test bridge on http://0.0.0.0:8001")
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
