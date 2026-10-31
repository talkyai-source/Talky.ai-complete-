import { CheckCircle, Clock, BarChart3, AlertCircle, Loader2 } from 'lucide-react';
import { api } from '../lib/api';
import { useAdminRead } from '../lib/use-admin-read';
import { validSystemHealth } from '../lib/health-contracts';
import { observedAt } from '../lib/health-display';

const loadSystemHealth = () => api.getSystemHealth();

export function SystemHealth() {
    const { data, loading, error } = useAdminRead(loadSystemHealth, 60000, validSystemHealth);
    const healthItems = data?.providers ?? [];

    const getStatusIcon = (status: string) => {
        switch (status) {
            case 'operational':
            case 'observed_success':
                return <CheckCircle />;
            case 'degraded':
                return <Clock />;
            case 'down':
            case 'observed_failure':
                return <AlertCircle />;
            default:
                return <Clock />;
        }
    };

    const getStatusClass = (status: string) => {
        switch (status) {
            case 'operational':
            case 'observed_success':
                return 'green';
            case 'degraded':
            case 'observed_failure':
                return 'orange';
            case 'down':
                return 'red';
            default:
                // Unrecognised status: neutral, never the healthy colour.
                return 'gray';
        }
    };

    const getStatusLabel = (status: string) => {
        switch (status) {
            case 'operational':
                return 'Operational';
            case 'observed_success':
                return 'Last observed request succeeded';
            case 'observed_failure':
                return 'Last observed request failed';
            case 'degraded':
                return 'Degraded';
            case 'down':
                return 'Down';
            case 'unknown':
                return 'Not measured';
            default:
                return status;
        }
    };

    if (loading) {
        return (
            <div className="card">
                <div className="card-header">
                    <h3 className="card-title">System Health</h3>
                </div>
                <div className="card-body" style={{ display: 'flex', justifyContent: 'center', padding: '2rem' }}>
                    <Loader2 className="animate-spin" size={24} />
                </div>
            </div>
        );
    }

    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title">System Health</h3>
            </div>
            <div className="card-body">
                <p className="text-muted">Voice provider results show the latest attempt per observed engine and credential in the last 10 minutes, up to 128 scopes. They do not represent every configured account or a platform success rate.</p>
                {error && (
                    <div className="error-banner inline" role="alert">
                        <p>
                            System health unavailable: {error}
                            {healthItems.length > 0 ? ' Showing last known status, which may be stale.' : ''}
                        </p>
                    </div>
                )}
                {healthItems.map((item) => (
                    <div className="health-item" key={item.name}>
                        <div className="health-left">
                            <div className={`health-status-icon ${error ? 'gray' : getStatusClass(item.status)}`}>
                                {getStatusIcon(item.status)}
                            </div>
                            <div className="health-label">
                                <span>{item.name}: </span>
                                <span className={!error && item.status === 'operational' ? 'operational' : 'text-muted'}>
                                    {error ? 'Last observed: ' : ''}{getStatusLabel(item.status)}
                                </span>
                                <p className="text-muted">{item.scope}</p>
                                {item.detail && <p>{item.detail}</p>}
                                <small>{observedAt(item.last_check)}</small>
                            </div>
                        </div>
                        <div className="health-right">
                            <BarChart3 />
                            <span>{item.latency_ms === null ? 'Latency unavailable' : item.latency_display}</span>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}
