"use client";

/**
 * Presentational controls for the AI Options page.
 *
 * - RadialKnob: an interactive circular "volume knob" (drag / arrow keys /
 *   click-on-arc) used for Temperature and Max Tokens — the physical dial look.
 * - Segmented: a sliding pill segmented control (provider + accent filter).
 * - Equalizer: animated audio bars shown while a voice previews.
 * - accent helpers: normalise free-text voice accents into US / UK / Other.
 *
 * Everything here is purely visual + emits values up; no data fetching.
 */
import { useCallback, useId, useRef } from "react";
import { motion } from "framer-motion";

// ── RadialKnob ────────────────────────────────────────────────
// 270° sweep with a 90° gap at the bottom. Angles are measured clockwise
// from straight up: min at -135°, max at +135°.
function polar(cx: number, cy: number, r: number, angleDeg: number) {
    const a = (angleDeg * Math.PI) / 180;
    return { x: cx + r * Math.sin(a), y: cy - r * Math.cos(a) };
}

function arcPath(cx: number, cy: number, r: number, from: number, to: number) {
    const start = polar(cx, cy, r, from);
    const end = polar(cx, cy, r, to);
    const large = Math.abs(to - from) > 180 ? 1 : 0;
    return `M ${start.x} ${start.y} A ${r} ${r} 0 ${large} 1 ${end.x} ${end.y}`;
}

export function RadialKnob({
    label,
    value,
    min,
    max,
    step,
    onChange,
    color = "#a855f7",
    format,
    hint,
    size = 132,
}: {
    label: string;
    value: number;
    min: number;
    max: number;
    step: number;
    onChange: (v: number) => void;
    color?: string;
    format?: (v: number) => string;
    hint?: string;
    size?: number;
}) {
    const ref = useRef<SVGSVGElement | null>(null);
    const cx = 50, cy = 50, r = 38;
    const START = -135, END = 135;
    const frac = max > min ? (value - min) / (max - min) : 0;
    const angle = START + frac * (END - START);

    const clampSnap = useCallback(
        (raw: number) => {
            const stepped = Math.round((raw - min) / step) * step + min;
            const bounded = Math.min(max, Math.max(min, stepped));
            // kill floating-point dust from the rounding (e.g. 0.30000000004)
            return parseFloat(bounded.toFixed(4));
        },
        [min, max, step],
    );

    const setFromPoint = useCallback(
        (clientX: number, clientY: number) => {
            const svg = ref.current;
            if (!svg) return;
            const rect = svg.getBoundingClientRect();
            const dx = clientX - (rect.left + rect.width / 2);
            const dy = clientY - (rect.top + rect.height / 2);
            let deg = (Math.atan2(dx, -dy) * 180) / Math.PI; // clockwise from up
            deg = Math.min(END, Math.max(START, deg));
            const f = (deg - START) / (END - START);
            onChange(clampSnap(min + f * (max - min)));
        },
        [onChange, clampSnap, min, max],
    );

    const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
        (e.target as Element).setPointerCapture?.(e.pointerId);
        setFromPoint(e.clientX, e.clientY);
    };
    const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
        if (e.buttons !== 1) return;
        setFromPoint(e.clientX, e.clientY);
    };
    const onKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === "ArrowUp" || e.key === "ArrowRight") {
            e.preventDefault();
            onChange(clampSnap(value + step));
        } else if (e.key === "ArrowDown" || e.key === "ArrowLeft") {
            e.preventDefault();
            onChange(clampSnap(value - step));
        }
    };

    const knob = polar(cx, cy, r, angle);
    const display = format ? format(value) : String(value);

    return (
        <div className="flex flex-col items-center gap-1 select-none">
            <svg
                ref={ref}
                width={size}
                height={size}
                viewBox="0 0 100 100"
                role="slider"
                tabIndex={0}
                aria-valuemin={min}
                aria-valuemax={max}
                aria-valuenow={value}
                aria-label={label}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onKeyDown={onKeyDown}
                className="cursor-pointer touch-none outline-none focus-visible:drop-shadow-[0_0_8px_var(--knob)]"
                style={{ ["--knob" as string]: color }}
            >
                <defs>
                    <radialGradient id={`kg-${label}`} cx="50%" cy="35%" r="75%">
                        <stop offset="0%" stopColor="#ffffff" stopOpacity="0.10" />
                        <stop offset="100%" stopColor="#000000" stopOpacity="0.35" />
                    </radialGradient>
                </defs>
                {/* track */}
                <path d={arcPath(cx, cy, r, START, END)} fill="none" stroke="#ffffff18" strokeWidth={7} strokeLinecap="round" />
                {/* value arc */}
                <motion.path
                    d={arcPath(cx, cy, r, START, angle)}
                    fill="none"
                    stroke={color}
                    strokeWidth={7}
                    strokeLinecap="round"
                    initial={false}
                    style={{ filter: `drop-shadow(0 0 4px ${color}aa)` }}
                />
                {/* dished body (3D) */}
                <circle cx={cx} cy={cy} r={26} fill={`url(#kg-${label})`} stroke="#ffffff12" strokeWidth={1} />
                {/* indicator dot */}
                <circle cx={knob.x} cy={knob.y} r={4.2} fill="#fff" style={{ filter: `drop-shadow(0 0 5px ${color})` }} />
                <text x={cx} y={cy - 1} textAnchor="middle" className="fill-white" style={{ fontSize: 15, fontWeight: 700 }}>
                    {display}
                </text>
                <text x={cx} y={cy + 11} textAnchor="middle" style={{ fontSize: 6.5, fill: "#94a3b8", letterSpacing: 0.5 }}>
                    {label.toUpperCase()}
                </text>
            </svg>
            {hint && <span className="text-[11px] text-zinc-500">{hint}</span>}
        </div>
    );
}

