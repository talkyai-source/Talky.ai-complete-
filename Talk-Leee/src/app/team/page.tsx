"use client";

import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { focusInviteForm, TeamInvites } from "@/components/team/team-invites";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Select } from "@/components/ui/select";
import { EmptyState, ErrorState, LoadingState, failedReadMessage, failedReadRequestId } from "@/components/states/page-states";
import { useAuth } from "@/hooks/useAuth";
import { backendApi } from "@/lib/backend-api";
import { isApiClientError } from "@/lib/http-client";
import type { TenantMember } from "@/lib/models";
import {
    canManageTeam,
    selectableTeamRoles,
    teamMemberDisplayName,
    teamQueryKeys,
    teamRoleLabel,
    teamStatusLabel,
    useTeamMembers,
    useTeamRoles,
} from "@/lib/team-management";
import { cn } from "@/lib/utils";

function formatError(err: unknown) {
    if (isApiClientError(err)) return err.message;
    return err instanceof Error ? err.message : "Request failed";
}

function statusClassName(status: string) {
    if (status === "active") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
    if (status === "pending") return "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300";
    if (status === "suspended") return "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300";
    return "border-border bg-muted text-muted-foreground";
}

type RowResult = { ok: true } | { ok: false; message: string };

// A role change and a removal are both asked about first (QA F1-15).
type PendingChange =
    | { kind: "role"; member: TenantMember; role: string }
    | { kind: "remove"; member: TenantMember };

function confirmCopy(pending: PendingChange | null) {
    if (!pending) return { title: "Remove", description: undefined, warningText: "", confirmLabel: "Remove" };
    const who = teamMemberDisplayName(pending.member);
    if (pending.kind === "role") {
        return {
            title: "Change this person's role?",
            description: `${who} will become ${teamRoleLabel(pending.role)}.`,
            warningText: "The change applies at once, on every device they are signed in on.",
            confirmLabel: "Change role",
        };
    }
    return {
        title: `Remove ${who}?`,
        description: `${who} will lose access to this workspace.`,
        warningText: `${who} (${pending.member.email})`,
        confirmLabel: "Remove",
    };
}

