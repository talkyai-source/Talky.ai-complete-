import { useState, useEffect, useCallback } from 'react';
import { Database, Loader2, RefreshCw, AlertCircle } from 'lucide-react';
import { api } from '../lib/api';
import type { DatabaseHealthResponse } from '../lib/api';

export function DatabaseHealthCard() {
    const [db, setDb] = useState<DatabaseHealthResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const fetchDbHealth = useCallback(async () => {
        try {
            const response = await api.getDatabaseHealth();
            if (response.data) {
                setDb(response.data);
                setError(null);
            } else if (response.error) {
                setError(response.error.message);
            }
        } catch {
            setError('Failed to fetch database health');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchDbHealth();
        const interval = setInterval(fetchDbHealth, 30000);
        return () => clearInterval(interval);
    }, [fetchDbHealth]);

    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title"><Database size={18} /> Database Health</h3>
                <div className="card-header-actions">
                    {db && (
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
                        <span>Failed to load database status</span>
                        <button className="btn btn-sm btn-secondary" onClick={fetchDbHealth}>
                            <RefreshCw size={14} /> Retry
                        </button>
                    </div>
                ) : (
                    <table className="data-table compact">
                        <tbody>
                            <tr>
                                <td className="detail-label">Latency</td>
                                <td>{db.latency_ms}ms</td>
                            </tr>
                            <tr>
                                <td className="detail-label">Connection Pool</td>
                                <td>{db.active_connections}/{db.pool_size} active</td>
                            </tr>
                            <tr>
                                <td className="detail-label">Available Connections</td>
                                <td>{db.available_connections}</td>
                            </tr>
                            <tr>
                                <td className="detail-label">Tables</td>
                                <td>{db.table_count}</td>
                            </tr>
                            <tr>
                                <td className="detail-label">Size</td>
                                <td>{db.database_size_mb > 0 ? `${db.database_size_mb.toFixed(1)} MB` : 'N/A'}</td>
                            </tr>
                            <tr>
                                <td className="detail-label">Last Check</td>
                                <td>{new Date(db.last_check).toLocaleTimeString()}</td>
                            </tr>
                        </tbody>
                    </table>
                )}
            </div>
        </div>
    );
}
