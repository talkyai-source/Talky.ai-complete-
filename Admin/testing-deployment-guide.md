# Testing and Deployment Guide for Admin Panel

## Overview
This comprehensive guide covers the testing strategy, deployment procedures, and operational considerations for the Talky.ai admin panel, ensuring reliable and secure deployment across different environments.

## Testing Strategy

### Testing Pyramid
```
                    ┌─────────────────────────────────────┐
                    │         E2E Tests (10%)            │
                    │  - Critical user journeys          │
                    │  - Cross-browser testing           │
                    │  - Mobile responsiveness           │
                    └─────────────────┬───────────────────┘
                                      │
                    ┌─────────────────┴───────────────────┐
                    │      Integration Tests (30%)         │
                    │  - API integration testing         │
                    │  - Database operations             │
                    │  - External service mocking        │
                    └─────────────────┬───────────────────┘
                                      │
                    ┌─────────────────┴───────────────────┐
                    │      Unit Tests (60%)              │
                    │  - Component testing               │
                    │  - Utility function testing        │
                    │  - Hook and service testing        │
                    └─────────────────────────────────────┘
```

### Unit Testing

#### Component Testing Framework
```typescript
// Testing setup with Jest and React Testing Library
// jest.config.js
module.exports = {
  testEnvironment: 'jsdom',
  setupFilesAfterEnv: ['<rootDir>/src/test-utils/setup.ts'],
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/src/$1',
    '^@/components/(.*)$': '<rootDir>/src/components/$1',
    '^@/services/(.*)$': '<rootDir>/src/services/$1'
  },
  coverageThreshold: {
    global: {
      branches: 80,
      functions: 80,
      lines: 80,
      statements: 80
    }
  }
};

// Test utilities
// src/test-utils/render.tsx
import { render, RenderOptions } from '@testing-library/react';
import { AdminPanelProvider } from '@/contexts/AdminPanelContext';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const createTestQueryClient = () => new QueryClient({
  defaultOptions: {
    queries: { retry: false },
    mutations: { retry: false }
  }
});

export function renderWithAdminPanel(
  ui: React.ReactElement,
  options?: Omit<RenderOptions, 'wrapper'>
) {
  const queryClient = createTestQueryClient();
  
  function TestWrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <AdminPanelProvider>
          {children}
        </AdminPanelProvider>
      </QueryClientProvider>
    );
  }
  
  return render(ui, { wrapper: TestWrapper, ...options });
}
```

#### Component Test Examples
```typescript
// TenantList.test.tsx
import { renderWithAdminPanel, screen, waitFor } from '@/test-utils';
import { TenantList } from '@/components/admin/tenants/TenantList';
import { mockTenants } from '@/test-utils/mocks';

describe('TenantList Component', () => {
  beforeEach(() => {
    // Mock API calls
    jest.spyOn(api, 'getTenants').mockResolvedValue({
      data: mockTenants,
      pagination: { page: 1, total: 2 }
    });
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  it('renders tenant list correctly', async () => {
    renderWithAdminPanel(<TenantList />);
    
    // Wait for data to load
    await waitFor(() => {
      expect(screen.getByText('Acme Corporation')).toBeInTheDocument();
      expect(screen.getByText('TechStart Inc')).toBeInTheDocument();
    });
  });

  it('handles search functionality', async () => {
    renderWithAdminPanel(<TenantList />);
    
    const searchInput = screen.getByPlaceholderText('Search tenants...');
    
    // Simulate user typing
    await userEvent.type(searchInput, 'Acme');
    
    await waitFor(() => {
      expect(screen.getByText('Acme Corporation')).toBeInTheDocument();
      expect(screen.queryByText('TechStart Inc')).not.toBeInTheDocument();
    });
  });

  it('displays loading state', () => {
    // Mock slow response
    jest.spyOn(api, 'getTenants').mockImplementation(() => 
      new Promise(resolve => setTimeout(resolve, 1000))
    );
    
    renderWithAdminPanel(<TenantList />);
    
    expect(screen.getByText('Loading tenants...')).toBeInTheDocument();
  });

  it('handles API errors gracefully', async () => {
    jest.spyOn(api, 'getTenants').mockRejectedValue(new Error('API Error'));
    
    renderWithAdminPanel(<TenantList />);
    
    await waitFor(() => {
      expect(screen.getByText('Failed to load tenants')).toBeInTheDocument();
      expect(screen.getByText('Retry')).toBeInTheDocument();
    });
  });
});
```

