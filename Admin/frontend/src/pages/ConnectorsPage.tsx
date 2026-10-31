import { useCallback, useEffect, useRef, useState } from 'react';
import { Link2, CheckCircle, XCircle, Clock, RefreshCw } from 'lucide-react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { ConnectorsTable } from '../components/ConnectorsTable';
import { ConnectorDetailDrawer } from '../components/ConnectorDetailDrawer';
import { UsageBreakdownCard } from '../components/UsageBreakdownCard';
import { api } from '../lib/api';
import type { AdminConnectorItem, AdminConnectorListResponse } from '../lib/api';
import { useAuthGeneration } from '../lib/use-auth-generation';

export function ConnectorsPage() {
    const generation = useAuthGeneration();
    return <OwnedConnectorsPage key={generation} generation={generation} />;
}

function OwnedConnectorsPage({ generation }: { generation: number }) {
    const [selectedConnector, setSelectedConnector] = useState<AdminConnectorItem | null>(null);
    const [stats, setStats] = useState<AdminConnectorListResponse['summary']>(undefined);
    const [statsError, setStatsError] = useState<string | null>(null);
    const [statsLoading, setStatsLoading] = useState(true);
    const [refreshKey, setRefreshKey] = useState(0);
    const [statsVersion, setStatsVersion] = useState(0);
    const sequence = useRef(0);
    const mounted = useRef(false);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; sequence.current += 1; };
    }, []);

    const fetchStats = useCallback(async () => {
        if (!mounted.current || generation !== api.getAuthGeneration()) return;
        const request = ++sequence.current;
        const owns = () => mounted.current && request === sequence.current && generation === api.getAuthGeneration();
        setStatsLoading(true);
        try {
            const response = await api.getConnectors({ page_size: 1 });
            if (!owns()) return;
            if (response.error) throw new Error(response.error.message);
            const summary = response.data?.summary;
            if (!summary || summary.scope !== 'all_connectors'
                || [summary.total, summary.active, summary.expiring_or_expired, summary.error].some(value => !Number.isSafeInteger(value) || value < 0))
                throw new Error('Complete connector totals are unavailable.');
            setStats(summary); setStatsError(null);
        } catch (error) {
            if (owns()) { setStats(undefined); setStatsError(error instanceof Error ? error.message : 'Connector totals are unavailable.'); }
        } finally {
            if (owns()) setStatsLoading(false);
        }
    }, [generation]);

    useEffect(() => {
        // Fetching external connector data is the synchronization performed here.
        void fetchStats();
    }, [fetchStats, refreshKey, statsVersion]);

    const handleRefresh = () => {
        setRefreshKey(k => k + 1);
    };

    return (
        <div className="app-layout">
            <Sidebar />

            <main className="main-content">
                <Header />

                <div className="dashboard-content">
                    {/* Page Header */}
                    <div className="page-header">
                        <div className="page-header-content-left">
                            <div className="page-header-icon">
                                <Link2 />
                            </div>
                            <div>
                                <h1 className="page-title">Connectors</h1>
                                <p className="page-description">
                                    Manage OAuth integrations and third-party connections
                                </p>
                            </div>
                        </div>
                        <button className="btn btn-primary" onClick={handleRefresh}>
                            <RefreshCw size={16} />
                            Refresh
                        </button>
                    </div>

                    {/* Stats Cards */}
                    <div className="stats-grid stats-grid-4">
                        <div className="stat-card">
                            <div className="stat-icon">
                                <Link2 size={20} />
                            </div>
                            <div className="stat-content">
                                <span className="stat-value">{statsLoading ? 'Loading…' : stats?.total ?? 'Unavailable'}</span>
                                <span className="stat-label">Total Connectors</span>
                            </div>
                        </div>
                        <div className="stat-card stat-success">
                            <div className="stat-icon">
                                <CheckCircle size={20} />
                            </div>
                            <div className="stat-content">
                                <span className="stat-value">{statsLoading ? 'Loading…' : stats?.active ?? 'Unavailable'}</span>
                                <span className="stat-label">Active status</span>
                            </div>
                        </div>
                        <div className="stat-card stat-warning">
                            <div className="stat-icon">
                                <Clock size={20} />
                            </div>
                            <div className="stat-content">
                                <span className="stat-value">{statsLoading ? 'Loading…' : stats?.expiring_or_expired ?? 'Unavailable'}</span>
                                <span className="stat-label">Token expiry due</span>
                            </div>
                        </div>
                        <div className="stat-card stat-danger">
                            <div className="stat-icon">
                                <XCircle size={20} />
                            </div>
                            <div className="stat-content">
                                <span className="stat-value">{statsLoading ? 'Loading…' : stats?.error ?? 'Unavailable'}</span>
                                <span className="stat-label">Needs attention</span>
                            </div>
                        </div>
                    </div>
                    <p>Totals cover all connectors. Token expiry includes expired tokens and those due within 24 hours. Needs attention includes connection errors or uncertain refreshes; categories can overlap.</p>
                    {statsError && <div className="error-banner" role="alert">{statsError}<button onClick={() => void fetchStats()}>Retry totals</button></div>}

                    {/* Two Column Layout */}
                    <div className="connectors-layout">
                        {/* Connectors Table */}
                        <div className="connectors-main">
                            <ConnectorsTable
                                refreshVersion={refreshKey}
                                onSelectConnector={setSelectedConnector}
                                onChanged={() => setStatsVersion(value => value + 1)}
                            />
                        </div>

                        {/* Usage Sidebar */}
                        <div className="connectors-sidebar">
                            <UsageBreakdownCard />
                        </div>
                    </div>
                </div>
            </main>

            {/* Detail Drawer */}
            {selectedConnector && (
                <ConnectorDetailDrawer
                    connector={selectedConnector}
                    onClose={() => setSelectedConnector(null)}
                    onRefresh={handleRefresh}
                />
            )}
        </div>
    );
}
