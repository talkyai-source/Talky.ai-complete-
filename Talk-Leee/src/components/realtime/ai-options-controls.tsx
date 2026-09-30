"use client";
import type { AIProviderConfig, ProviderListResponse } from "@/lib/ai-options-api";
import { RealtimePromptEditor } from "./prompt-editor";

export function RealtimeControls({ config, catalog, onChange, onPreview, previewing }: {
    config: AIProviderConfig;
    catalog: ProviderListResponse["realtime"];
    onChange: (config: AIProviderConfig) => void;
    onPreview: (voice: string) => void;
    previewing: boolean;
}) {
    const settings = config.realtime_settings ?? {};
    const set = (patch: Partial<NonNullable<AIProviderConfig["realtime_settings"]>>) => onChange({ ...config, realtime_settings: { ...settings, ...patch } });
    const cls = "mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm";
    return <section aria-label="Realtime configuration" className="space-y-5 rounded-xl border bg-card p-5">
        <div><h3 className="text-lg font-semibold">Realtime voice</h3>
            <p className="mt-1 text-sm text-muted-foreground">Independent speech-to-speech engine, persona and prompt. These are account defaults; campaign overrides stay separate from traditional settings.</p></div>
        {!catalog?.available && <p role="alert">{catalog?.unavailable_reason || "Realtime availability could not be verified."}</p>}
        <fieldset disabled={!catalog?.available} className="space-y-5">
            <div className="grid gap-4 sm:grid-cols-2">
                <label className="text-sm font-medium">Realtime model<select className={cls} value={config.realtime_model || catalog?.model || ""} onChange={(e) => onChange({ ...config, realtime_model: e.target.value })}>
                    {catalog?.model && <option value={catalog.model}>{catalog.model}</option>}
                </select></label>
                <label className="text-sm font-medium">Realtime voice<select className={cls} value={config.realtime_voice || ""} onChange={(e) => onChange({ ...config, realtime_voice: e.target.value })}>
                    {catalog?.voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.name}</option>)}
                </select></label>
            </div>
            <button type="button" className="rounded-md border px-3 py-2 text-sm" disabled={previewing || !config.realtime_voice} onClick={() => onPreview(config.realtime_voice!)}>{previewing ? "Generating sample…" : "Preview Realtime voice"}</button>
            <p className="text-xs text-muted-foreground">The sample uses the actual Realtime model. A sample verifies voice generation, not a complete campaign call.</p>
            <div className="grid gap-4 sm:grid-cols-2">
                <label className="text-sm font-medium">Turn detection<select className={cls} value={settings.turn_detection || "medium"} onChange={(e) => set({ turn_detection: e.target.value })}>
                    {(catalog?.turn_detection || []).map((v) => <option key={v} value={v}>{v}</option>)}
                </select></label>
                <label className="text-sm font-medium">Noise reduction<select className={cls} value={settings.noise_reduction || "near_field"} onChange={(e) => set({ noise_reduction: e.target.value })}>
                    {(catalog?.noise_reduction || []).map((v) => <option key={v} value={v}>{v}</option>)}
                </select></label>
                <label className="text-sm font-medium">Reasoning effort<select className={cls} value={settings.reasoning_effort || "low"} onChange={(e) => set({ reasoning_effort: e.target.value })}>
                    {["minimal", "low", "medium", "high", "xhigh", "none"].map((v) => <option key={v} value={v}>{v === "none" ? "Provider default" : v}</option>)}
                </select></label>
                <label className="text-sm font-medium">Speech speed<input className={cls} type="number" min={0.25} max={1.5} step={0.05} value={settings.speed ?? 1} onChange={(e) => set({ speed: Number(e.target.value) })} /></label>
                <label className="text-sm font-medium">Maximum response tokens<input className={cls} type="number" min={64} max={4096} step={64} value={settings.max_output_tokens ?? 1024} onChange={(e) => set({ max_output_tokens: Number(e.target.value) })} /></label>
            </div>
            <RealtimePromptEditor value={settings.prompt} onChange={(prompt) => set({ prompt })} />
        </fieldset>
        <p className="text-xs text-muted-foreground">Replies are validated before playback. Interrupted or unverified replies are withheld. Connection failures are reported without switching engines.</p>
    </section>;
}
