# Billing Layer Implementation Documentation

## Overview
The Billing Layer is implemented across the project with both Frontend and Backend components. It manages partner billing, usage accounts, tenant billing accounts, invoice history, overage tracking, and billing-related permissions and data.

---

## BACKEND IMPLEMENTATION

### 1. **src/server/rbac.ts** - RBAC (Role-Based Access Control) with Billing Account Management

#### Key Data Types & Structures:
- **Lines 5-14**: Permission definitions including `view_billing` permission
  ```typescript
  export type PermissionName =
      | "manage_partners"
      | "manage_tenants"
      | "manage_users"
      | "view_billing"          // Billing permission
      | "start_call"
      | "view_calls"
      | "manage_agent_settings";
  ```

- **Lines 38-49**: Billing and Usage Account Row Types
  ```typescript
  export type UsageAccountRow = {
      id: string;
      partner_id: string;
      tenant_id: string;
      created_at: Date;
  };

  export type BillingAccountRow = {
      id: string;
      partner_id: string;
      created_at: Date;
  };
  ```

#### Database Schema Creation (Lines 202-231):
- **Lines 203-209**: Billing Accounts Table Schema
  ```typescript
  create table if not exists billing_accounts (
      id uuid primary key,
      partner_id text not null references partners(partner_id) on delete cascade,
      created_at timestamptz not null default now()
  )
  ```
  - Unique constraint on `partner_id` (line 209)

- **Lines 211-220**: Usage Accounts Table Schema
  ```typescript
  create table if not exists usage_accounts (
      id uuid primary key,
      partner_id text not null references partners(partner_id) on delete cascade,
      tenant_id text not null references tenants(id) on delete cascade,
      created_at timestamptz not null default now()
  )
  ```
  - Unique constraint on `tenant_id` (line 219)
  - Index on `partner_id` (line 220)

- **Lines 222-231**: Tenant Accounts Table Schema
  ```typescript
  create table if not exists tenant_accounts (
      tenant_id text primary key references tenants(id) on delete cascade,
      usage_account_id uuid not null references usage_accounts(id) on delete restrict,
      billing_account_id uuid not null references billing_accounts(id) on delete restrict,
      created_at timestamptz not null default now()
  )
  ```
  - Maps tenant to both usage and billing accounts
  - Indexes on both foreign keys (lines 230-231)

#### Key Functions:
- **Lines 236-273**: `ensureTenantAccounts()` - Creates billing and usage accounts for a tenant
  - Creates entry in `billing_accounts` table (lines 245-251)
  - Creates entry in `usage_accounts` table (lines 255-261)
  - Links them in `tenant_accounts` table (lines 265-270)
  - Returns `{ usageAccountId, billingAccountId }`

- **Lines 275-291**: `getTenantAccounts()` - Retrieves billing context for a tenant
  - Queries `tenant_accounts` joined with `tenants` (lines 281-286)
  - Returns both account IDs and partner ID

#### RBAC Permissions Setup (Lines 326-346):
- **Line 313**: `view_billing` permission is assigned to:
  - `platform_admin` (line 329)
  - `partner_admin` (line 331)
  - Accessible by those who need billing visibility

---

### 2. **src/server/auth-core.ts** - Authentication with Billing Account Context

#### Key Data Structures:
- **Lines 25-53**: Database User Session Row with Billing Context
  ```typescript
  type DbSessionRow = {
      // ... other fields ...
      scope_usage_account_id: string | null;      // Line 51
      scope_billing_account_id: string | null;    // Line 52
  };
  ```
  - Session scopes track billing account context for each session

#### Billing Account Imports:
- **Line 6**: Imports from RBAC
  ```typescript
  import { 
      ensureDefaultPartnerAndTenantForUser, 
      ensureTenantAccounts,        // Used for billing setup
      getAuthzContextForUser, 
      getTenantAccounts,            // Used to fetch billing context
      type RoleName 
  } from "@/server/rbac";
  ```

---

### 3. **src/server/voice-security.ts** - Voice Call Security with Billing Guard

#### Key Data Structures:
- **Lines 43**: Billing Account ID field in voice call response
  ```typescript
  billingAccountId: string | null;
  ```

- **Lines 763-771**: Voice Call Billing Context Schemas
  ```typescript
  const VoiceCallActiveCallsSchema = z.object({
      tenant: z.number(),
      partner: z.number(),
  });

  const VoiceCallOverageSchema = z.object({
      tenant: z.boolean(),
      partner: z.boolean(),
  });
  ```

