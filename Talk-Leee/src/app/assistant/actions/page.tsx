"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { useAuth } from "@/lib/auth-context";
import { type AssistantRunsQuery, useAssistantActions, useAssistantExecute, useAssistantPlan, useAssistantRunRetry, useAssistantRuns } from "@/lib/api-hooks";
import { ApiClientError, isApiClientError } from "@/lib/http-client";
import { captureException, captureMessage } from "@/lib/monitoring";
import { dashboardApi, type Campaign, type Contact } from "@/lib/dashboard-api";
import type { AssistantAction, AssistantPlan, AssistantRun, AssistantRunStatus } from "@/lib/models";
import { cn } from "@/lib/utils";
import { AlertCircle, CheckCircle2, Clock, Download, FileJson, Loader2, Play, RefreshCw, Search, XCircle } from "lucide-react";

function formatError(err: unknown) {
    if (isApiClientError(err)) return err.message;
    return err instanceof Error ? err.message : "Request failed";
}

type ViewMode = "table" | "timeline";
type DatePreset = "today" | "last7" | "custom";
type Tab = "Audit Log" | "Catalog" | "Verification";

type LeadOption = {
    id: string;
    label: string;
    subtitle?: string;
};

const FILTERS_STORAGE_KEY = "assistant.actions.audit.filters.v1";
const UI_STORAGE_KEY = "assistant.actions.audit.ui.v1";
const VERIFICATION_STORAGE_KEY = "assistant.actions.verification.v1";

const STATUS_LABELS: Record<AssistantRunStatus, string> = {
    pending: "Pending",
    in_progress: "In progress",
    completed: "Completed",
    failed: "Failed",
};

function normalizeActionType(action: AssistantAction) {
    return action.id || action.name;
}

function actionCategory(type: string) {
    const raw = type.trim();
    const idx = raw.indexOf(":");
    if (idx > 0) return raw.slice(0, idx).trim();
    const slash = raw.indexOf("/");
    if (slash > 0) return raw.slice(0, slash).trim();
    const dash = raw.indexOf(" - ");
    if (dash > 0) return raw.slice(0, dash).trim();
    return "General";
}

function safeJsonParse<T>(raw: string | null, fallback: T): T {
    if (!raw) return fallback;
    try {
        return JSON.parse(raw) as T;
    } catch {
        return fallback;
    }
}

function toUtcIsoStartOfDay(dateStr: string) {
    const [y, m, d] = dateStr.split("-").map((v) => Number(v));
    if (!y || !m || !d) return undefined;
    return new Date(Date.UTC(y, m - 1, d, 0, 0, 0, 0)).toISOString();
}

function toUtcIsoEndOfDay(dateStr: string) {
    const [y, m, d] = dateStr.split("-").map((v) => Number(v));
    if (!y || !m || !d) return undefined;
    return new Date(Date.UTC(y, m - 1, d, 23, 59, 59, 999)).toISOString();
}

function formatDateTime(iso: string | undefined | null) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(d);
}

function statusBadgeClass(status: AssistantRunStatus) {
    if (status === "completed") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
    if (status === "failed") return "border-red-500/30 bg-red-500/10 text-red-200";
    if (status === "in_progress") return "border-blue-500/30 bg-blue-500/10 text-blue-200";
    return "border-white/10 bg-white/5 text-gray-200";
}

function statusIcon(status: AssistantRunStatus) {
    if (status === "completed") return <CheckCircle2 className="h-4 w-4 text-emerald-300" aria-hidden />;
    if (status === "failed") return <XCircle className="h-4 w-4 text-red-300" aria-hidden />;
    if (status === "in_progress") return <Loader2 className="h-4 w-4 animate-spin text-blue-200" aria-hidden />;
    return <Clock className="h-4 w-4 text-gray-200" aria-hidden />;
}

