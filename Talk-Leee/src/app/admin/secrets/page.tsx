"use client";

import { useState, useMemo } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { RouteGuard } from "@/components/guards/route-guard";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Lock, Key, RotateCw, AlertTriangle, Shield, Clock, Plus, CheckCircle, XCircle } from "lucide-react";
import { SECRETS } from "@/lib/billing-mock-data";
import type { SecretEntry, SecretCategory } from "@/lib/billing-types";

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function isRotationOverdue(secret: SecretEntry): boolean {
  if (!secret.lastRotatedAt || !secret.rotationIntervalDays) return false;
  const lastRotated = new Date(secret.lastRotatedAt);
  const now = new Date();
  const daysSince = Math.floor((now.getTime() - lastRotated.getTime()) / (1000 * 60 * 60 * 24));
  return daysSince > secret.rotationIntervalDays;
}

function daysSinceRotation(secret: SecretEntry): number {
  if (!secret.lastRotatedAt) return 0;
  const lastRotated = new Date(secret.lastRotatedAt);
  const now = new Date();
  return Math.floor((now.getTime() - lastRotated.getTime()) / (1000 * 60 * 60 * 24));
}

function categoryBadge(category: SecretCategory) {
  const map: Record<SecretCategory, string> = {
    api_key: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400",
    database: "border-purple-500/30 bg-purple-500/10 text-purple-700 dark:text-purple-400",
    payment: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
    voice_provider: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
    email: "border-teal-500/30 bg-teal-500/10 text-teal-700 dark:text-teal-400",
    storage: "border-indigo-500/30 bg-indigo-500/10 text-indigo-700 dark:text-indigo-400",
    monitoring: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400",
    other: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400",
  };
  const label = category.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold ${map[category]}`}>{label}</span>;
}

function envBadge(env: SecretEntry["environment"]) {
  const map: Record<string, string> = {
    production: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400",
    staging: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
    development: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400",
  };
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold ${map[env]}`}>{env}</span>;
}

