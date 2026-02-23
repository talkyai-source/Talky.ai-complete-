"use client";

import { motion } from "framer-motion";
import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { HoverTooltip, useHoverTooltip } from "@/components/ui/hover-tooltip";
import { computeDonutSegmentTextLayout } from "@/lib/donut-label-layout.mjs";

export type TimeSeriesPoint = {
  label: string;
  value: number;
};

export type DualSeriesPoint = {
  label: string;
  a: number;
  b: number;
};

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function toPath(points: Array<{ x: number; y: number }>, smoothing = 0.18) {
  if (points.length === 0) return "";
  if (points.length === 1) return `M ${points[0].x} ${points[0].y}`;

  const cps = (current: { x: number; y: number }, previous: { x: number; y: number }, next: { x: number; y: number }) => {
    const dx = next.x - previous.x;
    return {
      x: current.x - dx * smoothing,
      y: current.y,
    };
  };

  const cpe = (current: { x: number; y: number }, previous: { x: number; y: number }, next: { x: number; y: number }) => {
    const dx = next.x - previous.x;
    return {
      x: current.x + dx * smoothing,
      y: current.y,
    };
  };

  let d = `M ${points[0].x} ${points[0].y}`;

  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i - 1] ?? points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] ?? p2;
    const start = cpe(p1, p0, p2);
    const end = cps(p2, p1, p3);
    d += ` C ${start.x} ${start.y}, ${end.x} ${end.y}, ${p2.x} ${p2.y}`;
  }

  return d;
}

export function TimeSeriesLineChart({
  points,
  height = 160,
  stroke = "currentColor",
  fill = "color-mix(in oklab, currentColor 12%, transparent)",
}: {
  points: TimeSeriesPoint[];
  height?: number;
  stroke?: string;
  fill?: string;
}) {
  const tooltip = useHoverTooltip();
  const width = 600;
  const paddingX = 10;
  const paddingY = 12;

  const values = points.map((p) => p.value);
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 1);
  const range = max - min || 1;

  const chartPoints = points.map((p, i) => {
    const x = paddingX + (i * (width - paddingX * 2)) / Math.max(1, points.length - 1);
    const y = paddingY + (1 - (p.value - min) / range) * (height - paddingY * 2);
    return { x, y };
  });

  const d = toPath(chartPoints);
  const areaD =
    chartPoints.length > 0
      ? `${d} L ${chartPoints[chartPoints.length - 1].x} ${height - paddingY} L ${chartPoints[0].x} ${height - paddingY} Z`
      : "";

  return (
    <div className="relative text-foreground">
      <HoverTooltip state={tooltip.state} />
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-40">
        <path d={areaD} fill={fill} />
        <path d={d} fill="none" stroke={stroke} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
        {chartPoints.map((pt, i) => {
          const label = points[i]?.label ?? "";
          const value = points[i]?.value ?? 0;
          const content = (
            <div className="flex items-center gap-2">
              <span className="text-gray-600">{label}</span>
              <span className="font-black tabular-nums text-gray-900">{value.toLocaleString()}</span>
            </div>
          );

          return (
            <motion.circle
              key={`${label}-${i}`}
              cx={pt.x}
              cy={pt.y}
              r={4.25}
              fill={stroke}
              stroke="rgba(255,255,255,0.85)"
              strokeWidth={1.5}
              tabIndex={0}
              role="img"
              aria-label={`${label}: ${value}`}
              className="cursor-default"
              whileHover={{ scale: 1.15, filter: "drop-shadow(0px 6px 10px rgba(0,0,0,0.16))" }}
              transition={{ duration: 0.3, ease: "easeInOut" }}
              onMouseEnter={(e) => tooltip.show(e.clientX, e.clientY, content)}
              onMouseMove={(e) => tooltip.show(e.clientX, e.clientY, content)}
              onMouseLeave={() => tooltip.hide()}
              onPointerDown={(e) => {
                if (e.pointerType === "touch") tooltip.show(e.clientX, e.clientY, content, { pinned: true, autoHideMs: 2500 });
              }}
              onFocus={(e) => {
                const rect = (e.currentTarget as unknown as SVGCircleElement).getBoundingClientRect();
                tooltip.show(rect.left + rect.width / 2, rect.top + rect.height / 2, content);
              }}
              onBlur={() => tooltip.hide()}
            />
          );
        })}
      </svg>
    </div>
  );
}

export function RealTimeBarChart({
  points,
  maxValue,
  height = 160,
}: {
  points: DualSeriesPoint[];
  maxValue: number;
  height?: number;
}) {
  const tooltip = useHoverTooltip();
  const last = points[points.length - 1];
  const seedA = last?.a ?? 0;
  const seedB = last?.b ?? 0;
  const rafRef = useRef<number | null>(null);
  const pendingShowRef = useRef<{
    x: number;
    y: number;
    content: ReactNode;
    options?: { pinned?: boolean; autoHideMs?: number };
  } | null>(null);

  const toRange = (seed: number, min: number, max: number) => {
    const span = max - min;
    const v = Math.abs(Math.trunc(seed));
    return min + (v % (span + 1));
  };

  const answeredValue = toRange(seedA * 13 + points.length * 7 + maxValue, 250, 300);
  const failedValue = toRange(seedB * 17 + points.length * 5 + maxValue, 80, 100);

  const answeredTooltipContent = useMemo(() => {
    return (
      <div className="space-y-1">
        <div className="text-sm font-black text-gray-900">Answered Calls</div>
        <div className="text-sm font-black tabular-nums text-gray-900">{answeredValue.toLocaleString()}</div>
      </div>
    );
  }, [answeredValue]);

  const failedTooltipContent = useMemo(() => {
    return (
      <div className="space-y-1">
        <div className="text-sm font-black text-gray-900">Failed Calls</div>
        <div className="text-sm font-black tabular-nums text-gray-900">{failedValue.toLocaleString()}</div>
      </div>
    );
  }, [failedValue]);

  const scheduleShow = (
    x: number,
    y: number,
    content: ReactNode,
    options?: { pinned?: boolean; autoHideMs?: number }
  ) => {
    pendingShowRef.current = { x, y, content, options };
    if (rafRef.current !== null) return;
    rafRef.current = window.requestAnimationFrame(() => {
      rafRef.current = null;
      const pending = pendingShowRef.current;
      if (!pending) return;
      tooltip.show(pending.x, pending.y, pending.content, pending.options);
    });
  };

  const chart = useMemo(() => {
    const unit = 8;
    const roundToUnit = (v: number) => Math.round(v / unit) * unit;
    const toHalfPixel = (v: number) => Math.round(v * 2) / 2;
    const w = 600;
    const h = height;
    const left = unit * 8;
    const right = unit * 2;
    const top = unit * 4;
    const bottomMin = unit * 4;
    let plotH = roundToUnit(h * 0.618);
    let bottom = h - top - plotH;
    if (bottom < bottomMin) {
      bottom = bottomMin;
      plotH = h - top - bottom;
      plotH = roundToUnit(plotH);
      bottom = h - top - plotH;
    }
    const plotW = w - left - right;

    const maxVal = Math.max(1, answeredValue, failedValue);
    const niceMax = Math.ceil(maxVal / 50) * 50;
    const ticks: number[] = [];
    for (let t = 0; t <= niceMax; t += 50) ticks.push(t);

    const barW = roundToUnit(clamp(plotW / 4, 72, 112));
    const gap = roundToUnit(Math.max(48, (plotW - barW * 2) / 3));
    const x1 = left + gap;
    const x2 = left + gap * 2 + barW;

    const baseY = top + plotH;
    const hFor = (v: number) => toHalfPixel((v / niceMax) * plotH);
    const yFor = (v: number) => toHalfPixel(baseY - hFor(v));

    return {
      w,
      h,
      left,
      right,
      top,
      bottom,
      plotW,
      plotH,
      baseY,
      niceMax,
      ticks,
      barW,
      x1,
      x2,
      yFor,
      hFor,
      unit,
      roundToUnit,
    };
  }, [answeredValue, failedValue, height]);

  const [active, setActive] = useState<"" | "answered" | "failed">("");
  const barTransition = useMemo(() => {
    return { duration: 0.55, ease: [0.22, 1, 0.36, 1] as [number, number, number, number] };
  }, []);

  const title = "Real-Time Call Statistics";
  const yAxisLabel = "Number of Calls";

  return (
    <div className="relative">
      <HoverTooltip state={tooltip.state} />
      <svg viewBox={`0 0 ${chart.w} ${chart.h}`} className="w-full" style={{ height }}>
        <text
          x={chart.w / 2}
          y={chart.unit * 2}
          textAnchor="middle"
          style={{ fontWeight: 800, fontSize: 14, fill: "#111827" }}
        >
          {title}
        </text>

        <text
          x={chart.unit * 2}
          y={chart.top + chart.plotH / 2}
          textAnchor="middle"
          transform={`rotate(-90 ${chart.unit * 2} ${chart.top + chart.plotH / 2})`}
          style={{ fontWeight: 600, fontSize: 12, fill: "#4B5563" }}
        >
          {yAxisLabel}
        </text>

        {chart.ticks.map((t) => {
          const y = chart.yFor(t);
          return (
            <g key={t}>
              <line x1={chart.left} y1={y} x2={chart.w - chart.right} y2={y} stroke="rgba(17,24,39,0.08)" strokeWidth={1} />
              <text
                x={chart.left - 10}
                y={y + 4}
                textAnchor="end"
                style={{ fontWeight: 600, fontSize: 12, fill: "#6B7280" }}
              >
                {t}
              </text>
            </g>
          );
        })}

        <line
          x1={chart.left}
          y1={chart.top}
          x2={chart.left}
          y2={chart.top + chart.plotH}
          stroke="rgba(17,24,39,0.22)"
          strokeWidth={1.5}
        />
        <line
          x1={chart.left}
          y1={chart.top + chart.plotH}
          x2={chart.w - chart.right}
          y2={chart.top + chart.plotH}
          stroke="rgba(17,24,39,0.22)"
          strokeWidth={1.5}
        />

        <motion.rect
          x={chart.x1}
          y={chart.yFor(answeredValue)}
          width={chart.barW}
          height={chart.hFor(answeredValue)}
          rx={10}
          fill="#008080"
          initial={{ y: chart.baseY, height: 0, filter: "none" }}
          animate={{
            y: chart.yFor(answeredValue),
            height: chart.hFor(answeredValue),
            filter: active === "answered" ? "drop-shadow(0px 10px 18px rgba(0,0,0,0.16))" : "none",
          }}
          transition={barTransition}
          tabIndex={0}
          role="img"
          aria-label={`Answered Calls: ${answeredValue}`}
          onMouseEnter={(e) => {
            setActive("answered");
            scheduleShow(e.clientX, e.clientY, answeredTooltipContent);
          }}
          onMouseMove={(e) => scheduleShow(e.clientX, e.clientY, answeredTooltipContent)}
          onMouseLeave={() => {
            setActive("");
            tooltip.hide();
          }}
          onPointerDown={(e) => {
            if (e.pointerType !== "touch") return;
            setActive("answered");
            tooltip.show(e.clientX, e.clientY, answeredTooltipContent, { pinned: true, autoHideMs: 3000 });
          }}
          onFocus={(e) => {
            setActive("answered");
            const rect = (e.currentTarget as unknown as SVGRectElement).getBoundingClientRect();
            tooltip.show(rect.left + rect.width / 2, rect.top, answeredTooltipContent);
          }}
          onBlur={() => {
            setActive("");
            tooltip.hide();
          }}
        />

        <motion.rect
          x={chart.x2}
          y={chart.yFor(failedValue)}
          width={chart.barW}
          height={chart.hFor(failedValue)}
          rx={10}
          fill="#FF0000"
          initial={{ y: chart.baseY, height: 0, filter: "none" }}
          animate={{
            y: chart.yFor(failedValue),
            height: chart.hFor(failedValue),
            filter: active === "failed" ? "drop-shadow(0px 10px 18px rgba(0,0,0,0.16))" : "none",
          }}
          transition={barTransition}
          tabIndex={0}
          role="img"
          aria-label={`Failed Calls: ${failedValue}`}
          onMouseEnter={(e) => {
            setActive("failed");
            scheduleShow(e.clientX, e.clientY, failedTooltipContent);
          }}
          onMouseMove={(e) => scheduleShow(e.clientX, e.clientY, failedTooltipContent)}
          onMouseLeave={() => {
            setActive("");
            tooltip.hide();
          }}
          onPointerDown={(e) => {
            if (e.pointerType !== "touch") return;
            setActive("failed");
            tooltip.show(e.clientX, e.clientY, failedTooltipContent, { pinned: true, autoHideMs: 3000 });
          }}
          onFocus={(e) => {
            setActive("failed");
            const rect = (e.currentTarget as unknown as SVGRectElement).getBoundingClientRect();
            tooltip.show(rect.left + rect.width / 2, rect.top, failedTooltipContent);
          }}
          onBlur={() => {
            setActive("");
            tooltip.hide();
          }}
        />

        <motion.text
          x={chart.x1 + chart.barW / 2}
          y={Math.max(chart.top + chart.unit * 2, chart.roundToUnit(chart.yFor(answeredValue) - chart.unit))}
          textAnchor="middle"
          style={{ fontWeight: 900, fontSize: 16, fill: "#111827" }}
          initial={{ y: chart.baseY - chart.unit, opacity: 0 }}
          animate={{
            y: Math.max(chart.top + chart.unit * 2, chart.roundToUnit(chart.yFor(answeredValue) - chart.unit)),
            opacity: 1,
          }}
          transition={barTransition}
        >
          {answeredValue}
        </motion.text>
        <motion.text
          x={chart.x2 + chart.barW / 2}
          y={Math.max(chart.top + chart.unit * 2, chart.roundToUnit(chart.yFor(failedValue) - chart.unit))}
          textAnchor="middle"
          style={{ fontWeight: 900, fontSize: 16, fill: "#111827" }}
          initial={{ y: chart.baseY - chart.unit, opacity: 0 }}
          animate={{
            y: Math.max(chart.top + chart.unit * 2, chart.roundToUnit(chart.yFor(failedValue) - chart.unit)),
            opacity: 1,
          }}
          transition={barTransition}
        >
          {failedValue}
        </motion.text>

        <text
          x={chart.x1 + chart.barW / 2}
          y={chart.top + chart.plotH + chart.unit * 2}
          textAnchor="middle"
          style={{ fontWeight: 800, fontSize: 12, fill: "#111827" }}
        >
          Answered Calls
        </text>
        <text
          x={chart.x2 + chart.barW / 2}
          y={chart.top + chart.plotH + chart.unit * 2}
          textAnchor="middle"
          style={{ fontWeight: 800, fontSize: 12, fill: "#111827" }}
        >
          Failed Calls
        </text>

        <text
          x={chart.left + chart.plotW / 2}
          y={chart.h - chart.unit}
          textAnchor="middle"
          style={{ fontWeight: 600, fontSize: 12, fill: "#6B7280" }}
        >
          Category
        </text>
      </svg>
    </div>
  );
}

