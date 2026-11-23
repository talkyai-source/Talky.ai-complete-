import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { dashboardApi } from "@/lib/dashboard-api";
import { leadDetailsApi } from "@/lib/lead-details-api";
import { OpeningBoxText, OpeningNotes, parseOutboundFirstWords } from "./opening-first-words";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const router = { push() {}, replace() {}, back() {}, refresh() {}, prefetch: async () => {} };
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useRouter: () => router, useSearchParams: () => new URLSearchParams(window.location.search) };
    return originalLoad.call(this, id, ...args);
};
let CampaignWizard: typeof import("./campaign-wizard").CampaignWizard;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    CampaignWizard = require("./campaign-wizard").CampaignWizard;
} finally {
    loader._load = originalLoad;
}

const GREETING = "Hi, this is Grace, an AI assistant from Acme. Who am I speaking with?";
const NOTICE = "This call is recorded for quality.";
const POLICY = "The recording notice is said on calls where your recording policy requires one. You set it in Settings, under Recording policy.";
const NAME_LINE = 'When the contact has a first name on file, the question is "Am I speaking with [first name]?".';
const IN_OPENING = "\"on a recorded line\" is this account's recording notice.";

/** The box and its notes exactly as the wizard places them. */
function Box({ words, outbound = true }: { words?: unknown; outbound?: boolean }) {
    return (
        <div>
            <div data-testid="box"><OpeningBoxText greeting={GREETING} firstWords={words} outbound={outbound} /></div>
            <div data-testid="notes"><OpeningNotes firstWords={words} outbound={outbound} /></div>
        </div>
    );
}
const box = () => screen.getByTestId("box").textContent;
const notes = () => screen.getByTestId("notes").textContent;

afterEach(() => cleanup());

test("field absent: the box shows exactly the greeting and no new lines", () => {
    render(<Box />);
    assert.equal(box(), GREETING);
    assert.equal(notes(), "");
});

test("field null: the box shows exactly the greeting and no new lines", () => {
    render(<Box words={null} />);
    assert.equal(box(), GREETING);
    assert.equal(notes(), "");
});

test("notice first: both lines in order, then the policy line and the first-name line", () => {
    render(<Box words={{ notice_first: NOTICE, opening: "Hi, this is Grace from Acme.", notice_in_opening: false }} />);
    assert.equal(box(), `First, your recording notice, on its own: "${NOTICE}"Then the opening: "Hi, this is Grace from Acme."`);
    assert.equal(notes(), `${POLICY}${NAME_LINE}`);
});

test("notice inside the opening: the opening with the recorded-line words, its explanation and the policy line", () => {
    const opening = "Hi, this is Grace from Acme, on a recorded line. Who am I speaking with?";
    render(<Box words={{ notice_first: null, opening, notice_in_opening: true }} />);
    assert.equal(box(), opening);
    assert.equal(notes(), `${IN_OPENING} ${POLICY}${NAME_LINE}`);
});

test("no notice: the opening and only the first-name line", () => {
    render(<Box words={{ notice_first: null, opening: "Hi, this is Grace from Acme.", notice_in_opening: false }} />);
    assert.equal(box(), "Hi, this is Grace from Acme.");
    assert.equal(notes(), NAME_LINE);
});

test("an incoming campaign never shows the new lines even when the field is sent", () => {
    render(<Box outbound={false} words={{ notice_first: NOTICE, opening: "x", notice_in_opening: true }} />);
    assert.equal(box(), GREETING);
    assert.equal(notes(), "");
});

test("a malformed field is treated as absent", () => {
    const bad = [
        { notice_first: NOTICE, notice_in_opening: false },
        { opening: "x" },
        { opening: 3, notice_in_opening: false },
        { opening: "x", notice_first: 5, notice_in_opening: false },
        "text",
        [],
    ];
    for (const value of bad) assert.equal(parseOutboundFirstWords(value), null);
    render(<Box words={{ notice_first: NOTICE, notice_in_opening: false }} />);
    assert.equal(box(), GREETING);
    assert.equal(notes(), "");
});

const BASE = { notice_first: NOTICE, opening: "Hi, this is Grace from Acme.", notice_in_opening: false };
const PLACE = "Your recording policy asks for the notice only on calls to certain places. On other calls the agent says the opening without it. You set this in Settings, under Recording policy.";
const ALONE = "This call may be recorded for quality and training purposes.";
const WAITS_ALONE = `This campaign waits for the person to speak first. If their first words are not a greeting, the agent says "${ALONE}" and then answers them, without this opening.`;
const WAITS_PLAIN = "This campaign waits for the person to speak first. If their first words are not a greeting, the agent answers them without this opening.";

test("waits for the person with its own sentence: the quoted line sits between the policy line and the first-name line", () => {
    render(<Box words={{ ...BASE, waits_for_person: true, notice_alone: ALONE }} />);
    assert.equal(notes(), `${POLICY}${WAITS_ALONE}${NAME_LINE}`);
});

test("waits for the person with no notice owed: the second form of the line", () => {
    render(<Box words={{ ...BASE, waits_for_person: true, notice_alone: null }} />);
    assert.equal(notes(), `${POLICY}${WAITS_PLAIN}${NAME_LINE}`);
});

test("waits_for_person false or absent: neither waiting line", () => {
    const { unmount } = render(<Box words={{ ...BASE, waits_for_person: false, notice_alone: ALONE }} />);
    assert.equal(notes(), `${POLICY}${NAME_LINE}`);
    unmount();
    render(<Box words={{ ...BASE, notice_alone: ALONE }} />);
    assert.equal(notes(), `${POLICY}${NAME_LINE}`);
});

