# White Label Implementation Plan - Talky.ai

**Document Version**: 1.0  
**Created**: February 6, 2026  
**Estimated Duration**: 20 Working Days (Minimum)  
**Status**: Planning Phase

---

## Executive Summary

This document outlines the implementation roadmap for the **White Label Feature** in Talky.ai. Building upon the robust foundation we have established over the past development cycles, this feature will enable reseller partnerships where white label partners purchase bulk subscriptions at preferential rates ($25/user vs $30 regular) and resell the platform under their own branding.

Given the intricate nature of multi-tenant architectures we have already built, extending this to support hierarchical white label relationships requires careful orchestration across database design, backend services, admin interfaces, partner portals, billing systems, and security layers.

### Business Model

| User Type | Price | Description |
|-----------|-------|-------------|
| **Regular Users** | $30/month | Direct customers using Talky.ai branding |
| **White Label Partners** | $25/user/month | Bulk purchase, resell under their brand |
| **White Label End Users** | Varies | Managed by white label partner |

### Key Goals

1. **White Label Partner Portal** - A dedicated dashboard for partners to manage their customers, requiring its own layout, navigation, and branding system
2. **Admin Panel Integration** - Complete visibility and control over all white label partners, extending our existing admin infrastructure
3. **Branding Customization** - Logo, colors, custom domain support per partner with dynamic theming
4. **Billing Management** - Bulk subscription tracking, usage allocation, and invoice generation integrated with our Stripe billing system
5. **Sub-tenant Management** - Hierarchical tenant relationships with proper isolation and quota inheritance
6. **Analytics & Reporting** - Aggregated usage reports across partner hierarchies with drill-down capabilities

---

## What We Have Built So Far

### Existing Architecture

The Talky.ai platform already has:

- **Multi-tenant PostgreSQL** with Row-Level Security (RLS) for tenant isolation
- **Admin Panel** (React + Vite) for platform administration
- **User Frontend** (Next.js) for end-user interaction
- **FastAPI Backend** with comprehensive API endpoints
- **Stripe Billing** integration for subscription management
- **Tenant Management** with quotas, suspension, and quota overrides
- **Security Features**: RLS, audit logging, replay protection, token rotation

### Current Tenant Structure

```
┌─────────────────────────────────────────────────────────────┐
│                    PLATFORM (Talky.ai)                       │
│  ┌─────────────────────────────────────────────────────────┐│
│  │                    Admin Panel                           ││
│  │  • Tenant management                                     ││
│  │  • System monitoring                                     ││
│  │  • Subscription oversight                                ││
│  └─────────────────────────────────────────────────────────┘│
│                           │                                  │
│           ┌───────────────┼───────────────┐                  │
│           ▼               ▼               ▼                  │
│     ┌──────────┐   ┌──────────┐    ┌──────────┐             │
│     │ Tenant A │   │ Tenant B │    │ Tenant C │             │
│     │ Direct   │   │ Direct   │    │ Direct   │             │
│     │ Customer │   │ Customer │    │ Customer │             │
│     └──────────┘   └──────────┘    └──────────┘             │
└─────────────────────────────────────────────────────────────┘
```

### Proposed White Label Structure

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        PLATFORM (Talky.ai)                               │
│  ┌─────────────────────────────────────────────────────────────────────┐│
│  │                        Admin Panel                                   ││
│  │  • Direct tenant management                                          ││
│  │  • White label partner management                                    ││
│  │  • System monitoring across all                                      ││
│  │  • Global analytics & billing                                        ││
│  └─────────────────────────────────────────────────────────────────────┘│
│                                │                                         │
│        ┌───────────────────────┼───────────────────────┐                │
│        ▼                       ▼                       ▼                │
│  ┌───────────┐     ┌─────────────────────┐    ┌───────────┐            │
│  │ Direct    │     │ White Label Partner │    │ Direct    │            │
│  │ Tenant A  │     │ (Reseller)          │    │ Tenant B  │            │
│  │ $30/month │     │ Bulk: $25/user      │    │ $30/month │            │
│  └───────────┘     └─────────┬───────────┘    └───────────┘            │
│                              │                                          │
│              ┌───────────────┼───────────────┐                         │
│              ▼               ▼               ▼                         │
│        ┌──────────┐   ┌──────────┐    ┌──────────┐                     │
│        │Sub-tenant│   │Sub-tenant│    │Sub-tenant│                     │
│        │ Client 1 │   │ Client 2 │    │ Client 3 │                     │
│        │(Partner's│   │(Partner's│    │(Partner's│                     │
│        │ Customer)│   │ Customer)│    │ Customer)│                     │
│        └──────────┘   └──────────┘    └──────────┘                     │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Database Schema Extensions

