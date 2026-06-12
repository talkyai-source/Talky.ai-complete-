"use client";

/**
 * Presentational controls for the AI Options page. Theme-aware (uses the
 * app's semantic tokens — foreground / muted-foreground / card / border) so
 * it works on both the light and dark themes. A single accent colour
 * (emerald) is used for every interactive highlight — no rainbow.
 */
import { useCallback, useId, useRef } from "react";
import { motion } from "framer-motion";

export const ACCENT = "#10b981"; // single theme accent (emerald)

// ── RadialKnob ────────────────────────────────────────────────
// 270° sweep, 90° gap at the bottom. Angles measured clockwise from up:
// min at -135°, max at +135°. Centre text uses currentColor (theme aware).
function polar(cx: number, cy: number, r: number, angleDeg: number) {
    const a = (angleDeg * Math.PI) / 180;
    return { x: cx + r * Math.sin(a), y: cy - r * Math.cos(a) };
}
function arcPath(cx: number, cy: number, r: number, from: number, to: number) {
    const s = polar(cx, cy, r, from);
    const e = polar(cx, cy, r, to);
    const large = Math.abs(to - from) > 180 ? 1 : 0;
    return `M ${s.x} ${s.y} A ${r} ${r} 0 ${large} 1 ${e.x} ${e.y}`;
}

export function RadialKnob({
    label, value, min, max, step, onChange,
    color = ACCENT, format, hint, size = 112,
}: {
    label: string; value: number; min: number; max: number; step: number;
    onChange: (v: number) => void; color?: string;
    format?: (v: number) => string; hint?: string; size?: number;
}) {
    const ref = useRef<SVGSVGElement | null>(null);
    const cx = 50, cy = 50, r = 38, START = -135, END = 135;
    const frac = max > min ? (value - min) / (max - min) : 0;
    const angle = START + frac * (END - START);

    const clampSnap = useCallback((raw: number) => {
        const stepped = Math.round((raw - min) / step) * step + min;
        return parseFloat(Math.min(max, Math.max(min, stepped)).toFixed(4));
    }, [min, max, step]);

    const setFromPoint = useCallback((clientX: number, clientY: number) => {
        const svg = ref.current;
        if (!svg) return;
        const rect = svg.getBoundingClientRect();
        const dx = clientX - (rect.left + rect.width / 2);
        const dy = clientY - (rect.top + rect.height / 2);
        let deg = (Math.atan2(dx, -dy) * 180) / Math.PI;
        deg = Math.min(END, Math.max(START, deg));
        onChange(clampSnap(min + ((deg - START) / (END - START)) * (max - min)));
    }, [onChange, clampSnap, min, max]);

    const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
        (e.target as Element).setPointerCapture?.(e.pointerId);
        setFromPoint(e.clientX, e.clientY);
    };
    const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
        if (e.buttons !== 1) return;
        setFromPoint(e.clientX, e.clientY);
    };
    const onKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === "ArrowUp" || e.key === "ArrowRight") { e.preventDefault(); onChange(clampSnap(value + step)); }
        else if (e.key === "ArrowDown" || e.key === "ArrowLeft") { e.preventDefault(); onChange(clampSnap(value - step)); }
    };

    const knob = polar(cx, cy, r, angle);
    const display = format ? format(value) : String(value);
    const gid = useId();

    return (
        <div className="flex flex-col items-center gap-1 select-none text-foreground">
            <svg
                ref={ref} width={size} height={size} viewBox="0 0 100 100"
                role="slider" tabIndex={0} aria-valuemin={min} aria-valuemax={max} aria-valuenow={value} aria-label={label}
                onPointerDown={onPointerDown} onPointerMove={onPointerMove} onKeyDown={onKeyDown}
                className="cursor-pointer touch-none text-foreground outline-none"
            >
                <defs>
                    <radialGradient id={`kg-${gid}`} cx="50%" cy="34%" r="75%">
                        <stop offset="0%" stopColor="currentColor" stopOpacity="0.06" />
                        <stop offset="100%" stopColor="currentColor" stopOpacity="0.14" />
                    </radialGradient>
                </defs>
                <path d={arcPath(cx, cy, r, START, END)} fill="none" stroke="currentColor" strokeOpacity={0.14} strokeWidth={7} strokeLinecap="round" />
                <path d={arcPath(cx, cy, r, START, angle)} fill="none" stroke={color} strokeWidth={7} strokeLinecap="round" style={{ filter: `drop-shadow(0 0 4px ${color}99)` }} />
                <circle cx={cx} cy={cy} r={25} fill={`url(#kg-${gid})`} stroke="currentColor" strokeOpacity={0.12} strokeWidth={1} />
                <circle cx={knob.x} cy={knob.y} r={4} fill={color} style={{ filter: `drop-shadow(0 0 4px ${color})` }} />
                <text x={cx} y={cy} textAnchor="middle" fill="currentColor" style={{ fontSize: 15, fontWeight: 700 }}>{display}</text>
                <text x={cx} y={cy + 11} textAnchor="middle" fill="currentColor" fillOpacity={0.5} style={{ fontSize: 6.5, letterSpacing: 0.5 }}>{label.toUpperCase()}</text>
            </svg>
            {hint && <span className="text-[11px] text-muted-foreground">{hint}</span>}
        </div>
    );
}

