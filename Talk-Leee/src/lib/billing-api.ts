"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ApiClientError } from "@/lib/http-client";

// ── Fetch helper ──
//
// Phase 5 universal-auth-state: this helper delegates to the shared `api`
// client so requests participate in refresh-on-401, single-flight refresh
// dedup, fresh-login grace, and the unified session-expired redirect latch.
//
// FAILURES THROW. They used to be swallowed into `null` by a bare
// `catch { return null }`, which meant a 403, an expired session or a
// backend outage reached the billing page as an ordinary empty result — and
// the page rendered it as "0 of 0 minutes used" with "No invoices yet".
// That is a confident statement about a customer's money that nothing had
// verified. Letting the rejection through is what gives the calling
// `useQuery` a real `isError`, the same contract every other module in
// `src/lib` already uses (see `topup-api.ts` and `extended-api.ts`).

type BillingFetchInit = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: string;
};

async function billingFetch<T>(path: string, options?: BillingFetchInit): Promise<T> {
  const method = (options?.method as "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | undefined) ?? "GET";
  // billing hooks historically pass `body` as a JSON-encoded string
  // (JSON.stringify(...)); the shared client expects an object, so
  // parse it back. Empty/no body stays undefined.
  let body: unknown;
  if (options?.body !== undefined) {
    try {
      body = JSON.parse(options.body);
    } catch {
      body = options.body;
    }
  }
  return await api.request<T>({ path, method, body });
}

/**
 * For the one billing read where "not there" is a real answer rather than a
 * failure: `GET /billing/invoices/{id}` answers 404 for an id that is not
 * this tenant's, and the detail page has an honest "Invoice not found"
 * screen for exactly that. Every other status still throws.
 *
 * Same shape as `extended-api.ts` `getCallFeedback` / `getMyReview`.
 */
async function billingFetchOptional<T>(path: string): Promise<T | null> {
  try {
    return await billingFetch<T>(path);
  } catch (err) {
    if (err instanceof ApiClientError && err.status === 404) return null;
    throw err;
  }
}

// ── Query Keys ──

export const billingKeys = {
  plan: () => ["billing", "plan"] as const,
  usage: () => ["billing", "usage"] as const,
  dailyUsage: () => ["billing", "dailyUsage"] as const,
  invoices: () => ["billing", "invoices"] as const,
  invoice: (id: string) => ["billing", "invoice", id] as const,
  plans: () => ["billing", "plans"] as const,
  adjustments: () => ["billing", "adjustments"] as const,
  overageAlerts: () => ["billing", "overageAlerts"] as const,
  partnerBilling: () => ["billing", "partnerBilling"] as const,
  tenantBilling: () => ["billing", "tenantBilling"] as const,
  apiKeys: () => ["admin", "apiKeys"] as const,
  webhookEndpoints: () => ["admin", "webhookEndpoints"] as const,
  webhookDeliveries: () => ["admin", "webhookDeliveries"] as const,
  rateLimitRules: () => ["admin", "rateLimitRules"] as const,
  callGuardRules: () => ["admin", "callGuardRules"] as const,
  tenantLimits: () => ["admin", "tenantLimits"] as const,
  partnerLimits: () => ["admin", "partnerLimits"] as const,
  abuseEvents: () => ["admin", "abuseEvents"] as const,
  blockedEntities: () => ["admin", "blockedEntities"] as const,
  secrets: () => ["admin", "secrets"] as const,
};

// ── Billing Hooks ──

export function useBillingPlan() {
  return useQuery({
    queryKey: billingKeys.plan(),
    queryFn: async () => {
      // Backend exposes /billing/subscription (plan + status + period).
      const data = await billingFetch("/billing/subscription");
      // A successful response with no body is a genuine "no subscription",
      // which the page renders as "Choose a plan". A FAILED request now
      // throws instead of arriving here as null.
      return data ?? null;
    },
  });
}

export function useBillingUsage() {
  return useQuery({
    queryKey: billingKeys.usage(),
    queryFn: async () => {
      // Backend exposes /billing/usage (summary for current period).
      const data = await billingFetch("/billing/usage");
      return data ?? null;
    },
  });
}

export function useDailyUsage() {
  return useQuery({
    queryKey: billingKeys.dailyUsage(),
    queryFn: async () => {
      const data = await billingFetch("/billing/usage/daily");
      return data ?? [];
    },
  });
}

export function useBillingInvoices() {
  return useQuery({
    queryKey: billingKeys.invoices(),
    queryFn: async () => {
      const data = await billingFetch("/billing/invoices");
      return data ?? [];
    },
  });
}

export function useBillingInvoice(id: string) {
  return useQuery({
    queryKey: billingKeys.invoice(id),
    queryFn: async () => {
      // 404 here means the invoice does not exist — an answer, not an
      // outage — so it comes back as null and the page says so.
      const data = await billingFetchOptional(`/billing/invoices/${encodeURIComponent(id)}`);
      return data ?? null;
    },
    enabled: Boolean(id),
  });
}

export function useBillingPlans() {
  return useQuery({
    queryKey: billingKeys.plans(),
    queryFn: async () => {
      const data = await billingFetch("/billing/plans");
      return data ?? [];
    },
  });
}

export function useBillingAdjustments() {
  return useQuery({
    queryKey: billingKeys.adjustments(),
    queryFn: async () => {
      const data = await billingFetch("/billing/adjustments");
      return data ?? [];
    },
  });
}

export function useOverageAlerts() {
  return useQuery({
    queryKey: billingKeys.overageAlerts(),
    queryFn: async () => {
      const data = await billingFetch("/billing/overage-alerts");
      return data ?? [];
    },
  });
}

export function usePartnerBilling() {
  return useQuery({
    queryKey: billingKeys.partnerBilling(),
    queryFn: async () => {
      const data = await billingFetch("/billing/partners");
      return data ?? [];
    },
  });
}

export function useTenantBilling() {
  return useQuery({
    queryKey: billingKeys.tenantBilling(),
    queryFn: async () => {
      const data = await billingFetch("/billing/tenants");
      return data ?? [];
    },
  });
}

// ── Admin Hooks ──

export function useApiKeys() {
  return useQuery({
    queryKey: billingKeys.apiKeys(),
    queryFn: async () => {
      const data = await billingFetch("/admin/api-keys");
      return data ?? [];
    },
  });
}

export function useWebhookEndpoints() {
  return useQuery({
    queryKey: billingKeys.webhookEndpoints(),
    queryFn: async () => {
      const data = await billingFetch("/admin/webhooks");
      return data ?? [];
    },
  });
}

export function useWebhookDeliveries() {
  return useQuery({
    queryKey: billingKeys.webhookDeliveries(),
    queryFn: async () => {
      const data = await billingFetch("/admin/webhooks/deliveries");
      return data ?? [];
    },
  });
}

export function useRateLimitRules() {
  return useQuery({
    queryKey: billingKeys.rateLimitRules(),
    queryFn: async () => {
      const data = await billingFetch("/admin/rate-limits");
      return data ?? [];
    },
  });
}

export function useCallGuardRules() {
  return useQuery({
    queryKey: billingKeys.callGuardRules(),
    queryFn: async () => {
      const data = await billingFetch("/admin/call-guards");
      return data ?? [];
    },
  });
}

export function useTenantLimits() {
  return useQuery({
    queryKey: billingKeys.tenantLimits(),
    queryFn: async () => {
      const data = await billingFetch("/admin/tenant-limits");
      return data ?? [];
    },
  });
}

export function usePartnerLimits() {
  return useQuery({
    queryKey: billingKeys.partnerLimits(),
    queryFn: async () => {
      const data = await billingFetch("/admin/partner-limits");
      return data ?? [];
    },
  });
}

export function useAbuseEvents() {
  return useQuery({
    queryKey: billingKeys.abuseEvents(),
    queryFn: async () => {
      const data = await billingFetch("/admin/abuse-events");
      return data ?? [];
    },
  });
}

export function useBlockedEntities() {
  return useQuery({
    queryKey: billingKeys.blockedEntities(),
    queryFn: async () => {
      const data = await billingFetch("/admin/blocked-entities");
      return data ?? [];
    },
  });
}

export function useSecrets() {
  return useQuery({
    queryKey: billingKeys.secrets(),
    queryFn: async () => {
      const data = await billingFetch("/admin/secrets");
      return data ?? [];
    },
  });
}

// ── Mutation Hooks ──

export function useCreateApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { name: string; scopes: string[]; rateLimit?: number; expiresAt?: string }) =>
      billingFetch("/admin/api-keys", { method: "POST", body: JSON.stringify(input) }),
    onSettled: () => { void qc.invalidateQueries({ queryKey: billingKeys.apiKeys() }); },
    onError: (err) => { console.error("Failed to create API key:", err); },
  });
}

