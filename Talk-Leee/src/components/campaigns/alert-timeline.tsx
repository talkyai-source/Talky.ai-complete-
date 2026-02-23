"use client";

import { useEffect, useMemo, useState } from "react";
import type { Campaign } from "@/lib/dashboard-api";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import {
    AlertItem,
    AlertSeverity,
    AlertStatus,
    AlertType,
    isAlertSnoozed,
    severityBadgeClass,
} from "@/lib/campaign-performance";
import { cn } from "@/lib/utils";

type AlertTab = "Impact Analysis" | "Root Cause" | "Timeline Visualization" | "Recommended Actions" | "Related Incidents";

function toLocalStorage<T>(key: string, value: T) {
    try {
        window.localStorage.setItem(key, JSON.stringify(value));
    } catch { }
}

function fromLocalStorage<T>(key: string, fallback: T) {
    try {
        const raw = window.localStorage.getItem(key);
        if (!raw) return fallback;
        return JSON.parse(raw) as T;
    } catch {
        return fallback;
    }
}

function typeBadgeClass(type: AlertType) {
    switch (type) {
        case "Network":
            return "bg-background text-fuchsia-800 border border-fuchsia-700/50 dark:text-fuchsia-300 dark:border-fuchsia-400/50";
        case "API":
            return "bg-background text-cyan-800 border border-cyan-700/50 dark:text-cyan-300 dark:border-cyan-400/50";
        case "Campaign":
            return "bg-background text-emerald-800 border border-emerald-700/50 dark:text-emerald-300 dark:border-emerald-400/50";
        case "System":
            return "bg-background text-slate-800 border border-slate-600/50 dark:text-slate-200 dark:border-slate-500/60";
        default:
            return "bg-background text-slate-800 border border-slate-600/50 dark:text-slate-200 dark:border-slate-500/60";
    }
}

function statusBadgeClass(status: AlertStatus) {
    switch (status) {
        case "Active":
            return "bg-background text-red-800 border border-red-700/50 dark:text-red-300 dark:border-red-400/50";
        case "Investigating":
            return "bg-background text-orange-800 border border-orange-700/50 dark:text-orange-300 dark:border-orange-400/50";
        case "Resolved":
            return "bg-background text-emerald-800 border border-emerald-700/50 dark:text-emerald-300 dark:border-emerald-400/50";
        default:
            return "bg-background text-slate-800 border border-slate-600/50 dark:text-slate-200 dark:border-slate-500/60";
    }
}

function seedAlerts(campaigns: Campaign[]): AlertItem[] {
    const now = Date.now();
    const c = campaigns[0]?.id;
    return [
        {
            id: `al-${now - 1}`,
            title: "Carrier error spike",
            description: "Increased carrier error codes detected on outbound calls, retries engaged.",
            severity: "Critical",
            type: "Network",
            status: "Active",
            createdAt: new Date(now - 1000 * 60 * 18).toISOString(),
            updatedAt: new Date(now - 1000 * 60 * 6).toISOString(),
            acknowledged: false,
            relatedCampaignIds: c ? [c] : [],
            metadata: { code: "486", p95_ms: 540 },
        },
        {
            id: `al-${now - 2}`,
            title: "API throttling warning",
            description: "Upstream API returned 429 responses above threshold for 3 minutes.",
            severity: "Warning",
            type: "API",
            status: "Investigating",
            createdAt: new Date(now - 1000 * 60 * 55).toISOString(),
            updatedAt: new Date(now - 1000 * 60 * 20).toISOString(),
            acknowledged: true,
            metadata: { route: "/calls", rate: "12/min" },
        },
        {
            id: `al-${now - 3}`,
            title: "Campaign completion delayed",
            description: "Campaign completion ETA extended due to lead pacing settings.",
            severity: "Info",
            type: "Campaign",
            status: "Resolved",
            createdAt: new Date(now - 1000 * 60 * 60 * 28).toISOString(),
            updatedAt: new Date(now - 1000 * 60 * 60 * 20).toISOString(),
            acknowledged: true,
            metadata: { eta: "2h" },
        },
    ];
}

