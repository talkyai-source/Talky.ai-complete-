import { Phone, AlertCircle, Users, AlertTriangle, Loader2 } from 'lucide-react';
import { api } from '../lib/api';
import { useAdminRead } from '../lib/use-admin-read';
import { validStats } from '../lib/health-contracts';
import { metric, observedAt } from '../lib/health-display';

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

const loadStatsGrid = () => api.getDashboardStats();

export function StatsGrid() {
    const { data: stats, loading, error } = useAdminRead(loadStatsGrid, 30000, validStats);

    // A failed read is "unavailable", never zero: zeros read as an all-clear.
    const UNAVAILABLE = '\u2014';

    return (
        <>
        {error && (
            <div className="error-banner inline" role="alert">
                <p>
                    Dashboard stats unavailable: {error}
                    {stats ? ' Showing last known values, which may be stale.' : ''}
                </p>
            </div>
        )}
        <div className="stats-grid">
            <StatCard
                icon={<Phone />}
                label="Active Calls:"
                value={stats?.active_calls ?? UNAVAILABLE}
                iconColor="orange"
                loading={loading}
            />
            <StatCard
                icon={<AlertCircle />}
                label="Failed Call Rate (24h):"
                value={stats?.failed_call_rate_24h ?? (stats?.total_calls_24h === 0 && stats.failed_call_rate_24h === null ? 'No calls' : 'Unavailable')}
                iconColor="red"
                loading={loading}
            />
            <StatCard
                icon={<Users />}
                label="Active Tenants:"
                value={stats?.active_tenants ?? UNAVAILABLE}
                iconColor="green"
                loading={loading}
            />
            <StatCard
                icon={<AlertTriangle />}
                label="Failed Calls (24h):"
                value={stats?.failed_calls_24h ?? UNAVAILABLE}
                iconColor="teal"
                loading={loading}
            />
        </div>
        {stats && <p className="text-muted">{metric(stats.total_calls_24h)} calls created in the last 24 hours; failed/error states only. No-answer calls are excluded from failures. Active tenants have active accounts and active or trialing subscriptions. Observed {observedAt(stats.observed_at)}.</p>}
        </>
    );
}