export function useRevokeApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      billingFetch(`/admin/api-keys/${encodeURIComponent(id)}/revoke`, { method: "POST" }),
    onSettled: () => { void qc.invalidateQueries({ queryKey: billingKeys.apiKeys() }); },
    onError: (err) => { console.error("Failed to revoke API key:", err); },
  });
}

export function useCreateWebhook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { url: string; description: string; events: string[] }) =>
      billingFetch("/admin/webhooks", { method: "POST", body: JSON.stringify(input) }),
    onSettled: () => { void qc.invalidateQueries({ queryKey: billingKeys.webhookEndpoints() }); },
    onError: (err) => { console.error("Failed to create webhook:", err); },
  });
}

export function useDeleteWebhook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      billingFetch(`/admin/webhooks/${encodeURIComponent(id)}`, { method: "DELETE" }),
    onSettled: () => { void qc.invalidateQueries({ queryKey: billingKeys.webhookEndpoints() }); },
    onError: (err) => { console.error("Failed to delete webhook:", err); },
  });
}

export function useTestWebhook() {
  return useMutation({
    mutationFn: (id: string) =>
      billingFetch(`/admin/webhooks/${encodeURIComponent(id)}/test`, { method: "POST" }),
    onError: (err) => { console.error("Failed to test webhook:", err); },
  });
}

