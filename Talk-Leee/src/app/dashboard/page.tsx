"use client";

import { useEffect, useLayoutEffect, useMemo, useState, useRef } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { dashboardApi, DashboardSummary, Campaign } from "@/lib/dashboard-api";
import { extendedApi, CallSeriesItem } from "@/lib/extended-api";
import { Clock, Megaphone, ArrowUpRight, Activity, AlertTriangle } from "lucide-react";
import Link from "next/link";
import { motion, animate, useInView, useReducedMotion } from "framer-motion";
import {
    ActivityFeed,
    AlertTimeline,
    DonutChart,
    Heatmap,
    LiveCallsTimeSeriesChart,
    StackedAreaChart,
    type LiveAnomaly,
    type LiveChartMarker,
    type LiveTimeBucket,
    type LiveWindow,
    type DualSeriesPoint,
    type FeedItem,
    type TimelineItem,
} from "@/components/ui/dashboard-charts";
import { HoverTooltip, useHoverTooltip } from "@/components/ui/hover-tooltip";
import { computeMinutesUsageFontPx, MINUTES_USAGE_LAYOUT_SPEC } from "@/lib/minutes-usage-layout.mjs";

function Counter({ value }: { value: number }) {
    const nodeRef = useRef<HTMLSpanElement>(null);
    const isInView = useInView(nodeRef, { once: true, margin: "-10px" });
    
    useEffect(() => {
        const node = nodeRef.current;
        if (!node || !isInView) return;

        const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
        if (reducedMotion) {
            node.textContent = Math.floor(value).toLocaleString();
            return;
        }

        const current = Number((node.textContent || "0").replace(/,/g, "")) || 0;
        
        const controls = animate(current, value, {
            duration: 1.5,
            ease: "easeOut",
            onUpdate: (v) => {
                node.textContent = Math.floor(v).toLocaleString();
            }
        });
        
        return () => controls.stop();
    }, [value, isInView]);

    return <span ref={nodeRef} className="tabular-nums" data-testid="kpi-counter">0</span>;
}

function formatHhMmSs(ms: number) {
    return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function delta(current: number, previous: number) {
    const abs = current - previous;
    const pct = previous === 0 ? (current === 0 ? 0 : 100) : (abs / previous) * 100;
    return { abs, pct };
}

function statusVariant(value: number, thresholds: { green: number; yellow: number }) {
    if (value >= thresholds.green) return "green" as const;
    if (value >= thresholds.yellow) return "yellow" as const;
    return "red" as const;
}

function statusVariantLowerBetter(value: number, thresholds: { green: number; yellow: number }) {
    if (value <= thresholds.green) return "green" as const;
    if (value <= thresholds.yellow) return "yellow" as const;
    return "red" as const;
}

function KpiCard({
    title,
    value,
    valueSuffix,
    deltaAbs,
    deltaPct,
    lastUpdatedMs,
    status,
}: {
    title: string;
    value: number;
    valueSuffix?: string;
    deltaAbs: number;
    deltaPct: number;
    lastUpdatedMs: number;
    status: "green" | "yellow" | "red";
}) {
    const reduceMotion = useReducedMotion();
    const up = deltaAbs >= 0;
    const statusClass =
        status === "green"
            ? "bg-emerald-500"
            : status === "yellow"
                ? "bg-yellow-500"
                : "bg-red-500";
    const motionProps = reduceMotion
        ? {}
        : {
              initial: { opacity: 0, y: 14 },
              animate: { opacity: 1, y: 0 },
              transition: { duration: 0.35 },
              whileHover: { scale: 1.02 },
              whileTap: { scale: 0.99 },
          };

    return (
        <motion.div
            {...motionProps}
            className="h-full"
        >
            <div className="h-full rounded-2xl border border-border bg-background/70 backdrop-blur-sm p-6 shadow-sm transition-[background-color,box-shadow] duration-150 ease-out dark:hover:bg-background dark:hover:shadow-md">
                <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,7.75rem)] sm:grid-cols-[minmax(0,1fr)_minmax(0,9rem)] items-start gap-4">
                    <div className="min-w-0">
                        <div className="flex items-center gap-2">
                            <span className={`w-2.5 h-2.5 rounded-full ${statusClass}`} aria-hidden />
                            <p className="text-sm font-bold tracking-wide uppercase text-muted-foreground leading-none break-words">{title}</p>
                        </div>
                        <p className="mt-3 text-3xl sm:text-4xl font-black tabular-nums text-card-foreground leading-none whitespace-nowrap">
                            <Counter value={value} />
                            {valueSuffix ? <span className="ml-1 text-xl sm:text-2xl font-black text-card-foreground">{valueSuffix}</span> : null}
                        </p>
                    </div>
                    <div className="min-w-0 text-right">
                        <div className="text-xs font-bold text-muted-foreground leading-none truncate">Last updated</div>
                        <div className="mt-1 text-xs font-black tabular-nums text-card-foreground leading-none truncate">{formatHhMmSs(lastUpdatedMs)}</div>
                    </div>
                </div>

                <div className="mt-4 flex items-baseline justify-between gap-4">
                    <div className="flex items-baseline gap-2 text-sm font-semibold leading-none">
                        {up ? (
                            <span className="text-emerald-700 font-black">▲</span>
                        ) : (
                            <span className="text-red-700 font-black">▼</span>
                        )}
                        <span className={up ? "text-emerald-700 font-bold tabular-nums" : "text-red-700 font-bold tabular-nums"}>
                            {Math.abs(deltaPct).toFixed(1)}%
                        </span>
                    </div>
                    <div className="min-w-0 text-xs font-semibold text-muted-foreground tabular-nums leading-none text-right truncate">
                        {deltaAbs >= 0 ? "+" : "−"}
                        {Math.abs(deltaAbs).toLocaleString()} vs previous
                    </div>
                </div>
            </div>
        </motion.div>
    );
}

function useSeriesLiveBuckets(series: CallSeriesItem[]) {
    return useMemo(() => {
        const parsed = series
            .map((item) => {
                const startMs = Date.parse(item.date);
                if (!Number.isFinite(startMs)) return null;
                return {
                    startMs,
                    total: Math.max(0, Math.round(item.total_calls ?? 0)),
                    answered: Math.max(0, Math.round(item.answered ?? 0)),
                    failed: Math.max(0, Math.round(item.failed ?? 0)),
                };
            })
            .filter((item): item is { startMs: number; total: number; answered: number; failed: number } => item !== null)
            .sort((a, b) => a.startMs - b.startMs);

        if (parsed.length === 0) {
            return {
                loading: false,
                error: "",
                lastUpdatedMs: Date.now(),
                buckets: [] as LiveTimeBucket[],
            };
        }

        const inferredBucketMs =
            parsed.length > 1 ? Math.max(60_000, parsed[parsed.length - 1]!.startMs - parsed[parsed.length - 2]!.startMs) : 86_400_000;

        const buckets: LiveTimeBucket[] = parsed.map((item) => ({
            startMs: item.startMs,
            endMs: item.startMs + inferredBucketMs,
            total: item.total,
            answered: Math.min(item.total, item.answered),
            failed: Math.min(item.total, item.failed),
            avgDurationSec: null,
        }));

        return {
            loading: false,
            error: "",
            lastUpdatedMs: buckets[buckets.length - 1]!.endMs,
            buckets,
        };
    }, [series]);
}

function toDatetimeLocalValue(ms: number) {
    const d = new Date(ms);
    const pad = (n: number) => String(n).padStart(2, "0");
    const yyyy = d.getFullYear();
    const mm = pad(d.getMonth() + 1);
    const dd = pad(d.getDate());
    const hh = pad(d.getHours());
    const mi = pad(d.getMinutes());
    return `${yyyy}-${mm}-${dd}T${hh}:${mi}`;
}

function fromDatetimeLocalValue(value: string) {
    const ms = Date.parse(value);
    return Number.isFinite(ms) ? ms : null;
}

function OutcomePieChart({
    segments,
}: {
    segments: Array<{ label: string; value: number; color: string }>;
}) {
    const tooltip = useHoverTooltip();
    const total = Math.max(1, segments.reduce((a, s) => a + s.value, 0));
    const size = 220;
    const r = 84;
    const cx = size / 2;
    const cy = size / 2;

    const polar = (angleRad: number, radius: number) => ({
        x: cx + Math.cos(angleRad) * radius,
        y: cy + Math.sin(angleRad) * radius,
    });

    const arc = (start: number, end: number) => {
        const startPt = polar(start, r);
        const endPt = polar(end, r);
        const large = end - start > Math.PI ? 1 : 0;
        return `M ${cx} ${cy} L ${startPt.x} ${startPt.y} A ${r} ${r} 0 ${large} 1 ${endPt.x} ${endPt.y} Z`;
    };

    const paths = segments.map((s, idx) => {
        const prefix = segments.slice(0, idx).reduce((a, item) => a + item.value, 0);
        const start = -Math.PI / 2 + (prefix / total) * 2 * Math.PI;
        const end = start + (s.value / total) * 2 * Math.PI;
        const d = arc(start, end);
        return { ...s, d, startAngle: start, endAngle: end };
    });

    return (
        <div className="relative">
            <HoverTooltip state={tooltip.state} />
            <svg viewBox={`0 0 ${size} ${size}`} className="w-full max-w-[260px] h-auto mx-auto">
                {paths.map((p) => (
                    <path
                        key={p.label}
                        d={p.d}
                        fill={p.color}
                        onMouseEnter={(e) => tooltip.show(e.clientX, e.clientY, (
                            <div className="space-y-1">
                                <div className="text-sm font-black text-gray-900">{p.label}</div>
                                <div className="text-sm font-black tabular-nums text-gray-900">{p.value.toLocaleString()}</div>
                                <div className="text-xs font-semibold text-gray-600">{((p.value / total) * 100).toFixed(1)}%</div>
                            </div>
                        ))}
                        onMouseMove={(e) => tooltip.show(e.clientX, e.clientY, (
                            <div className="space-y-1">
                                <div className="text-sm font-black text-gray-900">{p.label}</div>
                                <div className="text-sm font-black tabular-nums text-gray-900">{p.value.toLocaleString()}</div>
                                <div className="text-xs font-semibold text-gray-600">{((p.value / total) * 100).toFixed(1)}%</div>
                            </div>
                        ))}
                        onMouseLeave={() => tooltip.hide()}
                    />
                ))}
                <circle cx={cx} cy={cy} r={48} fill="rgba(255,255,255,0.7)" />
                <text x={cx} y={cy - 6} textAnchor="middle" style={{ fontWeight: 900, fontSize: 14, fill: "#111827" }}>
                    Outcomes
                </text>
                <text x={cx} y={cy + 14} textAnchor="middle" style={{ fontWeight: 900, fontSize: 14, fill: "#111827" }}>
                    {segments.reduce((a, s) => a + s.value, 0).toLocaleString()}
                </text>
            </svg>
        </div>
    );
}