export function RealTimeCallLineChart({
  points,
  height = 300,
}: {
  points: DualSeriesPoint[];
  height?: number;
}) {
  const tooltip = useHoverTooltip();
  const svgRef = useRef<SVGSVGElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const pendingShowRef = useRef<{
    x: number;
    y: number;
    content: ReactNode;
    options?: { pinned?: boolean; autoHideMs?: number };
  } | null>(null);

  const scheduleShow = (
    x: number,
    y: number,
    content: ReactNode,
    options?: { pinned?: boolean; autoHideMs?: number }
  ) => {
    pendingShowRef.current = { x, y, content, options };
    if (rafRef.current !== null) return;
    rafRef.current = window.requestAnimationFrame(() => {
      rafRef.current = null;
      const pending = pendingShowRef.current;
      if (!pending) return;
      tooltip.show(pending.x, pending.y, pending.content, pending.options);
    });
  };

  const chart = useMemo(() => {
    const w = 760;
    const h = height;
    const left = 56;
    const right = 20;
    const top = 22;
    const bottom = 44;
    const plotW = w - left - right;
    const plotH = h - top - bottom;
    const baseY = top + plotH;

    const maxVal = Math.max(
      1,
      ...points.map((p) => Math.max(0, p.a, p.b))
    );
    const magnitude = Math.pow(10, Math.floor(Math.log10(maxVal)));
    const niceSteps = [1, 2, 2.5, 5, 10];
    const target = maxVal / magnitude;
    const stepBase = niceSteps.find((s) => target <= s) ?? 10;
    const niceMax = Math.ceil((maxVal / (stepBase * magnitude))) * stepBase * magnitude;

    const ticksCount = 5;
    const ticks: number[] = [];
    for (let i = 0; i < ticksCount; i++) {
      ticks.push(Math.round((i * niceMax) / (ticksCount - 1)));
    }

    const xFor = (i: number) => {
      if (points.length <= 1) return left + plotW / 2;
      return left + (i * plotW) / (points.length - 1);
    };
    const yFor = (v: number) => top + plotH - (clamp(v, 0, niceMax) / niceMax) * plotH;

    return { w, h, left, right, top, bottom, plotW, plotH, baseY, niceMax, ticks, xFor, yFor };
  }, [height, points]);

  const answeredPoints = useMemo(() => {
    return points.map((p, i) => ({ x: chart.xFor(i), y: chart.yFor(p.a) }));
  }, [chart, points]);

  const failedPoints = useMemo(() => {
    return points.map((p, i) => ({ x: chart.xFor(i), y: chart.yFor(p.b) }));
  }, [chart, points]);

  const answeredD = useMemo(() => toPath(answeredPoints, 0.16), [answeredPoints]);
  const failedD = useMemo(() => toPath(failedPoints, 0.16), [failedPoints]);

  const [activeIndex, setActiveIndex] = useState<number | null>(null);

  const showForIndex = (idx: number, clientX?: number, clientY?: number) => {
    const p = points[idx];
    if (!p) return;
    const content = (
      <div className="space-y-1">
        <div className="text-sm font-black text-gray-900">{p.label}</div>
        <div className="flex items-center justify-between gap-6 text-sm">
          <span className="text-emerald-700 font-bold">Answered</span>
          <span className="tabular-nums font-black text-gray-900">{p.a.toLocaleString()}</span>
        </div>
        <div className="flex items-center justify-between gap-6 text-sm">
          <span className="text-red-700 font-bold">Failed</span>
          <span className="tabular-nums font-black text-gray-900">{p.b.toLocaleString()}</span>
        </div>
      </div>
    );

    if (typeof clientX === "number" && typeof clientY === "number") {
      scheduleShow(clientX, clientY, content);
      return;
    }

    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    scheduleShow(rect.left + rect.width / 2, rect.top + rect.height * 0.2, content);
  };

  const handlePointerMove = (clientX: number, clientY: number) => {
    const svg = svgRef.current;
    if (!svg || points.length === 0) return;
    const rect = svg.getBoundingClientRect();
    const x = clientX - rect.left;
    const clamped = clamp(x, chart.left, chart.left + chart.plotW);
    const t = (clamped - chart.left) / Math.max(1, chart.plotW);
    const idx = Math.round(t * Math.max(0, points.length - 1));
    const next = clamp(idx, 0, Math.max(0, points.length - 1));
    setActiveIndex(next);
    showForIndex(next, clientX, clientY);
  };

  const activeX = activeIndex !== null ? chart.xFor(activeIndex) : null;
  const activeAnsweredY = activeIndex !== null ? chart.yFor(points[activeIndex]?.a ?? 0) : null;
  const activeFailedY = activeIndex !== null ? chart.yFor(points[activeIndex]?.b ?? 0) : null;

  const xTicks = useMemo(() => {
    if (points.length === 0) return [];
    const indices = Array.from(new Set([0, Math.floor((points.length - 1) / 3), Math.floor(((points.length - 1) * 2) / 3), points.length - 1]));
    return indices.map((i) => ({ i, x: chart.xFor(i), label: points[i]?.label ?? "" }));
  }, [chart, points]);

  return (
    <div className="relative">
      <HoverTooltip state={tooltip.state} />
      <svg
        ref={svgRef}
        viewBox={`0 0 ${chart.w} ${chart.h}`}
        preserveAspectRatio="xMidYMid meet"
        className="w-full"
        style={{ height }}
        role="img"
        aria-label="Real-time answered and failed calls over time"
        tabIndex={0}
        onKeyDown={(e) => {
          if (points.length === 0) return;
          if (e.key !== "ArrowLeft" && e.key !== "ArrowRight" && e.key !== "Home" && e.key !== "End") return;
          e.preventDefault();
          const current = activeIndex ?? points.length - 1;
          const next =
            e.key === "ArrowLeft"
              ? Math.max(0, current - 1)
              : e.key === "ArrowRight"
                ? Math.min(points.length - 1, current + 1)
                : e.key === "Home"
                  ? 0
                  : points.length - 1;
          setActiveIndex(next);
          showForIndex(next);
        }}
        onMouseMove={(e) => handlePointerMove(e.clientX, e.clientY)}
        onMouseLeave={() => {
          setActiveIndex(null);
          tooltip.hide();
        }}
        onPointerDown={(e) => {
          if (e.pointerType !== "touch") return;
          const svg = svgRef.current;
          if (!svg || points.length === 0) return;
          const rect = svg.getBoundingClientRect();
          const x = e.clientX - rect.left;
          const clamped = clamp(x, chart.left, chart.left + chart.plotW);
          const t = (clamped - chart.left) / Math.max(1, chart.plotW);
          const idx = clamp(Math.round(t * Math.max(0, points.length - 1)), 0, Math.max(0, points.length - 1));
          setActiveIndex(idx);
          const p = points[idx];
          if (!p) return;
          tooltip.show(
            e.clientX,
            e.clientY,
            (
              <div className="space-y-1">
                <div className="text-sm font-black text-gray-900">{p.label}</div>
                <div className="flex items-center justify-between gap-6 text-sm">
                  <span className="text-emerald-700 font-bold">Answered</span>
                  <span className="tabular-nums font-black text-gray-900">{p.a.toLocaleString()}</span>
                </div>
                <div className="flex items-center justify-between gap-6 text-sm">
                  <span className="text-red-700 font-bold">Failed</span>
                  <span className="tabular-nums font-black text-gray-900">{p.b.toLocaleString()}</span>
                </div>
              </div>
            ),
            { pinned: true, autoHideMs: 3000 }
          );
        }}
        onFocus={() => {
          if (points.length === 0) return;
          const idx = activeIndex ?? Math.max(0, points.length - 1);
          setActiveIndex(idx);
          showForIndex(idx);
        }}
        onBlur={() => {
          setActiveIndex(null);
          tooltip.hide();
        }}
      >
        <rect x={chart.left} y={chart.top} width={chart.plotW} height={chart.plotH} fill="rgba(17,24,39,0.02)" rx={14} />

        {chart.ticks.map((t) => {
          const y = chart.yFor(t);
          return (
            <g key={t}>
              <line
                x1={chart.left}
                y1={y}
                x2={chart.left + chart.plotW}
                y2={y}
                stroke="rgba(17,24,39,0.10)"
                strokeWidth={1}
                strokeDasharray="4 6"
                vectorEffect="non-scaling-stroke"
              />
              <text
                x={chart.left - 12}
                y={y + 4}
                textAnchor="end"
                style={{ fontWeight: 700, fontSize: 12, fill: "#6B7280" }}
              >
                {t}
              </text>
            </g>
          );
        })}

        <line
          x1={chart.left}
          y1={chart.top}
          x2={chart.left}
          y2={chart.top + chart.plotH}
          stroke="rgba(17,24,39,0.30)"
          strokeWidth={1.5}
          vectorEffect="non-scaling-stroke"
        />
        <line
          x1={chart.left}
          y1={chart.baseY}
          x2={chart.left + chart.plotW}
          y2={chart.baseY}
          stroke="rgba(17,24,39,0.30)"
          strokeWidth={1.5}
          vectorEffect="non-scaling-stroke"
        />

        <text
          x={chart.left + chart.plotW / 2}
          y={chart.h - 10}
          textAnchor="middle"
          style={{ fontWeight: 700, fontSize: 12, fill: "#6B7280" }}
        >
          Time
        </text>

        <text
          x={16}
          y={chart.top + chart.plotH / 2}
          textAnchor="middle"
          transform={`rotate(-90 16 ${chart.top + chart.plotH / 2})`}
          style={{ fontWeight: 700, fontSize: 12, fill: "#6B7280" }}
        >
          Calls
        </text>

        <g transform={`translate(${chart.left + 10}, ${chart.top - 8})`}>
          <g>
            <rect x={0} y={0} width={10} height={10} rx={3} fill="#059669" />
            <text x={16} y={9} style={{ fontWeight: 800, fontSize: 12, fill: "#111827" }}>
              Answered
            </text>
          </g>
          <g transform="translate(102, 0)">
            <rect x={0} y={0} width={10} height={10} rx={3} fill="#DC2626" />
            <text x={16} y={9} style={{ fontWeight: 800, fontSize: 12, fill: "#111827" }}>
              Failed
            </text>
          </g>
        </g>

        <motion.path
          d={answeredD}
          fill="none"
          stroke="#059669"
          strokeWidth={3}
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
          initial={false}
          animate={{ d: answeredD }}
          transition={{ duration: 0.35, ease: "easeInOut" }}
        />
        <motion.path
          d={failedD}
          fill="none"
          stroke="#DC2626"
          strokeWidth={3}
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
          initial={false}
          animate={{ d: failedD }}
          transition={{ duration: 0.35, ease: "easeInOut" }}
        />

        {activeX !== null ? (
          <g>
            <line
              x1={activeX}
              y1={chart.top}
              x2={activeX}
              y2={chart.baseY}
              stroke="rgba(17,24,39,0.18)"
              strokeWidth={1.5}
              vectorEffect="non-scaling-stroke"
            />
            {activeAnsweredY !== null ? (
              <circle
                cx={activeX}
                cy={activeAnsweredY}
                r={6}
                fill="#059669"
                stroke="rgba(255,255,255,0.95)"
                strokeWidth={2}
                vectorEffect="non-scaling-stroke"
              />
            ) : null}
            {activeFailedY !== null ? (
              <circle
                cx={activeX}
                cy={activeFailedY}
                r={6}
                fill="#DC2626"
                stroke="rgba(255,255,255,0.95)"
                strokeWidth={2}
                vectorEffect="non-scaling-stroke"
              />
            ) : null}
          </g>
        ) : null}

        {xTicks.map((t) => (
          <text
            key={t.i}
            x={t.x}
            y={chart.baseY + 24}
            textAnchor="middle"
            style={{ fontWeight: 700, fontSize: 12, fill: "#4B5563" }}
          >
            {t.label}
          </text>
        ))}

        <rect
          x={chart.left}
          y={chart.top}
          width={chart.plotW}
          height={chart.plotH}
          fill="transparent"
          onMouseMove={(e) => handlePointerMove(e.clientX, e.clientY)}
          onMouseLeave={() => {
            setActiveIndex(null);
            tooltip.hide();
          }}
        />
      </svg>
    </div>
  );
}

