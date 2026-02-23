"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { ViewportDrawer } from "@/components/ui/viewport-drawer";
import { RouteGuard } from "@/components/guards/route-guard";
import { EmptyState, ErrorState, LoadingState } from "@/components/states/page-states";
import { useCalendarEvents, useCancelCalendarEvent, useCreateCalendarEvent } from "@/lib/api-hooks";
import { notificationsStore } from "@/lib/notifications";
import { cn } from "@/lib/utils";
import {
    splitAndSortMeetings,
    formatMeetingDateTime,
    meetingLeadLabel,
    meetingParticipantSummary,
    meetingStatusBadgeClass,
    meetingStatusLabel,
    sanitizeMeetingNotesHtml,
    sortMeetings,
    type MeetingSortKey,
    type SortDir,
} from "@/lib/meetings-utils";
import { isApiClientError } from "@/lib/http-client";
import { dashboardApi, type Campaign, type Contact } from "@/lib/dashboard-api";
import type { CalendarEvent } from "@/lib/models";
import { Copy, Loader2, Plus, XCircle } from "lucide-react";

function formatError(err: unknown) {
    if (isApiClientError(err)) return err.message;
    return err instanceof Error ? err.message : "Request failed";
}

type LeadOption = {
    id: string;
    label: string;
    subtitle?: string;
    leadName: string;
};

function contactLabel(c: Contact) {
    const name = [c.first_name, c.last_name].filter(Boolean).join(" ").trim();
    if (name.length > 0) return name;
    return c.phone_number;
}

function matchScore(text: string, query: string) {
    const t = text.toLowerCase();
    const q = query.toLowerCase();
    if (q.length === 0) return 0;
    if (t === q) return 1000;
    if (t.startsWith(q)) return 700;
    if (t.includes(q)) return 300;
    return 0;
}

function sanitizeHtmlToText(html: string) {
    const trimmed = html.trim();
    if (trimmed.length === 0) return "";
    if (typeof document === "undefined") return trimmed;
    const el = document.createElement("div");
    el.innerHTML = trimmed;
    return el.textContent ?? "";
}

async function copyText(text: string) {
    if (typeof navigator === "undefined" || typeof window === "undefined") {
        throw new Error("Clipboard is not available.");
    }
    if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") {
        throw new Error("Clipboard API is unavailable in this browser.");
    }
    if (!window.isSecureContext) {
        throw new Error("Clipboard requires a secure context (HTTPS or localhost).");
    }
    await navigator.clipboard.writeText(text);
}

function meetingDurationMinutes(startIso: string) {
    const ms = Date.parse(startIso);
    if (!Number.isFinite(ms)) return 30;
    return 30;
}

function computeDefaultEndTime(startIso: string) {
    const ms = Date.parse(startIso);
    if (!Number.isFinite(ms)) return undefined;
    const end = new Date(ms + meetingDurationMinutes(startIso) * 60_000);
    return end.toISOString();
}

function toLocalDateTimeInputValue(iso: string | undefined) {
    if (!iso) return "";
    const ms = Date.parse(iso);
    if (!Number.isFinite(ms)) return "";
    const d = new Date(ms);
    const pad = (n: number) => String(n).padStart(2, "0");
    const yyyy = d.getFullYear();
    const mm = pad(d.getMonth() + 1);
    const dd = pad(d.getDate());
    const hh = pad(d.getHours());
    const min = pad(d.getMinutes());
    return `${yyyy}-${mm}-${dd}T${hh}:${min}`;
}

function localDateTimeInputToIso(value: string) {
    const ms = Date.parse(value);
    if (!Number.isFinite(ms)) return undefined;
    return new Date(ms).toISOString();
}