function downloadFile(name: string, content: Blob) {
    if (typeof document === "undefined") return;
    const url = URL.createObjectURL(content);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

function csvEscape(v: unknown) {
    if (v === null || v === undefined) return "";
    const s = typeof v === "string" ? v : JSON.stringify(v);
    if (/[,"\n]/.test(s)) return `"${s.replaceAll('"', '""')}"`;
    return s;
}

function runsToCsv(items: AssistantRun[]) {
    const header = [
        "id",
        "action_type",
        "source",
        "lead_id",
        "status",
        "created_at",
        "started_at",
        "completed_at",
        "result",
        "request_payload",
        "response_payload",
        "error",
    ];
    const rows = items.map((r) => [
        r.id,
        r.actionType,
        r.source,
        r.leadId ?? "",
        r.status,
        r.createdAt,
        r.startedAt ?? "",
        r.completedAt ?? "",
        r.result ?? "",
        r.requestPayload ?? null,
        r.responsePayload ?? null,
        r.error ?? null,
    ]);
    return [header.map(csvEscape).join(","), ...rows.map((row) => row.map(csvEscape).join(","))].join("\n") + "\n";
}

function guidanceForError(err: unknown) {
    if (err && typeof err === "object") {
        const obj = err as Record<string, unknown>;
        const message = typeof obj.message === "string" ? obj.message : undefined;
        const nextSteps = Array.isArray(obj.nextSteps) ? obj.nextSteps.filter((s): s is string => typeof s === "string") : undefined;
        const retryable = typeof obj.retryable === "boolean" ? obj.retryable : undefined;
        const docsUrl = typeof obj.docsUrl === "string" ? obj.docsUrl : undefined;
        if (message || nextSteps?.length || typeof retryable === "boolean" || docsUrl) {
            return {
                rootCause: message ?? "Action failed.",
                nextSteps:
                    nextSteps && nextSteps.length > 0
                        ? nextSteps
                        : ["Open run details and inspect request/response payloads.", "Retry after adjusting inputs.", "Contact support if the error persists."],
                retryable: retryable ?? true,
                docsUrl: docsUrl ?? "https://docs.talk-lee.ai/troubleshooting/assistant-actions",
            };
        }
    }
    if (isApiClientError(err)) {
        const apiErr = err as ApiClientError;
        if (apiErr.code === "unauthorized") {
            return {
                rootCause: "Your session is missing or expired.",
                nextSteps: ["Sign in again.", "Verify API URL configuration.", "Confirm your role has access."],
                retryable: false,
                docsUrl: "https://docs.talk-lee.ai/troubleshooting/auth",
            };
        }
        if (apiErr.code === "forbidden") {
            return {
                rootCause: "Your role does not have permission for this action.",
                nextSteps: ["Request access from an admin.", "Confirm you are using the correct workspace."],
                retryable: false,
                docsUrl: "https://docs.talk-lee.ai/troubleshooting/rbac",
            };
        }
        if (apiErr.code === "rate_limited") {
            return {
                rootCause: "The service is rate-limiting requests.",
                nextSteps: ["Wait briefly and retry.", "Reduce parallel actions.", "Check for runaway automation."],
                retryable: true,
                docsUrl: "https://docs.talk-lee.ai/troubleshooting/rate-limits",
            };
        }
        if (apiErr.code === "timeout" || apiErr.code === "network_error" || apiErr.code === "server_error") {
            return {
                rootCause: "The service did not respond successfully.",
                nextSteps: ["Retry the action.", "Check system health and recent deploys.", "If persistent, contact support with request id."],
                retryable: true,
                docsUrl: "https://docs.talk-lee.ai/troubleshooting/network",
            };
        }
    }
    return {
        rootCause: "An unexpected error occurred.",
        nextSteps: ["Retry the action.", "Inspect request/response payloads.", "Contact support if the error persists."],
        retryable: true,
        docsUrl: "https://docs.talk-lee.ai/troubleshooting",
    };
}

function contactLabel(c: Contact) {
    const name = [c.first_name, c.last_name].filter(Boolean).join(" ").trim();
    if (name.length > 0) return name;
    return c.phone_number;
}

function matchScore(haystack: string, query: string) {
    const h = haystack.toLowerCase();
    const q = query.toLowerCase().trim();
    if (q.length === 0) return 1;
    if (h === q) return 10;
    if (h.startsWith(q)) return 6;
    if (h.includes(q)) return 3;
    return 0;
}

function LeadTypeahead({
    value,
    onChange,
    options,
    loading,
    error,
    ariaLabel,
    placeholder,
}: {
    value: string;
    onChange: (next: { leadId?: string; leadLabel: string }) => void;
    options: LeadOption[];
    loading: boolean;
    error: string | null;
    ariaLabel: string;
    placeholder: string;
}) {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState(value);
    const [activeIndex, setActiveIndex] = useState(0);
    const listRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        setQuery(value);
    }, [value]);

    const filtered = useMemo(() => {
        const q = query.trim();
        const base = options.map((o) => ({
            o,
            score: matchScore(o.label, q) + (o.subtitle ? matchScore(o.subtitle, q) * 0.25 : 0),
        }));
        const candidates = q.length === 0 ? base : base.filter((x) => x.score > 0);
        candidates.sort((a, b) => b.score - a.score);
        return candidates.map((x) => x.o).slice(0, 10);
    }, [options, query]);

    useEffect(() => {
        setActiveIndex(0);
    }, [query, open]);

    useEffect(() => {
        if (!open) return;
        const el = listRef.current;
        if (!el) return;
        const cur = el.querySelector<HTMLButtonElement>(`button[data-idx="${activeIndex}"]`);
        cur?.scrollIntoView({ block: "nearest" });
    }, [activeIndex, open]);

    return (
        <div className="relative">
            <div className="relative">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
                <Input
                    value={query}
                    onChange={(e) => {
                        setQuery(e.target.value);
                        setOpen(true);
                    }}
                    onFocus={() => setOpen(true)}
                    onBlur={() => {
                        window.setTimeout(() => setOpen(false), 120);
                    }}
                    onKeyDown={(e) => {
                        if (!open) return;
                        if (e.key === "ArrowDown") {
                            e.preventDefault();
                            setActiveIndex((v) => Math.min(v + 1, filtered.length - 1));
                        }
                        if (e.key === "ArrowUp") {
                            e.preventDefault();
                            setActiveIndex((v) => Math.max(v - 1, 0));
                        }
                        if (e.key === "Enter") {
                            e.preventDefault();
                            const pick = filtered[activeIndex];
                            if (!pick) return;
                            onChange({ leadId: pick.id, leadLabel: pick.label });
                            setQuery(pick.label);
                            setOpen(false);
                        }
                        if (e.key === "Escape") {
                            e.preventDefault();
                            setOpen(false);
                        }
                    }}
                    placeholder={loading ? "Loading…" : placeholder}
                    disabled={loading || Boolean(error)}
                    className="pl-9"
                    aria-label={ariaLabel}
                    aria-expanded={open}
                    aria-haspopup="listbox"
                />
            </div>
            {error ? <div className="mt-2 text-xs text-red-600">{error}</div> : null}
            {open && filtered.length > 0 ? (
                <div ref={listRef} role="listbox" className="absolute z-20 mt-2 w-full overflow-hidden rounded-xl border border-gray-200 bg-white shadow-xl">
                    {filtered.map((opt, idx) => {
                        const active = idx === activeIndex;
                        return (
                            <button
                                key={opt.id}
                                type="button"
                                role="option"
                                aria-selected={value === opt.label}
                                data-idx={idx}
                                className={cn(
                                    "flex w-full items-start justify-between gap-3 px-3 py-2 text-left text-sm text-gray-900",
                                    active ? "bg-gray-100" : "hover:bg-gray-50"
                                )}
                                onMouseEnter={() => setActiveIndex(idx)}
                                onClick={() => {
                                    onChange({ leadId: opt.id, leadLabel: opt.label });
                                    setQuery(opt.label);
                                    setOpen(false);
                                }}
                            >
                                <div className="min-w-0">
                                    <div className="truncate font-medium">{opt.label}</div>
                                    {opt.subtitle ? <div className="mt-0.5 truncate text-xs text-muted-foreground">{opt.subtitle}</div> : null}
                                </div>
                            </button>
                        );
                    })}
                </div>
            ) : null}
        </div>
    );
}

export default function AssistantActionsPage() {
    const { user } = useAuth();
    const actionsQ = useAssistantActions();
    const executeM = useAssistantExecute();
    const planM = useAssistantPlan();
    const retryM = useAssistantRunRetry();

    const [tab, setTab] = useState<Tab>("Audit Log");
    const [viewMode, setViewMode] = useState<ViewMode>("table");
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(50);
    const [statuses, setStatuses] = useState<Array<AssistantRunStatus>>(["pending", "in_progress", "completed", "failed"]);
    const [actionType, setActionType] = useState<string>("");
    const [preset, setPreset] = useState<DatePreset>("last7");
    const [fromDate, setFromDate] = useState<string>("");
    const [toDate, setToDate] = useState<string>("");
    const [leadFilterLabel, setLeadFilterLabel] = useState("");
    const [leadFilterId, setLeadFilterId] = useState<string | undefined>(undefined);
    const [sortKey, setSortKey] = useState<AssistantRunsQuery["sortKey"]>("createdAt");
    const [sortDir, setSortDir] = useState<AssistantRunsQuery["sortDir"]>("desc");

    const [leadOptions, setLeadOptions] = useState<LeadOption[]>([]);
    const [leadsLoading, setLeadsLoading] = useState(false);
    const [leadsError, setLeadsError] = useState<string | null>(null);

    const [detailsOpen, setDetailsOpen] = useState(false);
    const [detailsRun, setDetailsRun] = useState<AssistantRun | null>(null);
    const [planOpen, setPlanOpen] = useState(false);
    const [planResult, setPlanResult] = useState<AssistantPlan | null>(null);
    const [planConfirmOpen, setPlanConfirmOpen] = useState(false);

    const [execActionType, setExecActionType] = useState<string>("");
    const [execSource, setExecSource] = useState<string>("dashboard");
    const [execLeadId, setExecLeadId] = useState<string | undefined>(undefined);
    const [execLeadLabel, setExecLeadLabel] = useState<string>("");
    const [execContext, setExecContext] = useState<string>("{}");
    const [execError, setExecError] = useState<string | null>(null);

    const [verification, setVerification] = useState<Record<string, boolean>>({
        "Data display: table & timeline": false,
        "Filtering: statuses, action type, date, lead": false,
        "Execute: optimistic log and progress UI": false,
        "Plan/preview: plan modal and confirmation": false,
        "Errors: guidance, retry, docs links": false,
        "Audit trail: payloads and export": false,
        "Accessibility: keyboard and labels": false,
        "Performance: filtered view load <2s": false,
        "Security: RBAC enforced": false,
        "Regression tests passing": false,
        "UAT sign-off recorded": false,
        "Monitoring instrumentation enabled": false,
    });

    useEffect(() => {
        if (typeof window === "undefined") return;
        const savedFilters = safeJsonParse<{
            pageSize?: number;
            statuses?: Array<AssistantRunStatus>;
            actionType?: string;
            preset?: DatePreset;
            fromDate?: string;
            toDate?: string;
            leadFilterLabel?: string;
            leadFilterId?: string;
            sortKey?: AssistantRunsQuery["sortKey"];
            sortDir?: AssistantRunsQuery["sortDir"];
        }>(window.localStorage.getItem(FILTERS_STORAGE_KEY), {});
        const savedUi = safeJsonParse<{ viewMode?: ViewMode; tab?: Tab }>(window.localStorage.getItem(UI_STORAGE_KEY), {});
        const savedVerification = safeJsonParse<Record<string, boolean>>(window.localStorage.getItem(VERIFICATION_STORAGE_KEY), {});

        if (savedFilters.pageSize) setPageSize(savedFilters.pageSize);
        if (savedFilters.statuses?.length) setStatuses(savedFilters.statuses);
        if (typeof savedFilters.actionType === "string") setActionType(savedFilters.actionType);
        if (savedFilters.preset) setPreset(savedFilters.preset);
        if (typeof savedFilters.fromDate === "string") setFromDate(savedFilters.fromDate);
        if (typeof savedFilters.toDate === "string") setToDate(savedFilters.toDate);
        if (typeof savedFilters.leadFilterLabel === "string") setLeadFilterLabel(savedFilters.leadFilterLabel);
        if (typeof savedFilters.leadFilterId === "string") setLeadFilterId(savedFilters.leadFilterId);
        if (savedFilters.sortKey) setSortKey(savedFilters.sortKey);
        if (savedFilters.sortDir) setSortDir(savedFilters.sortDir);

        if (savedUi.viewMode) setViewMode(savedUi.viewMode);
        if (savedUi.tab) setTab(savedUi.tab);

        const mergedVerification: Record<string, boolean> = {
            "Data display: table & timeline": false,
            "Filtering: statuses, action type, date, lead": false,
            "Execute: optimistic log and progress UI": false,
            "Plan/preview: plan modal and confirmation": false,
            "Errors: guidance, retry, docs links": false,
            "Audit trail: payloads and export": false,
            "Accessibility: keyboard and labels": false,
            "Performance: filtered view load <2s": false,
            "Security: RBAC enforced": false,
            "Regression tests passing": false,
            "UAT sign-off recorded": false,
            "Monitoring instrumentation enabled": false,
        };
        for (const [k, v] of Object.entries(savedVerification)) mergedVerification[k] = Boolean(v);
        setVerification(mergedVerification);
    }, []);

    useEffect(() => {
        if (typeof window === "undefined") return;
        window.localStorage.setItem(
            FILTERS_STORAGE_KEY,
            JSON.stringify({
                pageSize,
                statuses,
                actionType,
                preset,
                fromDate,
                toDate,
                leadFilterLabel,
                leadFilterId,
                sortKey,
                sortDir,
            })
        );
    }, [actionType, fromDate, leadFilterId, leadFilterLabel, pageSize, preset, sortDir, sortKey, statuses, toDate]);

    useEffect(() => {
        if (typeof window === "undefined") return;
        window.localStorage.setItem(UI_STORAGE_KEY, JSON.stringify({ viewMode, tab }));
    }, [tab, viewMode]);

    useEffect(() => {
        if (typeof window === "undefined") return;
        window.localStorage.setItem(VERIFICATION_STORAGE_KEY, JSON.stringify(verification));
    }, [verification]);

    useEffect(() => {
        let alive = true;
        (async () => {
            try {
                setLeadsLoading(true);
                setLeadsError(null);
                const campaignsRes = await dashboardApi.listCampaigns();
                const campaigns: Campaign[] = campaignsRes.campaigns ?? [];
                const all: LeadOption[] = [];
                for (const camp of campaigns) {
                    const contactsRes = await dashboardApi.listContacts(camp.id, 1, 200);
                    const items: Contact[] = contactsRes.items ?? [];
                    for (const c of items) {
                        const label = contactLabel(c);
                        const subtitle = [c.email, c.phone_number, camp.name].filter(Boolean).join(" • ");
                        all.push({ id: c.id, label, subtitle });
                    }
                }
                if (!alive) return;
                all.sort((a, b) => a.label.localeCompare(b.label));
                setLeadOptions(all);
                if (!execLeadId && all.length > 0) {
                    setExecLeadId(all[0]!.id);
                    setExecLeadLabel(all[0]!.label);
                }
            } catch (e) {
                if (!alive) return;
                setLeadsError(e instanceof Error ? e.message : "Failed to load contacts");
            } finally {
                if (!alive) return;
                setLeadsLoading(false);
            }
        })();
        return () => {
            alive = false;
        };
    }, [execLeadId]);

    const timeWindow = useMemo(() => {
        const now = new Date();
        if (preset === "today") {
            const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 0, 0, 0, 0)).toISOString();
            const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 23, 59, 59, 999)).toISOString();
            return { from: start, to: end };
        }
        if (preset === "last7") {
            const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 23, 59, 59, 999)).toISOString();
            const startDate = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 0, 0, 0, 0));
            startDate.setUTCDate(startDate.getUTCDate() - 6);
            const start = startDate.toISOString();
            return { from: start, to: end };
        }
        const from = fromDate ? toUtcIsoStartOfDay(fromDate) : undefined;
        const to = toDate ? toUtcIsoEndOfDay(toDate) : undefined;
        return { from, to };
    }, [fromDate, preset, toDate]);

    const runsQuery = useMemo<AssistantRunsQuery>(
        () => ({
            page,
            pageSize,
            statuses,
            actionType: actionType.trim().length ? actionType : undefined,
            leadId: leadFilterId,
            from: timeWindow.from,
            to: timeWindow.to,
            sortKey,
            sortDir,
        }),
        [actionType, leadFilterId, page, pageSize, sortDir, sortKey, statuses, timeWindow.from, timeWindow.to]
    );

    const runsQ = useAssistantRuns(runsQuery);
    const runsItems = (runsQ.data as { items: AssistantRun[] } | undefined)?.items ?? [];
    const runsTotal = (runsQ.data as { total?: number } | undefined)?.total;
    const pageCount = useMemo(() => {
        if (typeof runsTotal === "number" && runsTotal > 0) return Math.max(1, Math.ceil(runsTotal / pageSize));
        return runsItems.length < pageSize ? page : page + 1;
    }, [page, pageSize, runsItems.length, runsTotal]);

    const actionsItems = actionsQ.data?.items;
    const actions = actionsItems ?? [];
    const actionTypes = useMemo(() => {
        const types = (actionsItems ?? []).map(normalizeActionType).filter(Boolean);
        const unique = Array.from(new Set(types));
        unique.sort((a, b) => a.localeCompare(b));
        return unique;
    }, [actionsItems]);

    const categorizedActionTypes = useMemo(() => {
        const groups = new Map<string, string[]>();
        for (const t of actionTypes) {
            const c = actionCategory(t);
            const arr = groups.get(c) ?? [];
            arr.push(t);
            groups.set(c, arr);
        }
        const sortedCats = Array.from(groups.keys()).sort((a, b) => a.localeCompare(b));
        return sortedCats.map((cat) => ({ cat, types: (groups.get(cat) ?? []).sort((a, b) => a.localeCompare(b)) }));
    }, [actionTypes]);

    useEffect(() => {
        captureMessage("assistant.actions.audit.view", { tab, viewMode });
    }, [tab, viewMode]);

    function toggleStatus(s: AssistantRunStatus) {
        setPage(1);
        setStatuses((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]));
    }

    function toggleSort(nextKey: NonNullable<AssistantRunsQuery["sortKey"]>) {
        setPage(1);
        setSortKey(nextKey);
        setSortDir((prev) => (sortKey === nextKey ? (prev === "asc" ? "desc" : "asc") : "desc"));
    }

    async function parseContext() {
        const raw = execContext.trim();
        if (!raw.length) return {};
        const parsed = JSON.parse(raw) as unknown;
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed as Record<string, unknown>;
        throw new Error("Context must be a JSON object.");
    }

    async function handlePlan() {
        setExecError(null);
        if (!execActionType.trim()) return setExecError("Select an action type.");
        if (!execLeadId) return setExecError("Select a lead.");
        try {
            const context = await parseContext();
            captureMessage("assistant.actions.plan.start", { actionType: execActionType, leadId: execLeadId });
            const res = await planM.mutateAsync({ actionType: execActionType, source: execSource, leadId: execLeadId, context });
            setPlanResult(res);
            setPlanOpen(true);
        } catch (e) {
            setExecError(e instanceof Error ? e.message : "Planning failed");
            captureException(e, { area: "assistant.plan" });
        }
    }

    async function handleExecute() {
        setExecError(null);
        if (!execActionType.trim()) return setExecError("Select an action type.");
        if (!execLeadId) return setExecError("Select a lead.");
        try {
            const context = await parseContext();
            captureMessage("assistant.actions.execute.start", { actionType: execActionType, leadId: execLeadId });
            await executeM.mutateAsync({ actionType: execActionType, source: execSource, leadId: execLeadId, context });
        } catch (e) {
            setExecError(e instanceof Error ? e.message : "Execution failed");
            captureException(e, { area: "assistant.execute" });
        }
    }

    const canExecute = Boolean(execActionType.trim()) && Boolean(execLeadId) && !executeM.isPending;
    const canPlan = Boolean(execActionType.trim()) && Boolean(execLeadId) && !planM.isPending;
    const forbidden = user && user.role !== "admin";

    return (
        <DashboardLayout title="Assistant Actions" description="Audit, plan, and execute assistant actions.">
            <div className="mx-auto w-full max-w-6xl space-y-6">
                {forbidden ? (
                    <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-6 text-sm text-red-700">
                        You do not have permission to view Assistant Actions. Required role: admin.
                    </div>
                ) : (
                    <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1.6fr_1fr]">
                        <div className="space-y-6 min-w-0">
                            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                                <div className="flex items-center gap-2" role="tablist" aria-label="Assistant actions sections">
                                    {(["Audit Log", "Catalog", "Verification"] as const).map((t) => (
                                        <button
                                            key={t}
                                            type="button"
                                            onClick={() => setTab(t)}
                                            className={cn(
                                                "rounded-xl border px-3 py-2 text-sm font-semibold transition-colors",
                                                tab === t ? "border-gray-900 bg-gray-900 text-white" : "border-gray-200 bg-white text-gray-900 hover:bg-gray-50"
                                            )}
                                            role="tab"
                                            aria-selected={tab === t}
                                            tabIndex={tab === t ? 0 : -1}
                                        >
                                            {t}
                                        </button>
                                    ))}
                                </div>

                                {tab === "Audit Log" ? (
                                    <div className="flex items-center gap-2">
                                        <Select value={viewMode} onChange={(v) => setViewMode(v as ViewMode)} ariaLabel="Choose view mode" className="w-40">
                                            <option value="table">Table</option>
                                            <option value="timeline">Timeline</option>
                                        </Select>
                                        <Select
                                            value={String(pageSize)}
                                            onChange={(v) => {
                                                setPage(1);
                                                setPageSize(Number(v));
                                            }}
                                            ariaLabel="Choose page size"
                                            className="w-40"
                                        >
                                            <option value="25">25 / page</option>
                                            <option value="50">50 / page</option>
                                            <option value="100">100 / page</option>
                                        </Select>
                                    </div>
                                ) : null}
                            </div>

                            {tab === "Audit Log" ? (
                                <div className="rounded-2xl border border-gray-200 bg-white p-4 space-y-5">
                                    <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
                                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                                            <div className="space-y-2">
                                                <Label>Status</Label>
                                                <div className="flex flex-wrap gap-2">
                                                    {(["pending", "in_progress", "completed", "failed"] as const).map((s) => {
                                                        const active = statuses.includes(s);
                                                        return (
                                                            <button
                                                                key={s}
                                                                type="button"
                                                                onClick={() => toggleStatus(s)}
                                                                aria-pressed={active}
                                                                className={cn(
                                                                    "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold",
                                                                    active ? "border-gray-900 bg-gray-900 text-white" : "border-gray-200 bg-white text-gray-900 hover:bg-gray-50"
                                                                )}
                                                            >
                                                                {STATUS_LABELS[s]}
                                                            </button>
                                                        );
                                                    })}
                                                </div>
                                            </div>

                                            <div className="space-y-2">
                                                <Label>Action type</Label>
                                                <Select
                                                    value={actionType}
                                                    onChange={(v) => {
                                                        setPage(1);
                                                        setActionType(v);
                                                    }}
                                                    ariaLabel="Filter by action type"
                                                >
                                                    <option value="">All</option>
                                                    {categorizedActionTypes.map((g) => (
                                                        <optgroup key={g.cat} label={g.cat}>
                                                            {g.types.map((t) => (
                                                                <option key={t} value={t}>
                                                                    {t}
                                                                </option>
                                                            ))}
                                                        </optgroup>
                                                    ))}
                                                </Select>
                                            </div>

                                            <div className="space-y-2">
                                                <Label>Date range</Label>
                                                <Select
                                                    value={preset}
                                                    onChange={(v) => {
                                                        setPage(1);
                                                        setPreset(v as DatePreset);
                                                    }}
                                                    ariaLabel="Filter by date range"
                                                >
                                                    <option value="today">Today</option>
                                                    <option value="last7">Last 7 days</option>
                                                    <option value="custom">Custom</option>
                                                </Select>
                                                {preset === "custom" ? (
                                                    <div className="grid grid-cols-2 gap-2">
                                                        <Input
                                                            type="date"
                                                            value={fromDate}
                                                            onChange={(e) => {
                                                                setPage(1);
                                                                setFromDate(e.target.value);
                                                            }}
                                                            aria-label="Start date"
                                                        />
                                                        <Input
                                                            type="date"
                                                            value={toDate}
                                                            onChange={(e) => {
                                                                setPage(1);
                                                                setToDate(e.target.value);
                                                            }}
                                                            aria-label="End date"
                                                        />
                                                    </div>
                                                ) : null}
                                            </div>

                                            <div className="space-y-2">
                                                <Label>Lead</Label>
                                                <LeadTypeahead
                                                    value={leadFilterLabel}
                                                    onChange={(next) => {
                                                        setPage(1);
                                                        setLeadFilterId(next.leadId);
                                                        setLeadFilterLabel(next.leadLabel);
                                                    }}
                                                    options={leadOptions}
                                                    loading={leadsLoading}
                                                    error={leadsError}
                                                    ariaLabel="Search and select a lead to filter"
                                                    placeholder="Search contacts…"
                                                />
                                                {leadFilterId ? (
                                                    <button
                                                        type="button"
                                                        className="text-xs text-gray-500 hover:underline"
                                                        onClick={() => {
                                                            setPage(1);
                                                            setLeadFilterId(undefined);
                                                            setLeadFilterLabel("");
                                                        }}
                                                    >
                                                        Clear lead filter
                                                    </button>
                                                ) : null}
                                            </div>
                                        </div>

                                        <div className="flex flex-wrap items-center gap-2">
                                            <Button
                                                variant="secondary"
                                                onClick={() => {
                                                    downloadFile(`assistant-runs.page-${page}.json`, new Blob([JSON.stringify(runsItems, null, 2)], { type: "application/json" }));
                                                }}
                                                disabled={runsItems.length === 0}
                                            >
                                                <FileJson aria-hidden />
                                                Export JSON
                                            </Button>
                                            <Button
                                                variant="secondary"
                                                onClick={() => {
                                                    downloadFile(`assistant-runs.page-${page}.csv`, new Blob([runsToCsv(runsItems)], { type: "text/csv" }));
                                                }}
                                                disabled={runsItems.length === 0}
                                            >
                                                <Download aria-hidden />
                                                Export CSV
                                            </Button>
                                        </div>
                                    </div>

                                    <div className="text-xs text-gray-500">Audit records are retained for at least 90 days.</div>

                                    {runsQ.isLoading ? (
                                        <div className="flex items-center justify-center h-56">
                                            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-gray-500" />
                                        </div>
                                    ) : runsQ.isError ? (
                                        <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-700">
                                            {formatError(runsQ.error)}
                                        </div>
                                    ) : runsItems.length === 0 ? (
                                        <div className="rounded-2xl border border-gray-200 bg-gray-50 p-6 text-sm text-gray-600">No assistant runs match the current filters.</div>
                                    ) : viewMode === "timeline" ? (
                                        <div className="space-y-3">
                                            {runsItems.map((r) => (
                                                <button
                                                    key={r.id}
                                                    type="button"
                                                    className={cn(
                                                        "w-full rounded-2xl border bg-white p-4 text-left transition-colors hover:bg-gray-50",
                                                        r.status === "failed" ? "border-red-200" : "border-gray-200"
                                                    )}
                                                    onClick={() => {
                                                        setDetailsRun(r);
                                                        setDetailsOpen(true);
                                                    }}
                                                >
                                                    <div className="flex items-start justify-between gap-3">
                                                        <div className="min-w-0">
                                                            <div className="flex items-center gap-2">
                                                                {statusIcon(r.status)}
                                                                <div className="text-sm font-semibold text-gray-900 truncate">{r.actionType}</div>
                                                                <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold", statusBadgeClass(r.status))}>
                                                                    {STATUS_LABELS[r.status]}
                                                                </span>
                                                            </div>
                                                            <div className="mt-1 text-xs text-gray-500">
                                                                {r.source} • Lead {r.leadId ?? "—"} • Created {formatDateTime(r.createdAt)}
                                                            </div>
                                                            {r.result ? <div className="mt-2 text-sm text-gray-700">{r.result}</div> : null}
                                                        </div>
                                                        <div className="text-xs text-gray-500 text-right">
                                                            <div>Started: {formatDateTime(r.startedAt)}</div>
                                                            <div>Completed: {formatDateTime(r.completedAt)}</div>
                                                        </div>
                                                    </div>
                                                </button>
                                            ))}
                                        </div>
                                    ) : (
                                        <div className="overflow-x-auto rounded-2xl border border-gray-200">
                                            <table className="min-w-[920px] w-full">
                                                <thead className="bg-gray-50">
                                                    <tr className="text-left text-xs font-semibold text-gray-700">
                                                        <th className="px-4 py-3">
                                                            <button type="button" className="hover:underline" onClick={() => toggleSort("actionType")} aria-label="Sort by action type">
                                                                Action type
                                                            </button>
                                                        </th>
                                                        <th className="px-4 py-3">
                                                            <button type="button" className="hover:underline" onClick={() => toggleSort("source")} aria-label="Sort by source">
                                                                Source
                                                            </button>
                                                        </th>
                                                        <th className="px-4 py-3">
                                                            <button type="button" className="hover:underline" onClick={() => toggleSort("leadId")} aria-label="Sort by lead id">
                                                                Lead
                                                            </button>
                                                        </th>
                                                        <th className="px-4 py-3">
                                                            <button type="button" className="hover:underline" onClick={() => toggleSort("status")} aria-label="Sort by status">
                                                                Status
                                                            </button>
                                                        </th>
                                                        <th className="px-4 py-3">
                                                            <button type="button" className="hover:underline" onClick={() => toggleSort("createdAt")} aria-label="Sort by created time">
                                                                Created
                                                            </button>
                                                        </th>
                                                        <th className="px-4 py-3">
                                                            <button type="button" className="hover:underline" onClick={() => toggleSort("startedAt")} aria-label="Sort by started time">
                                                                Started
                                                            </button>
                                                        </th>
                                                        <th className="px-4 py-3">
                                                            <button type="button" className="hover:underline" onClick={() => toggleSort("completedAt")} aria-label="Sort by completed time">
                                                                Completed
                                                            </button>
                                                        </th>
                                                        <th className="px-4 py-3">Result</th>
                                                    </tr>
                                                </thead>
                                                <tbody className="divide-y divide-gray-200">
                                                    {runsItems.map((r) => (
                                                        <tr
                                                            key={r.id}
                                                            className={cn("hover:bg-gray-50 transition-colors cursor-pointer", r.status === "failed" ? "bg-red-50/50" : undefined)}
                                                            onClick={() => {
                                                                setDetailsRun(r);
                                                                setDetailsOpen(true);
                                                            }}
                                                        >
                                                            <td className="px-4 py-3 text-sm text-gray-900">
                                                                <div className="flex items-center gap-2 min-w-0">
                                                                    {statusIcon(r.status)}
                                                                    <span className="truncate">{r.actionType}</span>
                                                                </div>
                                                            </td>
                                                            <td className="px-4 py-3 text-sm text-gray-700">{r.source}</td>
                                                            <td className="px-4 py-3 text-sm text-gray-700">{r.leadId ?? "—"}</td>
                                                            <td className="px-4 py-3 text-sm">
                                                                <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold", statusBadgeClass(r.status))}>
                                                                    {STATUS_LABELS[r.status]}
                                                                </span>
                                                            </td>
                                                            <td className="px-4 py-3 text-sm text-gray-700">{formatDateTime(r.createdAt)}</td>
                                                            <td className="px-4 py-3 text-sm text-gray-700">{formatDateTime(r.startedAt)}</td>
                                                            <td className="px-4 py-3 text-sm text-gray-700">{formatDateTime(r.completedAt)}</td>
                                                            <td className="px-4 py-3 text-sm text-gray-700 max-w-[320px] truncate">{r.result ?? "—"}</td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    )}

                                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                                        <div className="text-xs text-gray-500">
                                            Page {page} {typeof runsTotal === "number" ? `of ${pageCount} (${runsTotal} total)` : ""}
                                        </div>
                                        <div className="flex items-center gap-2">
                                            <Button variant="secondary" onClick={() => setPage(1)} disabled={page <= 1}>
                                                First
                                            </Button>
                                            <Button variant="secondary" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1}>
                                                Prev
                                            </Button>
                                            <Button variant="secondary" onClick={() => setPage((p) => p + 1)} disabled={typeof runsTotal === "number" ? page >= pageCount : runsItems.length < pageSize}>
                                                Next
                                            </Button>
                                        </div>
                                    </div>
                                </div>
                            ) : null}

                            {tab === "Catalog" ? (
                                <div className="rounded-2xl border border-gray-200 bg-white p-4">
                                    {actionsQ.isLoading ? (
                                        <div className="flex items-center justify-center h-56">
                                            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-gray-500" />
                                        </div>
                                    ) : actionsQ.isError ? (
                                        <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-700">
                                            {formatError(actionsQ.error)}
                                        </div>
                                    ) : actions.length === 0 ? (
                                        <div className="rounded-2xl border border-gray-200 bg-gray-50 p-6 text-sm text-gray-600">No actions configured.</div>
                                    ) : (
                                        <div className="space-y-2">
                                            {actions.map((a) => (
                                                <div key={a.id} className="rounded-2xl border border-gray-200 bg-white p-4">
                                                    <div className="text-sm font-semibold text-gray-900">{a.name}</div>
                                                    <div className="mt-1 text-sm text-gray-600">{a.description}</div>
                                                    <div className="mt-2 text-xs text-gray-500">Type: {normalizeActionType(a)}</div>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            ) : null}

                            {tab === "Verification" ? (
                                <div className="rounded-2xl border border-gray-200 bg-white p-4 space-y-4">
                                    <div className="text-sm font-semibold text-gray-900">Definition of Done checklist</div>
                                    <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                                        {Object.entries(verification).map(([k, v]) => (
                                            <label key={k} className="flex items-center gap-3 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-800">
                                                <input type="checkbox" checked={v} onChange={(e) => setVerification((prev) => ({ ...prev, [k]: e.target.checked }))} aria-label={k} />
                                                <span>{k}</span>
                                            </label>
                                        ))}
                                    </div>
                                </div>
                            ) : null}
                        </div>

                        <div className="space-y-6">
                            <div className="rounded-2xl border border-gray-200 bg-white p-5 space-y-4">
                                <div className="flex items-start justify-between gap-3">
                                    <div>
                                        <div className="text-sm font-semibold text-gray-900">Execute action</div>
                                        <div className="mt-1 text-sm text-gray-600">Start an assistant action and write to the audit log immediately.</div>
                                    </div>
                                    {(executeM.isPending || planM.isPending) && <Loader2 className="h-5 w-5 animate-spin text-gray-500" aria-hidden />}
                                </div>

                                {execError ? <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-700">{execError}</div> : null}

                                <div className="space-y-2">
                                    <Label>Action type</Label>
                                    <Select value={execActionType} onChange={setExecActionType} ariaLabel="Select action type">
                                        <option value="">Select…</option>
                                        {categorizedActionTypes.map((g) => (
                                            <optgroup key={g.cat} label={g.cat}>
                                                {g.types.map((t) => (
                                                    <option key={t} value={t}>
                                                        {t}
                                                    </option>
                                                ))}
                                            </optgroup>
                                        ))}
                                    </Select>
                                </div>

                                <div className="space-y-2">
                                    <Label>Source</Label>
                                    <Input value={execSource} onChange={(e) => setExecSource(e.target.value)} aria-label="Action source" />
                                </div>

                                <div className="space-y-2">
                                    <Label>Lead / context</Label>
                                    <LeadTypeahead
                                        value={execLeadLabel}
                                        onChange={(next) => {
                                            setExecLeadId(next.leadId);
                                            setExecLeadLabel(next.leadLabel);
                                        }}
                                        options={leadOptions}
                                        loading={leadsLoading}
                                        error={leadsError}
                                        ariaLabel="Select lead"
                                        placeholder="Search contacts…"
                                    />
                                </div>

                                <div className="space-y-2">
                                    <Label>Context (JSON)</Label>
                                    <textarea
                                        value={execContext}
                                        onChange={(e) => setExecContext(e.target.value)}
                                        className="min-h-[120px] w-full resize-y rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-400 focus-visible:ring-offset-2"
                                        aria-label="Action context JSON"
                                    />
                                </div>

                                <div className="flex flex-col gap-2 sm:flex-row">
                                    <Button variant="secondary" onClick={handlePlan} disabled={!canPlan}>
                                        <Play aria-hidden />
                                        Plan Action
                                    </Button>
                                    <Button onClick={handleExecute} disabled={!canExecute}>
                                        <Play aria-hidden />
                                        Execute
                                    </Button>
                                </div>
                            </div>
                        </div>
                    </div>
                )}
            </div>

            <Modal
                open={detailsOpen}
                onOpenChange={(v) => {
                    setDetailsOpen(v);
                    if (!v) setDetailsRun(null);
                }}
                title="Assistant run details"
                description={detailsRun ? `${detailsRun.actionType} • ${STATUS_LABELS[detailsRun.status]}` : undefined}
                size="xl"
            >
                {detailsRun ? (
                    <div className="space-y-5">
                        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                                <div className="text-sm font-semibold text-white">Summary</div>
                                <div className="mt-3 space-y-2 text-sm text-gray-200">
                                    <div className="flex items-center justify-between gap-3">
                                        <span className="text-gray-300">Status</span>
                                        <span className={cn("inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-xs font-semibold", statusBadgeClass(detailsRun.status))}>
                                            {statusIcon(detailsRun.status)}
                                            {STATUS_LABELS[detailsRun.status]}
                                        </span>
                                    </div>
                                    <div className="flex items-center justify-between gap-3">
                                        <span className="text-gray-300">Source</span>
                                        <span className="text-gray-100">{detailsRun.source}</span>
                                    </div>
                                    <div className="flex items-center justify-between gap-3">
                                        <span className="text-gray-300">Lead</span>
                                        <span className="text-gray-100">{detailsRun.leadId ?? "—"}</span>
                                    </div>
                                    <div className="flex items-center justify-between gap-3">
                                        <span className="text-gray-300">Created</span>
                                        <span className="text-gray-100">{formatDateTime(detailsRun.createdAt)}</span>
                                    </div>
                                    <div className="flex items-center justify-between gap-3">
                                        <span className="text-gray-300">Started</span>
                                        <span className="text-gray-100">{formatDateTime(detailsRun.startedAt)}</span>
                                    </div>
                                    <div className="flex items-center justify-between gap-3">
                                        <span className="text-gray-300">Completed</span>
                                        <span className="text-gray-100">{formatDateTime(detailsRun.completedAt)}</span>
                                    </div>
                                </div>
                            </div>

                            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                                <div className="text-sm font-semibold text-white">Outcome</div>
                                <div className="mt-3 text-sm text-gray-200">{detailsRun.result ?? "—"}</div>
                                {detailsRun.status === "failed" ? (
                                    <div className="mt-4 rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">
                                        <div className="flex items-center gap-2 font-semibold">
                                            <AlertCircle className="h-4 w-4" aria-hidden />
                                            Failed
                                        </div>
                                        <div className="mt-2 space-y-2">
                                            {(() => {
                                                const g = guidanceForError(detailsRun.error);
                                                return (
                                                    <>
                                                        <div>
                                                            <div className="text-xs uppercase tracking-wide text-red-200/80">Root cause</div>
                                                            <div className="mt-1">{g.rootCause}</div>
                                                        </div>
                                                        <div>
                                                            <div className="text-xs uppercase tracking-wide text-red-200/80">Recommended next steps</div>
                                                            <ul className="mt-1 list-disc pl-5">
                                                                {g.nextSteps.map((s) => (
                                                                    <li key={s}>{s}</li>
                                                                ))}
                                                            </ul>
                                                        </div>
                                                        <div className="flex flex-wrap items-center gap-2">
                                                            {g.retryable ? (
                                                                <Button
                                                                    variant="secondary"
                                                                    onClick={() => retryM.mutate(detailsRun.id)}
                                                                    disabled={retryM.isPending}
                                                                >
                                                                    <RefreshCw aria-hidden />
                                                                    Retry
                                                                </Button>
                                                            ) : null}
                                                            <a href={g.docsUrl} target="_blank" rel="noreferrer" className="text-xs text-gray-200 underline">
                                                                Documentation
                                                            </a>
                                                        </div>
                                                    </>
                                                );
                                            })()}
                                        </div>
                                    </div>
                                ) : null}
                            </div>
                        </div>

                        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                                <div className="text-sm font-semibold text-white">Request payload</div>
                                <pre className="mt-3 overflow-auto rounded-xl border border-white/10 bg-black/30 p-3 text-xs text-gray-200">
                                    {JSON.stringify(detailsRun.requestPayload ?? null, null, 2)}
                                </pre>
                            </div>
                            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                                <div className="text-sm font-semibold text-white">Response payload</div>
                                <pre className="mt-3 overflow-auto rounded-xl border border-white/10 bg-black/30 p-3 text-xs text-gray-200">
                                    {JSON.stringify(detailsRun.responsePayload ?? null, null, 2)}
                                </pre>
                            </div>
                        </div>

                        {detailsRun.error !== undefined ? (
                            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                                <div className="text-sm font-semibold text-white">Error</div>
                                <pre className="mt-3 overflow-auto rounded-xl border border-white/10 bg-black/30 p-3 text-xs text-gray-200">
                                    {JSON.stringify(detailsRun.error, null, 2)}
                                </pre>
                            </div>
                        ) : null}
                    </div>
                ) : null}
            </Modal>

            <Modal
                open={planOpen}
                onOpenChange={(v) => {
                    setPlanOpen(v);
                    if (!v) setPlanResult(null);
                }}
                title="Plan preview"
                description={execActionType ? `Planned steps for ${execActionType}` : undefined}
                size="lg"
                footer={
                    <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
                        <Button variant="secondary" onClick={() => setPlanOpen(false)}>
                            Close
                        </Button>
                        <Button onClick={() => setPlanConfirmOpen(true)} disabled={!planResult}>
                            Execute planned action
                        </Button>
                    </div>
                }
            >
                {planResult ? (
                    <div className="space-y-4">
                        {planResult.summary ? <div className="text-sm text-gray-200">{planResult.summary}</div> : null}
                        {Array.isArray(planResult.steps) && planResult.steps.length > 0 ? (
                            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                                <div className="text-sm font-semibold text-white">Planned steps</div>
                                <div className="mt-3 space-y-2">
                                    {planResult.steps.slice(0, 20).map((s, idx) => (
                                        <div key={idx} className="rounded-xl border border-white/10 bg-black/20 px-3 py-2 text-sm text-gray-200">
                                            {typeof s === "string" ? s : JSON.stringify(s)}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        ) : (
                            <div className="text-sm text-gray-300">No structured steps returned by the API.</div>
                        )}
                        {planResult.estimatedImpact !== undefined ? (
                            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                                <div className="text-sm font-semibold text-white">Estimated impact</div>
                                <pre className="mt-3 overflow-auto rounded-xl border border-white/10 bg-black/30 p-3 text-xs text-gray-200">
                                    {JSON.stringify(planResult.estimatedImpact, null, 2)}
                                </pre>
                            </div>
                        ) : null}
                        <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                            <div className="text-sm font-semibold text-white">Raw plan payload</div>
                            <pre className="mt-3 overflow-auto rounded-xl border border-white/10 bg-black/30 p-3 text-xs text-gray-200">
                                {JSON.stringify(planResult, null, 2)}
                            </pre>
                        </div>
                    </div>
                ) : (
                    <div className="text-sm text-gray-300">No plan available.</div>
                )}
            </Modal>

            <Modal
                open={planConfirmOpen}
                onOpenChange={setPlanConfirmOpen}
                title="Confirm execution"
                description="Execute this action now?"
                size="sm"
                footer={
                    <div className="flex items-center justify-end gap-2">
                        <Button variant="secondary" onClick={() => setPlanConfirmOpen(false)}>
                            Cancel
                        </Button>
                        <Button
                            onClick={async () => {
                                setPlanConfirmOpen(false);
                                setPlanOpen(false);
                                await handleExecute();
                            }}
                            disabled={!canExecute}
                        >
                            Execute
                        </Button>
                    </div>
                }
            >
                <div className="text-sm text-gray-200">
                    Action type: <span className="font-semibold text-white">{execActionType || "—"}</span>
                </div>
                <div className="mt-2 text-sm text-gray-200">
                    Lead: <span className="font-semibold text-white">{execLeadId || "—"}</span>
                </div>
            </Modal>
        </DashboardLayout>
    );
}