function seeded01(seed: number) {
    const x = Math.sin(seed) * 10_000;
    return x - Math.floor(x);
}

function StatusStackedBarsChart({
    buckets,
    height = 240,
    onHoverBucket,
    peakBands = [],
    events = [],
}: {
    buckets: LiveTimeBucket[];
    height?: number;
    onHoverBucket?: (b: LiveTimeBucket | null) => void;
    peakBands?: Array<{ startMs: number; endMs: number; label: string }>;
    events?: Array<{ ms: number; label: string }>;
}) {
    const tooltip = useHoverTooltip();
    const svgRef = useRef<SVGSVGElement | null>(null);
    const w = 980;
    const left = 64;
    const right = 20;
    const top = 20;
    const bottom = 44;
    const plotW = w - left - right;
    const plotH = height - top - bottom;

    const data = useMemo(() => {
        const maxBars = 120;
        const step = Math.max(1, Math.ceil(buckets.length / maxBars));
        return step === 1 ? buckets : buckets.filter((_, i) => i % step === 0 || i === buckets.length - 1);
    }, [buckets]);

    const maxVal = useMemo(() => {
        return Math.max(
            1,
            ...data.map((b) => (typeof b.total === "number" ? b.total : 0))
        );
    }, [data]);

    const xFor = (i: number) => {
        if (data.length <= 1) return left + plotW / 2;
        return left + (i * plotW) / (data.length - 1);
    };

    const barW = data.length > 0 ? Math.max(3, Math.min(14, plotW / data.length)) : 6;

    const domain = useMemo(() => {
        if (data.length === 0) return { startMs: 0, endMs: 0 };
        const startMs = data[0]?.startMs ?? 0;
        const endMs = data[data.length - 1]?.endMs ?? 0;
        return { startMs, endMs };
    }, [data]);

    const xForMs = (ms: number) => {
        if (domain.endMs <= domain.startMs) return left;
        const t = Math.max(0, Math.min(1, (ms - domain.startMs) / (domain.endMs - domain.startMs)));
        return left + t * plotW;
    };

    return (
        <div className="relative">
            <HoverTooltip state={tooltip.state} />
            <svg
                ref={svgRef}
                viewBox={`0 0 ${w} ${height}`}
                className="w-full"
                style={{ height }}
                onMouseLeave={() => {
                    tooltip.hide();
                    onHoverBucket?.(null);
                }}
                onMouseMove={(e) => {
                    const svg = svgRef.current;
                    if (!svg || data.length === 0) return;
                    const rect = svg.getBoundingClientRect();
                    const x = e.clientX - rect.left;
                    const t = Math.max(0, Math.min(1, (x - left) / Math.max(1, plotW)));
                    const idx = Math.max(0, Math.min(data.length - 1, Math.round(t * (data.length - 1))));
                    const b = data[idx];
                    if (!b) return;
                    onHoverBucket?.(b);
                    const total = typeof b.total === "number" ? b.total : 0;
                    const answered = typeof b.answered === "number" ? b.answered : 0;
                    const failed = typeof b.failed === "number" ? b.failed : 0;
                    const inProgress = Math.max(0, total - answered - failed);
                    const successRate = total > 0 ? (answered / total) * 100 : 0;
                    tooltip.show(e.clientX, e.clientY, (
                        <div className="space-y-2">
                            <div className="text-sm font-black text-gray-900">
                                {new Date(b.startMs).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}–{new Date(b.endMs).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                            </div>
                            <div className="space-y-1.5">
                                <div className="flex items-center justify-between gap-8 text-sm">
                                    <span className="font-semibold text-gray-700">Active call count</span>
                                    <span className="font-black tabular-nums text-gray-900">{Math.max(0, Math.round(total * 0.18 + 6))}</span>
                                </div>
                                <div className="flex items-center justify-between gap-8 text-sm">
                                    <span className="font-semibold text-gray-700">Queue size</span>
                                    <span className="font-black tabular-nums text-gray-900">{Math.max(0, Math.round(total * 0.12 + 4))}</span>
                                </div>
                                <div className="flex items-center justify-between gap-8 text-sm">
                                    <span className="font-semibold text-gray-700">Completed/failed totals</span>
                                    <span className="font-black tabular-nums text-gray-900">{answered.toLocaleString()}/{failed.toLocaleString()}</span>
                                </div>
                                <div className="flex items-center justify-between gap-8 text-sm">
                                    <span className="font-semibold text-gray-700">Current success rate</span>
                                    <span className="font-black tabular-nums text-gray-900">{successRate.toFixed(1)}%</span>
                                </div>
                            </div>
                            <div className="text-[11px] font-semibold text-gray-600">
                                Status: Answered {answered.toLocaleString()}, Failed {failed.toLocaleString()}, In progress {inProgress.toLocaleString()}
                            </div>
                        </div>
                    ));
                }}
            >
                <rect x={left} y={top} width={plotW} height={plotH} fill="rgba(0,0,0,0.02)" rx={14} />

                {peakBands.map((b, i) => {
                    const x1 = xForMs(b.startMs);
                    const x2 = xForMs(b.endMs);
                    return <rect key={`peak-${i}`} x={x1} y={top} width={Math.max(0, x2 - x1)} height={plotH} fill="rgba(234,179,8,0.18)" />;
                })}

                {data.map((b, i) => {
                    const total = typeof b.total === "number" ? b.total : 0;
                    const answered = typeof b.answered === "number" ? b.answered : 0;
                    const failed = typeof b.failed === "number" ? b.failed : 0;
                    const inProgress = Math.max(0, total - answered - failed);
                    const x = xFor(i) - barW / 2;
                    const toH = (v: number) => (v / maxVal) * plotH;
                    const hAnswered = toH(answered);
                    const hFailed = toH(failed);
                    const hInProgress = toH(inProgress);
                    const yAnswered = top + plotH - hAnswered;
                    const yFailed = yAnswered - hFailed;
                    const yInProgress = yFailed - hInProgress;
                    return (
                        <g key={b.startMs}>
                            <rect x={x} y={yInProgress} width={barW} height={hInProgress} fill="rgba(59,130,246,0.7)" />
                            <rect x={x} y={yFailed} width={barW} height={hFailed} fill="rgba(239,68,68,0.75)" />
                            <rect x={x} y={yAnswered} width={barW} height={hAnswered} fill="rgba(16,185,129,0.75)" />
                        </g>
                    );
                })}

                {events.map((ev, i) => {
                    const x = xForMs(ev.ms);
                    const y = top + 14;
                    return (
                        <g
                            key={`ev-${i}`}
                            onMouseEnter={(e) => {
                                const clientX = (e as unknown as MouseEvent).clientX;
                                const clientY = (e as unknown as MouseEvent).clientY;
                                tooltip.show(clientX, clientY, (
                                    <div className="space-y-1">
                                        <div className="text-sm font-black text-gray-900">System event</div>
                                        <div className="text-xs font-semibold text-gray-600">{ev.label}</div>
                                    </div>
                                ));
                            }}
                            onMouseLeave={() => tooltip.hide()}
                        >
                            <circle cx={x} cy={y} r={8} fill="rgba(17,24,39,0.12)" stroke="rgba(17,24,39,0.25)" />
                            <text x={x} y={y + 4} textAnchor="middle" style={{ fontWeight: 900, fontSize: 12, fill: "#111827" }}>
                                !
                            </text>
                        </g>
                    );
                })}

                <text x={left + plotW / 2} y={height - 12} textAnchor="middle" style={{ fontWeight: 700, fontSize: 12, fill: "#6B7280" }}>
                    Time
                </text>
            </svg>
        </div>
    );
}

function CampaignLinesChart({
    buckets,
    campaigns,
    enabled,
    height = 240,
    peakBands = [],
    events = [],
}: {
    buckets: LiveTimeBucket[];
    campaigns: Array<{ id: string; label: string; color: string; weight: number }>;
    enabled: Record<string, boolean>;
    height?: number;
    peakBands?: Array<{ startMs: number; endMs: number; label: string }>;
    events?: Array<{ ms: number; label: string }>;
}) {
    const tooltip = useHoverTooltip();
    const svgRef = useRef<SVGSVGElement | null>(null);
    const w = 980;
    const left = 64;
    const right = 20;
    const top = 20;
    const bottom = 44;
    const plotW = w - left - right;
    const plotH = height - top - bottom;

    const data = useMemo(() => {
        const maxPoints = 240;
        const step = Math.max(1, Math.ceil(buckets.length / maxPoints));
        return step === 1 ? buckets : buckets.filter((_, i) => i % step === 0 || i === buckets.length - 1);
    }, [buckets]);

    const series = useMemo(() => {
        const active = campaigns.filter((c) => enabled[c.id] !== false);
        const points = active.map((c, ci) => {
            return {
                ...c,
                values: data.map((b) => {
                    const total = typeof b.total === "number" ? b.total : 0;
                    const noise = (seeded01(b.startMs * 0.00001 + ci * 91.7) - 0.5) * 0.16;
                    return Math.max(0, total * c.weight * (1 + noise));
                }),
            };
        });
        const maxVal = Math.max(1, ...points.flatMap((p) => p.values));
        return { active, points, maxVal };
    }, [campaigns, data, enabled]);

    const forecast = useMemo(() => {
        const totals = data.map((b) => (typeof b.total === "number" ? b.total : 0));
        const avg6 = (idx: number) => {
            const window = totals.slice(Math.max(0, idx - 5), idx + 1);
            if (window.length === 0) return 0;
            return window.reduce((a, v) => a + v, 0) / window.length;
        };
        return totals.map((_, i) => {
            const prev = avg6(Math.max(0, i - 1));
            const cur = avg6(i);
            const slope = cur - prev;
            return Math.max(0, cur + slope * 0.9);
        });
    }, [data]);

    const xFor = (i: number) => {
        if (data.length <= 1) return left + plotW / 2;
        return left + (i * plotW) / (data.length - 1);
    };
    const yFor = (v: number) => top + plotH - (Math.max(0, v) / series.maxVal) * plotH;

    const domain = useMemo(() => {
        if (data.length === 0) return { startMs: 0, endMs: 0 };
        const startMs = data[0]?.startMs ?? 0;
        const endMs = data[data.length - 1]?.endMs ?? 0;
        return { startMs, endMs };
    }, [data]);

    const xForMs = (ms: number) => {
        if (domain.endMs <= domain.startMs) return left;
        const t = Math.max(0, Math.min(1, (ms - domain.startMs) / (domain.endMs - domain.startMs)));
        return left + t * plotW;
    };

    return (
        <div className="relative">
            <HoverTooltip state={tooltip.state} />
            <svg
                ref={svgRef}
                viewBox={`0 0 ${w} ${height}`}
                className="w-full"
                style={{ height }}
                onMouseMove={(e) => {
                    const svg = svgRef.current;
                    if (!svg || data.length === 0 || series.points.length === 0) return;
                    const rect = svg.getBoundingClientRect();
                    const x = e.clientX - rect.left;
                    const t = Math.max(0, Math.min(1, (x - left) / Math.max(1, plotW)));
                    const idx = Math.max(0, Math.min(data.length - 1, Math.round(t * (data.length - 1))));
                    const b = data[idx];
                    if (!b) return;
                    const rows = series.points.map((p) => {
                        const v = p.values[idx] ?? 0;
                        return (
                            <div key={p.id} className="flex items-center justify-between gap-8 text-sm">
                                <span className="font-semibold" style={{ color: p.color }}>{p.label}</span>
                                <span className="font-black tabular-nums text-gray-900">{Math.round(v).toLocaleString()}</span>
                            </div>
                        );
                    });
                    tooltip.show(e.clientX, e.clientY, (
                        <div className="space-y-2">
                            <div className="text-sm font-black text-gray-900">
                                {new Date(b.startMs).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                            </div>
                            <div className="space-y-1">{rows}</div>
                        </div>
                    ));
                }}
                onMouseLeave={() => tooltip.hide()}
            >
                <rect x={left} y={top} width={plotW} height={plotH} fill="rgba(0,0,0,0.02)" rx={14} />

                {peakBands.map((b, i) => {
                    const x1 = xForMs(b.startMs);
                    const x2 = xForMs(b.endMs);
                    return <rect key={`peak-${i}`} x={x1} y={top} width={Math.max(0, x2 - x1)} height={plotH} fill="rgba(234,179,8,0.18)" />;
                })}

                {series.points.map((p) => {
                    const pts = p.values.map((v, i) => ({ x: xFor(i), y: yFor(v) }));
                    const d = `M ${pts[0]?.x ?? left} ${pts[0]?.y ?? top + plotH}` + pts.slice(1).map((pt) => ` L ${pt.x} ${pt.y}`).join("");
                    return (
                        <path key={p.id} d={d} fill="none" stroke={p.color} strokeWidth={2.25} strokeLinejoin="round" strokeLinecap="round" />
                    );
                })}

                {data.length > 1 ? (
                    <path
                        d={
                            `M ${xFor(0)} ${yFor(forecast[0] ?? 0)}` +
                            forecast.slice(1).map((v, i) => ` L ${xFor(i + 1)} ${yFor(v)}`).join("")
                        }
                        fill="none"
                        stroke="rgba(17,24,39,0.6)"
                        strokeWidth={2}
                        strokeDasharray="6 6"
                        strokeLinejoin="round"
                        strokeLinecap="round"
                    />
                ) : null}

                {events.map((ev, i) => {
                    const x = xForMs(ev.ms);
                    const y = top + 14;
                    return (
                        <g
                            key={`ev-${i}`}
                            onMouseEnter={(e) => {
                                const clientX = (e as unknown as MouseEvent).clientX;
                                const clientY = (e as unknown as MouseEvent).clientY;
                                tooltip.show(clientX, clientY, (
                                    <div className="space-y-1">
                                        <div className="text-sm font-black text-gray-900">System event</div>
                                        <div className="text-xs font-semibold text-gray-600">{ev.label}</div>
                                    </div>
                                ));
                            }}
                            onMouseLeave={() => tooltip.hide()}
                        >
                            <circle cx={x} cy={y} r={8} fill="rgba(17,24,39,0.12)" stroke="rgba(17,24,39,0.25)" />
                            <text x={x} y={y + 4} textAnchor="middle" style={{ fontWeight: 900, fontSize: 12, fill: "#111827" }}>
                                !
                            </text>
                        </g>
                    );
                })}

                <text x={left + plotW / 2} y={height - 12} textAnchor="middle" style={{ fontWeight: 700, fontSize: 12, fill: "#6B7280" }}>
                    Time
                </text>
            </svg>
        </div>
    );
}

export default function DashboardPage() {
    const [summary, setSummary] = useState<DashboardSummary | null>(null);
    const [liveSummary, setLiveSummary] = useState<DashboardSummary | null>(null);
    const [campaigns, setCampaigns] = useState<Campaign[]>([]);
    const [series, setSeries] = useState<CallSeriesItem[]>([]);
    const [liveBars, setLiveBars] = useState<DualSeriesPoint[]>([]);
    const [streamStatus, setStreamStatus] = useState<"connecting" | "connected" | "retrying" | "offline">("connecting");
    const [, setStreamFailures] = useState(0);
    const [, setStreamLatencyMs] = useState<number | null>(null);
    const [, setStreamError] = useState("");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const lastValidBarsRef = useRef<DualSeriesPoint[]>([]);
    const lastValidSummaryRef = useRef<DashboardSummary | null>(null);
    const lastAppliedTsRef = useRef<number>(0);
    const pendingBarsRef = useRef<{ ts: number; points: DualSeriesPoint[] } | null>(null);
    const pendingSummaryRef = useRef<{ ts: number; summary: DashboardSummary } | null>(null);

    const [liveRangeHours, setLiveRangeHours] = useState<1 | 4 | 8 | 24>(4);
    const [callRangeMode, setCallRangeMode] = useState<"preset" | "custom">("preset");
    const [customStartMs, setCustomStartMs] = useState<number | null>(null);
    const [customEndMs, setCustomEndMs] = useState<number | null>(null);
    const [notes] = useState<Array<{ ms: number; label: string }>>([]);
    const [activeBucket, setActiveBucket] = useState<LiveTimeBucket | null>(null);
    const [callStatsView, setCallStatsView] = useState<"status" | "campaigns" | "outcomes">("status");

    const sim = useSeriesLiveBuckets(series);

    const activeRange = useMemo(() => {
        const end = callRangeMode === "custom" ? (customEndMs ?? sim.lastUpdatedMs) : sim.lastUpdatedMs;
        const presetMs = liveRangeHours * 60 * 60 * 1000;
        const start = callRangeMode === "custom" ? (customStartMs ?? end - presetMs) : end - presetMs;
        const startMs = Math.min(start, end);
        const endMs = Math.max(start, end);
        return { startMs, endMs };
    }, [callRangeMode, customEndMs, customStartMs, liveRangeHours, sim.lastUpdatedMs]);

    useEffect(() => {
        if (callRangeMode !== "custom") return;
        if (customStartMs !== null && customEndMs !== null) return;
        const stepMs = 15 * 60 * 1000;
        const snap = (ms: number) => Math.round(ms / stepMs) * stepMs;
        const end = snap(sim.lastUpdatedMs);
        const start = snap(end - liveRangeHours * 60 * 60 * 1000);
        setCustomEndMs(end);
        setCustomStartMs(start);
    }, [callRangeMode, customEndMs, customStartMs, liveRangeHours, sim.lastUpdatedMs]);

    const liveBucketsBase = useMemo(() => {
        if (sim.buckets.length === 0) return [];
        const startMs = activeRange.startMs;
        const endMs = activeRange.endMs;
        return sim.buckets.filter((b) => b.endMs >= startMs && b.startMs <= endMs);
    }, [activeRange.endMs, activeRange.startMs, sim.buckets]);

    const previousBucketsBase = useMemo(() => {
        if (sim.buckets.length === 0) return [];
        const rangeMs = Math.max(1, activeRange.endMs - activeRange.startMs);
        const endMs = activeRange.startMs;
        const startMs = endMs - rangeMs;
        return sim.buckets.filter((b) => b.endMs >= startMs && b.startMs <= endMs);
    }, [activeRange.endMs, activeRange.startMs, sim.buckets]);

    const liveBuckets = useMemo(() => {
        const totals = liveBucketsBase.map((b) => b.total).filter((v): v is number => typeof v === "number");
        const avg6 = (idx: number) => {
            const window = totals.slice(Math.max(0, idx - 5), idx + 1);
            if (window.length === 0) return 0;
            return window.reduce((a, v) => a + v, 0) / window.length;
        };
        return liveBucketsBase.map((b, i) => {
            if (typeof b.total !== "number") return { ...b, forecastTotal: null };
            const prev = avg6(Math.max(0, i - 1));
            const cur = avg6(i);
            const slope = cur - prev;
            return { ...b, forecastTotal: Math.max(0, Math.round(cur + slope * 0.9)) };
        });
    }, [liveBucketsBase]);

    const { kpis, markers, maintenanceWindows, peakBands, anomalies, outcomes, hoverStats } = useMemo(() => {
        const sum = (items: LiveTimeBucket[], key: "total" | "answered" | "failed") =>
            items.reduce((a, b) => a + (typeof b[key] === "number" ? (b[key] as number) : 0), 0);

        const currentTotal = sum(liveBuckets, "total");
        const currentAnswered = sum(liveBuckets, "answered");
        const currentFailed = sum(liveBuckets, "failed");
        const currentSuccessRate = currentTotal > 0 ? (currentAnswered / currentTotal) * 100 : 0;

        const prevTotal = sum(previousBucketsBase, "total");
        const prevAnswered = sum(previousBucketsBase, "answered");
        const prevSuccessRate = prevTotal > 0 ? (prevAnswered / prevTotal) * 100 : 0;

        const currentAvgDurationSec = (() => {
            let totalWeight = 0;
            let acc = 0;
            for (const b of liveBuckets) {
                if (typeof b.total !== "number" || typeof b.avgDurationSec !== "number") continue;
                totalWeight += b.total;
                acc += b.avgDurationSec * b.total;
            }
            return totalWeight > 0 ? acc / totalWeight : 0;
        })();

        const prevAvgDurationSec = (() => {
            let totalWeight = 0;
            let acc = 0;
            for (const b of previousBucketsBase) {
                if (typeof b.total !== "number" || typeof b.avgDurationSec !== "number") continue;
                totalWeight += b.total;
                acc += b.avgDurationSec * b.total;
            }
            return totalWeight > 0 ? acc / totalWeight : 0;
        })();

        const totalDelta = delta(currentTotal, prevTotal);
        const successDelta = delta(currentSuccessRate, prevSuccessRate);
        const failedPct = currentTotal > 0 ? (currentFailed / currentTotal) * 100 : 0;

        const currentActiveCalls = Math.max(0, Math.round((liveBuckets[liveBuckets.length - 1]?.total ?? 0) * 0.18 + 6));
        const prevActiveCalls = Math.max(0, Math.round((previousBucketsBase[previousBucketsBase.length - 1]?.total ?? 0) * 0.18 + 6));
        const activeDelta = delta(currentActiveCalls, prevActiveCalls);

        const avgDurDelta = delta(currentAvgDurationSec, prevAvgDurationSec);

        const kpis = [
            {
                title: "Total Calls",
                value: currentTotal,
                valueSuffix: "",
                deltaAbs: totalDelta.abs,
                deltaPct: totalDelta.pct,
                status: statusVariant(currentTotal, { green: 500, yellow: 250 }),
            },
            {
                title: "Success Rate",
                value: Math.round(currentSuccessRate * 10) / 10,
                valueSuffix: "%",
                deltaAbs: successDelta.abs,
                deltaPct: successDelta.pct,
                status: statusVariant(currentSuccessRate, { green: 92, yellow: 85 }),
            },
            {
                title: "Active Calls",
                value: currentActiveCalls,
                valueSuffix: "",
                deltaAbs: activeDelta.abs,
                deltaPct: activeDelta.pct,
                status: statusVariant(currentActiveCalls, { green: 30, yellow: 18 }),
            },
            {
                title: "Avg Duration",
                value: Math.round(currentAvgDurationSec),
                valueSuffix: "s",
                deltaAbs: avgDurDelta.abs,
                deltaPct: avgDurDelta.pct,
                status: statusVariantLowerBetter(currentAvgDurationSec, { green: 70, yellow: 95 }),
            },
        ] as const;

        const now = activeRange.endMs;
        const windowStart = activeRange.startMs;
        const rangeMs = Math.max(1, now - windowStart);

        const baseMarkers: LiveChartMarker[] = [
            { ms: now - Math.round(rangeMs * 0.82), label: "Campaign A start", kind: "campaign-start" },
            { ms: now - Math.round(rangeMs * 0.46), label: "Campaign A end", kind: "campaign-end" },
            { ms: now - Math.round(rangeMs * 0.68), label: "Campaign B start", kind: "campaign-start" },
            { ms: now - Math.round(rangeMs * 0.22), label: "Campaign B end", kind: "campaign-end" },
            { ms: now - Math.round(rangeMs * 0.12), label: "Deploy", kind: "event" },
        ];
        const noteMarkers: LiveChartMarker[] = notes.map((n) => ({ ms: n.ms, label: n.label, kind: "note" }));
        const markers: LiveChartMarker[] = [...baseMarkers, ...noteMarkers].filter((m) => m.ms >= windowStart && m.ms <= now);

        const maintenanceWindows: LiveWindow[] = [
            { startMs: now - Math.round(rangeMs * 0.6), endMs: now - Math.round(rangeMs * 0.55), label: "Maintenance" },
        ].filter((w) => w.endMs >= windowStart && w.startMs <= now);

        const peaks = liveBuckets
            .map((b) => ({ b, v: typeof b.total === "number" ? b.total : -1 }))
            .filter((x) => x.v >= 0)
            .sort((a, b) => b.v - a.v)
            .slice(0, 2)
            .map((x) => ({ startMs: x.b.startMs, endMs: x.b.endMs, label: "Peak" }));

        const anomalies: LiveAnomaly[] = [];
        const recent: number[] = [];
        for (const b of liveBuckets) {
            if (typeof b.total === "number") {
                recent.push(b.total);
                const window = recent.slice(-10);
                const avg = window.reduce((a, v) => a + v, 0) / Math.max(1, window.length);
                if (avg > 0 && b.total > avg * 1.7) anomalies.push({ ms: b.startMs, kind: "spike" });
                if (avg > 0 && b.total < avg * 0.5) anomalies.push({ ms: b.startMs, kind: "drop" });
            }
        }

        const peakBands = peaks.map((p) => ({ startMs: p.startMs, endMs: p.endMs, label: p.label })) satisfies LiveWindow[];

        const outcomes = (() => {
            const completed = Math.max(0, Math.round(currentAnswered * 0.72));
            const voicemail = Math.max(0, Math.round(currentAnswered * 0.11));
            const busy = Math.max(0, Math.round(currentTotal * 0.05));
            const noAnswer = Math.max(0, Math.round(currentTotal * 0.07));
            const networkError = Math.max(0, currentFailed - busy - noAnswer);
            return [
                { label: "Completed", value: completed, color: "#10B981" },
                { label: "Voicemail", value: voicemail, color: "#3B82F6" },
                { label: "Busy", value: busy, color: "#F59E0B" },
                { label: "No Answer", value: noAnswer, color: "#A855F7" },
                { label: "Network Error", value: networkError, color: "#EF4444" },
            ];
        })();

        const bucket = activeBucket ?? liveBuckets[liveBuckets.length - 1] ?? null;
        const bucketTotal = bucket && typeof bucket.total === "number" ? bucket.total : 0;
        const bucketAnswered = bucket && typeof bucket.answered === "number" ? bucket.answered : 0;
        const bucketFailed = bucket && typeof bucket.failed === "number" ? bucket.failed : 0;
        const bucketInProgress = Math.max(0, bucketTotal - bucketAnswered - bucketFailed);

        const hoverStats = {
            activeCalls: Math.max(0, Math.round(bucketTotal * 0.18 + 6)),
            queueSize: Math.max(0, Math.round(bucketTotal * 0.12 + 4)),
            completedTotal: currentAnswered,
            failedTotal: currentFailed,
            successRate: currentTotal > 0 ? (currentAnswered / currentTotal) * 100 : 0,
            inProgress: bucketInProgress,
            failedPct,
        };

        return { kpis, markers, maintenanceWindows, peakBands, anomalies: anomalies.slice(-40), outcomes, hoverStats };
    }, [activeBucket, activeRange.endMs, activeRange.startMs, liveBuckets, notes, previousBucketsBase]);

    useEffect(() => {
        loadData();
    }, []);

    async function loadData() {
        try {
            setLoading(true);
            const [summaryData, campaignsData, analytics] = await Promise.all([
                dashboardApi.getDashboardSummary(),
                dashboardApi.listCampaigns(),
                extendedApi.getCallAnalytics(),
            ]);
            setSummary(summaryData);
            setLiveSummary(summaryData);
            setCampaigns(campaignsData.campaigns);
            setSeries(analytics.series);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load dashboard");
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => {
        if (!summary) return;
        if (streamStatus === "connected") return;
        setLiveSummary(summary);
    }, [summary, streamStatus]);

    useEffect(() => {
        if (series.length === 0) {
            setLiveBars([]);
            return;
        }
        if (streamStatus === "connected") return;
        const initial: DualSeriesPoint[] = series.slice(-12).map((item) => ({
            label: new Date(item.date).toLocaleDateString([], { month: "short", day: "numeric" }),
            a: Math.max(0, Math.round(item.answered ?? 0)),
            b: Math.max(0, Math.round(item.failed ?? 0)),
        }));
        setLiveBars(initial);
    }, [series, streamStatus]);

    useEffect(() => {
        if (liveBars.length > 0) lastValidBarsRef.current = liveBars;
    }, [liveBars]);

    useEffect(() => {
        if (liveSummary) lastValidSummaryRef.current = liveSummary;
    }, [liveSummary]);

    useEffect(() => {
        const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
        const isString = (v: unknown): v is string => typeof v === "string";

        const isDualSeriesPoint = (p: unknown): p is DualSeriesPoint => {
            if (!p || typeof p !== "object") return false;
            const obj = p as Record<string, unknown>;
            return isString(obj.label) && isNum(obj.a) && isNum(obj.b);
        };

        const isSummary = (s: unknown): s is DashboardSummary => {
            if (!s || typeof s !== "object") return false;
            const obj = s as Record<string, unknown>;
            return (
                isNum(obj.total_calls) &&
                isNum(obj.answered_calls) &&
                isNum(obj.failed_calls) &&
                isNum(obj.minutes_used) &&
                isNum(obj.minutes_remaining) &&
                isNum(obj.active_campaigns)
            );
        };

        const getWsUrl = () => {
            const envUrl = process.env.NEXT_PUBLIC_DASHBOARD_WS_URL;
            if (envUrl && envUrl.trim().length > 0) return envUrl.trim();
            const proto = window.location.protocol === "https:" ? "wss" : "ws";
            return `${proto}://${window.location.host}/ws`;
        };

        let ws: WebSocket | null = null;
        let stopped = false;
        let heartbeatId: number | null = null;
        let commitId: number | null = null;
        let retryTimerId: number | null = null;
        let attempt = 0;
        let lastSeenTs = 0;

        const cleanup = () => {
            if (heartbeatId) window.clearInterval(heartbeatId);
            if (commitId) window.clearInterval(commitId);
            if (retryTimerId) window.clearTimeout(retryTimerId);
            heartbeatId = null;
            commitId = null;
            retryTimerId = null;
            if (ws) ws.close();
            ws = null;
        };

        const commitPending = () => {
            const nextBars = pendingBarsRef.current;
            const nextSummary = pendingSummaryRef.current;
            const next = nextBars?.ts ?? nextSummary?.ts ?? 0;
            if (next <= lastAppliedTsRef.current) return;

            window.requestAnimationFrame(() => {
                let appliedTs = lastAppliedTsRef.current;
                const bars = pendingBarsRef.current;
                if (bars && bars.ts >= appliedTs) {
                    setLiveBars(bars.points);
                    pendingBarsRef.current = null;
                    appliedTs = Math.max(appliedTs, bars.ts);
                }

                const summary = pendingSummaryRef.current;
                if (summary && summary.ts >= appliedTs) {
                    setLiveSummary(summary.summary);
                    pendingSummaryRef.current = null;
                    appliedTs = Math.max(appliedTs, summary.ts);
                }

                lastAppliedTsRef.current = appliedTs;
            });
        };

        const scheduleReconnect = () => {
            if (stopped) return;
            attempt += 1;
            if (attempt > 3) {
                setStreamStatus("offline");
                return;
            }
            setStreamStatus("retrying");
            const delayMs = 250 * Math.pow(2, attempt);
            retryTimerId = window.setTimeout(() => {
                connect();
            }, delayMs);
        };

        const handleCorrupt = (message: string) => {
            setStreamFailures((n) => n + 1);
            setStreamError(message);
            const fallbackBars = lastValidBarsRef.current;
            if (fallbackBars.length > 0) setLiveBars(fallbackBars);
            const fallbackSummary = lastValidSummaryRef.current;
            if (fallbackSummary) setLiveSummary(fallbackSummary);
        };

        const connect = () => {
            cleanup();
            if (stopped) return;

            let url = "";
            try {
                url = getWsUrl();
            } catch {
                setStreamStatus("offline");
                return;
            }

            setStreamStatus(attempt === 0 ? "connecting" : "retrying");
            setStreamError("");

            try {
                ws = new WebSocket(url);
            } catch {
                scheduleReconnect();
                return;
            }

            ws.onopen = () => {
                attempt = 0;
                setStreamStatus("connected");
                heartbeatId = window.setInterval(() => {
                    if (!ws || ws.readyState !== WebSocket.OPEN) return;
                    try {
                        ws.send(JSON.stringify({ type: "ping", ts: Date.now() }));
                    } catch {
                        setStreamFailures((n) => n + 1);
                    }
                }, 1000);
            };

            ws.onmessage = (ev) => {
                let data: unknown;
                try {
                    data = JSON.parse(String(ev.data));
                } catch {
                    handleCorrupt("Data corruption detected");
                    return;
                }
                if (!data || typeof data !== "object") return;
                const obj = data as Record<string, unknown>;
                const ts = isNum(obj.ts) ? obj.ts : Date.now();
                if (ts <= lastSeenTs) return;
                lastSeenTs = ts;

                const latency = Date.now() - ts;
                if (Number.isFinite(latency)) setStreamLatencyMs(latency);
                if (latency > 500) setStreamFailures((n) => n + 1);

                const barsCandidate = (obj.points ?? obj.bars) as unknown;
                if (Array.isArray(barsCandidate)) {
                    const valid = barsCandidate.filter(isDualSeriesPoint) as DualSeriesPoint[];
                    if (valid.length === barsCandidate.length && valid.length > 0) {
                        pendingBarsRef.current = { ts, points: valid };
                    } else {
                        handleCorrupt("Schema validation failed");
                    }
                }

                const summaryCandidate = (obj.summary ?? obj.payload) as unknown;
                if (summaryCandidate && typeof summaryCandidate === "object" && isSummary(summaryCandidate)) {
                    if (ts > lastAppliedTsRef.current) pendingSummaryRef.current = { ts, summary: summaryCandidate };
                }
            };

            ws.onerror = () => {
                setStreamFailures((n) => n + 1);
                setStreamError("Connection error");
            };

            ws.onclose = () => {
                if (stopped) return;
                scheduleReconnect();
            };

            if (!commitId) {
                commitId = window.setInterval(() => {
                    commitPending();
                }, 1000);
            }
        };

        connect();

        return () => {
            stopped = true;
            cleanup();
        };
    }, []);

    const effectiveSummary = liveSummary ?? summary;

    const successRate = effectiveSummary
        ? effectiveSummary.total_calls > 0
            ? Math.round((effectiveSummary.answered_calls / effectiveSummary.total_calls) * 100)
            : 0
        : 0;

    const successRateFontClass = successRate >= 100 ? "text-base" : "text-lg";

    const minutesTooltip = useHoverTooltip();
    const minutesUsed = effectiveSummary?.minutes_used ?? 0;
    const minutesRemaining = effectiveSummary?.minutes_remaining ?? 0;
    const minutesTotal = minutesUsed + minutesRemaining;
    const minutesUsedPct = minutesTotal > 0 ? Math.round((minutesUsed / minutesTotal) * 100) : 0;
    const minutesUsedText = minutesUsed.toLocaleString();
    const minutesRemainingText = minutesRemaining.toLocaleString();
    const minutesTooltipContent = (
        <div className="space-y-2">
            <div className="text-sm font-black text-gray-900">Minutes usage</div>
            <div className="space-y-1.5">
                <div className="flex items-center justify-between gap-6">
                    <span className="text-emerald-700 font-bold">Used minutes</span>
                    <span className="tabular-nums font-black text-gray-900">
                        {minutesUsed.toLocaleString()}{" "}
                        <span className="text-gray-600 font-semibold">({minutesUsedPct}%)</span>
                    </span>
                </div>
                <div className="flex items-center justify-between gap-6">
                    <span className="text-gray-700 font-bold">Remaining minutes</span>
                    <span className="tabular-nums font-black text-gray-900">
                        {minutesRemaining.toLocaleString()}{" "}
                        <span className="text-gray-600 font-semibold">({Math.max(0, 100 - minutesUsedPct)}%)</span>
                    </span>
                </div>
            </div>
            <div className="text-xs font-semibold text-gray-600">Total: {minutesTotal.toLocaleString()} min</div>
        </div>
    );

    const minutesLayoutRef = useRef<HTMLDivElement | null>(null);
    const [minutesFontPx, setMinutesFontPx] = useState<{ used: number; remaining: number }>({
        used: MINUTES_USAGE_LAYOUT_SPEC.maxFontPx,
        remaining: MINUTES_USAGE_LAYOUT_SPEC.maxFontPx,
    });

    useLayoutEffect(() => {
        const el = minutesLayoutRef.current;
        if (!el) return;

        const update = () => {
            const rect = el.getBoundingClientRect();
            const containerPx = Math.max(0, rect.width);
            const { usedPx, remainingPx } = computeMinutesUsageFontPx({
                containerPx,
                usedText: minutesUsedText,
                remainingText: minutesRemainingText,
                spec: MINUTES_USAGE_LAYOUT_SPEC,
            });

            setMinutesFontPx((prev) => {
                if (prev.used === usedPx && prev.remaining === remainingPx) return prev;
                return { used: usedPx, remaining: remainingPx };
            });
        };

        update();
        const ro = new ResizeObserver(() => update());
        ro.observe(el);
        return () => ro.disconnect();
    }, [minutesRemainingText, minutesUsedText]);

    const stackedPoints: DualSeriesPoint[] = series.map((s) => ({
        label: new Date(s.date).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
        a: s.answered,
        b: s.failed,
    }));

    const heatRows = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    const heatCols = ["0–3", "3–6", "6–9", "9–12", "12–15", "15–18", "18–21", "21–24"];
    const seedTotals = series.length > 0 ? series.map((s) => s.total_calls) : [0, 0, 0, 0, 0, 0, 0];
    const heatValues = heatRows.map((_, ri) =>
        heatCols.map((__, ci) => {
            const base = seedTotals[ri % seedTotals.length] ?? 0;
            const wave = 0.55 + 0.45 * Math.sin((ri + 1) * 1.4 + (ci + 1) * 0.9);
            return Math.max(0, Math.round((base / heatCols.length) * wave));
        })
    );
    const heatMax = Math.max(1, ...heatValues.flat());

    const campaignLineSeries = useMemo(() => {
        const palette = ["#2563EB", "#10B981", "#F59E0B", "#A855F7"];
        const base = campaigns.length > 0
            ? campaigns.slice(0, 4).map((c, i) => ({
                id: c.id,
                label: c.name,
                weight: Math.max(0.35, Math.min(1.5, (c.max_concurrent_calls ?? 10) / 12)),
                color: palette[i % palette.length],
            }))
            : [
                { id: "camp-a", label: "Campaign A", weight: 1, color: palette[0] },
                { id: "camp-b", label: "Campaign B", weight: 0.8, color: palette[1] },
                { id: "camp-c", label: "Campaign C", weight: 0.6, color: palette[2] },
            ];
        const enabled: Record<string, boolean> = {};
        for (const c of base) enabled[c.id] = true;
        return { campaigns: base, enabled };
    }, [campaigns]);

    const [campaignEnabled, setCampaignEnabled] = useState<Record<string, boolean>>({});

    useEffect(() => {
        setCampaignEnabled((prev) => {
            const next: Record<string, boolean> = { ...prev };
            for (const c of campaignLineSeries.campaigns) {
                if (next[c.id] === undefined) next[c.id] = true;
            }
            for (const id of Object.keys(next)) {
                if (!campaignLineSeries.campaigns.some((c) => c.id === id)) delete next[id];
            }
            return next;
        });
    }, [campaignLineSeries.campaigns]);

    const feedItems: FeedItem[] = campaigns
        .slice(0, 6)
        .map((c) => {
            const tone: FeedItem["tone"] = c.status === "running" ? "good" : c.status === "paused" ? "warn" : "neutral";
            const time = new Date(c.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric" });
            return {
                id: c.id,
                title: c.name,
                detail: `${c.calls_completed} completed • ${c.calls_failed} failed • ${c.status}`,
                timeLabel: time,
                tone,
            };
        })
        .concat(
            liveBars.length > 0
                ? [
                    {
                        id: "live-feed",
                        title: "Live traffic update",
                        detail: `${liveBars[liveBars.length - 1].a + liveBars[liveBars.length - 1].b} calls in the last minute`,
                        timeLabel: "Now",
                        tone: "neutral" as const,
                    },
                ]
                : []
        )
        .slice(0, 7);

    const timelineItems: TimelineItem[] = series
        .slice(-5)
        .map((s) => {
            const rate = s.total_calls > 0 ? s.failed / s.total_calls : 0;
            const severity: TimelineItem["severity"] = rate > 0.18 ? "error" : rate > 0.12 ? "warn" : "info";
            const dateLabel = new Date(s.date).toLocaleDateString("en-US", { month: "short", day: "numeric" });
            return {
                id: `fail-${s.date}`,
                title: severity === "error" ? "High failure rate" : severity === "warn" ? "Elevated failures" : "Stable delivery",
                detail: `${s.failed} failed out of ${s.total_calls} calls (${Math.round(rate * 100)}%)`,
                timeLabel: dateLabel,
                severity,
            };
        })
        .reverse();

    return (
        <DashboardLayout title="Dashboard" description="Overview of your voice campaigns">
            {loading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-gray-800 dark:border-gray-200" />
                </div>
            ) : error ? (
                <div className="content-card border-red-500/30 text-red-600 dark:text-red-400 font-bold">
                    {error}
                </div>
            ) : (
                <div className="space-y-8">
                    {/* Stats Grid */}
                    <div data-testid="dashboard-kpi-row" className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 items-stretch">
                        {kpis.map((k) => (
                            <KpiCard
                                key={k.title}
                                title={k.title}
                                value={k.value}
                                valueSuffix={k.valueSuffix}
                                deltaAbs={k.deltaAbs}
                                deltaPct={k.deltaPct}
                                lastUpdatedMs={sim.lastUpdatedMs}
                                status={k.status}
                            />
                        ))}
                    </div>

                    {/* Live Analytics */}
                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.35 }}
                            whileHover={{ scale: 1.01 }}
                            className="rounded-2xl border border-border bg-background/70 backdrop-blur-sm p-6 shadow-sm transition-[background-color,box-shadow] duration-150 ease-out dark:hover:bg-background dark:hover:shadow-md lg:col-span-2"
                        >
                            <div className="flex items-center justify-between gap-3 mb-4">
                                <div className="min-w-0">
                                    <h3 className="text-xl font-bold text-gray-900 dark:text-foreground uppercase tracking-wide">Live Line Chart</h3>
                                    <p className="text-sm text-gray-600 dark:text-muted-foreground font-medium">Call volume over time</p>
                                </div>
                                <div className="flex items-center gap-2 text-xs font-bold text-emerald-700 bg-emerald-100 border border-emerald-200 px-3 py-1.5 rounded-full animate-pulse">
                                    <Activity className="w-4 h-4" />
                                    Live
                                </div>
                            </div>

                            <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
                                <div className="flex items-center gap-2">
                                    {([1, 4, 8, 24] as const).map((h) => (
                                        <button
                                            key={h}
                                            type="button"
                                            className={`px-3 py-1.5 rounded-full text-xs font-bold border transition-colors ${callRangeMode === "preset" && liveRangeHours === h
                                                ? "bg-muted text-foreground border-border"
                                                : "bg-background/70 text-muted-foreground border-border hover:bg-muted/50 dark:hover:bg-muted/50"
                                                }`}
                                            onClick={() => {
                                                setCallRangeMode("preset");
                                                setLiveRangeHours(h);
                                            }}
                                        >
                                            {h}h
                                        </button>
                                    ))}
                                    <button
                                        type="button"
                                        className={`px-3 py-1.5 rounded-full text-xs font-bold border transition-colors ${callRangeMode === "custom"
                                            ? "bg-muted text-foreground border-border"
                                            : "bg-background/70 text-muted-foreground border-border hover:bg-muted/50 dark:hover:bg-muted/50"
                                            }`}
                                        onClick={() => setCallRangeMode("custom")}
                                    >
                                        Custom
                                    </button>
                                </div>

                            </div>

                            {callRangeMode === "custom" ? (
                                <div className="mb-4 grid grid-cols-1 sm:grid-cols-2 gap-3">
                                    <label className="flex items-center justify-between gap-3 rounded-xl border border-border bg-background/70 px-3 py-2">
                                        <span className="text-xs font-bold text-muted-foreground">Start</span>
                                        <input
                                            type="datetime-local"
                                            step={900}
                                            className="text-xs font-bold text-foreground bg-transparent outline-none"
                                            value={toDatetimeLocalValue(customStartMs ?? activeRange.startMs)}
                                            onChange={(e) => {
                                                const ms = fromDatetimeLocalValue(e.target.value);
                                                if (ms === null) return;
                                                const stepMs = 15 * 60 * 1000;
                                                setCustomStartMs(Math.round(ms / stepMs) * stepMs);
                                            }}
                                        />
                                    </label>
                                    <label className="flex items-center justify-between gap-3 rounded-xl border border-border bg-background/70 px-3 py-2">
                                        <span className="text-xs font-bold text-muted-foreground">End</span>
                                        <input
                                            type="datetime-local"
                                            step={900}
                                            className="text-xs font-bold text-foreground bg-transparent outline-none"
                                            value={toDatetimeLocalValue(customEndMs ?? activeRange.endMs)}
                                            onChange={(e) => {
                                                const ms = fromDatetimeLocalValue(e.target.value);
                                                if (ms === null) return;
                                                const stepMs = 15 * 60 * 1000;
                                                setCustomEndMs(Math.round(ms / stepMs) * stepMs);
                                            }}
                                        />
                                    </label>
                                </div>
                            ) : null}

                            {liveBuckets.length === 0 ? (
                                <div className="h-40 flex items-center justify-center text-sm text-gray-600 dark:text-muted-foreground font-semibold">Initializing…</div>
                            ) : (
                                <LiveCallsTimeSeriesChart
                                    buckets={liveBuckets}
                                    markers={markers}
                                    anomalies={anomalies}
                                    maintenanceWindows={maintenanceWindows}
                                    peakBands={peakBands}
                                    onActiveBucketChange={setActiveBucket}
                                />
                            )}
                        </motion.div>

                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.4 }}
                            whileHover={{ scale: 1.01 }}
                            className="flex flex-col rounded-2xl border border-border bg-background/70 backdrop-blur-sm p-6 shadow-sm transition-[background-color,box-shadow] duration-150 ease-out dark:hover:bg-background dark:hover:shadow-md"
                        >
                            <h3 className="text-xl font-bold text-gray-900 dark:text-foreground uppercase tracking-wide mb-4">Donut Chart</h3>
                            <div className="flex flex-1 flex-col min-h-0">
                                <div className="flex flex-1 items-center justify-center">
                                    <div className="relative w-full max-w-[260px] mx-auto flex items-center justify-center">
                                        <DonutChart
                                            value={effectiveSummary?.answered_calls || 0}
                                            total={effectiveSummary?.total_calls || 0}
                                            size={200}
                                            strokeWidth={16}
                                            showSegmentLabels={false}
                                        />
                                        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                                            <div
                                                className={`${successRateFontClass} font-black text-gray-900 dark:text-foreground tabular-nums leading-none text-center`}
                                                style={{ maxWidth: "70%" }}
                                            >
                                                {successRate}%
                                            </div>
                                            <div className="mt-1 text-[11px] font-semibold text-gray-600 dark:text-muted-foreground">Success rate</div>
                                        </div>
                                    </div>
                                </div>

                                <div className="mt-auto pt-6 w-full grid grid-cols-[1fr_1px_1fr] items-center">
                                    <div className="flex flex-col items-center justify-center text-center px-3">
                                        <span className="text-sm font-semibold text-emerald-700">Answered</span>
                                        <span className="mt-1 text-lg font-black text-gray-900 dark:text-foreground tabular-nums">
                                            {effectiveSummary?.answered_calls || 0}
                                        </span>
                                    </div>
                                    <div className="h-9 w-px bg-gray-200/80" aria-hidden />
                                    <div className="flex flex-col items-center justify-center text-center px-3">
                                        <span className="text-sm font-semibold text-red-700">Failed</span>
                                        <span className="mt-1 text-lg font-black text-gray-900 dark:text-foreground tabular-nums">
                                            {effectiveSummary?.failed_calls || 0}
                                        </span>
                                    </div>
                                </div>
                            </div>
                        </motion.div>

                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.45 }}
                            whileHover={{ scale: 1.01 }}
                            className="rounded-2xl border border-border bg-background/70 backdrop-blur-sm p-6 shadow-sm transition-[background-color,box-shadow] duration-150 ease-out dark:hover:bg-background dark:hover:shadow-md"
                        >
                            <h3 className="text-xl font-bold text-gray-900 dark:text-foreground mb-4 uppercase tracking-wide">Minutes Usage</h3>
                            <HoverTooltip state={minutesTooltip.state} className="w-[280px] text-sm font-semibold" />
                            <motion.div
                                className="group rounded-2xl p-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                                tabIndex={0}
                                role="img"
                                aria-label={`Minutes usage: used ${minutesUsed} of ${minutesTotal} minutes, remaining ${minutesRemaining} minutes`}
                                whileHover={{ scale: 1.01 }}
                                transition={{ duration: 0.25, ease: "easeInOut" }}
                                onMouseEnter={(e) => minutesTooltip.show(e.clientX, e.clientY, minutesTooltipContent)}
                                onMouseMove={(e) => minutesTooltip.show(e.clientX, e.clientY, minutesTooltipContent)}
                                onMouseLeave={() => minutesTooltip.hide()}
                                onPointerDown={(e) => {
                                    if (e.pointerType === "touch") {
                                        minutesTooltip.show(e.clientX, e.clientY, minutesTooltipContent, { pinned: true, autoHideMs: 3000 });
                                    }
                                }}
                                onFocus={(e) => {
                                    const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
                                    minutesTooltip.show(rect.left + rect.width / 2, rect.top, minutesTooltipContent);
                                }}
                                onBlur={() => minutesTooltip.hide()}
                            >
                                <div className="relative flex flex-col min-h-[200px] py-1">
                                    <div className="absolute inset-0 grid grid-cols-2 rounded-2xl overflow-hidden pointer-events-none">
                                        <div className="bg-emerald-500/6" />
                                        <div className="bg-muted/40" />
                                    </div>

                                    <div className="relative flex-1 flex flex-col justify-center gap-5">
                                        <div className="text-center">
                                            <div className="text-[11px] font-bold uppercase tracking-wide text-gray-600 dark:text-muted-foreground">Minutes used</div>
                                            <div className="mt-2 flex items-center justify-center gap-2 text-sm font-semibold text-gray-700 dark:text-muted-foreground">
                                                <Clock className="w-4 h-4 text-gray-600 dark:text-muted-foreground" aria-hidden />
                                                <span className="tabular-nums font-black text-gray-900 dark:text-foreground">{minutesUsedPct}%</span>
                                                <span>used</span>
                                            </div>
                                        </div>

                                        <div className="px-4">
                                            <div className="relative w-full h-3 rounded-full overflow-hidden bg-gray-200 shadow-inner">
                                                <motion.div
                                                    initial={{ width: 0 }}
                                                    animate={{ width: `${minutesTotal > 0 ? (minutesUsed / minutesTotal) * 100 : 0}%` }}
                                                    transition={{ duration: 0.6, ease: "easeInOut" }}
                                                    className="absolute left-0 top-0 h-full rounded-full"
                                                >
                                                    <div className="absolute inset-0 bg-emerald-500/30 blur-sm" aria-hidden />
                                                    <div
                                                        className="relative h-full rounded-full bg-gradient-to-r from-emerald-500 to-emerald-600 shadow-[0_0_0_1px_rgba(16,185,129,0.18),0_10px_18px_rgba(16,185,129,0.18)] transition-shadow duration-300 ease-in-out dark:group-hover:shadow-[0_0_0_1px_rgba(16,185,129,0.20),0_14px_24px_rgba(16,185,129,0.22)]"
                                                        aria-hidden
                                                    />
                                                </motion.div>
                                            </div>
                                        </div>
                                    </div>

                                    <div className="relative mt-auto pt-6 pb-2">
                                        <div ref={minutesLayoutRef} className="grid grid-cols-[1fr_1px_1fr] items-center">
                                            <div className="min-w-0 flex flex-col items-center text-center px-3">
                                                <span className="text-sm font-semibold text-emerald-700">Used</span>
                                                <motion.span
                                                    key={minutesUsedText}
                                                    className="mt-1 font-black text-gray-900 dark:text-foreground tabular-nums leading-none transition-[font-size] duration-300 ease-in-out"
                                                    initial={{ opacity: 0, y: 3 }}
                                                    animate={{ opacity: 1, y: 0 }}
                                                    transition={{ duration: 0.28, ease: "easeOut" }}
                                                    style={{ fontSize: minutesFontPx.used }}
                                                >
                                                    {minutesUsedText}
                                                </motion.span>
                                            </div>

                                            <div className="min-w-0 flex flex-col items-center text-center px-3">
                                                <span className="text-sm font-semibold text-gray-700 dark:text-muted-foreground">Remaining</span>
                                                <motion.span
                                                    key={minutesRemainingText}
                                                    className="mt-1 font-black text-gray-900 dark:text-foreground tabular-nums leading-none transition-[font-size] duration-300 ease-in-out"
                                                    initial={{ opacity: 0, y: 3 }}
                                                    animate={{ opacity: 1, y: 0 }}
                                                    transition={{ duration: 0.28, ease: "easeOut" }}
                                                    style={{ fontSize: minutesFontPx.remaining }}
                                                >
                                                    {minutesRemainingText}
                                                </motion.span>
                                            </div>
                                        </div>
                                        <div className="mt-4 text-center text-[11px] font-bold uppercase tracking-wide text-gray-700 dark:text-muted-foreground">
                                            Total = <span className="tabular-nums">{minutesTotal.toLocaleString()} min</span>
                                        </div>
                                    </div>
                                </div>
                            </motion.div>
                        </motion.div>

                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.5 }}
                            whileHover={{ scale: 1.01 }}
                            className="rounded-2xl border border-border bg-background/70 backdrop-blur-sm p-6 shadow-sm transition-[background-color,box-shadow] duration-150 ease-out dark:hover:bg-background dark:hover:shadow-md lg:col-span-2"
                        >
                            <div className="flex items-center justify-between gap-3 mb-4">
                                <div className="min-w-0">
                                    <h3 className="text-xl font-bold text-gray-900 dark:text-foreground uppercase tracking-wide">Stacked Area Chart</h3>
                                    <p className="text-sm text-gray-600 dark:text-muted-foreground font-medium">Answered vs failed trend</p>
                                </div>
                                <div className="text-xs font-bold text-gray-700 dark:text-muted-foreground bg-gray-100 dark:bg-muted/30 border border-gray-200 dark:border-border px-3 py-1.5 rounded-full">
                                    Last {stackedPoints.length} points
                                </div>
                            </div>
                            {stackedPoints.length === 0 ? (
                                <div className="h-40 flex items-center justify-center text-sm text-gray-600 dark:text-muted-foreground font-semibold">No series data</div>
                            ) : (
                                <StackedAreaChart points={stackedPoints} />
                            )}
                        </motion.div>

                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.55 }}
                            whileHover={{ scale: 1.01 }}
                            className="rounded-2xl border border-border bg-background/70 backdrop-blur-sm p-6 shadow-sm transition-[background-color,box-shadow] duration-150 ease-out dark:hover:bg-background dark:hover:shadow-md lg:col-span-3"
                        >
                            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between mb-4">
                                <div className="min-w-0">
                                    <h3 className="text-xl font-bold text-gray-900 dark:text-foreground uppercase tracking-wide">Real-Time Call Statistics</h3>
                                    <p className="text-sm text-gray-600 dark:text-muted-foreground font-medium">
                                        {new Date(activeRange.startMs).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}–{new Date(activeRange.endMs).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                                    </p>
                                </div>

                                <div className="flex flex-wrap items-center gap-2">
                                    <button
                                        type="button"
                                        className={`px-3 py-1.5 rounded-full text-xs font-bold border transition-colors ${callStatsView === "status"
                                            ? "bg-muted text-foreground border-border"
                                            : "bg-background/70 text-muted-foreground border-border hover:bg-muted/50 dark:hover:bg-muted/50"
                                            }`}
                                        onClick={() => setCallStatsView("status")}
                                    >
                                        Status
                                    </button>
                                    <button
                                        type="button"
                                        className={`px-3 py-1.5 rounded-full text-xs font-bold border transition-colors ${callStatsView === "campaigns"
                                            ? "bg-muted text-foreground border-border"
                                            : "bg-background/70 text-muted-foreground border-border hover:bg-muted/50 dark:hover:bg-muted/50"
                                            }`}
                                        onClick={() => setCallStatsView("campaigns")}
                                    >
                                        Campaigns
                                    </button>
                                    <button
                                        type="button"
                                        className={`px-3 py-1.5 rounded-full text-xs font-bold border transition-colors ${callStatsView === "outcomes"
                                            ? "bg-muted text-foreground border-border"
                                            : "bg-background/70 text-muted-foreground border-border hover:bg-muted/50 dark:hover:bg-muted/50"
                                            }`}
                                        onClick={() => setCallStatsView("outcomes")}
                                    >
                                        Outcomes
                                    </button>
                                </div>
                            </div>

                            <div className="mb-4 grid grid-cols-2 sm:grid-cols-4 gap-3">
                                <div className="rounded-xl border border-gray-200 dark:border-border bg-white dark:bg-muted/30 px-3 py-2">
                                    <div className="text-[11px] font-bold uppercase tracking-wide text-gray-600 dark:text-muted-foreground">Active calls</div>
                                    <div className="mt-1 text-lg font-black tabular-nums text-gray-900 dark:text-foreground">{Math.round(hoverStats.activeCalls).toLocaleString()}</div>
                                </div>
                                <div className="rounded-xl border border-gray-200 dark:border-border bg-white dark:bg-muted/30 px-3 py-2">
                                    <div className="text-[11px] font-bold uppercase tracking-wide text-gray-600 dark:text-muted-foreground">Queue size</div>
                                    <div className="mt-1 text-lg font-black tabular-nums text-gray-900 dark:text-foreground">{Math.round(hoverStats.queueSize).toLocaleString()}</div>
                                </div>
                                <div className="rounded-xl border border-gray-200 dark:border-border bg-white dark:bg-muted/30 px-3 py-2">
                                    <div className="text-[11px] font-bold uppercase tracking-wide text-gray-600 dark:text-muted-foreground">In progress</div>
                                    <div className="mt-1 text-lg font-black tabular-nums text-gray-900 dark:text-foreground">{Math.round(hoverStats.inProgress).toLocaleString()}</div>
                                </div>
                                <div className="rounded-xl border border-gray-200 dark:border-border bg-white dark:bg-muted/30 px-3 py-2">
                                    <div className="text-[11px] font-bold uppercase tracking-wide text-gray-600 dark:text-muted-foreground">Success rate</div>
                                    <div className="mt-1 text-lg font-black tabular-nums text-gray-900 dark:text-foreground">{hoverStats.successRate.toFixed(1)}%</div>
                                </div>
                            </div>

                            {callStatsView === "status" ? (
                                <StatusStackedBarsChart
                                    buckets={liveBuckets}
                                    height={300}
                                    onHoverBucket={setActiveBucket}
                                    peakBands={peakBands.map((p) => ({ startMs: p.startMs, endMs: p.endMs, label: p.label ?? "Peak" }))}
                                    events={markers.filter((m) => m.kind === "event").map((m) => ({ ms: m.ms, label: m.label }))}
                                />
                            ) : callStatsView === "campaigns" ? (
                                <div className="space-y-4">
                                    <div className="flex flex-wrap items-center gap-2">
                                        {campaignLineSeries.campaigns.map((c) => (
                                            <button
                                                key={c.id}
                                                type="button"
                                                className={`px-3 py-1.5 rounded-full text-xs font-bold border transition-colors ${campaignEnabled[c.id] !== false
                                                    ? "bg-background/70 text-foreground border-border hover:bg-muted/50 dark:hover:bg-muted/50"
                                                    : "bg-muted/40 text-muted-foreground border-border hover:bg-muted/50 dark:hover:bg-muted/50"
                                                    }`}
                                                onClick={() => setCampaignEnabled((prev) => ({ ...prev, [c.id]: !(prev[c.id] !== false) }))}
                                            >
                                                <span className="inline-flex items-center gap-2">
                                                    <span className="w-2.5 h-2.5 rounded-full" style={{ background: c.color }} aria-hidden />
                                                    {c.label}
                                                </span>
                                            </button>
                                        ))}
                                    </div>
                                    <CampaignLinesChart
                                        buckets={liveBuckets}
                                        campaigns={campaignLineSeries.campaigns}
                                        enabled={campaignEnabled}
                                        height={320}
                                        peakBands={peakBands.map((p) => ({ startMs: p.startMs, endMs: p.endMs, label: p.label ?? "Peak" }))}
                                        events={markers.filter((m) => m.kind === "event").map((m) => ({ ms: m.ms, label: m.label }))}
                                    />
                                </div>
                            ) : (
                                <div className="grid grid-cols-1 lg:grid-cols-[1fr_260px] gap-6 items-start">
                                    <StatusStackedBarsChart
                                        buckets={liveBuckets}
                                        height={300}
                                        onHoverBucket={setActiveBucket}
                                        peakBands={peakBands.map((p) => ({ startMs: p.startMs, endMs: p.endMs, label: p.label ?? "Peak" }))}
                                        events={markers.filter((m) => m.kind === "event").map((m) => ({ ms: m.ms, label: m.label }))}
                                    />
                                    <OutcomePieChart segments={outcomes} />
                                </div>
                            )}
                        </motion.div>

                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.55 }}
                            whileHover={{ scale: 1.01 }}
                            className="rounded-2xl border border-border bg-background/70 backdrop-blur-sm p-6 shadow-sm transition-[background-color,box-shadow] duration-150 ease-out dark:hover:bg-background dark:hover:shadow-md lg:col-span-2"
                        >
                            <div className="flex items-center justify-between gap-3 mb-4">
                                <div className="min-w-0">
                                    <h3 className="text-xl font-bold text-gray-900 dark:text-foreground uppercase tracking-wide">Heatmap</h3>
                                    <p className="text-sm text-gray-600 dark:text-muted-foreground font-medium">Activity intensity by day and time</p>
                                </div>
                                <div className="text-xs font-bold text-gray-700 dark:text-muted-foreground bg-gray-100 dark:bg-muted/30 border border-gray-200 dark:border-border px-3 py-1.5 rounded-full">
                                    7×8
                                </div>
                            </div>
                            <Heatmap rows={heatRows} cols={heatCols} cellValues={heatValues} maxValue={heatMax} />
                        </motion.div>

                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.6 }}
                            whileHover={{ scale: 1.01 }}
                            className="rounded-2xl border border-border bg-background/70 backdrop-blur-sm p-6 shadow-sm transition-[background-color,box-shadow] duration-150 ease-out dark:hover:bg-background dark:hover:shadow-md"
                        >
                            <h3 className="text-xl font-bold text-gray-900 dark:text-foreground uppercase tracking-wide mb-4">Event Stream</h3>
                            <ActivityFeed items={feedItems} />
                        </motion.div>

                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.65 }}
                            whileHover={{ scale: 1.01 }}
                            className="rounded-2xl border border-border bg-background/70 backdrop-blur-sm p-6 shadow-sm transition-[background-color,box-shadow] duration-150 ease-out dark:hover:bg-background dark:hover:shadow-md lg:col-span-3"
                        >
                            <div className="flex items-center justify-between gap-3 mb-4">
                                <div className="min-w-0">
                                    <h3 className="text-xl font-bold text-gray-900 dark:text-foreground uppercase tracking-wide">Error & Alert Timeline</h3>
                                    <p className="text-sm text-gray-600 dark:text-muted-foreground font-medium">Recent delivery health signals</p>
                                </div>
                                <div className="flex items-center gap-2 text-xs font-bold text-gray-700 dark:text-muted-foreground bg-gray-100 dark:bg-muted/30 border border-gray-200 dark:border-border px-3 py-1.5 rounded-full">
                                    <AlertTriangle className="w-4 h-4" />
                                    Monitoring
                                </div>
                            </div>
                            {timelineItems.length === 0 ? (
                                <div className="text-sm text-gray-600 dark:text-muted-foreground font-semibold py-2">No alerts</div>
                            ) : (
                                <AlertTimeline items={timelineItems} />
                            )}
                        </motion.div>
                    </div>

                    {/* Recent Campaigns */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.5 }}
                        whileHover={{ scale: 1.01 }}
                        className="rounded-2xl border border-border bg-background/70 backdrop-blur-sm p-6 shadow-sm transition-[background-color,box-shadow] duration-150 ease-out dark:hover:bg-background dark:hover:shadow-md"
                    >
                        <div className="flex items-center justify-between mb-4">
                            <h3 className="text-xl font-bold text-gray-900 dark:text-foreground uppercase tracking-wide">Recent Campaigns</h3>
                            <Link
                                href="/campaigns"
                                className="text-sm font-semibold text-gray-700 dark:text-muted-foreground transition-colors duration-300 ease-in-out dark:hover:text-foreground"
                            >
                                View all
                            </Link>
                        </div>
                        {campaigns.length === 0 ? (
                            <div className="text-center py-8 text-gray-600 dark:text-muted-foreground">
                                <Megaphone className="w-12 h-12 mx-auto mb-4 opacity-50 text-gray-500 dark:text-muted-foreground" />
                                <p className="font-medium">No campaigns yet</p>
                                <Link
                                    href="/campaigns/new"
                                    className="mt-4 inline-block text-sm font-bold text-gray-900 dark:text-foreground dark:hover:underline"
                                >
                                    Create your first campaign
                                </Link>
                            </div>
                        ) : (
                            <div className="space-y-3">
                                {campaigns.slice(0, 5).map((campaign, index) => (
                                    <motion.div
                                        key={campaign.id}
                                        initial={{ opacity: 0, x: -20 }}
                                        animate={{ opacity: 1, x: 0 }}
                                        transition={{ delay: 0.6 + index * 0.1 }}
                                    >
                                        <Link
                                            href={`/campaigns/${campaign.id}`}
                                            className="flex items-center justify-between rounded-xl border border-border bg-background/70 px-4 py-4 shadow-sm transition-[transform,background-color,box-shadow] duration-150 ease-out hover:scale-[1.02] hover:bg-gray-50/80 hover:shadow-md active:scale-[0.99] dark:hover:scale-[1.02] dark:active:scale-[0.99] dark:hover:bg-gray-800 dark:hover:shadow-md"
                                        >
                                            <div>
                                                <h4 className="font-bold text-gray-900 dark:text-foreground">{campaign.name}</h4>
                                                <p className="text-sm font-medium text-gray-600 dark:text-muted-foreground">
                                                    {campaign.total_leads} leads | {campaign.calls_completed} completed
                                                </p>
                                            </div>
                                            <div className="flex items-center gap-4">
                                                <span
                                                    className={`px-2 py-1 text-xs font-bold rounded-full ${campaign.status === "running"
                                                        ? "bg-emerald-100 text-emerald-800 border border-emerald-300 dark:bg-emerald-500/20 dark:text-emerald-200 dark:border-emerald-500/30"
                                                        : campaign.status === "paused"
                                                            ? "bg-yellow-100 text-yellow-800 border border-yellow-300 dark:bg-yellow-500/20 dark:text-yellow-200 dark:border-yellow-500/30"
                                                            : campaign.status === "completed"
                                                                ? "bg-gray-100 text-gray-800 border border-gray-300 dark:bg-gray-500/20 dark:text-gray-200 dark:border-gray-500/30"
                                                                : "bg-gray-100 text-gray-800 dark:bg-gray-500/20 dark:text-gray-200"
                                                        }`}
                                                >
                                                    {campaign.status}
                                                </span>
                                                <ArrowUpRight className="w-4 h-4 text-gray-700 dark:text-muted-foreground" />
                                            </div>
                                        </Link>
                                    </motion.div>
                                ))}
                            </div>
                        )}
                    </motion.div>
                </div>
            )}
        </DashboardLayout>
    );
}
