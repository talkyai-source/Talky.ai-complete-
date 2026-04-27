"use client";

import { useState, useMemo } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { RouteGuard } from "@/components/guards/route-guard";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ShieldAlert, Ban, AlertTriangle, XCircle, ChevronDown, ChevronUp, Plus } from "lucide-react";
import { ABUSE_EVENTS, BLOCKED_ENTITIES } from "@/lib/billing-mock-data";
import type { AbuseEvent, BlockedEntity, AbuseSeverity } from "@/lib/billing-types";

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

function formatLabel(snake: string) {
  return snake.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function severityBadge(severity: AbuseSeverity) {
  const map: Record<AbuseSeverity, string> = {
    low: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400",
    medium: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
    high: "border-orange-500/30 bg-orange-500/10 text-orange-700 dark:text-orange-400",
    critical: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400",
  };
  return <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${map[severity]}`}>{severity}</span>;
}

function statusBadge(status: AbuseEvent["status"]) {
  const map: Record<string, string> = {
    open: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
    investigating: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400",
    resolved: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
    dismissed: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400",
  };
  return <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${map[status]}`}>{status}</span>;
}

function entityTypeBadge(type: BlockedEntity["type"]) {
  const map: Record<string, string> = {
    ip: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400",
    phone_number: "border-purple-500/30 bg-purple-500/10 text-purple-700 dark:text-purple-400",
    tenant: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400",
    user: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  };
  return <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${map[type]}`}>{formatLabel(type)}</span>;
}

function blockStatusBadge(status: BlockedEntity["status"]) {
  const map: Record<string, string> = {
    active: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400",
    expired: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400",
    removed: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  };
  return <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${map[status]}`}>{status}</span>;
}

