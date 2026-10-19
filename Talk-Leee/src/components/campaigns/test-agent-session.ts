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

/**
 * The backend's per-turn reply time (`turn_complete.latency`): from the moment
 * the tester actually stopped talking to the agent's first audio, with its
 * parts. A part the server could not measure is null.
 */
export interface TurnLatency {
    reply_ms: number;
    end_of_turn_ms: number | null;
    ai_first_word_ms: number | null;
    voice_first_audio_ms: number | null;
}

function optionalMs(value: unknown): number | null {
    return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

export function parseTurnLatency(value: unknown): TurnLatency | null {
    if (!value || typeof value !== "object") return null;
    const v = value as Record<string, unknown>;
    const reply = optionalMs(v.reply_ms);
    if (reply === null) return null;
    return {
        reply_ms: reply,
        end_of_turn_ms: optionalMs(v.end_of_turn_ms),
        ai_first_word_ms: optionalMs(v.ai_first_word_ms),
        voice_first_audio_ms: optionalMs(v.voice_first_audio_ms),
    };
}

export function describeReplyTime(latency: TurnLatency): string {
    const secs = (ms: number) => `${(ms / 1000).toFixed(2)} s`;
    const parts = [
        latency.end_of_turn_ms !== null ? `waiting for you to finish ${secs(latency.end_of_turn_ms)}` : null,
        latency.ai_first_word_ms !== null ? `AI ${secs(latency.ai_first_word_ms)}` : null,
        latency.voice_first_audio_ms !== null ? `voice ${secs(latency.voice_first_audio_ms)}` : null,
    ].filter((p): p is string => p !== null);
    return `Last reply: ${secs(latency.reply_ms)}${parts.length ? ` (${parts.join(" · ")})` : ""}`;
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
