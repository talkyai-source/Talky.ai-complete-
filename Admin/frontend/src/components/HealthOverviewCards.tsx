import { useState, useEffect, useCallback } from 'react';
import { Clock, Cpu, HardDrive, Server, Loader2, RefreshCw } from 'lucide-react';
import { api } from '../lib/api';
import type { DetailedHealthResponse } from '../lib/api';

export function HealthOverviewCards() {
    const [health, setHealth] = useState<DetailedHealthResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const fetchHealth = useCallback(async () => {
        try {
            const response = await api.getDetailedHealth();
            if (response.data) {
                setHealth(response.data);
                setError(null);
            } else if (response.error) {
                setError(response.error.message);
            }
        } catch (err) {
            setError('Failed to fetch health data');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchHealth();
        // Auto-refresh every 30 seconds
        const interval = setInterval(fetchHealth, 30000);
        return () => clearInterval(interval);
    }, [fetchHealth]);

    if (loading) {
        return (
            <div className="health-overview-cards">
                <div className="loading-state">
                    <Loader2 className="animate-spin" size={24} />
                    <span>Loading system metrics...</span>
                </div>
            </div>
        );
    }

    if (error || !health) {
        return (
            <div className="health-overview-cards">
                <div className="error-state">
                    <span>Unable to load system metrics</span>
                    <button className="btn btn-sm btn-secondary" onClick={fetchHealth}>
                        <RefreshCw size={14} /> Retry
                    </button>
                </div>
            </div>
        );
    }

    const cards = [
        {
            icon: <Clock size={24} />,
            label: 'Uptime',
            value: health.uptime_display,
            subtext: `${Math.floor(health.uptime_seconds / 3600)} hours`,
            color: 'var(--accent-green)'
        },
        {
            icon: <HardDrive size={24} />,
            label: 'Memory Usage',
            value: `${(health.memory_usage_mb / 1024).toFixed(1)} GB`,
            subtext: `${health.memory_percent.toFixed(1)}% of ${(health.memory_total_mb / 1024).toFixed(1)} GB`,
            color: health.memory_percent > 80 ? 'var(--accent-red)' : 'var(--accent-blue)'
        },
        {
            icon: <Cpu size={24} />,
            label: 'CPU Usage',
            value: `${health.cpu_usage_percent.toFixed(1)}%`,
            subtext: health.os_info,
            color: health.cpu_usage_percent > 70 ? 'var(--accent-orange)' : 'var(--accent-green)'
        },
        {
            icon: <Server size={24} />,
            label: 'Version',
            value: health.version,
            subtext: `Python ${health.python_version}`,
            color: 'var(--accent-purple)'
        }
    ];

    return (
        <div className="health-overview-cards">
            {cards.map((card, index) => (
                <div className="health-card" key={index}>
                    <div className="health-card-icon" style={{ color: card.color }}>
                        {card.icon}
                    </div>
                    <div className="health-card-content">
                        <span className="health-card-label">{card.label}</span>
                        <span className="health-card-value">{card.value}</span>
                        <span className="health-card-subtext">{card.subtext}</span>
                    </div>
                </div>
            ))}
        </div>
    );
}
