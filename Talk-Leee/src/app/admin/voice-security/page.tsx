"use client";

import { useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { RouteGuard } from "@/components/guards/route-guard";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CALL_GUARD_RULES, TENANT_LIMITS, PARTNER_LIMITS } from "@/lib/billing-mock-data";
import type { CallGuardRule, TenantLimit, PartnerLimit } from "@/lib/billing-types";
import { ShieldCheck, Users, Building2, AlertTriangle, Pencil, X, Save, Info } from "lucide-react";

/* ── helpers ── */

function formatDuration(seconds: number): string {
  if (seconds >= 3600) {
    const h = seconds / 3600;
    return `${h % 1 === 0 ? h : h.toFixed(1)}h`;
  }
  const m = seconds / 60;
  return `${m % 1 === 0 ? m : m.toFixed(1)}m`;
}

function formatDate(iso?: string): string {
  if (!iso) return "Never";
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

function statusBadge(status: "active" | "suspended" | "restricted") {
  const map: Record<typeof status, { label: string; className: string }> = {
    active: { label: "Active", className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" },
    suspended: { label: "Suspended", className: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400" },
    restricted: { label: "Restricted", className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400" },
  };
  const b = map[status];
  return <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${b.className}`}>{b.label}</span>;
}

function actionBadge(action: "block" | "warn" | "log_only") {
  const map: Record<typeof action, { label: string; className: string }> = {
    block: { label: "Block", className: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400" },
    warn: { label: "Warn", className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400" },
    log_only: { label: "Log Only", className: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400" },
  };
  const b = map[action];
  return <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${b.className}`}>{b.label}</span>;
}

function checkTypeBadge(check: CallGuardRule["check"]) {
  const labels: Record<CallGuardRule["check"], string> = {
    tenant_active: "Tenant",
    partner_active: "Partner",
    concurrency_limit: "Concurrency",
    rate_limit: "Rate Limit",
    allowed_feature: "Feature",
    billing_active: "Billing",
    caller_whitelist: "Whitelist",
  };
  return (
    <span className="inline-flex items-center rounded-full border border-border bg-muted/40 px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
      {labels[check]}
    </span>
  );
}

function featurePills(features: string[]) {
  return (
    <div className="flex flex-wrap gap-1">
      {features.map((f) => (
        <span key={f} className="inline-flex items-center rounded-full border border-border bg-muted/40 px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
          {f}
        </span>
      ))}
    </div>
  );
}

/* ── main page ── */

export default function VoiceSecurityPage() {
  const [activeTab, setActiveTab] = useState("call-guards");
  const [guards, setGuards] = useState<CallGuardRule[]>([...CALL_GUARD_RULES]);
  const [tenantLimits, setTenantLimits] = useState<TenantLimit[]>([...TENANT_LIMITS]);
  const [partnerLimits, setPartnerLimits] = useState<PartnerLimit[]>([...PARTNER_LIMITS]);

  // editing state
  const [editingGuardId, setEditingGuardId] = useState<string | null>(null);
  const [editingTenantId, setEditingTenantId] = useState<string | null>(null);
  const [editingPartnerId, setEditingPartnerId] = useState<string | null>(null);

  // temp edit values
  const [editGuardAction, setEditGuardAction] = useState<CallGuardRule["action"]>("block");
  const [editTenant, setEditTenant] = useState<Partial<TenantLimit>>({});
  const [editPartner, setEditPartner] = useState<Partial<PartnerLimit>>({});

  /* ── guard handlers ── */
  function toggleGuard(id: string) {
    setGuards((prev) => prev.map((g) => (g.id === id ? { ...g, enabled: !g.enabled } : g)));
  }

  function startEditGuard(g: CallGuardRule) {
    setEditingGuardId(g.id);
    setEditGuardAction(g.action);
  }

  function saveGuard(id: string) {
    setGuards((prev) => prev.map((g) => (g.id === id ? { ...g, action: editGuardAction } : g)));
    setEditingGuardId(null);
  }

  /* ── tenant handlers ── */
  function startEditTenant(t: TenantLimit) {
    setEditingTenantId(t.tenantId);
    setEditTenant({ maxConcurrentCalls: t.maxConcurrentCalls, maxCallsPerMinute: t.maxCallsPerMinute, maxCallsPerHour: t.maxCallsPerHour, maxCallDurationSeconds: t.maxCallDurationSeconds });
  }

  function saveTenant(tenantId: string) {
    setTenantLimits((prev) =>
      prev.map((t) => (t.tenantId === tenantId ? { ...t, ...editTenant, updatedAt: new Date().toISOString() } : t))
    );
    setEditingTenantId(null);
  }

  /* ── partner handlers ── */
  function startEditPartner(p: PartnerLimit) {
    setEditingPartnerId(p.partnerId);
    setEditPartner({ maxTenants: p.maxTenants, maxTotalConcurrentCalls: p.maxTotalConcurrentCalls, maxCallsPerMinute: p.maxCallsPerMinute, maxCallsPerHour: p.maxCallsPerHour });
  }

  function savePartner(partnerId: string) {
    setPartnerLimits((prev) =>
      prev.map((p) => (p.partnerId === partnerId ? { ...p, ...editPartner, updatedAt: new Date().toISOString() } : p))
    );
    setEditingPartnerId(null);
  }

  return (
    <DashboardLayout title="Voice Security" description="Configure call guards, tenant limits, and partner limits.">
      <RouteGuard title="Voice Security" description="Restricted to platform and partner administrators." requiredRoles={["platform_admin", "partner_admin", "admin"]} unauthorizedRedirectTo="/403">
        <div className="space-y-6">
          {/* Summary KPIs */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Card>
              <CardContent className="pt-6 text-center">
                <ShieldCheck className="mx-auto h-6 w-6 text-emerald-500" aria-hidden />
                <div className="mt-2 text-2xl font-black tabular-nums text-foreground">{guards.filter((g) => g.enabled).length}</div>
                <div className="mt-1 text-xs text-muted-foreground">Active Guards</div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6 text-center">
                <AlertTriangle className="mx-auto h-6 w-6 text-amber-500" aria-hidden />
                <div className="mt-2 text-2xl font-black tabular-nums text-foreground">{guards.reduce((s, g) => s + g.triggerCount, 0)}</div>
                <div className="mt-1 text-xs text-muted-foreground">Total Triggers</div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6 text-center">
                <Users className="mx-auto h-6 w-6 text-blue-500" aria-hidden />
                <div className="mt-2 text-2xl font-black tabular-nums text-foreground">{tenantLimits.length}</div>
                <div className="mt-1 text-xs text-muted-foreground">Tenant Configs</div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6 text-center">
                <Building2 className="mx-auto h-6 w-6 text-purple-500" aria-hidden />
                <div className="mt-2 text-2xl font-black tabular-nums text-foreground">{partnerLimits.length}</div>
                <div className="mt-1 text-xs text-muted-foreground">Partner Configs</div>
              </CardContent>
            </Card>
          </div>

          {/* Tabs */}
          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsList>
              <TabsTrigger value="call-guards" className="gap-1.5">
                <ShieldCheck className="h-4 w-4" aria-hidden /> Call Guards
              </TabsTrigger>
              <TabsTrigger value="tenant-limits" className="gap-1.5">
                <Users className="h-4 w-4" aria-hidden /> Tenant Limits
              </TabsTrigger>
              <TabsTrigger value="partner-limits" className="gap-1.5">
                <Building2 className="h-4 w-4" aria-hidden /> Partner Limits
              </TabsTrigger>
            </TabsList>

            {/* ── TAB 1: Call Guards ── */}
            <TabsContent value="call-guards" className="mt-4">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2"><ShieldCheck className="h-5 w-5" aria-hidden /> Call Guard Rules</CardTitle>
                  <CardDescription>Pre-call checks executed before every call is connected</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
                    <table className="min-w-full text-sm">
                      <thead>
                        <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                          <th className="px-4 py-3">#</th>
                          <th className="px-4 py-3">Name</th>
                          <th className="px-4 py-3">Check Type</th>
                          <th className="px-4 py-3">Description</th>
                          <th className="px-4 py-3">Action</th>
                          <th className="px-4 py-3">Enabled</th>
                          <th className="px-4 py-3">Last Triggered</th>
                          <th className="px-4 py-3 text-right">Trigger Count</th>
                          <th className="px-4 py-3" />
                        </tr>
                      </thead>
                      <tbody>
                        {guards
                          .sort((a, b) => a.priority - b.priority)
                          .map((g) => (
                            <tr key={g.id} className="border-b border-border last:border-b-0 hover:bg-muted/20 transition-colors">
                              <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{g.priority}</td>
                              <td className="px-4 py-3 font-semibold text-foreground">{g.name}</td>
                              <td className="px-4 py-3">{checkTypeBadge(g.check)}</td>
                              <td className="px-4 py-3 text-muted-foreground max-w-xs">{g.description}</td>
                              <td className="px-4 py-3">
                                {editingGuardId === g.id ? (
                                  <select
                                    value={editGuardAction}
                                    onChange={(e) => setEditGuardAction(e.target.value as CallGuardRule["action"])}
                                    className="rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                                  >
                                    <option value="block">Block</option>
                                    <option value="warn">Warn</option>
                                    <option value="log_only">Log Only</option>
                                  </select>
                                ) : (
                                  actionBadge(g.action)
                                )}
                              </td>
                              <td className="px-4 py-3">
                                <Switch checked={g.enabled} onCheckedChange={() => toggleGuard(g.id)} ariaLabel={`Toggle ${g.name}`} />
                              </td>
                              <td className="px-4 py-3 text-xs text-muted-foreground whitespace-nowrap">{formatDate(g.lastTriggeredAt)}</td>
                              <td className="px-4 py-3 text-right tabular-nums text-foreground font-semibold">{g.triggerCount.toLocaleString()}</td>
                              <td className="px-4 py-3">
                                {editingGuardId === g.id ? (
                                  <div className="flex items-center gap-1">
                                    <Button variant="ghost" size="sm" onClick={() => saveGuard(g.id)}><Save className="h-4 w-4" aria-hidden /></Button>
                                    <Button variant="ghost" size="sm" onClick={() => setEditingGuardId(null)}><X className="h-4 w-4" aria-hidden /></Button>
                                  </div>
                                ) : (
                                  <Button variant="ghost" size="sm" onClick={() => startEditGuard(g)}><Pencil className="h-4 w-4" aria-hidden /></Button>
                                )}
                              </td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>

                  {/* Execution order note */}
                  <div className="mt-4 flex items-start gap-2 rounded-lg border border-blue-500/20 bg-blue-500/5 p-3">
                    <Info className="mt-0.5 h-4 w-4 shrink-0 text-blue-500" aria-hidden />
                    <p className="text-xs text-blue-700 dark:text-blue-400">
                      <span className="font-semibold">Execution Order:</span> Guards are evaluated in priority order (lowest number first). If a guard with action &quot;block&quot; triggers, the call is immediately rejected. &quot;Warn&quot; guards allow the call but generate an alert. &quot;Log only&quot; guards record the event silently.
                    </p>
                  </div>
                </CardContent>
              </Card>
            </TabsContent>

            {/* ── TAB 2: Tenant Limits ── */}
            <TabsContent value="tenant-limits" className="mt-4">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2"><Users className="h-5 w-5" aria-hidden /> Tenant Limits</CardTitle>
                  <CardDescription>Per-tenant call and feature limits</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
                    <table className="min-w-full text-sm">
                      <thead>
                        <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                          <th className="px-4 py-3">Tenant Name</th>
                          <th className="px-4 py-3">Status</th>
                          <th className="px-4 py-3">Max Concurrent</th>
                          <th className="px-4 py-3">Calls/Min</th>
                          <th className="px-4 py-3">Calls/Hour</th>
                          <th className="px-4 py-3">Max Duration</th>
                          <th className="px-4 py-3">Allowed Features</th>
                          <th className="px-4 py-3" />
                        </tr>
                      </thead>
                      <tbody>
                        {tenantLimits.map((t) => (
                          <tr key={t.tenantId} className="border-b border-border last:border-b-0 hover:bg-muted/20 transition-colors">
                            {editingTenantId === t.tenantId ? (
                              <>
                                <td className="px-4 py-3 font-semibold text-foreground">{t.tenantName}</td>
                                <td className="px-4 py-3">{statusBadge(t.status)}</td>
                                <td className="px-4 py-3">
                                  <Input
                                    type="number"
                                    className="w-20 h-8 text-xs"
                                    value={editTenant.maxConcurrentCalls ?? ""}
                                    onChange={(e) => setEditTenant((prev) => ({ ...prev, maxConcurrentCalls: Number(e.target.value) }))}
                                  />
                                </td>
                                <td className="px-4 py-3">
                                  <Input
                                    type="number"
                                    className="w-20 h-8 text-xs"
                                    value={editTenant.maxCallsPerMinute ?? ""}
                                    onChange={(e) => setEditTenant((prev) => ({ ...prev, maxCallsPerMinute: Number(e.target.value) }))}
                                  />
                                </td>
                                <td className="px-4 py-3">
                                  <Input
                                    type="number"
                                    className="w-20 h-8 text-xs"
                                    value={editTenant.maxCallsPerHour ?? ""}
                                    onChange={(e) => setEditTenant((prev) => ({ ...prev, maxCallsPerHour: Number(e.target.value) }))}
                                  />
                                </td>
                                <td className="px-4 py-3">
                                  <div className="flex items-center gap-1">
                                    <Input
                                      type="number"
                                      className="w-20 h-8 text-xs"
                                      value={editTenant.maxCallDurationSeconds ?? ""}
                                      onChange={(e) => setEditTenant((prev) => ({ ...prev, maxCallDurationSeconds: Number(e.target.value) }))}
                                    />
                                    <span className="text-[10px] text-muted-foreground">sec</span>
                                  </div>
                                </td>
                                <td className="px-4 py-3">{featurePills(t.allowedFeatures)}</td>
                                <td className="px-4 py-3">
                                  <div className="flex items-center gap-1">
                                    <Button variant="ghost" size="sm" onClick={() => saveTenant(t.tenantId)}><Save className="h-4 w-4" aria-hidden /></Button>
                                    <Button variant="ghost" size="sm" onClick={() => setEditingTenantId(null)}><X className="h-4 w-4" aria-hidden /></Button>
                                  </div>
                                </td>
                              </>
                            ) : (
                              <>
                                <td className="px-4 py-3 font-semibold text-foreground">{t.tenantName}</td>
                                <td className="px-4 py-3">{statusBadge(t.status)}</td>
                                <td className="px-4 py-3 tabular-nums text-foreground">{t.maxConcurrentCalls}</td>
                                <td className="px-4 py-3 tabular-nums text-foreground">{t.maxCallsPerMinute}</td>
                                <td className="px-4 py-3 tabular-nums text-foreground">{t.maxCallsPerHour.toLocaleString()}</td>
                                <td className="px-4 py-3 tabular-nums text-foreground">{formatDuration(t.maxCallDurationSeconds)}</td>
                                <td className="px-4 py-3">{featurePills(t.allowedFeatures)}</td>
                                <td className="px-4 py-3">
                                  <Button variant="ghost" size="sm" onClick={() => startEditTenant(t)}><Pencil className="h-4 w-4" aria-hidden /></Button>
                                </td>
                              </>
                            )}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            </TabsContent>

            {/* ── TAB 3: Partner Limits ── */}
            <TabsContent value="partner-limits" className="mt-4">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2"><Building2 className="h-5 w-5" aria-hidden /> Partner Limits</CardTitle>
                  <CardDescription>Per-partner aggregate call and tenant limits</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
                    <table className="min-w-full text-sm">
                      <thead>
                        <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                          <th className="px-4 py-3">Partner Name</th>
                          <th className="px-4 py-3">Status</th>
                          <th className="px-4 py-3">Max Tenants</th>
                          <th className="px-4 py-3">Max Total Concurrent</th>
                          <th className="px-4 py-3">Calls/Min</th>
                          <th className="px-4 py-3">Calls/Hour</th>
                          <th className="px-4 py-3">Allowed Features</th>
                          <th className="px-4 py-3" />
                        </tr>
                      </thead>
                      <tbody>
                        {partnerLimits.map((p) => (
                          <tr key={p.partnerId} className="border-b border-border last:border-b-0 hover:bg-muted/20 transition-colors">
                            {editingPartnerId === p.partnerId ? (
                              <>
                                <td className="px-4 py-3 font-semibold text-foreground">{p.partnerName}</td>
                                <td className="px-4 py-3">{statusBadge(p.status)}</td>
                                <td className="px-4 py-3">
                                  <Input
                                    type="number"
                                    className="w-20 h-8 text-xs"
                                    value={editPartner.maxTenants ?? ""}
                                    onChange={(e) => setEditPartner((prev) => ({ ...prev, maxTenants: Number(e.target.value) }))}
                                  />
                                </td>
                                <td className="px-4 py-3">
                                  <Input
                                    type="number"
                                    className="w-20 h-8 text-xs"
                                    value={editPartner.maxTotalConcurrentCalls ?? ""}
                                    onChange={(e) => setEditPartner((prev) => ({ ...prev, maxTotalConcurrentCalls: Number(e.target.value) }))}
                                  />
                                </td>
                                <td className="px-4 py-3">
                                  <Input
                                    type="number"
                                    className="w-20 h-8 text-xs"
                                    value={editPartner.maxCallsPerMinute ?? ""}
                                    onChange={(e) => setEditPartner((prev) => ({ ...prev, maxCallsPerMinute: Number(e.target.value) }))}
                                  />
                                </td>
                                <td className="px-4 py-3">
                                  <Input
                                    type="number"
                                    className="w-20 h-8 text-xs"
                                    value={editPartner.maxCallsPerHour ?? ""}
                                    onChange={(e) => setEditPartner((prev) => ({ ...prev, maxCallsPerHour: Number(e.target.value) }))}
                                  />
                                </td>
                                <td className="px-4 py-3">{featurePills(p.allowedFeatures)}</td>
                                <td className="px-4 py-3">
                                  <div className="flex items-center gap-1">
                                    <Button variant="ghost" size="sm" onClick={() => savePartner(p.partnerId)}><Save className="h-4 w-4" aria-hidden /></Button>
                                    <Button variant="ghost" size="sm" onClick={() => setEditingPartnerId(null)}><X className="h-4 w-4" aria-hidden /></Button>
                                  </div>
                                </td>
                              </>
                            ) : (
                              <>
                                <td className="px-4 py-3 font-semibold text-foreground">{p.partnerName}</td>
                                <td className="px-4 py-3">{statusBadge(p.status)}</td>
                                <td className="px-4 py-3 tabular-nums text-foreground">{p.maxTenants}</td>
                                <td className="px-4 py-3 tabular-nums text-foreground">{p.maxTotalConcurrentCalls}</td>
                                <td className="px-4 py-3 tabular-nums text-foreground">{p.maxCallsPerMinute}</td>
                                <td className="px-4 py-3 tabular-nums text-foreground">{p.maxCallsPerHour.toLocaleString()}</td>
                                <td className="px-4 py-3">{featurePills(p.allowedFeatures)}</td>
                                <td className="px-4 py-3">
                                  <Button variant="ghost" size="sm" onClick={() => startEditPartner(p)}><Pencil className="h-4 w-4" aria-hidden /></Button>
                                </td>
                              </>
                            )}
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
