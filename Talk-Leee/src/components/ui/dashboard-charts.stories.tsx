import type { Meta, StoryObj } from "@storybook/react";
import {
    ActivityFeed,
    AlertTimeline,
    DonutChart,
    Heatmap,
    LiveCallsTimeSeriesChart,
    RealTimeBarChart,
    RealTimeCallLineChart,
    StackedAreaChart,
    TimeSeriesLineChart,
    type DualSeriesPoint,
    type FeedItem,
    type LiveAnomaly,
    type LiveChartMarker,
    type LiveTimeBucket,
    type LiveWindow,
    type TimeSeriesPoint,
    type TimelineItem,
} from "@/components/ui/dashboard-charts";

const meta: Meta<typeof TimeSeriesLineChart> = {
    title: "UI/DashboardCharts",
    component: TimeSeriesLineChart,
};

export default meta;
type Story = StoryObj<typeof TimeSeriesLineChart>;

const timeSeries: TimeSeriesPoint[] = [
    { label: "Mon", value: 120 },
    { label: "Tue", value: 180 },
    { label: "Wed", value: 240 },
    { label: "Thu", value: 210 },
    { label: "Fri", value: 320 },
    { label: "Sat", value: 280 },
    { label: "Sun", value: 360 },
];

const dualSeries: DualSeriesPoint[] = [
    { label: "09:00", a: 220, b: 64 },
    { label: "10:00", a: 240, b: 58 },
    { label: "11:00", a: 260, b: 72 },
];

const feed: FeedItem[] = [
    { id: "f1", title: "Campaign started", detail: "Holiday Sales Outreach is now running.", timeLabel: "Now", tone: "good" },
    { id: "f2", title: "Queue spike", detail: "Inbound queue peaked at 42 calls.", timeLabel: "2m ago", tone: "warn" },
    { id: "f3", title: "Connector synced", detail: "Google Calendar sync completed.", timeLabel: "11m ago", tone: "neutral" },
];

const timeline: TimelineItem[] = [
    { id: "t1", title: "High failure rate", detail: "18 failed calls out of 96 (18.8%).", timeLabel: "Jan 13", severity: "warn" },
    { id: "t2", title: "Outage detected", detail: "Provider returned 503 for 3 minutes.", timeLabel: "Jan 12", severity: "error" },
    { id: "t3", title: "Recovered", detail: "Delivery stabilized after retry backoff.", timeLabel: "Jan 12", severity: "info" },
];

function nowMs() {
    return Date.now();
}

const buckets: LiveTimeBucket[] = Array.from({ length: 120 }).map((_, i) => {
    const startMs = nowMs() - (120 - i) * 60_000;
    const total = Math.max(0, Math.round(120 + Math.sin(i / 6) * 40 + (Math.random() - 0.5) * 18));
    const failed = Math.max(0, Math.round(total * (0.06 + Math.random() * 0.04)));
    const answered = Math.max(0, total - failed);
    return {
        startMs,
        endMs: startMs + 60_000,
        total,
        answered,
        failed,
        avgDurationSec: 95 + Math.sin(i / 10) * 18,
        forecastTotal: total + Math.round((Math.random() - 0.5) * 20),
    };
});

const markers: LiveChartMarker[] = [
    { ms: buckets[20]?.startMs ?? nowMs(), label: "Campaign start", kind: "campaign-start" },
    { ms: buckets[70]?.startMs ?? nowMs(), label: "Note", kind: "note" },
];

const anomalies: LiveAnomaly[] = [
    { ms: buckets[42]?.startMs ?? nowMs(), kind: "spike" },
    { ms: buckets[95]?.startMs ?? nowMs(), kind: "drop" },
];

const maintenanceWindows: LiveWindow[] = [
    { startMs: buckets[50]?.startMs ?? nowMs(), endMs: buckets[60]?.endMs ?? nowMs(), label: "Maintenance" },
];

const peakBands: LiveWindow[] = [
    { startMs: buckets[30]?.startMs ?? nowMs(), endMs: buckets[40]?.endMs ?? nowMs(), label: "Peak" },
];

export const TimeSeries: Story = {
    render: () => (
        <div className="p-6 bg-gray-50">
            <TimeSeriesLineChart points={timeSeries} />
        </div>
    ),
};

export const RealTimeBars: Story = {
    render: () => (
        <div className="p-6 bg-gray-50">
            <RealTimeBarChart points={dualSeries} maxValue={360} />
        </div>
    ),
};

export const RealTimeCallLine: Story = {
    render: () => (
        <div className="p-6 bg-gray-50">
            <RealTimeCallLineChart points={dualSeries} maxValue={360} />
        </div>
    ),
};

export const Donut: Story = {
    render: () => (
        <div className="p-6 bg-gray-50 max-w-sm">
            <DonutChart value={820} total={1000} />
        </div>
    ),
};

export const HeatmapDemo: Story = {
    render: () => (
        <div className="p-6 bg-gray-50">
            <Heatmap
                rows={7}
                cols={24}
                cellSize={14}
                values={Array.from({ length: 7 * 24 }).map((_, i) => Math.max(0, Math.sin(i / 6) * 0.55 + (Math.random() - 0.5) * 0.35))}
            />
        </div>
    ),
};

export const StackedArea: Story = {
    render: () => (
        <div className="p-6 bg-gray-50">
            <StackedAreaChart
                points={timeSeries.map((p, i) => ({ label: p.label, a: Math.round(p.value * 0.86), b: Math.round(p.value * 0.14 + (i % 2) * 6) }))}
            />
        </div>
    ),
};

export const Activity: Story = {
    render: () => (
        <div className="p-6 bg-gray-50 max-w-xl">
            <ActivityFeed items={feed} />
        </div>
    ),
};

export const Alerts: Story = {
    render: () => (
        <div className="p-6 bg-gray-50 max-w-xl">
            <AlertTimeline items={timeline} />
        </div>
    ),
};

export const LiveCalls: Story = {
    render: () => (
        <div className="p-6 bg-gray-50">
            <LiveCallsTimeSeriesChart
                buckets={buckets}
                markers={markers}
                anomalies={anomalies}
                maintenanceWindows={maintenanceWindows}
                peakBands={peakBands}
            />
        </div>
    ),
};

