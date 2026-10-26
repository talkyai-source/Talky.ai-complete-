import { AlertTriangle } from 'lucide-react';
import { api } from '../lib/api';
import { useAdminRead } from '../lib/use-admin-read';
import { validIncidentList } from '../lib/incident-contracts';

function relativeTime(iso: string): string {
    const ts = Date.parse(iso);
    if (!Number.isFinite(ts)) return '—';
    const sec = Math.floor((Date.now() - ts) / 1000);
    if (sec < 60) return 'just now';
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min} min ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr} hour${hr === 1 ? '' : 's'} ago`;
    const d = Math.floor(hr / 24);
    if (d < 7) return `${d} day${d === 1 ? '' : 's'} ago`;
    return new Date(ts).toLocaleDateString();
}

const loadOpenIncidents = () => api.getIncidents({ status: 'open', page: 1, page_size: 5 });

export function Incidents() {
    const { data, loading, error } = useAdminRead(loadOpenIncidents, 30000, validIncidentList);
    const incidents = data?.items ?? [];

    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title">Incidents</h3>
                <a href="/incidents">Manage incidents</a>
            </div>
            <div className="card-body">
                {loading && incidents.length === 0 && (
                    <div className="incident-item">
                        <span className="incident-time">Loading…</span>
                    </div>
                )}
                {error && (
                    <div className="error-banner inline" role="alert">
                        <p>
                            Incidents unavailable: {error}
                            {incidents.length > 0 ? ' Showing last known incidents, which may be stale.' : ' Open incidents may exist.'}
                        </p>
                    </div>
                )}
                {!loading && !error && incidents.length === 0 && (
                    <div className="incident-item">
                        <span className="incident-time">No open incidents.</span>
                    </div>
                )}
                {incidents.map((incident) => (
                    <div className="incident-item" key={incident.id}>
                        <div className="incident-left">
                            <div className="incident-icon">
                                <AlertTriangle />
                            </div>
                            <div className="incident-info">
                                <span className="incident-title">{incident.title}</span>
                                <span className="incident-time">{relativeTime(incident.triggered_at)}</span>
                            </div>
                        </div>
                        <span className="badge badge-outline">
                            <AlertTriangle size={12} />
                            {incident.severity}
                        </span>
                    </div>
                ))}
            </div>
        </div>
    );
}
