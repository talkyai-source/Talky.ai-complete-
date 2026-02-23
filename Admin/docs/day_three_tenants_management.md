# Day 3: Tenants Management - Complete Documentation

## Overview

Day 3 development focused on implementing a full Tenants Management feature for the Admin Panel. This includes listing all tenants, viewing their plans and usage, suspending/resuming tenants, and overriding quota limits.

**Definition of Done:** ✅ Admin can fully control tenant state.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Admin Frontend                                  │
│  ┌─────────────────┐   ┌────────────────────┐   ┌─────────────────────┐ │
│  │  TenantsPage    │───│   TenantsTable     │───│   QuotaModal        │ │
│  │  - Search       │   │   - Sortable cols  │   │   - Minutes input   │ │
│  │  - Filter       │   │   - Action menus   │   │   - Concurrent calls│ │
│  └─────────────────┘   └────────────────────┘   └─────────────────────┘ │
│                                  │                                       │
│                                  ▼                                       │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                          api.ts                                     │ │
│  │  getTenants(), getTenantById(), suspendTenant(), resumeTenant(),   │ │
│  │  updateTenantQuota()                                                │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │ HTTP
                                    ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                          Backend (FastAPI)                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                    admin.py - New Endpoints                          │  │
│  │  GET  /admin/tenants           - List all tenants with counts       │  │
│  │  GET  /admin/tenants/{id}      - Get tenant details                 │  │
│  │  PATCH /admin/tenants/{id}/quota - Update minutes + concurrency     │  │
│  │  POST /admin/tenants/{id}/suspend - Suspend tenant                  │  │
│  │  POST /admin/tenants/{id}/resume  - Resume tenant                   │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────┬───────────────────────────────────────┘
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                          Supabase PostgreSQL                               │
│  ┌─────────────┐  ┌───────────────────┐  ┌─────────────────────────────┐  │
│  │   tenants   │  │   user_profiles   │  │        campaigns            │  │
│  │ - id        │──│ (COUNT for users) │  │   (COUNT for campaigns)     │  │
│  │ - status    │  └───────────────────┘  └─────────────────────────────┘  │
│  │ - minutes   │                                                          │
│  └─────────────┘                                                          │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## Backend Changes

### File: `backend/app/api/v1/endpoints/admin.py`

#### New Pydantic Models

```python
class TenantListItem(BaseModel):
    """Enhanced tenant list item with counts and status"""
    id: str
    business_name: str
    plan_id: Optional[str] = None
    plan_name: Optional[str] = None
    minutes_used: int
    minutes_allocated: int
    status: str  # subscription_status: active, suspended, inactive
    user_count: int
    campaign_count: int
    max_concurrent_calls: int
    created_at: Optional[str] = None


class TenantDetailResponse(BaseModel):
    """Full tenant details response"""
    id: str
    business_name: str
    plan_id: Optional[str] = None
    plan_name: Optional[str] = None
    minutes_used: int
    minutes_allocated: int
    status: str
    user_count: int
    campaign_count: int
    max_concurrent_calls: int
    calling_rules: Optional[dict] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class QuotaUpdateRequest(BaseModel):
    """Request model for updating tenant quota"""
    minutes_allocated: Optional[int] = None
    max_concurrent_calls: Optional[int] = None
```

#### New/Enhanced Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/tenants` | GET | List all tenants with user counts, campaign counts, and status. Supports `?search=` and `?status=` query params. |
| `/admin/tenants/{id}` | GET | Get tenant by ID with full details including calling_rules. |
| `/admin/tenants/{id}/quota` | PATCH | Update minutes_allocated and/or max_concurrent_calls. |
| `/admin/tenants/{id}/suspend` | POST | Set subscription_status to 'suspended'. |
| `/admin/tenants/{id}/resume` | POST | Set subscription_status to 'active'. |

#### Key Implementation Details

1. **User/Campaign Counts**: The list endpoint performs COUNT queries on `user_profiles` and `campaigns` tables for each tenant.

2. **Plan Name**: Joins with `plans` table to get human-readable plan name.

3. **Max Concurrent Calls**: Extracted from `calling_rules` JSONB field.

4. **Status Tracking**: Uses `subscription_status` field (added by Stripe billing migration).

---

## Frontend Changes

### File: `Admin/frontend/src/lib/api.ts`

#### New Types

```typescript
export interface TenantListItem {
    id: string;
    business_name: string;
    plan_id: string | null;
    plan_name: string | null;
    minutes_used: number;
    minutes_allocated: number;
    status: string;  // 'active' | 'suspended' | 'inactive'
    user_count: number;
    campaign_count: number;
    max_concurrent_calls: number;
    created_at: string | null;
}

export interface QuotaUpdateRequest {
    minutes_allocated?: number;
    max_concurrent_calls?: number;
}
```

#### New API Methods

