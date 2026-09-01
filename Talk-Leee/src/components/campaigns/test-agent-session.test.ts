import assert from "node:assert/strict";
import { test } from "node:test";
import {
    buildCampaignTestWsUrl,
    describeTestSession,
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