#### VoiceCallGuardResponse Schemas (Lines 803-840):
- **Lines 812-813**: Billing context in allowed response
  ```typescript
  usageAccountId: z.string().nullable(),
  billingAccountId: z.string().nullable(),
  ```

- **Lines 819-827**: Snake case alternative schema
  ```typescript
  usage_account_id: z.string().nullable(),
  billing_account_id: z.string().nullable(),
  ```

#### Core Function:
- **Lines 151**: `ensureBillingContext()` interface
  ```typescript
  ensureBillingContext(input: { 
      tenantId: string; 
      partnerId: string 
  }): Promise<{ 
      usageAccountId: string | null; 
      billingAccountId: string | null 
  }>;
  ```

- **Lines 822-824**: In-memory implementation for testing
  ```typescript
  return { usageAccountId: null, billingAccountId: null };
  return { 
      usageAccountId: accounts.usageAccountId, 
      billingAccountId: accounts.billingAccountId 
  };
  ```

- **Lines 1175-1244**: Billing context cache
  ```typescript
  const billingByTenant = new Map<string, { 
      usageAccountId: string; 
      billingAccountId: string 
  }>();
  ```

- **Lines 1697-1718**: Retrieving and returning billing context in voice call flow
  ```typescript
  const billing = await backend.ensureBillingContext({ 
      tenantId, 
      partnerId 
  });
  // ... response includes:
  usageAccountId: billing.usageAccountId,
  billingAccountId: billing.billingAccountId,
  ```

---

### 4. **src/app/api/v1/[...path]/route.ts** - API Route with Billing Support

#### Imports:
- **Line 51**: Voice security import with billing guard
  ```typescript
  import { 
      call_guard, 
      startGuardedVoiceCallSession 
  } from "@/server/voice-security";
  ```

---

### 5. **src/lib/models.ts** - Billing Data Models (Zod Schemas)

#### Voice Call Billing Schemas:
- **Lines 763-771**: Voice call active calls and overage billing states
  - Tracks concurrent calls per tenant and partner
  - Tracks whether overage is occurring for each level

- **Lines 803-840**: Voice Call Guard Allowed Response with Billing
  ```typescript
  const VoiceCallGuardAllowedCamelSchema = z.object({
      // ...
      usageAccountId: z.string().nullable(),
      billingAccountId: z.string().nullable(),
      // ...
  });

  const VoiceCallGuardAllowedSnakeSchema = z.object({
      // ...
      usage_account_id: z.string().nullable(),
      billing_account_id: z.string().nullable(),
      // ...
  })
  ```

- **Lines 851-890**: Voice Call Start Response with Billing
  ```typescript
  const VoiceCallStartAllowedSnakeSchema = z.object({
      // ...
      usage_account_id: z.string().nullable(),
      billing_account_id: z.string().nullable(),
      // ...
  })
  ```

---

## FRONTEND IMPLEMENTATION

### 1. **src/app/white-label/[partner]/billing/page.tsx** - Main Billing Page (Server Component)

#### Type Definitions (Lines 12-30):
- **Lines 12-17**: Invoice Type
  ```typescript
  type PartnerInvoice = {
      invoiceId: string;
      date: string;
      amount: number;
      status: "Paid" | "Open" | "Past Due";
  };
  ```

- **Lines 19-25**: Billing Cycle Type
  ```typescript
  type BillingCycle = {
      cycleStart: string;
      cycleEnd: string;
      minutesUsed: number;
      planLimit: number;
      estimatedCharges: number;
  };
  ```

- **Lines 27-30**: Overage Alert Type
  ```typescript
  type OverageAlert = {
      type: "minutes" | "concurrency";
      exceededBy: number;
  };
  ```

#### Utility Functions (Lines 32-53):
- **Lines 32-35**: `formatMonthYear()` - Formats ISO date to "Mon YYYY"
- **Lines 37-43**: `formatDateRange()` - Formats start and end dates
- **Lines 45-47**: `formatCurrency()` - Formats amount to USD currency
- **Lines 49-53**: `invoiceStatusBadgeClass()` - CSS classes for invoice status display

