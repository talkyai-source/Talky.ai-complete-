import { useState, useEffect } from 'react';
import { Link2, CheckCircle, XCircle, Clock, RefreshCw } from 'lucide-react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { ConnectorsTable } from '../components/ConnectorsTable';
import { ConnectorDetailDrawer } from '../components/ConnectorDetailDrawer';
import { UsageBreakdownCard } from '../components/UsageBreakdownCard';
import { api } from '../lib/api';
import type { AdminConnectorItem } from '../lib/api';

export function ConnectorsPage() {
    const [selectedConnector, setSelectedConnector] = useState<AdminConnectorItem | null>(null);
    const [stats, setStats] = useState({
        total: 0,
        active: 0,
        expired: 0,
        error: 0
    });
    const [refreshKey, setRefreshKey] = useState(0);

    useEffect(() => {
        fetchStats();
    }, [refreshKey]);

    const fetchStats = async () => {
        try {
            const response = await api.getConnectors({ page_size: 100 });
            if (response.data) {
                const connectors = response.data.items;
                setStats({
                    total: response.data.total,
                    active: connectors.filter(c => c.status === 'active').length,
                    expired: connectors.filter(c => c.token_status === 'expired' || c.token_status === 'expiring_soon').length,
                    error: connectors.filter(c => c.status === 'error').length
                });
            }
        } catch (error) {
            console.error('Failed to fetch connector stats:', error);
        }
    };

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
                                <span className="stat-value">{stats.total}</span>
                                <span className="stat-label">Total Connectors</span>
                            </div>
                        </div>
                        <div className="stat-card stat-success">
                            <div className="stat-icon">
                                <CheckCircle size={20} />
                            </div>
                            <div className="stat-content">
                                <span className="stat-value">{stats.active}</span>
                                <span className="stat-label">Active</span>
                            </div>
                        </div>
                        <div className="stat-card stat-warning">
                            <div className="stat-icon">
                                <Clock size={20} />
                            </div>
                            <div className="stat-content">
                                <span className="stat-value">{stats.expired}</span>
                                <span className="stat-label">Expiring/Expired</span>
                            </div>
                        </div>
                        <div className="stat-card stat-danger">
                            <div className="stat-icon">
                                <XCircle size={20} />
                            </div>
                            <div className="stat-content">
                                <span className="stat-value">{stats.error}</span>
                                <span className="stat-label">Errors</span>
                            </div>
                        </div>
                    </div>

                    {/* Two Column Layout */}
                    <div className="connectors-layout">
                        {/* Connectors Table */}
                        <div className="connectors-main">
                            <ConnectorsTable
                                key={refreshKey}
                                onSelectConnector={setSelectedConnector}
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
