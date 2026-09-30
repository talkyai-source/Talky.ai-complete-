"use client";

import { useEffect, useState, useRef } from "react";
import { aiOptionsApi, type ProviderListResponse } from "@/lib/ai-options-api";

import { RealtimePromptEditor } from "./prompt-editor";
import { DEFAULT_REALTIME_PROMPT, type CampaignVoiceSettingsValue } from "./types";
export type { CampaignVoiceSettingsValue } from "./types";

export function CampaignVoiceSettings({ value, onChange }: {
    value: CampaignVoiceSettingsValue;
    onChange: (value: CampaignVoiceSettingsValue) => void;
}) {
    const [catalog, setCatalog] = useState<ProviderListResponse["realtime"]>();
    const [error, setError] = useState("");
    const changed = useRef(false);
    useEffect(() => {
        let cancelled = false;
        Promise.all([aiOptionsApi.getProviders(), aiOptionsApi.getConfig()]).then(([providers, config]) => {
            if (cancelled) return;
            setCatalog(providers.realtime);
            if (!changed.current && !value.pipeline_mode) onChange({ ...value,
                pipeline_mode: config.pipeline_mode || "cascaded",
                realtime_model: value.realtime_model || config.realtime_model,
                realtime_voice: value.realtime_voice || config.realtime_voice,
                realtime_prompt: value.realtime_prompt || config.realtime_settings?.prompt,
            });
        }).catch(() => { if (!cancelled) setError("Unable to check Realtime availability. Refresh before selecting it."); });
        return () => { cancelled = true; };
    // Load initial account defaults once; later changes belong to the operator.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    const realtime = value.pipeline_mode === "realtime";
    const prompt = value.realtime_prompt ?? DEFAULT_REALTIME_PROMPT;
    const inputClass = "mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm";
    return <section aria-label="Campaign voice engine" className="space-y-4 rounded-xl border p-4">
        <label className="block text-sm font-medium">Voice engine
            <select className={inputClass} value={value.pipeline_mode ?? ""} onChange={(event) => {
                changed.current = true;
                const mode = event.target.value as "cascaded" | "realtime";
                onChange({ ...value, pipeline_mode: mode,
                    ...(mode === "realtime" ? { realtime_model: catalog?.model, realtime_voice: value.realtime_voice || catalog?.voices[0]?.id,
                        realtime_prompt: prompt } : {}) });
            }}>
                <option value="" disabled>Select a voice engine</option>
                <option value="cascaded">Traditional · separate speech and language providers</option>
                <option value="realtime" disabled={!catalog?.available}>GPT Realtime · speech to speech</option>
            </select>
        </label>
        {(error || catalog?.unavailable_reason) && <p role="status" className="text-sm text-muted-foreground">{error || catalog?.unavailable_reason}</p>}
        {realtime && <>
            <p className="text-sm text-muted-foreground">Realtime uses its own prompt and voice. Traditional instructions, model, and TTS settings do not apply. Replies are checked before playback; connection failures are reported without changing engines.</p>
            <label className="block text-sm font-medium">Realtime voice
                <select className={inputClass} value={value.realtime_voice ?? ""} onChange={(event) => onChange({ ...value, realtime_voice: event.target.value })}>
                    <option value="" disabled>Select a Realtime voice</option>
                    {catalog?.voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.name}</option>)}
                </select>
            </label>
            <RealtimePromptEditor value={prompt} onChange={(realtime_prompt) => onChange({ ...value, realtime_prompt })} />
        </>}
    </section>;
}
