/**
 * Recording of incoming calls: a new campaign records by default once the
 * server says the platform allows it; saved campaigns are never touched; the
 * notice may not offer a key-press opt-out. Mount modelled on
 * inbound-campaign-form.blockers.test.tsx.
 */
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "@/lib/api";
import { dashboardApi } from "@/lib/dashboard-api";
import { inboundApi, type InboundCampaign } from "@/lib/inbound-api";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { AuthProvider } from "@/lib/auth-context";
import { initialInboundCampaignInput, validateInboundCampaign } from "@/lib/inbound-validation";
import { InboundCampaignForm } from "./inbound-campaign-form";

const SLOW = 10_000;
const DEFAULT_TEXT = "This call may be recorded for quality and training purposes.";
const OFF_SENTENCE = /Recording of incoming calls is switched off for the whole service right now\. A campaign with recording ticked cannot be activated until it is switched on\./;
const originals = { request: api.request, campaigns: dashboardApi.listCampaigns, numbers: inboundApi.availablePhoneNumbers, capabilities: inboundApi.getCapabilities, voices: aiOptionsApi.getVoices };
let client: QueryClient | undefined;
beforeEach(() => mock.method(window.HTMLCanvasElement.prototype, "getContext", () => null));
afterEach(() => {
    cleanup(); client?.clear(); mock.restoreAll(); window.localStorage.clear(); window.sessionStorage.clear();
    api.request = originals.request; dashboardApi.listCampaigns = originals.campaigns; inboundApi.availablePhoneNumbers = originals.numbers;
    inboundApi.getCapabilities = originals.capabilities; aiOptionsApi.getVoices = originals.voices;
});

type Caps = () => Promise<unknown>;

function savedCampaign(recording_enabled: boolean, consent_message: string): InboundCampaign {
    return {
        id: "inbound-1", name: "Main line", direction: "inbound", status: "draft", version: 1, config_version: 1, config_checksum: "c",
        campaign_id: "c1", sip_trunk_id: "t1", opening_mode: "caller_first", greeting: "Hello", silence_timeout_seconds: 5, timezone: "UTC",
        weekly_schedule: [], holiday_policy: "closed", after_hours_action: "hangup", transfer_enabled: false, transfer_destinations: [],
        transfer_failure_action: "hangup", max_transfer_attempts: 1, max_transfer_hops: 1, max_call_duration_seconds: 1800,
        recording_enabled, consent_message, readiness: { ready: false, checks: [], blockers: [] },
        created_at: "2026-08-28T00:00:00Z", updated_at: "2026-08-28T00:00:00Z",
    } as InboundCampaign;
}

function mount(caps: Caps, saved?: InboundCampaign) {
    window.localStorage.setItem("talklee.auth.token", "synthetic-session");
    mock.method(api, "getMe", async () => ({ id: "synthetic-user", tenant_id: "synthetic-tenant", email: "form@example.invalid", role: "tenant_admin", minutes_state: "known", minutes_remaining: 1 }));
    dashboardApi.listCampaigns = async () => ({ campaigns: [] });
    inboundApi.availablePhoneNumbers = (async () => []) as typeof inboundApi.availablePhoneNumbers;
    inboundApi.getCapabilities = caps as typeof inboundApi.getCapabilities;
    aiOptionsApi.getVoices = async () => ({ voices: [] });
    api.request = async <T,>() => [] as T;
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(<AuthProvider><QueryClientProvider client={client}>
        {saved
            ? <InboundCampaignForm mode="edit" initialValue={saved} pending={false} canAssignNumber={false} onSubmit={async () => {}} />
            : <InboundCampaignForm mode="create" lockCampaign initialCampaignId="c1" pending={false} canAssignNumber onSubmit={async () => {}} />}
    </QueryClientProvider></AuthProvider>);
}

const box = () => screen.getByRole("checkbox", { name: /Record inbound calls/ }) as HTMLInputElement;
const notice = () => document.getElementById("inbound-consent_message") as HTMLTextAreaElement | null;
const withPlatform = (recording_platform_enabled: boolean | undefined): Caps => async () => ({ transfer_configuration_available: false, ...(recording_platform_enabled === undefined ? {} : { recording_platform_enabled }) });

test("new campaign, platform allows recording: ticked with the default notice", async () => {
    mount(withPlatform(true));
    await waitFor(() => assert.equal(box().checked, true), { timeout: SLOW });
    await waitFor(() => assert.equal(notice() !== null, true), { timeout: SLOW });
    assert.equal(notice()?.value, DEFAULT_TEXT);
    assert.equal(screen.queryByText(OFF_SENTENCE) !== null, false);
});

