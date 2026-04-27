"use client";

import { useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Shield, Filter } from "lucide-react";
import { AUDIT_LOGS } from "@/lib/billing-mock-data";
import type { AuditLogEntry } from "@/lib/billing-types";

function formatTimestamp(iso: string) {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) + " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function severityBadge(severity: AuditLogEntry["severity"]) {
  const map: Record<string, string> = {
    info: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400",
    warning: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
    critical: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400",
  };
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold ${map[severity]}`}>{severity}</span>;
}

function categoryBadge(category: AuditLogEntry["category"]) {
  const map: Record<string, string> = {
    auth: "border-indigo-500/30 bg-indigo-500/10 text-indigo-700 dark:text-indigo-400",
    billing: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
    role: "border-purple-500/30 bg-purple-500/10 text-purple-700 dark:text-purple-400",
    suspension: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400",
    settings: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400",
    security: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  };
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold ${map[category]}`}>{category}</span>;
}

const ALL_CATEGORIES = ["all", "auth", "billing", "role", "suspension", "settings", "security"] as const;

export default function AuditLogsPage() {
  const [categoryFilter, setCategoryFilter] = useState<string>("all");

  const filteredLogs = categoryFilter === "all"
    ? AUDIT_LOGS
    : AUDIT_LOGS.filter((log) => log.category === categoryFilter);

  return (
    <DashboardLayout title="Audit Logs" description="Security events, role changes, billing changes, and login activity.">
      <div className="space-y-6">
        {/* Filters */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex flex-wrap items-center gap-2">
              <Filter className="h-4 w-4 text-muted-foreground" aria-hidden />
              <span className="text-xs font-semibold text-muted-foreground mr-2">Filter by category:</span>
              {ALL_CATEGORIES.map((cat) => (
                <Button
                  key={cat}
                  variant={categoryFilter === cat ? "default" : "outline"}
                  size="sm"
                  onClick={() => setCategoryFilter(cat)}
                  className="text-xs capitalize"
                >
                  {cat === "all" ? "All" : cat}
                </Button>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Log Table */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Shield className="h-5 w-5" aria-hidden /> Audit Trail</CardTitle>
            <CardDescription>{filteredLogs.length} event{filteredLogs.length !== 1 ? "s" : ""} found</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                    <th className="px-4 py-3">Timestamp</th>
                    <th className="px-4 py-3">User</th>
                    <th className="px-4 py-3">Action</th>
                    <th className="px-4 py-3">Category</th>
                    <th className="px-4 py-3">Details</th>
                    <th className="px-4 py-3">IP Address</th>
                    <th className="px-4 py-3">Severity</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredLogs.map((log) => (
                    <tr key={log.id} className="border-b border-border last:border-b-0 hover:bg-muted/20 transition-colors">
                      <td className="px-4 py-3 text-muted-foreground whitespace-nowrap text-xs">{formatTimestamp(log.timestamp)}</td>
                      <td className="px-4 py-3">
                        <div className="font-medium text-foreground text-xs">{log.userName}</div>
                      </td>
                      <td className="px-4 py-3">
                        <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono text-foreground">{log.action}</code>
                      </td>
                      <td className="px-4 py-3">{categoryBadge(log.category)}</td>
                      <td className="px-4 py-3 text-muted-foreground text-xs max-w-[300px]">{log.details}</td>
                      <td className="px-4 py-3 text-muted-foreground text-xs font-mono">{log.ipAddress}</td>
                      <td className="px-4 py-3">{severityBadge(log.severity)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {filteredLogs.length === 0 && (
              <div className="py-8 text-center text-sm text-muted-foreground">No audit log entries match the selected filter.</div>
            )}
          </CardContent>
        </Card>
      </div>
    </DashboardLayout>
  );
}
