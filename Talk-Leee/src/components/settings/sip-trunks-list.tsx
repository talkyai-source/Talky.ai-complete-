"use client";

import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Plus, Loader2, ServerCog } from "lucide-react";
import {
    useSipTrunks,
    useTestSipTrunk,
    useActivateSipTrunk,
    useDeactivateSipTrunk,
    useDeleteSipTrunk,
    usePoolTrunks,
    usePoolAssignment,
    useSetPoolAssignment,
    useOutboundRoute,
    type OutboundRoute,
    type SipTrunkRow,
} from "@/lib/telephony-api";
import { useNotificationsActions } from "@/lib/notifications-client";
import { PbxLineCard, useNow } from "@/components/settings/pbx-line-card";
import { PbxLineDialog, type SavedLine } from "@/components/settings/pbx-line-dialog";
import type { LineTestState } from "@/components/settings/pbx-test-result";

/**
 * Shared trunk pool selector — allot one of the platform's registered pool
 * accounts (e.g. 150001–150004) to this tenant. When set, this tenant's
 * outbound calls dial on that account with no own trunk / registration needed.
 */
function PoolAccountSelector() {
    const { create: createNotification } = useNotificationsActions();
    const poolQuery = usePoolTrunks();
    const assignmentQuery = usePoolAssignment();
    const setAssignment = useSetPoolAssignment();

    const pool = poolQuery.data ?? [];
    const current = assignmentQuery.data?.pool_trunk_id ?? "";

    // Nothing to show until the platform actually has pool accounts.
    if (!poolQuery.isLoading && pool.length === 0) return null;

    async function onChange(value: string) {
        try {
            await setAssignment.mutateAsync(value || null);
            createNotification({
                type: "success",
                title: value ? "Shared account assigned" : "Shared account cleared",
                message: value
                    ? "This tenant will now dial on the selected pool account."
                    : "This tenant will use its own trunk (or the default).",
            });
        } catch (e) {
            createNotification({
                type: "error",
                title: "Couldn't update",
                message: e instanceof Error ? e.message : "Failed to set the shared account.",
            });
        }
    }

    return (
        <div className="mb-4 rounded-lg border border-border bg-muted/30 p-3">
            <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:justify-between">
                <div>
                    <div className="text-sm font-medium text-foreground">Shared trunk account</div>
                    <div className="text-xs text-muted-foreground">
                        Dial on one of the platform&apos;s registered pool accounts — no setup or
                        registration needed. Overrides your own trunks when set.
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    <Select
                        ariaLabel="Shared trunk account"
                        value={current}
                        onChange={(next) => void onChange(next)}
                        disabled={setAssignment.isPending || poolQuery.isLoading}
                        className="min-w-[14rem]"
                    >
                        <option value="">— None (use my own trunk) —</option>
                        {pool.map((p) => (
                            // One string: the Select reads an option's label from a single string child,
                            // and several JSX children were joined with commas ("Pool 150001, · +1…,,").
                            <option key={p.id} value={p.id} disabled={!p.runtime_ready}>
                                {`${p.label}${p.caller_id ? ` · ${p.caller_id}` : ""}${
                                    p.registration_status && p.registration_status !== "registered" ? ` · ${p.registration_status}` : ""
                                }${!p.runtime_ready ? ` · unavailable: ${p.runtime_status_detail}` : ""}`}
                            </option>
                        ))}
                    </Select>
                    {setAssignment.isPending && (
                        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden />
                    )}
                </div>
            </div>
        </div>
    );
}

/** The number outbound calls really go out from, decided by the dialer's own rules. */
function OutboundRouteSummary({ route }: { route?: OutboundRoute }) {
    // A cloud-provider account does not dial through these SIP lines.
    if (!route || typeof route.ready !== "boolean" || route.reason_code === "sip_not_required") return null;
    return (
        <div role="status" className="mb-4 rounded-lg border border-border bg-muted/30 p-3 text-sm text-foreground">
            {route.ready && route.caller_id ? (
                <>
                    Outbound calls go out from <strong>{route.caller_id}</strong>
                    {route.trunk_name ? <> on <strong>{route.trunk_name}</strong></> : " on the shared line"}.
                </>
            ) : (
                <>
                    Outbound calls can&apos;t go out right now{route.reason ? `: ${route.reason}` : "."}
                    {route.caller_id ? <> They would show <strong>{route.caller_id}</strong>.</> : null}
                </>
            )}
        </div>
    );
}

