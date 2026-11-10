"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Loader2, RotateCw, Send, Trash2, Users, X } from "lucide-react";
import { z } from "zod";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ReadError } from "@/components/ui/read-error";
import { Select } from "@/components/ui/select";
import { useAuth } from "@/hooks/useAuth";
import { isApiClientError } from "@/lib/http-client";
import { ASSIGNABLE_ROLES, roleName, teamApi, type AssignableRole, type TeamInvite, type TeamMember } from "@/lib/team-api";
import { formatTimestamp } from "@/lib/timestamps";

/**
 * QA F1-15 (QA-UI-151): team management had an API but no screen, so a
 * customer could not see who is in their workspace. Everyone can see the
 * active members; admins can change another member's role, remove them,
 * and invite someone by email (D4-2).
 */

// backend require_role(TENANT_ADMIN), after normalize_role's aliases.
const ADMIN_ROLES = new Set(["tenant_admin", "partner_admin", "platform_admin", "admin", "owner", "super_admin"]);
const TEAM_QUERY_KEY = ["team", "members"] as const;
const INVITES_QUERY_KEY = ["team", "invites"] as const;
const emailSchema = z.string().email();

type PendingChange =
    | { kind: "role"; member: TeamMember; role: AssignableRole }
    | { kind: "remove"; member: TeamMember }
    | { kind: "withdraw"; invite: TeamInvite };
type Notice = { tone: "success" | "warning" | "error"; text: string };
const NOTICE_TONE: Record<Notice["tone"], string> = {
    success: "text-emerald-700 dark:text-emerald-400",
    warning: "text-amber-700 dark:text-amber-400",
    error: "text-destructive",
};

function displayName(member: TeamMember) {
    return member.name?.trim() || member.email;
}