#### Hook Testing
```typescript
// useTenantManagement.test.ts
import { renderHook, waitFor } from '@testing-library/react';
import { useTenantManagement } from '@/hooks/admin/useTenantManagement';
import { createWrapper } from '@/test-utils';

describe('useTenantManagement Hook', () => {
  it('fetches tenants successfully', async () => {
    const mockTenants = [
      { id: '1', businessName: 'Acme Corp', status: 'active' }
    ];
    
    jest.spyOn(api, 'getTenants').mockResolvedValue({ data: mockTenants });
    
    const { result } = renderHook(() => useTenantManagement(), {
      wrapper: createWrapper()
    });
    
    await waitFor(() => {
      expect(result.current.tenants).toEqual(mockTenants);
      expect(result.current.isLoading).toBe(false);
    });
  });

  it('handles tenant suspension', async () => {
    const { result } = renderHook(() => useTenantManagement(), {
      wrapper: createWrapper()
    });
    
    await act(async () => {
      await result.current.suspendTenant('tenant-123', 'Violation of terms');
    });
    
    expect(api.suspendTenant).toHaveBeenCalledWith('tenant-123', {
      reason: 'Violation of terms'
    });
  });

  it('validates quota updates', async () => {
    const { result } = renderHook(() => useTenantManagement(), {
      wrapper: createWrapper()
    });
    
    await act(async () => {
      await result.current.updateTenantQuota('tenant-123', -100);
    });
    
    expect(result.current.error).toBe('Quota must be a positive number');
  });
});
```

### Integration Testing

#### API Integration Tests
```typescript
// admin-api.integration.test.ts
import request from 'supertest';
import { app } from '@/test-utils/test-server';
import { generateAdminToken, createTestAdmin } from '@/test-utils/auth';

describe('Admin API Integration', () => {
  let adminToken: string;
  let testAdmin: AdminUser;
  
  beforeAll(async () => {
    testAdmin = await createTestAdmin({ role: 'admin' });
    adminToken = generateAdminToken(testAdmin);
  });

  describe('GET /api/v1/admin/tenants', () => {
    it('returns tenants for authorized admin', async () => {
      const response = await request(app)
        .get('/api/v1/admin/tenants')
        .set('Authorization', `Bearer ${adminToken}`)
        .expect(200);
      
      expect(response.body).toHaveProperty('tenants');
      expect(response.body).toHaveProperty('pagination');
      expect(Array.isArray(response.body.tenants)).toBe(true);
    });

    it('rejects unauthorized requests', async () => {
      const response = await request(app)
        .get('/api/v1/admin/tenants')
        .expect(401);
      
      expect(response.body.error).toBe('Authentication required');
    });

    it('rejects non-admin users', async () => {
      const regularUser = await createTestUser({ role: 'user' });
      const userToken = generateUserToken(regularUser);
      
      const response = await request(app)
        .get('/api/v1/admin/tenants')
        .set('Authorization', `Bearer ${userToken}`)
        .expect(403);
      
      expect(response.body.error).toBe('Admin access required');
    });
  });

  describe('PATCH /api/v1/admin/tenants/:id/quota', () => {
    it('updates tenant quota successfully', async () => {
      const tenant = await createTestTenant();
      
      const response = await request(app)
        .patch(`/api/v1/admin/tenants/${tenant.id}/quota`)
        .set('Authorization', `Bearer ${adminToken}`)
        .send({ minutes_allocated: 5000 })
        .expect(200);
      
      expect(response.body.minutes_allocated).toBe(5000);
      
      // Verify audit log was created
      const auditLog = await getLatestAuditLog(testAdmin.id, 'tenant_quota_update');
      expect(auditLog).toBeDefined();
      expect(auditLog.outcome_status).toBe('success');
    });

    it('validates input parameters', async () => {
      const tenant = await createTestTenant();
      
      const response = await request(app)
        .patch(`/api/v1/admin/tenants/${tenant.id}/quota`)
        .set('Authorization', `Bearer ${adminToken}`)
        .send({ minutes_allocated: -100 })
        .expect(400);
      
      expect(response.body.error).toBe('Validation failed');
    });
  });
});
```

