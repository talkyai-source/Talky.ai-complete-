/**
 * Billing API Client
 * Handles Stripe subscription and billing operations
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

// ============================================
// Types
// ============================================

export interface BillingConfig {
    stripe_configured: boolean;
    mock_mode: boolean;
    publishable_key: string | null;
}

export interface CheckoutSessionRequest {
    plan_id: string;
    success_url?: string;
    cancel_url?: string;
}

export interface CheckoutSessionResponse {
    session_id: string;
    checkout_url: string;
    mock_mode: boolean;
    message?: string;
}

export interface PortalRequest {
    return_url?: string;
}

export interface PortalResponse {
    portal_url: string;
    mock_mode: boolean;
    message?: string;
}

export interface Subscription {
    status: string;
    plan_id: string | null;
    plan_name: string | null;
    current_period_start: string | null;
    current_period_end: string | null;
    cancel_at_period_end: boolean;
    minutes_allocated: number;
    minutes_used: number;
    minutes_remaining: number;
}

export interface UsageSummary {
    usage_type: string;
    total_used: number;
    allocated: number;
    remaining: number;
    overage: number;
}

export interface Invoice {
    id: string;
    stripe_invoice_id: string;
    amount_due: number;
    amount_paid: number;
    currency: string;
    status: string;
    invoice_pdf: string | null;
    hosted_invoice_url: string | null;
    period_start: string | null;
    period_end: string | null;
    paid_at: string | null;
    created_at: string;
}

export interface CancelResponse {
    status: string;
    cancel_at_period_end: boolean;
    mock_mode: boolean;
    message?: string;
}

export interface Plan {
    id: string;
    name: string;
    price: number;
    description: string;
    minutes: number;
    agents: number;
    concurrent_calls: number;
    features: string[];
    not_included: string[];
    popular: boolean;
    stripe_price_id: string | null;
    stripe_product_id: string | null;
    billing_period: string;
}

// ============================================
// API Client
// ============================================

class BillingApiClient {
    private getToken(): string | null {
        if (typeof window === "undefined") return null;
        return localStorage.getItem("token");
    }

    private async request<T>(
        endpoint: string,
        options: RequestInit = {}
    ): Promise<T> {
        const token = this.getToken();
        const headers: HeadersInit = {
            "Content-Type": "application/json",
            ...(options.headers || {}),
        };

        if (token) {
            (headers as Record<string, string>)["Authorization"] = `Bearer ${token}`;
        }

        const response = await fetch(`${API_BASE}${endpoint}`, {
            ...options,
            headers,
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({
                detail: "An error occurred",
            }));
            throw new Error(error.detail);
        }

        return response.json();
    }

    // ========================================
    // Billing Config
    // ========================================

    async getBillingConfig(): Promise<BillingConfig> {
        return this.request<BillingConfig>("/billing/config");
    }

    // ========================================
    // Subscription
    // ========================================

    async getSubscription(): Promise<Subscription> {
        return this.request<Subscription>("/billing/subscription");
    }

    async cancelSubscription(): Promise<CancelResponse> {
        return this.request<CancelResponse>("/billing/cancel", {
            method: "POST",
        });
    }

    // ========================================
    // Checkout
    // ========================================

    async createCheckoutSession(
        planId: string,
        successUrl?: string,
        cancelUrl?: string
    ): Promise<CheckoutSessionResponse> {
        return this.request<CheckoutSessionResponse>("/billing/create-checkout-session", {
            method: "POST",
            body: JSON.stringify({
                plan_id: planId,
                success_url: successUrl,
                cancel_url: cancelUrl,
            }),
        });
    }

    // ========================================
    // Customer Portal
    // ========================================

    async createPortalSession(returnUrl?: string): Promise<PortalResponse> {
        return this.request<PortalResponse>("/billing/portal", {
            method: "POST",
            body: JSON.stringify({
                return_url: returnUrl,
            }),
        });
    }

    // ========================================
    // Usage
    // ========================================

    async getUsageSummary(usageType: string = "minutes"): Promise<UsageSummary> {
        return this.request<UsageSummary>(`/billing/usage?usage_type=${usageType}`);
    }

    // ========================================
    // Invoices
    // ========================================

    async listInvoices(limit: number = 10): Promise<{ invoices: Invoice[]; count: number }> {
        return this.request<{ invoices: Invoice[]; count: number }>(
            `/billing/invoices?limit=${limit}`
        );
    }

    // ========================================
    // Plans
    // ========================================

    async getPlans(): Promise<Plan[]> {
        return this.request<Plan[]>("/plans");
    }
}

export const billingApi = new BillingApiClient();
