"use client";

import { useMemo } from "react";
import { motion } from "framer-motion";
import { HoverTooltip, useHoverTooltip } from "@/components/ui/hover-tooltip";

type MinutesBySubTenant = {
    subTenant: string;
    minutesUsed: number;
};

type ConcurrencyPoint = {
    time: string;
    concurrentCalls: number;
};

type DailyUsagePoint = {
    date: string;
    minutes: number;
};

function stableNumberFromString(input: string) {
    let hash = 0;
    for (let i = 0; i < input.length; i++) {
        hash = (hash * 31 + input.charCodeAt(i)) >>> 0;
    }
    return hash;
}

function clamp(value: number, min: number, max: number) {
    return Math.min(max, Math.max(min, value));
}

function toSmoothPath(points: Array<{ x: number; y: number }>, smoothing = 0.18) {
    if (points.length === 0) return "";
    if (points.length === 1) return `M ${points[0]!.x} ${points[0]!.y}`;

    const cps = (current: { x: number; y: number }, previous: { x: number; y: number }, next: { x: number; y: number }) => {
        const dx = next.x - previous.x;
        return { x: current.x - dx * smoothing, y: current.y };
    };

    const cpe = (current: { x: number; y: number }, previous: { x: number; y: number }, next: { x: number; y: number }) => {
        const dx = next.x - previous.x;
        return { x: current.x + dx * smoothing, y: current.y };
    };

    let d = `M ${points[0]!.x} ${points[0]!.y}`;
    for (let i = 0; i < points.length - 1; i++) {
        const p0 = points[i - 1] ?? points[i]!;
        const p1 = points[i]!;
        const p2 = points[i + 1]!;
        const p3 = points[i + 2] ?? p2;
        const start = cpe(p1, p0, p2);
        const end = cps(p2, p1, p3);
        d += ` C ${start.x} ${start.y}, ${end.x} ${end.y}, ${p2.x} ${p2.y}`;
    }
    return d;
}