#### Main Page Component (Lines 55-221):
- **Lines 55-66**: Auth & Authorization Check
  ```typescript
  const me = await getServerMe();
  if (!me) redirect(`/auth/login?...`);
  if (me.role !== WHITE_LABEL_ADMIN_ROLE) redirect("/403");
  ```

- **Lines 68-78**: Sample Billing Cycle Data
  ```typescript
  const billingCycle: BillingCycle = {
      cycleStart: cycleStart.toISOString(),
      cycleEnd: cycleEnd.toISOString(),
      minutesUsed: 3200,
      planLimit: 7500,
      estimatedCharges: 84,
  };
  ```

- **Lines 80-84**: Sample Invoice History Data
  ```typescript
  const invoices: PartnerInvoice[] = [
      { invoiceId: "INV-1023", date: "2026-02-01T...", amount: 120, status: "Paid" },
      { invoiceId: "INV-1018", date: "2026-01-01T...", amount: 95, status: "Paid" },
      { invoiceId: "INV-1011", date: "2025-12-01T...", amount: 102, status: "Paid" },
  ];
  ```

- **Lines 86-90**: Overage Alerts Calculation
  ```typescript
  const overageAlerts: OverageAlert[] = [
      billingCycle.minutesUsed > billingCycle.planLimit
          ? { type: "minutes", exceededBy: billingCycle.minutesUsed - billingCycle.planLimit }
          : null,
  ].filter((x): x is OverageAlert => Boolean(x));
  ```

- **Lines 92-93**: Usage Calculation
  ```typescript
  const remainingMinutes = Math.max(0, billingCycle.planLimit - billingCycle.minutesUsed);
  const usagePct = billingCycle.planLimit > 0 ? Math.min(100, (billingCycle.minutesUsed / billingCycle.planLimit) * 100) : 0;
  ```

#### UI Sections (Lines 100-217):

**Current Billing Cycle Section (Lines 101-139)**:
- Displays cycle date range (lines 110-112)
- Shows minutes usage with progress bar (lines 114-126)
- Displays estimated charges (lines 128-136)

**Overage Alerts Section (Lines 141-175)**:
- Shows alert messages when limits are exceeded (lines 153-171)
- Displays warning icons and detailed messages (lines 159-169)
- Empty state when no overage (lines 147-150)

**Invoice History Section (Lines 177-217)**:
- Table with columns: Invoice ID, Billing Date, Total, Status (lines 187-191)
- Formatted invoice data with status badges (lines 194-213)
- Responsive table layout (lines 183-215)

---

### 2. **src/components/admin/admin-operations-console.tsx** - Admin Billing Event Tracking

#### Icon Mapping (Lines 108-123):
- **Lines 116-118**: Billing change event icon detection
  ```typescript
  if (eventType === "billing_change" || eventType.includes("billing")) {
      return <CreditCard className={iconClass} aria-hidden />;
  }
  ```

#### Import:
- **Line 4**: CreditCard icon from lucide-react for billing events

---

### 3. **src/components/dashboard/PartnerDashboard.tsx** - Billing Summary Card

#### Billing Summary Data (Lines 5, 20, 24, 31-33):
- **Line 5**: `billingSummary: string;` - Property to store billing data
- **Line 20**: Sample data "High-level monthly summary" returning billingSummary as "$320"
- **Line 24**: Sample data returning billingSummary as "$190"
- **Lines 31-33**: Dynamic calculation
  ```typescript
  const billingSummary = `$${(80 + (seed % 1200)).toLocaleString()}`;
  return { totalSubTenants, activeCalls, minutesUsed, billingSummary };
  ```

- **Line 68**: MetricCard displaying billing summary
  ```typescript
  <MetricCard 
      label="Billing Summary" 
      value={stats.billingSummary} 
      valueSuffix="This Month" 
      helper="High-level monthly summary." 
  />
  ```

---

## TEST FILES WITH BILLING DATA

### **src/lib/backend-api.voice-calls.test.ts** - Voice Call API Tests with Billing

#### Test Data (Lines 10-22 & 59-77):
- **Lines 19-20**: Guard response with billing accounts
  ```typescript
  usage_account_id: "usage-1",
  billing_account_id: "billing-1",
  ```

- **Lines 74-75**: Start response with billing accounts
  ```typescript
  usage_account_id: "usage-1",
  billing_account_id: "billing-1",
  ```

---

## BILLING LAYER OUTPUTS & FUNCTIONS