export function DonutChart({
  value,
  total,
  size = 132,
  strokeWidth = 14,
  valueColor = "#10B981",
  failedColor = "#EF4444",
  trackColor = "rgba(17, 24, 39, 0.10)",
  showSegmentLabels = true,
}: {
  value: number;
  total: number;
  size?: number;
  strokeWidth?: number;
  valueColor?: string;
  failedColor?: string;
  trackColor?: string;
  showSegmentLabels?: boolean;
}) {
  const tooltip = useHoverTooltip();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [renderSize, setRenderSize] = useState(size);

  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      const next = Math.max(64, Math.min(size, Math.floor(entry.contentRect.width)));
      setRenderSize((prev) => (prev === next ? prev : next));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [size]);

  const r = (renderSize - strokeWidth) / 2;
  const c = 2 * Math.PI * r;
  const answered = Math.max(0, value);
  const failed = Math.max(0, total - answered);
  const answeredPct = total > 0 ? clamp(answered / total, 0, 1) : 0;
  const failedPct = total > 0 ? clamp(failed / total, 0, 1) : 0;
  const answeredDash = c * answeredPct;
  const failedDash = c * failedPct;
  const cx = renderSize / 2;
  const cy = renderSize / 2;
  const rInner = Math.max(0, r - strokeWidth / 2);
  const rOuter = r + strokeWidth / 2;
  const paddingPx = 6;
  const baseAngle = -Math.PI / 2;

  const answeredText = useMemo(() => `Answered ${answered.toLocaleString()}`, [answered]);
  const failedText = useMemo(() => `Failed ${failed.toLocaleString()}`, [failed]);

  const answeredLayout = useMemo(() => {
    return computeDonutSegmentTextLayout({
      cx,
      cy,
      startAngleRad: baseAngle,
      sweepAngleRad: answeredPct * 2 * Math.PI,
      rInner,
      rOuter,
      paddingPx,
      text: answeredText,
      minFontPx: 8,
      maxFontPx: 10,
    });
  }, [answeredPct, answeredText, baseAngle, cx, cy, rInner, rOuter]);

  const failedLayout = useMemo(() => {
    return computeDonutSegmentTextLayout({
      cx,
      cy,
      startAngleRad: baseAngle + answeredPct * 2 * Math.PI,
      sweepAngleRad: failedPct * 2 * Math.PI,
      rInner,
      rOuter,
      paddingPx,
      text: failedText,
      minFontPx: 8,
      maxFontPx: 10,
    });
  }, [answeredPct, failedPct, failedText, baseAngle, cx, cy, rInner, rOuter]);

  const answeredLabelVisible = answeredLayout.render && !(answeredLayout.lines.length === 1 && answeredLayout.lines[0] === "…");
  const failedLabelVisible = failedLayout.render && !(failedLayout.lines.length === 1 && failedLayout.lines[0] === "…");

  const summaryContent = (
    <div className="space-y-2">
      <div className="text-sm font-black text-gray-900">Calls breakdown</div>
      <div className="space-y-1.5">
        <div className="flex items-center justify-between gap-6">
          <span className="text-emerald-700 font-bold">Answered</span>
          <span className="tabular-nums font-black text-gray-900">
            {answered.toLocaleString()}{" "}
            <span className="text-gray-600 font-semibold">
              ({Math.round(answeredPct * 100)}%)
            </span>
          </span>
        </div>
        <div className="flex items-center justify-between gap-6">
          <span className="text-red-700 font-bold">Failed</span>
          <span className="tabular-nums font-black text-gray-900">
            {failed.toLocaleString()}{" "}
            <span className="text-gray-600 font-semibold">({Math.round(failedPct * 100)}%)</span>
          </span>
        </div>
      </div>
      <div className="text-xs font-semibold text-gray-600">Total: {total.toLocaleString()}</div>
    </div>
  );

  const answeredContent = (
    <div className="space-y-1">
      <div className="text-sm font-black text-gray-900">Answered</div>
      <div className="flex items-center justify-between gap-6">
        <span className="tabular-nums font-black text-gray-900">{answered.toLocaleString()}</span>
        <span className="text-gray-600 font-semibold">{Math.round(answeredPct * 100)}%</span>
      </div>
    </div>
  );

  const failedContent = (
    <div className="space-y-1">
      <div className="text-sm font-black text-gray-900">Failed</div>
      <div className="flex items-center justify-between gap-6">
        <span className="tabular-nums font-black text-gray-900">{failed.toLocaleString()}</span>
        <span className="text-gray-600 font-semibold">{Math.round(failedPct * 100)}%</span>
      </div>
    </div>
  );

  return (
    <motion.div
      ref={containerRef}
      className="relative flex items-center justify-center outline-none"
      tabIndex={0}
      role="img"
      aria-label={`Answered ${answered} (${Math.round(answeredPct * 100)}%), Failed ${failed} (${Math.round(failedPct * 100)}%), Total ${total}`}
      whileHover={{ scale: 1.01, filter: "drop-shadow(0px 10px 18px rgba(0,0,0,0.14))" }}
      transition={{ duration: 0.25, ease: "easeInOut" }}
      onMouseEnter={(e) => tooltip.show(e.clientX, e.clientY, summaryContent)}
      onMouseMove={(e) => tooltip.show(e.clientX, e.clientY, summaryContent)}
      onMouseLeave={() => tooltip.hide()}
      onPointerDown={(e) => {
        if (e.pointerType === "touch") tooltip.show(e.clientX, e.clientY, summaryContent, { pinned: true, autoHideMs: 3000 });
      }}
      onFocus={(e) => {
        const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
        tooltip.show(rect.left + rect.width / 2, rect.top + rect.height / 2, summaryContent);
      }}
      onBlur={() => tooltip.hide()}
    >
      <HoverTooltip state={tooltip.state} />
      <svg
        width={renderSize}
        height={renderSize}
        viewBox={`0 0 ${renderSize} ${renderSize}`}
        className="shrink-0 overflow-visible"
      >
        <circle cx={cx} cy={cy} r={r} stroke={trackColor} strokeWidth={strokeWidth} fill="none" />
        <motion.circle
          cx={cx}
          cy={cy}
          r={r}
          stroke={failedColor}
          strokeWidth={strokeWidth}
          fill="none"
          strokeLinecap="round"
          strokeDasharray={`${failedDash} ${Math.max(0, c - failedDash)}`}
          strokeDashoffset={c * 0.25 - answeredDash}
          initial={false}
          animate={{ strokeDasharray: `${failedDash} ${Math.max(0, c - failedDash)}` }}
          transition={{ type: "spring", stiffness: 120, damping: 20 }}
          whileHover={{
            strokeWidth: strokeWidth * 1.2,
            filter: "drop-shadow(0px 8px 14px rgba(0,0,0,0.16))",
            transition: { duration: 0.3, ease: "easeInOut" },
          }}
          onMouseEnter={(e) => tooltip.show(e.clientX, e.clientY, failedContent)}
          onMouseMove={(e) => tooltip.show(e.clientX, e.clientY, failedContent)}
          onMouseLeave={() => tooltip.hide()}
          onPointerDown={(e) => {
            if (e.pointerType === "touch") tooltip.show(e.clientX, e.clientY, failedContent, { pinned: true, autoHideMs: 2500 });
          }}
        />
        <motion.circle
          cx={cx}
          cy={cy}
          r={r}
          stroke={valueColor}
          strokeWidth={strokeWidth}
          fill="none"
          strokeLinecap="round"
          strokeDasharray={`${answeredDash} ${Math.max(0, c - answeredDash)}`}
          strokeDashoffset={c * 0.25}
          initial={false}
          animate={{ strokeDasharray: `${answeredDash} ${Math.max(0, c - answeredDash)}` }}
          transition={{ type: "spring", stiffness: 120, damping: 20 }}
          whileHover={{
            strokeWidth: strokeWidth * 1.2,
            filter: "drop-shadow(0px 8px 14px rgba(0,0,0,0.16))",
            transition: { duration: 0.3, ease: "easeInOut" },
          }}
          onMouseEnter={(e) => tooltip.show(e.clientX, e.clientY, answeredContent)}
          onMouseMove={(e) => tooltip.show(e.clientX, e.clientY, answeredContent)}
          onMouseLeave={() => tooltip.hide()}
          onPointerDown={(e) => {
            if (e.pointerType === "touch") tooltip.show(e.clientX, e.clientY, answeredContent, { pinned: true, autoHideMs: 2500 });
          }}
        />

        {showSegmentLabels && answeredLabelVisible ? (
          <motion.text
            x={answeredLayout.x}
            y={answeredLayout.y}
            textAnchor="middle"
            dominantBaseline="middle"
            style={{ fontWeight: 800, fontSize: answeredLayout.fontPx, fill: "#111827" }}
            initial={{ opacity: 0, y: answeredLayout.y + 2 }}
            animate={{ opacity: 1, y: answeredLayout.y }}
            transition={{ duration: 0.55, ease: [0.22, 1, 0.36, 1] }}
          >
            {answeredLayout.lines.map((line, i) => (
              <tspan key={i} x={answeredLayout.x} dy={i === 0 ? 0 : answeredLayout.fontPx * 1.05}>
                {line}
              </tspan>
            ))}
            {answeredLayout.truncated ? <title>{answeredLayout.fullText}</title> : null}
          </motion.text>
        ) : null}

        {showSegmentLabels && failedLabelVisible ? (
          <motion.text
            x={failedLayout.x}
            y={failedLayout.y}
            textAnchor="middle"
            dominantBaseline="middle"
            style={{ fontWeight: 800, fontSize: failedLayout.fontPx, fill: "#111827" }}
            initial={{ opacity: 0, y: failedLayout.y + 2 }}
            animate={{ opacity: 1, y: failedLayout.y }}
            transition={{ duration: 0.55, ease: [0.22, 1, 0.36, 1] }}
          >
            {failedLayout.lines.map((line, i) => (
              <tspan key={i} x={failedLayout.x} dy={i === 0 ? 0 : failedLayout.fontPx * 1.05}>
                {line}
              </tspan>
            ))}
            {failedLayout.truncated ? <title>{failedLayout.fullText}</title> : null}
          </motion.text>
        ) : null}
      </svg>
    </motion.div>
  );
}