test("depends on place: the 'certain places' sentence replaces the 'requires one' sentence; false or absent is the reverse", () => {
    const { unmount } = render(<Box words={{ ...BASE, notice_depends_on_place: true }} />);
    assert.equal(notes(), `${PLACE}${NAME_LINE}`);
    unmount();
    cleanup();
    render(<Box words={{ ...BASE, notice_depends_on_place: false }} />);
    assert.equal(notes(), `${POLICY}${NAME_LINE}`);
    cleanup();
    render(<Box words={BASE} />);
    assert.equal(notes(), `${POLICY}${NAME_LINE}`);
});

test("depends on place with the notice inside the opening: the recorded-line sentence stays in front", () => {
    render(<Box words={{ notice_first: null, opening: "Hi, on a recorded line.", notice_in_opening: true, notice_depends_on_place: true }} />);
    assert.equal(notes(), `${IN_OPENING} ${PLACE}${NAME_LINE}`);
});

test("the old three-field object renders as before", () => {
    render(<Box words={BASE} />);
    assert.equal(box(), `First, your recording notice, on its own: "${NOTICE}"Then the opening: "Hi, this is Grace from Acme."`);
    assert.equal(notes(), `${POLICY}${NAME_LINE}`);
});

test("a non-boolean waits_for_person is read as false and does not hide the rest", () => {
    render(<Box words={{ ...BASE, waits_for_person: "yes", notice_alone: 7, notice_depends_on_place: "true" }} />);
    assert.equal(box(), `First, your recording notice, on its own: "${NOTICE}"Then the opening: "Hi, this is Grace from Acme."`);
    assert.equal(notes(), `${POLICY}${NAME_LINE}`);
});

// ---- the wizard's review step ----
const REALTIME = { pipeline_mode: "realtime" as const, realtime_model: "gpt-realtime", realtime_voice: "alloy" };
const KEY = "opening-first-words";
const original = {
    getProviders: aiOptionsApi.getProviders, getConfig: aiOptionsApi.getConfig, getVoices: aiOptionsApi.getVoices,
    fieldSpec: leadDetailsApi.fieldSpec, campaignFields: leadDetailsApi.campaignFields,
    previewCampaignPrompt: dashboardApi.previewCampaignPrompt,
};
let answer: Record<string, unknown> = {};
beforeEach(() => {
    window.sessionStorage.clear();
    window.history.replaceState(null, "", "/campaigns/new");
    aiOptionsApi.getProviders = async () => ({
        realtime: { available: true, model: "gpt-realtime", voices: [{ id: "alloy", name: "Alloy" }] },
    }) as unknown as Awaited<ReturnType<typeof aiOptionsApi.getProviders>>;
    aiOptionsApi.getConfig = async () => ({ ...REALTIME, tts_provider: "cartesia" }) as unknown as Awaited<ReturnType<typeof aiOptionsApi.getConfig>>;
    aiOptionsApi.getVoices = async () => ({ voices: [] });
    leadDetailsApi.fieldSpec = async () => ({ fields: [], csv_template_headers: [] });
    leadDetailsApi.campaignFields = async () => ({ fields: [] });
    dashboardApi.previewCampaignPrompt = (async () => ({
        system_prompt: "prompt", greeting: GREETING, prompt_chars: 6, layers: [], ...answer,
    })) as unknown as typeof dashboardApi.previewCampaignPrompt;
});
afterEach(() => {
    Object.assign(aiOptionsApi, { getProviders: original.getProviders, getConfig: original.getConfig, getVoices: original.getVoices });
    Object.assign(leadDetailsApi, { fieldSpec: original.fieldSpec, campaignFields: original.campaignFields });
    dashboardApi.previewCampaignPrompt = original.previewCampaignPrompt;
    window.sessionStorage.clear();
});

async function toReview() {
    window.sessionStorage.setItem(KEY, JSON.stringify({
        step: 0, voiceSelection: REALTIME, name: "Spring push", companyName: "Acme", agentNamesRaw: "Alex", personaType: "lead_gen",
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(<QueryClientProvider client={client}><CampaignWizard draftStorageKey={KEY} /></QueryClientProvider>);
    const next = await screen.findByRole("button", { name: /Next: Knowledge/ }, { timeout: 15_000 }) as HTMLButtonElement;
    await waitFor(() => assert.equal(next.disabled, false), { timeout: 15_000 });
    await act(async () => { fireEvent.click(next); });
    const review = await screen.findByRole("button", { name: /Next: Review/ }, { timeout: 15_000 });
    await act(async () => { fireEvent.click(review); });
}

test("wizard review, field absent: the greeting alone, none of the new lines", async () => {
    answer = {};
    await toReview();
    await screen.findByText(GREETING, undefined, { timeout: 15_000 });
    assert.equal(screen.queryByText(/Am I speaking with \[first name\]/), null);
});

test("wizard review, notice first: both lines, the policy line and the first-name line", async () => {
    answer = { outbound_first_words: { notice_first: NOTICE, opening: "Hi, this is Grace from Acme.", notice_in_opening: false } };
    await toReview();
    await screen.findByText(`First, your recording notice, on its own: "${NOTICE}"`, undefined, { timeout: 15_000 });
    screen.getByText('Then the opening: "Hi, this is Grace from Acme."');
    screen.getByText(POLICY);
    screen.getByText(NAME_LINE);
    assert.equal(screen.queryByText(GREETING), null);
});
