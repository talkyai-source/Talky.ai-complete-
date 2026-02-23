# Admin Panel API Integration Guide

## Overview
This document provides detailed API integration specifications for the Talky.ai admin panel, including endpoint details, authentication requirements, and data models.

## Authentication

### JWT Token Management
```typescript
// Admin authentication flow
interface AdminAuth {
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
  role: 'admin' | 'super_admin';
}

// Token refresh mechanism
const refreshAdminToken = async (refreshToken: string): Promise<AdminAuth> => {
  const response = await fetch('/api/v1/auth/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken })
  });
  
  if (!response.ok) {
    throw new Error('Token refresh failed');
  }
  
  return response.json();
};
```

### Role-Based Access Control
```typescript
// Admin role verification
const requireAdmin = (user: User): boolean => {
  return user.role === 'admin' || user.role === 'super_admin';
};

// Permission levels
enum AdminPermission {
  VIEW_DASHBOARD = 'view_dashboard',
  MANAGE_TENANTS = 'manage_tenants',
  MANAGE_USERS = 'manage_users',
  VIEW_ANALYTICS = 'view_analytics',
  MANAGE_SYSTEM = 'manage_system',
  VIEW_AUDIT_LOGS = 'view_audit_logs'
}
```

## Core API Endpoints

### 1. Admin Management Endpoints

#### List All Tenants
```http
GET /api/v1/admin/tenants
Authorization: Bearer {admin_token}
Content-Type: application/json

Query Parameters:
- page: number (default: 1)
- limit: number (default: 50, max: 200)
- search: string (search by business_name)
- status: string (active, suspended, all)
- plan_id: string (filter by plan)
- sort_by: string (business_name, created_at, minutes_used)
- sort_order: string (asc, desc)

Response: 200 OK
{
  "tenants": [
    {
      "id": "uuid",
      "business_name": "string",
      "plan_id": "string",
      "minutes_used": "number",
      "minutes_allocated": "number",
      "created_at": "ISO8601",
      "updated_at": "ISO8601",
      "status": "active|suspended",
      "user_count": "number",
      "campaign_count": "number"
    }
  ],
  "pagination": {
    "page": "number",
    "limit": "number",
    "total": "number",
    "pages": "number"
  }
}
```

#### Get Tenant Details
```http
GET /api/v1/admin/tenants/{tenant_id}
Authorization: Bearer {admin_token}

Response: 200 OK
{
  "id": "uuid",
  "business_name": "string",
  "plan_id": "string",
  "minutes_used": "number",
  "minutes_allocated": "number",
  "calling_rules": {
    "time_window_start": "09:00",
    "time_window_end": "19:00",
    "timezone": "America/New_York",
    "max_concurrent_calls": "number",
    "retry_delay_seconds": "number"
  },
  "created_at": "ISO8601",
  "updated_at": "ISO8601",
  "users": [
    {
      "id": "uuid",
      "email": "string",
      "name": "string",
      "role": "string",
      "created_at": "ISO8601"
    }
  ],
  "recent_activity": {
    "last_call": "ISO8601",
    "total_calls_this_month": "number",
    "active_campaigns": "number"
  }
}
```

#### Update Tenant Quota
```http
PATCH /api/v1/admin/tenants/{tenant_id}/minutes
Authorization: Bearer {admin_token}
Content-Type: application/json

Request Body:
{
  "minutes_allocated": "number",
  "reason": "string (optional)",
  "expires_at": "ISO8601 (optional)"
}

Response: 200 OK
{
  "detail": "Minutes updated",
  "minutes_allocated": "number",
  "previous_minutes": "number",
  "tenant_id": "uuid"
}
```

#### Suspend/Reactivate Tenant
```http
POST /api/v1/admin/tenants/{tenant_id}/status
Authorization: Bearer {admin_token}
Content-Type: application/json

Request Body:
{
  "status": "suspended|active",
  "reason": "string (required for suspension)",
  "suspend_until": "ISO8601 (optional)"
}

Response: 200 OK
{
  "tenant_id": "uuid",
  "status": "string",
  "suspended_at": "ISO8601|null",
  "suspended_by": "uuid",
  "reason": "string"
}
```

### 2. User Management Endpoints

