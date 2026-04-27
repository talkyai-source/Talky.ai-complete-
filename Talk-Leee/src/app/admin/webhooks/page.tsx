"use client";

import { useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { RouteGuard } from "@/components/guards/route-guard";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Webhook,
  Plus,
  Pencil,
  Trash2,
  TestTube,
  ChevronDown,
  ChevronRight,
  ShieldCheck,
  RotateCcw,
  X,
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
} from "lucide-react";
import { WEBHOOK_ENDPOINTS, WEBHOOK_DELIVERIES } from "@/lib/billing-mock-data";
import type { WebhookEndpoint, WebhookDelivery, WebhookEventType } from "@/lib/billing-types";

// ── Helpers ──

const ALL_EVENT_TYPES: WebhookEventType[] = [
  "call.started",
  "call.ended",
  "call.failed",
  "billing.invoice_created",
  "billing.payment_received",
  "billing.payment_failed",
  "billing.plan_changed",
  "tenant.created",
  "tenant.suspended",
  "partner.suspended",
  "security.mfa_enabled",
  "security.login_failed",
];

function formatTimestamp(iso: string) {
  const d = new Date(iso);
  return (
    d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) +
    " " +
    d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
  );
}

function truncateUrl(url: string, max = 40) {
  return url.length > max ? url.slice(0, max) + "\u2026" : url;
}

