"use client";

import { useEffect, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import {
    connectorsApi,
    Connector,
    ProviderInfo,
    PROVIDER_ICONS,
    PROVIDER_COLORS,
    CONNECTOR_TYPES,
} from "@/lib/connectors-api";
import {
    CheckCircle,
    XCircle,
    RefreshCw,
    ExternalLink,
    Plug,
    AlertTriangle,
    Loader2,
    Calendar,
    Mail,
    Building2,
    HardDrive,
    Trash2,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

// ============================================
// Type Icon Component
// ============================================

function TypeIcon({ type }: { type: string }) {
    const iconClass = "w-5 h-5";
    switch (type) {
        case "calendar":
            return <Calendar className={iconClass} />;
        case "email":
            return <Mail className={iconClass} />;
        case "crm":
            return <Building2 className={iconClass} />;
        case "drive":
            return <HardDrive className={iconClass} />;
        default:
            return <Plug className={iconClass} />;
    }
}

// ============================================
// Status Badge Component
// ============================================

function StatusBadge({ status }: { status: string }) {
    const styles: Record<string, string> = {
        active: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
        pending: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
        error: "bg-red-500/20 text-red-400 border-red-500/30",
        disconnected: "bg-gray-500/20 text-gray-400 border-gray-500/30",
    };

    return (
        <span className={`px-2 py-0.5 text-xs font-medium rounded-full border ${styles[status] || styles.disconnected}`}>
            {status.charAt(0).toUpperCase() + status.slice(1)}
        </span>
    );
}

// ============================================
// Connected Connector Card
// ============================================

function ConnectorCard({
    connector,
    onDisconnect,
    onRefresh,
    loading,
}: {
    connector: Connector;
    onDisconnect: (id: string) => void;
    onRefresh: (id: string) => void;
    loading: boolean;
}) {
    const colors = PROVIDER_COLORS[connector.provider] || PROVIDER_COLORS.google_calendar;

    return (
        <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.95 }}
            className={`content-card ${colors.border}`}
        >
            <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                    <div className={`p-3 rounded-xl ${colors.bg}`}>
                        <span className="text-2xl">{PROVIDER_ICONS[connector.provider] || "🔗"}</span>
                    </div>
                    <div>
                        <h3 className="font-semibold text-white">
                            {connector.name || connector.provider.replace("_", " ").replace(/\b\w/g, c => c.toUpperCase())}
                        </h3>
                        {connector.account_email && (
                            <p className="text-sm text-gray-400">{connector.account_email}</p>
                        )}
                    </div>
                </div>
                <StatusBadge status={connector.status} />
            </div>

            <div className="mt-4 flex items-center gap-2">
                <button
                    onClick={() => onRefresh(connector.id)}
                    disabled={loading}
                    className="flex items-center gap-1 px-3 py-1.5 text-sm bg-white/5 hover:bg-white/10 text-gray-300 rounded-lg transition-colors"
                    title="Refresh tokens"
                >
                    <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
                    Refresh
                </button>
                <button
                    onClick={() => onDisconnect(connector.id)}
                    disabled={loading}
                    className="flex items-center gap-1 px-3 py-1.5 text-sm text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
                >
                    <Trash2 className="w-4 h-4" />
                    Disconnect
                </button>
            </div>

            <p className="mt-3 text-xs text-gray-500">
                Connected {new Date(connector.created_at).toLocaleDateString()}
            </p>
        </motion.div>
    );
}

// ============================================
// Available Provider Card
// ============================================