#### List All Users
```http
GET /api/v1/admin/users
Authorization: Bearer {admin_token}
Content-Type: application/json

Query Parameters:
- page: number (default: 1)
- limit: number (default: 50, max: 200)
- search: string (search by email or name)
- tenant_id: string (filter by tenant)
- role: string (user, admin)
- status: string (active, suspended, all)
- sort_by: string (email, created_at, last_active)
- sort_order: string (asc, desc)

Response: 200 OK
{
  "users": [
    {
      "id": "uuid",
      "email": "string",
      "name": "string",
      "role": "string",
      "tenant_id": "uuid",
      "tenant_name": "string",
      "created_at": "ISO8601",
      "last_active": "ISO8601|null",
      "status": "active|suspended",
      "two_factor_enabled": "boolean"
    }
  ],
  "pagination": {
    "page": "number",
    "limit": "number",
    "total": "number",
    "pages": "number"
  }
}
```

#### Get User Details
```http
GET /api/v1/admin/users/{user_id}
Authorization: Bearer {admin_token}

Response: 200 OK
{
  "id": "uuid",
  "email": "string",
  "name": "string",
  "role": "string",
  "tenant_id": "uuid",
  "tenant_name": "string",
  "created_at": "ISO8601",
  "last_active": "ISO8601|null",
  "two_factor_enabled": "boolean",
  "recent_activity": [
    {
      "action": "string",
      "timestamp": "ISO8601",
      "ip_address": "string"
    }
  ],
  "login_history": [
    {
      "timestamp": "ISO8601",
      "ip_address": "string",
      "user_agent": "string",
      "success": "boolean"
    }
  ]
}
```

#### Update User Role
```http
PATCH /api/v1/admin/users/{user_id}/role
Authorization: Bearer {admin_token}
Content-Type: application/json

Request Body:
{
  "role": "user|admin",
  "reason": "string (optional)"
}

Response: 200 OK
{
  "user_id": "uuid",
  "previous_role": "string",
  "new_role": "string",
  "updated_at": "ISO8601"
}
```

#### Reset User Password
```http
POST /api/v1/admin/users/{user_id}/reset-password
Authorization: Bearer {admin_token}
Content-Type: application/json

Request Body:
{
  "send_email": "boolean (default: true)",
  "temporary_password": "string (optional)"
}

Response: 200 OK
{
  "user_id": "uuid",
  "reset_initiated": "boolean",
  "email_sent": "boolean",
  "temporary_password": "string (if generated)"
}
```

### 3. Analytics & Reporting Endpoints

#### System Analytics
```http
GET /api/v1/analytics/system
Authorization: Bearer {admin_token}
Content-Type: application/json

Query Parameters:
- from: string (YYYY-MM-DD, default: 30 days ago)
- to: string (YYYY-MM-DD, default: today)
- group_by: string (day, week, month)

Response: 200 OK
{
  "overview": {
    "total_tenants": "number",
    "active_tenants": "number",
    "total_users": "number",
    "total_calls": "number",
    "total_minutes": "number",
    "revenue_this_month": "number"
  },
  "trends": {
    "signups": [
      {
        "date": "string",
        "count": "number"
      }
    ],
    "calls": [
      {
        "date": "string",
        "total": "number",
        "answered": "number",
        "failed": "number"
      }
    ],
    "revenue": [
      {
        "date": "string",
        "amount": "number"
      }
    ]
  },
  "top_tenants": [
    {
      "tenant_id": "uuid",
      "business_name": "string",
      "minutes_used": "number",
      "calls_made": "number"
    }
  ]
}
```

#### Provider Performance Analytics
```http
GET /api/v1/analytics/providers
Authorization: Bearer {admin_token}
Content-Type: application/json

Query Parameters:
- from: string (YYYY-MM-DD, default: 7 days ago)
- to: string (YYYY-MM-DD, default: today)
- provider_type: string (stt, tts, llm, telephony, optional)

Response: 200 OK
{
  "providers": [
    {
      "type": "string",
      "name": "string",
      "status": "healthy|degraded|down",
      "avg_latency_ms": "number",
      "error_rate": "number",
      "total_requests": "number",
      "successful_requests": "number",
      "failed_requests": "number",
      "uptime_percentage": "number"
    }
  ],
  "trends": {
    "latency": [
      {
        "timestamp": "ISO8601",
        "provider": "string",
        "latency_ms": "number"
      }
    ],
    "error_rate": [
      {
        "timestamp": "ISO8601",
        "provider": "string",
        "error_rate": "number"
      }
    ]
  }
}
```

### 4. System Configuration Endpoints

