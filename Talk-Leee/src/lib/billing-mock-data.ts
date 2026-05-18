/**
 * Empty-shell defaults for the billing layer.
 *
 * Previously this file held synthetic mock data (hard-coded plans,
 * sample invoices, fake adjustments etc.) which the billing/admin
 * pages imported directly. That bypassed the API hooks in
 * `billing-api.ts` and meant users saw fabricated numbers even when
 * the backend was healthy.
 *
 * Every export name is preserved so existing imports keep type-checking,
 * but the values are now empty/zero/structurally-minimal — pages render
 * "no data yet" instead of synthetic figures. The hooks in
 * `billing-api.ts` are the canonical source of real data; pages should
 * migrate to them incrementally.
 */

import type {
  BillingPlan,
  TenantPlan,
  UsageSummary,
  Invoice,
  BillingAdjustment,
  OverageAlert,
  PartnerBillingSummary,
  TenantBillingSummary,
  AuditLogEntry,
  ApiKey,
  WebhookEndpoint,
  WebhookDelivery,
  RateLimitRule,
  CallGuardRule,
  TenantLimit,
  PartnerLimit,
  AbuseEvent,
  BlockedEntity,
  SecretEntry,
} from "./billing-types";

// ── Plans ──

const EMPTY_PLAN: BillingPlan = {
  id: "",
  name: "—",
  tier: "starter",
  priceMonthly: 0,
  priceYearly: 0,
  includedMinutes: 0,
  includedConcurrentCalls: 0,
  overagePerMinute: 0,
  overageConcurrencyPerSlot: 0,
  features: [],
};

export const PLANS: BillingPlan[] = [];

// ── Current Tenant Plan ──

const _now = new Date();

export const CURRENT_TENANT_PLAN: TenantPlan = {
  planId: "",
  plan: EMPTY_PLAN,
  billingState: "active",
  billingCycleStart: _now.toISOString(),
  billingCycleEnd: _now.toISOString(),
  isYearly: false,
  nextInvoiceDate: _now.toISOString(),
};

// ── Usage Summary ──

export const CURRENT_USAGE: UsageSummary = {
  period: "monthly",
  date: _now.toISOString(),
  minutesUsed: 0,
  minutesIncluded: 0,
  minutesOverage: 0,
  peakConcurrency: 0,
  concurrencyLimit: 0,
  totalCalls: 0,
  successfulCalls: 0,
  failedCalls: 0,
  averageCallDuration: 0,
};

export const DAILY_USAGE: UsageSummary[] = [];

// ── Overage Alerts ──

export const OVERAGE_ALERTS: OverageAlert[] = [];

// ── Adjustments ──

export const ADJUSTMENTS: BillingAdjustment[] = [];

// ── Invoices ──

export const INVOICES: Invoice[] = [];

// ── Partner / Tenant billing ──

export const PARTNER_BILLING: PartnerBillingSummary[] = [];
export const TENANT_BILLING: TenantBillingSummary[] = [];

// ── Audit logs ──

export const AUDIT_LOGS: AuditLogEntry[] = [];

// ── API keys / webhooks ──

export const API_KEYS: ApiKey[] = [];
export const WEBHOOK_ENDPOINTS: WebhookEndpoint[] = [];
export const WEBHOOK_DELIVERIES: WebhookDelivery[] = [];

// ── Rate-limit rules / call guards ──

export const RATE_LIMIT_RULES: RateLimitRule[] = [];
export const CALL_GUARD_RULES: CallGuardRule[] = [];

// ── Tenant / partner limits ──

export const TENANT_LIMITS: TenantLimit[] = [];
export const PARTNER_LIMITS: PartnerLimit[] = [];

// ── Abuse / blocked / secrets ──

export const ABUSE_EVENTS: AbuseEvent[] = [];
export const BLOCKED_ENTITIES: BlockedEntity[] = [];
export const SECRETS: SecretEntry[] = [];