### Data Outputs:

1. **Billing Cycle Information**:
   - Current cycle start and end dates
   - Minutes used vs plan limit
   - Estimated charges
   - Remaining minutes calculation

2. **Invoice History**:
   - Invoice ID
   - Invoice date (month/year)
   - Amount due
   - Payment status (Paid, Open, Past Due)

3. **Overage Alerts**:
   - Alert type (minutes or concurrency exceeded)
   - Amount exceeded by
   - Warning messages to users

4. **Usage Metrics**:
   - Current usage percentage
   - Active call counts (tenant and partner level)
   - Overage flag (boolean per level)

5. **Account IDs**:
   - `usageAccountId` - UUID for tracking usage
   - `billingAccountId` - UUID for tracking billing

### Database Operations:

1. **Creating Billing Accounts**: `ensureTenantAccounts()`
   - Creates partner billing account
   - Creates tenant usage account
   - Links them in tenant_accounts table

2. **Retrieving Billing Context**: `getTenantAccounts()`
   - Fetches usage and billing account IDs for a tenant
   - Returns partner information

3. **Permission Checking**: `hasPermissionInPartner()` & `hasPermissionInTenant()`
   - Validates `view_billing` permission before displaying billing data

### API Endpoints:

1. **Voice Call Guard** (`/voice/calls/guard`):
   - POST request with tenant/partner IDs
   - Returns billing context (usage_account_id, billing_account_id)
   - Includes overage status and active call counts

2. **Voice Call Start** (`/voice/calls/start`):
   - POST request to start a call
   - Returns billing account information
   - Includes usage account context

### Frontend Displays:

1. **Billing Page** (`/white-label/[partner]/billing`):
   - Current cycle status
   - Usage progress bar
   - Estimated charges
   - Overage warnings
   - Invoice history table

2. **Admin Console**:
   - Billing change audit logs
   - Billing-related security events
   - Partner/Tenant suspension with billing impact

3. **Partner Dashboard**:
   - Billing summary card showing monthly costs
   - High-level overview for quick reference

---

## KEY RELATIONSHIPS

```
Partner (partner_id)
  ├─ billing_accounts (1:1 relationship via billing_accounts table)
  │   └─ id = billing_account_id
  │
  └─ Tenant (tenant_id)
      └─ usage_accounts (1:1 relationship via usage_accounts table)
          └─ id = usage_account_id
          
          └─ tenant_accounts (junction table)
              ├─ usage_account_id → usage_accounts(id)
              └─ billing_account_id → billing_accounts(id)
```

---

## PERMISSIONS STRUCTURE

- **`view_billing`** Permission:
  - Assigned to: `platform_admin`, `partner_admin`
  - Used to: Control access to billing data and pages
  - Checked in: `requireTenantAccess()`, `hasPermissionInPartner()`

---

## SECURITY FEATURES

1. **Role-Based Access Control**: Only authorized roles can view billing
2. **Database Constraints**: Foreign key relationships ensure data integrity
3. **User Authorization**: White-label admin role required for billing page
4. **Audit Logging**: Billing changes are tracked in audit logs
5. **Session Scoping**: Billing context can be scoped to sessions for security

---

## NOTES

- Billing data is currently using sample/mock data in the frontend (hardcoded invoices, usage)
- The actual billing calculations and invoice generation would be implemented in the backend
- Usage accounts track per-tenant usage
- Billing accounts are per-partner for consolidated billing
- Overage tracking is implemented in the voice-security layer
- The system supports multi-tenant, multi-partner hierarchical billing structure

---

## PLAN.txt DATA ORGANIZATION

### Frontend

#### User Interface & Display Components
- **Billing Page Components** (Plan.txt - Core Billing Structure):
  - Responsive billing dashboard for partner and tenant users
  - Accessible and intuitive navigation for billing information
  - Real-time or near-real-time usage metrics presentation

#### Invoice Display & Presentation
- **Invoice Content Requirements** (Plan.txt Lines 99-109):
  - Plan name display on invoices
  - Billing period presentation with start and end dates
  - Included minutes allocation per plan clearly labeled
  - Used/consumed minutes tracking and display
  - Overage minutes calculation and visual presentation
  - Included concurrent calls display with current usage
  - Peak concurrent calls observed during billing cycle
  - Extra charges and adjustments itemization with descriptions
  - Final amount due summary prominently displayed
  - Payment status indication (Paid, Open, Past Due, etc.)

