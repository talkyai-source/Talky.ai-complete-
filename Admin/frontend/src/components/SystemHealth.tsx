import { useState, useEffect, useCallback } from 'react';
import { CheckCircle, Clock, BarChart3, AlertCircle, Loader2 } from 'lucide-react';
import { api } from '../lib/api';
import type { SystemHealthItem } from '../lib/api';

export function SystemHealth() {
    const [healthItems, setHealthItems] = useState<SystemHealthItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const fetchHealth = useCallback(async () => {
        try {
            const response = await api.getSystemHealth();
            if (response.data) {
                setHealthItems(response.data.providers);
                setError(null);
            } else if (response.error) {
                setError(response.error.message);
            }
        } catch (err) {
            setError('Failed to fetch health status');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchHealth();

        // Auto-refresh every 60 seconds
        const interval = setInterval(fetchHealth, 60000);
        return () => clearInterval(interval);
    }, [fetchHealth]);

    const getStatusIcon = (status: string) => {
        switch (status) {
            case 'operational':
                return <CheckCircle />;
            case 'degraded':
                return <Clock />;
            case 'down':
                return <AlertCircle />;
            default:
                return <CheckCircle />;
        }
    };

    const getStatusClass = (status: string) => {
        switch (status) {
            case 'operational':
                return 'green';
            case 'degraded':
                return 'orange';
            case 'down':
                return 'red';
            default:
                return 'green';
        }
    };

    const getStatusLabel = (status: string) => {
        switch (status) {
            case 'operational':
                return 'Operational';
            case 'degraded':
                return 'Degraded';
            case 'down':
                return 'Down';
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

    if (error) {
        console.warn('SystemHealth error:', error);
    }

    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title">System Health</h3>
            </div>
            <div className="card-body">
                {healthItems.map((item, index) => (
                    <div className="health-item" key={index}>
                        <div className="health-left">
                            <div className={`health-status-icon ${getStatusClass(item.status)}`}>
                                {getStatusIcon(item.status)}
                            </div>
                            <div className="health-label">
                                <span>{item.name}: </span>
                                <span className={item.status === 'operational' ? 'operational' : 'degraded'}>
                                    {getStatusLabel(item.status)}
                                </span>
                            </div>
                        </div>
                        <div className="health-right">
                            <BarChart3 />
                            <span>{item.latency_display}</span>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}