export default function TeamPage() {
    const qc = useQueryClient();
    const { user } = useAuth();
    const canManage = canManageTeam(user?.role);
    const ownEmail = (user?.email ?? "").toLowerCase();
    const membersQuery = useTeamMembers(canManage);
    const rolesQuery = useTeamRoles({ enabled: canManage });

    const [busyId, setBusyId] = useState<string | null>(null);
    const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
    const [pending, setPending] = useState<PendingChange | null>(null);

    const roleOptions = useMemo(() => selectableTeamRoles(rolesQuery.data, user?.role), [rolesQuery.data, user?.role]);

    const run = async (member: TenantMember, action: () => Promise<unknown>): Promise<RowResult> => {
        setBusyId(member.id);
        setRowErrors((prev) => {
            if (!(member.id in prev)) return prev;
            const next = { ...prev };
            delete next[member.id];
            return next;
        });
        try {
            await action();
            await qc.invalidateQueries({ queryKey: teamQueryKeys.members(canManage) });
            return { ok: true };
        } catch (err) {
            const message = formatError(err);
            setRowErrors((prev) => ({ ...prev, [member.id]: message }));
            return { ok: false, message };
        } finally {
            setBusyId(null);
        }
    };

    const members = membersQuery.data ?? [];
    const copy = confirmCopy(pending);

    return (
        <DashboardLayout title="Team">
            <div className="mx-auto w-full max-w-5xl space-y-6">
                <Card>
                    <CardHeader>
                        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                            <CardTitle>Team</CardTitle>
                            {/* Inviting by email (QA F1-15, D4-2): the form is further down this page. */}
                            {canManage ? (
                                <Button type="button" onClick={() => focusInviteForm()} data-testid="team-invite-button">
                                    Invite teammate
                                </Button>
                            ) : null}
                        </div>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {canManage && rolesQuery.isError ? (
                            <div role="alert" className="rounded-2xl border border-red-500/30 bg-background/70 p-4 text-sm text-red-500" data-testid="team-roles-error">
                                {failedReadMessage(rolesQuery.error, "Request failed")}
                            </div>
                        ) : null}

                        {membersQuery.isPending ? (
                            <LoadingState />
                        ) : membersQuery.isError ? (
                            <ErrorState requestId={failedReadRequestId(membersQuery.error)} message={failedReadMessage(membersQuery.error, "Request failed")} onRetry={() => { void membersQuery.refetch(); }} />
                        ) : members.length === 0 ? (
                            <EmptyState title="Try refreshing the page." message="" />
                        ) : (
                            <ul className="divide-y divide-border rounded-2xl border border-border" data-testid="team-member-list">
                                {members.map((member) => {
                                    const busy = busyId === member.id;
                                    const rowError = rowErrors[member.id];
                                    // Your own row is never changed from here; the server refuses it too.
                                    const isSelf = member.email.toLowerCase() === ownEmail;
                                    const changeable = canManage && !isSelf;
                                    const roleSelectable = changeable && roleOptions.some((r) => r.name === member.role);
                                    return (
                                        <li key={member.id} className="p-4" data-testid="team-member-row" data-member-id={member.id}>
                                            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                                                <div className="min-w-0">
                                                    <div className="flex min-w-0 items-center gap-2">
                                                        <div className="min-w-0 truncate text-sm font-semibold text-foreground" data-testid="team-member-name">{teamMemberDisplayName(member)}</div>
                                                        {isSelf ? <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-xs font-semibold text-muted-foreground">You</span> : null}
                                                    </div>
                                                    <div className="truncate text-xs text-muted-foreground" data-testid="team-member-email">{member.email}</div>
                                                </div>
                                                <div className="flex min-w-0 flex-wrap items-center gap-2 md:justify-end">
                                                    <span
                                                        className={cn("inline-flex items-center rounded-xl border px-2.5 py-1 text-[11px] font-semibold", statusClassName(member.status))}
                                                        data-testid="team-member-status"
                                                    >
                                                        {teamStatusLabel(member.status)}
                                                    </span>
                                                    {roleSelectable ? (
                                                        <Select
                                                            value={member.role}
                                                            onChange={(next) => {
                                                                if (next === member.role) return;
                                                                setPending({ kind: "role", member, role: next });
                                                            }}
                                                            ariaLabel={`Role: ${member.email}`}
                                                            disabled={busy}
                                                            className="w-full sm:w-48"
                                                        >
                                                            {roleOptions.map((role) => (
                                                                <option key={role.name} value={role.name}>{teamRoleLabel(role.name)}</option>
                                                            ))}
                                                        </Select>
                                                    ) : (
                                                        <span className="text-sm text-foreground" data-testid="team-member-role">{teamRoleLabel(member.role)}</span>
                                                    )}
                                                    {changeable ? (
                                                        <>
                                                            <Button
                                                                type="button"
                                                                variant="outline"
                                                                size="sm"
                                                                disabled={busy || member.status === "suspended"}
                                                                onClick={() => void run(member, () => backendApi.team.updateMember({ id: member.id, status: "suspended" }))}
                                                            >
                                                                Deactivate
                                                            </Button>
                                                            <Button
                                                                type="button"
                                                                variant="destructive"
                                                                size="sm"
                                                                disabled={busy}
                                                                onClick={() => setPending({ kind: "remove", member })}
                                                            >
                                                                Remove
                                                            </Button>
                                                        </>
                                                    ) : null}
                                                </div>
                                            </div>
                                            {rowError ? (
                                                <div role="alert" className="mt-3 break-words rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-300" data-testid="team-member-error">
                                                    {rowError}
                                                </div>
                                            ) : null}
                                        </li>
                                    );
                                })}
                            </ul>
                        )}
                    </CardContent>
                </Card>

                {canManage ? <TeamInvites timeZone={user?.account_timezone} /> : null}

                <ConfirmDialog
                    open={pending !== null}
                    onOpenChange={(open) => { if (!open) setPending(null); }}
                    intent={pending?.kind === "role" ? "suspend" : "delete"}
                    title={copy.title}
                    description={copy.description}
                    warningText={copy.warningText}
                    confirmLabel={copy.confirmLabel}
                    onConfirm={async () => {
                        if (!pending) return;
                        const { member } = pending;
                        const result = await run(member, () => (
                            pending.kind === "role"
                                ? backendApi.team.updateMember({ id: member.id, role_name: pending.role })
                                : backendApi.team.removeMember({ id: member.id })
                        ));
                        if (!result.ok) throw new Error(result.message);
                    }}
                />
            </div>
        </DashboardLayout>
    );
}
