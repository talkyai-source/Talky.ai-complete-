import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, test } from "node:test";
import React, { useSyncExternalStore } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { dashboardApi } from "@/lib/dashboard-api";
import { leadDetailsApi } from "@/lib/lead-details-api";

// QA F1-22 (QA-UI-042): the wizard's three steps were component state on one
// address, so browser Back from step 3 left the wizard. Each step now has its
// own history entry (?step=), Back/Forward move between steps, and the review
// step loads its preview however it is reached.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const router = { push() {}, replace() {}, back() {}, refresh() {}, prefetch: async () => {} };
loader._load = function (id, ...args) {
    // Like Next's router: the search params follow the address, popstate included.
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

const SLOW = 15_000;
const KEY = "wizard-steps";
const REALTIME = { pipeline_mode: "realtime" as const, realtime_model: "gpt-realtime", realtime_voice: "alloy" };
const original = {
    getProviders: aiOptionsApi.getProviders, getConfig: aiOptionsApi.getConfig, getVoices: aiOptionsApi.getVoices,
    fieldSpec: leadDetailsApi.fieldSpec, campaignFields: leadDetailsApi.campaignFields,
    previewCampaignPrompt: dashboardApi.previewCampaignPrompt,
};
const clients: QueryClient[] = [];
let previews = 0;
// Next's router re-renders on its own pushState/replaceState as well as on
// popstate; the harness does the same.
const nativePushState = window.history.pushState;
const nativeReplaceState = window.history.replaceState;
const NAVIGATED = "test:navigated";

beforeEach(() => {
    previews = 0;
    window.sessionStorage.clear();
    window.history.pushState = function (...args: Parameters<History["pushState"]>) {
        nativePushState.apply(window.history, args);
        window.dispatchEvent(new window.Event(NAVIGATED));
    };
    window.history.replaceState = function (...args: Parameters<History["replaceState"]>) {
        nativeReplaceState.apply(window.history, args);
        window.dispatchEvent(new window.Event(NAVIGATED));
    };
    window.history.replaceState(null, "", "/campaigns/new");
    aiOptionsApi.getProviders = async () => ({
        realtime: { available: true, model: "gpt-realtime", voices: [{ id: "alloy", name: "Alloy" }] },
    }) as unknown as Awaited<ReturnType<typeof aiOptionsApi.getProviders>>;
    aiOptionsApi.getConfig = async () => ({ ...REALTIME, tts_provider: "cartesia" }) as unknown as Awaited<ReturnType<typeof aiOptionsApi.getConfig>>;
    aiOptionsApi.getVoices = async () => ({ voices: [] });
    leadDetailsApi.fieldSpec = async () => ({ fields: [], csv_template_headers: [] });
    leadDetailsApi.campaignFields = async () => ({ fields: [] });
    dashboardApi.previewCampaignPrompt = (async () => {
        previews++;
        return { system_prompt: "prompt", greeting: "Hello from the preview", prompt_chars: 6, layers: [] };
    }) as unknown as typeof dashboardApi.previewCampaignPrompt;
});

afterEach(() => {
    cleanup();
    window.history.pushState = nativePushState;
    window.history.replaceState = nativeReplaceState;
    clients.splice(0).forEach((client) => client.clear());
    window.sessionStorage.clear();
    Object.assign(aiOptionsApi, { getProviders: original.getProviders, getConfig: original.getConfig, getVoices: original.getVoices });
    Object.assign(leadDetailsApi, { fieldSpec: original.fieldSpec, campaignFields: original.campaignFields });
    dashboardApi.previewCampaignPrompt = original.previewCampaignPrompt;
});

function subscribe(callback: () => void) {
    window.addEventListener("popstate", callback);
    window.addEventListener(NAVIGATED, callback);
    return () => {
        window.removeEventListener("popstate", callback);
        window.removeEventListener(NAVIGATED, callback);
    };
}
function Harness() {
    useSyncExternalStore(subscribe, () => window.location.search);
    return <CampaignWizard draftStorageKey={KEY} />;
}
function mount(withBasics = true) {
    if (withBasics) window.sessionStorage.setItem(KEY, JSON.stringify({
        step: 0, voiceSelection: REALTIME, name: "Spring push", companyName: "Acme", agentNamesRaw: "Alex", personaType: "lead_gen",
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>);
}
/** Resolves on the next history move; fails instead of hanging when there is none. */
function nextPopstate() {
    return new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("no history navigation happened")), 3_000);
        window.addEventListener("popstate", () => { clearTimeout(timer); resolve(); }, { once: true });
    });
}
async function browser(direction: "back" | "forward") {
    await act(async () => {
        const moved = nextPopstate();
        window.history[direction]();
        await moved;
    });
}
const onStep = async (button: RegExp) => screen.findByRole("button", { name: button }, { timeout: SLOW });

async function toKnowledge() {
    const next = await onStep(/Next: Knowledge/) as HTMLButtonElement;
    await waitFor(() => assert.equal(next.disabled, false), { timeout: SLOW });
    await act(async () => { fireEvent.click(next); });
    await onStep(/Next: Review/);
}

test("Next gives each step its own address; browser Back and Forward move between steps", async () => {
    mount();
    await toKnowledge();
    assert.equal(window.location.search, "?step=1");
    await browser("back");
    await onStep(/Next: Knowledge/);
    assert.equal(window.location.search, "");
    await browser("forward");
    await onStep(/Next: Review/);
    assert.equal(window.location.search, "?step=1");
});

test("the wizard's own Back steps back through history instead of adding an entry", async () => {
    mount();
    await toKnowledge();
    const length = window.history.length;
    await act(async () => {
        const moved = nextPopstate();
        fireEvent.click(screen.getByRole("button", { name: /^Back$/ }));
        await moved;
    });
    await onStep(/Next: Knowledge/);
    assert.equal(window.history.length, length, "no new entry");
    assert.equal(window.location.search, "");
});

test("the review step loads its preview however it is reached", async () => {
    mount();
    await toKnowledge();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Next: Review/ })); });
    await screen.findByText("Hello from the preview", undefined, { timeout: SLOW });
    assert.equal(window.location.search, "?step=2");
    await browser("back");
    await onStep(/Next: Review/);
    await browser("forward");
    await screen.findByText("Hello from the preview", undefined, { timeout: SLOW });
    assert.equal(previews, 2, "entering the review again reads a fresh preview");
});

test("an address step the basics do not allow opens the first step and corrects the address", async () => {
    window.history.replaceState(null, "", "/campaigns/new?step=2");
    mount(false);
    await onStep(/Next: Knowledge/);
    await waitFor(() => assert.equal(window.location.search, ""));
    assert.equal(previews, 0);
});