type DialogState = { session: number; open: boolean; mode: "create" | "edit"; trunk: SipTrunkRow | null };

function errorText(e: unknown, fallback: string): string {
    return e instanceof Error && e.message ? e.message : fallback;
}

export function SipTrunksList() {
    const { create: createNotification } = useNotificationsActions();
    const trunksQuery = useSipTrunks();
    const outboundRoute = useOutboundRoute().data;
    const testMutation = useTestSipTrunk();
    const activateMutation = useActivateSipTrunk();
    const deactivateMutation = useDeactivateSipTrunk();
    const deleteMutation = useDeleteSipTrunk();
    const now = useNow();

    // `session` changes on every open so the dialog always starts from a fresh form.
    const [dialog, setDialog] = useState<DialogState>({ session: 0, open: false, mode: "create", trunk: null });
    const [deletingTrunk, setDeletingTrunk] = useState<SipTrunkRow | null>(null);
    const [togglingId, setTogglingId] = useState<string | null>(null);
    // What each line's last Test showed, by line id. Only what this page ran or was just asked to run.
    const [tests, setTests] = useState<Record<string, LineTestState>>({});

    const trunks: SipTrunkRow[] = trunksQuery.data ?? [];

    function openCreate() {
        setDialog((d) => ({ session: d.session + 1, open: true, mode: "create", trunk: null }));
    }

    function openEdit(t: SipTrunkRow) {
        setDialog((d) => ({ session: d.session + 1, open: true, mode: "edit", trunk: t }));
    }

    function setTest(id: string, state: LineTestState | null) {
        setTests((current) => {
            const next = { ...current };
            if (state) next[id] = state;
            else delete next[id];
            return next;
        });
    }

    /** Runs the real server-side check for one line and shows exactly what it answered. */
    async function runTest(id: string) {
        setTest(id, { phase: "running" });
        try {
            const result = await testMutation.mutateAsync(id);
            setTest(id, { phase: "done", result });
        } catch (e: unknown) {
            setTest(id, { phase: "error", message: errorText(e, "The connection check failed. Please try again.") });
        }
    }

    function handleSaved(saved: SavedLine) {
        createNotification({
            type: "success",
            title: saved.mode === "create" ? "PBX connection saved" : "Changes saved",
            message: saved.mode === "create"
                ? `${saved.name} is saved. Checking the connection now.`
                : `${saved.name} was updated. Checking the connection now.`,
        });
        if (saved.id) void runTest(saved.id);
    }

    async function handleToggle(t: SipTrunkRow) {
        setTogglingId(t.id);
        try {
            if (t.is_active) {
                await deactivateMutation.mutateAsync(t.id);
                createNotification({
                    type: "success",
                    title: `${t.trunk_name} is off`,
                    message: "Calls no longer use this line.",
                });
            } else {
                const enabled = await activateMutation.mutateAsync(t.id);
                createNotification({
                    type: enabled.runtime_ready ? "success" : "warning",
                    title: `${t.trunk_name} is on`,
                    message: enabled.runtime_ready
                        ? "Connected and ready for calls."
                        : "Connecting to your provider. This usually takes a few seconds.",
                });
            }
        } catch (e: unknown) {
            createNotification({ type: "error", title: "Couldn't change the line", message: errorText(e, "Please try again.") });
        } finally {
            setTogglingId(null);
        }
    }

    /**
     * One call: the server switches the line off, unpins campaigns, disconnects its
     * incoming numbers and pauses the inbound campaigns that answered them.
     */
    async function handleDelete(t: SipTrunkRow) {
        const result = await deleteMutation.mutateAsync(t.id);
        setTest(t.id, null);
        const disconnected = result?.archived_inbound_numbers ?? 0;
        const paused = result?.paused_inbound_campaigns ?? 0;
        const message = [
            `${t.trunk_name} was removed.`,
            disconnected > 0 ? `${disconnected} incoming number${disconnected === 1 ? "" : "s"} disconnected.` : null,
            paused > 0 ? `${paused} inbound campaign${paused === 1 ? "" : "s"} paused.` : null,
        ].filter(Boolean).join(" ");
        createNotification({ type: "success", title: "Line deleted.", message });
    }

    return (
        <Card>
            <CardHeader>
                {/*
                  * Below lg: stacked (title, full-width description, button bottom-right).
                  * lg and up: button sits top-right on the title's row; description
                  * spans the full width on the row below. DOM order is title,
                  * description, button in both.
                  */}
                <div className="grid grid-cols-1 gap-y-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:gap-x-4 lg:gap-y-1.5">
                    <CardTitle className="flex min-w-0 items-center gap-2 text-[14px] sm:text-xl sm:leading-none lg:col-start-1 lg:row-start-1 lg:self-center">
                        <ServerCog className="h-4 w-4 shrink-0 sm:h-5 sm:w-5" aria-hidden /> Your PBX
                    </CardTitle>
                    <CardDescription className="lg:col-span-2 lg:row-start-2">
                        Connect your own PBX or phone provider so your AI agent can place and receive
                        calls on your numbers. Add the details your provider gave you and we&apos;ll
                        test the connection for you.
                    </CardDescription>
                    <Button
                        onClick={openCreate}
                        size="sm"
                        className="h-8 justify-self-end lg:col-start-2 lg:row-start-1 lg:h-7 lg:gap-1.5 lg:self-center lg:px-2.5 lg:[&_svg]:size-3.5"
                    >
                        <Plus className="mr-1 h-4 w-4" aria-hidden /> Connect PBX
                    </Button>
                </div>
            </CardHeader>
            <CardContent>
                <OutboundRouteSummary route={outboundRoute} />
                <PoolAccountSelector />
                {trunksQuery.isLoading ? (
                    <div className="flex items-center justify-center py-8 text-muted-foreground">
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Loading your PBX connections…
                    </div>
                ) : trunksQuery.isError && !trunksQuery.data ? (
                    <div role="alert" className="py-4 text-sm text-destructive">Could not load your PBX connections. <Button variant="outline" size="sm" onClick={() => void trunksQuery.refetch()}>Retry</Button></div>
                ) : trunks.length === 0 ? (
                    <div className="py-8 text-center text-sm text-muted-foreground">
                        No PBX connected yet. Select <strong>Connect PBX</strong> and enter the address,
                        username and password your provider gave you.
                    </div>
                ) : (
                    <div className="space-y-3">
                        {trunksQuery.isError ? (
                            <div role="status" className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
                                Couldn&apos;t refresh the latest status. Showing the last known state.
                            </div>
                        ) : null}
                        <ul className="space-y-3" aria-label="Your PBX connections">
                            {trunks.map((t) => (
                                <PbxLineCard
                                    key={t.id}
                                    trunk={t}
                                    now={now}
                                    usedForOutbound={Boolean(outboundRoute?.ready && outboundRoute.trunk_id === t.id)}
                                    test={tests[t.id]}
                                    toggling={togglingId === t.id}
                                    deleteBusy={deleteMutation.isPending}
                                    onTest={(line) => void runTest(line.id)}
                                    onEdit={openEdit}
                                    onToggle={(line) => void handleToggle(line)}
                                    onDelete={setDeletingTrunk}
                                    onDismissTest={(line) => setTest(line.id, null)}
                                />
                            ))}
                        </ul>
                    </div>
                )}
            </CardContent>

            <ConfirmDialog
                open={Boolean(deletingTrunk)}
                onOpenChange={(open) => { if (!open) setDeletingTrunk(null); }}
                intent="delete"
                title={deletingTrunk ? `Delete ${deletingTrunk.trunk_name}?` : "Delete line?"}
                warningText="Calls stop using this line, incoming numbers on it stop ringing, and their inbound campaigns are paused. Campaigns pinned to it switch to your default line, and call history is kept."
                confirmLabel="Delete line"
                pendingLabel="Deleting…"
                onConfirm={async () => { if (deletingTrunk) await handleDelete(deletingTrunk); }}
            />

            <PbxLineDialog
                key={dialog.session}
                open={dialog.open}
                mode={dialog.mode}
                trunk={dialog.trunk}
                onOpenChange={(open) => setDialog((d) => ({ ...d, open }))}
                onSaved={handleSaved}
            />
        </Card>
    );
}
