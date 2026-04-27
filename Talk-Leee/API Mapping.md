# Frontend Dashboard Data Contract v2.1

**Purpose:** Complete frontend-backend alignment document for dashboard  
**Status:** ✅ Ready for backend implementation  
**Last Updated:** 2026-04-13  
**Audience:** Backend API developers & Frontend team

---

## Table of Contents

1. [Data Requirements Overview](#data-requirements-overview)
2. [Initial Data Load](#initial-data-load)
3. [Dashboard Summary Endpoint](#dashboard-summary-endpoint)
4. [KPI Cards Data Flow](#kpi-cards-data-flow)
5. [Live Chart Data Flow](#live-chart-data-flow)
6. [Donut Chart Data Flow](#donut-chart-data-flow)
7. [Minutes Meter Data Flow](#minutes-meter-data-flow)
8. [Stacked Area Chart Data Flow](#stacked-area-chart-data-flow)
9. [Campaign Data Integration](#campaign-data-integration)
10. [Analytics Data Integration](#analytics-data-integration)
11. [Data Transformations & Calculations](#data-transformations--calculations)
12. [Response Formats & Schemas](#response-formats--schemas)
13. [Error Handling](#error-handling)

---

## Data Requirements Overview

The dashboard requires **3 main API calls** on page load:

```javascript
// Load sequence
Promise.all([
  dashboardApi.getDashboardSummary(),      // DashboardSummary
  dashboardApi.listCampaigns(),            // Campaign[]
  extendedApi.getCallAnalytics()           // CallSeriesItem[]
])
```

**Then real-time updates:**
- DashboardSummary updates every 1 second (increment pattern)
- Live chart data from simulated buckets (1-minute intervals)

---

## Initial Data Load

### On Page Mount

```typescript
// Frontend calls:
const [summaryData, campaignsData, analytics] = await Promise.all([
  dashboardApi.getDashboardSummary(),
  dashboardApi.listCampaigns(),
  extendedApi.getCallAnalytics(),
]);

// Frontend stores:
setSummary(summaryData);              // Initial summary
setLiveSummary(summaryData);          // Copy for real-time updates
setCampaigns(campaignsData.campaigns); // Campaign list
setSeries(analytics.series);           // Historical analytics
setLoading(false);
```

---

## Dashboard Summary Endpoint

### Request

```
GET /dashboard/summary
```

### Response Format

```typescript
interface DashboardSummary {
  total_calls: number;           // Total calls in current period
  answered_calls: number;        // Successfully answered calls
  failed_calls: number;          // Failed/unanswered calls
  minutes_used: number;          // Minutes consumed from quota
  minutes_remaining: number;     // Minutes left in quota
  active_campaigns: number;      // Currently running campaigns
}
```

### Response Example

```json
{
  "total_calls": 1247,
  "answered_calls": 1089,
  "failed_calls": 158,
  "minutes_used": 3420,
  "minutes_remaining": 1580,
  "active_campaigns": 3
}
```

### Validation Rules

```typescript
// Frontend validates:
- total_calls: number, >= 0
- answered_calls: number, >= 0, <= total_calls
- failed_calls: number, >= 0, <= total_calls
- minutes_used: number, >= 0
- minutes_remaining: number, >= 0
- active_campaigns: number, >= 0
```

### Update Pattern

After initial load, frontend updates every 1 second with increments:
```typescript
setLiveSummary((prev) => {
  const inc = Math.floor(Math.random() * 6);          // 0-5 new calls
  const answeredInc = Math.floor(Math.random() * (inc + 1));
  const failedInc = inc - answeredInc;
  const minutesInc = Math.floor(inc * (2 + Math.random() * 2));
  
  return {
    ...prev,
    total_calls: prev.total_calls + inc,
    answered_calls: prev.answered_calls + answeredInc,
    failed_calls: prev.failed_calls + failedInc,
    minutes_used: prev.minutes_used + minutesInc,
    minutes_remaining: Math.max(0, prev.minutes_remaining - minutesInc),
  };
});
```

---

## KPI Cards Data Flow

### Input Data

**Source:** `DashboardSummary`

### Frontend Calculations

The frontend calculates **4 KPI Cards** from simulated live buckets + dashboard summary:

#### 1. Total Calls Card

```typescript
// Current period
currentTotal = sum(liveBuckets, "total")

// Previous period (same time range, one period back)
prevTotal = sum(previousBucketsBase, "total")

// Delta calculation
const totalDelta = {
  abs: currentTotal - prevTotal,
  pct: prevTotal === 0 ? (currentTotal === 0 ? 0 : 100) : 
       (abs / prevTotal) * 100
}

// Status determination
status = currentTotal >= 500 ? "green" : 
         currentTotal >= 250 ? "yellow" : "red"

// Display
KpiCard {
  title: "Total Calls",
  value: currentTotal,
  valueSuffix: "",
  deltaAbs: totalDelta.abs,
  deltaPct: totalDelta.pct,
  status: status
}
```

---

#### 2. Success Rate Card

```typescript
// Calculate success rate
currentAnswered = sum(liveBuckets, "answered")
currentSuccessRate = currentTotal > 0 ? 
                     (currentAnswered / currentTotal) * 100 : 0

// Previous period
prevAnswered = sum(previousBucketsBase, "answered")
prevSuccessRate = prevTotal > 0 ? 
                  (prevAnswered / prevTotal) * 100 : 0

// Delta
successDelta = {
  abs: currentSuccessRate - prevSuccessRate,
  pct: prevSuccessRate === 0 ? ... : (abs / prevSuccessRate) * 100
}

// Status
status = currentSuccessRate >= 92 ? "green" : 
         currentSuccessRate >= 85 ? "yellow" : "red"

// Display
KpiCard {
  title: "Success Rate",
  value: Math.round(currentSuccessRate * 10) / 10,  // 1 decimal
  valueSuffix: "%",
  deltaAbs: successDelta.abs,
  deltaPct: successDelta.pct,
  status: status
}
```

---

#### 3. Active Calls Card

```typescript
// Calculated from latest bucket
latestBucketTotal = liveBuckets[liveBuckets.length - 1]?.total ?? 0
currentActiveCalls = Math.max(0, Math.round(latestBucketTotal * 0.18 + 6))

// Previous period
prevLatestBucketTotal = previousBucketsBase[previousBucketsBase.length - 1]?.total ?? 0
prevActiveCalls = Math.max(0, Math.round(prevLatestBucketTotal * 0.18 + 6))

// Delta
activeDelta = delta(currentActiveCalls, prevActiveCalls)

// Status
status = currentActiveCalls >= 30 ? "green" : 
         currentActiveCalls >= 18 ? "yellow" : "red"

// Display
KpiCard {
  title: "Active Calls",
  value: currentActiveCalls,
  valueSuffix: "",
  deltaAbs: activeDelta.abs,
  deltaPct: activeDelta.pct,
  status: status
}
```

---

#### 4. Avg Duration Card

```typescript
// Weighted average calculation
currentAvgDurationSec = (() => {
  let totalWeight = 0;
  let acc = 0;
  for (const b of liveBuckets) {
    if (typeof b.total === "number" && typeof b.avgDurationSec === "number") {
      totalWeight += b.total;
      acc += b.avgDurationSec * b.total;
    }
  }
  return totalWeight > 0 ? acc / totalWeight : 0;
})()

// Previous period
prevAvgDurationSec = (similar calculation with previousBucketsBase)

// Delta
avgDurDelta = delta(currentAvgDurationSec, prevAvgDurationSec)

// Status (lower is better)
status = currentAvgDurationSec <= 70 ? "green" : 
         currentAvgDurationSec <= 95 ? "yellow" : "red"

// Display
KpiCard {
  title: "Avg Duration",
  value: Math.round(currentAvgDurationSec),
  valueSuffix: "s",
  deltaAbs: avgDurDelta.abs,
  deltaPct: avgDurDelta.pct,
  status: status
}
```

---

### KPI Card Display Properties

| Property | Type | Format | Example |
|----------|------|--------|---------|
| title | string | As-is | "Total Calls" |
| value | number | Formatted with commas (large numbers) | 1,247 |
| valueSuffix | string | Appended to value | "%", "s", "" |
| deltaAbs | number | Absolute change | -25, +100 |
| deltaPct | number | Percentage change | -2.3, +15.8 |
| status | enum | "green" \| "yellow" \| "red" | "green" |
| lastUpdatedMs | number | Timestamp in ms | Date.now() |

---

## Live Chart Data Flow

### Input Data

**Source:** Simulated `LiveTimeBucket[]` - 48 hours of 1-minute buckets

### Data Structure

```typescript
interface LiveTimeBucket {
  startMs: number;           // Bucket start time (epoch)
  endMs: number;             // Bucket end time (epoch)
  total: number | null;      // Total calls in bucket
  answered: number | null;   // Answered calls
  failed: number | null;     // Failed calls
  avgDurationSec: number | null;  // Average duration
  forecastTotal?: number;    // Calculated forecast (frontend only)
}
```

### Frontend Time Range Filtering

```typescript
// User selects range (1h, 4h, 8h, 24h, or custom)
const activeRange = useMemo(() => {
  const end = callRangeMode === "custom" ? 
             (customEndMs ?? sim.lastUpdatedMs) : 
             sim.lastUpdatedMs;
  
  const presetMs = liveRangeHours * 60 * 60 * 1000;
  const start = callRangeMode === "custom" ? 
               (customStartMs ?? end - presetMs) : 
               end - presetMs;
  
  return { startMs: Math.min(start, end), endMs: Math.max(start, end) };
}, [callRangeMode, customEndMs, customStartMs, liveRangeHours, sim.lastUpdatedMs])

// Filter buckets within range
const liveBucketsBase = sim.buckets.filter(
  b => b.endMs >= activeRange.startMs && b.startMs <= activeRange.endMs
)
```

### Frontend Forecast Calculation

```typescript
const liveBuckets = useMemo(() => {
  const totals = liveBucketsBase
    .map(b => b.total)
    .filter(v => typeof v === "number")
  
  // 6-bucket moving average
  const avg6 = (idx: number) => {
    const window = totals.slice(Math.max(0, idx - 5), idx + 1)
    if (window.length === 0) return 0
    return window.reduce((a, v) => a + v, 0) / window.length
  }
  
  return liveBucketsBase.map((b, i) => {
    if (typeof b.total !== "number") return { ...b, forecastTotal: null }
    
    const prev = avg6(Math.max(0, i - 1))
    const cur = avg6(i)
    const slope = cur - prev
    
    return { ...b, forecastTotal: Math.max(0, Math.round(cur + slope * 0.9)) }
  })
}, [liveBucketsBase])
```

### Frontend Markers (Events)

```typescript
// Hardcoded event markers based on time range
const baseMarkers = [
  { ms: now - Math.round(rangeMs * 0.82), label: "Campaign A start", kind: "campaign-start" },
  { ms: now - Math.round(rangeMs * 0.46), label: "Campaign A end", kind: "campaign-end" },
  { ms: now - Math.round(rangeMs * 0.68), label: "Campaign B start", kind: "campaign-start" },
  { ms: now - Math.round(rangeMs * 0.22), label: "Campaign B end", kind: "campaign-end" },
  { ms: now - Math.round(rangeMs * 0.12), label: "Deploy", kind: "event" },
]
// Filtered within range
const markers = baseMarkers.filter(m => m.ms >= windowStart && m.ms <= now)
```

### Frontend Anomaly Detection

```typescript
// Spike & drop detection (last 40 only)
const anomalies = []
const recent = []
for (const b of liveBuckets) {
  if (typeof b.total === "number") {
    recent.push(b.total)
    const window = recent.slice(-10)
    const avg = window.reduce((a, v) => a + v, 0) / Math.max(1, window.length)
    
    if (avg > 0 && b.total > avg * 1.7) {
      anomalies.push({ ms: b.startMs, kind: "spike" })
    }
    if (avg > 0 && b.total < avg * 0.5) {
      anomalies.push({ ms: b.startMs, kind: "drop" })
    }
  }
}
```

### Frontend Peak Bands

```typescript
// Top 2 highest volume buckets
const peaks = liveBuckets
  .map(b => ({ b, v: typeof b.total === "number" ? b.total : -1 }))
  .filter(x => x.v >= 0)
  .sort((a, b) => b.v - a.v)
  .slice(0, 2)
  .map(x => ({ startMs: x.b.startMs, endMs: x.b.endMs, label: "Peak" }))
```

### Frontend Maintenance Windows

```typescript
// Hardcoded maintenance window
const maintenanceWindows = [
  { startMs: now - Math.round(rangeMs * 0.6), 
    endMs: now - Math.round(rangeMs * 0.55), 
    label: "Maintenance" }
].filter(w => w.endMs >= windowStart && w.startMs <= now)
```

### Chart Display Data

**Passed to `<LiveCallsTimeSeriesChart />` component:**

```typescript
<LiveCallsTimeSeriesChart
  buckets={liveBuckets}              // LiveTimeBucket[]
  markers={markers}                  // { ms, label, kind }[]
  anomalies={anomalies}              // { ms, kind }[]
  maintenanceWindows={maintenanceWindows}  // { startMs, endMs, label }[]
  peakBands={peakBands}              // { startMs, endMs, label }[]
  onActiveBucketChange={setActiveBucket}
/>
```

---

## Donut Chart Data Flow

### Input Data

**Source:** `DashboardSummary` (liveSummary)

### Frontend Calculation

```typescript
// Current success rate from summary
const effectiveSummary = liveSummary ?? summary
const successRate = effectiveSummary 
  ? (effectiveSummary.answered_calls / effectiveSummary.total_calls) * 100 
  : 0

// Display
<DonutChart
  value={effectiveSummary?.answered_calls || 0}    // Answered count
  total={effectiveSummary?.total_calls || 0}        // Total count
  size={200}
  strokeWidth={16}
  showSegmentLabels={false}
/>

// Center text
<div>{successRate}%</div>
<div>Success rate</div>

// Bottom stats
<div>
  <span>Answered: {effectiveSummary?.answered_calls}</span>
  <span>Failed: {effectiveSummary?.failed_calls}</span>
</div>
```

---

## Minutes Meter Data Flow

### Input Data

**Source:** `DashboardSummary` (liveSummary)

### Frontend Calculation

```typescript
const effectiveSummary = liveSummary ?? summary

const minutesUsed = effectiveSummary?.minutes_used ?? 0
const minutesRemaining = effectiveSummary?.minutes_remaining ?? 0
const minutesTotal = minutesUsed + minutesRemaining

const minutesUsedPct = minutesTotal > 0 
  ? (minutesUsed / minutesTotal) * 100 
  : 0

// Format for display
const minutesUsedText = `${minutesUsed.toLocaleString()}`
const minutesRemainingText = `${minutesRemaining.toLocaleString()}`
```

### Frontend Dynamic Font Sizing

```typescript
// Calculate font size based on text width
const computeMinutesUsageFontPx = (widthPx: number) => {
  // Responsive font sizing logic
  // Returns { used: number, remaining: number }
}

// Applied via inline style
<span style={{ fontSize: minutesFontPx.used }}>
  {minutesUsedText}
</span>
```

### Display Elements

| Element | Data | Format |
|---------|------|--------|
| Progress bar fill | minutesUsedPct | Percentage 0-100 |
| Left label | "Used" | Static text |
| Left value | minutesUsedText | Formatted number |
| Right label | "Remaining" | Static text |
| Right value | minutesRemainingText | Formatted number |
| Bottom label | minutesTotal.toLocaleString() | "Total = X min" |

---

## Stacked Area Chart Data Flow

### Input Data

**Source:** `CallSeriesItem[]` from `getCallAnalytics()`

### Frontend Calculation

```typescript
// Generate 12 historical data points
const initial = Array.from({ length: 12 }).map((_, i) => {
  const t = new Date(now.getTime() - (11 - i) * 60_000)
  const label = t.toLocaleTimeString([], { 
    hour: "2-digit", 
    minute: "2-digit", 
    second: "2-digit" 
  })
  
  // Base from analytics
  const baseA = Math.max(6, Math.round(
    series.reduce((a, s) => a + s.answered, 0) / series.length / 6
  ))
  const baseB = Math.max(1, Math.round(
    series.reduce((a, s) => a + s.failed, 0) / series.length / 10
  ))
  
  // Variation
  const a = Math.max(0, Math.round(baseA + (Math.random() - 0.4) * baseA))
  const b = Math.max(0, Math.round(baseB + (Math.random() - 0.4) * baseB))
  
  return { label, a, b }
})
```

### Frontend Real-Time Updates

```typescript
// Every 1 second, add new point and keep last 12
setLiveBars((prev) => {
  const now = new Date()
  const label = now.toLocaleTimeString([],{ 
    hour: "2-digit", 
    minute: "2-digit", 
    second: "2-digit" 
  })
  const last = prev[prev.length - 1] ?? { label, a: 0, b: 0 }
  
  const a = Math.max(0, Math.round(last.a + (Math.random() - 0.35) * 10))
  const b = Math.max(0, Math.round(last.b + (Math.random() - 0.4) * 5))
  
  return [...prev.slice(-11), { label, a, b }]
})
```

### Data Structure

```typescript
interface DualSeriesPoint {
  label: string;  // Time label (HH:MM:SS format)
  a: number;      // Series A value (answered calls)
  b: number;      // Series B value (failed calls)
}
```

---

## Campaign Data Integration

### Request

```
GET /campaigns
```

### Response Format

```typescript
interface Campaign {
  id: string;
  name: string;
  description?: string;
  status: "draft" | "running" | "paused" | "completed";
  system_prompt: string;
  voice_id: string;
  max_concurrent_calls: number;
  total_leads: number;
  calls_completed: number;
  calls_failed: number;
  created_at: string;  // ISO8601
  started_at?: string;
  completed_at?: string;
}
```

### Frontend Usage

```typescript
const campaigns = useMemo(() => campaignsQuery.data ?? [], [campaignsQuery.data])

// Stored in state
const [campaigns, setCampaigns] = useState<Campaign[]>([])
setCampaigns(campaignsData.campaigns)
```

---

## Analytics Data Integration

### Request

```
GET /analytics/calls?fromDate=&toDate=&groupBy=day
```

### Response Format

```typescript
interface CallSeriesItem {
  date: string;         // YYYY-MM-DD format
  total_calls: number;
  answered: number;
  failed: number;
}

interface CallAnalyticsResponse {
  series: CallSeriesItem[]
}
```

### Frontend Usage

```typescript
const series = useMemo(() => (q.data?.series ?? []), [q.data])

// Used to generate stacked area chart base values
const baseA = Math.max(6, Math.round(
  series.reduce((a, s) => a + s.answered, 0) / series.length / 6
))
const baseB = Math.max(1, Math.round(
  series.reduce((a, s) => a + s.failed, 0) / series.length / 10
))
```

---

## Data Transformations & Calculations

### Summary Transformation Flow

```
API Response (DashboardSummary)
    ↓
setSummary() - Initial state
setLiveSummary() - Real-time updates
    ↓
KPI Card Calculations:
  - Total Calls: raw value
  - Success Rate: (answered / total) * 100
  - Active Calls: (total * 0.18) + 6
  - Avg Duration: weighted average
    ↓
Delta Calculations:
  - Absolute: current - previous
  - Percentage: (abs / previous) * 100
    ↓
Status Colors:
  - Green: >= thresholds
  - Yellow: >= yellow threshold
  - Red: < yellow threshold
```

### Live Bucket Transformation Flow

```
LiveTimeBucket[] (1-minute intervals)
    ↓
Time Range Filter (activeRange)
    ↓
liveBucketsBase (filtered buckets)
    ↓
Forecast Calculation:
  - 6-bucket moving average
  - Linear regression slope
  - forecastTotal = cur + (slope * 0.9)
    ↓
Anomaly Detection:
  - Spike: total > avg * 1.7
  - Drop: total < avg * 0.5
    ↓
Peak Detection:
  - Top 2 highest volume buckets
    ↓
Chart Display Data
```

---

## Response Formats & Schemas

### DashboardSummary Response

```json
{
  "total_calls": 1247,
  "answered_calls": 1089,
  "failed_calls": 158,
  "minutes_used": 3420,
  "minutes_remaining": 1580,
  "active_campaigns": 3
}
```

### Campaign Response

```json
{
  "campaigns": [
    {
      "id": "camp-001",
      "name": "Holiday Sales Outreach",
      "description": "End of year promotional campaign",
      "status": "running",
      "system_prompt": "You are a friendly sales representative...",
      "voice_id": "voice-001",
      "max_concurrent_calls": 10,
      "total_leads": 500,
      "calls_completed": 342,
      "calls_failed": 45,
      "created_at": "2024-12-15T10:00:00Z",
      "started_at": "2024-12-16T09:00:00Z"
    }
  ]
}
```

### Analytics Response

```json
{
  "series": [
    {
      "date": "2024-12-24",
      "total_calls": 145,
      "answered": 128,
      "failed": 17
    },
    {
      "date": "2024-12-25",
      "total_calls": 52,
      "answered": 45,
      "failed": 7
    }
  ]
}
```

---

## Error Handling

### API Error Handling

```typescript
async function loadData() {
  try {
    setLoading(true)
    const [summaryData, campaignsData, analytics] = await Promise.all([
      dashboardApi.getDashboardSummary(),
      dashboardApi.listCampaigns(),
      extendedApi.getCallAnalytics(),
    ])
    setSummary(summaryData)
    setLiveSummary(summaryData)
    setCampaigns(campaignsData.campaigns)
    setSeries(analytics.series)
  } catch (err) {
    setError(err instanceof Error ? err.message : "Failed to load dashboard")
  } finally {
    setLoading(false)
  }
}
```

### Error States

| State | Display | Recovery |
|-------|---------|----------|
| Loading | Spinner | Wait for data |
| Error | Error message | Retry loadData() |
| No data | Empty state | No calls yet |

---

## Complete Data Flow Diagram

```
┌─────────────────────────────────────────────────────────┐
│         Dashboard Page Initialization                    │
└────────────────────────┬────────────────────────────────┘
                         │
                ┌────────┴────────┐
                │                 │
                ▼                 ▼
    ┌──────────────────────┐  ┌────────────────────┐
    │ Load Initial Data    │  │ Setup Simulated    │
    │ (Promise.all)        │  │ Live Buckets       │
    └──────────┬───────────┘  └────────┬───────────┘
               │                       │
       ┌───────┼───────────┬───────────┤
       │       │           │           │
       ▼       ▼           ▼           ▼
    Summary Campaign Analytics      SimBuckets
       │       │           │           │
       ▼       ▼           ▼           ▼
    setSummary setCampaigns setSeries setSimBuckets
       │                               │
       └───────────────┬───────────────┘
                       │
              ┌────────▼────────┐
              │ Render Initial  │
              │ Dashboard       │
              └────────┬────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
        ▼              ▼              ▼
    KPI Cards      Live Charts    Meters
        │              │              │
        └──────────────┼──────────────┘
                       │
              ┌────────▼────────┐
              │ Real-time Update│
              │ (Every 1s)      │
              └────────┬────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
        ▼              ▼              ▼
    Update        Add Bucket    Update Bars
    Summary       & Forecast    & Series
        │              │              │
        └──────────────┼──────────────┘
                       │
              ┌────────▼────────┐
              │ Re-calculate    │
              │ All KPIs        │
              └────────┬────────┘
                       │
              ┌────────▼────────┐
              │ Animate Updates │
              │ (Framer Motion) │
              └─────────────────┘
```

---

## Implementation Checklist for Backend

**Before deployment, ensure:**

- [ ] All fields in DashboardSummary response match spec
- [ ] All field values are numbers (no null/undefined)
- [ ] Dates are ISO8601 format strings
- [ ] Response time < 500ms for initial load
- [ ] Analytics supports custom date ranges
- [ ] Campaign list returns complete data
- [ ] Error messages are descriptive
- [ ] Responses are validated before sending
- [ ] All required fields are present

---

**Document Status:** ✅ Complete & Ready for Backend Implementation  
**Last Verified:** 2026-04-13  
**Version:** 2.1 (Full Alignment)
