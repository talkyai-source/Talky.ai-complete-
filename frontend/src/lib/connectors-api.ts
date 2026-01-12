/**
 * Connectors API Client
 * Handles OAuth integrations for calendar, email, CRM, and drive
 * Day 24: Unified Connector System
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

// ============================================
// Types
// ============================================

export interface ProviderInfo {
    provider: string;
    type: string;
    name: string;
    description: string;
    requires_oauth: boolean;
}

export interface Connector {
    id: string;
    type: string;
    provider: string;
    name: string | null;
    status: string;
    account_email: string | null;
    created_at: string;
}

export interface OAuthAuthorizeResponse {
    authorization_url: string;
    state: string;
}

export interface CreateConnectorRequest {
    type: string;
    provider: string;
    name?: string;
}

// Provider icons mapping
export const PROVIDER_ICONS: Record<string, string> = {
    google_calendar: "📅",
    gmail: "📧",
    hubspot: "🏢",
    google_drive: "📁",
};

// Provider brand colors
export const PROVIDER_COLORS: Record<string, { bg: string; text: string; border: string }> = {
    google_calendar: {
        bg: "bg-blue-500/10",
        text: "text-blue-400",
        border: "border-blue-500/30",
    },
    gmail: {
        bg: "bg-red-500/10",
        text: "text-red-400",
        border: "border-red-500/30",
    },
    hubspot: {
        bg: "bg-orange-500/10",
        text: "text-orange-400",
        border: "border-orange-500/30",
    },
    google_drive: {
        bg: "bg-yellow-500/10",
        text: "text-yellow-400",
        border: "border-yellow-500/30",
    },
};

// Provider type descriptions
export const CONNECTOR_TYPES: Record<string, { title: string; description: string; icon: string }> = {
    calendar: {
        title: "Calendar",
        description: "Book meetings and manage events",
        icon: "📅",
    },
    email: {
        title: "Email",
        description: "Send and receive emails",
        icon: "📧",
    },
    crm: {
        title: "CRM",
        description: "Sync contacts and deals",
        icon: "🏢",
    },
    drive: {
        title: "Storage",
        description: "Upload and manage files",
        icon: "📁",
    },
};

// ============================================
// API Client
// ============================================

class ConnectorsApiClient {
    private getToken(): string | null {
        if (typeof window === "undefined") return null;
        // Try multiple storage keys for compatibility
        return localStorage.getItem("token") || localStorage.getItem("access_token");
    }

    private async request<T>(
        endpoint: string,
        options: RequestInit = {}
    ): Promise<T> {
        const token = this.getToken();

        if (!token && !endpoint.includes("/providers")) {
            // Most endpoints require auth, except listing providers
            throw new Error("Please log in to continue");
        }

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

            // Handle auth errors
            if (response.status === 401 || response.status === 403) {
                throw new Error("Session expired. Please log in again.");
            }

            throw new Error(error.detail);
        }

        return response.json();
    }

    // ========================================
    // Providers (public)
    // ========================================

    async listProviders(): Promise<ProviderInfo[]> {
        return this.request<ProviderInfo[]>("/connectors/providers");
    }

    // ========================================
    // Connectors
    // ========================================

    async listConnectors(type?: string): Promise<Connector[]> {
        const params = type ? `?type=${type}` : "";
        return this.request<Connector[]>(`/connectors${params}`);
    }

    async getConnector(connectorId: string): Promise<Connector> {
        return this.request<Connector>(`/connectors/${connectorId}`);
    }

    async deleteConnector(connectorId: string): Promise<{ success: boolean; message: string }> {
        return this.request<{ success: boolean; message: string }>(
            `/connectors/${connectorId}`,
            { method: "DELETE" }
        );
    }

    // ========================================
    // OAuth
    // ========================================

    async authorize(request: CreateConnectorRequest): Promise<OAuthAuthorizeResponse> {
        return this.request<OAuthAuthorizeResponse>("/connectors/authorize", {
            method: "POST",
            body: JSON.stringify(request),
        });
    }

    async refreshTokens(connectorId: string): Promise<{ success: boolean; message: string }> {
        return this.request<{ success: boolean; message: string }>(
            `/connectors/${connectorId}/refresh`,
            { method: "POST" }
        );
    }
}

export const connectorsApi = new ConnectorsApiClient();
