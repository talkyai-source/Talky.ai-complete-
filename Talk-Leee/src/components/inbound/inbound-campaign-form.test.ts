import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { defaultInboundWeeklySchedule, inboundApi } from "@/lib/inbound-api";
import { NEW_CAMPAIGN_FOR_INBOUND_HREF } from "@/lib/campaign-create-return";
import {
    INBOUND_AFTER_HOURS_OPTIONS,
    initialInboundCampaignInput,
    isEligibleInboundBaseCampaign,
    isEligibleInboundTrunk,
    isValidE164,
    validateInboundCampaign,
    verifiedTransferConfigurationAvailable,
} from "@/lib/inbound-validation";

/**
 * The only override keys the voice runtime will accept.
 *
 * Source of truth is `_TEXT_LENGTHS` + `_SUPPORTED` in
 * backend/app/domain/services/telephony/inbound_overrides.py:9-15. This copy
 * exists so the assertion below reads clearly; the test after it proves the
 * copy still matches the backend rather than trusting that it does.
 */
const SUPPORTED_QUALIFICATION_KEYS = [
    "purpose",
    "persona",
    "system_prompt",
    "voice_id",
    "silence_timeout_seconds",
] as const;

test("inbound form requires a verified DID, AI campaign and inbound trunk", () => {
    const errors = validateInboundCampaign(initialInboundCampaignInput());
    assert.equal(errors.name, "Enter a name for this inbound campaign.");
    assert.equal(errors.did_number, "Choose a verified phone number.");
    assert.equal(errors.campaign_id, "Choose the AI campaign that should answer.");
    assert.equal(errors.sip_trunk_id, "Choose an inbound-capable SIP trunk.");
});

test("inbound form enforces recording disclosure and E.164 transfer destinations", () => {
    const value = initialInboundCampaignInput();
    value.name = "Main line";
    value.did_number = "+14155550123";
    value.campaign_id = "campaign-1";
    value.sip_trunk_id = "trunk-1";
    value.recording_enabled = true;
    value.consent_message = "";
    value.after_hours_action = "transfer";
    value.transfer_number = "not-a-number";
    const errors = validateInboundCampaign(value);
    assert.ok(errors.consent_message);
    assert.match(errors.after_hours_action ?? "", /capability gate/i);
    assert.ok(errors.transfer_number);
});

test("after-hours UI exposes only runtime-backed actions", () => {
    const options = new Map(INBOUND_AFTER_HOURS_OPTIONS.map((option) => [option.value, option]));
    assert.equal(initialInboundCampaignInput().after_hours_action, "hangup");
    assert.equal(options.get("hangup")?.label, "Reject before answer");
    assert.equal(options.get("hangup")?.runtimeSupported, true);
    assert.match(options.get("voicemail")?.label ?? "", /message intake/i);
    assert.equal(options.get("voicemail")?.runtimeSupported, true);
    assert.match(options.get("transfer")?.label ?? "", /not yet available/i);
    assert.equal(options.get("transfer")?.runtimeSupported, false);
});

test("AI message intake fails closed without its pinned opening message", () => {
    const value = initialInboundCampaignInput();
    value.after_hours_action = "voicemail";
    value.after_hours_message = "";
    assert.match(validateInboundCampaign(value).after_hours_message ?? "", /opening message/i);
    value.after_hours_message = "Please tell our AI your name, number, and message.";
    assert.equal(validateInboundCampaign(value).after_hours_message, undefined);
});

test("saved transfer policies can only be disabled while runtime proof is incomplete", () => {
    const value = initialInboundCampaignInput();
    value.transfer_enabled = true;
    value.transfer_destinations = ["+14155550123"];
    assert.match(validateInboundCampaign(value).transfer_enabled ?? "", /disable live transfer/i);
    assert.equal(validateInboundCampaign(value).transfer_destinations, undefined);

    const approved = validateInboundCampaign(value, { transferConfigurationAvailable: true });
    assert.equal(approved.transfer_enabled, undefined);
});

