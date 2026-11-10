"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Loader2, Trash2, Users } from "lucide-react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ReadError } from "@/components/ui/read-error";
import { Select } from "@/components/ui/select";
import { useAuth } from "@/hooks/useAuth";
import { isApiClientError } from "@/lib/http-client";
import { ASSIGNABLE_ROLES, roleName, teamApi, type AssignableRole, type TeamMember } from "@/lib/team-api";
import { formatTimestamp } from "@/lib/timestamps";

/**
 * QA F1-15 (QA-UI-151): team management had an API but no screen, so a
 * customer could not see who is in their workspace. Everyone can see the
 * active members; admins can change another member's role or remove them.
 */

// backend require_role(TENANT_ADMIN), after normalize_role's aliases.
const ADMIN_ROLES = new Set(["tenant_admin", "partner_admin", "platform_admin", "admin", "owner", "super_admin"]);
const TEAM_QUERY_KEY = ["team", "members"] as const;

type PendingChange = { kind: "role"; member: TeamMember; role: AssignableRole } | { kind: "remove"; member: TeamMember };

function displayName(member: TeamMember) {
    return member.name?.trim() || member.email;
}

export default function TeamPage() {
    const { user } = useAuth();
    const canManage = ADMIN_ROLES.has(user?.role ?? "");
    const ownEmail = (user?.email ?? "").toLowerCase();
    const qc = useQueryClient();
    const members = useQuery({ queryKey: TEAM_QUERY_KEY, queryFn: () => teamApi.list(), retry: false });
    const [pending, setPending] = useState<PendingChange | null>(null);

    async function confirmPending() {
        if (!pending) return;
        if (pending.kind === "role") await teamApi.changeRole(pending.member.id, pending.role);
        else await teamApi.remove(pending.member.id);
        setPending(null);
        await qc.invalidateQueries({ queryKey: TEAM_QUERY_KEY });
    }

    return (
        <DashboardLayout title="Team" description="The people in this workspace and what each of them can do.">
            <div className="mb-6">
                <Link href="/settings" className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
                    <ArrowLeft className="h-4 w-4" aria-hidden />
                    Back to settings
                </Link>
            </div>

            <section className="content-card" aria-labelledby="team-heading">
                <h2 id="team-heading" className="mb-4 text-base font-semibold text-foreground">Members</h2>
                {members.isLoading ? (
                    <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading the team…
                    </p>
                ) : members.isError ? (
                    <ReadError
                        resource="the team"
                        requestId={isApiClientError(members.error) ? members.error.requestId : undefined}
                        onRetry={() => { void members.refetch(); }}
                        busy={members.isFetching}
                    />
                ) : (members.data ?? []).length === 0 ? (
                    <p className="flex items-center gap-3 text-sm text-muted-foreground">
                        <Users className="h-5 w-5" aria-hidden /> No members to show.
                    </p>
                ) : (
                    <ul className="divide-y divide-border">
                        {(members.data ?? []).map((member) => {
                            const isSelf = member.email.toLowerCase() === ownEmail;
                            // Your own row and the workspace owner's are never changed from here.
                            const changeable = canManage && !isSelf && !member.is_primary;
                            const who = displayName(member);
                            return (
                                <li key={member.id} className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between">
                                    <div className="min-w-0">
                                        <p className="truncate font-medium text-foreground">
                                            {who}
                                            {isSelf ? <span className="ml-2 rounded-full bg-muted px-2 py-0.5 text-xs font-semibold text-muted-foreground">You</span> : null}
                                            {member.is_primary ? <span className="ml-2 rounded-full bg-muted px-2 py-0.5 text-xs font-semibold text-muted-foreground">Owner</span> : null}
                                        </p>
                                        <p className="truncate text-sm text-muted-foreground">
                                            {member.name ? `${member.email} · ` : ""}{roleName(member.role)}
                                            {member.status !== "active" ? ` · ${member.status}` : ""}
                                            {member.joined_at ? <> · joined <time dateTime={member.joined_at}>{formatTimestamp(member.joined_at, user?.account_timezone).date}</time></> : null}
                                        </p>
                                    </div>
                                    {changeable ? (
                                        <div className="flex items-center gap-2">
                                            <Select
                                                ariaLabel={`Role for ${who}`}
                                                value={member.role}
                                                onChange={(next) => {
                                                    if (next !== member.role) setPending({ kind: "role", member, role: next as AssignableRole });
                                                }}
                                                className="w-44"
                                            >
                                                {ASSIGNABLE_ROLES.map((role) => (
                                                    <option key={role} value={role}>{roleName(role)}</option>
                                                ))}
                                            </Select>
                                            <Button type="button" variant="ghost" size="sm" onClick={() => setPending({ kind: "remove", member })} aria-label={`Remove ${who}`}>
                                                <Trash2 className="h-4 w-4" aria-hidden />
                                                Remove
                                            </Button>
                                        </div>
                                    ) : null}
                                </li>
                            );
                        })}
                    </ul>
                )}
                {canManage ? (
                    <p className="mt-4 text-xs text-muted-foreground">Inviting new people by email is not available yet.</p>
                ) : null}
            </section>

            <ConfirmDialog
                open={pending !== null}
                onOpenChange={(open) => { if (!open) setPending(null); }}
                intent={pending?.kind === "remove" ? "delete" : "suspend"}
                title={pending?.kind === "remove" ? "Remove from this workspace?" : "Change this person's role?"}
                description={pending
                    ? pending.kind === "remove"
                        ? `${displayName(pending.member)} will lose access to this workspace.`
                        : `${displayName(pending.member)} will become ${roleName(pending.role)}.`
                    : undefined}
                warningText="The change applies at once, on every device they are signed in on."
                confirmLabel={pending?.kind === "remove" ? "Remove" : "Change role"}
                onConfirm={confirmPending}
            />
        </DashboardLayout>
    );
}
