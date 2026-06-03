"use client";

/**
 * Voice + TTS-provider picker (shared by the create wizard and the edit page).
 *
 * Provider dropdown filters the voice list; each voice has a ▶/⏹ play button
 * (synthesizes a sample via the preview API and plays it through WebAudio).
 * Provider + voice are now PER CAMPAIGN, so picking here has no account-wide
 * side effect — the chosen provider is reported up via onProviderChange and the
 * parent stores it on the campaign.
 *
 * Controlled for the voice (value + onVoiceChange). Provider is managed
 * internally (defaulting to the account's current provider) and reported up.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Play, Sparkles, Square } from "lucide-react";

import { Label } from "@/components/ui/label";
import { aiOptionsApi, VoiceInfo } from "@/lib/ai-options-api";
import { PersonaType } from "@/lib/dashboard-api";
import {
    compareVoicesByPersonaRecommendation,
    isRecommendedVoiceForPersona,
} from "@/lib/campaign-personas";

export type VoiceProviderPickerProps = {
    personaType: PersonaType;
    voiceId: string;
    /** voiceName is passed for display convenience; parents may ignore it. */
    onVoiceChange: (voiceId: string, voiceName?: string) => void;
    onProviderChange?: (provider: string) => void;
    /** Preselect this provider (e.g. the campaign's saved provider on edit). */
    initialProvider?: string | null;
};