function statusBadge(status: WebhookEndpoint["status"]) {
  const map: Record<string, { label: string; className: string }> = {
    active: { label: "Active", className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" },
    inactive: { label: "Inactive", className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400" },
    failing: { label: "Failing", className: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400" },
  };
  const b = map[status];
  return <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${b.className}`}>{b.label}</span>;
}

function deliveryStatusBadge(status: WebhookDelivery["status"]) {
  const map: Record<string, { label: string; className: string; Icon: typeof CheckCircle2 }> = {
    success: { label: "Success", className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400", Icon: CheckCircle2 },
    failed: { label: "Failed", className: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400", Icon: XCircle },
    pending: { label: "Pending", className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400", Icon: Clock },
  };
  const b = map[status];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-semibold ${b.className}`}>
      <b.Icon className="h-3 w-3" aria-hidden />
      {b.label}
    </span>
  );
}

function eventPill(event: WebhookEventType) {
  return (
    <span className="inline-flex items-center rounded-full border border-indigo-500/30 bg-indigo-500/10 px-2 py-0.5 text-[10px] font-semibold text-indigo-700 dark:text-indigo-400">
      {event}
    </span>
  );
}

function deliveryStatusIcon(status?: "success" | "failed") {
  if (status === "success") return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" aria-hidden />;
  if (status === "failed") return <XCircle className="h-3.5 w-3.5 text-red-500" aria-hidden />;
  return <span className="text-muted-foreground">--</span>;
}

// ── Verification code snippet ──

const VERIFICATION_SNIPPET = `import crypto from "crypto";

function verifyWebhookSignature(
  payload: string,
  signature: string,
  secret: string
): boolean {
  const expected = crypto
    .createHmac("sha256", secret)
    .update(payload, "utf8")
    .digest("hex");
  return crypto.timingSafeEqual(
    Buffer.from(signature),
    Buffer.from(expected)
  );
}`;

// ── Page Component ──

export default function WebhooksPage() {
  const [endpoints] = useState<WebhookEndpoint[]>(WEBHOOK_ENDPOINTS);
  const [deliveries] = useState<WebhookDelivery[]>(WEBHOOK_DELIVERIES);

  // Modal state
  const [showAddModal, setShowAddModal] = useState(false);
  const [newUrl, setNewUrl] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newEvents, setNewEvents] = useState<WebhookEventType[]>([]);

  // Expandable delivery rows
  const [expandedDeliveries, setExpandedDeliveries] = useState<Set<string>>(new Set());

  // Computed summary stats
  const totalEndpoints = endpoints.length;
  const activeEndpoints = endpoints.filter((e) => e.status === "active").length;
  const failingEndpoints = endpoints.filter((e) => e.status === "failing").length;

  function toggleDelivery(id: string) {
    setExpandedDeliveries((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleEvent(event: WebhookEventType) {
    setNewEvents((prev) =>
      prev.includes(event) ? prev.filter((e) => e !== event) : [...prev, event]
    );
  }

  function handleAddEndpoint() {
    // In a real app this would POST to the API
    setShowAddModal(false);
    setNewUrl("");
    setNewDescription("");
    setNewEvents([]);
  }

  function endpointForDelivery(endpointId: string) {
    return endpoints.find((e) => e.id === endpointId);
  }

  return (
    <DashboardLayout title="Webhooks" description="Manage webhook endpoints, review delivery logs, and configure signature verification.">
      <RouteGuard
        title="Webhooks"
        description="Restricted to platform and partner administrators."
        requiredRoles={["platform_admin", "partner_admin", "admin"]}
        unauthorizedRedirectTo="/403"
      >
        <div className="space-y-6">
          {/* ── Section 1: Webhook Endpoints ── */}

          {/* Summary Cards */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Card>
              <CardContent className="pt-6 text-center">
                <div className="text-2xl font-black tabular-nums text-foreground">{totalEndpoints}</div>
                <div className="mt-1 text-xs text-muted-foreground">Total Endpoints</div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6 text-center">
                <div className="text-2xl font-black tabular-nums text-emerald-600 dark:text-emerald-400">{activeEndpoints}</div>
                <div className="mt-1 text-xs text-muted-foreground">Active</div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6 text-center">
                <div className={`text-2xl font-black tabular-nums ${failingEndpoints > 0 ? "text-red-600 dark:text-red-400" : "text-foreground"}`}>{failingEndpoints}</div>
                <div className="mt-1 text-xs text-muted-foreground">Failing</div>
              </CardContent>
            </Card>
          </div>

          {/* Endpoints Table */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle className="flex items-center gap-2"><Webhook className="h-5 w-5" aria-hidden /> Webhook Endpoints</CardTitle>
                  <CardDescription>Endpoints receiving event notifications from the platform</CardDescription>
                </div>
                <Button size="sm" onClick={() => setShowAddModal(true)}>
                  <Plus className="mr-1 h-4 w-4" aria-hidden /> Add Endpoint
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                      <th className="px-4 py-3">URL</th>
                      <th className="px-4 py-3">Description</th>
                      <th className="px-4 py-3">Events</th>
                      <th className="px-4 py-3">Status</th>
                      <th className="px-4 py-3">Failures</th>
                      <th className="px-4 py-3">Last Delivery</th>
                      <th className="px-4 py-3 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {endpoints.map((ep) => (
                      <tr key={ep.id} className="border-b border-border last:border-b-0 hover:bg-muted/20 transition-colors">
                        <td className="px-4 py-3 font-mono text-xs text-foreground" title={ep.url}>{truncateUrl(ep.url)}</td>
                        <td className="px-4 py-3 text-muted-foreground max-w-[200px] truncate">{ep.description}</td>
                        <td className="px-4 py-3">
                          <span className="inline-flex items-center rounded-full border border-indigo-500/30 bg-indigo-500/10 px-2.5 py-0.5 text-xs font-semibold text-indigo-700 dark:text-indigo-400">
                            {ep.events.length} event{ep.events.length !== 1 ? "s" : ""}
                          </span>
                        </td>
                        <td className="px-4 py-3">{statusBadge(ep.status)}</td>
                        <td className="px-4 py-3 tabular-nums">
                          {ep.failureCount > 0 ? (
                            <span className="flex items-center gap-1 text-red-600 dark:text-red-400 font-semibold">
                              <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
                              {ep.failureCount}
                            </span>
                          ) : (
                            <span className="text-muted-foreground">0</span>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          {ep.lastDeliveryAt ? (
                            <div className="flex items-center gap-1.5">
                              {deliveryStatusIcon(ep.lastDeliveryStatus)}
                              <span className="text-xs text-muted-foreground">{formatTimestamp(ep.lastDeliveryAt)}</span>
                            </div>
                          ) : (
                            <span className="text-muted-foreground">--</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <div className="flex items-center justify-end gap-1">
                            <Button variant="ghost" size="sm" title="Edit endpoint">
                              <Pencil className="h-3.5 w-3.5" aria-hidden />
                            </Button>
                            <Button variant="ghost" size="sm" title="Delete endpoint">
                              <Trash2 className="h-3.5 w-3.5 text-red-500" aria-hidden />
                            </Button>
                            <Button variant="ghost" size="sm" title="Send test event">
                              <TestTube className="h-3.5 w-3.5 text-blue-500" aria-hidden />
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          {/* ── Section 2: Recent Deliveries ── */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2"><Clock className="h-5 w-5" aria-hidden /> Recent Deliveries</CardTitle>
              <CardDescription>Latest webhook delivery attempts and their outcomes</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                      <th className="w-8 px-2 py-3" />
                      <th className="px-4 py-3">Event Type</th>
                      <th className="px-4 py-3">Endpoint URL</th>
                      <th className="px-4 py-3">Status</th>
                      <th className="px-4 py-3">Response Code</th>
                      <th className="px-4 py-3">Duration</th>
                      <th className="px-4 py-3">Attempts</th>
                      <th className="px-4 py-3">Delivered At</th>
                    </tr>
                  </thead>
                  <tbody>
                    {deliveries.map((del) => {
                      const ep = endpointForDelivery(del.endpointId);
                      const isExpanded = expandedDeliveries.has(del.id);
                      return (
                        <tr key={del.id} className="border-b border-border last:border-b-0 group">
                          <td className="px-2 py-3">
                            <button
                              onClick={() => toggleDelivery(del.id)}
                              className="text-muted-foreground hover:text-foreground transition-colors"
                              title="Toggle payload preview"
                            >
                              {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                            </button>
                          </td>
                          <td className="px-4 py-3">{eventPill(del.event)}</td>
                          <td className="px-4 py-3 font-mono text-xs text-muted-foreground" title={ep?.url}>
                            {ep ? truncateUrl(ep.url, 35) : del.endpointId}
                          </td>
                          <td className="px-4 py-3">{deliveryStatusBadge(del.status)}</td>
                          <td className="px-4 py-3 tabular-nums text-foreground">{del.responseStatus ?? <span className="text-muted-foreground">--</span>}</td>
                          <td className="px-4 py-3 tabular-nums text-foreground">{del.duration.toLocaleString()} ms</td>
                          <td className="px-4 py-3 tabular-nums text-foreground">{del.attempts}</td>
                          <td className="px-4 py-3 text-xs text-muted-foreground">{formatTimestamp(del.deliveredAt)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                  {/* Expanded payload rows rendered separately to keep table structure valid */}
                </table>

                {/* Payload previews below the table for expanded rows */}
                {deliveries
                  .filter((del) => expandedDeliveries.has(del.id))
                  .map((del) => (
                    <div key={`payload-${del.id}`} className="border-t border-border bg-muted/20 px-6 py-3">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Payload</span>
                        <span className="text-[10px] text-muted-foreground">({del.event})</span>
                      </div>
                      <pre className="overflow-x-auto rounded-lg border border-border bg-background p-3 text-xs font-mono text-foreground">
                        {JSON.stringify(JSON.parse(del.payload), null, 2)}
                      </pre>
                      {del.responseBody && (
                        <div className="mt-2">
                          <span className="text-[10px] font-semibold uppercase tracking-wider text-red-500">Response Body</span>
                          <pre className="mt-1 overflow-x-auto rounded-lg border border-red-500/20 bg-red-500/5 p-3 text-xs font-mono text-red-600 dark:text-red-400">
                            {del.responseBody}
                          </pre>
                        </div>
                      )}
                    </div>
                  ))}
              </div>
            </CardContent>
          </Card>

          {/* ── Section 3: Signature Verification ── */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2"><ShieldCheck className="h-5 w-5" aria-hidden /> Signature Verification</CardTitle>
              <CardDescription>
                Every webhook delivery includes an <code className="rounded bg-muted px-1 py-0.5 text-xs font-mono">X-TalkLee-Signature</code> header.
                Verify the HMAC-SHA256 signature against the endpoint signing secret to ensure authenticity.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* Signing secrets per endpoint */}
              <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                      <th className="px-4 py-3">Endpoint</th>
                      <th className="px-4 py-3">Signing Secret</th>
                      <th className="px-4 py-3 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {endpoints.map((ep) => (
                      <tr key={ep.id} className="border-b border-border last:border-b-0 hover:bg-muted/20 transition-colors">
                        <td className="px-4 py-3">
                          <div className="font-mono text-xs text-foreground" title={ep.url}>{truncateUrl(ep.url, 45)}</div>
                          <div className="text-xs text-muted-foreground mt-0.5">{ep.description}</div>
                        </td>
                        <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{ep.secret}</td>
                        <td className="px-4 py-3 text-right">
                          <Button variant="outline" size="sm" title="Rotate signing secret">
                            <RotateCcw className="mr-1 h-3.5 w-3.5" aria-hidden /> Rotate Secret
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Verification example */}
              <div>
                <h3 className="text-sm font-semibold text-foreground mb-2">Verification Example (Node.js)</h3>
                <pre className="overflow-x-auto rounded-xl border border-border bg-muted/30 p-4 text-xs font-mono text-foreground leading-relaxed">
                  {VERIFICATION_SNIPPET}
                </pre>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* ── Add Endpoint Modal ── */}
        {showAddModal && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
            <div className="w-full max-w-lg rounded-2xl border border-border bg-background p-6 shadow-xl">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-bold text-foreground">Add Webhook Endpoint</h2>
                <button onClick={() => setShowAddModal(false)} className="text-muted-foreground hover:text-foreground transition-colors">
                  <X className="h-5 w-5" />
                </button>
              </div>

              <div className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="webhook-url">Endpoint URL</Label>
                  <Input
                    id="webhook-url"
                    placeholder="https://example.com/webhooks/talklee"
                    value={newUrl}
                    onChange={(e) => setNewUrl(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="webhook-desc">Description</Label>
                  <Input
                    id="webhook-desc"
                    placeholder="e.g. Production billing events"
                    value={newDescription}
                    onChange={(e) => setNewDescription(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label>Event Types</Label>
                  <div className="grid grid-cols-2 gap-2 max-h-52 overflow-y-auto rounded-lg border border-border p-3">
                    {ALL_EVENT_TYPES.map((event) => (
                      <label key={event} className="flex items-center gap-2 cursor-pointer text-xs text-foreground hover:text-foreground/80">
                        <input
                          type="checkbox"
                          checked={newEvents.includes(event)}
                          onChange={() => toggleEvent(event)}
                          className="h-3.5 w-3.5 rounded border-border accent-indigo-500"
                        />
                        {event}
                      </label>
                    ))}
                  </div>
                  {newEvents.length > 0 && (
                    <p className="text-xs text-muted-foreground">{newEvents.length} event{newEvents.length !== 1 ? "s" : ""} selected</p>
                  )}
                </div>
              </div>

              <div className="mt-6 flex items-center justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => setShowAddModal(false)}>Cancel</Button>
                <Button
                  size="sm"
                  disabled={!newUrl.trim() || newEvents.length === 0}
                  onClick={handleAddEndpoint}
                >
                  <Plus className="mr-1 h-4 w-4" aria-hidden /> Create Endpoint
                </Button>
              </div>
            </div>
          </div>
        )}
      </RouteGuard>
    </DashboardLayout>
  );
}
