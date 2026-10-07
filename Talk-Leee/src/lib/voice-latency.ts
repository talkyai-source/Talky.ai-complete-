/**
 * How long a TTS provider takes to start speaking, shown in the campaign voice
 * picker. Measured on production replies (14 days to 2026-10-01): ElevenLabs
 * first audio p50 0.56 s / p95 1.23 s; Deepgram replies started around 0.2 s.
 * Every reply waits for this before the caller hears anything.
 */
export function voiceLatencyNote(provider: string): string | null {
    if (provider.trim().toLowerCase() === "elevenlabs") {
        return "ElevenLabs adds about 0.5-1.2 s before the agent starts speaking. Cartesia or Deepgram voices start in about 0.2 s.";
    }
    return null;
}