function ProviderCard({
    provider,
    onConnect,
    loading,
    isConnected,
}: {
    provider: ProviderInfo;
    onConnect: (provider: ProviderInfo) => void;
    loading: boolean;
    isConnected: boolean;
}) {
    const colors = PROVIDER_COLORS[provider.provider] || PROVIDER_COLORS.google_calendar;

    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className={`content-card hover:border-white/20 transition-colors ${isConnected ? "opacity-60" : ""}`}
        >
            <div className="flex items-start gap-4">
                <div className={`p-3 rounded-xl ${colors.bg}`}>
                    <span className="text-2xl">{PROVIDER_ICONS[provider.provider] || "🔗"}</span>
                </div>
                <div className="flex-1">
                    <div className="flex items-center gap-2">
                        <h3 className="font-semibold text-white">{provider.name}</h3>
                        {isConnected && (
                            <CheckCircle className="w-4 h-4 text-emerald-400" />
                        )}
                    </div>
                    <p className="text-sm text-gray-400 mt-1">{provider.description}</p>
                    <div className="mt-2">
                        <span className={`inline-flex items-center gap-1 px-2 py-0.5 text-xs ${colors.bg} ${colors.text} rounded-full`}>
                            <TypeIcon type={provider.type} />
                            {CONNECTOR_TYPES[provider.type]?.title || provider.type}
                        </span>
                    </div>
                </div>
            </div>

            <button
                onClick={() => onConnect(provider)}
                disabled={loading || isConnected}
                className={`mt-4 w-full py-2.5 px-4 rounded-xl font-medium transition-all flex items-center justify-center gap-2 ${isConnected
                    ? "bg-white/5 text-gray-500 cursor-not-allowed"
                    : "bg-white/10 hover:bg-white/20 text-white"
                    }`}
            >
                {loading ? (
                    <>
                        <Loader2 className="w-4 h-4 animate-spin" />
                        Connecting...
                    </>
                ) : isConnected ? (
                    <>
                        <CheckCircle className="w-4 h-4" />
                        Connected
                    </>
                ) : (
                    <>
                        <ExternalLink className="w-4 h-4" />
                        Connect
                    </>
                )}
            </button>
        </motion.div>
    );
}

// ============================================
// Main Integrations Page
// ============================================