### New Tables

#### 1. `white_label_partners`

Stores white label partner information and branding settings.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | FK to tenants (partner's own tenant) |
| `company_name` | VARCHAR | Partner's company name |
| `branding` | JSONB | Logo URL, colors, fonts |
| `custom_domain` | VARCHAR | Partner's custom domain |
| `billing_rate` | DECIMAL | Rate charged per sub-tenant ($25 default) |
| `max_sub_tenants` | INTEGER | Maximum allowed sub-tenants |
| `status` | VARCHAR | active, suspended, pending |
| `created_at` | TIMESTAMPTZ | Creation timestamp |
| `updated_at` | TIMESTAMPTZ | Last update timestamp |

#### 2. `white_label_sub_tenants`

Links sub-tenants to their white label parent.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `partner_id` | UUID | FK to white_label_partners |
| `tenant_id` | UUID | FK to tenants (sub-tenant) |
| `allocated_minutes` | INTEGER | Minutes allocated by partner |
| `status` | VARCHAR | active, suspended |
| `created_at` | TIMESTAMPTZ | Creation timestamp |

#### 3. `white_label_billing`

Tracks billing between platform and white label partners.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `partner_id` | UUID | FK to white_label_partners |
| `billing_period` | DATE | Month/year of billing |
| `sub_tenant_count` | INTEGER | Number of active sub-tenants |
| `total_minutes_used` | INTEGER | Aggregate minutes across sub-tenants |
| `amount_due` | DECIMAL | Amount owed by partner |
| `status` | VARCHAR | pending, paid, overdue |
| `invoice_id` | VARCHAR | Stripe invoice reference |
| `created_at` | TIMESTAMPTZ | Creation timestamp |

### Modified Tables

#### `tenants` table additions

| Column | Type | Description |
|--------|------|-------------|
| `tenant_type` | VARCHAR | 'direct', 'white_label_partner', 'white_label_sub' |
| `parent_tenant_id` | UUID | FK to parent tenant (for sub-tenants) |
| `white_label_partner_id` | UUID | FK to white_label_partners |

#### `user_profiles` role extension

New role value: `white_label_admin` - Has access to white label partner portal

---

## API Endpoints (Backend)

### Admin Panel Endpoints (`/api/v1/admin/white-label/`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/partners` | GET | List all white label partners |
| `/partners` | POST | Create new white label partner |
| `/partners/{id}` | GET | Get partner details with sub-tenants |
| `/partners/{id}` | PATCH | Update partner settings |
| `/partners/{id}/suspend` | POST | Suspend partner |
| `/partners/{id}/resume` | POST | Resume partner |
| `/partners/{id}/sub-tenants` | GET | List partner's sub-tenants |
| `/partners/{id}/billing` | GET | Get partner billing history |
| `/partners/{id}/usage` | GET | Get partner usage analytics |

### White Label Partner Endpoints (`/api/v1/white-label/`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/dashboard` | GET | Partner dashboard stats |
| `/sub-tenants` | GET | List own sub-tenants |
| `/sub-tenants` | POST | Create new sub-tenant |
| `/sub-tenants/{id}` | GET | Get sub-tenant details |
| `/sub-tenants/{id}` | PATCH | Update sub-tenant |
| `/sub-tenants/{id}/suspend` | POST | Suspend sub-tenant |
| `/sub-tenants/{id}/resume` | POST | Resume sub-tenant |
| `/branding` | GET | Get branding settings |
| `/branding` | PUT | Update branding |
| `/billing` | GET | Get own billing summary |
| `/usage` | GET | Get usage across sub-tenants |

---

## Frontend Components

### Admin Panel Extensions

| Component | Purpose |
|-----------|---------|
| `WhiteLabelPage.tsx` | Main page for WL management |
| `PartnersTable.tsx` | List/filter/search partners |
| `PartnerDetailDrawer.tsx` | View/edit partner details |
| `PartnerSubTenantsTable.tsx` | Sub-tenants under a partner |
| `WhiteLabelBillingCard.tsx` | Partner billing overview |
| `WhiteLabelUsageChart.tsx` | Usage visualization |

### White Label Partner Portal (New Frontend Section)

| Component | Purpose |
|-----------|---------|
| `WhiteLabelLayout.tsx` | Layout with partner branding |
| `PartnerDashboard.tsx` | Partner's main dashboard |
| `SubTenantsPage.tsx` | Manage sub-tenants |
| `SubTenantForm.tsx` | Create/edit sub-tenant |
| `BrandingSettings.tsx` | Configure branding |
| `PartnerBillingPage.tsx` | View billing/invoices |
| `PartnerUsageAnalytics.tsx` | Usage reports |

---

## Day-by-Day Implementation Plan

### Week 1: Foundation & Database (Days 1-5)

---

#### Day 1: Database Schema Design & Migration

**Focus**: Create database foundation for white label feature

**Tasks**:
- Design and review complete database schema
- Write SQL migration file for new tables
- Add RLS policies for white label tables
- Add indexes for performance
- Test migration on development environment

**Deliverables**:
- `database/migrations/add_white_label_feature.sql`
- Updated schema documentation
- RLS policies for tenant isolation

---

#### Day 2: Backend Models & Core Services

**Focus**: Create backend data models and core service layer

**Tasks**:
- Create Pydantic models for white label entities
- Implement `WhiteLabelService` with core methods
- Add partner CRUD operations
- Add sub-tenant management logic
- Implement quota allocation logic

**Deliverables**:
- `app/domain/models/white_label.py`
- `app/services/white_label_service.py`
- Unit tests for core service methods

---

#### Day 3: Admin API Endpoints - Partners

**Focus**: Admin panel endpoints for partner management

**Tasks**:
- Create admin router for white label
- Implement partner list endpoint with filters
- Implement partner detail endpoint
- Implement create/update partner endpoints
- Add suspend/resume functionality

**Deliverables**:
- `app/api/v1/endpoints/admin/white_label.py`
- API documentation
- Endpoint tests

---

#### Day 4: Admin API Endpoints - Sub-tenants & Billing

**Focus**: Complete admin endpoints for sub-tenants and billing

**Tasks**:
- Implement sub-tenant list under partner
- Implement sub-tenant management endpoints
- Create billing summary endpoints
- Create usage analytics endpoints
- Add aggregation queries for reports

**Deliverables**:
- Extended admin white label router
- Billing calculation logic
- Usage aggregation queries

---

#### Day 5: White Label Partner API Endpoints

**Focus**: Self-service endpoints for white label partners

**Tasks**:
- Create partner router with auth checks
- Implement partner dashboard endpoint
- Implement sub-tenant management for partners
- Create branding endpoints
- Implement partner billing/usage endpoints

**Deliverables**:
- `app/api/v1/endpoints/white_label.py`
- Authentication middleware for partner role
- API documentation

---

### Week 2: Admin Panel Frontend (Days 6-10)

---

#### Day 6: Admin Panel - White Label Page Structure

**Focus**: Create admin panel page and routing

**Tasks**:
- Add White Label route to sidebar
- Create WhiteLabelPage component
- Implement page layout with tabs
- Create API client methods
- Add TypeScript types

**Deliverables**:
- `pages/WhiteLabelPage.tsx`
- Updated Sidebar with navigation
- API types in `api.ts`

---

#### Day 7: Admin Panel - Partners Table

**Focus**: Main partners table with filtering

**Tasks**:
- Create PartnersTable component
- Implement search and filters
- Add status badges and indicators
- Implement pagination
- Add action buttons

**Deliverables**:
- `components/PartnersTable.tsx`
- Table styles in CSS
- Filter state management

---

#### Day 8: Admin Panel - Partner Detail Drawer

**Focus**: Detailed view and edit capabilities

**Tasks**:
- Create PartnerDetailDrawer component
- Display partner information
- Show branding preview
- Add edit functionality
- Implement suspend/resume actions

**Deliverables**:
- `components/PartnerDetailDrawer.tsx`
- Edit modal component
- Confirmation dialogs

---

#### Day 9: Admin Panel - Sub-tenants & Usage

**Focus**: View partner's sub-tenants and usage

**Tasks**:
- Create PartnerSubTenantsTable
- Show sub-tenant status and usage
- Create usage visualization chart
- Add usage breakdown by sub-tenant
- Implement export functionality

**Deliverables**:
- `components/PartnerSubTenantsTable.tsx`
- `components/WhiteLabelUsageChart.tsx`
- Usage aggregation display

---

#### Day 10: Admin Panel - Create Partner Flow

**Focus**: Complete partner creation workflow

**Tasks**:
- Create partner creation modal/form
- Add validation for all fields
- Implement branding upload
- Create success/error notifications
- Add partner quick actions

**Deliverables**:
- `components/CreatePartnerModal.tsx`
- Form validation logic
- File upload for branding

---

### Week 3: White Label Partner Portal (Days 11-15)

---

#### Day 11: Partner Portal - Layout & Navigation

**Focus**: Create partner portal structure

**Tasks**:
- Decide: Separate app vs. Role-based routing
  - **Recommendation**: Role-based routing in existing User Frontend
  - If `user.role === 'white_label_admin'` → show partner portal
- Create WhiteLabelLayout component
- Apply partner branding dynamically
- Create partner-specific navigation
- Implement branding context provider

**Deliverables**:
- `components/layout/WhiteLabelLayout.tsx`
- `contexts/BrandingContext.tsx`
- Partner navigation component

---

#### Day 12: Partner Portal - Dashboard

**Focus**: Partner's main dashboard

**Tasks**:
- Create PartnerDashboard component
- Show key metrics (sub-tenants, usage, billing)
- Create summary cards
- Add quick action buttons
- Implement real-time stats refresh

**Deliverables**:
- `app/white-label/dashboard/page.tsx`
- Dashboard cards and metrics
- API integration for stats

---

#### Day 13: Partner Portal - Sub-tenant Management

**Focus**: Partners manage their customers

**Tasks**:
- Create SubTenantsPage component
- Implement sub-tenant list table
- Create add sub-tenant form
- Add edit and suspend capabilities
- Implement quota allocation per sub-tenant

**Deliverables**:
- `app/white-label/sub-tenants/page.tsx`
- `components/SubTenantForm.tsx`
- Sub-tenant management logic

---

#### Day 14: Partner Portal - Branding Settings

**Focus**: White label customization

**Tasks**:
- Create BrandingSettings page
- Implement logo upload/preview
- Add color scheme picker
- Preview branding changes
- Save and apply branding

**Deliverables**:
- `app/white-label/settings/branding/page.tsx`
- `components/BrandingEditor.tsx`
- Logo/color configuration

---

#### Day 15: Partner Portal - Billing & Usage

**Focus**: Partner's billing visibility

**Tasks**:
- Create billing overview page
- Show billing history
- Display usage per sub-tenant
- Create usage charts
- Add invoice download links

**Deliverables**:
- `app/white-label/billing/page.tsx`
- `app/white-label/usage/page.tsx`
- Billing/usage visualization

---

### Week 4: Integration, Testing & Polish (Days 16-20)

---

#### Day 16: Billing Integration

**Focus**: Connect white label to Stripe billing

**Tasks**:
- Extend BillingService for white label
- Implement bulk subscription creation
- Create partner invoice generation
- Add usage-based billing calculation
- Webhook handling for partner subscriptions

**Deliverables**:
- Updated `billing_service.py`
- Partner billing endpoints
- Invoice generation logic

---

#### Day 17: Security & Permissions

**Focus**: Ensure proper isolation and access control

**Tasks**:
- Verify RLS policies work correctly
- Test data isolation between partners
- Test isolation between sub-tenants
- Add audit logging for partner actions
- Security review and fixes

**Deliverables**:
- Security test cases
- Verified RLS policies
- Audit log entries for partner actions

---

#### Day 18: Integration Testing

**Focus**: End-to-end testing of all flows

**Tasks**:
- Test partner creation flow
- Test sub-tenant management
- Test branding application
- Test billing calculations
- Test role-based access

**Deliverables**:
- Integration test suite
- Test documentation
- Bug fixes

---

#### Day 19: UI Polish & Responsiveness

**Focus**: Finalize UI/UX

**Tasks**:
- Review all components for consistency
- Ensure responsive design
- Add loading states everywhere
- Polish animations and transitions
- Accessibility review

**Deliverables**:
- Polished CSS
- Responsive layouts
- Accessibility improvements

---

#### Day 20: Documentation & Deployment Prep

**Focus**: Complete documentation and prepare for deployment

**Tasks**:
- Write user documentation for partners
- Update API documentation
- Create deployment guide
- Database migration instructions
- Admin training documentation

**Deliverables**:
- `docs/white_label_user_guide.md`
- `docs/white_label_admin_guide.md`
- `docs/white_label_api.md`
- Deployment checklist

---

## File Structure Overview

### Backend Changes

```
backend/
├── database/migrations/
│   └── add_white_label_feature.sql     [NEW]
│
├── app/domain/models/
│   └── white_label.py                   [NEW]
│
├── app/services/
│   ├── white_label_service.py           [NEW]
│   └── billing_service.py               [MODIFIED]
│
├── app/api/v1/endpoints/
│   ├── admin/
│   │   └── white_label.py               [NEW]
│   └── white_label.py                   [NEW]
│
└── tests/unit/
    └── test_white_label_service.py      [NEW]
```

### Admin Panel Changes

```
Admin/frontend/src/
├── pages/
│   └── WhiteLabelPage.tsx               [NEW]
│
├── components/
│   ├── PartnersTable.tsx                [NEW]
│   ├── PartnerDetailDrawer.tsx          [NEW]
│   ├── PartnerSubTenantsTable.tsx       [NEW]
│   ├── WhiteLabelUsageChart.tsx         [NEW]
│   └── CreatePartnerModal.tsx           [NEW]
│
├── lib/
│   └── api.ts                           [MODIFIED]
│
└── index.css                            [MODIFIED]
```

### User Frontend Changes

```
Talk-Leee/src/
├── app/
│   └── white-label/
│       ├── dashboard/page.tsx           [NEW]
│       ├── sub-tenants/page.tsx         [NEW]
│       ├── settings/branding/page.tsx   [NEW]
│       ├── billing/page.tsx             [NEW]
│       └── usage/page.tsx               [NEW]
│
├── components/
│   ├── layout/
│   │   └── WhiteLabelLayout.tsx         [NEW]
│   ├── white-label/
│   │   ├── SubTenantForm.tsx            [NEW]
│   │   ├── BrandingEditor.tsx           [NEW]
│   │   └── PartnerCards.tsx             [NEW]
│
└── lib/
    ├── contexts/
    │   └── BrandingContext.tsx          [NEW]
    └── api/
        └── white-label.ts               [NEW]
```

---

## Security Considerations

### Access Control Matrix

| Role | Admin Panel | Partner Portal | User Frontend |
|------|-------------|----------------|---------------|
| Admin | ✅ Full Access | ✅ View All | ✅ View All |
| White Label Admin | ❌ | ✅ Own Partner | ✅ Own Sub-tenants |
| User (Direct) | ❌ | ❌ | ✅ Own Tenant |
| User (Sub-tenant) | ❌ | ❌ | ✅ Own Tenant |

### RLS Policies

- Partners can only see their own data
- Sub-tenants are isolated from each other
- Parent partner can view aggregate usage of sub-tenants
- Admin can view all data

### Audit Requirements

All partner actions should be logged:
- Sub-tenant creation/modification
- Quota allocations
- Suspension/resume actions
- Branding changes

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Partner creation time | < 5 minutes |
| Sub-tenant creation time | < 2 minutes |
| Branding application | Instant |
| Portal page load time | < 2 seconds |
| API response time | < 500ms |

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Data leakage between partners | Thorough RLS testing |
| Complex billing calculations | Automated tests for billing |
| Branding conflicts | Scoped CSS, isolated contexts |
| Performance with many sub-tenants | Pagination, lazy loading |
| Migration issues | Staged rollout, backup procedures |

---

## Summary

This 20-day plan delivers a complete white label solution:

| Week | Focus | Key Deliverables |
|------|-------|------------------|
| **Week 1** | Foundation | Database, Backend Services, APIs |
| **Week 2** | Admin Panel | Partner management UI |
| **Week 3** | Partner Portal | Self-service partner dashboard |
| **Week 4** | Integration | Billing, Security, Testing, Polish |

The implementation follows the existing Talky.ai patterns:
- Uses the established multi-tenant architecture
- Extends existing billing through Stripe
- Follows the same component structure as Admin Panel Days 1-8
- Maintains security through RLS and audit logging

---

**End of White Label Implementation Plan**  
*Document prepared for review and approval*