export default function SecretsPage() {
  const [secrets, setSecrets] = useState<SecretEntry[]>(SECRETS);
  const [envFilter, setEnvFilter] = useState<string>("all");
  const [showAddForm, setShowAddForm] = useState(false);
  const [confirmRotate, setConfirmRotate] = useState<string | null>(null);
  const [addForm, setAddForm] = useState({
    name: "",
    category: "api_key" as SecretCategory,
    environment: "production" as SecretEntry["environment"],
    description: "",
    rotationIntervalDays: 90,
  });

  const filtered = useMemo(() => {
    if (envFilter === "all") return secrets;
    return secrets.filter((s) => s.environment === envFilter);
  }, [secrets, envFilter]);

  const prodCount = secrets.filter((s) => s.environment === "production").length;
  const stagingCount = secrets.filter((s) => s.environment === "staging").length;
  const expiredCount = secrets.filter((s) => s.isExpired || isRotationOverdue(s)).length;

  const handleAdd = () => {
    if (!addForm.name) return;
    const newSecret: SecretEntry = {
      id: `sec_${Date.now()}`,
      name: addForm.name,
      category: addForm.category,
      maskedValue: "••••••••••••••••",
      environment: addForm.environment,
      rotationIntervalDays: addForm.rotationIntervalDays,
      isExpired: false,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      updatedBy: "admin@talkly.ai",
      description: addForm.description || undefined,
    };
    setSecrets((prev) => [newSecret, ...prev]);
    setAddForm({ name: "", category: "api_key", environment: "production", description: "", rotationIntervalDays: 90 });
    setShowAddForm(false);
  };

  const handleRotate = (id: string) => {
    setSecrets((prev) =>
      prev.map((s) =>
        s.id === id
          ? { ...s, lastRotatedAt: new Date().toISOString(), updatedAt: new Date().toISOString(), isExpired: false }
          : s
      )
    );
    setConfirmRotate(null);
  };

  const categories: SecretCategory[] = ["api_key", "database", "payment", "voice_provider", "email", "storage", "monitoring", "other"];
  const environments: SecretEntry["environment"][] = ["production", "staging", "development"];

  return (
    <DashboardLayout title="Secrets Management" description="Manage environment variables and sensitive configuration.">
      <RouteGuard
        title="Secrets Management"
        description="Restricted to platform administrators only."
        requiredRoles={["platform_admin", "admin"]}
        unauthorizedRedirectTo="/403"
      >
        <div className="space-y-6">
          {/* Security Notice */}
          <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 flex-shrink-0 text-amber-600 dark:text-amber-400 mt-0.5" aria-hidden />
            <div>
              <div className="text-sm font-semibold text-amber-800 dark:text-amber-300">Security Notice</div>
              <div className="text-sm text-amber-700 dark:text-amber-400">Secrets are masked for security. Values shown are redacted. Actual secret values are never exposed in the frontend. To set or update values, use the backend CLI.</div>
            </div>
          </div>

          {/* Summary KPIs */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Card>
              <CardContent className="pt-6">
                <div className="flex items-center gap-3">
                  <Lock className="h-8 w-8 text-muted-foreground" aria-hidden />
                  <div>
                    <div className="text-2xl font-bold tabular-nums text-foreground">{secrets.length}</div>
                    <div className="text-xs text-muted-foreground">Total Secrets</div>
                  </div>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6">
                <div className="flex items-center gap-3">
                  <Shield className="h-8 w-8 text-red-500" aria-hidden />
                  <div>
                    <div className="text-2xl font-bold tabular-nums text-foreground">{prodCount}</div>
                    <div className="text-xs text-muted-foreground">Production</div>
                  </div>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6">
                <div className="flex items-center gap-3">
                  <Key className="h-8 w-8 text-amber-500" aria-hidden />
                  <div>
                    <div className="text-2xl font-bold tabular-nums text-foreground">{stagingCount}</div>
                    <div className="text-xs text-muted-foreground">Staging</div>
                  </div>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6">
                <div className="flex items-center gap-3">
                  <AlertTriangle className="h-8 w-8 text-red-500" aria-hidden />
                  <div>
                    <div className="text-2xl font-bold tabular-nums text-foreground">{expiredCount}</div>
                    <div className="text-xs text-muted-foreground">Needs Rotation</div>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Main Card */}
          <Card>
            <CardHeader>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <CardTitle className="flex items-center gap-2"><Lock className="h-5 w-5" aria-hidden /> Environment Secrets</CardTitle>
                  <CardDescription>Encrypted environment variables and API credentials</CardDescription>
                </div>
                <Button onClick={() => setShowAddForm(!showAddForm)} size="sm">
                  <Plus className="h-4 w-4 mr-1" aria-hidden /> Add Secret
                </Button>
              </div>
              {/* Environment filter */}
              <div className="flex flex-wrap gap-2 pt-2">
                {["all", ...environments].map((env) => (
                  <Button key={env} variant={envFilter === env ? "default" : "outline"} size="sm" onClick={() => setEnvFilter(env)} className="capitalize">
                    {env}
                  </Button>
                ))}
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Add Form */}
              {showAddForm && (
                <div className="rounded-xl border border-border bg-muted/20 p-4 space-y-4">
                  <div className="text-sm font-semibold text-foreground">Add New Secret</div>
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                    <div className="space-y-2">
                      <Label>Name</Label>
                      <Input value={addForm.name} onChange={(e) => setAddForm({ ...addForm, name: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_") })} placeholder="e.g. STRIPE_API_KEY" className="font-mono" />
                    </div>
                    <div className="space-y-2">
                      <Label>Category</Label>
                      <Select value={addForm.category} onChange={(v) => setAddForm({ ...addForm, category: v as SecretCategory })} ariaLabel="Category">
                        {categories.map((c) => (
                          <option key={c} value={c}>{c.replace(/_/g, " ").replace(/\b\w/g, (ch) => ch.toUpperCase())}</option>
                        ))}
                      </Select>
                    </div>
                    <div className="space-y-2">
                      <Label>Environment</Label>
                      <Select value={addForm.environment} onChange={(v) => setAddForm({ ...addForm, environment: v as SecretEntry["environment"] })} ariaLabel="Environment">
                        {environments.map((e) => (
                          <option key={e} value={e}>{e}</option>
                        ))}
                      </Select>
                    </div>
                    <div className="space-y-2">
                      <Label>Rotation Interval (days)</Label>
                      <Input type="number" min={1} max={365} value={addForm.rotationIntervalDays} onChange={(e) => setAddForm({ ...addForm, rotationIntervalDays: Number(e.target.value) || 90 })} />
                    </div>
                    <div className="space-y-2 sm:col-span-2">
                      <Label>Description</Label>
                      <Input value={addForm.description} onChange={(e) => setAddForm({ ...addForm, description: e.target.value })} placeholder="What this secret is used for" />
                    </div>
                  </div>
                  <div className="text-xs text-muted-foreground">Note: The actual secret value will be set securely via the backend CLI.</div>
                  <div className="flex gap-2">
                    <Button onClick={handleAdd} size="sm"><Plus className="h-4 w-4 mr-1" /> Add</Button>
                    <Button onClick={() => setShowAddForm(false)} size="sm" variant="outline">Cancel</Button>
                  </div>
                </div>
              )}

              {/* Secrets Table */}
              <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                      <th className="px-4 py-3">Name</th>
                      <th className="px-4 py-3">Category</th>
                      <th className="px-4 py-3">Value</th>
                      <th className="px-4 py-3">Env</th>
                      <th className="px-4 py-3">Last Rotated</th>
                      <th className="px-4 py-3">Rotation</th>
                      <th className="px-4 py-3">Status</th>
                      <th className="px-4 py-3">Updated By</th>
                      <th className="px-4 py-3">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((s) => {
                      const overdue = s.isExpired || isRotationOverdue(s);
                      const days = daysSinceRotation(s);
                      return (
                        <tr key={s.id} className="border-b border-border last:border-b-0 hover:bg-muted/20 transition-colors">
                          <td className="px-4 py-3">
                            <div className="font-mono text-xs font-semibold text-foreground">{s.name}</div>
                            {s.description && <div className="text-[10px] text-muted-foreground mt-0.5 max-w-[200px] truncate">{s.description}</div>}
                          </td>
                          <td className="px-4 py-3">{categoryBadge(s.category)}</td>
                          <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{s.maskedValue}</td>
                          <td className="px-4 py-3">{envBadge(s.environment)}</td>
                          <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">{s.lastRotatedAt ? formatDate(s.lastRotatedAt) : "Never"}</td>
                          <td className="px-4 py-3 text-muted-foreground">
                            {s.rotationIntervalDays ? (
                              <span className="flex items-center gap-1">
                                <Clock className="h-3 w-3" aria-hidden />
                                {s.rotationIntervalDays}d
                                <span className="text-[10px]">({days}d ago)</span>
                              </span>
                            ) : "—"}
                          </td>
                          <td className="px-4 py-3">
                            {overdue ? (
                              <span className="inline-flex items-center gap-1 text-red-600 dark:text-red-400">
                                <XCircle className="h-4 w-4" aria-hidden />
                                <span className="text-xs font-semibold">Overdue</span>
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
                                <CheckCircle className="h-4 w-4" aria-hidden />
                                <span className="text-xs font-semibold">OK</span>
                              </span>
                            )}
                          </td>
                          <td className="px-4 py-3 text-muted-foreground text-xs">{s.updatedBy}</td>
                          <td className="px-4 py-3">
                            {confirmRotate === s.id ? (
                              <div className="flex gap-1">
                                <Button variant="destructive" size="sm" onClick={() => handleRotate(s.id)} className="text-xs px-2 py-1 h-auto">Confirm</Button>
                                <Button variant="outline" size="sm" onClick={() => setConfirmRotate(null)} className="text-xs px-2 py-1 h-auto">Cancel</Button>
                              </div>
                            ) : (
                              <Button variant="outline" size="sm" onClick={() => setConfirmRotate(s.id)} className="text-xs">
                                <RotateCw className="h-3 w-3 mr-1" aria-hidden /> Rotate
                              </Button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
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
