/**
 * Billing Layer Type Definitions
 * Covers plans, usage, invoices, payments, and partner/tenant billing
 */

export type BillingState = "active" | "trialing" | "past_due" | "grace_period" | "suspended" | "canceled";

export type InvoiceStatus = "paid" | "open" | "past_due" | "void" | "draft";

export type OverageType = "minutes" | "concurrency";

export interface BillingPlan {
  id: string;
  name: string;
  tier: "starter" | "professional" | "business" | "enterprise";
  priceMonthly: number;
  priceYearly: number;
  includedMinutes: number;
  includedConcurrentCalls: number;
  overagePerMinute: number;
  overageConcurrencyPerSlot: number;
  features: string[];
  recommended?: boolean;
}

export interface TenantPlan {
  planId: string;
  plan: BillingPlan;
  billingState: BillingState;
  billingCycleStart: string;
  billingCycleEnd: string;
  isYearly: boolean;
  trialEndsAt?: string;
  canceledAt?: string;
  nextInvoiceDate: string;
}

export interface UsageSummary {
  period: "daily" | "monthly";
  date: string;
  minutesUsed: number;
  minutesIncluded: number;
  minutesOverage: number;
  peakConcurrency: number;
  concurrencyLimit: number;
  totalCalls: number;
  successfulCalls: number;
  failedCalls: number;
  averageCallDuration: number;
}

export interface UsageLedgerEntry {
  ledgerId: string;
  sessionId: string;
  partnerId?: string;
  tenantId: string;
  planId: string;
  callStartTime: string;
  answerTime?: string;
  endTime: string;
  billableDurationSeconds: number;
  billableMinutes: number;
  peakConcurrencySample: number;
  transferFlag: boolean;
  failureReason?: string;
  createdAt: string;
}

export interface Invoice {
  id: string;
  tenantId: string;
  partnerId?: string;
  billingPeriodStart: string;
  billingPeriodEnd: string;
  planName: string;
  planFee: number;
  includedMinutes: number;
  usedMinutes: number;
  overageMinutes: number;
  overageCharges: number;
  includedConcurrentCalls: number;
  peakConcurrentCalls: number;
  adjustments: BillingAdjustment[];
  subtotal: number;
  tax: number;
  totalAmount: number;
  status: InvoiceStatus;
  paidAt?: string;
  dueDate: string;
  createdAt: string;
  lineItems: InvoiceLineItem[];
}

export interface InvoiceLineItem {
  description: string;
  quantity: number;
  unitPrice: number;
  total: number;
}

export interface BillingAdjustment {
  id: string;
  type: "credit" | "debit" | "refund" | "promo";
  description: string;
  amount: number;
  appliedAt: string;
  appliedBy?: string;
  reason: string;
}

export interface OverageAlert {
  type: OverageType;
  currentUsage: number;
  limit: number;
  exceededBy: number;
  estimatedCharge: number;
  severity: "warning" | "critical";
}

export interface PartnerBillingSummary {
  partnerId: string;
  partnerName: string;
  totalTenants: number;
  activeTenants: number;
  totalMinutesUsed: number;
  totalMinutesIncluded: number;
  totalOverageMinutes: number;
  totalRevenue: number;
  totalOverageCharges: number;
  billingState: BillingState;
  peakConcurrency: number;
}

export interface TenantBillingSummary {
  tenantId: string;
  tenantName: string;
  partnerId?: string;
  planName: string;
  minutesUsed: number;
  minutesIncluded: number;
  overageMinutes: number;
  peakConcurrency: number;
  concurrencyLimit: number;
  totalCharges: number;
  billingState: BillingState;
  lastPaymentDate?: string;
  lastPaymentStatus?: InvoiceStatus;
}

export interface AuditLogEntry {
  id: string;
  timestamp: string;
  userId: string;
  userName: string;
  action: string;
  category: "auth" | "billing" | "role" | "suspension" | "settings" | "security";
  details: string;
  ipAddress: string;
  userAgent?: string;
  severity: "info" | "warning" | "critical";
}

// ── API Key Management (DAY 6) ──

export interface ApiKey {
  id: string;
  name: string;
  keyPrefix: string;
  createdAt: string;
  lastUsedAt?: string;
  expiresAt?: string;
  status: "active" | "revoked" | "expired";
  scopes: string[];
  createdBy: string;
  rateLimit?: number;
}

