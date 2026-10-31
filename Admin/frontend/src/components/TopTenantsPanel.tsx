import { Building2 } from 'lucide-react';
import { api, type AdminConnectorItem, type AdminConnectorListResponse } from '../lib/api';
import { useAdminRead } from '../lib/use-admin-read';

const load = () => api.getConnectors({ page: 1, page_size: 5 });
const valid = (result: AdminConnectorListResponse) => Boolean(result && Array.isArray(result.items)
    && typeof result.total === 'number' && Number.isFinite(result.total) && result.total >= 0
    && result.items.every(item => item && typeof item.id === 'string' && typeof item.status === 'string'
        && (item.name === null || typeof item.name === 'string')
        && typeof item.provider === 'string' && typeof item.type === 'string'));

function statusLabel(item: AdminConnectorItem): string {
    if (item.status === 'disconnected') return item.provider_revocation_status === 'unconfirmed'
        ? 'Disabled locally; provider revocation unconfirmed' : 'Disabled locally';
    if (item.refresh_state === 'uncertain') return 'Refresh outcome unconfirmed';
    if (item.refresh_state === 'in_flight') return 'Refresh pending';
    if (item.status === 'expired' || item.token_status === 'expired') return 'Token expired';
    if (item.token_status === 'expiring_soon') return 'Token expiring soon';
    if (item.status === 'active') return item.token_status === 'valid'
        ? 'Enabled; token recorded as valid' : 'Enabled; token state unknown';
    if (item.status === 'error') return 'Error recorded';
    if (item.status === 'pending') return 'Connection pending';
    return 'State unknown';
}

export function TopTenantsPanel() {
    const { data, loading, error, refresh } = useAdminRead(load, 60000, valid);
    return <div className="card">
        <div className="card-header"><h3 className="card-title">Connectors</h3>
            <a className="btn btn-more" href="/connectors">View connectors</a></div>
        <div className="card-body">
            {loading && <p>Loading connectors…</p>}
            {error && <p role="alert">Connectors unavailable: {error}{data ? ' Last retrieved states may be stale.' : ''}
                <button onClick={() => void refresh()}>Retry connectors</button></p>}
            {data && <p>Showing {data.items.length} of {data.total} connectors. Saved account state; live provider availability is separate.</p>}
            {data?.items.length === 0 && !error && <p>No connectors configured.</p>}
            {data?.items.map(item => <div className="connector-item" key={item.id}>
                <div className="connector-left"><div className="connector-icon blue"><Building2 /></div>
                    <span className="connector-name">{item.name || item.provider || item.type}</span></div>
                <span className="connector-status">{error ? 'Last retrieved: ' : ''}{statusLabel(item)}</span>
            </div>)}
        </div>
    </div>;
}