#### Billing Cycle Visualization
- **Current Billing Cycle Display** (Plan.txt Lines 25-32):
  - Base subscription fee presentation
  - Progress bar or visual indicator for included minutes usage
  - Concurrency limit visualization with current peak displayed
  - Overage minutes highlighted when applicable
  - Estimated charges or bill preview
  - Remaining included minutes calculation
  - Time remaining in billing cycle
  - Usage percentage meter or gauge

#### Invoice History & Management Interface
- **Invoice History Table** (Plan.txt Lines 99-109):
  - Comprehensive table layout for historical invoice records
  - Invoice ID column with sortable/searchable functionality
  - Billing date column (formatted as month/year for clarity)
  - Amount due column (formatted in currency with proper symbols)
  - Payment status column with visual indicators/badges
  - Invoice download or details access functionality
  - Sortable and filterable invoice listings by date, status, or amount

#### Partner & Admin Dashboard Views
- **Partner Billing Dashboard** (Plan.txt Lines 84-90, 25-32):
  - Partner-level aggregated usage summary cards
  - High-level cost overview showing total monthly/period charges
  - Billing cycle status display with current period dates
  - Usage progress indicators with visual bars and percentages
  - Quick summary of billable dimensions:
    - Base subscription fee status
    - Included minutes remaining
    - Concurrent call utilization
    - Current overage charges if applicable

- **Admin Billing Visibility** (Plan.txt Lines 84-90):
  - Comprehensive dashboard view into every partner's usage metrics
  - Invoice state tracking and status across all partners
  - Partner billing account status monitoring interface
  - Tenant-level usage breakdown display with drill-down capability
  - Partner-specific billing trend analysis
  - Bulk invoice status indicators

#### Overage Alert & Warning Components
- **Overage Notifications & Alerts** (Plan.txt Lines 25-32, 49-56):
  - Alert UI for minute limit violations with warning styling
  - Alert UI for concurrent call limit violations
  - Warning messages displaying exact amounts exceeded by
  - Visual indicators (warning icons, alert badges) for overage states
  - Notifications when approaching limits (e.g., 80% usage)
  - Suggestions or prompts to upgrade plan
  - Color-coded status indicators (green = good, yellow = warning, red = critical)

#### Usage Summary & Metrics Visualization
- **Usage Data Display** (Plan.txt Lines 10-22):
  - Plan definition display showing what package was purchased
  - Included usage limits clearly presented
  - Current usage ledger summary showing actual consumption
  - Minutes tracking visualization with consumed vs. included breakdown
  - Concurrent calls tracking showing peak vs. limit
  - Overage logic display explaining extra charges
  - Real-time or periodic usage updates
  - Historical usage trends when available

#### Billing State Indicators
- **Account Status Display** (Plan.txt Lines 76-83):
  - Clear display of current billing state (active, trialing, past_due, etc.)
  - Grace period indicators with time remaining
  - Suspension warning messages
  - Cancellation status confirmation
  - Payment required alerts
  - Account health status visualization

#### Payment & Adjustment UI Elements
- **Payment Information** (Plan.txt Lines 69-74):
  - Payment status display (paid, pending, failed)
  - Payment date tracking
  - Payment method indicators
  - Retry payment button or manual payment submission option

- **Billing Adjustments Display** (Plan.txt Lines 17-22):
  - Credit display with reason/description
  - Adjustment history log
  - Explanation of manual adjustments or credits applied

---

### Backend

#### System Architecture & Purpose
- **Billing Layer Foundation** (Plan.txt Lines 4-8):
  - Billing layer positioned between session layer and payment infrastructure
  - Authoritative source of truth requirement for all billing calculations
  - Transparent billing logic ensuring customer understanding
  - Replay-safe billing calculations enabling audit and recomputation

#### Billing Coverage Areas
- **Comprehensive Billing Scope** (Plan.txt Lines 10-22):
  - Plan Definition: Complete package metadata and purchased tier information
  - Included Usage: Usage allowances per billing cycle per plan
  - Usage Ledger: Immutable, authoritative record of consumption
  - Minutes Tracking: Primary billable dimension duration tracking
  - Concurrent Calls: Peak concurrency measurement for capacity pricing
  - Overage Logic: Post-limit usage calculation and charging
  - Invoice Data: Structured billing output generation
  - Payment Status: Subscription/account status tracking
  - Webhook Synchronization: Payment provider state alignment
  - Adjustments/Credits: Correction mechanism for billing disputes
  - Partner Aggregation: Multi-level usage consolidation
  - Tenant Breakdown: Per-customer usage isolation and transparency