export default function AbuseDetectionPage() {
  const [events] = useState<AbuseEvent[]>(ABUSE_EVENTS);
  const [blocked, setBlocked] = useState<BlockedEntity[]>(BLOCKED_ENTITIES);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [showBlockForm, setShowBlockForm] = useState(false);
  const [blockForm, setBlockForm] = useState({ type: "ip" as BlockedEntity["type"], value: "", reason: "", expiresAt: "" });

  const filteredEvents = useMemo(() => {
    if (statusFilter === "all") return events;
    return events.filter((e) => e.status === statusFilter);
  }, [events, statusFilter]);

  const openCount = events.filter((e) => e.status === "open").length;
  const criticalCount = events.filter((e) => e.severity === "critical").length;
  const activeBlocked = blocked.filter((b) => b.status === "active").length;

  const handleBlock = () => {
    if (!blockForm.value || !blockForm.reason) return;
    const newBlock: BlockedEntity = {
      id: `blk_${Date.now()}`,
      type: blockForm.type,
      value: blockForm.value,
      reason: blockForm.reason,
      blockedAt: new Date().toISOString(),
      blockedBy: "admin@talkly.ai",
      expiresAt: blockForm.expiresAt || undefined,
      status: "active",
    };
    setBlocked((prev) => [newBlock, ...prev]);
    setBlockForm({ type: "ip", value: "", reason: "", expiresAt: "" });
    setShowBlockForm(false);
  };

  const handleUnblock = (id: string) => {
    setBlocked((prev) => prev.map((b) => (b.id === id ? { ...b, status: "removed" as const } : b)));
  };

  const statusFilters = ["all", "open", "investigating", "resolved", "dismissed"];

  return (
    <DashboardLayout title="Abuse Detection" description="Monitor and respond to suspicious activity and abuse patterns.">
      <RouteGuard
        title="Abuse Detection"
        description="Restricted to platform and partner administrators."
        requiredRoles={["platform_admin", "partner_admin", "admin"]}
        unauthorizedRedirectTo="/403"
      >
        <div className="space-y-6">
          {/* Summary KPIs */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Card>
              <CardContent className="pt-6">
                <div className="flex items-center gap-3">
                  <ShieldAlert className="h-8 w-8 text-muted-foreground" aria-hidden />
                  <div>
                    <div className="text-2xl font-bold tabular-nums text-foreground">{events.length}</div>
                    <div className="text-xs text-muted-foreground">Total Events</div>
                  </div>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6">
                <div className="flex items-center gap-3">
                  <AlertTriangle className="h-8 w-8 text-amber-500" aria-hidden />
                  <div>
                    <div className="text-2xl font-bold tabular-nums text-foreground">{openCount}</div>
                    <div className="text-xs text-muted-foreground">Open</div>
                  </div>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6">
                <div className="flex items-center gap-3">
                  <XCircle className="h-8 w-8 text-red-500" aria-hidden />
                  <div>
                    <div className="text-2xl font-bold tabular-nums text-foreground">{criticalCount}</div>
                    <div className="text-xs text-muted-foreground">Critical</div>
                  </div>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6">
                <div className="flex items-center gap-3">
                  <Ban className="h-8 w-8 text-red-500" aria-hidden />
                  <div>
                    <div className="text-2xl font-bold tabular-nums text-foreground">{activeBlocked}</div>
                    <div className="text-xs text-muted-foreground">Blocked Entities</div>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          <Tabs defaultValue="events" className="w-full">
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="events">Abuse Events</TabsTrigger>
              <TabsTrigger value="blocked">Blocked Entities</TabsTrigger>
            </TabsList>

            {/* Abuse Events Tab */}
            <TabsContent value="events" className="space-y-4">
              <Card>
                <CardHeader>
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                      <CardTitle className="flex items-center gap-2"><ShieldAlert className="h-5 w-5" aria-hidden /> Abuse Events</CardTitle>
                      <CardDescription>Detected suspicious activity and abuse patterns</CardDescription>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2 pt-2">
                    {statusFilters.map((f) => (
                      <Button
                        key={f}
                        variant={statusFilter === f ? "default" : "outline"}
                        size="sm"
                        onClick={() => setStatusFilter(f)}
                        className="capitalize"
                      >
                        {f === "all" ? "All" : f} {f !== "all" && `(${events.filter((e) => e.status === f).length})`}
                      </Button>
                    ))}
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
                    <table className="min-w-full text-sm">
                      <thead>
                        <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                          <th className="px-4 py-3">Severity</th>
                          <th className="px-4 py-3">Type</th>
                          <th className="px-4 py-3">Tenant</th>
                          <th className="px-4 py-3">Source IP</th>
                          <th className="px-4 py-3 max-w-[250px]">Description</th>
                          <th className="px-4 py-3">Detected</th>
                          <th className="px-4 py-3">Status</th>
                          <th className="px-4 py-3">Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {filteredEvents.length === 0 && (
                          <tr><td colSpan={8} className="px-4 py-8 text-center text-muted-foreground">No events match the selected filter.</td></tr>
                        )}
                        {filteredEvents.map((ev) => (
                          <>
                            <tr key={ev.id} className="border-b border-border last:border-b-0 hover:bg-muted/20 transition-colors">
                              <td className="px-4 py-3">{severityBadge(ev.severity)}</td>
                              <td className="px-4 py-3 font-medium text-foreground">{formatLabel(ev.type)}</td>
                              <td className="px-4 py-3 text-foreground">{ev.tenantName}</td>
                              <td className="px-4 py-3 text-muted-foreground font-mono text-xs">{ev.sourceIp || "—"}</td>
                              <td className="px-4 py-3 text-muted-foreground max-w-[250px] truncate">{ev.description}</td>
                              <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">{formatDate(ev.detectedAt)}</td>
                              <td className="px-4 py-3">{statusBadge(ev.status)}</td>
                              <td className="px-4 py-3">
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => setExpandedRow(expandedRow === ev.id ? null : ev.id)}
                                >
                                  {expandedRow === ev.id ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                                  <span className="ml-1">Details</span>
                                </Button>
                              </td>
                            </tr>
                            {expandedRow === ev.id && (
                              <tr key={`${ev.id}_detail`} className="border-b border-border bg-muted/10">
                                <td colSpan={8} className="px-6 py-4">
                                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                                    {ev.callCount != null && (
                                      <div className="rounded-lg border border-border bg-card/50 p-3">
                                        <div className="text-xs font-semibold text-muted-foreground">Call Count</div>
                                        <div className="mt-1 text-lg font-bold text-foreground">{ev.callCount}</div>
                                      </div>
                                    )}
                                    {ev.actionTaken && (
                                      <div className="rounded-lg border border-border bg-card/50 p-3 sm:col-span-2">
                                        <div className="text-xs font-semibold text-muted-foreground">Action Taken</div>
                                        <div className="mt-1 text-sm text-foreground">{ev.actionTaken}</div>
                                      </div>
                                    )}
                                    {ev.metadata && Object.entries(ev.metadata).map(([k, v]) => (
                                      <div key={k} className="rounded-lg border border-border bg-card/50 p-3">
                                        <div className="text-xs font-semibold text-muted-foreground">{formatLabel(k)}</div>
                                        <div className="mt-1 text-sm font-medium text-foreground">{String(v)}</div>
                                      </div>
                                    ))}
                                  </div>
                                </td>
                              </tr>
                            )}
                          </>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            </TabsContent>

            {/* Blocked Entities Tab */}
            <TabsContent value="blocked" className="space-y-4">
              <Card>
                <CardHeader>
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                      <CardTitle className="flex items-center gap-2"><Ban className="h-5 w-5" aria-hidden /> Blocked Entities</CardTitle>
                      <CardDescription>IPs, phone numbers, and accounts currently blocked</CardDescription>
                    </div>
                    <Button onClick={() => setShowBlockForm(!showBlockForm)} size="sm">
                      <Plus className="h-4 w-4 mr-1" aria-hidden /> Block Entity
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  {/* Block Form */}
                  {showBlockForm && (
                    <div className="rounded-xl border border-border bg-muted/20 p-4 space-y-4">
                      <div className="text-sm font-semibold text-foreground">Block New Entity</div>
                      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                        <div className="space-y-2">
                          <Label>Type</Label>
                          <Select value={blockForm.type} onChange={(v) => setBlockForm({ ...blockForm, type: v as BlockedEntity["type"] })} ariaLabel="Block type">
                            <option value="ip">IP Address</option>
                            <option value="phone_number">Phone Number</option>
                            <option value="tenant">Tenant</option>
                            <option value="user">User</option>
                          </Select>
                        </div>
                        <div className="space-y-2">
                          <Label>Value</Label>
                          <Input value={blockForm.value} onChange={(e) => setBlockForm({ ...blockForm, value: e.target.value })} placeholder="e.g. 192.168.1.1" />
                        </div>
                        <div className="space-y-2">
                          <Label>Reason</Label>
                          <Input value={blockForm.reason} onChange={(e) => setBlockForm({ ...blockForm, reason: e.target.value })} placeholder="Reason for blocking" />
                        </div>
                        <div className="space-y-2">
                          <Label>Expires (optional)</Label>
                          <Input type="datetime-local" value={blockForm.expiresAt} onChange={(e) => setBlockForm({ ...blockForm, expiresAt: e.target.value })} />
                        </div>
                      </div>
                      <div className="flex gap-2">
                        <Button onClick={handleBlock} size="sm" variant="destructive"><Ban className="h-4 w-4 mr-1" /> Block</Button>
                        <Button onClick={() => setShowBlockForm(false)} size="sm" variant="outline">Cancel</Button>
                      </div>
                    </div>
                  )}

                  <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
                    <table className="min-w-full text-sm">
                      <thead>
                        <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                          <th className="px-4 py-3">Type</th>
                          <th className="px-4 py-3">Value</th>
                          <th className="px-4 py-3">Reason</th>
                          <th className="px-4 py-3">Blocked By</th>
                          <th className="px-4 py-3">Blocked At</th>
                          <th className="px-4 py-3">Expires</th>
                          <th className="px-4 py-3">Status</th>
                          <th className="px-4 py-3">Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {blocked.map((b) => (
                          <tr key={b.id} className="border-b border-border last:border-b-0 hover:bg-muted/20 transition-colors">
                            <td className="px-4 py-3">{entityTypeBadge(b.type)}</td>
                            <td className="px-4 py-3 font-mono text-xs text-foreground">{b.value}</td>
                            <td className="px-4 py-3 text-muted-foreground max-w-[200px] truncate">{b.reason}</td>
                            <td className="px-4 py-3 text-muted-foreground">{b.blockedBy}</td>
                            <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">{formatDate(b.blockedAt)}</td>
                            <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">{b.expiresAt ? formatDate(b.expiresAt) : "Never"}</td>
                            <td className="px-4 py-3">{blockStatusBadge(b.status)}</td>
                            <td className="px-4 py-3">
                              {b.status === "active" && (
                                <Button variant="outline" size="sm" onClick={() => handleUnblock(b.id)} className="text-red-600 border-red-300 hover:bg-red-50 dark:text-red-400 dark:border-red-800 dark:hover:bg-red-950/30">
                                  Unblock
                                </Button>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </div>
      </RouteGuard>
    </DashboardLayout>
  );
}