```typescript
// Tenant Management Endpoints (Day 3)
async getTenants(search?: string, status?: string): Promise<TenantListItem[]>
async getTenantById(tenantId: string): Promise<TenantDetails>
async suspendTenant(tenantId: string): Promise<{ detail: string; status: string }>
async resumeTenant(tenantId: string): Promise<{ detail: string; status: string }>
async updateTenantQuota(tenantId: string, quota: QuotaUpdateRequest): Promise<{ detail: string; ... }>
```

---

### File: `Admin/frontend/src/components/TenantsTable.tsx` (NEW)

A comprehensive table component featuring:

- **Sortable Columns**: Click headers to sort by name, status, usage, or user count
- **Status Badges**: Color-coded (green=active, orange=suspended, gray=inactive)
- **Usage Progress Bars**: Visual representation of minutes used vs allocated
- **Action Menus**: Dropdown with Suspend/Resume and Edit Quota options
- **Confirmation Dialogs**: Prevent accidental actions
- **QuotaModal**: Inline modal for editing minutes and concurrent calls

#### Component Props

```typescript
interface TenantsTableProps {
    tenants: TenantListItem[];
    loading: boolean;
    onRefresh: () => void;
    searchTerm: string;
    onSearchChange: (term: string) => void;
    statusFilter: string;
    onStatusFilterChange: (status: string) => void;
}
```

---

### File: `Admin/frontend/src/pages/TenantsPage.tsx` (UPDATED)

Replaced placeholder with full implementation:

- **Data Fetching**: Uses `api.getTenants()` with useEffect and useCallback
- **Debounced Search**: 300ms delay before fetching on search/filter change
- **Error Handling**: Error banner with retry button
- **Loading States**: Spinner while fetching
- **Refresh Button**: Manual data refresh

---

### File: `Admin/frontend/src/index.css` (UPDATED)

Added 460+ lines of CSS for tenants management UI:

- Table toolbar (search box, filter dropdown)
- Data table styles (sortable headers, hover states)
- Status badges (active, suspended, inactive)
- Usage progress bars
- Action menus (dropdown, buttons)
- Modal dialogs (confirmation, quota edit)
- Form inputs
- Button variants (primary, secondary, danger)
- Error banners
- Loading states

---

## Testing Instructions

### Backend Testing

```bash
# Start the backend
cd backend
python -m uvicorn app.main:app --reload

# Test list tenants
curl http://localhost:8000/api/v1/admin/tenants

# Test search
curl "http://localhost:8000/api/v1/admin/tenants?search=acme"

# Test filter by status
curl "http://localhost:8000/api/v1/admin/tenants?status=active"

# Test suspend tenant
curl -X POST http://localhost:8000/api/v1/admin/tenants/{tenant_id}/suspend

# Test resume tenant
curl -X POST http://localhost:8000/api/v1/admin/tenants/{tenant_id}/resume

# Test update quota
curl -X PATCH http://localhost:8000/api/v1/admin/tenants/{tenant_id}/quota \
  -H "Content-Type: application/json" \
  -d '{"minutes_allocated": 5000, "max_concurrent_calls": 20}'
```

### Frontend Testing

```bash
# Start the frontend
cd Admin/frontend
npm run dev
```

1. Navigate to http://localhost:5173/tenants
2. Verify table displays all tenants
3. Test search by typing tenant name
4. Test status filter dropdown
5. Click column headers to sort
6. Click action menu (three dots) on a row
7. Test Suspend → confirm dialog → verify status changes
8. Test Resume → confirm dialog → verify status changes
9. Test Edit Quota → modal appears → change values → save → verify update

---

## Files Modified/Created

### Backend
- `backend/app/api/v1/endpoints/admin.py` - Enhanced with 3 new models, 4 new/enhanced endpoints

### Frontend
| File | Status | Description |
|------|--------|-------------|
| `src/lib/api.ts` | Modified | Added `TenantListItem`, `QuotaUpdateRequest` types and 5 API methods |
| `src/components/TenantsTable.tsx` | **New** | Complete table component with sorting, actions, modals |
| `src/pages/TenantsPage.tsx` | Modified | Full implementation replacing placeholder |
| `src/index.css` | Modified | Added 460+ lines of tenants management styles |

---

## Verification Results

| Check | Status |
|-------|--------|
| TypeScript Compilation | ✅ Passed |
| Backend Endpoints | ✅ All endpoints implemented |
| Frontend Components | ✅ All components created |
| API Integration | ✅ Connected to backend |
| CSS Styles | ✅ Complete styling |

---

## Next Steps (Day 4+)

1. **Calls Page**: Implement live call monitoring with real call data
2. **Actions Log Page**: Audit log viewing
3. **Connectors Page**: Third-party integrations management
4. **Usage & Cost Page**: Billing and usage analytics