// ── Segmented control ─────────────────────────────────────────
export function Segmented<T extends string>({
    options, value, onChange, color = ACCENT, size = "md",
}: {
    options: { value: T; label: React.ReactNode }[];
    value: T; onChange: (v: T) => void; color?: string; size?: "sm" | "md";
}) {
    const gid = useId();
    const pad = size === "sm" ? "px-3 py-1.5 text-xs" : "px-4 py-2 text-sm";
    return (
        <div className="inline-flex flex-wrap gap-1 rounded-xl border border-border bg-muted/60 p-1">
            {options.map((opt) => {
                const active = opt.value === value;
                return (
                    <button
                        key={opt.value} type="button" onClick={() => onChange(opt.value)}
                        className={`relative rounded-lg font-medium transition-colors ${pad} ${active ? "text-white" : "text-muted-foreground hover:text-foreground"}`}
                    >
                        {active && (
                            <motion.span
                                layoutId={`seg-${gid}`}
                                transition={{ type: "spring", stiffness: 400, damping: 32 }}
                                className="absolute inset-0 rounded-lg"
                                style={{ background: color, boxShadow: `0 4px 14px -4px ${color}` }}
                            />
                        )}
                        <span className="relative z-10 flex items-center gap-1.5 whitespace-nowrap">{opt.label}</span>
                    </button>
                );
            })}
        </div>
    );
}

// ── Equalizer (voice preview animation) ───────────────────────
export function Equalizer({ active, color = ACCENT, bars = 4 }: { active: boolean; color?: string; bars?: number }) {
    return (
        <div className="flex items-end gap-[2px] h-4" aria-hidden>
            {Array.from({ length: bars }).map((_, i) => (
                <motion.span
                    key={i} className="w-[3px] rounded-full" style={{ background: color }}
                    animate={active ? { height: ["35%", "100%", "50%", "85%", "35%"] } : { height: "32%" }}
                    transition={active ? { duration: 0.9, repeat: Infinity, ease: "easeInOut", delay: i * 0.12 } : { duration: 0.2 }}
                />
            ))}
        </div>
    );
}

// ── Accent detection ──────────────────────────────────────────
// Most voices are American (accent/language = en-US). UK/AU/IN appear mainly
// on ElevenLabs. We only surface buckets that actually exist, and label
// unknowns honestly rather than a vague "Other".
export type AccentBucket = "US" | "UK" | "AU" | "IN" | "INTL";

export function detectAccent(accent?: string, language?: string): AccentBucket {
    const s = `${accent ?? ""} ${language ?? ""}`.toLowerCase();
    if (/\bus\b|amer|en-us|united states/.test(s)) return "US";
    if (/\buk\b|brit|england|en-gb|united kingdom|received pron|welsh|scottish|irish/.test(s)) return "UK";
    if (/austral|en-au|\bau\b|new zealand|en-nz/.test(s)) return "AU";
    if (/\bindia|en-in|\bin\b/.test(s)) return "IN";
    return "INTL";
}

export const ACCENT_META: Record<AccentBucket, { flag: string; label: string }> = {
    US: { flag: "🇺🇸", label: "American" },
    UK: { flag: "🇬🇧", label: "British" },
    AU: { flag: "🇦🇺", label: "Australian" },
    IN: { flag: "🇮🇳", label: "Indian" },
    INTL: { flag: "🌍", label: "International" },
};

export const ACCENT_ORDER: AccentBucket[] = ["US", "UK", "AU", "IN", "INTL"];