test("new campaign, platform switched off: unticked and the whole-service sentence shows", async () => {
    mount(withPlatform(false));
    await screen.findByText(OFF_SENTENCE, undefined, { timeout: SLOW });
    assert.equal(box().checked, false);
    assert.equal(notice() !== null, false);
});

test("new campaign, field absent (older server): unticked, reads as switched off", async () => {
    mount(withPlatform(undefined));
    await screen.findByText(OFF_SENTENCE, undefined, { timeout: SLOW });
    assert.equal(box().checked, false);
});

test("new campaign, capabilities request failed: unticked and no whole-service sentence", async () => {
    mount(async () => { throw new Error("capabilities unavailable"); });
    await screen.findByText(/We could not check whether transfer is available/, undefined, { timeout: SLOW });
    assert.equal(box().checked, false);
    assert.equal(screen.queryByText(OFF_SENTENCE) !== null, false);
});

test("new campaign while capabilities are still loading: unticked, no sentence", async () => {
    mount(() => new Promise(() => {}));
    await screen.findByRole("checkbox", { name: /Record inbound calls/ }, { timeout: SLOW });
    assert.equal(box().checked, false);
    assert.equal(screen.queryByText(OFF_SENTENCE) !== null, false);
});

test("the plain-words helper text is shown under the checkbox", async () => {
    mount(withPlatform(true));
    await screen.findByText(/The agent says this notice first on every call, and the recording starts after it\. If the service cannot confirm the notice was played in full, the recording is still kept and the call is marked "notice not confirmed"\. Calls are not recorded if your account's recording setting is "disabled"\./, undefined, { timeout: SLOW });
});

test("editing a saved campaign with recording off stays unticked when the platform allows it", async () => {
    let resolved = false;
    mount(async () => { resolved = true; return { recording_platform_enabled: true }; }, savedCampaign(false, ""));
    await waitFor(() => assert.equal(resolved, true), { timeout: SLOW });
    await new Promise((r) => setTimeout(r, 50));
    assert.equal(box().checked, false);
    assert.equal(notice() !== null, false);
});

test("editing a saved campaign with recording on keeps its own text", async () => {
    mount(withPlatform(true), savedCampaign(true, "Our own notice."));
    await waitFor(() => assert.equal(notice() !== null, true), { timeout: SLOW });
    assert.equal(box().checked, true);
    assert.equal(notice()?.value, "Our own notice.");
});

test("the customer unticks, then capabilities arrive: stays unticked", async () => {
    let release: (v: unknown) => void = () => {};
    mount(() => new Promise((resolve) => { release = resolve; }));
    await screen.findByRole("checkbox", { name: /Record inbound calls/ }, { timeout: SLOW });
    fireEvent.click(box());
    fireEvent.click(box());
    assert.equal(box().checked, false);
    release({ recording_platform_enabled: true });
    await new Promise((r) => setTimeout(r, 100));
    assert.equal(box().checked, false);
    assert.equal(notice() !== null, false);
});

test("the customer typed their own notice, then capabilities arrive: text untouched", async () => {
    let release: (v: unknown) => void = () => {};
    mount(() => new Promise((resolve) => { release = resolve; }));
    await screen.findByRole("checkbox", { name: /Record inbound calls/ }, { timeout: SLOW });
    fireEvent.click(box());
    await waitFor(() => assert.equal(notice() !== null, true), { timeout: SLOW });
    fireEvent.change(notice() as HTMLTextAreaElement, { target: { value: "Calls are recorded." } });
    release({ recording_platform_enabled: true });
    await new Promise((r) => setTimeout(r, 100));
    assert.equal(box().checked, true);
    assert.equal(notice()?.value, "Calls are recorded.");
});

const OPT_OUT_ERROR = "The recording notice cannot offer callers a way to opt out, such as pressing a key: this service cannot do that during a call. Remove that sentence.";
function noticeError(recording_enabled: boolean, consent_message: string) {
    return validateInboundCampaign({ ...initialInboundCampaignInput(undefined, { campaignId: "c1" }), recording_enabled, consent_message }).consent_message;
}

test("validation: a key-press opt-out in the notice is refused with recording on", () => {
    assert.equal(noticeError(true, "This call is recorded. Press 9 to opt out."), OPT_OUT_ERROR);
    assert.equal(noticeError(true, "Say opt-out to stop."), OPT_OUT_ERROR);
});

test("validation: the same text with recording off, or an ordinary notice, is accepted", () => {
    assert.equal(noticeError(false, "Press 9 to opt out."), undefined);
    assert.equal(noticeError(true, DEFAULT_TEXT), undefined);
});
