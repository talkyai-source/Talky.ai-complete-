import assert from "node:assert/strict";
import { test } from "node:test";
import { voiceLatencyNote } from "@/lib/voice-latency";

test("ElevenLabs carries a latency note", () => {
    assert.match(voiceLatencyNote("elevenlabs") ?? "", /0\.5-1\.2 s/);
    assert.match(voiceLatencyNote(" ElevenLabs ") ?? "", /Cartesia or Deepgram/);
});

test("fast providers carry no note", () => {
    assert.equal(voiceLatencyNote("cartesia"), null);
    assert.equal(voiceLatencyNote("deepgram"), null);
    assert.equal(voiceLatencyNote(""), null);
});