// ── Webhook Management (DAY 6) ──

export type WebhookEventType =
  | "call.started"
  | "call.ended"
  | "call.failed"
  | "billing.invoice_created"
  | "billing.payment_received"
  | "billing.payment_failed"
  | "billing.plan_changed"
  | "tenant.created"
  | "tenant.suspended"
  | "partner.suspended"
  | "security.mfa_enabled"
  | "security.login_failed";

export interface WebhookEndpoint {
  id: string;
  url: string;
  description: string;
  events: WebhookEventType[];
  status: "active" | "inactive" | "failing";
  secret: string;
  createdAt: string;
  updatedAt: string;
  failureCount: number;
  lastDeliveryAt?: string;
  lastDeliveryStatus?: "success" | "failed";
}

export interface WebhookDelivery {
  id: string;
  endpointId: string;
  event: WebhookEventType;
  payload: string;
  responseStatus: number | null;
  responseBody?: string;
  deliveredAt: string;
  duration: number;
  status: "success" | "failed" | "pending";
  attempts: number;
}

// ── Rate Limiting Configuration (DAY 6) ──

export type RateLimitScope = "per_user" | "per_tenant" | "per_ip" | "global";

export interface RateLimitRule {
  id: string;
  name: string;
  scope: RateLimitScope;
  endpoint: string;
  maxRequests: number;
  windowSeconds: number;
  burstLimit?: number;
  status: "active" | "inactive";
  action: "reject" | "throttle" | "log_only";
  createdAt: string;
  updatedAt: string;
  currentUsage?: number;
}

// ── Voice Security & Call Guards (DAY 7) ──

export interface CallGuardRule {
  id: string;
  name: string;
  check: "tenant_active" | "partner_active" | "concurrency_limit" | "rate_limit" | "allowed_feature" | "billing_active" | "caller_whitelist";
  enabled: boolean;
  action: "block" | "warn" | "log_only";
  priority: number;
  description: string;
  lastTriggeredAt?: string;
  triggerCount: number;
}

export interface TenantLimit {
  tenantId: string;
  tenantName: string;
  maxConcurrentCalls: number;
  maxCallsPerMinute: number;
  maxCallsPerHour: number;
  maxCallDurationSeconds: number;
  allowedFeatures: string[];
  status: "active" | "suspended" | "restricted";
  updatedAt: string;
}

export interface PartnerLimit {
  partnerId: string;
  partnerName: string;
  maxTenants: number;
  maxTotalConcurrentCalls: number;
  maxCallsPerMinute: number;
  maxCallsPerHour: number;
  allowedFeatures: string[];
  status: "active" | "suspended" | "restricted";
  updatedAt: string;
}

// ── Abuse Detection (DAY 7) ──

export type AbuseEventType =
  | "rapid_dialing"
  | "concurrent_flood"
  | "unusual_destination"
  | "short_duration_spam"
  | "after_hours_spike"
  | "credential_stuffing"
  | "api_scraping"
  | "geo_anomaly";

export type AbuseSeverity = "low" | "medium" | "high" | "critical";

export interface AbuseEvent {
  id: string;
  type: AbuseEventType;
  severity: AbuseSeverity;
  tenantId: string;
  tenantName: string;
  sourceIp?: string;
  description: string;
  detectedAt: string;
  status: "open" | "investigating" | "resolved" | "dismissed";
  actionTaken?: string;
  callCount?: number;
  metadata?: Record<string, string | number>;
}

export interface BlockedEntity {
  id: string;
  type: "ip" | "phone_number" | "tenant" | "user";
  value: string;
  reason: string;
  blockedAt: string;
  blockedBy: string;
  expiresAt?: string;
  status: "active" | "expired" | "removed";
}

// ── Secrets / Environment Management (DAY 8) ──

export type SecretCategory = "api_key" | "database" | "payment" | "voice_provider" | "email" | "storage" | "monitoring" | "other";

export interface SecretEntry {
  id: string;
  name: string;
  category: SecretCategory;
  maskedValue: string;
  environment: "production" | "staging" | "development";
  lastRotatedAt?: string;
  rotationIntervalDays?: number;
  isExpired: boolean;
  createdAt: string;
  updatedAt: string;
  updatedBy: string;
  description?: string;
}