export default function TeamPage() {
    const { user } = useAuth();
    const canManage = ADMIN_ROLES.has(user?.role ?? "");
    const ownEmail = (user?.email ?? "").toLowerCase();
    const qc = useQueryClient();
    const members = useQuery({ queryKey: TEAM_QUERY_KEY, queryFn: () => teamApi.list(), retry: false });
    const invites = useQuery({ queryKey: INVITES_QUERY_KEY, queryFn: () => teamApi.listInvites(), retry: false, enabled: canManage });
    const [pending, setPending] = useState<PendingChange | null>(null);
    const [inviteEmail, setInviteEmail] = useState("");
    const [inviteRole, setInviteRole] = useState<AssignableRole>("user");
    const [sending, setSending] = useState(false);
    const [notice, setNotice] = useState<Notice | null>(null);

    async function confirmPending() {
        if (!pending) return;
        if (pending.kind === "role") await teamApi.changeRole(pending.member.id, pending.role);
        else if (pending.kind === "remove") await teamApi.remove(pending.member.id);
        else await teamApi.withdrawInvite(pending.invite.id);
        const changed = pending.kind === "withdraw" ? INVITES_QUERY_KEY : TEAM_QUERY_KEY;
        setPending(null);
        await qc.invalidateQueries({ queryKey: changed });
    }

    async function sendInvite(email: string, role: AssignableRole) {
        setSending(true);
        setNotice(null);
        try {
            const sent = await teamApi.invite(email, role);
            setNotice(sent.email_sent
                ? { tone: "success", text: `Invite sent to ${sent.email}.` }
                : { tone: "warning", text: `The invite to ${sent.email} was saved, but the email did not go out. Use Resend to try again.` });
            return true;
        } catch (err) {
            setNotice({ tone: "error", text: isApiClientError(err) ? err.message : "Could not send the invite. Try again." });
            return false;
        } finally {
            setSending(false);
            await qc.invalidateQueries({ queryKey: INVITES_QUERY_KEY });
        }
    }

    async function onInvite(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        const email = inviteEmail.trim().toLowerCase();
        if (!emailSchema.safeParse(email).success) {
            setNotice({ tone: "error", text: "Enter a valid email address." });
            return;
        }
        if (await sendInvite(email, inviteRole)) setInviteEmail("");
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
                            // Your own row is never changed from here; the server refuses it too.
                            // is_primary is the member's home workspace, not ownership.
                            const changeable = canManage && !isSelf;
                            const who = displayName(member);
                            return (
                                <li key={member.id} className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between">
                                    <div className="min-w-0">
                                        <p className="truncate font-medium text-foreground">
                                            {who}
                                            {isSelf ? <span className="ml-2 rounded-full bg-muted px-2 py-0.5 text-xs font-semibold text-muted-foreground">You</span> : null}
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
            </section>

            {canManage ? (
                <section className="content-card mt-6" aria-labelledby="invite-heading">
                    <h2 id="invite-heading" className="mb-4 text-base font-semibold text-foreground">Invite someone</h2>
                    <form className="flex flex-col gap-3 sm:flex-row sm:items-end" onSubmit={onInvite} noValidate>
                        <div className="flex-1 space-y-1.5">
                            <Label htmlFor="invite-email">Email</Label>
                            <Input
                                id="invite-email"
                                type="email"
                                autoComplete="off"
                                value={inviteEmail}
                                onChange={(event) => setInviteEmail(event.target.value)}
                                placeholder="name@company.com"
                            />
                        </div>
                        <Select ariaLabel="Role for the new person" value={inviteRole} onChange={(next) => setInviteRole(next as AssignableRole)} className="w-44">
                            {ASSIGNABLE_ROLES.map((role) => (
                                <option key={role} value={role}>{roleName(role)}</option>
                            ))}
                        </Select>
                        <Button type="submit" disabled={sending}>
                            {sending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Send className="h-4 w-4" aria-hidden />}
                            Send invite
                        </Button>
                    </form>
                    {notice ? (
                        <p role={notice.tone === "error" ? "alert" : "status"} className={`mt-3 text-sm ${NOTICE_TONE[notice.tone]}`}>{notice.text}</p>
                    ) : null}
                    <p className="mt-3 text-xs text-muted-foreground">The link works for 7 days and only once. Someone who already has an account cannot be invited.</p>

                    <h3 className="mb-2 mt-6 text-sm font-semibold text-foreground">Waiting to join</h3>
                    {invites.isLoading ? (
                        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
                            <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading invites…
                        </p>
                    ) : invites.isError ? (
                        <ReadError
                            resource="the invites"
                            requestId={isApiClientError(invites.error) ? invites.error.requestId : undefined}
                            onRetry={() => { void invites.refetch(); }}
                            busy={invites.isFetching}
                        />
                    ) : (invites.data ?? []).length === 0 ? (
                        <p className="text-sm text-muted-foreground">No open invites.</p>
                    ) : (
                        <ul className="divide-y divide-border">
                            {(invites.data ?? []).map((invite) => (
                                <li key={invite.id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between">
                                    <div className="min-w-0">
                                        <p className="truncate font-medium text-foreground">{invite.email}</p>
                                        <p className="text-sm text-muted-foreground">
                                            {roleName(invite.role)} · link works until{" "}
                                            <time dateTime={invite.expires_at}>{formatTimestamp(invite.expires_at, user?.account_timezone).date}</time>
                                        </p>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="sm"
                                            disabled={sending}
                                            onClick={() => { void sendInvite(invite.email, invite.role as AssignableRole); }}
                                            aria-label={`Resend the invite to ${invite.email}`}
                                        >
                                            <RotateCw className="h-4 w-4" aria-hidden />
                                            Resend
                                        </Button>
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="sm"
                                            onClick={() => setPending({ kind: "withdraw", invite })}
                                            aria-label={`Withdraw the invite to ${invite.email}`}
                                        >
                                            <X className="h-4 w-4" aria-hidden />
                                            Withdraw
                                        </Button>
                                    </div>
                                </li>
                            ))}
                        </ul>
                    )}
                </section>
            ) : null}

            <ConfirmDialog
                open={pending !== null}
                onOpenChange={(open) => { if (!open) setPending(null); }}
                intent={pending?.kind === "role" ? "suspend" : "delete"}
                title={pending?.kind === "withdraw"
                    ? "Withdraw this invite?"
                    : pending?.kind === "remove" ? "Remove from this workspace?" : "Change this person's role?"}
                description={pending
                    ? pending.kind === "withdraw"
                        ? `The invite link sent to ${pending.invite.email} will stop working.`
                        : pending.kind === "remove"
                            ? `${displayName(pending.member)} will lose access to this workspace.`
                            : `${displayName(pending.member)} will become ${roleName(pending.role)}.`
                    : undefined}
                warningText={pending?.kind === "withdraw"
                    ? "You can invite them again later."
                    : "The change applies at once, on every device they are signed in on."}
                confirmLabel={pending?.kind === "withdraw" ? "Withdraw" : pending?.kind === "remove" ? "Remove" : "Change role"}
                onConfirm={confirmPending}
            />
        </DashboardLayout>
    );
}
