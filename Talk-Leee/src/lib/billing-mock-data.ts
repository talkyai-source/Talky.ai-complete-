/**
 * Mock data for frontend billing layer development.
 * Replace with real API calls when backend is connected.
 */

import type {
  BillingPlan,
  TenantPlan,
  UsageSummary,
  Invoice,
  InvoiceLineItem,
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

export const PLANS: BillingPlan[] = [
  {
    id: "plan_starter",
    name: "Starter",
    tier: "starter",
    priceMonthly: 29,
    priceYearly: 290,
    includedMinutes: 500,
    includedConcurrentCalls: 5,
    overagePerMinute: 0.08,
    overageConcurrencyPerSlot: 15,
    features: ["500 included minutes", "5 concurrent calls", "Basic analytics", "Email support", "1 AI voice agent"],
  },
  {
    id: "plan_professional",
    name: "Professional",
    tier: "professional",
    priceMonthly: 79,
    priceYearly: 790,
    includedMinutes: 2000,
    includedConcurrentCalls: 20,
    overagePerMinute: 0.06,
    overageConcurrencyPerSlot: 12,
    features: ["2,000 included minutes", "20 concurrent calls", "Advanced analytics", "Priority support", "5 AI voice agents", "CRM integration", "Call recordings"],
    recommended: true,
  },
  {
    id: "plan_business",
    name: "Business",
    tier: "business",
    priceMonthly: 199,
    priceYearly: 1990,
    includedMinutes: 7500,
    includedConcurrentCalls: 50,
    overagePerMinute: 0.04,
    overageConcurrencyPerSlot: 10,
    features: ["7,500 included minutes", "50 concurrent calls", "Full analytics suite", "Dedicated support", "Unlimited AI voice agents", "All integrations", "Custom voices", "White-label option"],
  },
  {
    id: "plan_enterprise",
    name: "Enterprise",
    tier: "enterprise",
    priceMonthly: 499,
    priceYearly: 4990,
    includedMinutes: 25000,
    includedConcurrentCalls: 200,
    overagePerMinute: 0.03,
    overageConcurrencyPerSlot: 8,
    features: ["25,000 included minutes", "200 concurrent calls", "Enterprise analytics", "24/7 dedicated support", "Unlimited everything", "SLA guarantee", "Custom integrations", "Dedicated account manager", "Multi-tenant management"],
  },
];

// ── Current Tenant Plan ──

const now = new Date();
const cycleStart = new Date(now.getFullYear(), now.getMonth(), 1);
const cycleEnd = new Date(now.getFullYear(), now.getMonth() + 1, 0);

export const CURRENT_TENANT_PLAN: TenantPlan = {
  planId: "plan_professional",
  plan: PLANS[1],
  billingState: "active",
  billingCycleStart: cycleStart.toISOString(),
  billingCycleEnd: cycleEnd.toISOString(),
  isYearly: false,
  nextInvoiceDate: cycleEnd.toISOString(),
};

// ── Usage Summary ──

export const CURRENT_USAGE: UsageSummary = {
  period: "monthly",
  date: now.toISOString(),
  minutesUsed: 1437,
  minutesIncluded: 2000,
  minutesOverage: 0,
  peakConcurrency: 14,
  concurrencyLimit: 20,
  totalCalls: 892,
  successfulCalls: 847,
  failedCalls: 45,
  averageCallDuration: 96,
};

export const DAILY_USAGE: UsageSummary[] = Array.from({ length: 14 }, (_, i) => {
  const d = new Date();
  d.setDate(d.getDate() - (13 - i));
  const used = Math.floor(60 + Math.random() * 140);
  return {
    period: "daily" as const,
    date: d.toISOString(),
    minutesUsed: used,
    minutesIncluded: Math.floor(2000 / 30),
    minutesOverage: 0,
    peakConcurrency: Math.floor(3 + Math.random() * 12),
    concurrencyLimit: 20,
    totalCalls: Math.floor(40 + Math.random() * 80),
    successfulCalls: Math.floor(35 + Math.random() * 70),
    failedCalls: Math.floor(2 + Math.random() * 10),
    averageCallDuration: Math.floor(60 + Math.random() * 60),
  };
});

// ── Overage Alerts ──

export const OVERAGE_ALERTS: OverageAlert[] = [];

// ── Adjustments ──

export const ADJUSTMENTS: BillingAdjustment[] = [
  { id: "adj_001", type: "credit", description: "Service interruption credit", amount: -15.0, appliedAt: "2026-03-15T10:30:00Z", appliedBy: "Support Team", reason: "Compensation for 2-hour outage on March 14" },
  { id: "adj_002", type: "promo", description: "Referral bonus", amount: -25.0, appliedAt: "2026-02-20T14:00:00Z", appliedBy: "System", reason: "Referral program — referred TechCorp Inc." },
  { id: "adj_003", type: "debit", description: "Premium voice add-on", amount: 10.0, appliedAt: "2026-03-01T00:00:00Z", appliedBy: "System", reason: "Monthly add-on charge for premium voice package" },
  { id: "adj_004", type: "refund", description: "Billing correction", amount: -8.5, appliedAt: "2026-01-28T16:45:00Z", appliedBy: "Billing Admin", reason: "Duplicate charge corrected" },
];

// ── Invoices ──

function buildLineItems(plan: BillingPlan, usedMinutes: number, overageMinutes: number): InvoiceLineItem[] {
  const items: InvoiceLineItem[] = [
    { description: `${plan.name} Plan — Monthly subscription`, quantity: 1, unitPrice: plan.priceMonthly, total: plan.priceMonthly },
    { description: `Included minutes (${plan.includedMinutes.toLocaleString()})`, quantity: plan.includedMinutes, unitPrice: 0, total: 0 },
  ];
  if (overageMinutes > 0) {
    items.push({ description: "Overage minutes", quantity: overageMinutes, unitPrice: plan.overagePerMinute, total: +(overageMinutes * plan.overagePerMinute).toFixed(2) });
  }
  return items;
}

export const INVOICES: Invoice[] = [
  {
    id: "INV-2026-0003",
    tenantId: "tenant_001",
    billingPeriodStart: "2026-03-01T00:00:00Z",
    billingPeriodEnd: "2026-03-31T23:59:59Z",
    planName: "Professional",
    planFee: 79,
    includedMinutes: 2000,
    usedMinutes: 2340,
    overageMinutes: 340,
    overageCharges: 20.4,
    includedConcurrentCalls: 20,
    peakConcurrentCalls: 18,
    adjustments: [ADJUSTMENTS[0]],
    subtotal: 84.4,
    tax: 0,
    totalAmount: 84.4,
    status: "paid",
    paidAt: "2026-04-02T09:15:00Z",
    dueDate: "2026-04-05T00:00:00Z",
    createdAt: "2026-04-01T00:00:00Z",
    lineItems: [...buildLineItems(PLANS[1], 2340, 340), { description: "Service interruption credit", quantity: 1, unitPrice: -15, total: -15 }],
  },
  {
    id: "INV-2026-0002",
    tenantId: "tenant_001",
    billingPeriodStart: "2026-02-01T00:00:00Z",
    billingPeriodEnd: "2026-02-28T23:59:59Z",
    planName: "Professional",
    planFee: 79,
    includedMinutes: 2000,
    usedMinutes: 1820,
    overageMinutes: 0,
    overageCharges: 0,
    includedConcurrentCalls: 20,
    peakConcurrentCalls: 12,
    adjustments: [ADJUSTMENTS[1]],
    subtotal: 54,
    tax: 0,
    totalAmount: 54,
    status: "paid",
    paidAt: "2026-03-03T11:20:00Z",
    dueDate: "2026-03-05T00:00:00Z",
    createdAt: "2026-03-01T00:00:00Z",
    lineItems: [...buildLineItems(PLANS[1], 1820, 0), { description: "Referral bonus", quantity: 1, unitPrice: -25, total: -25 }],
  },
  {
    id: "INV-2026-0001",
    tenantId: "tenant_001",
    billingPeriodStart: "2026-01-01T00:00:00Z",
    billingPeriodEnd: "2026-01-31T23:59:59Z",
    planName: "Professional",
    planFee: 79,
    includedMinutes: 2000,
    usedMinutes: 1950,
    overageMinutes: 0,
    overageCharges: 0,
    includedConcurrentCalls: 20,
    peakConcurrentCalls: 15,
    adjustments: [],
    subtotal: 79,
    tax: 0,
    totalAmount: 79,
    status: "paid",
    paidAt: "2026-02-02T08:00:00Z",
    dueDate: "2026-02-05T00:00:00Z",
    createdAt: "2026-02-01T00:00:00Z",
    lineItems: buildLineItems(PLANS[1], 1950, 0),
  },
];

// ── Partner Billing ──

export const PARTNER_BILLING: PartnerBillingSummary[] = [
  { partnerId: "partner_001", partnerName: "Acme Solutions", totalTenants: 12, activeTenants: 10, totalMinutesUsed: 18500, totalMinutesIncluded: 25000, totalOverageMinutes: 0, totalRevenue: 2388, totalOverageCharges: 0, billingState: "active", peakConcurrency: 42 },
  { partnerId: "partner_002", partnerName: "TechBridge Corp", totalTenants: 8, activeTenants: 7, totalMinutesUsed: 14200, totalMinutesIncluded: 15000, totalOverageMinutes: 0, totalRevenue: 1592, totalOverageCharges: 0, billingState: "active", peakConcurrency: 28 },
  { partnerId: "partner_003", partnerName: "GlobalVoice Ltd", totalTenants: 5, activeTenants: 5, totalMinutesUsed: 8900, totalMinutesIncluded: 7500, totalOverageMinutes: 1400, totalRevenue: 1051, totalOverageCharges: 56, billingState: "active", peakConcurrency: 19 },
  { partnerId: "partner_004", partnerName: "CloudConnect Inc", totalTenants: 3, activeTenants: 1, totalMinutesUsed: 420, totalMinutesIncluded: 2000, totalOverageMinutes: 0, totalRevenue: 237, totalOverageCharges: 0, billingState: "past_due", peakConcurrency: 4 },
];

// ── Tenant Billing ──

export const TENANT_BILLING: TenantBillingSummary[] = [
  { tenantId: "t_001", tenantName: "Sunrise Dental", partnerId: "partner_001", planName: "Professional", minutesUsed: 1820, minutesIncluded: 2000, overageMinutes: 0, peakConcurrency: 8, concurrencyLimit: 20, totalCharges: 79, billingState: "active", lastPaymentDate: "2026-04-02", lastPaymentStatus: "paid" },
  { tenantId: "t_002", tenantName: "Metro Realty Group", partnerId: "partner_001", planName: "Business", minutesUsed: 5400, minutesIncluded: 7500, overageMinutes: 0, peakConcurrency: 22, concurrencyLimit: 50, totalCharges: 199, billingState: "active", lastPaymentDate: "2026-04-01", lastPaymentStatus: "paid" },
  { tenantId: "t_003", tenantName: "QuickFix Auto", partnerId: "partner_002", planName: "Starter", minutesUsed: 620, minutesIncluded: 500, overageMinutes: 120, peakConcurrency: 4, concurrencyLimit: 5, totalCharges: 38.6, billingState: "active", lastPaymentDate: "2026-04-03", lastPaymentStatus: "paid" },
  { tenantId: "t_004", tenantName: "Evergreen Health", partnerId: "partner_002", planName: "Professional", minutesUsed: 1100, minutesIncluded: 2000, overageMinutes: 0, peakConcurrency: 6, concurrencyLimit: 20, totalCharges: 79, billingState: "active", lastPaymentDate: "2026-03-30", lastPaymentStatus: "paid" },
  { tenantId: "t_005", tenantName: "PeakView Financial", partnerId: "partner_003", planName: "Business", minutesUsed: 8900, minutesIncluded: 7500, overageMinutes: 1400, peakConcurrency: 19, concurrencyLimit: 50, totalCharges: 255, billingState: "active", lastPaymentDate: "2026-04-01", lastPaymentStatus: "paid" },
  { tenantId: "t_006", tenantName: "StaleStartup LLC", partnerId: "partner_004", planName: "Starter", minutesUsed: 12, minutesIncluded: 500, overageMinutes: 0, peakConcurrency: 1, concurrencyLimit: 5, totalCharges: 29, billingState: "past_due", lastPaymentDate: "2026-02-01", lastPaymentStatus: "past_due" },
];

// ── Audit Logs ──

export const AUDIT_LOGS: AuditLogEntry[] = [
  { id: "log_001", timestamp: "2026-04-13T14:32:00Z", userId: "u_001", userName: "admin@talkly.ai", action: "user.login", category: "auth", details: "Successful login via OTP", ipAddress: "192.168.1.100", severity: "info" },
  { id: "log_002", timestamp: "2026-04-13T14:10:00Z", userId: "u_002", userName: "partner@acme.com", action: "role.changed", category: "role", details: "Role changed from user to partner_admin", ipAddress: "10.0.0.55", severity: "warning" },
  { id: "log_003", timestamp: "2026-04-13T13:45:00Z", userId: "u_001", userName: "admin@talkly.ai", action: "billing.plan_changed", category: "billing", details: "Plan upgraded from Starter to Professional", ipAddress: "192.168.1.100", severity: "info" },
  { id: "log_004", timestamp: "2026-04-13T12:20:00Z", userId: "u_003", userName: "john@sunrise.com", action: "user.login_failed", category: "auth", details: "Failed login attempt — invalid OTP (attempt 3/5)", ipAddress: "203.0.113.42", severity: "warning" },
  { id: "log_005", timestamp: "2026-04-13T11:55:00Z", userId: "u_001", userName: "admin@talkly.ai", action: "tenant.suspended", category: "suspension", details: "Tenant StaleStartup LLC suspended — payment overdue 45 days", ipAddress: "192.168.1.100", severity: "critical" },
  { id: "log_006", timestamp: "2026-04-13T10:30:00Z", userId: "u_004", userName: "sarah@techbridge.io", action: "billing.adjustment", category: "billing", details: "Credit of $15.00 applied — service interruption compensation", ipAddress: "172.16.0.88", severity: "info" },
  { id: "log_007", timestamp: "2026-04-12T22:10:00Z", userId: "u_005", userName: "mike@globalvoice.com", action: "security.mfa_enabled", category: "security", details: "MFA (TOTP) enabled for account", ipAddress: "10.10.5.12", severity: "info" },
  { id: "log_008", timestamp: "2026-04-12T18:45:00Z", userId: "u_001", userName: "admin@talkly.ai", action: "settings.webhook_updated", category: "settings", details: "Webhook endpoint updated for billing events", ipAddress: "192.168.1.100", severity: "info" },
  { id: "log_009", timestamp: "2026-04-12T15:20:00Z", userId: "u_006", userName: "lisa@peakview.com", action: "user.logout_all", category: "auth", details: "Logged out from all devices (5 sessions terminated)", ipAddress: "198.51.100.23", severity: "warning" },
  { id: "log_010", timestamp: "2026-04-12T09:00:00Z", userId: "u_001", userName: "admin@talkly.ai", action: "billing.invoice_generated", category: "billing", details: "Invoice INV-2026-0003 generated for March 2026 — $84.40", ipAddress: "192.168.1.100", severity: "info" },
  { id: "log_011", timestamp: "2026-04-11T14:30:00Z", userId: "u_002", userName: "partner@acme.com", action: "tenant.created", category: "role", details: "New tenant Metro Realty Group created under Acme Solutions", ipAddress: "10.0.0.55", severity: "info" },
  { id: "log_012", timestamp: "2026-04-11T11:15:00Z", userId: "u_007", userName: "new@quickfix.com", action: "user.registered", category: "auth", details: "New user registration completed", ipAddress: "203.0.113.88", severity: "info" },
];

// ── API Keys (DAY 6) ──

export const API_KEYS: ApiKey[] = [
  { id: "key_001", name: "Production API Key", keyPrefix: "tlk_prod_a8f2", createdAt: "2026-03-01T10:00:00Z", lastUsedAt: "2026-04-14T08:30:00Z", expiresAt: "2027-03-01T10:00:00Z", status: "active", scopes: ["calls:read", "calls:write", "campaigns:read", "contacts:read", "contacts:write"], createdBy: "admin@talkly.ai", rateLimit: 1000 },
  { id: "key_002", name: "Analytics Dashboard", keyPrefix: "tlk_prod_c4e1", createdAt: "2026-02-15T14:00:00Z", lastUsedAt: "2026-04-13T22:10:00Z", expiresAt: "2027-02-15T14:00:00Z", status: "active", scopes: ["analytics:read", "calls:read"], createdBy: "admin@talkly.ai", rateLimit: 500 },
  { id: "key_003", name: "CRM Integration", keyPrefix: "tlk_prod_9b3d", createdAt: "2026-01-20T09:00:00Z", lastUsedAt: "2026-04-10T16:45:00Z", expiresAt: "2027-01-20T09:00:00Z", status: "active", scopes: ["contacts:read", "contacts:write", "calls:read"], createdBy: "partner@acme.com", rateLimit: 200 },
  { id: "key_004", name: "Old Staging Key", keyPrefix: "tlk_stg_f7a2", createdAt: "2025-10-01T08:00:00Z", lastUsedAt: "2025-12-15T11:00:00Z", status: "revoked", scopes: ["calls:read"], createdBy: "admin@talkly.ai" },
  { id: "key_005", name: "Webhook Sender", keyPrefix: "tlk_prod_2e8f", createdAt: "2026-03-20T12:00:00Z", lastUsedAt: "2026-04-14T07:00:00Z", expiresAt: "2026-09-20T12:00:00Z", status: "active", scopes: ["webhooks:write", "calls:read"], createdBy: "admin@talkly.ai", rateLimit: 300 },
];

// ── Webhook Endpoints (DAY 6) ──

export const WEBHOOK_ENDPOINTS: WebhookEndpoint[] = [
  { id: "wh_001", url: "https://api.acme.com/webhooks/talklee", description: "Main production webhook for call events", events: ["call.started", "call.ended", "call.failed"], status: "active", secret: "whsec_••••••••••••a8f2", createdAt: "2026-02-01T10:00:00Z", updatedAt: "2026-04-10T08:00:00Z", failureCount: 0, lastDeliveryAt: "2026-04-14T08:30:00Z", lastDeliveryStatus: "success" },
  { id: "wh_002", url: "https://billing.acme.com/hooks/talklee", description: "Billing event notifications", events: ["billing.invoice_created", "billing.payment_received", "billing.payment_failed", "billing.plan_changed"], status: "active", secret: "whsec_••••••••••••c4e1", createdAt: "2026-02-15T14:00:00Z", updatedAt: "2026-04-01T12:00:00Z", failureCount: 2, lastDeliveryAt: "2026-04-13T22:10:00Z", lastDeliveryStatus: "success" },
  { id: "wh_003", url: "https://old-system.example.com/webhook", description: "Legacy system integration (deprecated)", events: ["call.ended"], status: "failing", secret: "whsec_••••••••••••9b3d", createdAt: "2025-11-01T09:00:00Z", updatedAt: "2026-03-15T16:00:00Z", failureCount: 47, lastDeliveryAt: "2026-04-12T14:20:00Z", lastDeliveryStatus: "failed" },
  { id: "wh_004", url: "https://security.internal/alerts", description: "Security event alerts", events: ["tenant.suspended", "partner.suspended", "security.mfa_enabled", "security.login_failed"], status: "active", secret: "whsec_••••••••••••f7a2", createdAt: "2026-03-01T08:00:00Z", updatedAt: "2026-04-14T06:00:00Z", failureCount: 0, lastDeliveryAt: "2026-04-14T06:00:00Z", lastDeliveryStatus: "success" },
];

export const WEBHOOK_DELIVERIES: WebhookDelivery[] = [
  { id: "del_001", endpointId: "wh_001", event: "call.ended", payload: '{"call_id":"c_892","duration":124,"status":"completed"}', responseStatus: 200, deliveredAt: "2026-04-14T08:30:00Z", duration: 145, status: "success", attempts: 1 },
  { id: "del_002", endpointId: "wh_001", event: "call.started", payload: '{"call_id":"c_893","from":"+1555123456"}', responseStatus: 200, deliveredAt: "2026-04-14T08:28:00Z", duration: 89, status: "success", attempts: 1 },
  { id: "del_003", endpointId: "wh_002", event: "billing.payment_received", payload: '{"invoice_id":"INV-2026-0003","amount":84.40}', responseStatus: 200, deliveredAt: "2026-04-13T22:10:00Z", duration: 230, status: "success", attempts: 1 },
  { id: "del_004", endpointId: "wh_003", event: "call.ended", payload: '{"call_id":"c_890","duration":60}', responseStatus: null, deliveredAt: "2026-04-12T14:20:00Z", duration: 30000, status: "failed", attempts: 3, responseBody: "Connection refused" },
  { id: "del_005", endpointId: "wh_004", event: "security.login_failed", payload: '{"user_id":"u_003","ip":"203.0.113.42","attempts":3}', responseStatus: 200, deliveredAt: "2026-04-14T06:00:00Z", duration: 112, status: "success", attempts: 1 },
  { id: "del_006", endpointId: "wh_002", event: "billing.plan_changed", payload: '{"tenant_id":"t_001","old_plan":"starter","new_plan":"professional"}', responseStatus: 200, deliveredAt: "2026-04-13T14:30:00Z", duration: 178, status: "success", attempts: 1 },
  { id: "del_007", endpointId: "wh_003", event: "call.ended", payload: '{"call_id":"c_888","duration":45}', responseStatus: null, deliveredAt: "2026-04-12T12:00:00Z", duration: 30000, status: "failed", attempts: 3, responseBody: "Connection refused" },
  { id: "del_008", endpointId: "wh_001", event: "call.failed", payload: '{"call_id":"c_887","reason":"no_answer"}', responseStatus: 200, deliveredAt: "2026-04-14T07:50:00Z", duration: 95, status: "success", attempts: 1 },
];

// ── Rate Limiting Rules (DAY 6) ──

export const RATE_LIMIT_RULES: RateLimitRule[] = [
  { id: "rl_001", name: "API Default Rate Limit", scope: "per_user", endpoint: "/api/v1/*", maxRequests: 100, windowSeconds: 60, burstLimit: 20, status: "active", action: "reject", createdAt: "2026-01-15T10:00:00Z", updatedAt: "2026-04-01T08:00:00Z", currentUsage: 42 },
  { id: "rl_002", name: "Login Rate Limit", scope: "per_ip", endpoint: "/auth/login", maxRequests: 5, windowSeconds: 300, burstLimit: 3, status: "active", action: "reject", createdAt: "2026-01-15T10:00:00Z", updatedAt: "2026-03-20T12:00:00Z", currentUsage: 1 },
  { id: "rl_003", name: "Tenant API Limit", scope: "per_tenant", endpoint: "/api/v1/*", maxRequests: 500, windowSeconds: 60, burstLimit: 100, status: "active", action: "throttle", createdAt: "2026-02-01T09:00:00Z", updatedAt: "2026-04-10T14:00:00Z", currentUsage: 185 },
  { id: "rl_004", name: "Call Initiation Limit", scope: "per_tenant", endpoint: "/api/v1/calls", maxRequests: 30, windowSeconds: 60, burstLimit: 10, status: "active", action: "reject", createdAt: "2026-02-15T11:00:00Z", updatedAt: "2026-04-05T09:00:00Z", currentUsage: 8 },
  { id: "rl_005", name: "Webhook Delivery Limit", scope: "global", endpoint: "/billing/webhooks/*", maxRequests: 1000, windowSeconds: 60, status: "active", action: "throttle", createdAt: "2026-03-01T08:00:00Z", updatedAt: "2026-04-01T08:00:00Z", currentUsage: 234 },
  { id: "rl_006", name: "Registration Limit", scope: "per_ip", endpoint: "/auth/register", maxRequests: 3, windowSeconds: 3600, status: "active", action: "reject", createdAt: "2026-01-15T10:00:00Z", updatedAt: "2026-01-15T10:00:00Z", currentUsage: 0 },
  { id: "rl_007", name: "CSV Upload Limit", scope: "per_user", endpoint: "/api/v1/contacts/upload", maxRequests: 10, windowSeconds: 3600, status: "inactive", action: "log_only", createdAt: "2026-03-10T14:00:00Z", updatedAt: "2026-04-01T10:00:00Z" },
];

// ── Call Guard Rules (DAY 7) ──

export const CALL_GUARD_RULES: CallGuardRule[] = [
  { id: "cg_001", name: "Tenant Active Check", check: "tenant_active", enabled: true, action: "block", priority: 1, description: "Block calls if the tenant account is suspended or inactive", lastTriggeredAt: "2026-04-12T14:20:00Z", triggerCount: 3 },
  { id: "cg_002", name: "Partner Active Check", check: "partner_active", enabled: true, action: "block", priority: 2, description: "Block calls if the parent partner is suspended", lastTriggeredAt: "2026-04-10T09:00:00Z", triggerCount: 1 },
  { id: "cg_003", name: "Concurrency Limit Check", check: "concurrency_limit", enabled: true, action: "block", priority: 3, description: "Reject new calls when concurrent call limit is reached for the tenant", lastTriggeredAt: "2026-04-14T07:30:00Z", triggerCount: 28 },
  { id: "cg_004", name: "Call Rate Limit Check", check: "rate_limit", enabled: true, action: "block", priority: 4, description: "Enforce per-minute call rate limits to prevent rapid dialing", lastTriggeredAt: "2026-04-13T16:45:00Z", triggerCount: 12 },
  { id: "cg_005", name: "Feature Entitlement Check", check: "allowed_feature", enabled: true, action: "block", priority: 5, description: "Verify the tenant has the required feature enabled for the call type", triggerCount: 0 },
  { id: "cg_006", name: "Billing Active Check", check: "billing_active", enabled: true, action: "warn", priority: 6, description: "Warn or block calls when billing is past due or in grace period", lastTriggeredAt: "2026-04-11T11:00:00Z", triggerCount: 7 },
  { id: "cg_007", name: "Caller Whitelist Check", check: "caller_whitelist", enabled: false, action: "log_only", priority: 7, description: "Only allow calls from pre-approved caller IDs (disabled by default)", triggerCount: 0 },
];

// ── Tenant Limits (DAY 7) ──

export const TENANT_LIMITS: TenantLimit[] = [
  { tenantId: "t_001", tenantName: "Sunrise Dental", maxConcurrentCalls: 20, maxCallsPerMinute: 30, maxCallsPerHour: 500, maxCallDurationSeconds: 3600, allowedFeatures: ["outbound", "inbound", "transfer", "recording", "ai_agent"], status: "active", updatedAt: "2026-04-01T10:00:00Z" },
  { tenantId: "t_002", tenantName: "Metro Realty Group", maxConcurrentCalls: 50, maxCallsPerMinute: 60, maxCallsPerHour: 1000, maxCallDurationSeconds: 7200, allowedFeatures: ["outbound", "inbound", "transfer", "recording", "ai_agent", "voicemail"], status: "active", updatedAt: "2026-04-01T10:00:00Z" },
  { tenantId: "t_003", tenantName: "QuickFix Auto", maxConcurrentCalls: 5, maxCallsPerMinute: 10, maxCallsPerHour: 100, maxCallDurationSeconds: 1800, allowedFeatures: ["outbound", "inbound"], status: "active", updatedAt: "2026-03-15T14:00:00Z" },
  { tenantId: "t_004", tenantName: "Evergreen Health", maxConcurrentCalls: 20, maxCallsPerMinute: 25, maxCallsPerHour: 400, maxCallDurationSeconds: 3600, allowedFeatures: ["outbound", "inbound", "transfer", "recording"], status: "active", updatedAt: "2026-03-20T09:00:00Z" },
  { tenantId: "t_005", tenantName: "PeakView Financial", maxConcurrentCalls: 50, maxCallsPerMinute: 50, maxCallsPerHour: 800, maxCallDurationSeconds: 5400, allowedFeatures: ["outbound", "inbound", "transfer", "recording", "ai_agent", "voicemail", "analytics"], status: "active", updatedAt: "2026-04-05T11:00:00Z" },
  { tenantId: "t_006", tenantName: "StaleStartup LLC", maxConcurrentCalls: 5, maxCallsPerMinute: 5, maxCallsPerHour: 50, maxCallDurationSeconds: 1800, allowedFeatures: ["outbound", "inbound"], status: "suspended", updatedAt: "2026-04-13T12:00:00Z" },
];

// ── Partner Limits (DAY 7) ──

export const PARTNER_LIMITS: PartnerLimit[] = [
  { partnerId: "partner_001", partnerName: "Acme Solutions", maxTenants: 25, maxTotalConcurrentCalls: 100, maxCallsPerMinute: 120, maxCallsPerHour: 3000, allowedFeatures: ["outbound", "inbound", "transfer", "recording", "ai_agent", "voicemail", "analytics", "white_label"], status: "active", updatedAt: "2026-04-01T10:00:00Z" },
  { partnerId: "partner_002", partnerName: "TechBridge Corp", maxTenants: 15, maxTotalConcurrentCalls: 60, maxCallsPerMinute: 80, maxCallsPerHour: 2000, allowedFeatures: ["outbound", "inbound", "transfer", "recording", "ai_agent"], status: "active", updatedAt: "2026-03-20T09:00:00Z" },
  { partnerId: "partner_003", partnerName: "GlobalVoice Ltd", maxTenants: 10, maxTotalConcurrentCalls: 50, maxCallsPerMinute: 60, maxCallsPerHour: 1500, allowedFeatures: ["outbound", "inbound", "transfer", "recording", "ai_agent", "analytics"], status: "active", updatedAt: "2026-04-05T14:00:00Z" },
  { partnerId: "partner_004", partnerName: "CloudConnect Inc", maxTenants: 5, maxTotalConcurrentCalls: 15, maxCallsPerMinute: 20, maxCallsPerHour: 500, allowedFeatures: ["outbound", "inbound"], status: "restricted", updatedAt: "2026-04-13T12:00:00Z" },
];

// ── Abuse Events (DAY 7) ──

export const ABUSE_EVENTS: AbuseEvent[] = [
  { id: "abuse_001", type: "rapid_dialing", severity: "high", tenantId: "t_003", tenantName: "QuickFix Auto", sourceIp: "203.0.113.42", description: "45 calls initiated in 60 seconds — exceeds normal pattern by 10x", detectedAt: "2026-04-14T07:30:00Z", status: "investigating", callCount: 45, metadata: { normal_rate: "4", detected_rate: "45" } },
  { id: "abuse_002", type: "concurrent_flood", severity: "critical", tenantId: "t_005", tenantName: "PeakView Financial", description: "Attempted to open 120 concurrent calls (limit: 50)", detectedAt: "2026-04-13T16:45:00Z", status: "resolved", actionTaken: "Calls exceeding limit were rejected. Tenant admin notified.", callCount: 120, metadata: { limit: "50", attempted: "120" } },
  { id: "abuse_003", type: "short_duration_spam", severity: "medium", tenantId: "t_001", tenantName: "Sunrise Dental", sourceIp: "10.0.0.55", description: "87 calls with average duration < 3 seconds in the last hour", detectedAt: "2026-04-13T14:20:00Z", status: "dismissed", actionTaken: "Reviewed — legitimate robocall detection test by tenant.", callCount: 87 },
  { id: "abuse_004", type: "credential_stuffing", severity: "high", tenantId: "t_006", tenantName: "StaleStartup LLC", sourceIp: "198.51.100.0/24", description: "142 failed login attempts from IP range in 10 minutes", detectedAt: "2026-04-12T22:00:00Z", status: "resolved", actionTaken: "IP range blocked. User passwords reset.", metadata: { attempts: "142", unique_emails: "38" } },
  { id: "abuse_005", type: "unusual_destination", severity: "medium", tenantId: "t_002", tenantName: "Metro Realty Group", description: "Calls to 12 different premium-rate international numbers detected", detectedAt: "2026-04-12T10:30:00Z", status: "open", callCount: 12, metadata: { destinations: "premium_rate", countries: "5" } },
  { id: "abuse_006", type: "after_hours_spike", severity: "low", tenantId: "t_004", tenantName: "Evergreen Health", description: "Unusual call volume spike (35 calls) detected between 2:00 AM - 3:00 AM", detectedAt: "2026-04-11T03:00:00Z", status: "dismissed", actionTaken: "Reviewed — scheduled campaign for different timezone.", callCount: 35 },
  { id: "abuse_007", type: "api_scraping", severity: "high", tenantId: "t_003", tenantName: "QuickFix Auto", sourceIp: "203.0.113.99", description: "API endpoint /contacts scraped at 500 req/min from single IP", detectedAt: "2026-04-10T18:15:00Z", status: "resolved", actionTaken: "IP blocked. API key rate limit reduced.", metadata: { requests_per_min: "500", endpoint: "/api/v1/contacts" } },
  { id: "abuse_008", type: "geo_anomaly", severity: "medium", tenantId: "t_001", tenantName: "Sunrise Dental", sourceIp: "192.0.2.50", description: "Login from unusual location (Eastern Europe) — account normally active in US West Coast", detectedAt: "2026-04-10T04:20:00Z", status: "open", metadata: { usual_location: "US-CA", detected_location: "RO" } },
];

// ── Blocked Entities (DAY 7) ──

export const BLOCKED_ENTITIES: BlockedEntity[] = [
  { id: "blk_001", type: "ip", value: "203.0.113.42", reason: "Rapid dialing abuse — exceeded rate limits repeatedly", blockedAt: "2026-04-14T07:35:00Z", blockedBy: "system_auto", status: "active" },
  { id: "blk_002", type: "ip", value: "198.51.100.0/24", reason: "Credential stuffing attack — 142 failed logins", blockedAt: "2026-04-12T22:05:00Z", blockedBy: "admin@talkly.ai", expiresAt: "2026-04-19T22:05:00Z", status: "active" },
  { id: "blk_003", type: "ip", value: "203.0.113.99", reason: "API scraping — 500 req/min on /contacts endpoint", blockedAt: "2026-04-10T18:20:00Z", blockedBy: "admin@talkly.ai", status: "active" },
  { id: "blk_004", type: "phone_number", value: "+1-900-555-0100", reason: "Premium rate number — blocked by policy", blockedAt: "2026-04-12T10:35:00Z", blockedBy: "system_auto", status: "active" },
  { id: "blk_005", type: "phone_number", value: "+44-909-555-0200", reason: "Premium rate number — blocked by policy", blockedAt: "2026-04-12T10:35:00Z", blockedBy: "system_auto", status: "active" },
  { id: "blk_006", type: "ip", value: "192.0.2.100", reason: "Suspicious activity — temporary block for investigation", blockedAt: "2026-04-08T14:00:00Z", blockedBy: "admin@talkly.ai", expiresAt: "2026-04-11T14:00:00Z", status: "expired" },
];

// ── Secrets / Environment Management (DAY 8) ──

export const SECRETS: SecretEntry[] = [
  { id: "sec_001", name: "STRIPE_SECRET_KEY", category: "payment", maskedValue: "sk_live_••••••••••••a8f2", environment: "production", lastRotatedAt: "2026-03-15T10:00:00Z", rotationIntervalDays: 90, isExpired: false, createdAt: "2025-06-01T08:00:00Z", updatedAt: "2026-03-15T10:00:00Z", updatedBy: "admin@talkly.ai", description: "Stripe production secret key for payment processing" },
  { id: "sec_002", name: "STRIPE_WEBHOOK_SECRET", category: "payment", maskedValue: "whsec_••••••••••••c4e1", environment: "production", lastRotatedAt: "2026-03-15T10:00:00Z", rotationIntervalDays: 90, isExpired: false, createdAt: "2025-06-01T08:00:00Z", updatedAt: "2026-03-15T10:00:00Z", updatedBy: "admin@talkly.ai", description: "Stripe webhook signing secret" },
  { id: "sec_003", name: "DATABASE_URL", category: "database", maskedValue: "postgresql://••••••••@db.prod:5432/talklee", environment: "production", lastRotatedAt: "2026-02-01T08:00:00Z", rotationIntervalDays: 180, isExpired: false, createdAt: "2025-01-01T08:00:00Z", updatedAt: "2026-02-01T08:00:00Z", updatedBy: "admin@talkly.ai", description: "Primary PostgreSQL database connection string" },
  { id: "sec_004", name: "TWILIO_AUTH_TOKEN", category: "voice_provider", maskedValue: "••••••••••••9b3d", environment: "production", lastRotatedAt: "2026-01-10T09:00:00Z", rotationIntervalDays: 90, isExpired: true, createdAt: "2025-03-01T08:00:00Z", updatedAt: "2026-01-10T09:00:00Z", updatedBy: "admin@talkly.ai", description: "Twilio authentication token for voice calls" },
  { id: "sec_005", name: "SENDGRID_API_KEY", category: "email", maskedValue: "SG.••••••••••••f7a2", environment: "production", lastRotatedAt: "2026-04-01T12:00:00Z", rotationIntervalDays: 90, isExpired: false, createdAt: "2025-04-01T08:00:00Z", updatedAt: "2026-04-01T12:00:00Z", updatedBy: "admin@talkly.ai", description: "SendGrid API key for transactional emails" },
  { id: "sec_006", name: "REDIS_URL", category: "database", maskedValue: "redis://••••••••@cache.prod:6379", environment: "production", lastRotatedAt: "2026-02-01T08:00:00Z", rotationIntervalDays: 180, isExpired: false, createdAt: "2025-02-01T08:00:00Z", updatedAt: "2026-02-01T08:00:00Z", updatedBy: "admin@talkly.ai", description: "Redis connection for session cache and rate limiting" },
  { id: "sec_007", name: "JWT_SECRET", category: "api_key", maskedValue: "••••••••••••2e8f", environment: "production", lastRotatedAt: "2026-03-01T08:00:00Z", rotationIntervalDays: 60, isExpired: false, createdAt: "2025-01-01T08:00:00Z", updatedAt: "2026-03-01T08:00:00Z", updatedBy: "admin@talkly.ai", description: "JWT signing secret for access tokens" },
  { id: "sec_008", name: "S3_ACCESS_KEY", category: "storage", maskedValue: "AKIA••••••••••••4B2F", environment: "production", lastRotatedAt: "2026-04-10T10:00:00Z", rotationIntervalDays: 90, isExpired: false, createdAt: "2025-05-01T08:00:00Z", updatedAt: "2026-04-10T10:00:00Z", updatedBy: "admin@talkly.ai", description: "AWS S3 access key for call recordings and file storage" },
  { id: "sec_009", name: "DATADOG_API_KEY", category: "monitoring", maskedValue: "dd_••••••••••••8c1a", environment: "production", lastRotatedAt: "2026-02-15T14:00:00Z", rotationIntervalDays: 180, isExpired: false, createdAt: "2025-07-01T08:00:00Z", updatedAt: "2026-02-15T14:00:00Z", updatedBy: "admin@talkly.ai", description: "Datadog API key for monitoring and alerting" },
  { id: "sec_010", name: "OPENAI_API_KEY", category: "api_key", maskedValue: "sk-••••••••••••7d3e", environment: "production", lastRotatedAt: "2026-04-05T09:00:00Z", rotationIntervalDays: 90, isExpired: false, createdAt: "2025-08-01T08:00:00Z", updatedAt: "2026-04-05T09:00:00Z", updatedBy: "admin@talkly.ai", description: "OpenAI API key for AI voice agent processing" },
  { id: "sec_011", name: "STRIPE_SECRET_KEY", category: "payment", maskedValue: "sk_test_••••••••••••stg1", environment: "staging", lastRotatedAt: "2026-04-01T08:00:00Z", rotationIntervalDays: 90, isExpired: false, createdAt: "2025-06-01T08:00:00Z", updatedAt: "2026-04-01T08:00:00Z", updatedBy: "admin@talkly.ai", description: "Stripe test key for staging environment" },
  { id: "sec_012", name: "DATABASE_URL", category: "database", maskedValue: "postgresql://••••••••@db.staging:5432/talklee_stg", environment: "staging", lastRotatedAt: "2026-03-01T08:00:00Z", rotationIntervalDays: 180, isExpired: false, createdAt: "2025-01-01T08:00:00Z", updatedAt: "2026-03-01T08:00:00Z", updatedBy: "admin@talkly.ai", description: "Staging PostgreSQL database" },
];
