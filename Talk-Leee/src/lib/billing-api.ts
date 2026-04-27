"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CURRENT_TENANT_PLAN, CURRENT_USAGE, DAILY_USAGE, INVOICES, PLANS,
  ADJUSTMENTS, OVERAGE_ALERTS, PARTNER_BILLING, TENANT_BILLING,
  API_KEYS, WEBHOOK_ENDPOINTS, WEBHOOK_DELIVERIES, RATE_LIMIT_RULES,
  CALL_GUARD_RULES, TENANT_LIMITS, PARTNER_LIMITS, ABUSE_EVENTS,
  BLOCKED_ENTITIES, SECRETS,
} from "./billing-mock-data";

// ── Fetch helper ──

async function billingFetch<T>(path: string, options?: RequestInit): Promise<T | null> {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || "";
  if (!baseUrl) return null;
  try {
    const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
    const res = await fetch(`${baseUrl}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...options?.headers,
      },
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
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
      const data = await billingFetch("/billing/plan");
      return data ?? CURRENT_TENANT_PLAN;
    },
  });
}

export function useBillingUsage() {
  return useQuery({
    queryKey: billingKeys.usage(),
    queryFn: async () => {
      const data = await billingFetch("/billing/usage/summary");
      return data ?? CURRENT_USAGE;
    },
  });
}

export function useDailyUsage() {
  return useQuery({
    queryKey: billingKeys.dailyUsage(),
    queryFn: async () => {
      const data = await billingFetch("/billing/usage/daily");
      return data ?? DAILY_USAGE;
    },
  });
}

export function useBillingInvoices() {
  return useQuery({
    queryKey: billingKeys.invoices(),
    queryFn: async () => {
      const data = await billingFetch("/billing/invoices");
      return data ?? INVOICES;
    },
  });
}

export function useBillingInvoice(id: string) {
  return useQuery({
    queryKey: billingKeys.invoice(id),
    queryFn: async () => {
      const data = await billingFetch(`/billing/invoices/${encodeURIComponent(id)}`);
      return data ?? INVOICES.find((inv) => inv.id === id) ?? null;
    },
    enabled: Boolean(id),
  });
}

export function useBillingPlans() {
  return useQuery({
    queryKey: billingKeys.plans(),
    queryFn: async () => {
      const data = await billingFetch("/billing/plans");
      return data ?? PLANS;
    },
  });
}

export function useBillingAdjustments() {
  return useQuery({
    queryKey: billingKeys.adjustments(),
    queryFn: async () => {
      const data = await billingFetch("/billing/adjustments");
      return data ?? ADJUSTMENTS;
    },
  });
}

export function useOverageAlerts() {
  return useQuery({
    queryKey: billingKeys.overageAlerts(),
    queryFn: async () => {
      const data = await billingFetch("/billing/overage-alerts");
      return data ?? OVERAGE_ALERTS;
    },
  });
}

export function usePartnerBilling() {
  return useQuery({
    queryKey: billingKeys.partnerBilling(),
    queryFn: async () => {
      const data = await billingFetch("/billing/partners");
      return data ?? PARTNER_BILLING;
    },
  });
}

export function useTenantBilling() {
  return useQuery({
    queryKey: billingKeys.tenantBilling(),
    queryFn: async () => {
      const data = await billingFetch("/billing/tenants");
      return data ?? TENANT_BILLING;
    },
  });
}

// ── Admin Hooks ──

export function useApiKeys() {
  return useQuery({
    queryKey: billingKeys.apiKeys(),
    queryFn: async () => {
      const data = await billingFetch("/admin/api-keys");
      return data ?? API_KEYS;
    },
  });
}

export function useWebhookEndpoints() {
  return useQuery({
    queryKey: billingKeys.webhookEndpoints(),
    queryFn: async () => {
      const data = await billingFetch("/admin/webhooks");
      return data ?? WEBHOOK_ENDPOINTS;
    },
  });
}

export function useWebhookDeliveries() {
  return useQuery({
    queryKey: billingKeys.webhookDeliveries(),
    queryFn: async () => {
      const data = await billingFetch("/admin/webhooks/deliveries");
      return data ?? WEBHOOK_DELIVERIES;
    },
  });
}

export function useRateLimitRules() {
  return useQuery({
    queryKey: billingKeys.rateLimitRules(),
    queryFn: async () => {
      const data = await billingFetch("/admin/rate-limits");
      return data ?? RATE_LIMIT_RULES;
    },
  });
}

export function useCallGuardRules() {
  return useQuery({
    queryKey: billingKeys.callGuardRules(),
    queryFn: async () => {
      const data = await billingFetch("/admin/call-guards");
      return data ?? CALL_GUARD_RULES;
    },
  });
}

export function useTenantLimits() {
  return useQuery({
    queryKey: billingKeys.tenantLimits(),
    queryFn: async () => {
      const data = await billingFetch("/admin/tenant-limits");
      return data ?? TENANT_LIMITS;
    },
  });
}

export function usePartnerLimits() {
  return useQuery({
    queryKey: billingKeys.partnerLimits(),
    queryFn: async () => {
      const data = await billingFetch("/admin/partner-limits");
      return data ?? PARTNER_LIMITS;
    },
  });
}

export function useAbuseEvents() {
  return useQuery({
    queryKey: billingKeys.abuseEvents(),
    queryFn: async () => {
      const data = await billingFetch("/admin/abuse-events");
      return data ?? ABUSE_EVENTS;
    },
  });
}

export function useBlockedEntities() {
  return useQuery({
    queryKey: billingKeys.blockedEntities(),
    queryFn: async () => {
      const data = await billingFetch("/admin/blocked-entities");
      return data ?? BLOCKED_ENTITIES;
    },
  });
}

export function useSecrets() {
  return useQuery({
    queryKey: billingKeys.secrets(),
    queryFn: async () => {
      const data = await billingFetch("/admin/secrets");
      return data ?? SECRETS;
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