test("after-hours transfer is accepted only in the server-approved proof window", () => {
    const value = initialInboundCampaignInput();
    value.after_hours_action = "transfer";
    value.transfer_number = "+14155550123";
    value.transfer_enabled = true;
    value.transfer_destinations = ["+14155550123"];

    assert.match(validateInboundCampaign(value).after_hours_action ?? "", /capability gates/i);
    const approved = validateInboundCampaign(value, { transferConfigurationAvailable: true });
    assert.equal(approved.after_hours_action, undefined);
    assert.equal(approved.transfer_enabled, undefined);
});

test("cached open transfer capability fails closed after a refresh error", () => {
    assert.equal(verifiedTransferConfigurationAvailable({
        data: { transfer_configuration_available: true },
        isSuccess: true,
        isError: true,
    }), false);
    assert.equal(verifiedTransferConfigurationAvailable({
        data: { transfer_configuration_available: true },
        isSuccess: true,
        isError: false,
    }), true);
});

test("E.164 and duration validation match the server boundary contract", () => {
    assert.equal(isValidE164("+1234567"), true);
    assert.equal(isValidE164("+123456789012345"), true);
    assert.equal(isValidE164("+123456"), false);
    assert.equal(isValidE164("+1234567890123456"), false);

    const value = initialInboundCampaignInput();
    value.max_call_duration_seconds = Number.NaN;
    assert.match(validateInboundCampaign(value).max_call_duration_seconds ?? "", /whole number/i);
    value.max_call_duration_seconds = 60.5;
    assert.match(validateInboundCampaign(value).max_call_duration_seconds ?? "", /whole number/i);
});

test("only server-visible eligible campaigns and runtime-ready inbound trunks can be selected", () => {
    assert.equal(isEligibleInboundBaseCampaign({ direction: "inbound", status: "paused" }), true);
    assert.equal(isEligibleInboundBaseCampaign({ direction: "outbound", status: "draft" }), true);
    assert.equal(isEligibleInboundBaseCampaign({ direction: "outbound", status: "running" }), false);
    assert.equal(isEligibleInboundBaseCampaign({ direction: "inbound", status: "completed" }), false);
    assert.equal(isEligibleInboundBaseCampaign({ direction: undefined, status: "draft" }), false);

    assert.equal(isEligibleInboundTrunk({ direction: "inbound", is_active: true, runtime_ready: true }), true);
    assert.equal(isEligibleInboundTrunk({ direction: "both", is_active: true, runtime_ready: true }), true);
    assert.equal(isEligibleInboundTrunk({ direction: "inbound", is_active: true, runtime_ready: false }), false);
    assert.equal(isEligibleInboundTrunk({ direction: "inbound", is_active: false, runtime_ready: true }), false);
    assert.equal(isEligibleInboundTrunk({ direction: "outbound", is_active: true, runtime_ready: true }), false);
});

test("inbound-specific overrides start neutral and inherit the base campaign", () => {
    const value = initialInboundCampaignInput();
    assert.equal(value.purpose, "");
    assert.equal(value.agent_persona, "");
    assert.equal(value.silence_timeout_seconds, 8);
});

/**
 * T1 — knowledge and tools can never reach the wire.
 *
 * `qualification_config` is the one free-form object on the inbound create
 * contract. `apply_qualification_overrides` raises on any key outside
 * `_SUPPORTED`, so an unsupported key does not fail at save time — it saves
 * cleanly and then fails the live call at admission, which is the worst
 * possible place to find out. A placeholder `knowledge_base_id` of exactly
 * that kind shipped here once and was removed on 2026-09-01; this test is
 * what stops it coming back.
 *
 * The input is built by spreading `initialInboundCampaignInput()` so a field
 * added to the form later is covered by this assertion without anyone
 * remembering to extend it.
 */