#### Database Integration Tests
```typescript
// database.integration.test.ts
import { supabase } from '@/test-utils/supabase';
import { seedTestData, cleanupTestData } from '@/test-utils/database';

describe('Database Integration', () => {
  beforeAll(async () => {
    await seedTestData();
  });

  afterAll(async () => {
    await cleanupTestData();
  });

  describe('Tenant Operations', () => {
    it('creates tenant with proper isolation', async () => {
      const tenantData = {
        business_name: 'Test Corp',
        plan_id: 'basic',
        minutes_allocated: 1000
      };
      
      const { data: tenant, error } = await supabase
        .from('tenants')
        .insert(tenantData)
        .select()
        .single();
      
      expect(error).toBeNull();
      expect(tenant).toBeDefined();
      expect(tenant.business_name).toBe('Test Corp');
      
      // Verify RLS is working
      const { data: otherTenantData } = await supabase
        .from('tenants')
        .select()
        .neq('id', tenant.id)
        .limit(1);
      
      // Should not be able to access other tenants without proper RLS
      expect(otherTenantData).toHaveLength(0);
    });

    it('enforces unique constraints', async () => {
      const tenantData = {
        business_name: 'Duplicate Corp',
        plan_id: 'basic'
      };
      
      // Create first tenant
      await supabase.from('tenants').insert(tenantData);
      
      // Try to create duplicate
      const { error } = await supabase
        .from('tenants')
        .insert(tenantData);
      
      expect(error).toBeDefined();
      expect(error.code).toBe('23505'); // unique_violation
    });
  });

  describe('Audit Logging', () => {
    it('logs admin actions with proper details', async () => {
      const adminUserId = 'test-admin-123';
      const actionData = {
        admin_user_id: adminUserId,
        action_type: 'tenant_create',
        target_type: 'tenant',
        action_details: { test: 'data' },
        ip_address: '192.168.1.1',
        outcome_status: 'success'
      };
      
      const { data: auditLog, error } = await supabase
        .from('admin_audit_log')
        .insert(actionData)
        .select()
        .single();
      
      expect(error).toBeNull();
      expect(auditLog).toBeDefined();
      expect(auditLog.action_type).toBe('tenant_create');
      expect(auditLog.outcome_status).toBe('success');
    });
  });
});
```

### End-to-End Testing

