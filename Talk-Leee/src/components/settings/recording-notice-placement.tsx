"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { sharedHttpClient } from "@/lib/api";
import { isApiClientError } from "@/lib/http-client";
import type { RecordingPolicy } from "@/components/settings/recording-policy-section";

export type NoticePlacement = "own_sentence_first" | "in_opening";

const OPENING_PLAIN = 'Hi, this is [agent name], an AI assistant from [company]. Am I speaking with [first name]?';
const OPENING_RECORDED = 'Hi, this is [agent name], an AI assistant from [company], on a recorded line. Am I speaking with [first name]?';

// Same wording rule as the policy section's own error formatting.
function formatError(err: unknown) {
    if (isApiClientError(err)) return err.message;
    return err instanceof Error ? err.message : "Request failed";
}

export async function saveNoticePlacement(value: NoticePlacement): Promise<RecordingPolicy> {
    return (await sharedHttpClient().request({
        path: "/recordings/policy/outbound-notice-placement",
        method: "PUT",
        body: { outbound_notice_placement: value },
        timeoutMs: 12_000,
    })) as RecordingPolicy;
}

/**
 * Where the agent says the recording notice on outgoing calls. Renders nothing
 * unless the server sent the placement (an older server does not), a policy is
 * saved and recording is on.
 */
export function OutboundNoticePlacement({
    policy,
    canEdit,
    formDirty,
    onSaved,
}: {
    policy: RecordingPolicy;
    canEdit: boolean;
    formDirty: boolean;
    onSaved?: (p: RecordingPolicy) => void;
}) {
    const [choice, setChoice] = useState<NoticePlacement | null>(null);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    if (policy.configured !== true || typeof policy.outbound_notice_placement !== "string" || policy.default_consent_mode === "disabled") {
        return null;
    }

    const saved: NoticePlacement = policy.outbound_notice_placement === "in_opening" ? "in_opening" : "own_sentence_first";
    const selected = choice ?? saved;
    const own = typeof policy.outbound_notice_own_sentence === "string" && policy.outbound_notice_own_sentence ? policy.outbound_notice_own_sentence : null;

    const save = async () => {
        setError(null);
        setSaving(true);
        try {
            const answer = await saveNoticePlacement(selected);
            onSaved?.(answer);
            setChoice(null);
        } catch (err) {
            if (isApiClientError(err) && err.code === "recording_policy_not_saved") {
                setError("Save the recording policy first.");
            } else {
                setError(formatError(err));
            }
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="space-y-2 sm:col-span-2" data-testid="outbound-notice-placement">
            <Label className="text-xs">Where the agent says the recording notice on outgoing calls</Label>
            {own === null ? (
                <div className="text-sm">
                    {`Your notice is the standard wording, so on outgoing calls it is already said inside the opening sentence: "${OPENING_RECORDED}"`}
                </div>
            ) : (
                <>
                    <div className="flex items-start gap-2">
                        <input
                            id="rp-placement-own"
                            type="radio"
                            name="rp-notice-placement"
                            checked={selected === "own_sentence_first"}
                            onChange={() => setChoice("own_sentence_first")}
                            disabled={!canEdit}
                            aria-describedby="rp-placement-own-help"
                            className="mt-1 h-4 w-4 accent-emerald-600"
                        />
                        <div>
                            <label htmlFor="rp-placement-own" className="text-sm text-foreground">Your notice first, as its own sentence</label>
                            <div id="rp-placement-own-help" className="text-xs text-muted-foreground">
                                {`The person first hears: "${own}" Then the agent's opening: "${OPENING_PLAIN}"`}
                            </div>
                        </div>
                    </div>
                    <div className="flex items-start gap-2">
                        <input
                            id="rp-placement-opening"
                            type="radio"
                            name="rp-notice-placement"
                            checked={selected === "in_opening"}
                            onChange={() => setChoice("in_opening")}
                            disabled={!canEdit}
                            aria-describedby="rp-placement-opening-help"
                            className="mt-1 h-4 w-4 accent-emerald-600"
                        />
                        <div>
                            <label htmlFor="rp-placement-opening" className="text-sm text-foreground">Inside the opening sentence</label>
                            <div id="rp-placement-opening-help" className="text-xs text-muted-foreground">
                                {`The person hears one sentence: "${OPENING_RECORDED}" Your notice above is not spoken on outgoing calls.`}
                            </div>
                        </div>
                    </div>
                </>
            )}
            <div className="text-xs text-muted-foreground">
                {'Outgoing calls only. Incoming calls say the notice set on each incoming line. With no first name on file the agent asks "Who am I speaking with?". Either way a recording is marked "notice confirmed" only when the phone system confirms the whole sentence was played.'}
            </div>
            {own !== null && canEdit ? (
                <div className="flex flex-wrap items-center gap-2">
                    <Button
                        type="button"
                        disabled={selected === saved || formDirty || saving}
                        onClick={() => void save()}
                        data-testid="outbound-notice-placement-save"
                    >
                        {saving ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
                        Save this choice
                    </Button>
                    {formDirty ? <span className="text-xs text-muted-foreground">Save or undo the changes above first.</span> : null}
                </div>
            ) : null}
            {error ? <div className="text-xs text-red-500">{error}</div> : null}
        </div>
    );
}