function MeetingsContent() {
    const q = useCalendarEvents();
    const createM = useCreateCalendarEvent();
    const cancelM = useCancelCalendarEvent();

    const [createOpen, setCreateOpen] = useState(false);
    const [drawerId, setDrawerId] = useState<string | null>(null);
    const [confirmCancelId, setConfirmCancelId] = useState<string | null>(null);

    const events = useMemo(() => q.data?.items ?? [], [q.data?.items]);
    const [query, setQuery] = useState("");
    const [sortKey, setSortKey] = useState<MeetingSortKey>("startTime");
    const [sortDir, setSortDir] = useState<SortDir>("asc");

    const filteredEvents = useMemo(() => {
        const needle = query.trim().toLowerCase();
        if (needle.length === 0) return events;
        return events.filter((m) => {
            const parts: string[] = [];
            parts.push(m.title);
            if (m.leadName) parts.push(m.leadName);
            if (m.leadId) parts.push(m.leadId);
            if (m.status) parts.push(m.status);
            for (const p of m.participants ?? []) {
                if (p.name) parts.push(p.name);
                if (p.email) parts.push(p.email);
                if (p.role) parts.push(p.role);
            }
            return parts.join(" ").toLowerCase().includes(needle);
        });
    }, [events, query]);

    const split = useMemo(() => splitAndSortMeetings(filteredEvents), [filteredEvents]);
    const sortedUpcoming = useMemo(() => sortMeetings(split.upcoming, sortKey, sortDir), [split.upcoming, sortDir, sortKey]);
    const sortedPast = useMemo(() => sortMeetings(split.past, sortKey, sortDir), [sortDir, sortKey, split.past]);

    const selected = useMemo(() => events.find((m) => m.id === drawerId) ?? null, [drawerId, events]);
    const confirmMeeting = useMemo(() => events.find((m) => m.id === confirmCancelId) ?? null, [confirmCancelId, events]);

    const [leadsLoading, setLeadsLoading] = useState(false);
    const [leadsError, setLeadsError] = useState<string | null>(null);
    const [leadOptions, setLeadOptions] = useState<LeadOption[]>([]);
    const [leadQuery, setLeadQuery] = useState("");
    const [leadOpen, setLeadOpen] = useState(false);
    const [leadActiveIndex, setLeadActiveIndex] = useState(0);
    const [leadId, setLeadId] = useState<string>("");
    const [leadName, setLeadName] = useState<string>("");
    const leadBoxRef = useRef<HTMLDivElement | null>(null);

    const [title, setTitle] = useState("");
    const [whenValue, setWhenValue] = useState("");
    const notesRef = useRef<HTMLDivElement | null>(null);
    const [formError, setFormError] = useState<string | null>(null);

    const formatNotesInline = (tagName: "strong" | "em") => {
        const root = notesRef.current;
        if (!root) return;
        root.focus();

        const sel = window.getSelection();
        if (!sel || sel.rangeCount === 0) return;
        const range = sel.getRangeAt(0);
        const common = range.commonAncestorContainer;
        const commonEl = common.nodeType === Node.ELEMENT_NODE ? (common as Element) : common.parentElement;
        if (!commonEl || !root.contains(commonEl)) return;

        const nearest = (node: Node) => {
            let cur: Node | null = node;
            while (cur && cur !== root) {
                if (cur.nodeType === Node.ELEMENT_NODE) {
                    const el = cur as HTMLElement;
                    if (el.tagName.toLowerCase() === tagName) return el;
                }
                cur = cur.parentNode;
            }
            return null;
        };

        const unwrap = (el: HTMLElement) => {
            const parent = el.parentNode;
            if (!parent) return;
            while (el.firstChild) parent.insertBefore(el.firstChild, el);
            parent.removeChild(el);
        };

        const startEl = nearest(range.startContainer);
        const endEl = nearest(range.endContainer);
        if (startEl && startEl === endEl) {
            unwrap(startEl);
            return;
        }

        if (range.collapsed) {
            const el = document.createElement(tagName);
            const textNode = document.createTextNode("\u200B");
            el.appendChild(textNode);
            range.insertNode(el);
            const next = document.createRange();
            next.setStart(textNode, 1);
            next.setEnd(textNode, 1);
            sel.removeAllRanges();
            sel.addRange(next);
            return;
        }

        const wrapper = document.createElement(tagName);
        const frag = range.extractContents();
        wrapper.appendChild(frag);
        range.insertNode(wrapper);
    };

    const timeZoneLabel = useMemo(() => {
        try {
            const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
            return tz ? `Times shown in ${tz}` : "Times shown in local time";
        } catch {
            return "Times shown in local time";
        }
    }, []);

    const leadSelectedLabel = useMemo(() => {
        const found = leadOptions.find((x) => x.id === leadId);
        return found?.label ?? "";
    }, [leadId, leadOptions]);

    const filteredLeads = useMemo(() => {
        const q = leadQuery.trim();
        const base = leadOptions.map((o) => ({
            o,
            score: matchScore(o.label, q) + (o.subtitle ? matchScore(o.subtitle, q) * 0.25 : 0),
        }));
        const filtered = q.length === 0 ? base : base.filter((x) => x.score > 0);
        filtered.sort((a, b) => b.score - a.score);
        return filtered.map((x) => x.o).slice(0, 10);
    }, [leadOptions, leadQuery]);

    useEffect(() => {
        if (!createOpen) return;
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
                        all.push({
                            id: c.id,
                            label,
                            subtitle,
                            leadName: label,
                        });
                    }
                }
                if (!alive) return;
                all.sort((a, b) => a.label.localeCompare(b.label));
                setLeadOptions(all);
                if (all.length > 0) {
                    setLeadId((prev) => prev || all[0]!.id);
                    setLeadName((prev) => prev || all[0]!.leadName);
                    setLeadQuery((prev) => (prev.trim().length > 0 ? prev : all[0]!.label));
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
    }, [createOpen]);

    useEffect(() => {
        if (!createOpen) return;
        setFormError(null);
        if (title.length > 0) return;
        setTitle("");
        if (whenValue.length > 0) return;
        const soon = new Date(Date.now() + 60 * 60_000);
        setWhenValue(toLocalDateTimeInputValue(soon.toISOString()));
    }, [createOpen, title.length, whenValue.length]);

    useEffect(() => {
        if (!leadOpen) return;
        const onDown = (e: PointerEvent) => {
            const t = e.target as Node | null;
            if (!t) return;
            if (leadBoxRef.current && leadBoxRef.current.contains(t)) return;
            setLeadOpen(false);
        };
        document.addEventListener("pointerdown", onDown);
        return () => {
            document.removeEventListener("pointerdown", onDown);
        };
    }, [leadOpen]);

    useEffect(() => {
        if (!createOpen) return;
        if (leadOpen) return;
        if (!leadId) return;
        if (!leadSelectedLabel) return;
        if (leadQuery.trim() === leadSelectedLabel.trim()) return;
        setLeadQuery(leadSelectedLabel);
    }, [createOpen, leadId, leadOpen, leadQuery, leadSelectedLabel]);

    const sanitizedSelectedNotes = useMemo(() => {
        if (!selected?.notes) return "";
        return sanitizeMeetingNotesHtml(selected.notes);
    }, [selected?.notes]);

    return (
        <>
            <div className="mx-auto w-full max-w-6xl space-y-6">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                        <div className="text-sm font-semibold text-foreground">Manage meetings</div>
                        <div className="mt-1 text-sm text-muted-foreground">
                            Create, review details, and cancel meetings.
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        <Button type="button" onClick={() => setCreateOpen(true)}>
                            <Plus aria-hidden />
                            Create meeting
                        </Button>
                    </div>
                </div>

                <div className="flex flex-col gap-3 rounded-2xl border border-border bg-background/70 p-4 md:flex-row md:items-end md:justify-between">
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 md:gap-4">
                        <div className="space-y-1">
                            <Label htmlFor="mtg-q">Search</Label>
                            <Input id="mtg-q" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Title, lead, participant…" />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="mtg-sort">Sort</Label>
                            <div className="flex gap-2">
                                <Select value={sortKey} onChange={(v) => setSortKey(v as MeetingSortKey)} ariaLabel="Sort key" className="flex-1">
                                    <option value="startTime">Date/time</option>
                                    <option value="title">Title</option>
                                    <option value="lead">Lead</option>
                                    <option value="status">Status</option>
                                </Select>
                                <Select value={sortDir} onChange={(v) => setSortDir(v as SortDir)} ariaLabel="Sort direction" className="w-28">
                                    <option value="asc">Asc</option>
                                    <option value="desc">Desc</option>
                                </Select>
                            </div>
                        </div>
                    </div>
                    <div className="text-xs font-semibold text-muted-foreground">{filteredEvents.length} meetings</div>
                </div>

                {q.isLoading ? (
                    <LoadingState title="Loading meetings" description="Fetching upcoming and recent meetings." />
                ) : q.isError ? (
                    <ErrorState
                        title="Failed to load meetings"
                        message={formatError(q.error)}
                        onRetry={() => void q.refetch()}
                        actionHref="/settings/connectors?required=calendar"
                        actionLabel="Open connectors"
                    />
                ) : events.length === 0 ? (
                    <EmptyState
                        title="No meetings yet"
                        message="Create your first meeting to track upcoming and past conversations."
                        actionLabel="Create meeting"
                        onAction={() => setCreateOpen(true)}
                    />
                ) : filteredEvents.length === 0 ? (
                    <EmptyState title="No matches" message="Try a different search query." />
                ) : (
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                        <section className="rounded-2xl border border-border bg-background/70 p-4 md:p-5">
                            <div className="flex items-center justify-between gap-3">
                                <div className="text-sm font-semibold text-foreground">Upcoming</div>
                                <div className="text-xs text-muted-foreground tabular-nums">{sortedUpcoming.length}</div>
                            </div>
                            {sortedUpcoming.length === 0 ? (
                                <div className="mt-4 rounded-xl border border-border bg-background p-4 text-sm text-muted-foreground">
                                    No upcoming meetings.
                                </div>
                            ) : (
                                <div className="mt-4 space-y-2">
                                    {sortedUpcoming.map((m) => (
                                        <MeetingRow key={m.id} meeting={m} onOpen={() => setDrawerId(m.id)} />
                                    ))}
                                </div>
                            )}
                        </section>

                        <section className="rounded-2xl border border-border bg-background/70 p-4 md:p-5">
                            <div className="flex items-center justify-between gap-3">
                                <div className="text-sm font-semibold text-foreground">Past</div>
                                <div className="text-xs text-muted-foreground tabular-nums">{sortedPast.length}</div>
                            </div>
                            {sortedPast.length === 0 ? (
                                <div className="mt-4 rounded-xl border border-border bg-background p-4 text-sm text-muted-foreground">
                                    No past meetings.
                                </div>
                            ) : (
                                <div className="mt-4 space-y-2">
                                    {sortedPast.map((m) => (
                                        <MeetingRow key={m.id} meeting={m} onOpen={() => setDrawerId(m.id)} />
                                    ))}
                                </div>
                            )}
                        </section>
                    </div>
                )}
            </div>

            <ViewportDrawer
                open={selected !== null}
                onOpenChange={(next) => setDrawerId(next ? drawerId : null)}
                side="right"
                size={520}
                margin={10}
                ariaLabel="Meeting details"
                panelClassName="bg-background/90 border border-border"
            >
                {selected ? (
                    <div className="p-5">
                        <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                                <div className="text-base font-semibold text-foreground truncate">{selected.title}</div>
                                <div className="mt-1 text-sm text-muted-foreground">
                                    {formatMeetingDateTime(selected.startTime)}
                                    {selected.endTime ? ` – ${formatMeetingDateTime(selected.endTime)}` : ""}
                                </div>
                            </div>
                            <button
                                type="button"
                                className="inline-flex h-9 w-9 items-center justify-center rounded-xl text-muted-foreground hover:bg-foreground/5 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-foreground/20"
                                onClick={() => setDrawerId(null)}
                                aria-label="Close details"
                            >
                                <XCircle className="h-5 w-5" aria-hidden />
                            </button>
                        </div>

                        <div className="mt-4 flex items-center gap-2">
                            <span className={cn("inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold", meetingStatusBadgeClass(selected))}>
                                {meetingStatusLabel(selected)}
                            </span>
                            <div className="text-sm text-muted-foreground truncate">Lead: {meetingLeadLabel(selected)}</div>
                        </div>

                        <div className="mt-5 space-y-5">
                            <div className="rounded-2xl border border-border bg-background p-4">
                                <div className="text-sm font-semibold text-foreground">Details</div>
                                <div className="mt-3 space-y-2">
                                    <InfoRow label="Meeting ID" value={selected.id} />
                                    {selected.leadId ? <InfoRow label="Lead ID" value={selected.leadId} /> : null}
                                    {selected.status ? <InfoRow label="Raw status" value={selected.status} /> : null}
                                </div>
                            </div>

                            {(selected.joinLink || selected.calendarLink) && (
                                <div className="rounded-2xl border border-border bg-background p-4">
                                    <div className="text-sm font-semibold text-foreground">Links</div>
                                    <div className="mt-3 space-y-2">
                                        {selected.joinLink ? (
                                            <LinkRow label="Join link" href={selected.joinLink} variant="default" />
                                        ) : null}
                                        {selected.calendarLink ? (
                                            <LinkRow label="Calendar link" href={selected.calendarLink} variant="secondary" />
                                        ) : null}
                                    </div>
                                </div>
                            )}

                            <div className="rounded-2xl border border-border bg-background p-4">
                                <div className="text-sm font-semibold text-foreground">Notes</div>
                                <div className="mt-3 text-sm text-foreground whitespace-pre-wrap break-words">
                                    {sanitizedSelectedNotes ? (
                                        <div dangerouslySetInnerHTML={{ __html: sanitizedSelectedNotes }} />
                                    ) : (
                                        <div className="text-muted-foreground">No notes.</div>
                                    )}
                                </div>
                            </div>

                            <div className="rounded-2xl border border-border bg-background p-4">
                                <div className="text-sm font-semibold text-foreground">Participants</div>
                                <div className="mt-3 space-y-2">
                                    {(selected.participants ?? []).length === 0 ? (
                                        <div className="text-sm text-muted-foreground">No participants.</div>
                                    ) : (
                                        (selected.participants ?? []).map((p, idx) => (
                                            <div key={`${p.id ?? p.email ?? p.name ?? "p"}-${idx}`} className="rounded-xl border border-border bg-background/70 p-3">
                                                <div className="text-sm font-semibold text-foreground">{p.name ?? "Unknown"}</div>
                                                <div className="mt-1 text-xs text-muted-foreground">
                                                    {[p.email, p.role].filter(Boolean).join(" • ") || "—"}
                                                </div>
                                            </div>
                                        ))
                                    )}
                                </div>
                            </div>

                            <div className="flex items-center justify-end gap-2">
                                <Button type="button" variant="outline" onClick={() => setDrawerId(null)}>
                                    Close
                                </Button>
                                <Button
                                    type="button"
                                    variant="destructive"
                                    onClick={() => setConfirmCancelId(selected.id)}
                                >
                                    Cancel meeting
                                </Button>
                            </div>
                        </div>
                    </div>
                ) : null}
            </ViewportDrawer>

            <Modal
                open={createOpen}
                onOpenChange={(next) => {
                    if (!next) {
                        setCreateOpen(false);
                        setFormError(null);
                        setLeadOpen(false);
                        setLeadQuery("");
                        setLeadActiveIndex(0);
                        setTitle("");
                        if (notesRef.current) notesRef.current.innerHTML = "";
                    } else {
                        setCreateOpen(true);
                    }
                }}
                title="Create meeting"
                description="Schedule a meeting and attach notes."
                size="lg"
                footer={
                    <div className="flex items-center justify-end gap-2">
                        <Button
                            type="button"
                            variant="outline"
                            onClick={() => setCreateOpen(false)}
                            disabled={createM.isPending}
                        >
                            Cancel
                        </Button>
                        <Button
                            type="button"
                            onClick={async () => {
                                setFormError(null);
                                const trimmedTitle = title.trim();
                                if (!leadId) {
                                    setFormError("Select a lead/contact.");
                                    return;
                                }
                                if (trimmedTitle.length === 0) {
                                    setFormError("Meeting title is required.");
                                    return;
                                }
                                if (trimmedTitle.length > 100) {
                                    setFormError("Meeting title must be 100 characters or less.");
                                    return;
                                }
                                const startIso = localDateTimeInputToIso(whenValue);
                                if (!startIso) {
                                    setFormError("Choose a valid date/time.");
                                    return;
                                }
                                const startMs = Date.parse(startIso);
                                if (!Number.isFinite(startMs) || startMs < Date.now() - 60_000) {
                                    setFormError("Meeting time must be in the future.");
                                    return;
                                }

                                const endIso = computeDefaultEndTime(startIso);
                                const notesHtml = notesRef.current?.innerHTML ?? "";
                                const sanitizedNotes = sanitizeMeetingNotesHtml(notesHtml);
                                const notesText = sanitizeHtmlToText(sanitizedNotes);
                                try {
                                    await createM.mutateAsync({
                                        leadId,
                                        leadName: leadName || leadSelectedLabel || undefined,
                                        title: trimmedTitle,
                                        startTime: startIso,
                                        endTime: endIso,
                                        notes: notesText.length > 0 ? sanitizedNotes : undefined,
                                    });
                                    setCreateOpen(false);
                                } catch (e) {
                                    setFormError(formatError(e));
                                }
                            }}
                            disabled={createM.isPending}
                        >
                            {createM.isPending ? (
                                <>
                                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                                    Creating…
                                </>
                            ) : (
                                "Create"
                            )}
                        </Button>
                    </div>
                }
            >
                <div className="space-y-5">
                    {formError ? (
                        <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">
                            {formError}
                        </div>
                    ) : null}

                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                            <Label className="text-gray-200">Lead / contact</Label>
                            <div className="relative" ref={leadBoxRef}>
                                <Input
                                    value={leadQuery}
                                    onChange={(e) => {
                                        setLeadQuery(e.target.value);
                                        setLeadId("");
                                        setLeadName("");
                                        setLeadOpen(true);
                                        setLeadActiveIndex(0);
                                    }}
                                    onFocus={() => setLeadOpen(true)}
                                    onKeyDown={(e) => {
                                        if (!leadOpen) return;
                                        if (e.key === "Escape") {
                                            setLeadOpen(false);
                                            return;
                                        }
                                        if (e.key === "ArrowDown") {
                                            e.preventDefault();
                                            setLeadActiveIndex((i) => Math.min(filteredLeads.length - 1, i + 1));
                                            return;
                                        }
                                        if (e.key === "ArrowUp") {
                                            e.preventDefault();
                                            setLeadActiveIndex((i) => Math.max(0, i - 1));
                                            return;
                                        }
                                        if (e.key === "Enter") {
                                            e.preventDefault();
                                            const pick = filteredLeads[leadActiveIndex];
                                            if (!pick) return;
                                            setLeadId(pick.id);
                                            setLeadName(pick.leadName);
                                            setLeadQuery(pick.label);
                                            setLeadOpen(false);
                                        }
                                    }}
                                    placeholder={leadsLoading ? "Loading contacts…" : "Search contacts…"}
                                    disabled={leadsLoading || Boolean(leadsError)}
                                    className="border-white/15 bg-white/5 text-white placeholder:text-gray-300 focus-visible:ring-white/20"
                                    aria-label="Search contacts"
                                    aria-expanded={leadOpen}
                                    aria-haspopup="listbox"
                                />
                                {leadsError ? (
                                    <div className="mt-2 text-xs text-red-200">
                                        {leadsError}
                                    </div>
                                ) : null}
                                {leadOpen && filteredLeads.length > 0 ? (
                                    <div role="listbox" className="absolute z-20 mt-2 w-full overflow-hidden rounded-xl border border-white/10 bg-gray-950/95 shadow-xl">
                                        {filteredLeads.map((opt, idx) => {
                                            const active = idx === leadActiveIndex;
                                            return (
                                                <button
                                                    key={opt.id}
                                                    type="button"
                                                    role="option"
                                                    aria-selected={leadId === opt.id}
                                                    className={cn(
                                                        "flex w-full items-start justify-between gap-3 px-3 py-2 text-left text-sm text-white",
                                                        active ? "bg-white/10" : "hover:bg-white/5"
                                                    )}
                                                    onMouseEnter={() => setLeadActiveIndex(idx)}
                                                    onClick={() => {
                                                        setLeadId(opt.id);
                                                        setLeadName(opt.leadName);
                                                        setLeadQuery(opt.label);
                                                        setLeadOpen(false);
                                                    }}
                                                >
                                                    <div className="min-w-0">
                                                        <div className="truncate font-semibold">{opt.label}</div>
                                                        {opt.subtitle ? <div className="mt-0.5 truncate text-xs text-gray-300">{opt.subtitle}</div> : null}
                                                    </div>
                                                </button>
                                            );
                                        })}
                                    </div>
                                ) : null}
                                {!leadOpen && leadSelectedLabel ? (
                                    <div className="mt-2 text-xs text-gray-300">
                                        Selected: <span className="font-semibold text-white">{leadSelectedLabel}</span>
                                    </div>
                                ) : null}
                            </div>
                        </div>

                        <div className="space-y-2">
                            <Label className="text-gray-200">Date & time</Label>
                            <Input
                                type="datetime-local"
                                value={whenValue}
                                onChange={(e) => setWhenValue(e.target.value)}
                                className="border-white/15 bg-white/5 text-white placeholder:text-gray-300 focus-visible:ring-white/20"
                                aria-label="Meeting date and time"
                            />
                            <div className="text-xs text-gray-300">{timeZoneLabel}</div>
                        </div>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center justify-between gap-3">
                            <Label className="text-gray-200">Meeting title</Label>
                            <div className={cn("text-xs tabular-nums", title.trim().length > 100 ? "text-red-200" : "text-gray-300")}>
                                {title.trim().length}/100
                            </div>
                        </div>
                        <Input
                            value={title}
                            onChange={(e) => setTitle(e.target.value)}
                            placeholder="e.g., Follow-up call"
                            maxLength={130}
                            className="border-white/15 bg-white/5 text-white placeholder:text-gray-300 focus-visible:ring-white/20"
                            aria-label="Meeting title"
                        />
                    </div>

                    <div className="space-y-2">
                        <Label className="text-gray-200">Notes</Label>
                        <div className="flex items-center gap-2">
                            <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                onClick={() => {
                                    try {
                                        formatNotesInline("strong");
                                    } catch {}
                                }}
                            >
                                Bold
                            </Button>
                            <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                onClick={() => {
                                    try {
                                        formatNotesInline("em");
                                    } catch {}
                                }}
                            >
                                Italic
                            </Button>
                        </div>
                        <div
                            ref={notesRef}
                            role="textbox"
                            aria-multiline="true"
                            contentEditable
                            className="min-h-28 w-full rounded-xl border border-white/15 bg-white/5 px-3 py-2 text-sm text-white outline-none focus-visible:ring-2 focus-visible:ring-white/20"
                            onInput={() => setFormError(null)}
                        />
                        <div className="text-xs text-gray-300">Formatting is saved as rich text.</div>
                    </div>
                </div>
            </Modal>

            <Modal
                open={confirmMeeting !== null}
                onOpenChange={(next) => setConfirmCancelId(next ? confirmCancelId : null)}
                title="Cancel meeting"
                description="This will remove the meeting from your list."
                size="sm"
                footer={
                    <div className="flex items-center justify-end gap-2">
                        <Button type="button" variant="outline" onClick={() => setConfirmCancelId(null)} disabled={cancelM.isPending}>
                            Keep
                        </Button>
                        <Button
                            type="button"
                            variant="destructive"
                            disabled={cancelM.isPending || !confirmMeeting}
                            onClick={async () => {
                                if (!confirmMeeting) return;
                                try {
                                    await cancelM.mutateAsync(confirmMeeting.id);
                                    setConfirmCancelId(null);
                                    setDrawerId(null);
                                } catch (e) {
                                    notificationsStore.create({ type: "error", title: "Cancel failed", message: formatError(e) });
                                }
                            }}
                        >
                            {cancelM.isPending ? (
                                <>
                                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                                    Cancelling…
                                </>
                            ) : (
                                "Cancel meeting"
                            )}
                        </Button>
                    </div>
                }
            >
                {confirmMeeting ? (
                    <div className="space-y-3 text-sm text-gray-200">
                        <div className="rounded-xl border border-white/10 bg-white/5 p-3">
                            <div className="font-semibold text-white">{confirmMeeting.title}</div>
                            <div className="mt-1 text-xs text-gray-300">{formatMeetingDateTime(confirmMeeting.startTime)}</div>
                            <div className="mt-1 text-xs text-gray-300">Lead: {meetingLeadLabel(confirmMeeting)}</div>
                        </div>
                        <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-red-100">
                            This action cannot be undone.
                        </div>
                    </div>
                ) : null}
            </Modal>
        </>
    );
}

