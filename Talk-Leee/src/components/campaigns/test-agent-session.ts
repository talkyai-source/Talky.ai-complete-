/**
 * Pure helpers for the browser "Test agent" session.
 *
 * Two facts the UI used to blur into one radio button:
 *  - direction: a campaign test is always an OUTBOUND test (the backend pins
 *    `Direction.OUTBOUND`; the old server code derived INBOUND from
 *    "you speak first" and told the agent the tester had called in);
 *  - opening mode: who talks first. That is what the radio actually chooses.
 *
 * Barge-in is off by default in the browser because a laptop mic hears the
 * agent's own speech and interrupts it; the tester opts in when wearing
 * headphones. The frontend never sent this flag before, so the toggle the
 * backend offered was unreachable.
 */
export type FirstSpeaker = "agent" | "user";
export type OpeningMode = "agent_first" | "callee_first";

export interface TestSessionOptions {
    firstSpeaker: FirstSpeaker;
    allowBargeIn: boolean;
}

export function buildCampaignTestWsUrl(
    wsBase: string,
    campaignId: string,
    opts: TestSessionOptions,
): string {
    const params = new URLSearchParams({
        first_speaker: opts.firstSpeaker,
        allow_barge_in: opts.allowBargeIn ? "true" : "false",
    });
    return `${wsBase}/ws/campaign-test/${encodeURIComponent(campaignId)}?${params.toString()}`;
}

export interface TestSessionDescription {
    /** Always "OUTBOUND TEST" — shown so nobody reads a callee-first test as inbound. */
    badge: "OUTBOUND TEST";
    openingMode: OpeningMode;
    /** The exact opening the session will use, in plain words. */
    opening: string;
}

export function describeTestSession(input: {
    firstSpeaker: FirstSpeaker;
    agentName: string;
}): TestSessionDescription {
    const name = input.agentName.trim() || "The agent";
    if (input.firstSpeaker === "user") {
        return {
            badge: "OUTBOUND TEST",
            openingMode: "callee_first",
            opening: `You say hello first, then ${name} introduces the call`,
        };
    }
    return {
        badge: "OUTBOUND TEST",
        openingMode: "agent_first",
        opening: `${name} opens`,
    };
}
