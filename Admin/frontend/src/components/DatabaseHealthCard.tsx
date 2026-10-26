import { Database, Loader2, RefreshCw, AlertCircle } from 'lucide-react';
import { api } from '../lib/api';
import { useAdminRead } from '../lib/use-admin-read';
import { validDatabaseHealth } from '../lib/health-contracts';
import { metric, observedAt } from '../lib/health-display';

const loadDatabaseHealthCard = () => api.getDatabaseHealth();

export function DatabaseHealthCard() {
    const { data: db, loading, error, refresh: fetchDbHealth } = useAdminRead(loadDatabaseHealthCard, 30000, validDatabaseHealth);

    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title"><Database size={18} /> Database Health</h3>
                <div className="card-header-actions">
                    {db && !error && (
                        <span className={`badge ${db.connected ? 'badge-success' : 'badge-danger'}`}>
                            {db.connected ? 'Connected' : 'Disconnected'}
                        </span>
                    )}
                </div>
            </div>
            <div className="card-body">
                {loading ? (
                    <div className="loading-state">
                        <Loader2 className="animate-spin" size={24} />
                        <span>Loading database status...</span>
                    </div>
                ) : error || !db ? (
                    <div className="error-state">
                        <AlertCircle size={24} />
                        <span role="alert">Database observations unavailable: {error ?? 'No data returned'}</span>
                        <button className="btn btn-sm btn-secondary" onClick={fetchDbHealth}>
                            <RefreshCw size={14} /> Retry
                        </button>
                    </div>
                ) : (
                    <>
                    <p className="text-muted">{db.scope}</p>
                    {db.error && <p role="alert">{db.error}</p>}
                    <table className="data-table compact">
                        <tbody>
                            <tr>
                                <td className="detail-label">Latency</td>
                                <td>{metric(db.latency_ms, 'ms')}</td>
                            </tr>
                            <tr>
                                <td className="detail-label">Connection Pool</td>
                                <td>{metric(db.active_connections)} active / {metric(db.pool_size)} allocated</td>
                            </tr>
                            <tr>
                                <td className="detail-label">Available Connections</td>
                                <td>{metric(db.available_connections)}</td>
                            </tr>
                            <tr>
                                <td className="detail-label">Tables</td>
                                <td>{metric(db.table_count)}</td>
                            </tr>
                            <tr>
                                <td className="detail-label">Size</td>
                                <td>{metric(db.database_size_mb, ' MB', 1)}</td>
                            </tr>
                            <tr>
                                <td className="detail-label">Last Check</td>
                                <td>{observedAt(db.last_check)}</td>
                            </tr>
                        </tbody>
                    </table>
                    </>
                )}
            </div>
        </div>
    );
}