export default function MeetingsPage() {
    return (
        <DashboardLayout title="Meetings" description="Upcoming and recent meetings.">
            <RouteGuard title="Meetings" description="Upcoming and recent meetings." requiredConnectors={["calendar"]}>
                <MeetingsContent />
            </RouteGuard>
        </DashboardLayout>
    );
}

function MeetingRow({ meeting, onOpen }: { meeting: CalendarEvent; onOpen: () => void }) {
    const participantSummary = meetingParticipantSummary(meeting);

    return (
        <button
            type="button"
            onClick={onOpen}
            className="w-full rounded-2xl border border-border bg-background p-4 text-left transition-colors hover:bg-foreground/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-foreground/20"
            aria-label={`Open details for ${meeting.title}`}
        >
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                    <div className="text-sm font-semibold text-foreground truncate">{meeting.title}</div>
                    <div className="mt-1 text-xs text-muted-foreground truncate">{formatMeetingDateTime(meeting.startTime)}</div>
                    <div className="mt-2 text-xs text-muted-foreground truncate">Lead: {meetingLeadLabel(meeting)}</div>
                    {participantSummary ? <div className="mt-1 text-xs text-muted-foreground truncate">Participants: {participantSummary}</div> : null}
                </div>
                <div className="shrink-0">
                    <span className={cn("inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold", meetingStatusBadgeClass(meeting))}>
                        {meetingStatusLabel(meeting)}
                    </span>
                </div>
            </div>
        </button>
    );
}

