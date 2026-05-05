import { useState, useEffect, useCallback } from 'react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { Settings, RefreshCw, Loader2, Save, CheckCircle } from 'lucide-react';
import { api } from '../lib/api';
import type { SystemConfiguration, ProviderConfig } from '../lib/api';

function ProviderCard({
    type,
    config,
    onUpdate,
    updating,
}: {
    type: string;
    config: ProviderConfig;
    onUpdate: (type: string, active: string) => void;
    updating: boolean;
}) {
    const [selected, setSelected] = useState(config.active);

    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title">{type.toUpperCase()} Provider</h3>
                {updating && <Loader2 size={16} className="spinning" />}
            </div>
            <div className="card-body" style={{ padding: '16px 20px' }}>
                <div className="form-group">
                    <label className="form-label">Active Provider</label>
                    <select
                        className="filter-select"
                        value={selected}
                        onChange={(e) => setSelected(e.target.value)}
                        disabled={updating}
                    >
                        {config.available.map((provider) => (
                            <option key={provider} value={provider}>
                                {provider}
                            </option>
                        ))}
                    </select>
                </div>
                {selected !== config.active && (
                    <button
                        className="btn btn-primary"
                        onClick={() => onUpdate(type, selected)}
                        disabled={updating}
                    >
                        <Save size={14} />
                        Save Changes
                    </button>
                )}
            </div>
        </div>
    );
}

function FeatureToggle({
    label,
    enabled,
    onToggle,
}: {
    label: string;
    enabled: boolean;
    onToggle: () => void;
}) {
    return (
        <div className="feature-toggle">
            <span>{label}</span>
            <button
                className={`toggle-switch ${enabled ? 'active' : ''}`}
                onClick={onToggle}
            >
                <span className="toggle-knob"></span>
            </button>
        </div>
    );
}

export function SystemConfigPage() {
    const [config, setConfig] = useState<SystemConfiguration | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [updatingProvider, setUpdatingProvider] = useState<string | null>(null);
    const [saveMessage, setSaveMessage] = useState<string | null>(null);

    const fetchConfig = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const response = await api.getConfiguration();
            if (response.data) {
                setConfig(response.data);
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to fetch configuration');
            console.error('Failed to fetch configuration:', err);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchConfig();
    }, [fetchConfig]);

    const handleUpdateProvider = async (providerType: string, active: string) => {
        setUpdatingProvider(providerType);
        setSaveMessage(null);
        try {
            const response = await api.updateProviderConfig(providerType, { active });
            if (!response.error) {
                setSaveMessage(`${providerType.toUpperCase()} provider updated successfully`);
                fetchConfig();
            } else {
                setError(response.error.message || 'Failed to update provider');
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to update provider');
        } finally {
            setUpdatingProvider(null);
        }
    };

    return (
        <div className="app-layout">
            <Sidebar />

            <main className="main-content">
                <Header />

                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon">
                            <Settings />
                        </div>
                        <div>
                            <h1 className="page-title">System Configuration</h1>
                            <p className="page-description">Manage providers, features, and system limits</p>
                        </div>
                        <div className="page-header-actions">
                            <button
                                className="btn btn-secondary"
                                onClick={fetchConfig}
                                disabled={loading}
                            >
                                <RefreshCw size={16} className={loading ? 'spinning' : ''} />
                                Refresh
                            </button>
                        </div>
                    </div>

                    {saveMessage && (
                        <div className="success-banner">
                            <CheckCircle size={16} />
                            <p>{saveMessage}</p>
                        </div>
                    )}

                    {error && (
                        <div className="error-banner">
                            <p>{error}</p>
                            <button onClick={fetchConfig}>Retry</button>
                        </div>
                    )}

                    {loading ? (
                        <div className="table-loading">
                            <div className="loading-spinner"></div>
                            <p>Loading configuration...</p>
                        </div>
                    ) : config ? (
                        <>
                            <div className="config-grid">
                                {config.providers && Object.entries(config.providers).map(([type, providerConfig]) => (
                                    <ProviderCard
                                        key={type}
                                        type={type}
                                        config={providerConfig}
                                        onUpdate={handleUpdateProvider}
                                        updating={updatingProvider === type}
                                    />
                                ))}
                            </div>

                            <div className="card" style={{ marginTop: '24px' }}>
                                <div className="card-header">
                                    <h3 className="card-title">Features</h3>
                                </div>
                                <div className="card-body" style={{ padding: '16px 20px' }}>
                                    <div className="feature-toggles">
                                        {config.features && (
                                            <>
                                                <FeatureToggle
                                                    label="WebSocket Enabled"
                                                    enabled={config.features.websocket_enabled}
                                                    onToggle={() => {}}
                                                />
                                                <FeatureToggle
                                                    label="Analytics Enabled"
                                                    enabled={config.features.analytics_enabled}
                                                    onToggle={() => {}}
                                                />
                                                <FeatureToggle
                                                    label="Billing Enabled"
                                                    enabled={config.features.billing_enabled}
                                                    onToggle={() => {}}
                                                />
                                                <FeatureToggle
                                                    label="Quota Enforcement"
                                                    enabled={config.features.quota_enforcement}
                                                    onToggle={() => {}}
                                                />
                                            </>
                                        )}
                                    </div>
                                </div>
                            </div>

                            <div className="card" style={{ marginTop: '24px' }}>
                                <div className="card-header">
                                    <h3 className="card-title">System Limits</h3>
                                </div>
                                <div className="card-body" style={{ padding: '16px 20px' }}>
                                    <div className="limits-grid">
                                        {config.limits && (
                                            <>
                                                <div className="limit-item">
                                                    <span className="limit-label">Max Tenants</span>
                                                    <span className="limit-value">{config.limits.max_tenants}</span>
                                                </div>
                                                <div className="limit-item">
                                                    <span className="limit-label">Max Users per Tenant</span>
                                                    <span className="limit-value">{config.limits.max_users_per_tenant}</span>
                                                </div>
                                                <div className="limit-item">
                                                    <span className="limit-label">Max Concurrent Calls</span>
                                                    <span className="limit-value">{config.limits.max_concurrent_calls}</span>
                                                </div>
                                                <div className="limit-item">
                                                    <span className="limit-label">Max Campaigns per Tenant</span>
                                                    <span className="limit-value">{config.limits.max_campaigns_per_tenant}</span>
                                                </div>
                                            </>
                                        )}
                                    </div>
                                </div>
                            </div>
                        </>
                    ) : (
                        <div className="empty-state">
                            <Settings size={48} strokeWidth={1} />
                            <h3>No Configuration</h3>
                            <p>Could not load system configuration.</p>
                        </div>
                    )}
                </div>
            </main>
        </div>
    );
}
