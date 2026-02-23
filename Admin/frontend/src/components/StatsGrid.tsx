import { useState, useEffect, useCallback } from 'react';
import { Phone, AlertCircle, Users, AlertTriangle, Loader2 } from 'lucide-react';
import { api } from '../lib/api';
import type { DashboardStats } from '../lib/api';

interface StatCardProps {
    icon: React.ReactNode;
    label: string;
    value: string | number;
    iconColor: 'orange' | 'red' | 'green' | 'teal';
    loading?: boolean;
}

function StatCard({ icon, label, value, iconColor, loading }: StatCardProps) {
    return (
        <div className="stat-card">
            <div className={`stat-icon ${iconColor}`}>
                {icon}
            </div>
            <div className="stat-content">
                <span className="stat-label">{label}</span>
                <span className={`stat-value ${iconColor}`}>
                    {loading ? <Loader2 className="animate-spin" size={16} /> : value}
                </span>
            </div>
        </div>
    );
}

export function StatsGrid() {
    const [stats, setStats] = useState<DashboardStats | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const fetchStats = useCallback(async () => {
        try {
            const response = await api.getDashboardStats();
            if (response.data) {
                setStats(response.data);
                setError(null);
            } else if (response.error) {
                setError(response.error.message);
            }
        } catch (err) {
            setError('Failed to fetch stats');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchStats();

        // Auto-refresh every 30 seconds
        const interval = setInterval(fetchStats, 30000);
        return () => clearInterval(interval);
    }, [fetchStats]);

    if (error) {
        console.warn('StatsGrid error:', error);
    }

    return (
        <div className="stats-grid">
            <StatCard
                icon={<Phone />}
                label="Active Calls:"
                value={stats?.active_calls ?? 0}
                iconColor="orange"
                loading={loading}
            />
            <StatCard
                icon={<AlertCircle />}
                label="Error Rate (24h):"
                value={stats?.error_rate_24h ?? '0%'}
                iconColor="red"
                loading={loading}
            />
            <StatCard
                icon={<Users />}
                label="Active Tenants:"
                value={stats?.active_tenants ?? 0}
                iconColor="green"
                loading={loading}
            />
            <StatCard
                icon={<AlertTriangle />}
                label="API Errors (24h):"
                value={stats?.api_errors_24h ?? 0}
                iconColor="teal"
                loading={loading}
            />
        </div>
    );
}
