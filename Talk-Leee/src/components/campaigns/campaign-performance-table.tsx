"use client";

import Link from "next/link";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import type { Campaign } from "@/lib/dashboard-api";
import {
    applyCampaignFilters,
    applyCampaignSort,
    CampaignFilters,
    CampaignSortKey,
    CampaignSortSpec,
    CampaignStatus,
    SortDir,
    campaignProgressPct,
    campaignSuccessRatePct,
    formatPct,
    normalizeCampaignStatus,
    paginate,
    progressColorClass,
    RowsPerPage,
    statusBadgeClass,
} from "@/lib/campaign-performance";
import { cn } from "@/lib/utils";
import { ChevronDown, ChevronRight, Copy, Ellipsis, Pause, Play, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { createPortal } from "react-dom";

type ColumnDef = { key: CampaignSortKey; label: string; numeric?: boolean };

const COLUMNS: ColumnDef[] = [
    { key: "name", label: "Campaign" },
    { key: "status", label: "Status" },
    { key: "progress", label: "Completion", numeric: true },
    { key: "successRate", label: "Success Rate", numeric: true },
    { key: "leads", label: "Leads", numeric: true },
    { key: "completed", label: "Completed", numeric: true },
    { key: "failed", label: "Failed", numeric: true },
];

const TABLE_GRID_COLS =
    "grid-cols-[36px_minmax(0,1.5fr)_minmax(0,1fr)] sm:grid-cols-[36px_minmax(0,1.6fr)_110px_170px_120px_90px_90px_90px_44px]";

const ALL_STATUSES: CampaignStatus[] = ["Active", "Paused", "Completed", "Draft", "Failed"];

type ExportPreset = "All Time" | "Last 7 Days" | "Last 30 Days" | "This Month" | "Custom";
type ExportColumnKey =
    | "id"
    | "name"
    | "status"
    | "total_leads"
    | "calls_completed"
    | "calls_failed"
    | "progress_pct"
    | "success_rate_pct"
    | "created_at";

const EXPORT_PRESETS: ExportPreset[] = ["All Time", "Last 7 Days", "Last 30 Days", "This Month", "Custom"];

function isExportPreset(value: unknown): value is ExportPreset {
    return typeof value === "string" && (EXPORT_PRESETS as string[]).includes(value);
}

type ExportPrefs = {
    preset?: ExportPreset;
    from?: string;
    to?: string;
    cols?: ExportColumnKey[];
};

const EXPORT_COLUMNS: Array<{ key: ExportColumnKey; label: string }> = [
    { key: "id", label: "ID" },
    { key: "name", label: "Campaign" },
    { key: "status", label: "Status" },
    { key: "total_leads", label: "Leads" },
    { key: "calls_completed", label: "Completed" },
    { key: "calls_failed", label: "Failed" },
    { key: "progress_pct", label: "Completion %" },
    { key: "success_rate_pct", label: "Success Rate %" },
    { key: "created_at", label: "Created At" },
];

function computeExportRange(preset: ExportPreset, fromISO: string, toISO: string) {
    const now = new Date();
    const startOfToday = new Date(now);
    startOfToday.setHours(0, 0, 0, 0);

    if (preset === "All Time") return { from: null as number | null, to: null as number | null, label: "All time" };
    if (preset === "Last 7 Days") {
        const from = new Date(startOfToday);
        from.setDate(from.getDate() - 7);
        return { from: from.getTime(), to: now.getTime(), label: "Last 7 days" };
    }
    if (preset === "Last 30 Days") {
        const from = new Date(startOfToday);
        from.setDate(from.getDate() - 30);
        return { from: from.getTime(), to: now.getTime(), label: "Last 30 days" };
    }
    if (preset === "This Month") {
        const from = new Date(now.getFullYear(), now.getMonth(), 1);
        return { from: from.getTime(), to: now.getTime(), label: "This month" };
    }
    const from = fromISO ? new Date(`${fromISO}T00:00:00`).getTime() : NaN;
    const to = toISO ? new Date(`${toISO}T23:59:59`).getTime() : NaN;
    const safeFrom = Number.isFinite(from) ? from : null;
    const safeTo = Number.isFinite(to) ? to : null;
    const label =
        safeFrom && safeTo
            ? `${fromISO} → ${toISO}`
            : safeFrom
                ? `Since ${fromISO}`
                : safeTo
                    ? `Until ${toISO}`
                    : "Custom";
    return { from: safeFrom, to: safeTo, label };
}

function applyDateRangeByCreatedAt(items: Campaign[], range: { from: number | null; to: number | null }) {
    if (!range.from && !range.to) return items;
    const from = range.from ?? Number.NEGATIVE_INFINITY;
    const to = range.to ?? Number.POSITIVE_INFINITY;
    return items.filter((c) => {
        const t = new Date(c.created_at).getTime();
        if (!Number.isFinite(t)) return false;
        return t >= from && t <= to;
    });
}

function campaignsToCsvWithColumns(campaigns: Campaign[], columns: ExportColumnKey[]) {
    const headers = columns.join(",");
    const lines = [headers];
    for (const c of campaigns) {
        const row = columns.map((col) => {
            if (col === "id") return csvEscape(c.id);
            if (col === "name") return csvEscape(c.name);
            if (col === "status") return csvEscape(normalizeCampaignStatus(c.status));
            if (col === "total_leads") return String(c.total_leads ?? 0);
            if (col === "calls_completed") return String(c.calls_completed ?? 0);
            if (col === "calls_failed") return String(c.calls_failed ?? 0);
            if (col === "progress_pct") return campaignProgressPct(c).toFixed(1);
            if (col === "success_rate_pct") return campaignSuccessRatePct(c).toFixed(1);
            if (col === "created_at") return csvEscape(c.created_at);
            return "";
        });
        lines.push(row.join(","));
    }
    return lines.join("\n");
}

function campaignsToJsonWithColumns(campaigns: Campaign[], columns: ExportColumnKey[], meta: { rangeLabel: string }) {
    const items = campaigns.map((c) => {
        const out: Partial<Record<ExportColumnKey, string | number>> = {};
        for (const col of columns) {
            if (col === "id") out[col] = c.id;
            else if (col === "name") out[col] = c.name;
            else if (col === "status") out[col] = normalizeCampaignStatus(c.status);
            else if (col === "total_leads") out[col] = Number(c.total_leads ?? 0);
            else if (col === "calls_completed") out[col] = Number(c.calls_completed ?? 0);
            else if (col === "calls_failed") out[col] = Number(c.calls_failed ?? 0);
            else if (col === "progress_pct") out[col] = Number(campaignProgressPct(c).toFixed(1));
            else if (col === "success_rate_pct") out[col] = Number(campaignSuccessRatePct(c).toFixed(1));
            else if (col === "created_at") out[col] = c.created_at;
        }
        return out;
    });
    return JSON.stringify({ generatedAt: new Date().toISOString(), range: meta.rangeLabel, items }, null, 2);
}

function toggleSort(prev: CampaignSortSpec[], key: CampaignSortKey, multi: boolean): CampaignSortSpec[] {
    const idx = prev.findIndex((s) => s.key === key);
    const next: CampaignSortSpec[] = multi ? [...prev] : [];
    if (idx === -1) {
        next.push({ key, dir: "asc" });
        return next;
    }
    const current = prev[idx];
    const dir: SortDir = current.dir === "asc" ? "desc" : "asc";
    if (!multi) return [{ key, dir }];
    next[idx] = { ...current, dir };
    return next;
}

function sortIndicator(spec: CampaignSortSpec[] | undefined, key: CampaignSortKey) {
    const s = (spec || []).find((x) => x.key === key);
    if (!s) return "↕";
    return s.dir === "asc" ? "↑" : "↓";
}

function downloadBlob(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

function printCampaignsWithColumns(campaigns: Campaign[], columns: ExportColumnKey[], rangeLabel: string) {
    const w = window.open("", "_blank", "noopener,noreferrer");
    if (!w) return;
    const headCells = columns.map((c) => `<th>${escapeHtml(c)}</th>`).join("");
    const rows = campaigns
        .map((c) => {
            const cells = columns
                .map((col) => {
                    if (col === "id") return `<td>${escapeHtml(c.id)}</td>`;
                    if (col === "name") return `<td>${escapeHtml(c.name)}</td>`;
                    if (col === "status") return `<td>${escapeHtml(normalizeCampaignStatus(c.status))}</td>`;
                    if (col === "total_leads") return `<td style="text-align:right">${Number(c.total_leads || 0).toLocaleString()}</td>`;
                    if (col === "calls_completed") return `<td style="text-align:right">${Number(c.calls_completed || 0).toLocaleString()}</td>`;
                    if (col === "calls_failed") return `<td style="text-align:right">${Number(c.calls_failed || 0).toLocaleString()}</td>`;
                    if (col === "progress_pct") return `<td style="text-align:right">${campaignProgressPct(c).toFixed(1)}%</td>`;
                    if (col === "success_rate_pct") return `<td style="text-align:right">${campaignSuccessRatePct(c).toFixed(1)}%</td>`;
                    if (col === "created_at") return `<td>${escapeHtml(new Date(c.created_at).toLocaleString())}</td>`;
                    return `<td></td>`;
                })
                .join("");
            return `<tr>${cells}</tr>`;
        })
        .join("");
    w.document.write(`<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Campaign Export</title>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;margin:20px;}
    h1{font-size:16px;margin:0 0 12px;}
    table{width:100%;border-collapse:collapse;}
    th,td{border:1px solid #ddd;padding:8px;font-size:12px;}
    th{text-align:left;background:#f5f5f5;}
  </style>
</head>
<body>
  <h1>Campaign Export (${escapeHtml(rangeLabel)})</h1>
  <table>
    <thead>
      <tr>${headCells}</tr>
    </thead>
    <tbody>${rows}</tbody>
  </table>
</body>
</html>`);
    w.document.close();
    w.focus();
    w.print();
}

function escapeHtml(value: string) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function csvEscape(value: string) {
    const v = String(value ?? "");
    const needs = v.includes(",") || v.includes('"') || v.includes("\n") || v.includes("\r");
    if (!needs) return v;
    return `"${v.replaceAll('"', '""')}"`;
}

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

export function CampaignPerformanceTable({
    campaigns,
    loading,
    error,
    onPause,
    onResume,
    onDelete,
    onDuplicate,
    onUpdate,
}: {
    campaigns: Campaign[];
    loading: boolean;
    error: string;
    onPause: (id: string) => Promise<void>;
    onResume: (id: string) => Promise<void>;
    onDelete: (id: string) => Promise<void>;
    onDuplicate: (id: string) => Promise<void>;
    onUpdate: (next: Campaign) => Promise<void>;
}) {
    const router = useRouter();
    const [sort, setSort] = useState<CampaignSortSpec[]>([]);
    const [filters, setFilters] = useState<CampaignFilters>({ statuses: [], successMin: 0, successMax: 100, query: "" });
    const [rowsPerPage, setRowsPerPage] = useState<RowsPerPage>(25);
    const [page, setPage] = useState(1);
    const [statusOpen, setStatusOpen] = useState(false);
    const [statusPanelStyle, setStatusPanelStyle] = useState<CSSProperties | null>(null);
    const [suggestOpen, setSuggestOpen] = useState(false);
    const [menuOpenFor, setMenuOpenFor] = useState<string | null>(null);
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [expanded, setExpanded] = useState<Set<string>>(new Set());
    const [detailsId, setDetailsId] = useState<string | null>(null);
    const [editId, setEditId] = useState<string | null>(null);
    const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
    const [exportOpen, setExportOpen] = useState(false);
    const [exportPreset, setExportPreset] = useState<ExportPreset>("All Time");
    const [exportFrom, setExportFrom] = useState<string>("");
    const [exportTo, setExportTo] = useState<string>("");
    const [exportCols, setExportCols] = useState<Set<ExportColumnKey>>(
        new Set(["name", "status", "total_leads", "calls_completed", "calls_failed", "progress_pct", "success_rate_pct", "created_at"])
    );

    const statusButtonRef = useRef<HTMLButtonElement | null>(null);
    const statusPanelRef = useRef<HTMLDivElement | null>(null);
    const suggestRef = useRef<HTMLDivElement | null>(null);
    const menuRefs = useRef<Record<string, HTMLDivElement | null>>({});
    const tableScrollRef = useRef<HTMLDivElement | null>(null);
    const headerRef = useRef<HTMLDivElement | null>(null);

    useLayoutEffect(() => {
        if (!statusOpen) {
            setStatusPanelStyle(null);
            return;
        }

        const margin = 8;

        const update = () => {
            const btn = statusButtonRef.current;
            if (!btn) return;

            const rect = btn.getBoundingClientRect();
            const width = rect.width;

            let left = rect.left;
            left = Math.max(margin, Math.min(left, window.innerWidth - width - margin));

            let top = rect.bottom + margin;

            const panel = statusPanelRef.current;
            if (panel) {
                const h = panel.offsetHeight;
                if (top + h > window.innerHeight - margin) {
                    top = Math.max(margin, window.innerHeight - h - margin);
                }
            }

            setStatusPanelStyle({ left, top, width });
        };

        update();
        window.addEventListener("resize", update);
        window.addEventListener("scroll", update, true);
        return () => {
            window.removeEventListener("resize", update);
            window.removeEventListener("scroll", update, true);
        };
    }, [statusOpen]);

    useEffect(() => {
        const savedSort = fromLocalStorage<CampaignSortSpec[]>("campaigns.performance.sort", []);
        const savedFilters = fromLocalStorage<CampaignFilters>("campaigns.performance.filters", {
            statuses: [],
            successMin: 0,
            successMax: 100,
            query: "",
        });
        const savedRows = fromLocalStorage<RowsPerPage>("campaigns.performance.rowsPerPage", 25 as RowsPerPage);
        const savedPage = fromLocalStorage<number>("campaigns.performance.page", 1);
        const savedExport = fromLocalStorage<ExportPrefs | null>("campaigns.performance.exportPrefs", null);

        const raf = window.requestAnimationFrame(() => {
            setSort(Array.isArray(savedSort) ? savedSort : []);
            setFilters(savedFilters);
            setRowsPerPage(savedRows);
            setPage(savedPage);
            if (savedExport) {
                if (isExportPreset(savedExport.preset)) setExportPreset(savedExport.preset);
                if (typeof savedExport.from === "string") setExportFrom(savedExport.from);
                if (typeof savedExport.to === "string") setExportTo(savedExport.to);
                if (Array.isArray(savedExport.cols)) {
                    const allowed = new Set(EXPORT_COLUMNS.map((c) => c.key));
                    const nextCols = savedExport.cols.filter((c): c is ExportColumnKey => allowed.has(c));
                    setExportCols(new Set(nextCols));
                }
            }
        });

        return () => window.cancelAnimationFrame(raf);
    }, []);

    useEffect(() => toLocalStorage("campaigns.performance.sort", sort), [sort]);
    useEffect(() => toLocalStorage("campaigns.performance.filters", filters), [filters]);
    useEffect(() => toLocalStorage("campaigns.performance.rowsPerPage", rowsPerPage), [rowsPerPage]);
    useEffect(() => toLocalStorage("campaigns.performance.page", page), [page]);
    useEffect(() => {
        toLocalStorage("campaigns.performance.exportPrefs", {
            preset: exportPreset,
            from: exportFrom,
            to: exportTo,
            cols: Array.from(exportCols),
        });
    }, [exportCols, exportFrom, exportPreset, exportTo]);

    useEffect(() => {
        const onClick = (e: MouseEvent) => {
            const t = e.target as Node | null;
            if (!t) return;
            if (statusOpen) {
                const btn = statusButtonRef.current;
                const panel = statusPanelRef.current;
                if (btn && btn.contains(t)) return;
                if (panel && panel.contains(t)) return;
                setStatusOpen(false);
            }
            if (suggestOpen) {
                const box = suggestRef.current;
                if (box && box.contains(t)) return;
                setSuggestOpen(false);
            }
            if (menuOpenFor) {
                const menu = menuRefs.current[menuOpenFor];
                if (menu && menu.contains(t)) return;
                setMenuOpenFor(null);
            }
        };
        window.addEventListener("click", onClick);
        return () => window.removeEventListener("click", onClick);
    }, [menuOpenFor, statusOpen, suggestOpen]);

    useEffect(() => {
        const raf = window.requestAnimationFrame(() => {
            setPage(1);
            setSelected(new Set());
            setExpanded(new Set());
            if (tableScrollRef.current) tableScrollRef.current.scrollTop = 0;
        });
        return () => window.cancelAnimationFrame(raf);
    }, [filters, rowsPerPage, sort]);

    const nameSuggestions = useMemo(() => {
        const q = (filters.query || "").trim().toLowerCase();
        if (q.length < 2) return [];
        const names = campaigns
            .map((c) => c.name || "")
            .filter((n) => n.toLowerCase().includes(q))
            .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
        return Array.from(new Set(names)).slice(0, 6);
    }, [campaigns, filters.query]);

    const filtered = useMemo(() => applyCampaignFilters(campaigns, filters), [campaigns, filters]);
    const sorted = useMemo(() => applyCampaignSort(filtered, sort), [filtered, sort]);
    const paged = useMemo(() => paginate(sorted, page, rowsPerPage), [page, rowsPerPage, sorted]);

    const exportCandidates = useMemo(() => {
        if (selected.size === 0) return paged.slice;
        return sorted.filter((c) => selected.has(c.id));
    }, [paged.slice, selected, sorted]);

    const exportRange = useMemo(() => computeExportRange(exportPreset, exportFrom, exportTo), [exportFrom, exportPreset, exportTo]);
    const exportItems = useMemo(() => applyDateRangeByCreatedAt(exportCandidates, exportRange), [exportCandidates, exportRange]);
    const exportColumnList = useMemo<ExportColumnKey[]>(() => {
        const cols = EXPORT_COLUMNS.map((c) => c.key).filter((k): k is ExportColumnKey => exportCols.has(k));
        const fallback: ExportColumnKey[] = ["name", "status", "created_at"];
        return cols.length > 0 ? cols : fallback;
    }, [exportCols]);

    const bulkCount = selected.size;

    const toggleSelected = (id: string, next: boolean) => {
        setSelected((prev) => {
            const copy = new Set(prev);
            if (next) copy.add(id);
            else copy.delete(id);
            return copy;
        });
    };

    const toggleAllVisible = (next: boolean) => {
        setSelected((prev) => {
            const copy = new Set(prev);
            for (const c of paged.slice) {
                if (next) copy.add(c.id);
                else copy.delete(c.id);
            }
            return copy;
        });
    };

    const allVisibleSelected = paged.slice.length > 0 && paged.slice.every((c) => selected.has(c.id));

    const toggleExpanded = (id: string) => {
        setExpanded((prev) => {
            const copy = new Set(prev);
            if (copy.has(id)) copy.delete(id);
            else copy.add(id);
            return copy;
        });
    };

    const detailsCampaign = useMemo(() => campaigns.find((c) => c.id === detailsId) || null, [campaigns, detailsId]);
    const detailsStatus = useMemo(
        () => (detailsCampaign ? normalizeCampaignStatus(detailsCampaign.status) : null),
        [detailsCampaign]
    );
    const detailsCanPause = detailsStatus === "Active";
    const detailsCanResume = detailsStatus === "Paused" || detailsStatus === "Draft" || detailsStatus === "Failed";
    const editCampaign = useMemo(() => campaigns.find((c) => c.id === editId) || null, [campaigns, editId]);

    const [editDraft, setEditDraft] = useState<{ name: string; description: string; maxConcurrent: number; voiceId: string } | null>(null);

    useEffect(() => {
        const raf = window.requestAnimationFrame(() => {
            if (!editCampaign) {
                setEditDraft(null);
                return;
            }
            setEditDraft({
                name: editCampaign.name || "",
                description: editCampaign.description || "",
                maxConcurrent: editCampaign.max_concurrent_calls || 0,
                voiceId: editCampaign.voice_id || "",
            });
        });
        return () => window.cancelAnimationFrame(raf);
    }, [editCampaign]);

    const useVirtual = rowsPerPage === "All" && expanded.size === 0 && paged.slice.length > 50;
    const rowHeight = 56;
    const [scrollTop, setScrollTop] = useState(0);
    const [viewportH, setViewportH] = useState(520);
    const [headerH, setHeaderH] = useState(0);

    useEffect(() => {
        if (!useVirtual) return;
        const el = tableScrollRef.current;
        if (!el) return;
        const onScroll = () => setScrollTop(el.scrollTop);
        onScroll();
        el.addEventListener("scroll", onScroll, { passive: true });
        const ro = new ResizeObserver(() => {
            setViewportH(el.clientHeight);
            setHeaderH(headerRef.current?.offsetHeight ?? 0);
        });
        ro.observe(el);
        return () => {
            el.removeEventListener("scroll", onScroll);
            ro.disconnect();
        };
    }, [useVirtual]);

    const virtual = useMemo(() => {
        if (!useVirtual) return null;
        const total = paged.slice.length;
        const effectiveScrollTop = Math.max(0, scrollTop - headerH);
        const effectiveViewportH = Math.max(0, viewportH - headerH);
        const startIndex = Math.max(0, Math.floor(effectiveScrollTop / rowHeight) - 6);
        const endIndex = Math.min(total, startIndex + Math.ceil(effectiveViewportH / rowHeight) + 12);
        return { total, startIndex, endIndex, topPad: startIndex * rowHeight, bottomPad: (total - endIndex) * rowHeight };
    }, [headerH, paged.slice.length, rowHeight, scrollTop, useVirtual, viewportH]);

    const visibleRows = useMemo(() => {
        if (!useVirtual || !virtual) return paged.slice;
        return paged.slice.slice(virtual.startIndex, virtual.endIndex);
    }, [paged.slice, useVirtual, virtual]);

    const onHeaderClick = (key: CampaignSortKey, e: React.MouseEvent) => {
        const multi = e.shiftKey;
        setSort((prev) => toggleSort(prev, key, multi));
    };

    const onExport = (format: "csv" | "json" | "pdf") => {
        const items = exportItems;
        if (format === "csv") {
            const csv = campaignsToCsvWithColumns(items, exportColumnList);
            downloadBlob(new Blob([csv], { type: "text/csv;charset=utf-8" }), "campaigns-export.csv");
            return;
        }
        if (format === "json") {
            const json = campaignsToJsonWithColumns(items, exportColumnList, { rangeLabel: exportRange.label });
            downloadBlob(new Blob([json], { type: "application/json" }), "campaigns-export.json");
            return;
        }
        printCampaignsWithColumns(items, exportColumnList, exportRange.label);
    };

    const bulkPause = async () => {
        const ids = Array.from(selected);
        for (const id of ids) await onPause(id);
        setSelected(new Set());
    };

    const bulkResume = async () => {
        const ids = Array.from(selected);
        for (const id of ids) await onResume(id);
        setSelected(new Set());
    };

    const bulkDelete = async () => {
        const ids = Array.from(selected);
        for (const id of ids) await onDelete(id);
        setSelected(new Set());
    };

    const TableHeader = (
        <div
            ref={headerRef}
            role="row"
            className={cn(
                "sticky top-0 z-20 grid gap-2 border-b border-border bg-background px-3 py-2 text-xs font-semibold text-muted-foreground",
                TABLE_GRID_COLS
            )}
        >
            <div className="flex items-center justify-center">
                <input
                    aria-label="Select all visible campaigns"
                    type="checkbox"
                    checked={allVisibleSelected}
                    onChange={(e) => toggleAllVisible(e.target.checked)}
                    className="h-4 w-4 rounded border-input bg-background accent-primary"
                />
            </div>
            <button
                type="button"
                onClick={(e) => onHeaderClick("name", e)}
                className="flex items-center gap-2 rounded-md px-2 py-1 text-left transition-colors duration-150 ease-out hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                aria-label="Sort by Campaign"
            >
                <span aria-hidden className="w-6 shrink-0" />
                <span className="truncate">Campaign</span>
                <span className="text-muted-foreground">{sortIndicator(sort, "name")}</span>
            </button>
            <button
                type="button"
                onClick={(e) => onHeaderClick("progress", e)}
                className="flex items-center justify-end gap-2 rounded-md px-2 py-1 text-left transition-colors duration-150 ease-out hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:hidden"
                aria-label="Sort by Metrics"
            >
                <span className="truncate">Metrics</span>
                <span className="text-muted-foreground">{sortIndicator(sort, "progress")}</span>
            </button>
            {COLUMNS.filter((c) => c.key !== "name").map((c) => (
                <button
                    key={c.key}
                    type="button"
                    onClick={(e) => onHeaderClick(c.key, e)}
                    className={cn(
                        "hidden items-center gap-2 rounded-md px-2 py-1 text-left transition-colors duration-150 ease-out hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:flex",
                        c.numeric ? "justify-end" : "justify-start"
                    )}
                    aria-label={`Sort by ${c.label}`}
                >
                    <span className="truncate">{c.label}</span>
                    <span className="text-muted-foreground">{sortIndicator(sort, c.key)}</span>
                </button>
            ))}
            <div className="hidden sm:block" />
        </div>
    );

    return (
        <div className="space-y-4">
            <div className="content-card relative">
                <div className="grid grid-cols-1 gap-4 md:grid-cols-[1fr_auto] md:items-start">
                    <div className="min-w-0">
                        <div className="text-sm font-semibold text-foreground">Filters</div>
                        <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-3 md:items-start">
                            <div className="relative flex min-w-0 flex-col" ref={suggestRef}>
                                <label className="text-xs font-semibold text-muted-foreground">Campaign name</label>
                                <Input
                                    value={filters.query}
                                    placeholder="Search campaigns…"
                                    onChange={(e) => {
                                        const next = e.target.value;
                                        setFilters((p) => ({ ...p, query: next }));
                                        setSuggestOpen(true);
                                    }}
                                    onFocus={() => {
                                        if (nameSuggestions.length > 0) setSuggestOpen(true);
                                    }}
                                    className="mt-1 border-0 bg-background/50 text-foreground placeholder:text-muted-foreground hover:bg-background focus-visible:ring-0 focus-visible:ring-offset-0"
                                />
                                {suggestOpen && nameSuggestions.length > 0 ? (
                                    <div
                                        role="listbox"
                                        className="absolute left-0 top-full z-50 mt-2 w-full max-h-64 origin-top overflow-auto rounded-xl border border-border bg-popover shadow-xl animate-in fade-in-0 zoom-in-95"
                                    >
                                        {nameSuggestions.map((n) => (
                                            <button
                                                key={n}
                                                type="button"
                                                role="option"
                                                aria-selected={filters.query === n}
                                                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm text-foreground transition-colors duration-150 ease-out hover:bg-accent hover:text-accent-foreground"
                                                onClick={() => {
                                                    setFilters((p) => ({ ...p, query: n }));
                                                    setSuggestOpen(false);
                                                }}
                                            >
                                                <span className="truncate">{n}</span>
                                            </button>
                                        ))}
                                    </div>
                                ) : null}
                            </div>

                            <div className="relative flex min-w-0 flex-col">
                                <label className="text-xs font-semibold text-muted-foreground">Status</label>
                                <button
                                    ref={statusButtonRef}
                                    type="button"
                                    onClick={() =>
                                        setStatusOpen((v) => {
                                            const next = !v;
                                            if (!next) {
                                                setStatusPanelStyle(null);
                                                return next;
                                            }
                                            const btn = statusButtonRef.current;
                                            if (!btn) return next;
                                            const rect = btn.getBoundingClientRect();
                                            setStatusPanelStyle({ left: rect.left, top: rect.bottom + 8, width: rect.width });
                                            return next;
                                        })
                                    }
                                    className="mt-1 flex h-10 w-full items-center justify-between rounded-md border border-input bg-background/50 px-3 text-sm text-foreground hover:bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                    aria-haspopup="listbox"
                                    aria-expanded={statusOpen}
                                >
                                    <span className="truncate">
                                        {filters.statuses.length === 0 ? "All statuses" : `${filters.statuses.length} selected`}
                                    </span>
                                    <ChevronDown className="h-4 w-4 text-muted-foreground" />
                                </button>
                                {statusOpen && typeof document !== "undefined"
                                    ? createPortal(
                                          <div className="fixed inset-0 z-50">
                                              <button
                                                  type="button"
                                                  className="absolute inset-0 bg-transparent"
                                                  aria-label="Close status filter"
                                                  onClick={() => setStatusOpen(false)}
                                              />
                                              <div
                                                  ref={statusPanelRef}
                                                  role="listbox"
                                                  className="absolute overflow-hidden rounded-xl border border-border bg-popover p-2 shadow-xl animate-in fade-in-0 zoom-in-95"
                                                  style={statusPanelStyle ?? undefined}
                                              >
                                                  <div className="max-h-[108px] overflow-y-auto overscroll-contain pr-1 scrollbar-gutter-stable">
                                                      {ALL_STATUSES.map((s) => {
                                                          const checked = filters.statuses.includes(s);
                                                          return (
                                                              <label
                                                                  key={s}
                                                                  className="flex cursor-pointer items-center justify-between rounded-lg px-2 py-2 text-sm text-foreground transition-colors duration-150 ease-out hover:bg-accent hover:text-accent-foreground"
                                                              >
                                                                  <span className="flex items-center gap-2">
                                                                      <span
                                                                          className={cn(
                                                                              "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold",
                                                                              statusBadgeClass(s)
                                                                          )}
                                                                      >
                                                                          {s}
                                                                      </span>
                                                                  </span>
                                                                  <input
                                                                      type="checkbox"
                                                                      checked={checked}
                                                                      onChange={(e) => {
                                                                          const next = e.target.checked;
                                                                          setFilters((p) => {
                                                                              const set = new Set(p.statuses);
                                                                              if (next) set.add(s);
                                                                              else set.delete(s);
                                                                              return { ...p, statuses: Array.from(set) };
                                                                          });
                                                                      }}
                                                                      className="h-4 w-4 rounded border-input bg-background accent-primary"
                                                                  />
                                                              </label>
                                                          );
                                                      })}
                                                  </div>
                                                  <div className="mt-2 flex items-center justify-between gap-2">
                                                      <Button
                                                          type="button"
                                                          variant="secondary"
                                                          size="sm"
                                                          onClick={() => setFilters((p) => ({ ...p, statuses: [] }))}
                                                      >
                                                          Clear
                                                      </Button>
                                                      <Button type="button" variant="outline" size="sm" onClick={() => setStatusOpen(false)}>
                                                          Done
                                                      </Button>
                                                  </div>
                                              </div>
                                          </div>,
                                          document.body
                                      )
                                    : null}
                            </div>

                            <div className="flex min-w-0 flex-col">
                                <label className="text-xs font-semibold text-muted-foreground">Success rate</label>
                                <div className="mt-1 flex h-10 items-center gap-2 rounded-md border-0 bg-background/50 px-2">
                                    <input
                                        aria-label="Minimum success rate"
                                        type="number"
                                        min={0}
                                        max={100}
                                        value={filters.successMin}
                                        onChange={(e) => {
                                            const v = Number(e.target.value);
                                            setFilters((p) => ({ ...p, successMin: Number.isFinite(v) ? Math.max(0, Math.min(100, v)) : 0 }));
                                        }}
                                        className="h-8 flex-1 min-w-0 rounded-md border-0 bg-background px-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-0"
                                    />
                                    <span className="text-sm text-muted-foreground">to</span>
                                    <input
                                        aria-label="Maximum success rate"
                                        type="number"
                                        min={0}
                                        max={100}
                                        value={filters.successMax}
                                        onChange={(e) => {
                                            const v = Number(e.target.value);
                                            setFilters((p) => ({ ...p, successMax: Number.isFinite(v) ? Math.max(0, Math.min(100, v)) : 100 }));
                                        }}
                                        className="h-8 flex-1 min-w-0 rounded-md border-0 bg-background px-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-0"
                                    />
                                </div>
                            </div>
                        </div>
                    </div>

                    <div className="flex items-center justify-end gap-2 md:flex-col md:items-end md:justify-between md:self-stretch">
                        <Button
                            type="button"
                            variant="outline"
                            onClick={() => setExportOpen(true)}
                            className="h-10 border-teal-500/60 bg-teal-600 text-white shadow-sm hover:bg-teal-700 hover:text-white"
                        >
                            Export
                        </Button>
                        <Link href="/campaigns/new" className="shrink-0">
                            <Button
                                type="button"
                                variant="outline"
                                className="h-10 border-teal-500/60 bg-teal-600 text-white shadow-sm hover:bg-teal-700 hover:text-white"
                            >
                                New Campaign
                            </Button>
                        </Link>
                    </div>
                </div>
            </div>

            {bulkCount > 0 ? (
                <div className="content-card flex flex-wrap items-center justify-between gap-3">
                    <div className="text-sm font-semibold text-foreground">
                        {bulkCount} selected
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        <Button type="button" variant="outline" size="sm" onClick={bulkPause}>
                            <Pause className="h-4 w-4" />
                            Pause Selected
                        </Button>
                        <Button type="button" variant="outline" size="sm" onClick={bulkResume}>
                            <Play className="h-4 w-4" />
                            Resume Selected
                        </Button>
                        <Button type="button" variant="secondary" size="sm" onClick={() => setExportOpen(true)}>
                            Export Selected
                        </Button>
                        <Button type="button" variant="destructive" size="sm" onClick={() => setConfirmDeleteId("__bulk__")}>
                            <Trash2 className="h-4 w-4" />
                            Delete Selected
                        </Button>
                    </div>
                </div>
            ) : null}

            <div className="content-card overflow-hidden">
                <div className="flex flex-col gap-3 border-b border-border px-3 py-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
                    <div className="text-sm text-muted-foreground text-center sm:text-left">
                        {paged.start}-{paged.end} of {sorted.length}
                    </div>
                    <div className="flex flex-wrap items-center justify-center gap-2 sm:justify-end">
                        <div className="text-xs font-semibold text-muted-foreground">Rows</div>
                        <select
                            aria-label="Rows per page"
                            value={rowsPerPage}
                            onChange={(e) => {
                                const v = e.target.value;
                                const next: RowsPerPage =
                                    v === "All" ? "All" : (Number(v) as RowsPerPage);
                                setRowsPerPage(next);
                            }}
                            className="h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground"
                        >
                            <option value={10}>10</option>
                            <option value={25}>25</option>
                            <option value={50}>50</option>
                            <option value={100}>100</option>
                            <option value="All">All</option>
                        </select>
                        <div className="flex flex-wrap items-center justify-center gap-1">
                            <Button type="button" variant="outline" size="sm" onClick={() => setPage(1)} disabled={paged.page <= 1}>
                                First
                            </Button>
                            <Button type="button" variant="outline" size="sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={paged.page <= 1}>
                                Prev
                            </Button>
                            <div className="px-2 text-xs font-semibold text-muted-foreground">
                                {paged.page}/{paged.pageCount}
                            </div>
                            <Button type="button" variant="outline" size="sm" onClick={() => setPage((p) => Math.min(paged.pageCount, p + 1))} disabled={paged.page >= paged.pageCount}>
                                Next
                            </Button>
                            <Button type="button" variant="outline" size="sm" onClick={() => setPage(paged.pageCount)} disabled={paged.page >= paged.pageCount}>
                                Last
                            </Button>
                        </div>
                    </div>
                </div>

                {loading ? (
                    <div className="flex items-center justify-center py-16">
                        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
                    </div>
                ) : error ? (
                    <div className="p-6 text-sm font-semibold text-destructive">{error}</div>
                ) : sorted.length === 0 ? (
                    <div className="p-10 text-center">
                        <div className="text-sm font-semibold text-foreground">No campaigns match your filters</div>
                        <div className="mt-1 text-sm text-muted-foreground">Try adjusting status or success rate.</div>
                    </div>
                ) : (
                    <div
                        ref={tableScrollRef}
                        data-testid="campaigns-performance-table"
                        className={cn("scrollbar-gutter-stable relative max-h-[520px] overflow-y-auto overflow-x-hidden overscroll-contain", useVirtual ? "pb-2" : "")}
                        role="rowgroup"
                    >
                        {TableHeader}
                        {useVirtual && virtual ? <div style={{ height: virtual.topPad }} /> : null}

                                {visibleRows.map((campaign) => {
                                    const st = normalizeCampaignStatus(campaign.status);
                                    const progress = campaignProgressPct(campaign);
                                    const success = campaignSuccessRatePct(campaign);
                                    const isSelected = selected.has(campaign.id);
                                    const isExpanded = expanded.has(campaign.id);
                                    const canPause = st === "Active";
                                    const canResume = st === "Paused" || st === "Draft" || st === "Failed";

                                    return (
                                        <div key={campaign.id} className="border-b border-border">
                                            <div role="row" className={cn("grid items-center gap-2 px-3 py-2 text-sm text-foreground", TABLE_GRID_COLS)}>
                                                <div className="flex items-center justify-center">
                                                    <input
                                                        aria-label={`Select ${campaign.name}`}
                                                        type="checkbox"
                                                        checked={isSelected}
                                                        onChange={(e) => toggleSelected(campaign.id, e.target.checked)}
                                                        className="h-4 w-4 rounded border-input bg-background accent-primary"
                                                    />
                                                </div>
                                                <div className="flex items-center gap-2 px-2">
                                                    <button
                                                        type="button"
                                                        aria-label={isExpanded ? "Collapse row details" : "Expand row details"}
                                                        className="rounded-md p-1 text-muted-foreground transition-colors duration-150 ease-out hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                                        onClick={() => toggleExpanded(campaign.id)}
                                                    >
                                                        {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                                                    </button>
                                                    <button
                                                        type="button"
                                                        className="min-w-0 flex-1 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                                        onClick={() => setDetailsId(campaign.id)}
                                                    >
                                                        <div className="min-w-0 flex items-center gap-2">
                                                            <div className="min-w-0 truncate font-semibold hover:underline">{campaign.name}</div>
                                                            <span
                                                                className={cn(
                                                                    "sm:hidden inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold",
                                                                    statusBadgeClass(st)
                                                                )}
                                                            >
                                                                {st}
                                                            </span>
                                                        </div>
                                                        <div className="truncate text-xs text-muted-foreground">{campaign.description || "—"}</div>
                                                    </button>
                                                </div>
                                                <div className="px-2 sm:hidden">
                                                    <div className="flex items-center gap-2">
                                                        <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                                                            <div className={cn("h-full", progressColorClass(progress))} style={{ width: `${progress.toFixed(1)}%` }} />
                                                        </div>
                                                        <div className="w-12 text-right text-xs font-semibold tabular-nums text-foreground">
                                                            {formatPct(progress)}
                                                        </div>
                                                    </div>
                                                    <div className="mt-1 overflow-x-auto">
                                                        <div className="flex min-w-max items-center gap-3 text-[11px] tabular-nums text-muted-foreground whitespace-nowrap">
                                                            <span className="text-foreground font-semibold">SR {formatPct(success)}</span>
                                                            <span>L {Number(campaign.total_leads || 0).toLocaleString()}</span>
                                                            <span>C {Number(campaign.calls_completed || 0).toLocaleString()}</span>
                                                            <span>F {Number(campaign.calls_failed || 0).toLocaleString()}</span>
                                                        </div>
                                                    </div>
                                                </div>
                                                <div className="hidden px-2 sm:block">
                                                    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold", statusBadgeClass(st))}>
                                                        {st}
                                                    </span>
                                                </div>
                                                <div className="hidden px-2 sm:block">
                                                    <div className="flex items-center justify-end gap-2">
                                                        <div className="h-2 w-20 overflow-hidden rounded-full bg-muted">
                                                            <div className={cn("h-full", progressColorClass(progress))} style={{ width: `${progress.toFixed(1)}%` }} />
                                                        </div>
                                                        <div className="w-12 text-right text-xs font-semibold tabular-nums text-foreground">
                                                            {formatPct(progress)}
                                                        </div>
                                                    </div>
                                                </div>
                                                <div className="hidden px-2 text-right text-sm font-semibold tabular-nums text-foreground sm:block">{formatPct(success)}</div>
                                                <div className="hidden px-2 text-right tabular-nums text-foreground sm:block">{Number(campaign.total_leads || 0).toLocaleString()}</div>
                                                <div className="hidden px-2 text-right tabular-nums text-foreground sm:block">{Number(campaign.calls_completed || 0).toLocaleString()}</div>
                                                <div className="hidden px-2 text-right tabular-nums text-foreground sm:block">{Number(campaign.calls_failed || 0).toLocaleString()}</div>
                                                <div className="relative hidden items-center justify-end sm:flex">
                                                    <button
                                                        type="button"
                                                        aria-label="Row actions"
                                                        className="rounded-md p-2 text-muted-foreground transition-colors duration-150 ease-out hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                                        onClick={() => setMenuOpenFor((v) => (v === campaign.id ? null : campaign.id))}
                                                    >
                                                        <Ellipsis className="h-4 w-4" />
                                                    </button>
                                                    {menuOpenFor === campaign.id ? (
                                                        <div
                                                            ref={(node) => {
                                                                menuRefs.current[campaign.id] = node;
                                                            }}
                                                            role="menu"
                                                            className="absolute right-0 top-10 z-50 w-56 origin-top-right overflow-hidden rounded-xl border border-border bg-popover shadow-xl animate-in fade-in-0 zoom-in-95"
                                                        >
                                                            <button
                                                                type="button"
                                                                role="menuitem"
                                                                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-foreground transition-colors duration-150 ease-out hover:bg-accent hover:text-accent-foreground"
                                                                onClick={() => {
                                                                    setMenuOpenFor(null);
                                                                    setDetailsId(campaign.id);
                                                                }}
                                                            >
                                                                View Details
                                                            </button>
                                                            <button
                                                                type="button"
                                                                role="menuitem"
                                                                className={cn(
                                                                    "flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-foreground transition-colors duration-150 ease-out hover:bg-accent hover:text-accent-foreground",
                                                                    !(canPause || canResume) ? "opacity-50" : ""
                                                                )}
                                                                disabled={!(canPause || canResume)}
                                                                onClick={async () => {
                                                                    setMenuOpenFor(null);
                                                                    if (canPause) await onPause(campaign.id);
                                                                    else if (canResume) await onResume(campaign.id);
                                                                }}
                                                            >
                                                                {canPause ? (
                                                                    <>
                                                                        <Pause className="h-4 w-4" /> Pause Campaign
                                                                    </>
                                                                ) : (
                                                                    <>
                                                                        <Play className="h-4 w-4" /> Resume Campaign
                                                                    </>
                                                                )}
                                                            </button>
                                                            <button
                                                                type="button"
                                                                role="menuitem"
                                                                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-foreground transition-colors duration-150 ease-out hover:bg-accent hover:text-accent-foreground"
                                                                onClick={() => {
                                                                    setMenuOpenFor(null);
                                                                    setEditId(campaign.id);
                                                                }}
                                                            >
                                                                Edit Settings
                                                            </button>
                                                            <button
                                                                type="button"
                                                                role="menuitem"
                                                                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-foreground transition-colors duration-150 ease-out hover:bg-muted"
                                                                onClick={() => {
                                                                    setMenuOpenFor(null);
                                                                    router.push(`/analytics?campaign=${encodeURIComponent(campaign.id)}`);
                                                                }}
                                                            >
                                                                View Analytics
                                                            </button>
                                                            <button
                                                                type="button"
                                                                role="menuitem"
                                                                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-foreground transition-colors duration-150 ease-out hover:bg-accent hover:text-accent-foreground"
                                                                onClick={async () => {
                                                                    setMenuOpenFor(null);
                                                                    await onDuplicate(campaign.id);
                                                                }}
                                                            >
                                                                <Copy className="h-4 w-4" />
                                                                Duplicate
                                                            </button>
                                                            <button
                                                                type="button"
                                                                role="menuitem"
                                                                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-destructive transition-colors duration-150 ease-out hover:bg-destructive/10"
                                                                onClick={() => {
                                                                    setMenuOpenFor(null);
                                                                    setConfirmDeleteId(campaign.id);
                                                                }}
                                                            >
                                                                <Trash2 className="h-4 w-4" />
                                                                Delete
                                                            </button>
                                                        </div>
                                                    ) : null}
                                                </div>
                                            </div>

                                            {isExpanded ? (
                                                <div className="grid grid-cols-1 gap-4 px-6 pb-5 pt-3 text-sm text-muted-foreground md:grid-cols-3">
                                                    <div className="rounded-xl border border-border bg-muted/30 p-4">
                                                        <div className="text-xs font-semibold text-muted-foreground">Performance breakdown</div>
                                                        <div className="mt-3 space-y-2">
                                                            <div className="flex items-center justify-between">
                                                                <div className="text-sm text-muted-foreground">Completion</div>
                                                                <div className="text-sm font-semibold tabular-nums text-foreground">{formatPct(progress)}</div>
                                                            </div>
                                                            <div className="flex items-center justify-between">
                                                                <div className="text-sm text-muted-foreground">Success rate</div>
                                                                <div className="text-sm font-semibold tabular-nums text-foreground">{formatPct(success)}</div>
                                                            </div>
                                                            <div className="flex items-center justify-between">
                                                                <div className="text-sm text-muted-foreground">Leads</div>
                                                                <div className="text-sm font-semibold tabular-nums text-foreground">{Number(campaign.total_leads || 0).toLocaleString()}</div>
                                                            </div>
                                                        </div>
                                                    </div>
                                                    <div className="rounded-xl border border-border bg-muted/30 p-4">
                                                        <div className="text-xs font-semibold text-muted-foreground">Recent activity</div>
                                                        <div className="mt-3 space-y-2">
                                                            <div className="flex items-center justify-between">
                                                                <div className="text-sm text-muted-foreground">Last update</div>
                                                                <div className="text-xs font-semibold text-muted-foreground">{new Date(campaign.created_at).toLocaleString()}</div>
                                                            </div>
                                                            <div className="flex items-center justify-between">
                                                                <div className="text-sm text-muted-foreground">Status change</div>
                                                                <div className="text-xs font-semibold text-muted-foreground">{st}</div>
                                                            </div>
                                                            <div className="flex items-center justify-between">
                                                                <div className="text-sm text-muted-foreground">Agent</div>
                                                                <div className="text-xs font-semibold text-muted-foreground">Talky AI</div>
                                                            </div>
                                                        </div>
                                                    </div>
                                                    <div className="rounded-xl border border-border bg-muted/30 p-4">
                                                        <div className="text-xs font-semibold text-muted-foreground">Assets & resources</div>
                                                        <div className="mt-3 space-y-2 text-sm text-muted-foreground">
                                                            <div className="flex items-center justify-between">
                                                                <div>Script</div>
                                                                <div className="text-xs font-semibold text-muted-foreground truncate max-w-[160px]">{campaign.voice_id}</div>
                                                            </div>
                                                            <div className="flex items-center justify-between">
                                                                <div>Prompt</div>
                                                                <div className="text-xs font-semibold text-muted-foreground truncate max-w-[160px]">{campaign.system_prompt?.slice(0, 16) || "—"}</div>
                                                            </div>
                                                            <div className="flex items-center justify-between">
                                                                <div>Concurrency</div>
                                                                <div className="text-xs font-semibold text-muted-foreground tabular-nums">{campaign.max_concurrent_calls || 0}</div>
                                                            </div>
                                                        </div>
                                                    </div>
                                                </div>
                                            ) : null}
                                        </div>
                                    );
                                })}

                                {useVirtual && virtual ? <div style={{ height: virtual.bottomPad }} /> : null}
                    </div>
                )}
            </div>

            <Modal
                open={exportOpen}
                onOpenChange={setExportOpen}
                title="Export & Reporting"
                description={selected.size > 0 ? "Exports selected campaigns." : "Exports the current page."}
                size="lg"
            >
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                    <div className="rounded-xl border border-border bg-muted/30 p-4">
                        <div className="text-sm font-semibold text-foreground">Export format</div>
                        <div className="mt-3 flex flex-wrap gap-2">
                            <Button type="button" variant="secondary" onClick={() => onExport("pdf")}>
                                PDF
                            </Button>
                            <Button type="button" variant="secondary" onClick={() => onExport("csv")}>
                                Excel
                            </Button>
                            <Button type="button" variant="secondary" onClick={() => onExport("csv")}>
                                CSV
                            </Button>
                            <Button type="button" variant="secondary" onClick={() => onExport("json")}>
                                JSON
                            </Button>
                        </div>
                        <div className="mt-3 text-xs font-semibold text-muted-foreground">
                            PDF opens a print view; save as PDF from your browser.
                        </div>
                    </div>
                    <div className="rounded-xl border border-border bg-muted/30 p-4">
                        <div className="text-sm font-semibold text-foreground">Scheduling</div>
                        <div className="mt-2 text-sm text-muted-foreground">Save recurring settings locally (prototype mode).</div>
                        <ReportScheduleEditor storageKey="campaigns.performance.reportSchedule" />
                    </div>
                </div>
                <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
                    <div className="rounded-xl border border-border bg-muted/30 p-4">
                        <div className="text-sm font-semibold text-foreground">Date range</div>
                        <div className="mt-3 grid grid-cols-1 gap-3">
                            <select
                                aria-label="Export date range preset"
                                value={exportPreset}
                                onChange={(e) => setExportPreset(e.target.value as ExportPreset)}
                                className="h-10 rounded-md border border-border bg-background px-2 text-sm text-foreground"
                            >
                                <option value="All Time">All Time</option>
                                <option value="Last 7 Days">Last 7 Days</option>
                                <option value="Last 30 Days">Last 30 Days</option>
                                <option value="This Month">This Month</option>
                                <option value="Custom">Custom</option>
                            </select>
                            {exportPreset === "Custom" ? (
                                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                                    <div>
                                        <label className="text-xs font-semibold text-muted-foreground">From</label>
                                        <input
                                            type="date"
                                            value={exportFrom}
                                            onChange={(e) => setExportFrom(e.target.value)}
                                            className="mt-1 h-10 w-full rounded-md border border-border bg-background px-2 text-sm text-foreground"
                                        />
                                    </div>
                                    <div>
                                        <label className="text-xs font-semibold text-muted-foreground">To</label>
                                        <input
                                            type="date"
                                            value={exportTo}
                                            onChange={(e) => setExportTo(e.target.value)}
                                            className="mt-1 h-10 w-full rounded-md border border-border bg-background px-2 text-sm text-foreground"
                                        />
                                    </div>
                                </div>
                            ) : null}
                            <div className="text-xs font-semibold text-muted-foreground">Using campaign created date • {exportRange.label}</div>
                        </div>
                    </div>
                    <div className="rounded-xl border border-border bg-muted/30 p-4">
                        <div className="text-sm font-semibold text-foreground">Columns</div>
                        <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2">
                            {EXPORT_COLUMNS.map((c) => {
                                const checked = exportCols.has(c.key);
                                return (
                                    <label key={c.key} className="flex items-center justify-between gap-3 rounded-lg border border-border bg-background px-3 py-2">
                                        <div className="text-sm font-semibold text-foreground">{c.label}</div>
                                        <input
                                            type="checkbox"
                                            checked={checked}
                                            onChange={(e) => {
                                                const next = e.target.checked;
                                                setExportCols((prev) => {
                                                    const set = new Set(prev);
                                                    if (next) set.add(c.key);
                                                    else set.delete(c.key);
                                                    return set;
                                                });
                                            }}
                                            className="h-4 w-4 rounded border-border bg-background"
                                        />
                                    </label>
                                );
                            })}
                        </div>
                    </div>
                </div>
                <div className="mt-4 rounded-xl border border-border bg-muted/30 p-4">
                    <div className="text-sm font-semibold text-foreground">Included rows</div>
                    <div className="mt-2 text-sm text-muted-foreground">{exportItems.length} campaigns</div>
                </div>
            </Modal>

            <Modal
                open={detailsId !== null}
                onOpenChange={(next) => setDetailsId(next ? detailsId : null)}
                title={detailsCampaign ? detailsCampaign.name : "Campaign Details"}
                description={detailsCampaign ? normalizeCampaignStatus(detailsCampaign.status) : undefined}
                size="lg"
            >
                {detailsCampaign ? (
                    <div className="space-y-4">
                        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                            <div className="rounded-xl border border-border bg-muted/30 p-4">
                                <div className="text-xs font-semibold text-muted-foreground">Completion</div>
                                <div className="mt-2 text-2xl font-black tabular-nums text-foreground">{formatPct(campaignProgressPct(detailsCampaign))}</div>
                            </div>
                            <div className="rounded-xl border border-border bg-muted/30 p-4">
                                <div className="text-xs font-semibold text-muted-foreground">Success Rate</div>
                                <div className="mt-2 text-2xl font-black tabular-nums text-foreground">{formatPct(campaignSuccessRatePct(detailsCampaign))}</div>
                            </div>
                            <div className="rounded-xl border border-border bg-muted/30 p-4">
                                <div className="text-xs font-semibold text-muted-foreground">Leads</div>
                                <div className="mt-2 text-2xl font-black tabular-nums text-foreground">{Number(detailsCampaign.total_leads || 0).toLocaleString()}</div>
                            </div>
                        </div>
                        <div className="rounded-xl border border-border bg-muted/30 p-4">
                            <div className="text-sm font-semibold text-foreground">Description</div>
                            <div className="mt-2 text-sm text-muted-foreground">{detailsCampaign.description || "—"}</div>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                            <Link href={`/campaigns/${detailsCampaign.id}`}>
                                <Button type="button" variant="secondary">
                                    Open Page
                                </Button>
                            </Link>
                            {detailsCanPause || detailsCanResume ? (
                                <Button
                                    type="button"
                                    variant="outline"
                                    onClick={async () => {
                                        if (!detailsCampaign) return;
                                        if (detailsCanPause) await onPause(detailsCampaign.id);
                                        else if (detailsCanResume) await onResume(detailsCampaign.id);
                                    }}
                                >
                                    {detailsCanPause ? "Pause" : "Resume"}
                                </Button>
                            ) : null}
                            <Button
                                type="button"
                                variant="outline"
                                onClick={async () => {
                                    await onDuplicate(detailsCampaign.id);
                                }}
                            >
                                Duplicate
                            </Button>
                            <Button
                                type="button"
                                variant="outline"
                                onClick={() => {
                                    setDetailsId(null);
                                    setEditId(detailsCampaign.id);
                                }}
                            >
                                Edit Settings
                            </Button>
                            <Button
                                type="button"
                                variant="outline"
                                onClick={() => router.push(`/analytics?campaign=${encodeURIComponent(detailsCampaign.id)}`)}
                            >
                                View Analytics
                            </Button>
                            <Button
                                type="button"
                                variant="destructive"
                                onClick={() => {
                                    setDetailsId(null);
                                    setConfirmDeleteId(detailsCampaign.id);
                                }}
                            >
                                Delete
                            </Button>
                        </div>
                    </div>
                ) : null}
            </Modal>

            <Modal
                open={editId !== null}
                onOpenChange={(next) => setEditId(next ? editId : null)}
                title={editCampaign ? `Edit: ${editCampaign.name}` : "Edit Campaign"}
                size="lg"
                footer={
                    <div className="flex items-center justify-end gap-2">
                        <Button type="button" variant="outline" onClick={() => setEditId(null)}>
                            Cancel
                        </Button>
                        <Button
                            type="button"
                            onClick={async () => {
                                if (!editCampaign || !editDraft) return;
                                await onUpdate({
                                    ...editCampaign,
                                    name: editDraft.name,
                                    description: editDraft.description,
                                    max_concurrent_calls: Math.max(0, Math.floor(editDraft.maxConcurrent || 0)),
                                    voice_id: editDraft.voiceId,
                                });
                                setEditId(null);
                            }}
                        >
                            Save
                        </Button>
                    </div>
                }
            >
                {editCampaign && editDraft ? (
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                        <div>
                            <label className="text-xs font-semibold text-muted-foreground">Name</label>
                            <Input
                                value={editDraft.name}
                                onChange={(e) => setEditDraft((p) => (p ? { ...p, name: e.target.value } : p))}
                                className="mt-1"
                            />
                        </div>
                        <div>
                            <label className="text-xs font-semibold text-muted-foreground">Voice ID</label>
                            <Input
                                value={editDraft.voiceId}
                                onChange={(e) => setEditDraft((p) => (p ? { ...p, voiceId: e.target.value } : p))}
                                className="mt-1"
                            />
                        </div>
                        <div className="md:col-span-2">
                            <label className="text-xs font-semibold text-muted-foreground">Description</label>
                            <textarea
                                value={editDraft.description}
                                onChange={(e) => setEditDraft((p) => (p ? { ...p, description: e.target.value } : p))}
                                className="mt-1 h-24 w-full resize-none rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            />
                        </div>
                        <div>
                            <label className="text-xs font-semibold text-muted-foreground">Max concurrent calls</label>
                            <Input
                                type="number"
                                min={0}
                                value={editDraft.maxConcurrent}
                                onChange={(e) => setEditDraft((p) => (p ? { ...p, maxConcurrent: Number(e.target.value) } : p))}
                                className="mt-1"
                            />
                        </div>
                    </div>
                ) : null}
            </Modal>

            <Modal
                open={confirmDeleteId !== null}
                onOpenChange={(next) => setConfirmDeleteId(next ? confirmDeleteId : null)}
                title="Confirm delete"
                description={confirmDeleteId === "__bulk__" ? "Delete all selected campaigns?" : "Delete this campaign?"}
                size="sm"
                footer={
                    <div className="flex items-center justify-end gap-2">
                        <Button type="button" variant="outline" onClick={() => setConfirmDeleteId(null)}>
                            Cancel
                        </Button>
                        <Button
                            type="button"
                            variant="destructive"
                            onClick={async () => {
                                if (!confirmDeleteId) return;
                                if (confirmDeleteId === "__bulk__") await bulkDelete();
                                else await onDelete(confirmDeleteId);
                                setConfirmDeleteId(null);
                            }}
                        >
                            Delete
                        </Button>
                    </div>
                }
            >
                <div className="text-sm text-muted-foreground">
                    This action cannot be undone.
                </div>
            </Modal>
        </div>
    );
}

function ReportScheduleEditor({ storageKey }: { storageKey: string }) {
    const [enabled, setEnabled] = useState(false);
    const [recurrence, setRecurrence] = useState<"Daily" | "Weekly" | "Monthly">("Weekly");
    const [delivery, setDelivery] = useState<"Email" | "Webhook">("Email");
    const [time, setTime] = useState("09:00");
    const [recipients, setRecipients] = useState<string>("ops@company.com");
    const [webhook, setWebhook] = useState<string>("https://example.com/webhook");

    useEffect(() => {
        try {
            const raw = window.localStorage.getItem(storageKey);
            if (!raw) return;
            const parsed = JSON.parse(raw) as unknown;
            if (!parsed || typeof parsed !== "object") return;
            const p = parsed as Record<string, unknown>;
            const nextEnabled = Boolean(p.enabled);
            const nextRecurrence =
                p.recurrence === "Daily" || p.recurrence === "Weekly" || p.recurrence === "Monthly" ? p.recurrence : null;
            const nextDelivery = p.delivery === "Email" || p.delivery === "Webhook" ? p.delivery : null;
            const nextTime = typeof p.time === "string" ? p.time : null;
            const nextRecipients = typeof p.recipients === "string" ? p.recipients : null;
            const nextWebhook = typeof p.webhook === "string" ? p.webhook : null;

            const raf = window.requestAnimationFrame(() => {
                setEnabled(nextEnabled);
                if (nextRecurrence) setRecurrence(nextRecurrence);
                if (nextDelivery) setDelivery(nextDelivery);
                if (nextTime) setTime(nextTime);
                if (nextRecipients) setRecipients(nextRecipients);
                if (nextWebhook) setWebhook(nextWebhook);
            });
            return () => window.cancelAnimationFrame(raf);
        } catch { }
    }, [storageKey]);

    useEffect(() => {
        try {
            window.localStorage.setItem(
                storageKey,
                JSON.stringify({
                    enabled,
                    recurrence,
                    delivery,
                    time,
                    recipients,
                    webhook,
                })
            );
        } catch { }
    }, [delivery, enabled, recurrence, recipients, storageKey, time, webhook]);

    return (
        <div className="mt-3 space-y-3">
            <label className="flex items-center justify-between gap-3 rounded-lg border border-border bg-muted/30 px-3 py-2">
                <div className="text-sm font-semibold text-foreground">Enable recurring report</div>
                <input
                    type="checkbox"
                    checked={enabled}
                    onChange={(e) => setEnabled(e.target.checked)}
                    className="h-4 w-4 rounded border-border bg-background"
                />
            </label>
            <div className={cn("grid grid-cols-1 gap-3 md:grid-cols-2", !enabled ? "opacity-50" : "")} aria-disabled={!enabled}>
                <div>
                    <label className="text-xs font-semibold text-muted-foreground">Recurrence</label>
                    <select
                        value={recurrence}
                        disabled={!enabled}
                        onChange={(e) => {
                            const v = e.target.value;
                            if (v === "Daily" || v === "Weekly" || v === "Monthly") setRecurrence(v);
                        }}
                        className="mt-1 h-10 w-full rounded-md border border-input bg-background px-2 text-sm text-foreground"
                    >
                        <option value="Daily">Daily</option>
                        <option value="Weekly">Weekly</option>
                        <option value="Monthly">Monthly</option>
                    </select>
                </div>
                <div>
                    <label className="text-xs font-semibold text-muted-foreground">Delivery</label>
                    <select
                        value={delivery}
                        disabled={!enabled}
                        onChange={(e) => {
                            const v = e.target.value;
                            if (v === "Email" || v === "Webhook") setDelivery(v);
                        }}
                        className="mt-1 h-10 w-full rounded-md border border-input bg-background px-2 text-sm text-foreground"
                    >
                        <option value="Email">Email</option>
                        <option value="Webhook">Webhook</option>
                    </select>
                </div>
                <div>
                    <label className="text-xs font-semibold text-muted-foreground">Time</label>
                    <input
                        type="time"
                        value={time}
                        disabled={!enabled}
                        onChange={(e) => setTime(e.target.value)}
                        className="mt-1 h-10 w-full rounded-md border border-input bg-background px-2 text-sm text-foreground"
                    />
                </div>
                {delivery === "Email" ? (
                    <div className="md:col-span-2">
                        <label className="text-xs font-semibold text-muted-foreground">Recipients</label>
                        <Input
                            value={recipients}
                            disabled={!enabled}
                            onChange={(e) => setRecipients(e.target.value)}
                            className="mt-1"
                        />
                    </div>
                ) : (
                    <div className="md:col-span-2">
                        <label className="text-xs font-semibold text-muted-foreground">Webhook URL</label>
                        <Input
                            value={webhook}
                            disabled={!enabled}
                            onChange={(e) => setWebhook(e.target.value)}
                            className="mt-1"
                        />
                    </div>
                )}
            </div>
        </div>
    );
}