export function Heatmap({
  rows,
  cols,
  cellValues,
  maxValue,
}: {
  rows: string[];
  cols: string[];
  cellValues: number[][];
  maxValue: number;
}) {
  const tooltip = useHoverTooltip();
  return (
    <div className="relative overflow-x-auto">
      <HoverTooltip state={tooltip.state} />
      <div className="min-w-[520px]">
        <div className="grid" style={{ gridTemplateColumns: `120px repeat(${cols.length}, minmax(0, 1fr))` }}>
          <div />
          {cols.map((c) => (
            <div key={c} className="text-[11px] font-semibold text-gray-600 text-center pb-2">
              {c}
            </div>
          ))}

          {rows.map((r, ri) => (
            <div key={r} className="contents">
              <div className="text-xs font-semibold text-gray-700 pr-3 flex items-center">{r}</div>
              {cols.map((c, ci) => {
                const v = cellValues[ri]?.[ci] ?? 0;
                const t = maxValue > 0 ? clamp(v / maxValue, 0, 1) : 0;
                const bg = `rgba(16, 185, 129, ${0.06 + t * 0.5})`;
                return (
                  <div key={`${r}-${c}`} className="p-1">
                    <div
                      className="h-6 rounded-md border border-black/5 transition-all duration-300 ease-in-out hover:border-gray-900/10 hover:shadow-sm dark:hover:border-gray-900/20 dark:hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-900/20"
                      style={{ background: bg }}
                      tabIndex={0}
                      role="img"
                      aria-label={`${r} ${c}: ${v}`}
                      onMouseEnter={(e) =>
                        tooltip.show(e.clientX, e.clientY, (
                          <div className="flex items-center gap-2">
                            <span className="text-gray-700">{r}</span>
                            <span className="text-gray-600">{c}</span>
                            <span className="font-black tabular-nums">{v.toLocaleString()}</span>
                          </div>
                        ))
                      }
                      onMouseMove={(e) =>
                        tooltip.show(e.clientX, e.clientY, (
                          <div className="flex items-center gap-2">
                            <span className="text-gray-700">{r}</span>
                            <span className="text-gray-600">{c}</span>
                            <span className="font-black tabular-nums">{v.toLocaleString()}</span>
                          </div>
                        ))
                      }
                      onMouseLeave={() => tooltip.hide()}
                      onPointerDown={(e) => {
                        if (e.pointerType === "touch") {
                          tooltip.show(e.clientX, e.clientY, (
                            <div className="flex items-center gap-2">
                              <span className="text-gray-700">{r}</span>
                              <span className="text-gray-600">{c}</span>
                              <span className="font-black tabular-nums">{v.toLocaleString()}</span>
                            </div>
                          ), { pinned: true, autoHideMs: 2500 });
                        }
                      }}
                      onFocus={(e) => {
                        const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
                        tooltip.show(rect.left + rect.width / 2, rect.top, (
                          <div className="flex items-center gap-2">
                            <span className="text-gray-700">{r}</span>
                            <span className="text-gray-600">{c}</span>
                            <span className="font-black tabular-nums">{v.toLocaleString()}</span>
                          </div>
                        ));
                      }}
                      onBlur={() => tooltip.hide()}
                    />
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function StackedAreaChart({
  points,
  height = 160,
  aColor = "rgba(16, 185, 129, 0.22)",
  bColor = "rgba(239, 68, 68, 0.18)",
  aStroke = "rgba(16, 185, 129, 0.95)",
  bStroke = "rgba(239, 68, 68, 0.85)",
}: {
  points: DualSeriesPoint[];
  height?: number;
  aColor?: string;
  bColor?: string;
  aStroke?: string;
  bStroke?: string;
}) {
  const tooltip = useHoverTooltip();
  const width = 600;
  const paddingX = 10;
  const paddingY = 16;
  const [activeIndex, setActiveIndex] = useState<number | null>(null);

  const data = useMemo(() => {
    const totals = points.map((p) => p.a + p.b);
    const max = Math.max(...totals, 1);

    const xForIndex = (i: number) => paddingX + (i * (width - paddingX * 2)) / Math.max(1, points.length - 1);
    const yForValue = (v: number) => paddingY + (1 - v / max) * (height - paddingY * 2);

    const topLine = points.map((p, i) => ({ x: xForIndex(i), y: yForValue(p.a + p.b) }));
    const midLine = points.map((p, i) => ({ x: xForIndex(i), y: yForValue(p.a) }));

    const topD = toPath(topLine, 0.16);
    const midD = toPath(midLine, 0.16);

    const baselineY = height - paddingY;
    const topArea =
      topLine.length > 0
        ? `${topD} L ${topLine[topLine.length - 1].x} ${baselineY} L ${topLine[0].x} ${baselineY} Z`
        : "";
    const midArea =
      midLine.length > 0
        ? `${midD} L ${midLine[midLine.length - 1].x} ${baselineY} L ${midLine[0].x} ${baselineY} Z`
        : "";

    return { topLine, midLine, topD, midD, topArea, midArea, baselineY };
  }, [height, paddingX, paddingY, points]);

  const indexForClientX = (clientX: number, rect: DOMRect) => {
    const x = ((clientX - rect.left) / Math.max(1, rect.width)) * width;
    const t = (x - paddingX) / Math.max(1, width - paddingX * 2);
    const raw = Math.round(t * Math.max(1, points.length - 1));
    return clamp(raw, 0, Math.max(0, points.length - 1));
  };

  const showForIndex = (i: number, clientX: number, clientY: number, pin?: boolean) => {
    const p = points[i];
    if (!p) return;
    const total = p.a + p.b;
    const aPct = total > 0 ? Math.round((p.a / total) * 100) : 0;
    const bPct = total > 0 ? Math.round((p.b / total) * 100) : 0;
    tooltip.show(
      clientX,
      clientY,
      <div className="space-y-2">
        <div className="text-sm font-black text-gray-900">{p.label}</div>
        <div className="space-y-1.5">
          <div className="flex items-center justify-between gap-6">
            <span className="text-emerald-700 font-bold">Answered</span>
            <span className="tabular-nums font-black text-gray-900">
              {p.a.toLocaleString()} <span className="text-gray-600 font-semibold">({aPct}%)</span>
            </span>
          </div>
          <div className="flex items-center justify-between gap-6">
            <span className="text-red-700 font-bold">Failed</span>
            <span className="tabular-nums font-black text-gray-900">
              {p.b.toLocaleString()} <span className="text-gray-600 font-semibold">({bPct}%)</span>
            </span>
          </div>
        </div>
        <div className="text-xs font-semibold text-gray-600">Total: {total.toLocaleString()}</div>
      </div>,
      pin ? { pinned: true, autoHideMs: 3000 } : undefined
    );
  };

  const activeX = activeIndex !== null ? data.midLine[activeIndex]?.x ?? null : null;
  const activeMid = activeIndex !== null ? data.midLine[activeIndex] ?? null : null;
  const activeTop = activeIndex !== null ? data.topLine[activeIndex] ?? null : null;

  return (
    <div className="relative">
      <HoverTooltip state={tooltip.state} className="w-[260px] text-sm font-semibold" />
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full h-40"
        onPointerMove={(e) => {
          const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const idx = indexForClientX(e.clientX, rect);
          setActiveIndex(idx);
          showForIndex(idx, e.clientX, e.clientY);
        }}
        onPointerLeave={() => {
          setActiveIndex(null);
          tooltip.hide();
        }}
        onPointerDown={(e) => {
          if (e.pointerType !== "touch") return;
          const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const idx = indexForClientX(e.clientX, rect);
          setActiveIndex(idx);
          showForIndex(idx, e.clientX, e.clientY, true);
        }}
        role="img"
        aria-label="Stacked area chart of answered and failed calls"
      >
        <path d={data.topArea} fill={bColor} />
        <path d={data.midArea} fill={aColor} />
        <path d={data.topD} fill="none" stroke={bStroke} strokeWidth={2.25} strokeLinejoin="round" strokeLinecap="round" />
        <path d={data.midD} fill="none" stroke={aStroke} strokeWidth={2.25} strokeLinejoin="round" strokeLinecap="round" />
        {activeX !== null ? (
          <>
            <motion.line
              x1={activeX}
              x2={activeX}
              y1={paddingY}
              y2={height - paddingY}
              stroke="rgba(17, 24, 39, 0.18)"
              strokeWidth={2}
              initial={false}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.3, ease: "easeInOut" }}
            />
            {activeMid ? (
              <motion.circle
                cx={activeMid.x}
                cy={activeMid.y}
                r={5}
                fill={aStroke}
                stroke="rgba(255,255,255,0.9)"
                strokeWidth={2}
                initial={false}
                animate={{ r: 5.4 }}
                transition={{ duration: 0.3, ease: "easeInOut" }}
              />
            ) : null}
            {activeTop ? (
              <motion.circle
                cx={activeTop.x}
                cy={activeTop.y}
                r={5}
                fill={bStroke}
                stroke="rgba(255,255,255,0.9)"
                strokeWidth={2}
                initial={false}
                animate={{ r: 5.4 }}
                transition={{ duration: 0.3, ease: "easeInOut" }}
              />
            ) : null}
          </>
        ) : null}
      </svg>
    </div>
  );
}

export type FeedItem = {
  id: string;
  title: string;
  detail: string;
  timeLabel: string;
  tone?: "neutral" | "good" | "warn" | "bad";
};

export function ActivityFeed({ items }: { items: FeedItem[] }) {
  const tooltip = useHoverTooltip();
  const [activeId, setActiveId] = useState<string>("");
  return (
    <div className="relative space-y-2">
      <HoverTooltip state={tooltip.state} className="w-[320px] text-sm font-semibold p-4" />
      {items.map((item) => (
        <div
          key={item.id}
          className="group relative rounded-xl border border-transparent px-2 py-2 transition-all duration-300 ease-in-out hover:scale-[1.01] active:scale-[0.99] hover:border-gray-200 hover:bg-gray-50/80 hover:shadow-sm focus-within:border-gray-200 focus-within:bg-gray-50/80 focus-within:shadow-sm dark:border-border dark:bg-background/70 dark:shadow-sm dark:transition-[transform,background-color,box-shadow] dark:duration-150 dark:ease-out dark:hover:scale-[1.01] dark:hover:border-border dark:hover:bg-gray-800 dark:hover:shadow-md dark:active:scale-[0.99] dark:focus-within:border-border dark:focus-within:bg-gray-800 dark:focus-within:shadow-md"
          onMouseEnter={(e) => {
            setActiveId(item.id);
            tooltip.show(e.clientX, e.clientY, (
              <div className="space-y-1.5">
                <div className="text-sm font-black text-gray-900">{item.title}</div>
                <div className="text-xs text-gray-700">{item.detail}</div>
                <div className="text-[11px] font-semibold text-gray-600">Time: {item.timeLabel}</div>
              </div>
            ));
          }}
          onMouseMove={(e) => {
            tooltip.show(e.clientX, e.clientY, (
              <div className="space-y-1.5">
                <div className="text-sm font-black text-gray-900">{item.title}</div>
                <div className="text-xs text-gray-700">{item.detail}</div>
                <div className="text-[11px] font-semibold text-gray-600">Time: {item.timeLabel}</div>
              </div>
            ));
          }}
          onMouseLeave={() => {
            setActiveId("");
            tooltip.hide();
          }}
          onPointerDown={(e) => {
            if (e.pointerType !== "touch") return;
            setActiveId(item.id);
            tooltip.show(e.clientX, e.clientY, (
              <div className="space-y-1.5">
                <div className="text-sm font-black text-gray-900">{item.title}</div>
                <div className="text-xs text-gray-700">{item.detail}</div>
                <div className="text-[11px] font-semibold text-gray-600">Time: {item.timeLabel}</div>
              </div>
            ), { pinned: true, autoHideMs: 3500 });
          }}
          tabIndex={0}
          role="group"
          aria-label={`${item.title}: ${item.detail}`}
          onKeyDown={(e) => {
            if (e.key !== "Enter" && e.key !== " ") return;
            const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
            tooltip.show(rect.left + rect.width / 2, rect.top + rect.height / 2, (
              <div className="space-y-1.5">
                <div className="text-sm font-black text-gray-900">{item.title}</div>
                <div className="text-xs text-gray-700">{item.detail}</div>
                <div className="text-[11px] font-semibold text-gray-600">Time: {item.timeLabel}</div>
              </div>
            ), { pinned: true, autoHideMs: 4000 });
          }}
          onFocus={(e) => {
            const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
            setActiveId(item.id);
            tooltip.show(rect.left + rect.width / 2, rect.top + rect.height / 2, (
              <div className="space-y-1.5">
                <div className="text-sm font-black text-gray-900">{item.title}</div>
                <div className="text-xs text-gray-700">{item.detail}</div>
                <div className="text-[11px] font-semibold text-gray-600">Time: {item.timeLabel}</div>
              </div>
            ));
          }}
          onBlur={() => {
            setActiveId("");
            tooltip.hide();
          }}
        >
          <div className="absolute left-0 top-2 bottom-2 w-[2px] origin-left bg-gray-200 transition-transform duration-300 ease-in-out group-hover:scale-x-[1.2] dark:bg-border dark:group-hover:scale-x-[1.2]" />
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <div
                  className={
                    item.tone === "good"
                      ? `w-2.5 h-2.5 rounded-full bg-emerald-500 transition-transform duration-300 ease-in-out ${activeId === item.id ? "scale-110" : ""}`
                      : item.tone === "warn"
                        ? `w-2.5 h-2.5 rounded-full bg-yellow-500 transition-transform duration-300 ease-in-out ${activeId === item.id ? "scale-110" : ""}`
                        : item.tone === "bad"
                          ? `w-2.5 h-2.5 rounded-full bg-red-500 transition-transform duration-300 ease-in-out ${activeId === item.id ? "scale-110" : ""}`
                          : `w-2.5 h-2.5 rounded-full bg-gray-400 transition-transform duration-300 ease-in-out ${activeId === item.id ? "scale-110" : ""}`
                  }
                />
                <p className="text-sm font-bold text-gray-900 dark:text-foreground truncate">{item.title}</p>
              </div>
              <p className="text-xs text-gray-600 dark:text-muted-foreground mt-1">{item.detail}</p>
            </div>
            <div className="text-[11px] font-semibold text-gray-500 dark:text-muted-foreground shrink-0">{item.timeLabel}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

export type TimelineItem = {
  id: string;
  title: string;
  detail: string;
  timeLabel: string;
  severity: "info" | "warn" | "error";
};

export function AlertTimeline({ items }: { items: TimelineItem[] }) {
  const tooltip = useHoverTooltip();
  const [activeId, setActiveId] = useState<string>("");
  return (
    <div className="relative space-y-2">
      <HoverTooltip state={tooltip.state} className="w-[340px] text-sm font-semibold p-4" />
      {items.map((item) => (
        <div
          key={item.id}
          className="group relative rounded-xl border border-transparent px-2 py-2 transition-all duration-300 ease-in-out hover:scale-[1.01] active:scale-[0.99] hover:border-gray-200 hover:bg-gray-50/80 hover:shadow-sm focus-within:border-gray-200 focus-within:bg-gray-50/80 focus-within:shadow-sm dark:border-border dark:bg-background/70 dark:shadow-sm dark:transition-[transform,background-color,box-shadow] dark:duration-150 dark:ease-out dark:hover:scale-[1.01] dark:hover:border-border dark:hover:bg-gray-800 dark:hover:shadow-md dark:active:scale-[0.99] dark:focus-within:border-border dark:focus-within:bg-gray-800 dark:focus-within:shadow-md"
          onMouseEnter={(e) => {
            setActiveId(item.id);
            tooltip.show(e.clientX, e.clientY, (
              <div className="space-y-1.5">
                <div className="text-sm font-black text-gray-900">{item.title}</div>
                <div className="text-xs text-gray-700">{item.detail}</div>
                <div className="text-[11px] font-semibold text-gray-600">Date: {item.timeLabel}</div>
                <div className="text-[11px] font-semibold text-gray-600">
                  Severity:{" "}
                  <span className={item.severity === "error" ? "text-red-700 font-bold" : item.severity === "warn" ? "text-yellow-700 font-bold" : "text-gray-700 font-bold"}>
                    {item.severity.toUpperCase()}
                  </span>
                </div>
              </div>
            ));
          }}
          onMouseMove={(e) => {
            tooltip.show(e.clientX, e.clientY, (
              <div className="space-y-1.5">
                <div className="text-sm font-black text-gray-900">{item.title}</div>
                <div className="text-xs text-gray-700">{item.detail}</div>
                <div className="text-[11px] font-semibold text-gray-600">Date: {item.timeLabel}</div>
                <div className="text-[11px] font-semibold text-gray-600">
                  Severity:{" "}
                  <span className={item.severity === "error" ? "text-red-700 font-bold" : item.severity === "warn" ? "text-yellow-700 font-bold" : "text-gray-700 font-bold"}>
                    {item.severity.toUpperCase()}
                  </span>
                </div>
              </div>
            ));
          }}
          onMouseLeave={() => {
            setActiveId("");
            tooltip.hide();
          }}
          onPointerDown={(e) => {
            if (e.pointerType !== "touch") return;
            setActiveId(item.id);
            tooltip.show(e.clientX, e.clientY, (
              <div className="space-y-1.5">
                <div className="text-sm font-black text-gray-900">{item.title}</div>
                <div className="text-xs text-gray-700">{item.detail}</div>
                <div className="text-[11px] font-semibold text-gray-600">Date: {item.timeLabel}</div>
                <div className="text-[11px] font-semibold text-gray-600">
                  Severity:{" "}
                  <span className={item.severity === "error" ? "text-red-700 font-bold" : item.severity === "warn" ? "text-yellow-700 font-bold" : "text-gray-700 font-bold"}>
                    {item.severity.toUpperCase()}
                  </span>
                </div>
              </div>
            ), { pinned: true, autoHideMs: 4000 });
          }}
          tabIndex={0}
          role="group"
          aria-label={`${item.title}: ${item.detail}`}
          onKeyDown={(e) => {
            if (e.key !== "Enter" && e.key !== " ") return;
            const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
            tooltip.show(rect.left + rect.width / 2, rect.top + rect.height / 2, (
              <div className="space-y-1.5">
                <div className="text-sm font-black text-gray-900">{item.title}</div>
                <div className="text-xs text-gray-700">{item.detail}</div>
                <div className="text-[11px] font-semibold text-gray-600">Date: {item.timeLabel}</div>
                <div className="text-[11px] font-semibold text-gray-600">
                  Severity:{" "}
                  <span className={item.severity === "error" ? "text-red-700 font-bold" : item.severity === "warn" ? "text-yellow-700 font-bold" : "text-gray-700 font-bold"}>
                    {item.severity.toUpperCase()}
                  </span>
                </div>
              </div>
            ), { pinned: true, autoHideMs: 5000 });
          }}
          onFocus={(e) => {
            const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
            setActiveId(item.id);
            tooltip.show(rect.left + rect.width / 2, rect.top + rect.height / 2, (
              <div className="space-y-1.5">
                <div className="text-sm font-black text-gray-900">{item.title}</div>
                <div className="text-xs text-gray-700">{item.detail}</div>
                <div className="text-[11px] font-semibold text-gray-600">Date: {item.timeLabel}</div>
                <div className="text-[11px] font-semibold text-gray-600">
                  Severity:{" "}
                  <span className={item.severity === "error" ? "text-red-700 font-bold" : item.severity === "warn" ? "text-yellow-700 font-bold" : "text-gray-700 font-bold"}>
                    {item.severity.toUpperCase()}
                  </span>
                </div>
              </div>
            ));
          }}
          onBlur={() => {
            setActiveId("");
            tooltip.hide();
          }}
        >
          <div className="absolute left-0 top-2 bottom-2 w-[2px] origin-left bg-gray-200 transition-transform duration-300 ease-in-out group-hover:scale-x-[1.2] dark:bg-border dark:group-hover:scale-x-[1.2]" />
          <div className="flex items-start gap-3">
            <div
              className={
                item.severity === "error"
                  ? `w-2.5 h-2.5 mt-1 rounded-full bg-red-500 transition-transform duration-300 ease-in-out ${activeId === item.id ? "scale-110 shadow-sm" : ""}`
                  : item.severity === "warn"
                    ? `w-2.5 h-2.5 mt-1 rounded-full bg-yellow-500 transition-transform duration-300 ease-in-out ${activeId === item.id ? "scale-110 shadow-sm" : ""}`
                    : `w-2.5 h-2.5 mt-1 rounded-full bg-gray-400 transition-transform duration-300 ease-in-out ${activeId === item.id ? "scale-110 shadow-sm" : ""}`
              }
            />
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-bold text-gray-900 dark:text-foreground truncate">{item.title}</p>
                <span className="text-[11px] font-semibold text-gray-500 dark:text-muted-foreground shrink-0">{item.timeLabel}</span>
              </div>
              <p className="text-xs text-gray-600 dark:text-muted-foreground mt-1">{item.detail}</p>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

export type LiveTimeBucket = {
  startMs: number;
  endMs: number;
  total: number | null;
  answered: number | null;
  failed: number | null;
  avgDurationSec: number | null;
  forecastTotal?: number | null;
};

export type LiveChartMarker = {
  ms: number;
  label: string;
  kind: "campaign-start" | "campaign-end" | "note" | "event";
};

export type LiveAnomaly = {
  ms: number;
  kind: "spike" | "drop";
};

export type LiveWindow = {
  startMs: number;
  endMs: number;
  label?: string;
};

function formatHhMm(ms: number) {
  const d = new Date(ms);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatMmSs(seconds: number) {
  const s = Math.max(0, Math.round(seconds));
  const mm = Math.floor(s / 60);
  const ss = s % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

function buildSegmentedPath(points: Array<{ x: number; y: number; valid: boolean }>, smoothing = 0.16) {
  const segments: Array<Array<{ x: number; y: number }>> = [];
  let current: Array<{ x: number; y: number }> = [];
  for (const p of points) {
    if (!p.valid) {
      if (current.length > 0) segments.push(current);
      current = [];
      continue;
    }
    current.push({ x: p.x, y: p.y });
  }
  if (current.length > 0) segments.push(current);
  return segments.map((seg) => toPath(seg, smoothing)).filter(Boolean);
}

export function LiveCallsTimeSeriesChart({
  buckets,
  height = 320,
  markers = [],
  anomalies = [],
  maintenanceWindows = [],
  peakBands = [],
  noteMode = false,
  onAddNoteAtMs,
  onActiveBucketChange,
}: {
  buckets: LiveTimeBucket[];
  height?: number;
  markers?: LiveChartMarker[];
  anomalies?: LiveAnomaly[];
  maintenanceWindows?: LiveWindow[];
  peakBands?: LiveWindow[];
  noteMode?: boolean;
  onAddNoteAtMs?: (ms: number) => void;
  onActiveBucketChange?: (bucket: LiveTimeBucket | null) => void;
}) {
  const tooltip = useHoverTooltip();
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [activeIdx, setActiveIdx] = useState<number | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState(1);
  const panDragRef = useRef<{ active: boolean; startX: number; startPan: number } | null>(null);
  const pinchRef = useRef<{
    active: boolean;
    a: { id: number; x: number; y: number } | null;
    b: { id: number; x: number; y: number } | null;
    startZoom: number;
    startDist: number;
  } | null>(null);

  const chart = useMemo(() => {
    const w = 980;
    const h = height;
    const left = 70;
    const right = 24;
    const top = 26;
    const bottom = 56;
    const plotW = w - left - right;
    const plotH = h - top - bottom;
    return { w, h, left, right, top, bottom, plotW, plotH, baseY: top + plotH };
  }, [height]);

  const visible = useMemo(() => {
    const n = buckets.length;
    if (n === 0) return { start: 0, end: 0, items: [] as LiveTimeBucket[] };
    const clampedZoom = clamp(zoom, 1, 2);
    const visibleCount = clamp(Math.round(n / clampedZoom), 2, n);
    const maxStart = Math.max(0, n - visibleCount);
    const start = clamp(Math.round(pan * maxStart), 0, maxStart);
    const end = Math.min(n - 1, start + visibleCount - 1);
    const slice = buckets.slice(start, end + 1);
    const maxPoints = 720;
    const step = Math.max(1, Math.ceil(slice.length / maxPoints));
    const downsampled = step === 1 ? slice : slice.filter((_, i) => i % step === 0 || i === slice.length - 1);
    return { start, end, items: downsampled };
  }, [buckets, pan, zoom]);

  const scale = useMemo(() => {
    const values = visible.items.map((b) => (typeof b.total === "number" ? Math.max(0, b.total) : 0));
    const maxVal = Math.max(1, ...values);
    const magnitude = Math.pow(10, Math.floor(Math.log10(maxVal)));
    const niceSteps = [1, 2, 2.5, 5, 10];
    const stepBase = niceSteps.find((s) => maxVal / magnitude <= s) ?? 10;
    const niceMax = Math.ceil(maxVal / (stepBase * magnitude)) * stepBase * magnitude;
    const ticksCount = 5;
    const ticks: number[] = [];
    for (let i = 0; i < ticksCount; i++) ticks.push(Math.round((i * niceMax) / (ticksCount - 1)));
    const xFor = (i: number) => {
      if (visible.items.length <= 1) return chart.left + chart.plotW / 2;
      return chart.left + (i * chart.plotW) / (visible.items.length - 1);
    };
    const yFor = (v: number) => chart.top + chart.plotH - (clamp(v, 0, niceMax) / niceMax) * chart.plotH;
    return { niceMax, ticks, xFor, yFor };
  }, [chart.left, chart.plotH, chart.plotW, chart.top, visible.items]);

  const linePoints = useMemo(() => {
    return visible.items.map((b, i) => {
      const x = scale.xFor(i);
      const valid = typeof b.total === "number" && Number.isFinite(b.total);
      const y = scale.yFor(valid ? b.total ?? 0 : 0);
      return { x, y, valid };
    });
  }, [scale, visible.items]);

  const forecastPoints = useMemo(() => {
    return visible.items.map((b, i) => {
      const x = scale.xFor(i);
      const valid = typeof b.forecastTotal === "number" && Number.isFinite(b.forecastTotal);
      const y = scale.yFor(valid ? b.forecastTotal ?? 0 : 0);
      return { x, y, valid };
    });
  }, [scale, visible.items]);

  const lineSegments = useMemo(() => buildSegmentedPath(linePoints, 0.16), [linePoints]);
  const forecastSegments = useMemo(() => buildSegmentedPath(forecastPoints, 0.16), [forecastPoints]);

  const areaSegments = useMemo(() => {
    return lineSegments.map((d) => {
      if (!d) return "";
      let first: { x: number; y: number; valid: boolean } | null = null;
      let last: { x: number; y: number; valid: boolean } | null = null;
      for (const p of linePoints) {
        if (!p.valid) continue;
        if (!first) first = p;
        last = p;
      }
      if (!first || !last) return "";
      return `${d} L ${last.x} ${chart.baseY} L ${first.x} ${chart.baseY} Z`;
    }).filter(Boolean);
  }, [chart.baseY, linePoints, lineSegments]);

  const xTicks = useMemo(() => {
    const n = visible.items.length;
    if (n === 0) return [];
    const indices = Array.from(new Set([0, Math.floor((n - 1) / 3), Math.floor(((n - 1) * 2) / 3), n - 1]));
    const fallbackMs = visible.items[0]?.startMs ?? 0;
    return indices.map((i) => ({ i, x: scale.xFor(i), label: formatHhMm(visible.items[i]?.startMs ?? fallbackMs) }));
  }, [scale, visible.items]);

  const markerInView = useMemo(() => {
    if (visible.items.length === 0) return { startMs: 0, endMs: 0, inView: [] as LiveChartMarker[] };
    const startMs = visible.items[0]?.startMs ?? 0;
    const endMs = visible.items[visible.items.length - 1]?.endMs ?? 0;
    return { startMs, endMs, inView: markers.filter((m) => m.ms >= startMs && m.ms <= endMs) };
  }, [markers, visible.items]);

  const windowsInView = useMemo(() => {
    const startMs = markerInView.startMs;
    const endMs = markerInView.endMs;
    return {
      maintenance: maintenanceWindows.filter((w) => w.endMs >= startMs && w.startMs <= endMs),
      peaks: peakBands.filter((w) => w.endMs >= startMs && w.startMs <= endMs),
    };
  }, [maintenanceWindows, markerInView.endMs, markerInView.startMs, peakBands]);

  const anomaliesInView = useMemo(() => {
    const startMs = markerInView.startMs;
    const endMs = markerInView.endMs;
    return anomalies.filter((a) => a.ms >= startMs && a.ms <= endMs);
  }, [anomalies, markerInView.endMs, markerInView.startMs]);

  const xForMs = (ms: number) => {
    if (markerInView.endMs <= markerInView.startMs) return chart.left;
    const t = clamp((ms - markerInView.startMs) / (markerInView.endMs - markerInView.startMs), 0, 1);
    return chart.left + t * chart.plotW;
  };

  const showTooltipForIndex = (idx: number, clientX: number, clientY: number) => {
    const b = visible.items[idx];
    if (!b) return;
    const total = typeof b.total === "number" ? b.total : null;
    const answered = typeof b.answered === "number" ? b.answered : null;
    const failed = typeof b.failed === "number" ? b.failed : null;
    const avgDur = typeof b.avgDurationSec === "number" ? b.avgDurationSec : null;
    const answeredPct = total && answered !== null ? (answered / Math.max(1, total)) * 100 : null;
    const failedPct = total && failed !== null ? (failed / Math.max(1, total)) * 100 : null;

    const content = (
      <div className="space-y-2">
        <div className="text-sm font-black text-gray-900">
          {formatHhMm(b.startMs)}–{formatHhMm(b.endMs)}
        </div>
        {total === null ? (
          <div className="text-xs font-semibold text-gray-600">Data gap</div>
        ) : (
          <div className="space-y-1.5">
            <div className="flex items-center justify-between gap-8 text-sm">
              <span className="text-gray-700 font-semibold">Total call volume</span>
              <span className="tabular-nums font-black text-gray-900">{total.toLocaleString()}</span>
            </div>
            <div className="flex items-center justify-between gap-8 text-sm">
              <span className="text-gray-700 font-semibold">Answered</span>
              <span className="tabular-nums font-black text-gray-900">
                {answeredPct === null ? "—" : `${answeredPct.toFixed(1)}%`}
              </span>
            </div>
            <div className="flex items-center justify-between gap-8 text-sm">
              <span className="text-gray-700 font-semibold">Failed</span>
              <span className="tabular-nums font-black text-gray-900">
                {failedPct === null ? "—" : `${failedPct.toFixed(1)}%`}
              </span>
            </div>
            <div className="flex items-center justify-between gap-8 text-sm">
              <span className="text-gray-700 font-semibold">Average duration</span>
              <span className="tabular-nums font-black text-gray-900">{avgDur === null ? "—" : formatMmSs(avgDur)}</span>
            </div>
          </div>
        )}
      </div>
    );
    tooltip.show(clientX, clientY, content);
  };

  const handlePointerMove = (clientX: number, clientY: number) => {
    const svg = svgRef.current;
    if (!svg || visible.items.length === 0) return;
    const rect = svg.getBoundingClientRect();
    const x = clientX - rect.left;
    const clamped = clamp(x, chart.left, chart.left + chart.plotW);
    const t = (clamped - chart.left) / Math.max(1, chart.plotW);
    const idx = clamp(Math.round(t * Math.max(0, visible.items.length - 1)), 0, Math.max(0, visible.items.length - 1));
    setActiveIdx(idx);
    onActiveBucketChange?.(visible.items[idx] ?? null);
    showTooltipForIndex(idx, clientX, clientY);
  };

  const resetView = () => {
    setZoom(1);
    setPan(1);
    setActiveIdx(null);
    tooltip.hide();
  };

  return (
    <div className="relative">
      <HoverTooltip state={tooltip.state} />
      <svg
        ref={svgRef}
        viewBox={`0 0 ${chart.w} ${chart.h}`}
        preserveAspectRatio="xMidYMid meet"
        className="w-full select-none text-foreground"
        style={{ height, touchAction: "none" }}
        role="img"
        aria-label="Live calls time-series chart"
        onMouseMove={(e) => handlePointerMove(e.clientX, e.clientY)}
        onMouseLeave={() => {
          setActiveIdx(null);
          onActiveBucketChange?.(null);
          tooltip.hide();
        }}
        onWheel={(e) => {
          if (visible.items.length === 0) return;
          const svg = svgRef.current;
          if (!svg) return;
          const rect = svg.getBoundingClientRect();
          const x = e.clientX - rect.left;
          const t = clamp((x - chart.left) / Math.max(1, chart.plotW), 0, 1);
          const nextZoom = clamp(zoom + (e.deltaY > 0 ? -0.12 : 0.12), 1, 2);
          const n = buckets.length;
          const prevCount = clamp(Math.round(n / zoom), 2, n);
          const nextCount = clamp(Math.round(n / nextZoom), 2, n);
          const prevStart = clamp(Math.round(pan * Math.max(0, n - prevCount)), 0, Math.max(0, n - prevCount));
          const anchor = prevStart + t * (prevCount - 1);
          const nextStart = clamp(Math.round(anchor - t * (nextCount - 1)), 0, Math.max(0, n - nextCount));
          const maxStart = Math.max(0, n - nextCount);
          setZoom(nextZoom);
          setPan(maxStart === 0 ? 0 : nextStart / maxStart);
        }}
        onDoubleClick={() => resetView()}
        onPointerDown={(e) => {
          if (noteMode) {
            const svg = svgRef.current;
            if (!svg || visible.items.length === 0) return;
            const rect = svg.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const clamped = clamp(x, chart.left, chart.left + chart.plotW);
            const t = (clamped - chart.left) / Math.max(1, chart.plotW);
            const idx = clamp(Math.round(t * Math.max(0, visible.items.length - 1)), 0, Math.max(0, visible.items.length - 1));
            const b = visible.items[idx];
            if (b) onAddNoteAtMs?.(b.startMs);
            return;
          }
          if (e.pointerType === "mouse" || e.pointerType === "pen") {
            panDragRef.current = { active: true, startX: e.clientX, startPan: pan };
          }
          if (e.pointerType === "touch") {
            const pr = pinchRef.current ?? { active: false, a: null, b: null, startZoom: zoom, startDist: 0 };
            const next = { ...pr };
            if (!next.a) next.a = { id: e.pointerId, x: e.clientX, y: e.clientY };
            else if (!next.b && next.a.id !== e.pointerId) next.b = { id: e.pointerId, x: e.clientX, y: e.clientY };
            if (next.a && next.b) {
              next.active = true;
              next.startZoom = zoom;
              next.startDist = Math.hypot(next.a.x - next.b.x, next.a.y - next.b.y);
            }
            pinchRef.current = next;
          }
        }}
        onPointerMove={(e) => {
          const panDrag = panDragRef.current;
          if (panDrag?.active) {
            const dx = e.clientX - panDrag.startX;
            const n = buckets.length;
            const visibleCount = clamp(Math.round(n / zoom), 2, Math.max(2, n));
            const maxStart = Math.max(0, n - visibleCount);
            if (maxStart <= 0) return;
            const delta = -dx / Math.max(1, chart.plotW);
            const nextPan = clamp(panDrag.startPan + delta, 0, 1);
            setPan(nextPan);
            return;
          }

          const pr = pinchRef.current;
          if (!pr?.active) return;
          const next = { ...pr };
          if (next.a?.id === e.pointerId) next.a = { ...next.a, x: e.clientX, y: e.clientY };
          if (next.b?.id === e.pointerId) next.b = { ...next.b, x: e.clientX, y: e.clientY };
          if (!next.a || !next.b) return;
          const dist = Math.hypot(next.a.x - next.b.x, next.a.y - next.b.y);
          const ratio = next.startDist > 0 ? dist / next.startDist : 1;
          setZoom(clamp(next.startZoom * ratio, 1, 2));
          pinchRef.current = next;
        }}
        onPointerUp={(e) => {
          const panDrag = panDragRef.current;
          if (panDrag?.active) panDragRef.current = null;
          const pr = pinchRef.current;
          if (pr?.a?.id === e.pointerId) pinchRef.current = { ...pr, a: null, active: false };
          else if (pr?.b?.id === e.pointerId) pinchRef.current = { ...pr, b: null, active: false };
        }}
      >
        <defs>
          <linearGradient id="liveLineGradient" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#3498db" />
            <stop offset="100%" stopColor="#1abc9c" />
          </linearGradient>
          <linearGradient id="liveAreaGradient" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#3498db" stopOpacity={0.1} />
            <stop offset="100%" stopColor="#1abc9c" stopOpacity={0.1} />
          </linearGradient>
        </defs>

        <rect x={chart.left} y={chart.top} width={chart.plotW} height={chart.plotH} fill="rgba(0,0,0,0.02)" rx={14} />

        {windowsInView.peaks.map((w, i) => {
          const x1 = xForMs(w.startMs);
          const x2 = xForMs(w.endMs);
          return <rect key={`peak-${i}`} x={x1} y={chart.top} width={Math.max(0, x2 - x1)} height={chart.plotH} fill="rgba(234,179,8,0.18)" />;
        })}

        {windowsInView.maintenance.map((w, i) => {
          const x1 = xForMs(w.startMs);
          const x2 = xForMs(w.endMs);
          return <rect key={`mw-${i}`} x={x1} y={chart.top} width={Math.max(0, x2 - x1)} height={chart.plotH} fill="rgba(107,114,128,0.18)" />;
        })}

        {xTicks.map((t) => (
          <line
            key={`xg-${t.i}`}
            x1={t.x}
            x2={t.x}
            y1={chart.top}
            y2={chart.baseY}
            stroke="currentColor"
            strokeOpacity={0.3}
            strokeWidth={0.5}
            strokeDasharray="4 5"
          />
        ))}

        {scale.ticks.map((t) => {
          const y = scale.yFor(t);
          return (
            <line
              key={`yg-${t}`}
              x1={chart.left}
              x2={chart.left + chart.plotW}
              y1={y}
              y2={y}
              stroke="currentColor"
              strokeOpacity={0.5}
              strokeWidth={1}
            />
          );
        })}

        {areaSegments.map((d, i) => (
          <path key={`area-${i}`} d={d} fill="url(#liveAreaGradient)" />
        ))}

        {lineSegments.map((d, i) => (
          <path key={`line-${i}`} d={d} fill="none" stroke="url(#liveLineGradient)" strokeWidth={2.75} strokeLinejoin="round" strokeLinecap="round" />
        ))}

        {forecastSegments.map((d, i) => (
          <path
            key={`forecast-${i}`}
            d={d}
            fill="none"
            stroke="currentColor"
            strokeOpacity={0.5}
            strokeWidth={2}
            strokeDasharray="6 6"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}

        {markerInView.inView.map((m, i) => {
          const x = xForMs(m.ms);
          const color =
            m.kind === "campaign-start" ? "rgba(52,152,219,0.7)" : m.kind === "campaign-end" ? "rgba(26,188,156,0.7)" : m.kind === "note" ? "rgba(147,51,234,0.75)" : "rgba(107,114,128,0.75)";
          return (
            <g key={`${m.kind}-${m.ms}-${i}`}>
              <line x1={x} x2={x} y1={chart.top} y2={chart.baseY} stroke={color} strokeWidth={1} />
              <text x={x + 6} y={chart.top + 14} style={{ fontWeight: 800, fontSize: 11, fill: color }}>
                {m.label}
              </text>
            </g>
          );
        })}

        {anomaliesInView.map((a, i) => {
          const x = xForMs(a.ms);
          const idx = visible.items.findIndex((b) => a.ms >= b.startMs && a.ms <= b.endMs);
          const b = idx >= 0 ? visible.items[idx] : null;
          const y = b && typeof b.total === "number" ? scale.yFor(b.total) : chart.top + chart.plotH * 0.5;
          const size = 7;
          const fill = a.kind === "spike" ? "rgba(239,68,68,0.95)" : "rgba(249,115,22,0.95)";
          const points = a.kind === "spike"
            ? `${x},${y - size} ${x - size},${y + size} ${x + size},${y + size}`
            : `${x},${y + size} ${x - size},${y - size} ${x + size},${y - size}`;
          return <polygon key={`anom-${i}`} points={points} fill={fill} />;
        })}

        {activeIdx !== null ? (
          (() => {
            const p = linePoints[activeIdx];
            if (!p?.valid) return null;
            return (
              <circle
                cx={p.x}
                cy={p.y}
                r={2}
                fill="#ffffff"
                stroke="url(#liveLineGradient)"
                strokeWidth={2}
              />
            );
          })()
        ) : null}

        {scale.ticks.map((t) => (
          <text
            key={`yl-${t}`}
            x={chart.left - 10}
            y={scale.yFor(t) + 4}
            textAnchor="end"
            style={{ fontWeight: 700, fontSize: 12, fill: "currentColor", opacity: 0.75 }}
          >
            {t.toLocaleString()}
          </text>
        ))}

        {xTicks.map((t) => (
          <text
            key={`xl-${t.i}`}
            x={t.x}
            y={chart.baseY + 26}
            textAnchor="middle"
            style={{ fontWeight: 700, fontSize: 12, fill: "currentColor", opacity: 0.75 }}
          >
            {t.label}
          </text>
        ))}

        <text
          x={chart.left + chart.plotW / 2}
          y={chart.h - 12}
          textAnchor="middle"
          style={{ fontWeight: 700, fontSize: 12, fill: "currentColor", opacity: 0.85 }}
        >
          Time
        </text>

        <text
          x={14}
          y={chart.top + chart.plotH / 2}
          transform={`rotate(-90 14 ${chart.top + chart.plotH / 2})`}
          textAnchor="middle"
          style={{ fontWeight: 700, fontSize: 12, fill: "currentColor", opacity: 0.85 }}
        >
          Number of Calls
        </text>
      </svg>
    </div>
  );
}