function IntegrationsPageContent() {
    const searchParams = useSearchParams();
    const [connectors, setConnectors] = useState<Connector[]>([]);
    const [providers, setProviders] = useState<ProviderInfo[]>([]);
    const [loading, setLoading] = useState(true);
    const [actionLoading, setActionLoading] = useState<string | null>(null);
    const [error, setError] = useState("");
    const [successMessage, setSuccessMessage] = useState("");

    // Check for OAuth callback params
    useEffect(() => {
        const success = searchParams.get("success");
        const errorParam = searchParams.get("error");
        const provider = searchParams.get("provider");

        if (success === "true" && provider) {
            setSuccessMessage(`Successfully connected ${provider.replace("_", " ")}!`);
            // Clear params from URL
            window.history.replaceState({}, "", "/integrations");
        } else if (errorParam) {
            setError(`OAuth error: ${errorParam}`);
            window.history.replaceState({}, "", "/integrations");
        }
    }, [searchParams]);

    useEffect(() => {
        loadData();
    }, []);

    async function loadData() {
        try {
            setLoading(true);
            const [connectorsData, providersData] = await Promise.all([
                connectorsApi.listConnectors(),
                connectorsApi.listProviders(),
            ]);
            setConnectors(connectorsData);
            setProviders(providersData);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load integrations");
        } finally {
            setLoading(false);
        }
    }

    async function handleConnect(provider: ProviderInfo) {
        try {
            setActionLoading(provider.provider);
            setError("");

            const response = await connectorsApi.authorize({
                type: provider.type,
                provider: provider.provider,
            });

            // Redirect to OAuth provider
            window.location.href = response.authorization_url;
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to start OAuth flow");
            setActionLoading(null);
        }
    }

    async function handleDisconnect(connectorId: string) {
        if (!confirm("Are you sure you want to disconnect this integration?")) {
            return;
        }

        try {
            setActionLoading(connectorId);
            await connectorsApi.deleteConnector(connectorId);
            setConnectors((prev) => prev.filter((c) => c.id !== connectorId));
            setSuccessMessage("Integration disconnected successfully");
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to disconnect");
        } finally {
            setActionLoading(null);
        }
    }

    async function handleRefresh(connectorId: string) {
        try {
            setActionLoading(connectorId);
            await connectorsApi.refreshTokens(connectorId);
            setSuccessMessage("Tokens refreshed successfully");
            await loadData();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to refresh tokens");
        } finally {
            setActionLoading(null);
        }
    }

    // Get connected provider names for checking
    const connectedProviders = new Set(connectors.map((c) => c.provider));

    // Group connectors by type
    const connectorsByType = connectors.reduce((acc, connector) => {
        if (!acc[connector.type]) {
            acc[connector.type] = [];
        }
        acc[connector.type].push(connector);
        return acc;
    }, {} as Record<string, Connector[]>);

    return (
        <DashboardLayout title="Integrations" description="Connect your favorite tools and services">
            {/* Success/Error Messages */}
            <AnimatePresence>
                {successMessage && (
                    <motion.div
                        initial={{ opacity: 0, y: -20 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0 }}
                        className="mb-6 flex items-center gap-3 p-4 bg-emerald-500/10 border border-emerald-500/30 rounded-xl"
                    >
                        <CheckCircle className="w-5 h-5 text-emerald-400" />
                        <p className="text-sm text-emerald-400">{successMessage}</p>
                        <button
                            onClick={() => setSuccessMessage("")}
                            className="ml-auto text-emerald-400 hover:text-emerald-300"
                        >
                            <XCircle className="w-4 h-4" />
                        </button>
                    </motion.div>
                )}

                {error && (
                    <motion.div
                        initial={{ opacity: 0, y: -20 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0 }}
                        className="mb-6 flex items-center gap-3 p-4 bg-red-500/10 border border-red-500/30 rounded-xl"
                    >
                        <AlertTriangle className="w-5 h-5 text-red-400" />
                        <p className="text-sm text-red-400">{error}</p>
                        <button
                            onClick={() => setError("")}
                            className="ml-auto text-red-400 hover:text-red-300"
                        >
                            <XCircle className="w-4 h-4" />
                        </button>
                    </motion.div>
                )}
            </AnimatePresence>

            {loading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
                </div>
            ) : (
                <div className="space-y-8">
                    {/* Connected Integrations */}
                    {connectors.length > 0 && (
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                        >
                            <h2 className="text-xl font-bold text-white mb-4 flex items-center gap-2">
                                <CheckCircle className="w-5 h-5 text-emerald-400" />
                                Connected Integrations ({connectors.length})
                            </h2>
                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                                <AnimatePresence>
                                    {connectors.map((connector, index) => (
                                        <ConnectorCard
                                            key={connector.id || `connector-${index}`}
                                            connector={connector}
                                            onDisconnect={handleDisconnect}
                                            onRefresh={handleRefresh}
                                            loading={actionLoading === connector.id}
                                        />
                                    ))}
                                </AnimatePresence>
                            </div>
                        </motion.div>
                    )}

                    {/* Available Integrations */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.1 }}
                    >
                        <h2 className="text-xl font-bold text-white mb-4 flex items-center gap-2">
                            <Plug className="w-5 h-5 text-gray-400" />
                            Available Integrations
                        </h2>

                        {/* Group by type */}
                        {Object.entries(CONNECTOR_TYPES).map(([type, info]) => {
                            const typeProviders = providers.filter((p) => p.type === type);
                            if (typeProviders.length === 0) return null;

                            return (
                                <div key={type} className="mb-6">
                                    <h3 className="text-lg font-medium text-gray-300 mb-3 flex items-center gap-2">
                                        <span>{info.icon}</span>
                                        {info.title}
                                        <span className="text-sm text-gray-500">— {info.description}</span>
                                    </h3>
                                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                                        {typeProviders.map((provider) => (
                                            <ProviderCard
                                                key={provider.provider}
                                                provider={provider}
                                                onConnect={handleConnect}
                                                loading={actionLoading === provider.provider}
                                                isConnected={connectedProviders.has(provider.provider)}
                                            />
                                        ))}
                                    </div>
                                </div>
                            );
                        })}
                    </motion.div>

                    {/* Help Section */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.2 }}
                        className="content-card bg-gradient-to-br from-white/5 to-white/0"
                    >
                        <div className="flex items-start gap-4">
                            <div className="p-3 bg-white/10 rounded-xl">
                                <AlertTriangle className="w-6 h-6 text-yellow-400" />
                            </div>
                            <div>
                                <h3 className="font-semibold text-white">Need OAuth Credentials?</h3>
                                <p className="text-sm text-gray-400 mt-1">
                                    To connect Google services, you need to set up OAuth credentials in Google Cloud Console.
                                    For HubSpot, create a developer app in HubSpot Developer Portal.
                                </p>
                                <div className="mt-3 flex flex-wrap gap-2">
                                    <a
                                        href="https://console.cloud.google.com/apis/credentials"
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="inline-flex items-center gap-1 text-sm text-blue-400 hover:text-blue-300"
                                    >
                                        <ExternalLink className="w-4 h-4" />
                                        Google Cloud Console
                                    </a>
                                    <a
                                        href="https://developers.hubspot.com/"
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="inline-flex items-center gap-1 text-sm text-orange-400 hover:text-orange-300"
                                    >
                                        <ExternalLink className="w-4 h-4" />
                                        HubSpot Developer Portal
                                    </a>
                                </div>
                            </div>
                        </div>
                    </motion.div>
                </div>
            )}
        </DashboardLayout>
    );
}

function LoadingFallback() {
    return (
        <div className="min-h-screen bg-gray-900 flex items-center justify-center">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-white" />
        </div>
    );
}

export default function IntegrationsPage() {
    return (
        <Suspense fallback={<LoadingFallback />}>
            <IntegrationsPageContent />
        </Suspense>
    );
}
