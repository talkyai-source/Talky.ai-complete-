import { useEffect, useState } from 'react';
import { Building2, Mail } from 'lucide-react';
import { api, type TenantListItem } from '../lib/api';

export function TopTenantsList() {
    const [tenants, setTenants] = useState<TenantListItem[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        const fetchOnce = async () => {
            try {
                const res = await api.getTenants();
                if (cancelled) return;
                const items = res.data ?? [];
                // Sort by minutes_used desc; cap at top 5 for the widget.
                items.sort((a, b) => (b.minutes_used ?? 0) - (a.minutes_used ?? 0));
                setTenants(items.slice(0, 5));
            } catch {
                if (!cancelled) setTenants([]);
            } finally {
                if (!cancelled) setLoading(false);
            }
        };
        void fetchOnce();
    }, []);

    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title">Top Tenants</h3>
                <button className="btn btn-more">More +</button>
            </div>
            <div className="card-body">
                {loading && tenants.length === 0 && (
                    <div className="tenant-item">
                        <span className="tenant-calls">Loading…</span>
                    </div>
                )}
                {!loading && tenants.length === 0 && (
                    <div className="tenant-item">
                        <span className="tenant-calls">No tenants yet.</span>
                    </div>
                )}
                {tenants.map((tenant) => (
                    <div className="tenant-item" key={tenant.id}>
                        <div className="tenant-left">
                            <div className="tenant-icon">
                                <Building2 />
                            </div>
                            <div className="tenant-info">
                                <span className="tenant-name">{tenant.business_name || '—'}</span>
                                <span className="tenant-calls">
                                    {(tenant.minutes_used ?? 0).toLocaleString()} min
                                    {tenant.user_count ? ` · ${tenant.user_count} users` : ''}
                                </span>
                            </div>
                        </div>
                        <button className="btn btn-alert">
                            <Mail size={12} />
                            Alert
                        </button>
                    </div>
                ))}
            </div>
        </div>
    );
}
