import { useEffect, useState } from 'react';
import {
    Building2,
    Calendar,
    Database,
    Mail,
    CheckCircle,
    AlertTriangle,
    RefreshCw,
} from 'lucide-react';
import { api, type AdminConnectorItem } from '../lib/api';

type StatusBucket = 'connected' | 'error' | 'refreshing';

function bucketStatus(s: string): StatusBucket {
    if (s === 'active') return 'connected';
    if (s === 'pending' || s === 'refreshing') return 'refreshing';
    return 'error';
}

function statusLabel(c: AdminConnectorItem): string {
    const b = bucketStatus(c.status);
    if (b === 'connected') return 'Connected';
    if (b === 'refreshing') return 'Refreshing…';
    if (c.status === 'expired') return 'Token Expired';
    if (c.token_status === 'expired') return 'Token Expired';
    if (c.status === 'disconnected') return 'Disconnected';
    return 'Auth Error';
}

function iconForType(type: string) {
    if (type === 'calendar') return <Calendar />;
    if (type === 'crm') return <Database />;
    if (type === 'email') return <Mail />;
    return <Building2 />;
}

function colorForType(type: string): 'blue' | 'orange' | 'green' {
    if (type === 'calendar') return 'orange';
    if (type === 'crm') return 'green';
    return 'blue';
}

function StatusIcon({ status }: { status: StatusBucket }) {
    if (status === 'connected') return <CheckCircle size={14} />;
    if (status === 'error') return <AlertTriangle size={14} />;
    return <RefreshCw size={14} />;
}

export function TopTenantsPanel() {
    const [connectors, setConnectors] = useState<AdminConnectorItem[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        const fetchOnce = async () => {
            try {
                const res = await api.getConnectors({ page: 1, page_size: 5 });
                if (cancelled) return;
                setConnectors(res.data?.items ?? []);
            } catch {
                if (!cancelled) setConnectors([]);
            } finally {
                if (!cancelled) setLoading(false);
            }
        };
        void fetchOnce();
        const id = window.setInterval(fetchOnce, 60_000);
        return () => {
            cancelled = true;
            window.clearInterval(id);
        };
    }, []);

    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title">Connectors</h3>
            </div>
            <div className="card-body">
                {loading && connectors.length === 0 && (
                    <div className="connector-item">
                        <span className="connector-name">Loading…</span>
                    </div>
                )}
                {!loading && connectors.length === 0 && (
                    <div className="connector-item">
                        <span className="connector-name">No connectors configured.</span>
                    </div>
                )}
                {connectors.map((c) => {
                    const bucket = bucketStatus(c.status);
                    return (
                        <div className="connector-item" key={c.id}>
                            <div className="connector-left">
                                <div className={`connector-icon ${colorForType(c.type)}`}>
                                    {iconForType(c.type)}
                                </div>
                                <span className="connector-name">
                                    {c.name || c.provider || c.type}
                                </span>
                            </div>
                            <div className={`connector-status ${bucket}`}>
                                <StatusIcon status={bucket} />
                                <span>{statusLabel(c)}</span>
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