function formatShortDate(isoDate: string) {
    const d = new Date(isoDate);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function makeMockData(partnerId: string): {
    minutesBySubTenant: MinutesBySubTenant[];
    concurrencyPeaks: ConcurrencyPoint[];
    dailyUsage: DailyUsagePoint[];
} {
    const key = partnerId.trim().toLowerCase();

    if (key === "acme") {
        const today = new Date();
        const dailyUsage = Array.from({ length: 14 }).map((_, i) => {
            const d = new Date(today);
            d.setDate(today.getDate() - (13 - i));
            const base = [320, 410, 290, 530][i % 4] ?? 380;
            const minutes = base + Math.round(((i % 5) - 2) * 18);
            return { date: d.toISOString().slice(0, 10), minutes: Math.max(0, minutes) };
        });

        return {
            minutesBySubTenant: [
                { subTenant: "Salon Assistant", minutesUsed: 600 },
                { subTenant: "Clinic Bot", minutesUsed: 1200 },
                { subTenant: "Restaurant Bot", minutesUsed: 350 },
            ],
            concurrencyPeaks: [
                { time: "10:00", concurrentCalls: 3 },
                { time: "11:00", concurrentCalls: 6 },
                { time: "12:00", concurrentCalls: 8 },
                { time: "13:00", concurrentCalls: 4 },
            ],
            dailyUsage,
        };
    }

    const seed = stableNumberFromString(key);
    const tenantsCount = 4 + (seed % 6);
    const minutesBySubTenant = Array.from({ length: tenantsCount }).map((_, i) => {
        const minutesUsed = 180 + ((seed + i * 97) % 1700);
        return { subTenant: `Sub-Tenant ${i + 1}`, minutesUsed };
    });

    const concurrencyPoints = 8 + (seed % 5);
    const startHour = 9 + (seed % 3);
    const concurrencyPeaks = Array.from({ length: concurrencyPoints }).map((_, i) => {
        const hour = startHour + i;
        const time = `${String(hour).padStart(2, "0")}:00`;
        const wave = Math.sin((i / Math.max(1, concurrencyPoints - 1)) * Math.PI);
        const noise = ((seed + i * 43) % 5) - 2;
        const concurrentCalls = Math.max(0, Math.round(2 + wave * (6 + (seed % 5)) + noise));
        return { time, concurrentCalls };
    });

    const today = new Date();
    const days = 14;
    const dailyUsage = Array.from({ length: days }).map((_, i) => {
        const d = new Date(today);
        d.setDate(today.getDate() - (days - 1 - i));
        const base = 220 + ((seed + i * 41) % 560);
        const drift = Math.round(((i - (days - 1) / 2) / days) * (seed % 180));
        const minutes = Math.max(0, base + drift);
        return { date: d.toISOString().slice(0, 10), minutes };
    });

    return { minutesBySubTenant, concurrencyPeaks, dailyUsage };
}

function BarChart({
    title,
    subtitle,
    yLabel,
    items,
}: {
    title: string;
    subtitle: string;
    yLabel: string;
    items: Array<{ label: string; value: number }>;
}) {
    const tooltip = useHoverTooltip();
    const maxValue = Math.max(1, ...items.map((x) => x.value));
    const total = items.reduce((acc, x) => acc + x.value, 0);

    const height = 240;
    const top = 18;
    const bottom = 56;
    const left = 64;
    const right = 18;
    const barW = 56;
    const gap = 22;
    const plotH = height - top - bottom;
    const plotW = items.length * barW + Math.max(0, items.length - 1) * gap;
    const width = Math.max(560, left + right + plotW);

    const ticks = 4;
    const yTicks = Array.from({ length: ticks + 1 }).map((_, i) => {
        const v = Math.round((i * maxValue) / ticks);
        return v;
    });

    return (
        <div className="content-card">
            <div className="flex flex-col gap-1">
                <div className="text-sm font-semibold text-foreground">{title}</div>
                <div className="text-sm text-muted-foreground">{subtitle}</div>
            </div>

            <div className="mt-4 relative">
                <HoverTooltip tooltip={tooltip} />
                <div className="overflow-x-auto">
                    <svg width={width} height={height} className="min-w-full">
                        <rect x={left} y={top} width={width - left - right} height={plotH} rx={14} fill="hsl(var(--muted) / 0.45)" />

                        {yTicks.map((t) => {
                            const y = top + plotH - (t / maxValue) * plotH;
                            return (
                                <g key={t}>
                                    <line
                                        x1={left}
                                        x2={width - right}
                                        y1={y}
                                        y2={y}
                                        stroke="hsl(var(--border) / 0.9)"
                                        strokeWidth={1}
                                        strokeDasharray={t === 0 ? undefined : "4 6"}
                                    />
                                    <text
                                        x={left - 12}
                                        y={y + 4}
                                        textAnchor="end"
                                        style={{ fontSize: 11, fontWeight: 700, fill: "hsl(var(--muted-foreground))" }}
                                    >
                                        {t.toLocaleString()}
                                    </text>
                                </g>
                            );
                        })}

                        <text
                            x={18}
                            y={top + plotH / 2}
                            transform={`rotate(-90 18 ${top + plotH / 2})`}
                            style={{ fontSize: 11, fontWeight: 800, fill: "hsl(var(--muted-foreground))" }}
                        >
                            {yLabel}
                        </text>

                        {items.map((item, i) => {
                            const x = left + i * (barW + gap);
                            const h = (item.value / maxValue) * plotH;
                            const y = top + plotH - h;
                            const pct = total > 0 ? Math.round((item.value / total) * 100) : 0;

                            const content = (
                                <div className="space-y-1">
                                    <div className="text-sm font-black text-gray-900">{item.label}</div>
                                    <div className="flex items-center justify-between gap-6 text-sm">
                                        <span className="text-gray-700 font-semibold">Minutes</span>
                                        <span className="tabular-nums font-black text-gray-900">{item.value.toLocaleString()}</span>
                                    </div>
                                    <div className="text-xs font-semibold text-gray-600">Share: {pct}%</div>
                                </div>
                            );

                            return (
                                <g
                                    key={item.label}
                                    onMouseEnter={(e) => tooltip.show(e.clientX, e.clientY, content)}
                                    onMouseMove={(e) => tooltip.show(e.clientX, e.clientY, content)}
                                    onMouseLeave={() => tooltip.hide()}
                                    onPointerDown={(e) => {
                                        if (e.pointerType === "touch") tooltip.show(e.clientX, e.clientY, content, { pinned: true, autoHideMs: 2500 });
                                    }}
                                    tabIndex={0}
                                    role="img"
                                    aria-label={`${item.label}: ${item.value} minutes`}
                                    onFocus={(e) => {
                                        const rect = (e.currentTarget as unknown as SVGGElement).getBoundingClientRect();
                                        tooltip.show(rect.left + rect.width / 2, rect.top + rect.height / 2, content);
                                    }}
                                    onBlur={() => tooltip.hide()}
                                    className="cursor-default"
                                >
                                    <rect x={x} y={y} width={barW} height={h} rx={12} fill="url(#minutesBarGradient)" />
                                    <text
                                        x={x + barW / 2}
                                        y={top + plotH + 20}
                                        textAnchor="middle"
                                        style={{ fontSize: 11, fontWeight: 800, fill: "hsl(var(--muted-foreground))" }}
                                    >
                                        {item.label.length > 12 ? `${item.label.slice(0, 10)}…` : item.label}
                                    </text>
                                </g>
                            );
                        })}

                        <defs>
                            <linearGradient id="minutesBarGradient" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="0%" stopColor="rgba(16,185,129,0.95)" />
                                <stop offset="100%" stopColor="rgba(52,211,153,0.65)" />
                            </linearGradient>
                        </defs>
                    </svg>
                </div>
            </div>
        </div>
    );
}

function LineChart({
    title,
    subtitle,
    xLabel,
    yLabel,
    points,
    highlightMax = true,
    lineColor = "rgba(59,130,246,0.92)",
    areaColor = "rgba(59,130,246,0.14)",
}: {
    title: string;
    subtitle: string;
    xLabel: string;
    yLabel: string;
    points: Array<{ label: string; value: number }>;
    highlightMax?: boolean;
    lineColor?: string;
    areaColor?: string;
}) {
    const tooltip = useHoverTooltip();

    const height = 240;
    const top = 18;
    const bottom = 56;
    const left = 64;
    const right = 18;
    const plotH = height - top - bottom;

    const values = points.map((p) => p.value);
    const minVal = Math.min(...values, 0);
    const maxVal = Math.max(...values, 1);
    const range = Math.max(1, maxVal - minVal);

    const width = Math.max(560, left + right + Math.max(0, points.length - 1) * 72);
    const plotW = width - left - right;

    const maxIdx = points.length > 0 ? values.indexOf(maxVal) : -1;

    const xFor = (i: number) => {
        if (points.length <= 1) return left + plotW / 2;
        return left + (i * plotW) / (points.length - 1);
    };

    const yFor = (v: number) => {
        const t = (v - minVal) / range;
        return top + (1 - clamp(t, 0, 1)) * plotH;
    };

    const chartPoints = points.map((p, i) => ({ x: xFor(i), y: yFor(p.value) }));
    const d = toSmoothPath(chartPoints);
    const areaD =
        chartPoints.length > 0
            ? `${d} L ${chartPoints[chartPoints.length - 1]!.x} ${top + plotH} L ${chartPoints[0]!.x} ${top + plotH} Z`
            : "";

    const ticks = 4;
    const yTicks = Array.from({ length: ticks + 1 }).map((_, i) => {
        const v = minVal + (i * range) / ticks;
        return Math.round(v);
    });

    const xTickIdxs = Array.from(new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])).filter((i) => i >= 0);

    return (
        <div className="content-card">
            <div className="flex flex-col gap-1">
                <div className="text-sm font-semibold text-foreground">{title}</div>
                <div className="text-sm text-muted-foreground">{subtitle}</div>
            </div>

            <div className="mt-4 relative">
                <HoverTooltip tooltip={tooltip} />
                <div className="overflow-x-auto">
                    <svg width={width} height={height} className="min-w-full">
                        <rect x={left} y={top} width={plotW} height={plotH} rx={14} fill="hsl(var(--muted) / 0.45)" />

                        {yTicks.map((t) => {
                            const y = yFor(t);
                            return (
                                <g key={t}>
                                    <line
                                        x1={left}
                                        x2={width - right}
                                        y1={y}
                                        y2={y}
                                        stroke="hsl(var(--border) / 0.9)"
                                        strokeWidth={1}
                                        strokeDasharray={t === minVal ? undefined : "4 6"}
                                    />
                                    <text
                                        x={left - 12}
                                        y={y + 4}
                                        textAnchor="end"
                                        style={{ fontSize: 11, fontWeight: 700, fill: "hsl(var(--muted-foreground))" }}
                                    >
                                        {t.toLocaleString()}
                                    </text>
                                </g>
                            );
                        })}

                        <text
                            x={18}
                            y={top + plotH / 2}
                            transform={`rotate(-90 18 ${top + plotH / 2})`}
                            style={{ fontSize: 11, fontWeight: 800, fill: "hsl(var(--muted-foreground))" }}
                        >
                            {yLabel}
                        </text>

                        <text
                            x={left + plotW / 2}
                            y={height - 14}
                            textAnchor="middle"
                            style={{ fontSize: 11, fontWeight: 800, fill: "hsl(var(--muted-foreground))" }}
                        >
                            {xLabel}
                        </text>

                        <path d={areaD} fill={areaColor} />
                        <path d={d} fill="none" stroke={lineColor} strokeWidth={2.75} strokeLinejoin="round" strokeLinecap="round" />

                        {chartPoints.map((pt, i) => {
                            const p = points[i]!;
                            const isMax = highlightMax && i === maxIdx;

                            const content = (
                                <div className="space-y-1">
                                    <div className="text-sm font-black text-gray-900">{p.label}</div>
                                    <div className="flex items-center justify-between gap-6 text-sm">
                                        <span className="text-gray-700 font-semibold">{yLabel}</span>
                                        <span className="tabular-nums font-black text-gray-900">{p.value.toLocaleString()}</span>
                                    </div>
                                    {isMax ? <div className="text-xs font-semibold text-amber-700">Peak</div> : null}
                                </div>
                            );

                            return (
                                <g
                                    key={`${p.label}-${i}`}
                                    onMouseEnter={(e) => tooltip.show(e.clientX, e.clientY, content)}
                                    onMouseMove={(e) => tooltip.show(e.clientX, e.clientY, content)}
                                    onMouseLeave={() => tooltip.hide()}
                                    onPointerDown={(e) => {
                                        if (e.pointerType === "touch") tooltip.show(e.clientX, e.clientY, content, { pinned: true, autoHideMs: 2500 });
                                    }}
                                    tabIndex={0}
                                    role="img"
                                    aria-label={`${p.label}: ${p.value}`}
                                    onFocus={(e) => {
                                        const rect = (e.currentTarget as unknown as SVGGElement).getBoundingClientRect();
                                        tooltip.show(rect.left + rect.width / 2, rect.top + rect.height / 2, content);
                                    }}
                                    onBlur={() => tooltip.hide()}
                                    className="cursor-default"
                                >
                                    {isMax ? (
                                        <circle cx={pt.x} cy={pt.y} r={9} fill="rgba(234,179,8,0.34)" />
                                    ) : null}
                                    <motion.circle
                                        initial={{ r: 0 }}
                                        animate={{ r: isMax ? 5.25 : 4.25 }}
                                        transition={{ duration: 0.35, ease: "easeOut", delay: 0.12 + i * 0.03 }}
                                        cx={pt.x}
                                        cy={pt.y}
                                        fill={lineColor}
                                        stroke="rgba(255,255,255,0.85)"
                                        strokeWidth={1.5}
                                    />
                                </g>
                            );
                        })}

                        {xTickIdxs.map((i) => (
                            <text
                                key={`x-${i}`}
                                x={xFor(i)}
                                y={top + plotH + 20}
                                textAnchor="middle"
                                style={{ fontSize: 11, fontWeight: 800, fill: "hsl(var(--muted-foreground))" }}
                            >
                                {points[i]?.label ?? ""}
                            </text>
                        ))}
                    </svg>
                </div>
            </div>
        </div>
    );
}