#### Critical User Journeys
```typescript
// e2e/admin-journeys.test.ts
import { test, expect } from '@playwright/test';
import { AdminLoginPage } from '@/e2e/pages/AdminLoginPage';
import { AdminDashboardPage } from '@/e2e/pages/AdminDashboardPage';
import { TenantManagementPage } from '@/e2e/pages/TenantManagementPage';

test.describe('Admin Panel Critical Journeys', () => {
  test('admin can login and view dashboard', async ({ page }) => {
    const loginPage = new AdminLoginPage(page);
    const dashboardPage = new AdminDashboardPage(page);
    
    // Navigate to admin login
    await loginPage.goto();
    
    // Login with valid credentials
    await loginPage.login('admin@talky.ai', 'secure-password');
    
    // Verify dashboard loads
    await expect(dashboardPage.heading).toBeVisible();
    await expect(dashboardPage.kpiCards).toHaveCount(4);
    
    // Verify key metrics are displayed
    await expect(dashboardPage.activeTenantsCount).toContainText(/\d+/);
    await expect(dashboardPage.totalUsersCount).toContainText(/\d+/);
  });

  test('admin can suspend and reactivate tenant', async ({ page }) => {
    const dashboardPage = new AdminDashboardPage(page);
    const tenantPage = new TenantManagementPage(page);
    
    // Navigate to tenant management
    await dashboardPage.navigateToTenants();
    
    // Select a tenant
    await tenantPage.selectTenant('Test Corp');
    
    // Suspend the tenant
    await tenantPage.suspendTenant('Testing suspension functionality');
    
    // Verify suspension
    await expect(tenantPage.statusBadge).toHaveText('Suspended');
    
    // Reactivate the tenant
    await tenantPage.reactivateTenant();
    
    // Verify reactivation
    await expect(tenantPage.statusBadge).toHaveText('Active');
  });

  test('admin can update tenant quota', async ({ page }) => {
    const tenantPage = new TenantManagementPage(page);
    
    await tenantPage.goto();
    await tenantPage.selectTenant('Test Corp');
    
    // Get current quota
    const currentQuota = await tenantPage.getCurrentQuota();
    
    // Update quota
    const newQuota = currentQuota + 1000;
    await tenantPage.updateQuota(newQuota, 'Increasing for testing');
    
    // Verify update
    await expect(tenantPage.quotaDisplay).toContainText(newQuota.toString());
    
    // Verify audit log entry
    await tenantPage.viewAuditLog();
    await expect(page.locator('text=Quota updated')).toBeVisible();
  });

  test('real-time notifications work correctly', async ({ page }) => {
    const dashboardPage = new AdminDashboardPage(page);
    
    await dashboardPage.goto();
    
    // Wait for WebSocket connection
    await page.waitForTimeout(1000);
    
    // Trigger a system alert (via API or database)
    await triggerSystemAlert('Test Alert', 'high');
    
    // Verify notification appears
    await expect(dashboardPage.notificationBadge).toBeVisible();
    await expect(dashboardPage.notificationItem).toContainText('Test Alert');
    
    // Dismiss notification
    await dashboardPage.dismissNotification();
    
    // Verify notification is gone
    await expect(dashboardPage.notificationItem).not.toBeVisible();
  });
});
```

#### Cross-Browser Testing
```typescript
// playwright.config.ts
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure'
  },
  
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
    {
      name: 'Mobile Chrome',
      use: { ...devices['Pixel 5'] },
    },
    {
      name: 'Mobile Safari',
      use: { ...devices['iPhone 12'] },
    }
  ]
});
```

## Deployment Strategy

### Environment Configuration

#### Environment Variables
```bash
# .env.production
NEXT_PUBLIC_API_URL=https://api.talky.ai
NEXT_PUBLIC_WS_URL=wss://api.talky.ai
NEXT_PUBLIC_ENVIRONMENT=production

# Admin-specific
ADMIN_SESSION_TIMEOUT=28800000  # 8 hours
ADMIN_RATE_LIMIT_WINDOW=60000   # 1 minute
ADMIN_RATE_LIMIT_MAX=100        # requests per minute
ADMIN_MFA_REQUIRED=true
ADMIN_IP_WHITELIST_ENABLED=true

# Security
ADMIN_ENCRYPTION_KEY=${ADMIN_ENCRYPTION_KEY}
ADMIN_JWT_SECRET=${ADMIN_JWT_SECRET}
ADMIN_AUDIT_RETENTION_DAYS=90
```

#### Docker Configuration
```dockerfile
# Dockerfile
FROM node:18-alpine AS builder

WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production

COPY . .
RUN npm run build

FROM node:18-alpine AS runner
WORKDIR /app

# Security: Run as non-root user
RUN addgroup -g 1001 -S nodejs
RUN adduser -S nextjs -u 1001

# Copy built application
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs

EXPOSE 3000

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

CMD ["node", "server.js"]
```

### Deployment Pipeline

