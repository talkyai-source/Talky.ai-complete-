import assert from "node:assert/strict";
import { test } from "node:test";

import {
    INBOUND_AFTER_HOURS_OPTIONS,
    initialInboundCampaignInput,
    isEligibleInboundBaseCampaign,
    isEligibleInboundTrunk,
    isValidE164,
    validateInboundCampaign,
    verifiedTransferConfigurationAvailable,
} from "@/lib/inbound-validation";

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
    assert.equal(value.knowledge_base_id, "");
    assert.deepEqual(value.allowed_tools, []);
    assert.equal(value.silence_timeout_seconds, 8);
});
