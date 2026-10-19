import assert from "node:assert/strict";
import { test } from "node:test";
import {
    buildCampaignTestWsUrl,
    describeReplyTime,
    describeTestSession,
    parseTurnLatency,
} from "@/components/campaigns/test-agent-session";

test("the browser test is always an OUTBOUND test, whoever opens", () => {
    const agentFirst = describeTestSession({ firstSpeaker: "agent", agentName: "Sarah" });
    const calleeFirst = describeTestSession({ firstSpeaker: "user", agentName: "Sarah" });
    assert.equal(agentFirst.badge, "OUTBOUND TEST");
    assert.equal(calleeFirst.badge, "OUTBOUND TEST");
    assert.equal(agentFirst.openingMode, "agent_first");
    assert.equal(calleeFirst.openingMode, "callee_first");
    assert.equal(agentFirst.opening, "Sarah opens");
    assert.equal(calleeFirst.opening, "You say hello first, then Sarah introduces the call");
});

test("the opening description never implies the tester called in", () => {
    const d = describeTestSession({ firstSpeaker: "user", agentName: "Sarah" });
    assert.doesNotMatch(d.opening.toLowerCase(), /how can i help|thanks for calling|receptionist/);
});

test("barge-in is only requested when the tester says they wear headphones", () => {
    const off = buildCampaignTestWsUrl("wss://api.example", "camp-1", {
        firstSpeaker: "user",
        allowBargeIn: false,
    });
    const on = buildCampaignTestWsUrl("wss://api.example", "camp-1", {
        firstSpeaker: "agent",
        allowBargeIn: true,
    });
    assert.equal(off, "wss://api.example/ws/campaign-test/camp-1?first_speaker=user&allow_barge_in=false");
    assert.equal(on, "wss://api.example/ws/campaign-test/camp-1?first_speaker=agent&allow_barge_in=true");
});

test("the campaign id is URL-encoded", () => {
    const url = buildCampaignTestWsUrl("wss://api.example", "a b/c", {
        firstSpeaker: "agent",
        allowBargeIn: false,
    });
    assert.match(url, /campaign-test\/a%20b%2Fc\?/);
});

test("the reply time reads from the end of the tester's speech, with its parts", () => {
    const latency = parseTurnLatency({
        reply_ms: 950, end_of_turn_ms: 400, ai_first_word_ms: 250, voice_first_audio_ms: 150, other_ms: 150,
    });
    assert.ok(latency);
    assert.equal(
        describeReplyTime(latency),
        "Last reply: 0.95 s (waiting for you to finish 0.40 s · AI 0.25 s · voice 0.15 s)",
    );
});

test("unmeasured parts are left out and a malformed report is ignored", () => {
    const latency = parseTurnLatency({ reply_ms: 800, end_of_turn_ms: null, ai_first_word_ms: null });
    assert.ok(latency);
    assert.equal(describeReplyTime(latency), "Last reply: 0.80 s");
    assert.equal(parseTurnLatency(undefined), null);
    assert.equal(parseTurnLatency({ reply_ms: "fast" }), null);
    assert.equal(parseTurnLatency({ reply_ms: -1 }), null);
});
