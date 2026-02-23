"use client";

import { useEffect, useMemo, useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { RouteGuard } from "@/components/guards/route-guard";
import { EmptyState, ErrorState, LoadingState } from "@/components/states/page-states";
import { useCalendarEvents, useCancelReminder, useCreateReminder, useReminders, queryKeys } from "@/lib/api-hooks";
import { isApiClientError } from "@/lib/http-client";
import { captureException } from "@/lib/monitoring";
import type { CalendarEvent, Reminder, ReminderChannel, ReminderStatus } from "@/lib/models";
import { isValidEmail } from "@/lib/email-utils";
import {
    applyReminderJoins,
    filterReminders,
    formatIsoDateTime,
    groupReminders,
    reminderStatusBadgeClass,
    reminderStatusLabel,
    retryGuidance,
    sanitizeFailureReason,
    sortReminders,
    type ReminderFilters,
    type ReminderSortKey,
    type SortDir,
} from "@/lib/reminders-utils";
import { useQueryClient } from "@tanstack/react-query";

function formatError(err: unknown) {
    if (isApiClientError(err)) return err.message;
    return err instanceof Error ? err.message : "Request failed";
}

function channelLabel(c: ReminderChannel) {
    if (c === "sms") return "SMS";
    return "Email";
}

function canCancel(status: ReminderStatus) {
    return status === "scheduled" || status === "failed";
}

function toDatetimeLocalValue(iso: string) {
    const ms = Date.parse(iso);
    if (!Number.isFinite(ms)) return "";
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
    if (!Number.isFinite(ms)) return "";
    return new Date(ms).toISOString();
}

function RemindersContent() {
    const qc = useQueryClient();
    const q = useReminders();
    const calQ = useCalendarEvents();
    const createM = useCreateReminder();
    const cancelM = useCancelReminder();

    const [query, setQuery] = useState("");
    const [sortKey, setSortKey] = useState<ReminderSortKey>("scheduledAt");
    const [sortDir, setSortDir] = useState<SortDir>("asc");
    const [statusSet, setStatusSet] = useState<Set<ReminderStatus>>(new Set());
    const [channelSet, setChannelSet] = useState<Set<ReminderChannel>>(new Set());

    const [createOpen, setCreateOpen] = useState(false);
    const [createConfirmOpen, setCreateConfirmOpen] = useState(false);
    const [createMeetingId, setCreateMeetingId] = useState<string>("none");
    const [createChannel, setCreateChannel] = useState<ReminderChannel>("email");
    const [createOffset, setCreateOffset] = useState<"custom" | "t24h" | "t1h" | "t10m">("t24h");
    const [createContent, setCreateContent] = useState("");
    const [createToEmail, setCreateToEmail] = useState("");
    const [createToPhone, setCreateToPhone] = useState("");
    const [createCustomWhen, setCreateCustomWhen] = useState(() => toDatetimeLocalValue(new Date(Date.now() + 60_000 * 30).toISOString()));
    const [createInlineError, setCreateInlineError] = useState<string | null>(null);

    const [cancelId, setCancelId] = useState<string | null>(null);
    const [cancelOpen, setCancelOpen] = useState(false);

    useEffect(() => {
        const onMessage = (event: MessageEvent) => {
            const data = event.data as unknown;
            if (!data || typeof data !== "object") return;
            const t = (data as Record<string, unknown>).type;
            if (t !== "reminders:updated") return;
            void qc.invalidateQueries({ queryKey: queryKeys.reminders() });
        };
        const bc = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("reminders") : null;
        bc?.addEventListener("message", onMessage);
        window.addEventListener("message", onMessage);
        return () => {
            bc?.removeEventListener("message", onMessage);
            bc?.close();
            window.removeEventListener("message", onMessage);
        };
    }, [qc]);

    useEffect(() => {
        if (!q.isError) return;
        captureException(q.error, { area: "reminders", action: "list" });
    }, [q.error, q.isError]);

    const meetings = useMemo(() => {
        const items = calQ.data?.items ?? [];
        const sorted = [...items];
        sorted.sort((a, b) => Date.parse(a.startTime) - Date.parse(b.startTime));
        return sorted;
    }, [calQ.data?.items]);

    const joined = useMemo(() => applyReminderJoins(q.data?.items ?? [], calQ.data?.items ?? []), [q.data?.items, calQ.data?.items]);
    const filtered = useMemo(() => {
        const filters: ReminderFilters = { query, statuses: statusSet, channels: channelSet };
        return filterReminders(joined, filters);
    }, [joined, query, statusSet, channelSet]);
    const sorted = useMemo(() => sortReminders(filtered, sortKey, sortDir), [filtered, sortKey, sortDir]);
    const groups = useMemo(() => groupReminders(sorted), [sorted]);

    const createMeeting = useMemo<CalendarEvent | null>(() => {
        if (createMeetingId === "none") return null;
        return meetings.find((m) => m.id === createMeetingId) ?? null;
    }, [createMeetingId, meetings]);

    const computedScheduledAt = useMemo(() => {
        if (!createMeeting || createOffset === "custom") {
            return fromDatetimeLocalValue(createCustomWhen);
        }
        const startMs = Date.parse(createMeeting.startTime);
        if (!Number.isFinite(startMs)) return "";
        const deltaMs =
            createOffset === "t24h" ? 1000 * 60 * 60 * 24 : createOffset === "t1h" ? 1000 * 60 * 60 : 1000 * 60 * 10;
        return new Date(startMs - deltaMs).toISOString();
    }, [createCustomWhen, createMeeting, createOffset]);

    const createSummary = useMemo(() => {
        const meetingTitle = createMeeting?.title ?? "—";
        const when = computedScheduledAt ? formatIsoDateTime(computedScheduledAt) : "—";
        const to = createChannel === "sms" ? createToPhone.trim() : createToEmail.trim();
        return { meetingTitle, when, to };
    }, [computedScheduledAt, createChannel, createMeeting?.title, createToEmail, createToPhone]);

    function toggleStatusFilter(s: ReminderStatus) {
        setStatusSet((prev) => {
            const next = new Set(prev);
            if (next.has(s)) next.delete(s);
            else next.add(s);
            return next;
        });
    }

    function toggleChannelFilter(c: ReminderChannel) {
        setChannelSet((prev) => {
            const next = new Set(prev);
            if (next.has(c)) next.delete(c);
            else next.add(c);
            return next;
        });
    }

    function validateCreate() {
        const content = createContent.trim();
        if (content.length === 0) return "Message is required.";
        if (createChannel === "email") {
            const v = createToEmail.trim();
            if (!v) return "Recipient email is required.";
            if (!isValidEmail(v)) return "Recipient email is invalid.";
        } else {
            const v = createToPhone.trim();
            if (!v) return "Recipient phone is required.";
            if (!/^\+?[0-9][0-9\s().-]{6,}$/.test(v)) return "Recipient phone is invalid.";
        }
        if (!computedScheduledAt) return "Scheduled time is required.";
        const ms = Date.parse(computedScheduledAt);
        if (!Number.isFinite(ms)) return "Scheduled time is invalid.";
        if (ms < Date.now() + 30_000) return "Scheduled time must be in the future.";
        return null;
    }

    function requestCreate() {
        const err = validateCreate();
        setCreateInlineError(err);
        if (err) return;
        setCreateConfirmOpen(true);
    }

    async function confirmCreate() {
        setCreateConfirmOpen(false);
        const err = validateCreate();
        setCreateInlineError(err);
        if (err) return;

        const meeting = createMeeting;
        const scheduledAt = computedScheduledAt;
        if (!scheduledAt) return;

        try {
            await createM.mutateAsync({
                content: createContent.trim(),
                channel: createChannel,
                scheduledAt,
                meetingId: meeting?.id,
                meetingTitle: meeting?.title,
                contactId: meeting?.leadId,
                contactName: meeting?.leadName,
                toEmail: createChannel === "email" ? createToEmail.trim() : undefined,
                toPhone: createChannel === "sms" ? createToPhone.trim() : undefined,
            });

            setCreateOpen(false);
            setCreateInlineError(null);
            setCreateContent("");
        } catch (e) {
            setCreateInlineError(formatError(e));
        }
    }

    function requestCancel(id: string) {
        setCancelId(id);
        setCancelOpen(true);
    }

    async function confirmCancel() {
        const id = cancelId;
        setCancelOpen(false);
        setCancelId(null);
        if (!id) return;
        try {
            await cancelM.mutateAsync(id);
        } catch {
        }
    }

    return (
        <>
            <div className="mx-auto w-full max-w-6xl space-y-6">
                <div className="flex flex-col gap-3 rounded-2xl border border-border bg-background/70 p-4 md:flex-row md:items-end md:justify-between">
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-3 md:gap-4">
                        <div className="space-y-1">
                            <Label htmlFor="rem-q">Search</Label>
                            <Input id="rem-q" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Meeting, contact, content…" />
                        </div>

                        <div className="space-y-1">
                            <Label htmlFor="rem-sort">Sort</Label>
                            <div className="flex gap-2">
                                <Select value={sortKey} onChange={(v) => setSortKey(v as ReminderSortKey)} ariaLabel="Sort key" className="flex-1">
                                    <option value="scheduledAt">Scheduled time</option>
                                    <option value="status">Status</option>
                                    <option value="meeting">Meeting</option>
                                    <option value="contact">Contact</option>
                                    <option value="retryCount">Retry count</option>
                                </Select>
                                <Select value={sortDir} onChange={(v) => setSortDir(v as SortDir)} ariaLabel="Sort direction" className="w-28">
                                    <option value="asc">Asc</option>
                                    <option value="desc">Desc</option>
                                </Select>
                            </div>
                        </div>

                        <div className="space-y-1">
                            <Label>Filters</Label>
                            <div className="flex flex-wrap gap-2">
                                {(["scheduled", "sent", "failed", "canceled"] as ReminderStatus[]).map((s) => (
                                    <button
                                        key={s}
                                        type="button"
                                        onClick={() => toggleStatusFilter(s)}
                                        className={[
                                            "rounded-lg border px-2 py-1 text-xs font-semibold",
                                            statusSet.has(s) ? "border-white/30 bg-white/10 text-white" : "border-white/15 bg-transparent text-gray-200",
                                        ].join(" ")}
                                    >
                                        {reminderStatusLabel(s)}
                                    </button>
                                ))}
                                {(["email", "sms"] as ReminderChannel[]).map((c) => (
                                    <button
                                        key={c}
                                        type="button"
                                        onClick={() => toggleChannelFilter(c)}
                                        className={[
                                            "rounded-lg border px-2 py-1 text-xs font-semibold",
                                            channelSet.has(c) ? "border-white/30 bg-white/10 text-white" : "border-white/15 bg-transparent text-gray-200",
                                        ].join(" ")}
                                    >
                                        {channelLabel(c)}
                                    </button>
                                ))}
                            </div>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <Button variant="secondary" onClick={() => setCreateOpen(true)}>
                            Create reminder
                        </Button>
                    </div>
                </div>

                {q.isLoading || calQ.isLoading ? (
                    <LoadingState title="Loading reminders" description="Fetching reminders and upcoming meetings." />
                ) : q.isError || calQ.isError ? (
                    <ErrorState
                        title="Failed to load reminders"
                        message={formatError(q.isError ? q.error : calQ.error)}
                        onRetry={() => {
                            void q.refetch();
                            void calQ.refetch();
                        }}
                    />
                ) : groups.length === 0 ? (
                    <EmptyState
                        title="No reminders yet"
                        message="Create your first reminder to follow up with leads."
                        actionLabel="Create reminder"
                        actionAriaLabel="New reminder (empty state)"
                        onAction={() => setCreateOpen(true)}
                    />
                ) : (
                    <div className="space-y-4">
                        {groups.map((g) => (
                            <div key={g.key} className="rounded-2xl border border-border bg-background/70 p-4">
                                <div className="flex flex-col gap-1 md:flex-row md:items-center md:justify-between">
                                    <div className="min-w-0">
                                        <div className="text-sm font-semibold text-foreground truncate">{g.meetingTitle}</div>
                                        <div className="text-xs text-muted-foreground truncate">{g.contactLabel}</div>
                                    </div>
                                    <div className="text-xs font-semibold text-muted-foreground">{g.items.length} reminders</div>
                                </div>

                                <div className="mt-3 space-y-2">
                                    {g.items.map((r) => (
                                        <div key={r.id} className="rounded-xl border border-white/10 bg-white/5 p-3">
                                            <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
                                                <div className="min-w-0">
                                                    <div className="flex flex-wrap items-center gap-2">
                                                        <span
                                                            className={[
                                                                "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold",
                                                                reminderStatusBadgeClass(r.status),
                                                            ].join(" ")}
                                                        >
                                                            {reminderStatusLabel(r.status)}
                                                        </span>
                                                        <span className="text-xs font-semibold text-gray-200">{channelLabel(r.channel)}</span>
                                                        {r.status === "failed" ? (
                                                            <span className="text-xs font-semibold text-red-200">
                                                                Retries: {typeof r.retryCount === "number" ? r.retryCount : 0}
                                                                {typeof r.maxRetries === "number" ? `/${r.maxRetries}` : ""}
                                                            </span>
                                                        ) : null}
                                                    </div>

                                                    <div className="mt-2 text-sm font-semibold text-white break-words">{r.content}</div>

                                                    <div className="mt-2 grid grid-cols-1 gap-2 text-xs md:grid-cols-3">
                                                        <div className="rounded-lg border border-white/10 bg-black/10 px-2 py-1">
                                                            <div className="font-semibold text-gray-200">Scheduled</div>
                                                            <div className="mt-0.5 text-gray-300 tabular-nums">{formatIsoDateTime(r.scheduledAt)}</div>
                                                        </div>
                                                        <div className="rounded-lg border border-white/10 bg-black/10 px-2 py-1">
                                                            <div className="font-semibold text-gray-200">Sent</div>
                                                            <div className="mt-0.5 text-gray-300 tabular-nums">{formatIsoDateTime(r.sentAt)}</div>
                                                        </div>
                                                        <div className="rounded-lg border border-white/10 bg-black/10 px-2 py-1">
                                                            <div className="font-semibold text-gray-200">Canceled</div>
                                                            <div className="mt-0.5 text-gray-300 tabular-nums">{formatIsoDateTime(r.canceledAt)}</div>
                                                        </div>
                                                    </div>

                                                    {r.status === "failed" ? (
                                                        <div className="mt-3 space-y-2">
                                                            <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2">
                                                                <div className="text-xs font-semibold text-red-100">Failure reason</div>
                                                                <div className="mt-1 whitespace-pre-wrap break-words text-xs text-red-100">
                                                                    {sanitizeFailureReason(r.failureReason) || "—"}
                                                                </div>
                                                            </div>
                                                            <div className="text-xs font-semibold text-gray-200">{retryGuidance(r) || "—"}</div>
                                                        </div>
                                                    ) : null}
                                                </div>

                                                <div className="flex shrink-0 items-center justify-end gap-2">
                                                    {canCancel(r.status) ? (
                                                        <Button
                                                            variant="destructive"
                                                            size="sm"
                                                            onClick={() => requestCancel(r.id)}
                                                            disabled={cancelM.isPending}
                                                        >
                                                            Cancel
                                                        </Button>
                                                    ) : null}
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            <Modal
                open={createOpen}
                onOpenChange={(next) => {
                    setCreateOpen(next);
                    if (!next) setCreateConfirmOpen(false);
                }}
                title="Create reminder"
                description="Choose channel and schedule the reminder."
                size="lg"
                footer={
                    <div className="flex items-center justify-between gap-3">
                        <div className="text-xs text-red-200">{createInlineError ?? ""}</div>
                        <div className="flex items-center gap-2">
                            <Button variant="outline" onClick={() => setCreateOpen(false)}>
                                Close
                            </Button>
                            <Button onClick={requestCreate} disabled={createM.isPending}>
                                Schedule
                            </Button>
                        </div>
                    </div>
                }
            >
                <div className="space-y-4">
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                        <div className="space-y-1">
                            <Label htmlFor="rem-meeting">Meeting</Label>
                            <Select
                                value={createMeetingId}
                                onChange={setCreateMeetingId}
                                ariaLabel="Select meeting"
                                className="w-full"
                                disabled={calQ.isLoading}
                            >
                                <option value="none">None</option>
                                {meetings.map((m) => (
                                    <option key={m.id} value={m.id}>
                                        {m.title}
                                    </option>
                                ))}
                            </Select>
                            {createMeeting ? (
                                <div className="text-xs text-gray-300">Starts: {formatIsoDateTime(createMeeting.startTime)}</div>
                            ) : null}
                        </div>

                        <div className="space-y-1">
                            <Label htmlFor="rem-channel">Channel</Label>
                            <Select value={createChannel} onChange={(v) => setCreateChannel(v as ReminderChannel)} ariaLabel="Select channel">
                                <option value="email">Email</option>
                                <option value="sms">SMS</option>
                            </Select>
                            <div className="text-xs text-gray-300">
                                SMS requires backend support; validation still applies.
                            </div>
                        </div>
                    </div>

                    <div className="space-y-1">
                        <Label htmlFor="rem-content">Message</Label>
                        <Input id="rem-content" value={createContent} onChange={(e) => setCreateContent(e.target.value)} placeholder="Reminder message…" />
                    </div>

                    {createChannel === "email" ? (
                        <div className="space-y-1">
                            <Label htmlFor="rem-to-email">Recipient email</Label>
                            <Input
                                id="rem-to-email"
                                value={createToEmail}
                                onChange={(e) => setCreateToEmail(e.target.value)}
                                placeholder="name@example.com"
                                inputMode="email"
                            />
                        </div>
                    ) : (
                        <div className="space-y-1">
                            <Label htmlFor="rem-to-phone">Recipient phone</Label>
                            <Input
                                id="rem-to-phone"
                                value={createToPhone}
                                onChange={(e) => setCreateToPhone(e.target.value)}
                                placeholder="+1 555 000 0000"
                                inputMode="tel"
                            />
                        </div>
                    )}

                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                        <div className="space-y-1">
                            <Label htmlFor="rem-offset">Schedule</Label>
                            <Select
                                value={createOffset}
                                onChange={(v) => setCreateOffset(v as "custom" | "t24h" | "t1h" | "t10m")}
                                ariaLabel="Schedule option"
                            >
                                <option value="t24h">T-24h</option>
                                <option value="t1h">T-1h</option>
                                <option value="t10m">T-10m</option>
                                <option value="custom">Custom</option>
                            </Select>
                            {!createMeeting && createOffset !== "custom" ? (
                                <div className="text-xs text-amber-200">Pick a meeting or use Custom scheduling.</div>
                            ) : null}
                        </div>

                        <div className="space-y-1">
                            <Label htmlFor="rem-custom">Custom time</Label>
                            <Input
                                id="rem-custom"
                                type="datetime-local"
                                value={createCustomWhen}
                                onChange={(e) => setCreateCustomWhen(e.target.value)}
                                disabled={createOffset !== "custom"}
                            />
                            <div className="text-xs text-gray-300">Scheduled: {computedScheduledAt ? formatIsoDateTime(computedScheduledAt) : "—"}</div>
                        </div>
                    </div>
                </div>
            </Modal>

            <Modal
                open={createConfirmOpen}
                onOpenChange={setCreateConfirmOpen}
                title="Confirm reminder"
                description="Review details before scheduling."
                size="md"
                footer={
                    <div className="flex items-center justify-end gap-2">
                        <Button variant="outline" onClick={() => setCreateConfirmOpen(false)}>
                            Back
                        </Button>
                        <Button onClick={confirmCreate} disabled={createM.isPending}>
                            Confirm
                        </Button>
                    </div>
                }
            >
                <div className="space-y-3 text-sm text-gray-200">
                    <div className="rounded-xl border border-white/10 bg-white/5 p-3">
                        <div className="text-xs font-semibold text-gray-300">Meeting</div>
                        <div className="mt-1 font-semibold text-white">{createSummary.meetingTitle}</div>
                    </div>
                    <div className="rounded-xl border border-white/10 bg-white/5 p-3">
                        <div className="text-xs font-semibold text-gray-300">Recipient</div>
                        <div className="mt-1 font-semibold text-white">{createSummary.to || "—"}</div>
                    </div>
                    <div className="rounded-xl border border-white/10 bg-white/5 p-3">
                        <div className="text-xs font-semibold text-gray-300">Scheduled time</div>
                        <div className="mt-1 font-semibold text-white">{createSummary.when}</div>
                    </div>
                    <div className="rounded-xl border border-white/10 bg-white/5 p-3">
                        <div className="text-xs font-semibold text-gray-300">Message</div>
                        <div className="mt-1 font-semibold text-white break-words">{createContent.trim() || "—"}</div>
                    </div>
                </div>
            </Modal>

            <Modal
                open={cancelOpen}
                onOpenChange={setCancelOpen}
                title="Cancel reminder"
                description="This action cannot be undone."
                size="md"
                footer={
                    <div className="flex items-center justify-end gap-2">
                        <Button variant="outline" onClick={() => setCancelOpen(false)}>
                            Keep
                        </Button>
                        <Button variant="destructive" onClick={confirmCancel} disabled={cancelM.isPending}>
                            Cancel reminder
                        </Button>
                    </div>
                }
            >
                <div className="text-sm text-gray-200">
                    {cancelId ? (
                        <CancelPreview reminders={q.data?.items ?? []} id={cancelId} />
                    ) : (
                        <div className="rounded-xl border border-white/10 bg-white/5 p-3">—</div>
                    )}
                </div>
            </Modal>
        </>
    );
}

export default function RemindersPage() {
    return (
        <DashboardLayout title="Reminders" description="Reminders grouped by meeting/contact with lifecycle status.">
            <RouteGuard title="Reminders" description="Reminders grouped by meeting/contact with lifecycle status." requiredConnectors={["calendar", "email"]}>
                <RemindersContent />
            </RouteGuard>
        </DashboardLayout>
    );
}

function CancelPreview({ reminders, id }: { reminders: Reminder[]; id: string }) {
    const r = reminders.find((x) => x.id === id);
    if (!r) return <div className="rounded-xl border border-white/10 bg-white/5 p-3">Reminder not found.</div>;
    return (
        <div className="space-y-2">
            <div className="rounded-xl border border-white/10 bg-white/5 p-3">
                <div className="text-xs font-semibold text-gray-300">Content</div>
                <div className="mt-1 font-semibold text-white break-words">{r.content}</div>
            </div>
            <div className="rounded-xl border border-white/10 bg-white/5 p-3">
                <div className="text-xs font-semibold text-gray-300">Scheduled</div>
                <div className="mt-1 font-semibold text-white">{formatIsoDateTime(r.scheduledAt)}</div>
            </div>
        </div>
    );
}