#### Get System Configuration
```http
GET /api/v1/admin/configuration
Authorization: Bearer {admin_token}

Response: 200 OK
{
  "providers": {
    "stt": {
      "active": "string",
      "available": ["string"],
      "config": {}
    },
    "tts": {
      "active": "string",
      "available": ["string"],
      "config": {}
    },
    "llm": {
      "active": "string",
      "available": ["string"],
      "config": {}
    },
    "telephony": {
      "active": "string",
      "available": ["string"],
      "config": {}
    }
  },
  "features": {
    "websocket_enabled": "boolean",
    "analytics_enabled": "boolean",
    "billing_enabled": "boolean",
    "quota_enforcement": "boolean"
  },
  "limits": {
    "max_tenants": "number",
    "max_users_per_tenant": "number",
    "max_concurrent_calls": "number",
    "max_campaigns_per_tenant": "number"
  }
}
```

#### Update Provider Configuration
```http
PATCH /api/v1/admin/configuration/providers/{provider_type}
Authorization: Bearer {admin_token}
Content-Type: application/json

Request Body:
{
  "active": "string",
  "config": {
    "api_key": "string",
    "model": "string",
    "other_config": "value"
  }
}

Response: 200 OK
{
  "provider_type": "string",
  "active": "string",
  "updated_at": "ISO8601",
  "status": "active|pending_restart"
}
```

### 5. Security & Audit Endpoints

#### Get Audit Log
```http
GET /api/v1/admin/audit
Authorization: Bearer {admin_token}
Content-Type: application/json

Query Parameters:
- page: number (default: 1)
- limit: number (default: 50, max: 200)
- action_type: string (filter by action type)
- user_id: string (filter by user)
- tenant_id: string (filter by tenant)
- from: string (ISO8601, start date)
- to: string (ISO8601, end date)
- outcome_status: string (success, failed, quota_exceeded, etc.)

Response: 200 OK
{
  "audit_entries": [
    {
      "id": "uuid",
      "tenant_id": "uuid",
      "action_type": "string",
      "triggered_by": "string",
      "outcome_status": "string",
      "input_data": "object|null",
      "output_data": "object|null",
      "error": "string|null",
      "user_id": "uuid|null",
      "ip_address": "string|null",
      "created_at": "ISO8601"
    }
  ],
  "pagination": {
    "page": "number",
    "limit": "number",
    "total": "number",
    "pages": "number"
  }
}
```

#### Get Security Events
```http
GET /api/v1/admin/security/events
Authorization: Bearer {admin_token}
Content-Type: application/json

Query Parameters:
- from: string (ISO8601, default: 24 hours ago)
- to: string (ISO8601, default: now)
- severity: string (critical, high, medium, low)
- acknowledged: boolean (filter by acknowledgment status)

Response: 200 OK
{
  "events": [
    {
      "id": "uuid",
      "type": "string",
      "severity": "string",
      "title": "string",
      "message": "string",
      "metadata": "object",
      "acknowledged": "boolean",
      "acknowledged_by": "uuid|null",
      "acknowledged_at": "ISO8601|null",
      "created_at": "ISO8601"
    }
  ],
  "summary": {
    "total_events": "number",
    "critical": "number",
    "high": "number",
    "medium": "number",
    "low": "number",
    "unacknowledged": "number"
  }
}
```

## WebSocket Real-time Endpoints

### Admin Dashboard WebSocket
```javascript
// Connection URL
const ws = new WebSocket('wss://api.talky.ai/api/v1/ws/admin');

// Authentication
ws.onopen = () => {
  ws.send(JSON.stringify({
    type: 'authenticate',
    token: adminJwtToken
  }));
};

// Message types
const ADMIN_WS_MESSAGES = {
  // Server to Client
  ALERT: 'alert',
  METRIC_UPDATE: 'metric_update',
  PROVIDER_STATUS: 'provider_status',
  TENANT_ACTIVITY: 'tenant_activity',
  SYSTEM_HEALTH: 'system_health',
  
  // Client to Server
  SUBSCRIBE_METRICS: 'subscribe_metrics',
  SUBSCRIBE_ALERTS: 'subscribe_alerts',
  ACKNOWLEDGE_ALERT: 'acknowledge_alert',
  HEARTBEAT: 'heartbeat'
};

// Example: Subscribe to metrics
ws.send(JSON.stringify({
  type: 'subscribe_metrics',
  filters: {
    tenant_id: 'all',
    metric_types: ['calls', 'users', 'revenue']
  }
}));

// Example: Receive alert
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  
  switch (data.type) {
    case 'alert':
      handleAlert(data.payload);
      break;
    case 'metric_update':
      updateDashboard(data.payload);
      break;
  }
};
```

## Error Handling

### Standard Error Response
```json
{
  "error": {
    "code": "string",
    "message": "string",
    "details": "object|null",
    "request_id": "uuid",
    "timestamp": "ISO8601"
  }
}
```