export function VoiceProviderPicker({
    personaType, voiceId, onVoiceChange, onProviderChange, initialProvider,
}: VoiceProviderPickerProps) {
    const [voices, setVoices] = useState<VoiceInfo[]>([]);
    const [provider, setProvider] = useState<string>("");
    const [loaded, setLoaded] = useState(false);
    const [playingId, setPlayingId] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const audioCtxRef = useRef<AudioContext | null>(null);

    const emitProvider = useRef(onProviderChange);
    emitProvider.current = onProviderChange;

    useEffect(() => {
        let cancelled = false;
        Promise.all([
            aiOptionsApi.getVoices().then((r) => r.voices).catch(() => [] as VoiceInfo[]),
            aiOptionsApi.getConfig().catch(() => null),
        ]).then(([vs, cfg]) => {
            if (cancelled) return;
            setVoices(vs);
            const withVoices = new Set(vs.map((v) => v.provider));
            const want = (initialProvider || cfg?.tts_provider || "").trim();
            const def = want && withVoices.has(want) ? want : (vs[0]?.provider ?? "");
            setProvider(def);
            emitProvider.current?.(def);
            setLoaded(true);
        });
        return () => {
            cancelled = true;
            if (audioCtxRef.current) { void audioCtxRef.current.close(); audioCtxRef.current = null; }
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const providerOptions = useMemo(
        () => Array.from(new Set(voices.map((v) => v.provider))).filter(Boolean).sort(),
        [voices],
    );
    const shownVoices = useMemo(
        () => voices
            .filter((v) => !provider || v.provider === provider)
            .sort(compareVoicesByPersonaRecommendation(personaType)),
        [voices, provider, personaType],
    );

    const stopPlayback = () => {
        if (audioCtxRef.current) { void audioCtxRef.current.close(); audioCtxRef.current = null; }
        setPlayingId(null);
    };

    const changeProvider = (p: string) => {
        setProvider(p);
        stopPlayback();
        emitProvider.current?.(p);
        if (voiceId && !voices.some((v) => v.id === voiceId && v.provider === p)) onVoiceChange("");
    };

    const playVoice = async (v: VoiceInfo) => {
        if (playingId === v.id) { stopPlayback(); return; }
        stopPlayback();
        setPlayingId(v.id);
        setError(null);
        try {
            const res = await aiOptionsApi.previewVoice({ voice_id: v.id });
            if (!res.audio_base64) throw new Error("No preview audio returned");
            const rawStr = atob(res.audio_base64);
            const view = new DataView(new ArrayBuffer(rawStr.length));
            for (let i = 0; i < rawStr.length; i++) view.setUint8(i, rawStr.charCodeAt(i));
            const samples = new Float32Array(rawStr.length / 4);
            for (let i = 0; i < samples.length; i++) samples[i] = view.getFloat32(i * 4, true);
            const ctx = new AudioContext({ sampleRate: 24000 });
            audioCtxRef.current = ctx;
            const buf = ctx.createBuffer(1, samples.length, 24000);
            buf.getChannelData(0).set(samples);
            const src = ctx.createBufferSource();
            src.buffer = buf;
            src.connect(ctx.destination);
            src.onended = () => {
                if (audioCtxRef.current === ctx) { void ctx.close(); audioCtxRef.current = null; }
                setPlayingId((cur) => (cur === v.id ? null : cur));
            };
            src.start();
        } catch (err) {
            stopPlayback();
            setError(err instanceof Error ? err.message : "Couldn't play that voice");
        }
    };

    return (
        <div>
            <div className="flex items-center justify-between gap-3">
                <Label>Voice</Label>
                {providerOptions.length > 0 && (
                    <div className="flex items-center gap-1.5">
                        <span className="text-xs text-muted-foreground">Provider</span>
                        <select
                            value={provider}
                            onChange={(e) => changeProvider(e.target.value)}
                            className="rounded-md border border-gray-300 dark:border-white/15 bg-white dark:bg-zinc-900 px-2 py-1 text-xs capitalize focus:outline-none focus:ring-2 focus:ring-emerald-500"
                        >
                            {providerOptions.map((p) => (
                                <option key={p} value={p}>{p}</option>
                            ))}
                        </select>
                    </div>
                )}
            </div>
            {error && <p className="mt-1 text-[11px] text-red-600 dark:text-red-400">{error}</p>}
            {!loaded ? (
                <p className="mt-1 text-xs text-muted-foreground">Loading voices…</p>
            ) : shownVoices.length === 0 ? (
                <p className="mt-1 text-xs text-muted-foreground">No voices for this provider.</p>
            ) : (
                <div className="mt-1 grid max-h-56 gap-2 overflow-y-auto pr-1 sm:grid-cols-2">
                    {shownVoices.map((v) => {
                        const rec = isRecommendedVoiceForPersona(v, personaType);
                        const selected = voiceId === v.id;
                        const playing = playingId === v.id;
                        return (
                            <div
                                key={v.id}
                                className={`flex items-center gap-1 rounded-lg border pr-1 text-sm transition ${
                                    selected
                                        ? "border-emerald-500 ring-1 ring-emerald-500 bg-emerald-50 dark:bg-emerald-950/40"
                                        : "border-gray-200 dark:border-white/10 hover:border-gray-300"}`}
                            >
                                <button
                                    type="button"
                                    onClick={() => onVoiceChange(v.id, v.name)}
                                    className="flex min-w-0 flex-1 items-center justify-between gap-2 px-3 py-2 text-left"
                                >
                                    <span className="min-w-0">
                                        <span className="block truncate font-medium text-gray-900 dark:text-zinc-100">{v.name}</span>
                                        <span className="block truncate text-xs text-muted-foreground">
                                            {[v.gender, v.accent].filter(Boolean).join(" · ") || v.language}
                                        </span>
                                    </span>
                                    {rec && (
                                        <span className="ml-1 inline-flex shrink-0 items-center gap-1 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                                            <Sparkles className="h-2.5 w-2.5" /> Rec
                                        </span>
                                    )}
                                </button>
                                <button
                                    type="button"
                                    onClick={() => playVoice(v)}
                                    title={playing ? "Stop" : "Play sample"}
                                    aria-label={playing ? "Stop preview" : "Play voice preview"}
                                    className="shrink-0 rounded-md p-1.5 text-muted-foreground hover:text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-950/40"
                                >
                                    {playing ? <Square className="h-3.5 w-3.5 fill-current" /> : <Play className="h-3.5 w-3.5" />}
                                </button>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

export default VoiceProviderPicker;