test("every qualification_config key is one of the five runtime-supported names", async () => {
    const previousFetch = globalThis.fetch;
    const bodies: string[] = [];
    globalThis.fetch = (async (_url: RequestInfo | URL, init?: RequestInit) => {
        bodies.push(String(init?.body));
        return new Response(JSON.stringify({ id: "in-1" }), { status: 201, headers: { "content-type": "application/json" } });
    }) as typeof fetch;
    try {
        // Every override non-neutral, so none is dropped by cleanInput's
        // "only send what differs from the base campaign" rule.
        const populated = {
            ...initialInboundCampaignInput(),
            name: "Main support line",
            did_number: "+14155550123",
            campaign_id: "campaign-1",
            sip_trunk_id: "trunk-1",
            purpose: "Handle new enquiries",
            agent_persona: "Warm, concise receptionist",
            system_prompt: "Answer only from approved facts.",
            voice_id: "voice-live-1",
            silence_timeout_seconds: 12,
            greeting: "Hello, thanks for calling.",
        };
        await inboundApi.create(populated, populated.did_number);

        const body = JSON.parse(bodies[0]) as { qualification_config?: Record<string, unknown> };
        const config = body.qualification_config ?? {};
        const keys = Object.keys(config);

        // Guard the guard: an empty object would satisfy a subset check
        // vacuously and prove nothing.
        assert.equal(keys.length, SUPPORTED_QUALIFICATION_KEYS.length);
        for (const key of keys) {
            assert.ok(
                (SUPPORTED_QUALIFICATION_KEYS as readonly string[]).includes(key),
                `qualification_config carried an unsupported key the runtime will reject: ${key}`,
            );
        }
        // Named outright, because these two are the shapes that regressed before.
        assert.equal(Object.hasOwn(config, "knowledge_base_id"), false);
        assert.equal(Object.hasOwn(config, "allowed_tools"), false);
    } finally {
        globalThis.fetch = previousFetch;
    }
});

/**
 * T2 — the frontend's idea of "supported" is the backend's, still.
 *
 * T1 checks the payload against the local constant. That constant could drift
 * from the runtime the moment someone edits `_SUPPORTED`, and nothing would
 * fail. This reads the backend module and compares the two sets directly, the
 * same way inbound-state-parity.test.ts reads the frozen spec. Both CI
 * (actions/checkout@v4, full tree) and local runs have backend/ on disk.
 */
test("the supported override set still matches inbound_overrides.py", () => {
    const here = path.dirname(fileURLToPath(import.meta.url));
    const source = readFileSync(
        path.join(here, "..", "..", "..", "..", "backend", "app", "domain", "services", "telephony", "inbound_overrides.py"),
        "utf8",
    );

    const textLengths = /_TEXT_LENGTHS\s*=\s*\{([\s\S]*?)\}/.exec(source);
    assert.ok(textLengths, "inbound_overrides.py must declare _TEXT_LENGTHS");
    const textKeys = [...textLengths[1].matchAll(/"([a-z_]+)"\s*:/g)].map((match) => match[1]);

    const supported = /_SUPPORTED\s*=\s*\{([^}]*)\}/.exec(source);
    assert.ok(supported, "inbound_overrides.py must declare _SUPPORTED");
    // _SUPPORTED is `{*_TEXT_LENGTHS, "silence_timeout_seconds"}` — the splat
    // contributes no literal, so this picks up only the extra names.
    const extraKeys = [...supported[1].matchAll(/"([a-z_]+)"/g)].map((match) => match[1]);

    assert.deepEqual(
        [...textKeys, ...extraKeys].sort(),
        [...SUPPORTED_QUALIFICATION_KEYS].sort(),
    );
});

/**
 * R8 — agent-first without a greeting is not a saveable campaign.
 *
 * Agent-first means the agent speaks first. With no greeting there is nothing
 * to speak, and the failure is silence on a live call rather than an error on
 * a form. Caller-first is the mirror case and must stay optional: the greeting
 * there is only an acknowledgement used after the caller's first turn, so
 * requiring it would block a valid configuration.
 */
test("agent_first requires a greeting and caller_first does not", () => {
    const agentFirst = initialInboundCampaignInput();
    agentFirst.opening_mode = "agent_first";
    agentFirst.greeting = "";
    assert.match(validateInboundCampaign(agentFirst).greeting ?? "", /greeting/i);

    // Whitespace is not a greeting — it synthesises to nothing.
    agentFirst.greeting = "   ";
    assert.match(validateInboundCampaign(agentFirst).greeting ?? "", /greeting/i);

    agentFirst.greeting = "Hello, thanks for calling. How can I help?";
    assert.equal(validateInboundCampaign(agentFirst).greeting, undefined);

    const callerFirst = initialInboundCampaignInput();
    callerFirst.opening_mode = "caller_first";
    callerFirst.greeting = "";
    assert.equal(validateInboundCampaign(callerFirst).greeting, undefined);
});

