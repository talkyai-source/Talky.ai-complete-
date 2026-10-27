import { Building2 } from 'lucide-react';
import { api, type TenantListItem } from '../lib/api';
import { useAdminRead } from '../lib/use-admin-read';

const load = () => api.getTenants(undefined, undefined, 5, 0, false, 'minutes_used');
const valid = (rows: TenantListItem[]) => Array.isArray(rows) && rows.every(row =>
    row && typeof row.id === 'string' && typeof row.business_name === 'string'
    && typeof row.minutes_used === 'number' && Number.isFinite(row.minutes_used) && row.minutes_used >= 0);

export function TopTenantsList() {
    const { data: tenants, loading, error, refresh } = useAdminRead(load, 60000, valid);
    return <div className="card">
        <div className="card-header"><h3 className="card-title">Top Tenants</h3>
            <a className="btn btn-more" href="/tenants">View tenants</a></div>
        <div className="card-body">
            <p>Highest recorded minutes across non-archived tenants.</p>
            {loading && <p>Loading tenants…</p>}
            {error && <p role="alert">Tenant usage unavailable: {error}{tenants ? ' Showing the last retrieved ranking; it may be stale.' : ''}
                <button onClick={() => void refresh()}>Retry tenants</button></p>}
            {tenants?.length === 0 && !error && <p>No tenants yet.</p>}
            {tenants?.map(tenant => <div className="tenant-item" key={tenant.id}>
                <div className="tenant-left"><div className="tenant-icon"><Building2 /></div>
                    <div className="tenant-info"><span className="tenant-name">{tenant.business_name || 'Unnamed tenant'}</span>
                        <span className="tenant-calls">{tenant.minutes_used.toLocaleString()} min</span>
                    </div></div>
            </div>)}
        </div>
    </div>;
}