export function useCreateRateLimitRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { name: string; scope: string; endpoint: string; maxRequests: number; windowSeconds: number; burstLimit?: number; action: string }) =>
      billingFetch("/admin/rate-limits", { method: "POST", body: JSON.stringify(input) }),
    onSettled: () => { void qc.invalidateQueries({ queryKey: billingKeys.rateLimitRules() }); },
    onError: (err) => { console.error("Failed to create rate limit rule:", err); },
  });
}

export function useToggleRateLimitRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; status: "active" | "inactive" }) =>
      billingFetch(`/admin/rate-limits/${encodeURIComponent(input.id)}`, { method: "PATCH", body: JSON.stringify({ status: input.status }) }),
    onSettled: () => { void qc.invalidateQueries({ queryKey: billingKeys.rateLimitRules() }); },
    onError: (err) => { console.error("Failed to toggle rate limit rule:", err); },
  });
}

export function useToggleCallGuard() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; enabled: boolean }) =>
      billingFetch(`/admin/call-guards/${encodeURIComponent(input.id)}`, { method: "PATCH", body: JSON.stringify({ enabled: input.enabled }) }),
    onSettled: () => { void qc.invalidateQueries({ queryKey: billingKeys.callGuardRules() }); },
    onError: (err) => { console.error("Failed to toggle call guard:", err); },
  });
}

export function useUpdateTenantLimit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { tenantId: string; maxConcurrentCalls?: number; maxCallsPerMinute?: number; maxCallsPerHour?: number; maxCallDurationSeconds?: number }) =>
      billingFetch(`/admin/tenant-limits/${encodeURIComponent(input.tenantId)}`, { method: "PUT", body: JSON.stringify(input) }),
    onSettled: () => { void qc.invalidateQueries({ queryKey: billingKeys.tenantLimits() }); },
    onError: (err) => { console.error("Failed to update tenant limit:", err); },
  });
}

export function useUpdatePartnerLimit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { partnerId: string; maxTenants?: number; maxTotalConcurrentCalls?: number; maxCallsPerMinute?: number; maxCallsPerHour?: number }) =>
      billingFetch(`/admin/partner-limits/${encodeURIComponent(input.partnerId)}`, { method: "PUT", body: JSON.stringify(input) }),
    onSettled: () => { void qc.invalidateQueries({ queryKey: billingKeys.partnerLimits() }); },
    onError: (err) => { console.error("Failed to update partner limit:", err); },
  });
}

export function useBlockEntity() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { type: string; value: string; reason: string; expiresAt?: string }) =>
      billingFetch("/admin/blocked-entities", { method: "POST", body: JSON.stringify(input) }),
    onSettled: () => { void qc.invalidateQueries({ queryKey: billingKeys.blockedEntities() }); },
    onError: (err) => { console.error("Failed to block entity:", err); },
  });
}

export function useUnblockEntity() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      billingFetch(`/admin/blocked-entities/${encodeURIComponent(id)}`, { method: "DELETE" }),
    onSettled: () => { void qc.invalidateQueries({ queryKey: billingKeys.blockedEntities() }); },
    onError: (err) => { console.error("Failed to unblock entity:", err); },
  });
}

export function useRotateSecret() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      billingFetch(`/admin/secrets/${encodeURIComponent(id)}/rotate`, { method: "POST" }),
    onSettled: () => { void qc.invalidateQueries({ queryKey: billingKeys.secrets() }); },
    onError: (err) => { console.error("Failed to rotate secret:", err); },
  });
}

export function useChangePlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { planId: string; isYearly: boolean }) =>
      billingFetch("/billing/plan/change", { method: "POST", body: JSON.stringify(input) }),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: billingKeys.plan() });
      void qc.invalidateQueries({ queryKey: billingKeys.plans() });
    },
    onError: (err) => { console.error("Failed to change plan:", err); },
  });
}

export function useCreateAdjustment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { type: string; description: string; amount: number; reason: string }) =>
      billingFetch("/billing/adjustment", { method: "POST", body: JSON.stringify(input) }),
    onSettled: () => { void qc.invalidateQueries({ queryKey: billingKeys.adjustments() }); },
    onError: (err) => { console.error("Failed to create adjustment:", err); },
  });
}
