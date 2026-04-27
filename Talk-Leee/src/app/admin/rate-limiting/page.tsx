"use client";

import { useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { RouteGuard } from "@/components/guards/route-guard";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Shield, Clock, Activity, AlertTriangle, Plus, Pencil, Trash2, X } from "lucide-react";
import { RATE_LIMIT_RULES } from "@/lib/billing-mock-data";
import type { RateLimitRule } from "@/lib/billing-types";

type RateLimitScope = "per_user" | "per_tenant" | "per_ip" | "global";
type RateLimitAction = "reject" | "throttle" | "log_only";

interface RuleFormData {
  name: string;
  scope: RateLimitScope;
  endpoint: string;
  maxRequests: number;
  windowSeconds: number;
  burstLimit: number;
  action: RateLimitAction;
}

const emptyForm: RuleFormData = {
  name: "",
  scope: "per_user",
  endpoint: "",
  maxRequests: 100,
  windowSeconds: 60,
  burstLimit: 20,
  action: "reject",
};

function scopeBadge(scope: RateLimitScope) {
  const map: Record<RateLimitScope, { label: string; className: string }> = {
    per_user: { label: "Per User", className: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400" },
    per_tenant: { label: "Per Tenant", className: "border-purple-500/30 bg-purple-500/10 text-purple-700 dark:text-purple-400" },
    per_ip: { label: "Per IP", className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400" },
    global: { label: "Global", className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400" },
  };
  const b = map[scope];
  return <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${b.className}`}>{b.label}</span>;
}

function actionBadge(action: RateLimitAction) {
  const map: Record<RateLimitAction, { label: string; className: string }> = {
    reject: { label: "Reject", className: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400" },
    throttle: { label: "Throttle", className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400" },
    log_only: { label: "Log Only", className: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400" },
  };
  const b = map[action];
  return <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${b.className}`}>{b.label}</span>;
}

function usageBarColor(pct: number) {
  if (pct >= 80) return "bg-red-500";
  if (pct >= 50) return "bg-amber-500";
  return "bg-emerald-500";
}

export default function RateLimitingPage() {
  const [rules, setRules] = useState<RateLimitRule[]>([...RATE_LIMIT_RULES]);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<RuleFormData>({ ...emptyForm });

  const activeRules = rules.filter((r) => r.status === "active");
  const inactiveRules = rules.filter((r) => r.status === "inactive");
  const totalBlocked = 1_247; // placeholder count

  function openAddForm() {
    setForm({ ...emptyForm });
    setEditingId(null);
    setShowForm(true);
  }

  function openEditForm(rule: RateLimitRule) {
    setForm({
      name: rule.name,
      scope: rule.scope,
      endpoint: rule.endpoint,
      maxRequests: rule.maxRequests,
      windowSeconds: rule.windowSeconds,
      burstLimit: rule.burstLimit ?? 0,
      action: rule.action,
    });
    setEditingId(rule.id);
    setShowForm(true);
  }

  function closeForm() {
    setShowForm(false);
    setEditingId(null);
  }

  function handleSave() {
    if (!form.name.trim() || !form.endpoint.trim()) return;
    const now = new Date().toISOString();

    if (editingId) {
      setRules((prev) =>
        prev.map((r) =>
          r.id === editingId
            ? { ...r, name: form.name, scope: form.scope, endpoint: form.endpoint, maxRequests: form.maxRequests, windowSeconds: form.windowSeconds, burstLimit: form.burstLimit || undefined, action: form.action, updatedAt: now }
            : r
        )
      );
    } else {
      const newRule: RateLimitRule = {
        id: `rl_${Date.now()}`,
        name: form.name,
        scope: form.scope,
        endpoint: form.endpoint,
        maxRequests: form.maxRequests,
        windowSeconds: form.windowSeconds,
        burstLimit: form.burstLimit || undefined,
        status: "active",
        action: form.action,
        createdAt: now,
        updatedAt: now,
        currentUsage: 0,
      };
      setRules((prev) => [...prev, newRule]);
    }
    closeForm();
  }

  function handleDelete(id: string) {
    setRules((prev) => prev.filter((r) => r.id !== id));
  }

  function toggleStatus(id: string) {
    setRules((prev) =>
      prev.map((r) =>
        r.id === id ? { ...r, status: r.status === "active" ? "inactive" : "active", updatedAt: new Date().toISOString() } : r
      )
    );
  }

  return (
    <DashboardLayout title="Rate Limiting" description="Configure API and call rate limits per user, tenant, and IP.">
      <RouteGuard title="Rate Limiting" description="Restricted to platform and partner administrators." requiredRoles={["platform_admin", "partner_admin", "admin"]} unauthorizedRedirectTo="/403">
        <div className="space-y-6">
          {/* Summary KPIs */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Card>
              <CardContent className="pt-6 text-center">
                <Shield className="mx-auto mb-2 h-5 w-5 text-blue-500" aria-hidden />
                <div className="text-2xl font-black tabular-nums text-foreground">{rules.length}</div>
                <div className="mt-1 text-xs text-muted-foreground">Total Rules</div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6 text-center">
                <Activity className="mx-auto mb-2 h-5 w-5 text-emerald-500" aria-hidden />
                <div className="text-2xl font-black tabular-nums text-foreground">{activeRules.length}</div>
                <div className="mt-1 text-xs text-muted-foreground">Active Rules</div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6 text-center">
                <Clock className="mx-auto mb-2 h-5 w-5 text-gray-400" aria-hidden />
                <div className="text-2xl font-black tabular-nums text-foreground">{inactiveRules.length}</div>
                <div className="mt-1 text-xs text-muted-foreground">Inactive Rules</div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6 text-center">
                <AlertTriangle className="mx-auto mb-2 h-5 w-5 text-red-500" aria-hidden />
                <div className="text-2xl font-black tabular-nums text-foreground">{totalBlocked.toLocaleString()}</div>
                <div className="mt-1 text-xs text-muted-foreground">Total Blocked</div>
              </CardContent>
            </Card>
          </div>

          {/* Add Rule Form Modal */}
          {showForm && (
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle>{editingId ? "Edit Rule" : "Add Rule"}</CardTitle>
                    <CardDescription>{editingId ? "Update the rate limit rule configuration." : "Create a new rate limit rule."}</CardDescription>
                  </div>
                  <Button variant="ghost" size="icon" onClick={closeForm}><X className="h-4 w-4" aria-hidden /></Button>
                </div>
              </CardHeader>
              <CardContent>
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  <div className="space-y-2">
                    <Label htmlFor="rl-name">Name</Label>
                    <Input id="rl-name" placeholder="e.g. API Default Limit" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="rl-scope">Scope</Label>
                    <select
                      id="rl-scope"
                      value={form.scope}
                      onChange={(e) => setForm((f) => ({ ...f, scope: e.target.value as RateLimitScope }))}
                      className="flex h-10 w-full rounded-lg border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                    >
                      <option value="per_user">Per User</option>
                      <option value="per_tenant">Per Tenant</option>
                      <option value="per_ip">Per IP</option>
                      <option value="global">Global</option>
                    </select>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="rl-endpoint">Endpoint Pattern</Label>
                    <Input id="rl-endpoint" placeholder="e.g. /api/v1/*" value={form.endpoint} onChange={(e) => setForm((f) => ({ ...f, endpoint: e.target.value }))} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="rl-max">Max Requests</Label>
                    <Input id="rl-max" type="number" min={1} value={form.maxRequests} onChange={(e) => setForm((f) => ({ ...f, maxRequests: parseInt(e.target.value) || 1 }))} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="rl-window">Window (seconds)</Label>
                    <Input id="rl-window" type="number" min={1} value={form.windowSeconds} onChange={(e) => setForm((f) => ({ ...f, windowSeconds: parseInt(e.target.value) || 1 }))} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="rl-burst">Burst Limit</Label>
                    <Input id="rl-burst" type="number" min={0} value={form.burstLimit} onChange={(e) => setForm((f) => ({ ...f, burstLimit: parseInt(e.target.value) || 0 }))} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="rl-action">Action</Label>
                    <select
                      id="rl-action"
                      value={form.action}
                      onChange={(e) => setForm((f) => ({ ...f, action: e.target.value as RateLimitAction }))}
                      className="flex h-10 w-full rounded-lg border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                    >
                      <option value="reject">Reject</option>
                      <option value="throttle">Throttle</option>
                      <option value="log_only">Log Only</option>
                    </select>
                  </div>
                  <div className="flex items-end">
                    <Button onClick={handleSave} className="w-full">{editingId ? "Update Rule" : "Add Rule"}</Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Rules Table */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle className="flex items-center gap-2"><Shield className="h-5 w-5" aria-hidden /> Rate Limit Rules</CardTitle>
                  <CardDescription>Manage API and call rate limiting rules across scopes.</CardDescription>
                </div>
                {!showForm && (
                  <Button size="sm" onClick={openAddForm}><Plus className="mr-1 h-4 w-4" aria-hidden /> Add Rule</Button>
                )}
              </div>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                      <th className="px-4 py-3">Name</th>
                      <th className="px-4 py-3">Scope</th>
                      <th className="px-4 py-3">Endpoint</th>
                      <th className="px-4 py-3">Limit</th>
                      <th className="px-4 py-3">Burst</th>
                      <th className="px-4 py-3">Action</th>
                      <th className="px-4 py-3">Status</th>
                      <th className="px-4 py-3">Current Usage</th>
                      <th className="px-4 py-3 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rules.map((rule) => {
                      const usage = rule.currentUsage ?? 0;
                      const pct = rule.maxRequests > 0 ? (usage / rule.maxRequests) * 100 : 0;
                      return (
                        <tr key={rule.id} className="border-b border-border last:border-b-0 hover:bg-muted/20 transition-colors">
                          <td className="px-4 py-3 font-semibold text-foreground">{rule.name}</td>
                          <td className="px-4 py-3">{scopeBadge(rule.scope)}</td>
                          <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{rule.endpoint}</td>
                          <td className="px-4 py-3 tabular-nums text-foreground whitespace-nowrap">{rule.maxRequests} req/{rule.windowSeconds}s</td>
                          <td className="px-4 py-3 tabular-nums text-foreground">{rule.burstLimit ?? <span className="text-muted-foreground">--</span>}</td>
                          <td className="px-4 py-3">{actionBadge(rule.action)}</td>
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2">
                              <Switch
                                checked={rule.status === "active"}
                                onCheckedChange={() => toggleStatus(rule.id)}
                                ariaLabel={`Toggle ${rule.name}`}
                              />
                              <span className={`text-xs font-medium ${rule.status === "active" ? "text-emerald-600 dark:text-emerald-400" : "text-gray-500 dark:text-gray-400"}`}>
                                {rule.status === "active" ? "Active" : "Inactive"}
                              </span>
                            </div>
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2">
                              <div className="flex-1 min-w-[80px]">
                                <div className="h-2 w-full overflow-hidden rounded-full bg-muted/40">
                                  <div className={`h-full rounded-full transition-all ${usageBarColor(pct)}`} style={{ width: `${Math.min(100, pct)}%` }} />
                                </div>
                              </div>
                              <span className="text-xs tabular-nums text-muted-foreground whitespace-nowrap">{usage}/{rule.maxRequests}</span>
                            </div>
                          </td>
                          <td className="px-4 py-3 text-right">
                            <div className="flex items-center justify-end gap-1">
                              <Button variant="ghost" size="icon" onClick={() => openEditForm(rule)} aria-label={`Edit ${rule.name}`}>
                                <Pencil className="h-4 w-4" />
                              </Button>
                              <Button variant="ghost" size="icon" onClick={() => handleDelete(rule.id)} aria-label={`Delete ${rule.name}`}>
                                <Trash2 className="h-4 w-4 text-red-500" />
                              </Button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                    {rules.length === 0 && (
                      <tr>
                        <td colSpan={9} className="px-4 py-8 text-center text-muted-foreground">No rate limit rules configured.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </div>
      </RouteGuard>
    </DashboardLayout>
  );
}