export function PartnerAnalyticsClient({ partnerId }: { partnerId: string }) {
    const data = useMemo(() => makeMockData(partnerId), [partnerId]);

    const minutesItems = useMemo(() => {
        return data.minutesBySubTenant.map((x) => ({ label: x.subTenant, value: x.minutesUsed }));
    }, [data.minutesBySubTenant]);

    const concurrencyItems = useMemo(() => {
        return data.concurrencyPeaks.map((x) => ({ label: x.time, value: x.concurrentCalls }));
    }, [data.concurrencyPeaks]);

    const dailyItems = useMemo(() => {
        return data.dailyUsage.map((x) => ({ label: formatShortDate(x.date), value: x.minutes }));
    }, [data.dailyUsage]);

    return (
        <div className="space-y-6">
            <BarChart
                title="Minutes Per Sub-Tenant"
                subtitle="Total minutes consumed by each sub-tenant (aggregated)."
                yLabel="Minutes"
                items={minutesItems}
            />

            <LineChart
                title="Concurrent Usage Peaks"
                subtitle="Peak concurrent call usage over time (aggregated)."
                xLabel="Time"
                yLabel="Concurrent calls"
                points={concurrencyItems}
                highlightMax
                lineColor="rgba(16,185,129,0.92)"
                areaColor="rgba(16,185,129,0.14)"
            />

            <LineChart
                title="Daily Usage Trends"
                subtitle="Total minutes consumed per day (aggregated)."
                xLabel="Date"
                yLabel="Minutes"
                points={dailyItems}
                highlightMax={false}
                lineColor="rgba(59,130,246,0.92)"
                areaColor="rgba(59,130,246,0.14)"
            />
        </div>
    );
}