### Common Error Codes
```typescript
enum AdminErrorCode {
  // Authentication Errors
  UNAUTHORIZED = 'UNAUTHORIZED',
  FORBIDDEN = 'FORBIDDEN',
  TOKEN_EXPIRED = 'TOKEN_EXPIRED',
  INVALID_TOKEN = 'INVALID_TOKEN',
  
  // Validation Errors
  VALIDATION_ERROR = 'VALIDATION_ERROR',
  INVALID_PARAMETERS = 'INVALID_PARAMETERS',
  MISSING_REQUIRED_FIELD = 'MISSING_REQUIRED_FIELD',
  
  // Business Logic Errors
  TENANT_NOT_FOUND = 'TENANT_NOT_FOUND',
  USER_NOT_FOUND = 'USER_NOT_FOUND',
  PLAN_NOT_FOUND = 'PLAN_NOT_FOUND',
  PROVIDER_NOT_FOUND = 'PROVIDER_NOT_FOUND',
  
  // Operation Errors
  OPERATION_NOT_PERMITTED = 'OPERATION_NOT_PERMITTED',
  TENANT_SUSPENDED = 'TENANT_SUSPENDED',
  USER_SUSPENDED = 'USER_SUSPENDED',
  QUOTA_EXCEEDED = 'QUOTA_EXCEEDED',
  
  // System Errors
  INTERNAL_ERROR = 'INTERNAL_ERROR',
  SERVICE_UNAVAILABLE = 'SERVICE_UNAVAILABLE',
  DATABASE_ERROR = 'DATABASE_ERROR'
}
```

## Rate Limiting

### Admin API Rate Limits
```typescript
const ADMIN_RATE_LIMITS = {
  // General endpoints
  standard: {
    windowMs: 60000, // 1 minute
    max: 100 // requests per minute
  },
  
  // Sensitive operations
  sensitive: {
    windowMs: 60000, // 1 minute
    max: 20 // requests per minute
  },
  
  // Export/reporting endpoints
  export: {
    windowMs: 3600000, // 1 hour
    max: 10 // requests per hour
  }
};
```

## Data Models

### Tenant Model
```typescript
interface AdminTenant {
  id: string;
  business_name: string;
  plan_id: string;
  plan_name: string;
  minutes_used: number;
  minutes_allocated: number;
  minutes_remaining: number;
  status: 'active' | 'suspended' | 'pending';
  calling_rules: CallingRules;
  created_at: string;
  updated_at: string;
  metadata: {
    user_count: number;
    campaign_count: number;
    call_count: number;
    last_active: string | null;
  };
}

interface CallingRules {
  time_window_start: string;
  time_window_end: string;
  timezone: string;
  max_concurrent_calls: number;
  retry_delay_seconds: number;
  max_retry_attempts: number;
}
```

### User Model
```typescript
interface AdminUser {
  id: string;
  email: string;
  name: string | null;
  role: 'user' | 'admin';
  tenant_id: string;
  tenant_name: string;
  status: 'active' | 'suspended';
  two_factor_enabled: boolean;
  last_active: string | null;
  created_at: string;
  updated_at: string;
  metadata: {
    login_count: number;
    failed_login_count: number;
    last_login_ip: string | null;
    last_login_user_agent: string | null;
  };
}
```

### Analytics Models
```typescript
interface SystemAnalytics {
  overview: {
    total_tenants: number;
    active_tenants: number;
    total_users: number;
    total_calls: number;
    total_minutes: number;
    revenue_this_month: number;
  };
  trends: {
    signups: TimeSeriesData[];
    calls: CallTrendData[];
    revenue: RevenueTrendData[];
  };
  top_tenants: TopTenantData[];
}

interface ProviderAnalytics {
  providers: ProviderMetrics[];
  trends: {
    latency: TimeSeriesLatency[];
    error_rate: TimeSeriesErrorRate[];
  };
}
```

## Best Practices

### 1. Data Fetching
- Use SWR or React Query for client-side caching
- Implement pagination for large datasets
- Use debouncing for search inputs
- Implement optimistic updates where appropriate

### 2. Error Handling
- Always handle network errors gracefully
- Show user-friendly error messages
- Log errors for debugging
- Implement retry logic for transient failures

### 3. Performance
- Implement request debouncing
- Use pagination for large lists
- Cache frequently accessed data
- Optimize images and assets

### 4. Security
- Never expose sensitive data in client-side code
- Always validate input on both client and server
- Use HTTPS for all API communications
- Implement proper CORS policies

This API integration guide provides the foundation for building a robust admin panel that communicates effectively with the Talky.ai backend services while maintaining security and performance standards.