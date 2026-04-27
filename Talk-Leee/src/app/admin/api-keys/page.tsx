"use client";

import { useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { RouteGuard } from "@/components/guards/route-guard";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Key, Plus, ShieldCheck, ShieldOff, Clock, X } from "lucide-react";
import { API_KEYS } from "@/lib/billing-mock-data";
import type { ApiKey } from "@/lib/billing-types";

const ALL_SCOPES = [
  "calls:read",
  "calls:write",
  "campaigns:read",
  "contacts:read",
  "contacts:write",
  "analytics:read",
  "webhooks:write",
] as const;

function formatDate(iso: string) {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function formatTimestamp(iso: string) {
  const d = new Date(iso);
  return (
    d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) +
    " " +
    d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
  );
}

function statusBadge(status: ApiKey["status"]) {
  const map: Record<string, string> = {
    active: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
    revoked: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400",
    expired: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${map[status]}`}
    >
      {status}
    </span>
  );
}

function scopePill(scope: string) {
  return (
    <span
      key={scope}
      className="inline-flex items-center rounded-full border border-indigo-500/30 bg-indigo-500/10 px-2 py-0.5 text-[10px] font-semibold text-indigo-700 dark:text-indigo-400"
    >
      {scope}
    </span>
  );
}

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<ApiKey[]>(API_KEYS);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [confirmRevokeId, setConfirmRevokeId] = useState<string | null>(null);

  // Create form state
  const [newName, setNewName] = useState("");
  const [newScopes, setNewScopes] = useState<string[]>([]);
  const [newRateLimit, setNewRateLimit] = useState("1000");
  const [newExpiration, setNewExpiration] = useState("");

  const activeCount = keys.filter((k) => k.status === "active").length;
  const revokedCount = keys.filter((k) => k.status === "revoked").length;

  function handleCreate() {
    if (!newName.trim() || newScopes.length === 0) return;
    const key: ApiKey = {
      id: `key_${Date.now()}`,
      name: newName.trim(),
      keyPrefix: `tlk_prod_${Math.random().toString(36).slice(2, 6)}`,
      createdAt: new Date().toISOString(),
      status: "active",
      scopes: [...newScopes],
      createdBy: "admin@talkly.ai",
      rateLimit: parseInt(newRateLimit, 10) || undefined,
      expiresAt: newExpiration ? new Date(newExpiration).toISOString() : undefined,
    };
    setKeys((prev) => [key, ...prev]);
    setNewName("");
    setNewScopes([]);
    setNewRateLimit("1000");
    setNewExpiration("");
    setShowCreateModal(false);
  }

  function handleRevoke(id: string) {
    setKeys((prev) =>
      prev.map((k) => (k.id === id ? { ...k, status: "revoked" as const } : k))
    );
    setConfirmRevokeId(null);
  }

  function toggleScope(scope: string) {
    setNewScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope]
    );
  }

  return (
    <DashboardLayout title="API Keys" description="Create, manage, and revoke API keys for programmatic access.">
      <RouteGuard
        title="API Keys"
        description="Restricted to platform and partner administrators."
        requiredRoles={["platform_admin", "partner_admin", "admin"]}
      >
        <div className="space-y-6">
          {/* Summary Bar */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Card>
              <CardContent className="flex items-center gap-3 pt-6">
                <Key className="h-5 w-5 text-muted-foreground" aria-hidden />
                <div>
                  <p className="text-2xl font-bold text-foreground">{keys.length}</p>
                  <p className="text-xs text-muted-foreground">Total Keys</p>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center gap-3 pt-6">
                <ShieldCheck className="h-5 w-5 text-emerald-500" aria-hidden />
                <div>
                  <p className="text-2xl font-bold text-foreground">{activeCount}</p>
                  <p className="text-xs text-muted-foreground">Active</p>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center gap-3 pt-6">
                <ShieldOff className="h-5 w-5 text-red-500" aria-hidden />
                <div>
                  <p className="text-2xl font-bold text-foreground">{revokedCount}</p>
                  <p className="text-xs text-muted-foreground">Revoked</p>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Create Button */}
          <div className="flex justify-end">
            <Button onClick={() => setShowCreateModal(true)} className="gap-2">
              <Plus className="h-4 w-4" aria-hidden />
              Create API Key
            </Button>
          </div>

          {/* Create Modal */}
          {showCreateModal && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
              <Card className="w-full max-w-lg">
                <CardHeader>
                  <div className="flex items-center justify-between">
                    <CardTitle className="flex items-center gap-2">
                      <Plus className="h-5 w-5" aria-hidden />
                      Create API Key
                    </CardTitle>
                    <button
                      onClick={() => setShowCreateModal(false)}
                      className="rounded-md p-1 text-muted-foreground hover:text-foreground transition-colors"
                    >
                      <X className="h-5 w-5" />
                    </button>
                  </div>
                  <CardDescription>Generate a new API key with specific scopes and rate limits.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  {/* Name */}
                  <div>
                    <label className="block text-xs font-semibold text-muted-foreground mb-1">Name</label>
                    <input
                      type="text"
                      value={newName}
                      onChange={(e) => setNewName(e.target.value)}
                      placeholder="e.g. Production API Key"
                      className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 dark:border-white/10 dark:bg-white/5"
                    />
                  </div>

                  {/* Scopes */}
                  <div>
                    <label className="block text-xs font-semibold text-muted-foreground mb-2">Scopes</label>
                    <div className="flex flex-wrap gap-2">
                      {ALL_SCOPES.map((scope) => (
                        <label
                          key={scope}
                          className={`inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold transition-colors ${
                            newScopes.includes(scope)
                              ? "border-primary bg-primary/10 text-primary"
                              : "border-border bg-muted/30 text-muted-foreground hover:border-primary/50"
                          }`}
                        >
                          <input
                            type="checkbox"
                            checked={newScopes.includes(scope)}
                            onChange={() => toggleScope(scope)}
                            className="sr-only"
                          />
                          {scope}
                        </label>
                      ))}
                    </div>
                  </div>

                  {/* Rate Limit */}
                  <div>
                    <label className="block text-xs font-semibold text-muted-foreground mb-1">
                      Rate Limit (requests/min)
                    </label>
                    <input
                      type="number"
                      value={newRateLimit}
                      onChange={(e) => setNewRateLimit(e.target.value)}
                      min={1}
                      className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 dark:border-white/10 dark:bg-white/5"
                    />
                  </div>

                  {/* Expiration */}
                  <div>
                    <label className="block text-xs font-semibold text-muted-foreground mb-1">
                      Expiration Date (optional)
                    </label>
                    <input
                      type="date"
                      value={newExpiration}
                      onChange={(e) => setNewExpiration(e.target.value)}
                      className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 dark:border-white/10 dark:bg-white/5"
                    />
                  </div>

                  {/* Actions */}
                  <div className="flex justify-end gap-2 pt-2">
                    <Button variant="outline" onClick={() => setShowCreateModal(false)}>
                      Cancel
                    </Button>
                    <Button onClick={handleCreate} disabled={!newName.trim() || newScopes.length === 0}>
                      Create Key
                    </Button>
                  </div>
                </CardContent>
              </Card>
            </div>
          )}

          {/* Revoke Confirmation Dialog */}
          {confirmRevokeId && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
              <Card className="w-full max-w-sm">
                <CardHeader>
                  <CardTitle className="text-base">Revoke API Key</CardTitle>
                  <CardDescription>
                    Are you sure you want to revoke{" "}
                    <span className="font-semibold text-foreground">
                      {keys.find((k) => k.id === confirmRevokeId)?.name}
                    </span>
                    ? This action cannot be undone.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="flex justify-end gap-2">
                    <Button variant="outline" onClick={() => setConfirmRevokeId(null)}>
                      Cancel
                    </Button>
                    <Button
                      variant="destructive"
                      onClick={() => handleRevoke(confirmRevokeId)}
                    >
                      Revoke
                    </Button>
                  </div>
                </CardContent>
              </Card>
            </div>
          )}

          {/* API Keys Table */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Key className="h-5 w-5" aria-hidden />
                API Keys
              </CardTitle>
              <CardDescription>
                {keys.length} key{keys.length !== 1 ? "s" : ""} configured
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                      <th className="px-4 py-3">Name</th>
                      <th className="px-4 py-3">Key Prefix</th>
                      <th className="px-4 py-3">Status</th>
                      <th className="px-4 py-3">Scopes</th>
                      <th className="px-4 py-3">Rate Limit</th>
                      <th className="px-4 py-3">Created</th>
                      <th className="px-4 py-3">Last Used</th>
                      <th className="px-4 py-3">Expires</th>
                      <th className="px-4 py-3">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {keys.map((key) => (
                      <tr
                        key={key.id}
                        className="border-b border-border last:border-b-0 hover:bg-muted/20 transition-colors"
                      >
                        <td className="px-4 py-3">
                          <div className="font-medium text-foreground text-xs">{key.name}</div>
                        </td>
                        <td className="px-4 py-3">
                          <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono text-foreground">
                            {key.keyPrefix}
                          </code>
                        </td>
                        <td className="px-4 py-3">{statusBadge(key.status)}</td>
                        <td className="px-4 py-3">
                          <div className="flex flex-wrap gap-1 max-w-[240px]">
                            {key.scopes.map((s) => scopePill(s))}
                          </div>
                        </td>
                        <td className="px-4 py-3 text-muted-foreground text-xs whitespace-nowrap">
                          {key.rateLimit ? `${key.rateLimit.toLocaleString()}/min` : "\u2014"}
                        </td>
                        <td className="px-4 py-3 text-muted-foreground text-xs whitespace-nowrap">
                          {formatDate(key.createdAt)}
                        </td>
                        <td className="px-4 py-3 text-muted-foreground text-xs whitespace-nowrap">
                          {key.lastUsedAt ? formatTimestamp(key.lastUsedAt) : "\u2014"}
                        </td>
                        <td className="px-4 py-3 text-muted-foreground text-xs whitespace-nowrap">
                          {key.expiresAt ? (
                            <span className="inline-flex items-center gap-1">
                              <Clock className="h-3 w-3" aria-hidden />
                              {formatDate(key.expiresAt)}
                            </span>
                          ) : (
                            "\u2014"
                          )}
                        </td>
                        <td className="px-4 py-3">
                          {key.status === "active" ? (
                            <Button
                              variant="outline"
                              size="sm"
                              className="text-xs text-red-600 border-red-500/30 hover:bg-red-500/10 dark:text-red-400"
                              onClick={() => setConfirmRevokeId(key.id)}
                            >
                              Revoke
                            </Button>
                          ) : (
                            <span className="text-xs text-muted-foreground">\u2014</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {keys.length === 0 && (
                <div className="py-8 text-center text-sm text-muted-foreground">
                  No API keys configured. Create one to get started.
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </RouteGuard>
    </DashboardLayout>
  );
}