#### CI/CD Configuration
```yaml
# .github/workflows/deploy-admin-panel.yml
name: Deploy Admin Panel

on:
  push:
    branches: [main]
    paths: ['admin-panel/**']
  pull_request:
    branches: [main]
    paths: ['admin-panel/**']

env:
  NODE_VERSION: '18'
  REGISTRY: ghcr.io
  IMAGE_NAME: talky-ai/admin-panel

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Setup Node.js
        uses: actions/setup-node@v3
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'npm'
          cache-dependency-path: admin-panel/package-lock.json
      
      - name: Install dependencies
        run: npm ci
        working-directory: admin-panel
      
      - name: Run unit tests
        run: npm run test:unit -- --coverage
        working-directory: admin-panel
      
      - name: Run integration tests
        run: npm run test:integration
        working-directory: admin-panel
        env:
          TEST_DATABASE_URL: ${{ secrets.TEST_DATABASE_URL }}
      
      - name: Upload coverage reports
        uses: codecov/codecov-action@v3
        with:
          directory: admin-panel/coverage
  
  security-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Run security audit
        run: npm audit --audit-level=high
        working-directory: admin-panel
      
      - name: Run Snyk security scan
        uses: snyk/actions/node@master
        env:
          SNYK_TOKEN: ${{ secrets.SNYK_TOKEN }}
        with:
          args: --severity-threshold=high
  
  build:
    needs: [test, security-scan]
    runs-on: ubuntu-latest
    outputs:
      image-tag: ${{ steps.meta.outputs.tags }}
    steps:
      - uses: actions/checkout@v3
      
      - name: Setup Docker Buildx
        uses: docker/setup-buildx-action@v2
      
      - name: Login to Container Registry
        uses: docker/login-action@v2
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      
      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v4
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=ref,event=branch
            type=ref,event=pr
            type=sha,prefix={{branch}}-
            type=raw,value=latest,enable={{is_default_branch}}
      
      - name: Build and push Docker image
        uses: docker/build-push-action@v4
        with:
          context: ./admin-panel
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
  
  deploy-staging:
    needs: build
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    environment: staging
    steps:
      - name: Deploy to staging
        uses: azure/webapps-deploy@v2
        with:
          app-name: talky-ai-admin-staging
          images: ${{ needs.build.outputs.image-tag }}
      
      - name: Run smoke tests
        run: |
          npm run test:smoke -- --base-url=https://admin-staging.talky.ai
        working-directory: admin-panel
      
      - name: Run E2E tests
        run: |
          npm run test:e2e -- --base-url=https://admin-staging.talky.ai
        working-directory: admin-panel
        env:
          E2E_ADMIN_USERNAME: ${{ secrets.E2E_ADMIN_USERNAME }}
          E2E_ADMIN_PASSWORD: ${{ secrets.E2E_ADMIN_PASSWORD }}
  
  deploy-production:
    needs: [build, deploy-staging]
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    environment: production
    steps:
      - name: Deploy to production
        uses: azure/webapps-deploy@v2
        with:
          app-name: talky-ai-admin-production
          images: ${{ needs.build.outputs.image-tag }}
      
      - name: Verify deployment
        run: |
          curl -f https://admin.talky.ai/health || exit 1
      
      - name: Notify deployment success
        uses: 8398a7/action-slack@v3
        with:
          status: success
          text: 'Admin panel deployed successfully to production'
        env:
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
```

### Blue-Green Deployment

