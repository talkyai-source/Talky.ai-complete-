"use client";

/**
 * Contact lists row — one card per named list (each CSV upload / paste batch is
 * its own list). Each card shows the list name, contact count, an active/inactive
 * toggle (PATCH is_active) and a "Call this list" button (POST call → REAL
 * outbound calls, confirmed first). Active lists get the emerald "active" tint +
 * ring used elsewhere for the active provider; inactive lists are muted.
 *
 * The synthetic "ungrouped" list (NULL-list leads) can't be toggled or called,
 * so those controls are hidden for it.
 *
 * Self-fetching so it can be dropped onto both the /contacts home and the
 * campaign detail page. Fail-soft: a failed fetch shows an inline error strip
 * and never blanks the surrounding page. Toggles are optimistic with
 * revert-on-error + a toast; calls confirm() then toast the result.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, Loader2, Phone, Users } from "lucide-react";

import { Button } from "@/components/ui/button";
import { dashboardApi, ContactList } from "@/lib/dashboard-api";
import { notificationsStore } from "@/lib/notifications";

export const UNGROUPED_LIST_ID = "ungrouped";

function errText(err: unknown, fallback: string): string {
    return err instanceof Error ? err.message : fallback;
}

export function ContactLists({
    campaignId,
    refreshToken = 0,
    selectedListId = null,
    onSelectList,
    onListsLoaded,
    onCallStarted,
}: {
    campaignId: string;
    /** Bump to force a re-fetch (e.g. after a CSV import). */
    refreshToken?: number;
    /** Currently active filter list id (highlights the matching card). */
    selectedListId?: string | null;
    /** Click a card to filter contacts by that list. Passing the same id again clears it. */
    onSelectList?: (listId: string | null) => void;
    /** Reports the loaded lists to the parent (e.g. for an active-contacts summary). */
    onListsLoaded?: (lists: ContactList[]) => void;
    /** Fired after a "Call this list" enqueues jobs, so the parent can refresh stats. */
    onCallStarted?: () => void;
}) {
    const [lists, setLists] = useState<ContactList[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [togglingId, setTogglingId] = useState<string | null>(null);
    const [callingId, setCallingId] = useState<string | null>(null);

    // Keep callbacks in refs so they aren't effect dependencies (parents often
    // pass fresh closures each render).
    const loadedCb = useRef(onListsLoaded);
    loadedCb.current = onListsLoaded;

    const load = useCallback(async () => {
        try {
            setLoading(true);
            setError("");
            const data = await dashboardApi.listContactLists(campaignId);
            setLists(Array.isArray(data) ? data : []);
        } catch (err) {
            setError(errText(err, "Failed to load contact lists"));
        } finally {
            setLoading(false);
        }
    }, [campaignId]);

    useEffect(() => {
        void load();
    }, [load, refreshToken]);

    // Report every lists change up (initial load, toggles, refreshes).
    useEffect(() => {
        loadedCb.current?.(lists);
    }, [lists]);

    async function handleToggle(list: ContactList) {
        if (list.id === UNGROUPED_LIST_ID || togglingId) return;
        const next = !list.is_active;
        setTogglingId(list.id);
        // Optimistic flip.
        setLists((prev) => prev.map((l) => (l.id === list.id ? { ...l, is_active: next } : l)));
        try {
            const updated = await dashboardApi.updateContactList(list.id, next);
            setLists((prev) => prev.map((l) => (l.id === list.id ? { ...l, ...updated } : l)));
            notificationsStore.create({
                type: "success",
                title: next ? "List activated" : "List paused",
                message: `“${list.name}” is now ${next ? "active" : "inactive"}.`,
            });
        } catch (err) {
            // Revert to the server-truth value we started from.
            setLists((prev) => prev.map((l) => (l.id === list.id ? { ...l, is_active: list.is_active } : l)));
            notificationsStore.create({
                type: "error",
                title: "Couldn't update list",
                message: errText(err, "The active state was reverted."),
            });
        } finally {
            setTogglingId(null);
        }
    }

    async function handleCall(list: ContactList) {
        if (list.id === UNGROUPED_LIST_ID || callingId) return;
        const ok = window.confirm(
            `Call all eligible contacts in “${list.name}” now?\n\n` +
            "This places REAL outbound calls immediately — just like starting the campaign.",
        );
        if (!ok) return;
        setCallingId(list.id);
        try {
            const res = await dashboardApi.callContactList(list.id);
            notificationsStore.create({
                type: res.jobs_enqueued > 0 ? "success" : "warning",
                title: res.jobs_enqueued > 0 ? "Calls started" : "No calls placed",
                message:
                    res.message ||
                    `${res.jobs_enqueued} call${res.jobs_enqueued === 1 ? "" : "s"} queued from ${res.eligible_count} eligible contact${res.eligible_count === 1 ? "" : "s"}.`,
            });
            onCallStarted?.();
        } catch (err) {
            notificationsStore.create({
                type: "error",
                title: "Couldn't start calls",
                message: errText(err, "The list was not called."),
            });
        } finally {
            setCallingId(null);
        }
    }

    if (loading) {
        return (
            <div className="flex items-center gap-2 rounded-2xl border border-border bg-muted/30 px-4 py-6 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading contact lists…
            </div>
        );
    }

    // Fail-soft: an error strip, but never a blanked page.
    if (error) {
        return (
            <div className="flex items-center justify-between gap-3 rounded-2xl border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                <span className="flex items-center gap-2">
                    <AlertCircle className="h-4 w-4 shrink-0" /> {error}
                </span>
                <Button variant="outline" size="sm" onClick={() => void load()}>Retry</Button>
            </div>
        );
    }

    if (lists.length === 0) return null;

    return (
        <div className="space-y-2">
            <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-foreground">Contact lists</h3>
                {selectedListId && (
                    <button
                        type="button"
                        onClick={() => onSelectList?.(null)}
                        className="text-xs font-medium text-emerald-700 hover:underline dark:text-emerald-400"
                    >
                        Show all contacts
                    </button>
                )}
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {lists.map((list) => {
                    const active = list.is_active;
                    const isUngrouped = list.id === UNGROUPED_LIST_ID;
                    const selected = selectedListId === list.id;
                    return (
                        <div
                            key={list.id}
                            className={`flex flex-col justify-between rounded-2xl border p-4 shadow-sm transition-colors ${
                                active
                                    ? "border-emerald-500/40 bg-emerald-500/5"
                                    : "border-border bg-muted/40"
                            } ${selected ? "ring-2 ring-emerald-500/60" : active ? "ring-1 ring-emerald-500/20" : ""}`}
                        >
                            <button
                                type="button"
                                onClick={() => onSelectList?.(selected ? null : list.id)}
                                className="group text-left"
                                title="Filter contacts by this list"
                            >
                                <div className="flex items-start justify-between gap-2">
                                    <span className="line-clamp-2 text-sm font-semibold text-foreground group-hover:underline">
                                        {list.name}
                                    </span>
                                    {!isUngrouped && (
                                        <span
                                            className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${
                                                active
                                                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                                                    : "border-border bg-muted text-muted-foreground"
                                            }`}
                                        >
                                            {active ? "Active" : "Inactive"}
                                        </span>
                                    )}
                                </div>
                                <div className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
                                    <Users className="h-3.5 w-3.5" />
                                    <span className="tabular-nums">
                                        {list.contact_count.toLocaleString()} contact{list.contact_count === 1 ? "" : "s"}
                                    </span>
                                </div>
                            </button>

                            {!isUngrouped && (
                                <div className="mt-4 flex items-center justify-between gap-2">
                                    {/* Active/inactive toggle */}
                                    <button
                                        type="button"
                                        role="switch"
                                        aria-checked={active}
                                        aria-label={`Set “${list.name}” ${active ? "inactive" : "active"}`}
                                        disabled={togglingId === list.id}
                                        onClick={() => void handleToggle(list)}
                                        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-60 ${
                                            active ? "bg-emerald-500" : "bg-muted-foreground/30"
                                        }`}
                                    >
                                        <span
                                            className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
                                                active ? "translate-x-5" : "translate-x-0.5"
                                            }`}
                                        />
                                    </button>

                                    <Button
                                        size="sm"
                                        variant={active ? "default" : "outline"}
                                        disabled={callingId === list.id}
                                        onClick={() => void handleCall(list)}
                                    >
                                        {callingId === list.id ? (
                                            <Loader2 className="h-4 w-4 animate-spin" />
                                        ) : (
                                            <Phone className="h-4 w-4" />
                                        )}
                                        Call this list
                                    </Button>
                                </div>
                            )}
                            {isUngrouped && (
                                <p className="mt-4 text-[11px] text-muted-foreground">
                                    Leads not tied to an uploaded list. Import a CSV to group them.
                                </p>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

/**
 * Compact active-contacts summary for the campaign start surface — shows which
 * lists are active and their total contacts, with a "View more" that expands
 * to the per-list breakdown / links out to the full lists row.
 */
export function ActiveContactsSummary({
    lists,
    onViewMore,
}: {
    lists: ContactList[];
    onViewMore?: () => void;
}) {
    const [expanded, setExpanded] = useState(false);
    const activeLists = lists.filter((l) => l.is_active);
    const activeContacts = activeLists.reduce((sum, l) => sum + (l.contact_count || 0), 0);

    if (lists.length === 0) return null;

    return (
        <div className="rounded-2xl border border-emerald-500/30 bg-emerald-500/5 p-4 shadow-sm">
            <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2.5">
                    <div className="rounded-lg bg-emerald-500/10 p-2">
                        <Users className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
                    </div>
                    <div>
                        <p className="text-lg font-bold tabular-nums text-emerald-700 dark:text-emerald-300">
                            {activeContacts.toLocaleString()}
                        </p>
                        <p className="text-xs text-muted-foreground">
                            active contact{activeContacts === 1 ? "" : "s"} across {activeLists.length} of {lists.length} list{lists.length === 1 ? "" : "s"} — these get dialed on Start
                        </p>
                    </div>
                </div>
                <button
                    type="button"
                    onClick={() => {
                        setExpanded((v) => !v);
                        onViewMore?.();
                    }}
                    className="shrink-0 text-xs font-semibold text-emerald-700 hover:underline dark:text-emerald-400"
                >
                    {expanded ? "Hide" : "View more"}
                </button>
            </div>

            {expanded && (
                <ul className="mt-3 space-y-1 border-t border-emerald-500/20 pt-3">
                    {lists.map((l) => (
                        <li key={l.id} className="flex items-center justify-between text-xs">
                            <span className="flex items-center gap-2">
                                <span
                                    className={`h-2 w-2 rounded-full ${l.is_active ? "bg-emerald-500" : "bg-muted-foreground/40"}`}
                                    aria-hidden
                                />
                                <span className={l.is_active ? "text-foreground" : "text-muted-foreground"}>{l.name}</span>
                            </span>
                            <span className="tabular-nums text-muted-foreground">
                                {l.contact_count.toLocaleString()}
                            </span>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}