#### Billable Dimensions & Plan Structure
- **Plan Dimensions** (Plan.txt Lines 25-32):
  - Base subscription fee per billing cycle
  - Included minutes per billing cycle allocation
  - Included concurrent calls per billing cycle allocation
  - Overage minutes pricing and calculation
  - Optional overage concurrency or hard cap enforcement
  - Optional add-ons: additional sub-tenants, advanced analytics, premium voices, white-label branding
  - Per-minute overage rates specification
  - Per-concurrency-unit overage rates specification

#### Billing Truth: Usage Ledger Architecture
- **Immutable Usage Ledger Implementation** (Plan.txt Lines 33-48):
  - Source of billing truth derived exclusively from session lifecycle events
  - Immutable record creation and storage (append-only pattern)
  - Backend event-driven population (not frontend-driven)
  - Complete ledger entry structure with all required fields:
    - `ledger_id`: Unique immutable ledger entry identifier
    - `session_id`: Reference to corresponding voice session
    - `partner_id`: Partner account identifier for attribution
    - `tenant_id`: Tenant/customer identifier for attribution
    - `plan_id`: Active plan reference at time of consumption
    - `call_start_time`: Call initiation timestamp (ring start or media start)
    - `answer_time`: Call answer timestamp for answered calls
    - `end_time`: Call termination or disconnect timestamp
    - `billable_duration_seconds`: Precise duration in seconds
    - `billable_minutes`: Rounded billable duration per rounding rules
    - `peak_concurrency_sample`: Peak concurrent calls during session
    - `transfer_flag`: Indicator for transferred calls
    - `failure_reason`: Failure documentation for failed calls
    - `created_at`: Ledger entry creation timestamp

#### Billable Event Rules & Decision Framework
- **Billable Event Logic** (Plan.txt Lines 49-56):
  - Billing start point definition decision: Ring start vs. answer time vs. media start
  - Minute rounding methodology specification: Exact seconds vs. per-second billing vs. per-minute rounding
  - Failed call billing determination: Billable or non-billable status for failures
  - Transfer call handling specification: Single billable record vs. multiple linked records
  - Included minutes exhaustion logic: Hard stop enforcement vs. overage allowance
  - Concurrency limit behavior: Rejection policy vs. overage charging capability
  - Clear encoding of chosen rules in application logic

#### Database Schema & Tables
- **Core Billing Tables** (Plan.txt Lines 57-68):
  - `plans`: Plan definition, metadata, pricing tiers, and features
  - `plan_limits`: Limit constraints per plan tier (minutes, concurrency, etc.)
  - `tenant_plan`: Tenant-to-plan assignment and relationship mapping
  - `partner_plan`: Partner-to-plan assignment and relationship mapping
  - `usage_ledger`: Immutable usage event record storage (primary source of truth)
  - `usage_summary_daily`: Pre-aggregated daily usage calculations
  - `usage_summary_monthly`: Pre-aggregated monthly usage calculations
  - `invoices`: Generated invoice records with metadata and totals
  - `invoice_line_items`: Itemized charges and credits per invoice
  - `payments`: Payment transaction records with status and timing
  - `billing_events`: Billing state change and event history tracking

#### Payment Provider Integration
- **Payment Processing Framework** (Plan.txt Lines 69-74):
  - Hosted payment processor integration (Stripe recommended)
  - PCI compliance enforcement: No raw card number storage locally
  - Webhook signature verification and cryptographic validation
  - Idempotent webhook processing for duplicate prevention
  - Invoice ID and payment status reference mapping
  - Provider subscription state synchronization with platform state
  - Payment status tracking and updates from provider
  - Subscription management and lifecycle state synchronization
  - Customer payment method storage via provider tokens only

#### Billing States & Status Management
- **Billing Account States** (Plan.txt Lines 76-83):
  - `active`: Account in good standing and active with service access
  - `trialing`: Trial period active with full service access
  - `past_due`: Payment overdue but service not yet suspended
  - `grace_period`: Grace period active for payment with access maintained
  - `suspended`: Service suspended due to non-payment or other violation
  - `canceled`: Account cancellation completed and service terminated
  - State transition logic with defined rules
  - Notification requirements for each state change