#### Deployment Strategy
```typescript
// deployment/blue-green-deployment.ts
interface DeploymentConfig {
  environment: 'staging' | 'production';
  blueDeployment: string;
  greenDeployment: string;
  currentActive: 'blue' | 'green';
  healthCheckUrl: string;
  healthCheckTimeout: number;
  rollbackOnFailure: boolean;
}

class BlueGreenDeployment {
  async deployNewVersion(config: DeploymentConfig): Promise<DeploymentResult> {
    const inactiveDeployment = config.currentActive === 'blue' ? 'green' : 'blue';
    
    try {
      // 1. Deploy to inactive environment
      console.log(`Deploying to ${inactiveDeployment} environment...`);
      await this.deployToEnvironment(inactiveDeployment, config);
      
      // 2. Run health checks
      console.log('Running health checks...');
      const healthCheck = await this.runHealthChecks(config);
      
      if (!healthCheck.healthy) {
        throw new Error(`Health checks failed: ${healthCheck.errors.join(', ')}`);
      }
      
      // 3. Run smoke tests
      console.log('Running smoke tests...');
      const smokeTests = await this.runSmokeTests(config);
      
      if (!smokeTests.passed) {
        throw new Error(`Smoke tests failed: ${smokeTests.errors.join(', ')}`);
      }
      
      // 4. Switch traffic
      console.log('Switching traffic...');
      await this.switchTraffic(inactiveDeployment, config);
      
      // 5. Verify switch
      console.log('Verifying traffic switch...');
      await this.verifyTrafficSwitch(config);
      
      // 6. Update DNS/load balancer
      console.log('Updating load balancer configuration...');
      await this.updateLoadBalancer(inactiveDeployment, config);
      
      return {
        success: true,
        newActiveDeployment: inactiveDeployment,
        previousDeployment: config.currentActive,
        healthCheckResults: healthCheck,
        smokeTestResults: smokeTests
      };
      
    } catch (error) {
      // Rollback if configured
      if (config.rollbackOnFailure) {
        console.log('Deployment failed, initiating rollback...');
        await this.rollbackDeployment(config);
      }
      
      throw error;
    }
  }
  
  private async runHealthChecks(config: DeploymentConfig): Promise<HealthCheckResult> {
    const checks = [
      this.checkApplicationHealth(config),
      this.checkDatabaseConnectivity(config),
      this.checkExternalServiceHealth(config),
      this.checkMemoryUsage(config),
      this.checkCPUUsage(config)
    ];
    
    const results = await Promise.allSettled(checks);
    
    const errors = results
      .filter(result => result.status === 'rejected')
      .map(result => (result as PromiseRejectedResult).reason.message);
    
    return {
      healthy: errors.length === 0,
      errors,
      details: results.map((result, index) => ({
        check: ['application', 'database', 'external', 'memory', 'cpu'][index],
        status: result.status === 'fulfilled' ? 'passed' : 'failed',
        message: result.status === 'fulfilled' ? result.value : result.reason.message
      }))
    };
  }
}
```

## Monitoring and Observability

### Application Monitoring

#### Metrics Collection
```typescript
// monitoring/metrics.ts
import { Registry, Counter, Histogram, Gauge } from 'prom-client';

export const metricsRegistry = new Registry();

// HTTP request metrics
export const httpRequestDuration = new Histogram({
  name: 'admin_http_request_duration_seconds',
  help: 'Duration of HTTP requests in seconds',
  labelNames: ['method', 'route', 'status_code'],
  buckets: [0.1, 0.3, 0.5, 0.7, 1, 3, 5, 7, 10]
});

export const httpRequestTotal = new Counter({
  name: 'admin_http_requests_total',
  help: 'Total number of HTTP requests',
  labelNames: ['method', 'route', 'status_code']
});

// Business metrics
export const adminActionsTotal = new Counter({
  name: 'admin_actions_total',
  help: 'Total number of admin actions performed',
  labelNames: ['action_type', 'outcome', 'user_role']
});

export const activeAdminSessions = new Gauge({
  name: 'admin_active_sessions',
  help: 'Number of active admin sessions',
  labelNames: ['role']
});

// System metrics
export const systemHealth = new Gauge({
  name: 'admin_system_health',
  help: 'System health status (1 = healthy, 0 = unhealthy)',
  labelNames: ['component']
});

// Register all metrics
metricsRegistry.registerMetric(httpRequestDuration);
metricsRegistry.registerMetric(httpRequestTotal);
metricsRegistry.registerMetric(adminActionsTotal);
metricsRegistry.registerMetric(activeAdminSessions);
metricsRegistry.registerMetric(systemHealth);
```

#### Logging Configuration
```typescript
// monitoring/logging.ts
import winston from 'winston';

const logFormat = winston.format.combine(
  winston.format.timestamp(),
  winston.format.errors({ stack: true }),
  winston.format.json()
);

export const logger = winston.createLogger({
  level: process.env.LOG_LEVEL || 'info',
  format: logFormat,
  defaultMeta: { service: 'admin-panel' },
  transports: [
    new winston.transports.File({ filename: 'logs/error.log', level: 'error' }),
    new winston.transports.File({ filename: 'logs/combined.log' }),
    new winston.transports.Console({
      format: winston.format.combine(
        winston.format.colorize(),
        winston.format.simple()
      )
    })
  ],
  
  // Add custom log levels for security events
  levels: {
    ...winston.config.npm.levels,
    security: 1,
    audit: 2
  }
});

// Security event logging
export const securityLogger = winston.createLogger({
  level: 'security',
  format: logFormat,
  defaultMeta: { type: 'security_event' },
  transports: [
    new winston.transports.File({ filename: 'logs/security.log' })
  ]
});

// Audit logging
export const auditLogger = winston.createLogger({
  level: 'audit',
  format: logFormat,
  defaultMeta: { type: 'audit_log' },
  transports: [
    new winston.transports.File({ filename: 'logs/audit.log' })
  ]
});
```