export function AlertTimeline({ campaigns }: { campaigns: Campaign[] }) {
    const [alerts, setAlerts] = useState<AlertItem[]>(() => seedAlerts(campaigns));
    const [sev, setSev] = useState<Set<AlertSeverity>>(new Set());
    const [type, setType] = useState<Set<AlertType>>(new Set());
    const [status, setStatus] = useState<Set<AlertStatus>>(new Set());
    const [detailsId, setDetailsId] = useState<string | null>(null);
    const [ruleOpen, setRuleOpen] = useState(false);
    const [tab, setTab] = useState<AlertTab>("Impact Analysis");

    useEffect(() => {
        const saved = fromLocalStorage<{ sev: AlertSeverity[]; type: AlertType[]; status: AlertStatus[] }>("campaigns.performance.alertPrefs", {
            sev: [],
            type: [],
            status: [],
        });
        const raf = window.requestAnimationFrame(() => {
            setSev(new Set(saved.sev || []));
            setType(new Set(saved.type || []));
            setStatus(new Set(saved.status || []));
        });
        return () => window.cancelAnimationFrame(raf);
    }, []);

    useEffect(() => {
        toLocalStorage("campaigns.performance.alertPrefs", { sev: Array.from(sev), type: Array.from(type), status: Array.from(status) });
    }, [sev, status, type]);

    const filtered = useMemo(() => {
        const now = new Date();
        return alerts.filter((a) => {
            if (isAlertSnoozed(a, now)) return false;
            if (sev.size > 0 && !sev.has(a.severity)) return false;
            if (type.size > 0 && !type.has(a.type)) return false;
            if (status.size > 0 && !status.has(a.status)) return false;
            return true;
        });
    }, [alerts, sev, status, type]);

    const details = useMemo(() => alerts.find((a) => a.id === detailsId) || null, [alerts, detailsId]);

    const updateAlert = (id: string, updater: (prev: AlertItem) => AlertItem) => {
        setAlerts((prev) => prev.map((a) => (a.id === id ? updater(a) : a)));
    };

    const footerButtons = details ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => updateAlert(details.id, (a) => ({ ...a, acknowledged: true, updatedAt: new Date().toISOString() }))}
                >
                    Acknowledge
                </Button>
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => updateAlert(details.id, (a) => ({ ...a, status: "Investigating", updatedAt: new Date().toISOString() }))}
                >
                    Investigate
                </Button>
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => updateAlert(details.id, (a) => ({ ...a, status: "Resolved", updatedAt: new Date().toISOString() }))}
                >
                    Resolve
                </Button>
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() =>
                        updateAlert(details.id, (a) => ({
                            ...a,
                            severity: a.severity === "Critical" ? "Critical" : a.severity === "Warning" ? "Critical" : "Warning",
                            updatedAt: new Date().toISOString(),
                        }))
                    }
                >
                    Escalate
                </Button>
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() =>
                        updateAlert(details.id, (a) => ({
                            ...a,
                            snoozedUntil: new Date(Date.now() + 1000 * 60 * 30).toISOString(),
                            updatedAt: new Date().toISOString(),
                        }))
                    }
                >
                    Snooze
                </Button>
                <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => setRuleOpen(true)}
                >
                    Create Rule
                </Button>
            </div>
            <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setDetailsId(null)}
            >
                Close
            </Button>
        </div>
    ) : null;

    return (
        <div className="content-card">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div>
                    <div className="text-sm font-semibold text-foreground">Error & Alert Timeline</div>
                    <div className="mt-1 text-sm text-muted-foreground">Track, triage, and resolve incidents.</div>
                </div>
                <div className="text-sm font-semibold text-muted-foreground">{filtered.length} alerts</div>
            </div>

            <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3">
                <FilterPills
                    label="Severity"
                    options={["Critical", "Warning", "Info"] as AlertSeverity[]}
                    value={sev}
                    onChange={(next) => setSev(next)}
                    classFor={(v) => severityBadgeClass(v)}
                />
                <FilterPills
                    label="Type"
                    options={["Network", "API", "Campaign", "System"] as AlertType[]}
                    value={type}
                    onChange={(next) => setType(next)}
                    classFor={(v) => typeBadgeClass(v)}
                />
                <FilterPills
                    label="Status"
                    options={["Active", "Resolved", "Investigating"] as AlertStatus[]}
                    value={status}
                    onChange={(next) => setStatus(next)}
                    classFor={(v) => statusBadgeClass(v)}
                />
            </div>

            <div className="mt-4 space-y-2">
                {filtered.map((a) => (
                    <button
                        key={a.id}
                        type="button"
                        className="flex w-full items-start justify-between gap-3 rounded-xl border border-border bg-card/50 px-3 py-3 text-left transition-[background-color,border-color,box-shadow,color] duration-150 ease-out hover:bg-accent hover:text-accent-foreground hover:border-border/80 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        onClick={() => {
                            setTab("Impact Analysis");
                            setDetailsId(a.id);
                        }}
                    >
                        <div className="min-w-0">
                            <div className="flex items-center gap-2">
                                <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold", severityBadgeClass(a.severity))}>
                                    {a.severity}
                                </span>
                                <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold", typeBadgeClass(a.type))}>
                                    {a.type}
                                </span>
                                <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold", statusBadgeClass(a.status))}>
                                    {a.status}
                                </span>
                                {!a.acknowledged ? (
                                    <span className="inline-flex items-center rounded-full bg-background border border-indigo-700/50 px-2 py-0.5 text-xs font-semibold text-indigo-800 dark:border-indigo-400/50 dark:text-indigo-300">
                                        New
                                    </span>
                                ) : (
                                    <span className="inline-flex items-center rounded-full bg-background border border-slate-400/70 px-2 py-0.5 text-xs font-semibold text-slate-700 dark:border-slate-500/70 dark:text-slate-200">
                                        Ack
                                    </span>
                                )}
                            </div>
                            <div className="mt-2 text-sm font-semibold text-foreground">{a.title}</div>
                            <div className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{a.description}</div>
                        </div>
                        <div className="shrink-0 text-right text-xs font-semibold text-muted-foreground">
                            <div>{new Date(a.createdAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</div>
                            <div className="mt-1">{new Date(a.createdAt).toLocaleDateString()}</div>
                        </div>
                    </button>
                ))}
            </div>

            <Modal
                open={detailsId !== null}
                onOpenChange={(next) => setDetailsId(next ? detailsId : null)}
                title={details ? details.title : "Alert details"}
                description={details ? `${details.severity} • ${details.type} • ${details.status}` : undefined}
                size="xl"
                footer={footerButtons}
            >
                {details ? (
                    <div className="space-y-4">
                        <div className="flex flex-wrap items-center gap-2">
                            {(["Impact Analysis", "Root Cause", "Timeline Visualization", "Recommended Actions", "Related Incidents"] as AlertTab[]).map((t) => (
                                <Button
                                    key={t}
                                    type="button"
                                    size="sm"
                                    variant={tab === t ? "secondary" : "outline"}
                                    onClick={() => setTab(t)}
                                >
                                    {t}
                                </Button>
                            ))}
                        </div>
                        <div className="rounded-xl border border-border bg-card p-4">
                            {tab === "Impact Analysis" ? <ImpactPanel alert={details} campaigns={campaigns} /> : null}
                            {tab === "Root Cause" ? <RootCausePanel alert={details} /> : null}
                            {tab === "Timeline Visualization" ? <TimelinePanel alert={details} /> : null}
                            {tab === "Recommended Actions" ? <RecommendedPanel alert={details} /> : null}
                            {tab === "Related Incidents" ? <RelatedPanel alert={details} /> : null}
                        </div>
                    </div>
                ) : null}
            </Modal>

            <Modal open={ruleOpen} onOpenChange={setRuleOpen} title="Rule builder" description="Create an alert rule (prototype mode)." size="lg">
                <RuleBuilder onDone={() => setRuleOpen(false)} />
            </Modal>
        </div>
    );
}