#### White-Label & Multi-Tenant Billing Architecture
- **Multi-Level Billing Structure** (Plan.txt Lines 84-90):
  - Partner-level aggregated usage consolidation and totaling
  - Sub-tenant/customer usage breakdown and isolation
  - Partner plan enforcement with per-partner limit checking
  - Overage calculation at both partner and tenant levels
  - Optional internal margin tracking for reseller pricing (future feature)
  - Admin visibility into complete partner usage metrics and trends
  - Partner invoice state monitoring and payment tracking
  - Reseller billing support for multi-level partner hierarchy
  - Tenant-level usage tracking and attribution for transparency

#### API Endpoints & Services
- **Required Billing API Endpoints** (Plan.txt Lines 91-98):
  - `POST /billing/webhooks/provider`: Payment provider webhook receiver and processor
  - `GET /billing/plan`: Retrieve current plan details and limits
  - `GET /billing/usage/summary`: Fetch usage summary metrics for period
  - `GET /billing/invoices`: List invoices for requested period with filtering
  - `GET /billing/invoices/{id}`: Retrieve specific invoice details with line items
  - `POST /billing/adjustment`: Create billing adjustments or credits with reason
  - `POST /billing/recalculate`: Trigger manual billing recalculation for period

#### Usage Aggregation & Summaries
- **Aggregation Logic** (Plan.txt Lines 25-68):
  - Daily aggregation from usage ledger to daily summary table
  - Monthly aggregation from daily summaries to monthly summary table
  - Accuracy verification that aggregates match source ledger
  - Efficiency optimization through pre-computed summaries
  - Real-time query capability from ledger for exact numbers

#### Overage Processing & Calculation
- **Overage Logic Implementation** (Plan.txt Lines 25-32, 49-56):
  - Overage minute calculation when usage exceeds plan limit
  - Overage concurrency calculation when peak concurrent calls exceed limit
  - Overage rate application based on plan configuration
  - Overage blocking enforcement if hard cap policy selected
  - Overage charging implementation if enabled in plan
  - Overage flagging for invoice itemization
  - Overage alert generation for monitoring and reporting

#### Invoice Generation & Content
- **Invoice Creation Process** (Plan.txt Lines 99-109):
  - Automated invoice generation at billing period end
  - Invoice totals calculated from usage ledger and plan terms
  - Line item generation for base fee, included allowances, overages, adjustments
  - Invoice metadata: invoice ID, billing period, customer details
  - Invoice status tracking: draft, finalized, sent, paid
  - Structured data format for accounting system export

#### Implementation Sequence
- **Ordered Implementation Steps** (Plan.txt Lines 111-119):
  1. Define plans table and included usage rules per plan
  2. Build immutable usage ledger architecture from session events
  3. Implement daily and monthly usage aggregation processes
  4. Implement overage calculation and tracking logic
  5. Create invoice generation with summary data
  6. Integrate payment provider (Stripe) and verify webhooks
  7. Add failed payment handling and grace period logic
  8. Expose admin and partner billing views with aggregated data

#### Acceptance Criteria & Validation Framework
- **Billing System Validation Requirements** (Plan.txt Lines 120-126):
  - Every completed voice call produces correct usage ledger entry
  - Minute totals and concurrency totals reproducible from backend data
  - Invoice summaries match ledger totals exactly (byte-for-byte accuracy)
  - Payment provider webhook state matches platform billing state
  - Partner billing view shows correct aggregated numbers
  - Admin billing view shows correct aggregated numbers across all partners
  - Over-limit usage enforced: either blocked or charged per chosen policy
  - Complete audit trail for all billing operations and state changes
  - Replay-safe billing calculations enabling full recomputation
  - Deterministic billing output with no floating-point precision issues

#### Security & Data Integrity Requirements
- **Billing Data Protection** (Plan.txt - Overall Requirements):
  - Never depend on frontend for billing calculations or truth
  - Backend-only billing truth enforced in business logic
  - Database constraints ensuring referential integrity
  - Audit logging of all billing operations and changes
  - Role-based access control for billing data viewing
  - Idempotent operations preventing double-billing
  - Transaction safety ensuring billing consistency
