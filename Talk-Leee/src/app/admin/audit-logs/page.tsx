"use client";

/** A validated, identity-owned view of the most recent 200 canonical records.
 * Category buttons filter this loaded page only; they do not search all history.
 */

import { useMemo, useState } from "react";
import { AlertTriangle, Filter, Loader2, Shield } from "lucide-react";

import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useAuditLogs } from "@/lib/api-hooks";
import type { AuditLogEvent } from "@/lib/models";

/**
 * The server's vocabulary, not a friendlier one invented here. `EventCategory`
 * and `Severity` are upper-case enums in app/domain/services/audit_logger.py;
 * renaming them in the UI would mean a value that appears on screen cannot be
 * pasted back into a filter or a log search.
 */
const CATEGORIES = [
  "AUTHENTICATION",
  "AUTHORIZATION",
  "USER_MANAGEMENT",
  "TENANT_ADMIN",
  "SECURITY",
  "DATA_ACCESS",
  "SYSTEM",
] as const;

const SEVERITY_CLASS: Record<string, string> = {
  CRITICAL: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400",
  HIGH: "border-orange-500/30 bg-orange-500/10 text-orange-700 dark:text-orange-400",
  MEDIUM: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  LOW: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400",
  INFO: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400",
};

const UNKNOWN_CLASS = "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400";

function formatTimestamp(iso: string) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return (
    d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) +
    " " +
    d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
  );
}

function badge(value: string, className: string) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold ${className}`}>
      {value}
    </span>
  );
}

/** Actors arrive as an id and a type; there is no display name on this endpoint. */
function actorLabel(log: AuditLogEvent) {
  return log.actor.id ? `${log.actor.type}: ${log.actor.id}` : `${log.actor.type} (ID unavailable)`;
}

export default function AuditLogsPage() {
  const [categoryFilter, setCategoryFilter] = useState<string>("all");

  const logsQuery = useAuditLogs({ pageSize: 200 });
  const logs = useMemo(() => logsQuery.data?.items ?? [], [logsQuery.data]);
  const filteredLogs = useMemo(
    () => (categoryFilter === "all" ? logs : logs.filter((log) => log.eventCategory === categoryFilter)),
    [logs, categoryFilter],
  );

  return (
    <DashboardLayout title="Audit Logs" description="Audit activity available to the current session. Up to 200 recent events; categories filter this loaded page.">
      <div className="space-y-6">
        {/* Filters */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex flex-wrap items-center gap-2">
              <Filter className="h-4 w-4 text-muted-foreground" aria-hidden />
              <span className="mr-2 text-xs font-semibold text-muted-foreground">Filter by category:</span>
              {(["all", ...CATEGORIES] as const).map((cat) => (
                <Button
                  key={cat}
                  variant={categoryFilter === cat ? "default" : "outline"}
                  size="sm"
                  onClick={() => setCategoryFilter(cat)}
                  className="text-xs"
                >
                  {cat === "all" ? "All" : cat.replace(/_/g, " ").toLowerCase()}
                </Button>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Log Table */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Shield className="h-5 w-5" aria-hidden /> Audit Trail
            </CardTitle>
            <CardDescription>
              {logsQuery.isPending
                ? "Loading…"
                : logsQuery.isError
                  ? "Could not be loaded"
                  : `${filteredLogs.length} event${filteredLogs.length !== 1 ? "s" : ""} shown of ${logs.length} most recent`}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {logsQuery.isPending && (
              <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading audit events…
              </div>
            )}

            {/* An error is reported as an error. It is never allowed to fall
                through to the empty state, which would claim the tenant has a
                clean audit history when in fact nothing was read. */}
            {logsQuery.isError && (
              <div className="flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 text-sm">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" aria-hidden />
                <div className="space-y-2">
                  <p className="text-foreground">The audit log could not be loaded, so none of it is shown.</p>
                  <p className="text-xs text-muted-foreground">
                    {logsQuery.error instanceof Error ? logsQuery.error.message : "Request failed"}
                    {" — reading the audit trail needs the audit:read permission."}
                  </p>
                  <Button variant="outline" size="sm" onClick={() => void logsQuery.refetch()}>
                    Try again
                  </Button>
                </div>
              </div>
            )}

            {logsQuery.isSuccess && filteredLogs.length > 0 && (
              <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                      <th className="px-4 py-3">Timestamp</th>
                      <th className="px-4 py-3">Actor</th>
                      <th className="px-4 py-3">Action</th>
                      <th className="px-4 py-3">Category</th>
                      <th className="px-4 py-3">Details</th>
                      <th className="px-4 py-3">IP Address</th>
                      <th className="px-4 py-3">Severity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredLogs.map((log) => (
                      <tr key={log.id} className="border-b border-border transition-colors last:border-b-0 hover:bg-muted/20">
                        <td className="whitespace-nowrap px-4 py-3 text-xs text-muted-foreground">{formatTimestamp(log.timestamp)}</td>
                        <td className="px-4 py-3">
                          <div className="break-all font-mono text-xs text-foreground">{actorLabel(log)}</div>
                        </td>
                        <td className="px-4 py-3">
                          <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-foreground">{log.actionType}</code>
                        </td>
                        <td className="px-4 py-3">{badge(log.eventCategory, UNKNOWN_CLASS)}</td>
                        <td className="max-w-[300px] px-4 py-3 text-xs text-muted-foreground">{log.description ?? log.eventType}</td>
                        <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{log.ipAddress ?? "—"}</td>
                        <td className="px-4 py-3">{badge(log.severity, SEVERITY_CLASS[log.severity] ?? UNKNOWN_CLASS)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {logsQuery.isSuccess && filteredLogs.length === 0 && (
              <div className="py-8 text-center text-sm text-muted-foreground">
                {logs.length === 0
                  ? "No audit events were returned for the current scope."
                  : "No audit events in the loaded page match the selected category."}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </DashboardLayout>
  );
}
