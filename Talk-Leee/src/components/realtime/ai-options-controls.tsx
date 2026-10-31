"use client";
import type { AIProviderConfig, ProviderListResponse } from "@/lib/ai-options-api";
import { Select } from "@/components/ui/select";
import { RealtimePromptEditor } from "./prompt-editor";

export function RealtimeControls({ config, catalog, onChange, onPreview, previewing }: {
    config: AIProviderConfig;
    catalog: ProviderListResponse["realtime"];
    onChange: (config: AIProviderConfig) => void;
    onPreview: (voice: string) => void;
    previewing: boolean;
}) {
    const settings = config.realtime_settings ?? {};
    const vad = settings.turn_detection;
    const vadSelection = typeof vad === "object" ? (vad.type === "server_vad" ? "server_vad" : vad.eagerness || "medium") : vad || "medium";
    const set = (patch: Partial<NonNullable<AIProviderConfig["realtime_settings"]>>) => onChange({ ...config, realtime_settings: { ...settings, ...patch } });
    const cls = "mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm";
    // Shared-Select sizing matching the old native controls (h-auto + py-2).
    const selCls = "h-auto px-3 py-2 text-sm rounded-md border bg-background";
    return <section aria-label="Realtime configuration" className="space-y-5 rounded-xl border bg-card p-5">
        <div><h3 className="text-lg font-semibold">Realtime voice</h3>
            <p className="mt-1 text-sm text-muted-foreground">Independent speech-to-speech engine, persona and prompt. These are account defaults; campaign overrides stay separate from traditional settings.</p></div>
        {!catalog?.available && <p role="alert">{catalog?.unavailable_reason || "Realtime availability could not be verified."}</p>}
        <fieldset disabled={!catalog?.available} className="space-y-5">
            <div className="grid gap-4 sm:grid-cols-2">
                <label className="text-sm font-medium">Realtime model<Select className="mt-1" selectClassName={selCls} ariaLabel="Realtime model" value={config.realtime_model || catalog?.model || ""} onChange={(next) => onChange({ ...config, realtime_model: next })}>
                    {catalog?.model && <option value={catalog.model}>{catalog.model}</option>}
                </Select></label>
                <label className="text-sm font-medium">Realtime voice<Select className="mt-1" selectClassName={selCls} ariaLabel="Realtime voice" value={config.realtime_voice || ""} onChange={(next) => onChange({ ...config, realtime_voice: next })}>
                    {catalog?.voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.name}</option>)}
                </Select></label>
            </div>
            <button type="button" className="rounded-md border px-3 py-2 text-sm" disabled={previewing || !config.realtime_voice} onClick={() => onPreview(config.realtime_voice!)}>{previewing ? "Generating sample…" : "Preview Realtime voice"}</button>
            <p className="text-xs text-muted-foreground">The sample uses the actual Realtime model. A sample verifies voice generation, not a complete campaign call.</p>
            <div className="grid gap-4 sm:grid-cols-2">
                <label className="text-sm font-medium">Turn detection<Select className="mt-1" selectClassName={selCls} ariaLabel="Turn detection" value={vadSelection} onChange={(next) => { if (next !== "server_vad") set({ turn_detection: next }); }}>
                    {vadSelection === "server_vad" && <option value="server_vad">Server VAD · saved settings</option>}
                    {(catalog?.turn_detection || []).map((v) => <option key={v} value={v}>{v}</option>)}
                </Select></label>
                <label className="text-sm font-medium">Noise reduction<Select className="mt-1" selectClassName={selCls} ariaLabel="Noise reduction" value={settings.noise_reduction || "near_field"} onChange={(next) => set({ noise_reduction: next })}>
                    {(catalog?.noise_reduction || []).map((v) => <option key={v} value={v}>{v}</option>)}
                </Select></label>
                <label className="text-sm font-medium">Reasoning effort<Select className="mt-1" selectClassName={selCls} ariaLabel="Reasoning effort" value={settings.reasoning_effort || "low"} onChange={(next) => set({ reasoning_effort: next })}>
                    {["minimal", "low", "medium", "high", "xhigh", "none"].map((v) => <option key={v} value={v}>{v === "none" ? "Provider default" : v}</option>)}
                </Select></label>
                <label className="text-sm font-medium">Speech speed<input className={cls} type="number" min={0.25} max={1.5} step={0.05} value={settings.speed ?? 1} onChange={(e) => set({ speed: Number(e.target.value) })} /></label>
                <label className="text-sm font-medium">Maximum response tokens<input className={cls} type="number" min={64} max={4096} step={64} value={settings.max_output_tokens ?? 1024} onChange={(e) => set({ max_output_tokens: Number(e.target.value) })} /></label>
            </div>
            <RealtimePromptEditor value={settings.prompt} onChange={(prompt) => set({ prompt })} />
        </fieldset>
        <p className="text-xs text-muted-foreground">Replies are validated before playback. Interrupted or unverified replies are withheld. Connection failures are reported without switching engines.</p>
    </section>;
}