function LinkRow({ label, href, variant }: { label: string; href: string; variant: "default" | "secondary" }) {
    return (
        <div className="flex items-center justify-between gap-2 rounded-xl border border-border bg-background/70 p-3">
            <div className="min-w-0">
                <div className="text-xs font-semibold text-muted-foreground">{label}</div>
                <div className="mt-1 text-sm text-foreground truncate">{href}</div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
                <Button
                    type="button"
                    variant={variant}
                    onClick={() => {
                        window.open(href, "_blank", "noopener,noreferrer");
                    }}
                >
                    Open
                </Button>
                <Button
                    type="button"
                    variant="outline"
                    onClick={async () => {
                        try {
                            await copyText(href);
                            notificationsStore.create({ type: "success", title: "Copied", message: "Link copied to clipboard." });
                        } catch (e) {
                            notificationsStore.create({ type: "error", title: "Copy failed", message: formatError(e) });
                        }
                    }}
                >
                    <Copy aria-hidden />
                    Copy
                </Button>
            </div>
        </div>
    );
}

function InfoRow({ label, value }: { label: string; value: string }) {
    return (
        <div className="flex items-center justify-between gap-2 rounded-xl border border-border bg-background/70 p-3">
            <div className="min-w-0">
                <div className="text-xs font-semibold text-muted-foreground">{label}</div>
                <div className="mt-1 text-sm text-foreground truncate">{value}</div>
            </div>
            <div className="shrink-0">
                <Button
                    type="button"
                    variant="outline"
                    onClick={async () => {
                        try {
                            await copyText(value);
                            notificationsStore.create({ type: "success", title: "Copied", message: "Copied to clipboard." });
                        } catch (e) {
                            notificationsStore.create({ type: "error", title: "Copy failed", message: formatError(e) });
                        }
                    }}
                >
                    <Copy aria-hidden />
                    Copy
                </Button>
            </div>
        </div>
    );
}