// ── Segmented control ─────────────────────────────────────────
export function Segmented<T extends string>({
    options,
    value,
    onChange,
    color = "#a855f7",
    size = "md",
}: {
    options: { value: T; label: React.ReactNode }[];
    value: T;
    onChange: (v: T) => void;
    color?: string;
    size?: "sm" | "md";
}) {
    const gid = useId();
    const pad = size === "sm" ? "px-3 py-1.5 text-xs" : "px-4 py-2 text-sm";
    return (
        <div className="inline-flex flex-wrap gap-1 rounded-xl border border-white/10 bg-black/20 p-1 backdrop-blur">
            {options.map((opt) => {
                const active = opt.value === value;
                return (
                    <button
                        key={opt.value}
                        type="button"
                        onClick={() => onChange(opt.value)}
                        className={`relative rounded-lg font-medium transition-colors ${pad} ${active ? "text-white" : "text-zinc-400 hover:text-zinc-200"}`}
                    >
                        {active && (
                            <motion.span
                                layoutId={`seg-${gid}`}
                                transition={{ type: "spring", stiffness: 400, damping: 32 }}
                                className="absolute inset-0 rounded-lg"
                                style={{ background: `linear-gradient(135deg, ${color}, ${color}99)`, boxShadow: `0 6px 18px -6px ${color}` }}
                            />
                        )}
                        <span className="relative z-10 flex items-center gap-1.5">{opt.label}</span>
                    </button>
                );
            })}
        </div>
    );
}

// ── Equalizer (voice preview animation) ───────────────────────
export function Equalizer({ active, color = "#10b981", bars = 5 }: { active: boolean; color?: string; bars?: number }) {
    return (
        <div className="flex items-end gap-[3px] h-4" aria-hidden>
            {Array.from({ length: bars }).map((_, i) => (
                <motion.span
                    key={i}
                    className="w-[3px] rounded-full"
                    style={{ background: color }}
                    animate={active ? { height: ["35%", "100%", "45%", "85%", "35%"] } : { height: "30%" }}
                    transition={active ? { duration: 0.9, repeat: Infinity, ease: "easeInOut", delay: i * 0.12 } : { duration: 0.2 }}
                />
            ))}
        </div>
    );
}

// ── Accent helpers ────────────────────────────────────────────
export type AccentBucket = "US" | "UK" | "AU" | "Other";

export function normalizeAccent(accent?: string, language?: string): AccentBucket {
    const s = `${accent ?? ""} ${language ?? ""}`.toLowerCase();
    if (/amer|\bus\b|u\.s|united states|en-us/.test(s)) return "US";
    if (/brit|\buk\b|england|english \(uk\)|united kingdom|en-gb|received pron/.test(s)) return "UK";
    if (/austral|\bau\b|en-au/.test(s)) return "AU";
    return "Other";
}

export const ACCENT_META: Record<AccentBucket, { flag: string; label: string }> = {
    US: { flag: "🇺🇸", label: "American" },
    UK: { flag: "🇬🇧", label: "British" },
    AU: { flag: "🇦🇺", label: "Australian" },
    Other: { flag: "🌐", label: "Other" },
};