### Alerting Configuration

#### Alert Rules
```yaml
# monitoring/alert-rules.yml
groups:
  - name: admin_panel_alerts
    rules:
      - alert: HighErrorRate
        expr: rate(admin_http_requests_total{status_code=~"5.."}[5m]) > 0.1
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "High error rate in admin panel"
          description: "Error rate is {{ $value | humanizePercentage }} over the last 5 minutes"
      
      - alert: HighResponseTime
        expr: histogram_quantile(0.95, admin_http_request_duration_seconds_bucket) > 2
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High response time in admin panel"
          description: "95th percentile response time is {{ $value }}s"
      
      - alert: TooManyFailedLogins
        expr: rate(admin_failed_logins_total[5m]) > 10
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "High number of failed login attempts"
          description: "{{ $value | humanize }} failed login attempts per second"
      
      - alert: AdminSessionAnomaly
        expr: admin_active_sessions > 50
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Unusually high number of admin sessions"
          description: "{{ $value }} active admin sessions detected"
      
      - alert: SystemHealthDegraded
        expr: admin_system_health != 1
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "System health is degraded"
          description: "{{ $labels.component }} is reporting unhealthy status"
```

## Operational Procedures

### Backup and Recovery

#### Database Backup Strategy
```sql
-- Automated backup procedures
-- Daily full backups at 2 AM UTC
-- Hourly incremental backups
-- 30-day retention for daily backups
-- 7-day retention for hourly backups

-- Full backup function
CREATE OR REPLACE FUNCTION perform_full_backup()
RETURNS void AS $$
BEGIN
    -- Create backup with timestamp
    PERFORM pg_dump 
        --format=custom 
        --verbose 
        --file='/backups/full_backup_' || to_char(now(), 'YYYY-MM-DD_HH24-MI-SS') || '.dump'
        --host=localhost 
        --port=5432 
        --username=backup_user 
        --dbname=talky_ai_production;
    
    -- Log backup completion
    INSERT INTO backup_log (backup_type, status, started_at, completed_at)
    VALUES ('full', 'completed', now(), now());
END;
$$ LANGUAGE plpgsql;

-- Incremental backup function
CREATE OR REPLACE FUNCTION perform_incremental_backup()
RETURNS void AS $$
BEGIN
    -- Create incremental backup based on WAL files
    PERFORM pg_basebackup 
        --format=tar 
        --gzip 
        --checkpoint=fast 
        --label='incremental_' || to_char(now(), 'YYYY-MM-DD_HH24-MI-SS')
        --target-dir='/backups/incremental/' || to_char(now(), 'YYYY-MM-DD_HH24-MI-SS');
    
    -- Log backup completion
    INSERT INTO backup_log (backup_type, status, started_at, completed_at)
    VALUES ('incremental', 'completed', now(), now());
END;
$$ LANGUAGE plpgsql;
```