function FilterPills<T extends string>({
    label,
    options,
    value,
    onChange,
    classFor: _classFor,
}: {
    label: string;
    options: T[];
    value: Set<T>;
    onChange: (next: Set<T>) => void;
    classFor: (v: T) => string;
}) {
    const toggle = (v: T) => {
        const next = new Set(value);
        if (next.has(v)) next.delete(v);
        else next.add(v);
        onChange(next);
    };

    return (
        <div className="rounded-xl border border-border bg-card p-3">
            <div className="text-xs font-semibold text-muted-foreground">{label}</div>
            <div className="mt-2 flex flex-wrap gap-2">
                {options.map((o) => (
                    <button
                        key={o}
                        type="button"
                        className={cn(
                            "inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold transition-[background-color,border-color,box-shadow] duration-150 ease-out hover:bg-muted/60 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                            value.has(o)
                                ? cn(_classFor(o), "border border-teal-500/60 bg-teal-600 text-white hover:bg-teal-700")
                                : "border border-teal-500/40 bg-background text-foreground hover:bg-teal-600/15 dark:bg-zinc-900/60 dark:text-white/90 dark:hover:bg-teal-600/20"
                        )}
                        aria-pressed={value.has(o)}
                        onClick={() => toggle(o)}
                    >
                        {o}
                    </button>
                ))}
                {value.size > 0 ? (
                    <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => onChange(new Set())}
                        className="text-teal-600 hover:bg-teal-600/15 hover:text-teal-600 dark:text-teal-300 dark:hover:bg-teal-600/20 dark:hover:text-teal-200"
                    >
                        Clear
                    </Button>
                ) : null}
            </div>
        </div>
    );
}