/**
 * R8 / matrix G-3 — day 0 is Monday on both sides of the wire.
 *
 * `weekly_schedule[].day` is written by this form and read by the backend
 * scheduler, and no schema validates it: `business_hours` is an opaque
 * `dict[str, Any]` on the create contract
 * (backend/app/api/v1/schemas/inbound_campaigns.py:39). If the two ends
 * disagreed about which integer is Monday, every campaign's business hours
 * would be silently offset by a day — open when it should be closed — with
 * nothing failing anywhere. G-3 recorded this as unverified; this test is the
 * verification.
 *
 * The backend convention is read from source rather than assumed, the same
 * way the `inbound_overrides.py` check above works, because the only thing
 * that can change it is an edit to that file.
 */
test("weekly_schedule day 0 is Monday on both the form and the backend scheduler", () => {
    const here = path.dirname(fileURLToPath(import.meta.url));

    // ── Backend: Monday=0 ────────────────────────────────────────────────
    const scheduler = readFileSync(
        path.join(here, "..", "..", "..", "..", "backend", "app", "domain", "services", "telephony", "business_hours.py"),
        "utf8",
    );
    // `datetime.weekday()` is Monday=0. `isoweekday()` is Monday=1 and
    // `strftime("%w")` is Sunday=0 — either would shift the whole week.
    assert.match(
        scheduler,
        /^\s*day = local\.weekday\(\)\s*$/m,
        "business_hours.py must derive the schedule day from datetime.weekday() (Monday=0)",
    );
    assert.doesNotMatch(scheduler, /isoweekday\(\)/, "isoweekday() is Monday=1 and would offset the week");
    assert.doesNotMatch(scheduler, /strftime\(\s*["']%[wu]["']\s*\)/, "%w is Sunday=0 and would offset the week");
    assert.match(
        scheduler,
        /Monday=0/,
        "business_hours.py must still state its Monday=0 contract",
    );

    // ── Form: the label the operator reads against each index ────────────
    const form = readFileSync(path.join(here, "inbound-campaign-form.tsx"), "utf8");
    const declaration = /const DAY_NAMES = \[([^\]]*)\]/.exec(form);
    assert.ok(declaration, "inbound-campaign-form.tsx must declare DAY_NAMES");
    const dayNames = [...declaration[1].matchAll(/"([A-Za-z]+)"/g)].map((match) => match[1]);
    assert.deepEqual(dayNames, [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ], "the row rendered for day N must be the day the backend evaluates for N");

    // ── Default: exactly Monday to Friday open ───────────────────────────
    const schedule = defaultInboundWeeklySchedule();
    assert.equal(schedule.length, 7);
    assert.deepEqual(schedule.map((entry) => entry.day), [0, 1, 2, 3, 4, 5, 6]);
    const openDays = schedule.filter((entry) => entry.enabled).map((entry) => dayNames[entry.day]);
    assert.deepEqual(openDays, ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]);
});

test("a new inbound form can be seeded with the campaign just created for it", () => {
    assert.equal(initialInboundCampaignInput(undefined, { campaignId: "draft-1" }).campaign_id, "draft-1");
    assert.equal(initialInboundCampaignInput(undefined, { campaignId: null }).campaign_id, "");
});

test("a saved inbound campaign ignores a stray preselect", () => {
    const saved = { campaign_id: "saved-9" } as unknown as Parameters<typeof initialInboundCampaignInput>[0];
    assert.equal(initialInboundCampaignInput(saved, { campaignId: "draft-1" }).campaign_id, "saved-9");
});

test("the create form offers a way to make the AI campaign it requires", () => {
    const source = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "inbound-campaign-form.tsx"), "utf8");
    assert.ok(source.includes("NEW_CAMPAIGN_FOR_INBOUND_HREF"), "create link missing from the inbound form");
    assert.equal(NEW_CAMPAIGN_FOR_INBOUND_HREF, "/campaigns/new?for=inbound");
});