#### Disaster Recovery Procedures
```typescript
// disaster-recovery.ts
interface DisasterRecoveryPlan {
  recoveryTimeObjective: number; // RTO in minutes
  recoveryPointObjective: number; // RPO in minutes
  backupLocations: string[];
  recoveryProcedures: RecoveryProcedure[];
  communicationPlan: CommunicationPlan;
  testingSchedule: string;
}

class DisasterRecoveryService {
  async executeDisasterRecovery(scenario: DisasterScenario): Promise<RecoveryResult> {
    const startTime = Date.now();
    
    try {
      // 1. Assess the situation
      const assessment = await this.assessDisaster(scenario);
      
      // 2. Activate disaster recovery team
      await this.activateDRTeam(assessment);
      
      // 3. Restore from backup
      const restoreResult = await this.restoreFromBackup(assessment);
      
      // 4. Verify system integrity
      const verificationResult = await this.verifySystemIntegrity();
      
      // 5. Switch to disaster recovery site
      await this.switchToDRSite(assessment);
      
      // 6. Notify stakeholders
      await this.notifyStakeholders(assessment, 'recovery_complete');
      
      const recoveryTime = Date.now() - startTime;
      
      return {
        success: true,
        recoveryTime,
        recoveryPoint: restoreResult.recoveryPoint,
        verificationStatus: verificationResult.status,
        nextSteps: verificationResult.nextSteps
      };
      
    } catch (error) {
      await this.notifyStakeholders(scenario, 'recovery_failed');
      throw error;
    }
  }
}
```

### Performance Optimization

#### Performance Monitoring
```typescript
// performance-monitoring.ts
interface PerformanceMetrics {
  pageLoadTime: number;
  timeToInteractive: number;
  firstContentfulPaint: number;
  largestContentfulPaint: number;
  firstInputDelay: number;
  cumulativeLayoutShift: number;
  apiResponseTimes: Record<string, number>;
  errorRate: number;
}

class PerformanceMonitor {
  private metrics: PerformanceMetrics = {
    pageLoadTime: 0,
    timeToInteractive: 0,
    firstContentfulPaint: 0,
    largestContentfulPaint: 0,
    firstInputDelay: 0,
    cumulativeLayoutShift: 0,
    apiResponseTimes: {},
    errorRate: 0
  };

  async collectPerformanceMetrics(): Promise<PerformanceMetrics> {
    // Collect Web Vitals
    this.metrics.pageLoadTime = performance.timing.loadEventEnd - performance.timing.navigationStart;
    this.metrics.timeToInteractive = await this.measureTimeToInteractive();
    
    // Collect Core Web Vitals
    this.metrics.firstContentfulPaint = await this.measureFCP();
    this.metrics.largestContentfulPaint = await this.measureLCP();
    this.metrics.firstInputDelay = await this.measureFID();
    this.metrics.cumulativeLayoutShift = await this.measureCLS();
    
    // Collect API performance
    this.metrics.apiResponseTimes = await this.collectAPIResponseTimes();
    
    // Calculate error rate
    this.metrics.errorRate = await this.calculateErrorRate();
    
    return this.metrics;
  }
  
  private async measureTimeToInteractive(): Promise<number> {
    return new Promise((resolve) => {
      if ('PerformanceObserver' in window) {
        const observer = new PerformanceObserver((list) => {
          const entries = list.getEntries();
          const tti = entries.find(entry => entry.name === 'time-to-interactive');
          if (tti) {
            resolve(tti.startTime);
            observer.disconnect();
          }
        });
        observer.observe({ entryTypes: ['measure'] });
      } else {
        resolve(0);
      }
    });
  }
}
```

#### Database Performance Optimization
```sql
-- Performance optimization queries
-- Index optimization for common admin queries
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_admin_tenant_search 
ON tenants(business_name, status) 
WHERE status = 'active';

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_admin_user_activity 
ON user_profiles(last_active_at DESC, tenant_id) 
WHERE status = 'active';

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_admin_audit_log_recent 
ON admin_audit_log(created_at DESC, admin_user_id) 
WHERE created_at > NOW() - INTERVAL '30 days';

-- Query performance analysis
EXPLAIN (ANALYZE, BUFFERS) 
SELECT t.*, p.name as plan_name, 
       COUNT(u.id) as user_count,
       COUNT(c.id) as campaign_count
FROM tenants t
LEFT JOIN plans p ON t.plan_id = p.id
LEFT JOIN user_profiles u ON t.id = u.tenant_id AND u.status = 'active'
LEFT JOIN campaigns c ON t.id = c.tenant_id
WHERE t.status = 'active'
GROUP BY t.id, p.name
ORDER BY t.created_at DESC
LIMIT 50;
```

This comprehensive testing and deployment guide ensures the Talky.ai admin panel is deployed reliably, monitored effectively, and maintained properly across all environments while maintaining high performance and availability standards.