function ImpactPanel({ alert, campaigns }: { alert: AlertItem; campaigns: Campaign[] }) {
    const related = useMemo(() => {
        if (!alert.relatedCampaignIds || alert.relatedCampaignIds.length === 0) return [];
        const set = new Set(alert.relatedCampaignIds);
        return campaigns.filter((c) => set.has(c.id));
    }, [alert.relatedCampaignIds, campaigns]);

    return (
        <div className="space-y-3">
            <div className="text-sm font-semibold text-foreground">Impact Analysis</div>
            <div className="text-sm text-muted-foreground">{alert.description}</div>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <div className="rounded-xl border border-border bg-muted/30 p-3">
                    <div className="text-xs font-semibold text-muted-foreground">Scope</div>
                    <div className="mt-2 space-y-1 text-sm text-foreground">
                        <div>Start: {new Date(alert.createdAt).toLocaleString()}</div>
                        <div>Last update: {new Date(alert.updatedAt).toLocaleString()}</div>
                    </div>
                </div>
                <div className="rounded-xl border border-border bg-muted/30 p-3">
                    <div className="text-xs font-semibold text-muted-foreground">Related campaigns</div>
                    <div className="mt-2 space-y-2">
                        {related.length === 0 ? (
                            <div className="text-sm text-muted-foreground">—</div>
                        ) : (
                            related.map((c) => (
                                <div key={c.id} className="flex items-center justify-between gap-3">
                                    <div className="min-w-0">
                                        <div className="truncate text-sm font-semibold text-foreground">{c.name}</div>
                                        <div className="truncate text-xs font-semibold text-muted-foreground">{c.id}</div>
                                    </div>
                                    <Button type="button" variant="outline" size="sm" asChild>
                                        <a href={`/campaigns/${c.id}`}>Open</a>
                                    </Button>
                                </div>
                            ))
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}

function RootCausePanel({ alert }: { alert: AlertItem }) {
    return (
        <div className="space-y-3">
            <div className="text-sm font-semibold text-foreground">Root Cause</div>
            <div className="text-sm text-muted-foreground">
                Prototype analysis: likely upstream dependency behavior, transient network conditions, or campaign pacing configuration.
            </div>
            <div className="rounded-xl border border-border bg-muted/30 p-3">
                <div className="text-xs font-semibold text-muted-foreground">Metadata</div>
                <div className="mt-2 space-y-2">
                    {alert.metadata ? (
                        Object.entries(alert.metadata).map(([k, v]) => (
                            <div key={k} className="flex items-center justify-between gap-3">
                                <div className="text-sm text-muted-foreground">{k}</div>
                                <div className="text-sm font-semibold text-foreground tabular-nums">{String(v)}</div>
                            </div>
                        ))
                    ) : (
                        <div className="text-sm text-muted-foreground">—</div>
                    )}
                </div>
            </div>
        </div>
    );
}

function TimelinePanel({ alert }: { alert: AlertItem }) {
    const events = [
        { t: alert.createdAt, label: "Detected" },
        { t: new Date(new Date(alert.createdAt).getTime() + 1000 * 60 * 6).toISOString(), label: "Investigating" },
        { t: alert.updatedAt, label: "Latest update" },
    ];
    return (
        <div className="space-y-3">
            <div className="text-sm font-semibold text-foreground">Timeline Visualization</div>
            <div className="space-y-2">
                {events.map((e) => (
                    <div key={e.t} className="flex items-start gap-3 rounded-xl border border-border bg-muted/30 px-3 py-2">
                        <div className="mt-0.5 h-2 w-2 rounded-full bg-primary/60" />
                        <div className="min-w-0 flex-1">
                            <div className="text-sm font-semibold text-foreground">{e.label}</div>
                            <div className="text-xs font-semibold text-muted-foreground tabular-nums">{new Date(e.t).toLocaleString()}</div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}

function RecommendedPanel({ alert }: { alert: AlertItem }) {
    const items = [
        "Acknowledge the alert and assign an owner.",
        "Inspect network/API error codes and recent deployment changes.",
        "Apply targeted retries or lower concurrency temporarily.",
        "Create a rule to auto-escalate if conditions persist.",
    ];
    return (
        <div className="space-y-3">
            <div className="text-sm font-semibold text-foreground">Recommended Actions</div>
            <div className="space-y-2">
                {items.map((t) => (
                    <div key={t} className="rounded-xl border border-border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
                        {t}
                    </div>
                ))}
            </div>
            {alert.status === "Resolved" ? (
                <div className="text-sm font-semibold text-emerald-500">Status indicates the incident is resolved.</div>
            ) : null}
        </div>
    );
}

function RelatedPanel({ alert }: { alert: AlertItem }) {
    return (
        <div className="space-y-3">
            <div className="text-sm font-semibold text-foreground">Related Incidents</div>
            <div className="rounded-xl border border-border bg-muted/30 p-3 text-sm text-muted-foreground">
                Prototype view: no related incidents linked for {alert.id}.
            </div>
        </div>
    );
}

function RuleBuilder({ onDone }: { onDone: () => void }) {
    const [name, setName] = useState("Auto-escalate carrier errors");
    const [condition, setCondition] = useState("type == Network AND severity >= Warning");
    const [action, setAction] = useState("Escalate severity to Critical");

    return (
        <div className="space-y-4">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <div>
                    <label className="text-xs font-semibold text-muted-foreground">Rule name</label>
                    <input
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        className="mt-1 h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    />
                </div>
                <div>
                    <label className="text-xs font-semibold text-muted-foreground">Action</label>
                    <input
                        value={action}
                        onChange={(e) => setAction(e.target.value)}
                        className="mt-1 h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    />
                </div>
                <div className="md:col-span-2">
                    <label className="text-xs font-semibold text-muted-foreground">Condition</label>
                    <textarea
                        value={condition}
                        onChange={(e) => setCondition(e.target.value)}
                        className="mt-1 h-24 w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    />
                </div>
            </div>
            <div className="flex items-center justify-end gap-2">
                <Button type="button" variant="outline" onClick={onDone}>
                    Cancel
                </Button>
                <Button
                    type="button"
                    onClick={() => {
                        try {
                            const saved = { name, condition, action, savedAt: new Date().toISOString() };
                            const key = "campaigns.performance.rules";
                            const prevRaw = window.localStorage.getItem(key);
                            const prev = prevRaw ? (JSON.parse(prevRaw) as unknown[]) : [];
                            window.localStorage.setItem(key, JSON.stringify([saved, ...(prev || [])].slice(0, 50)));
                        } catch { }
                        onDone();
                    }}
                >
                    Save rule
                </Button>
            </div>
        </div>
    );
}
