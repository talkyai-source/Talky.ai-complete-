import { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { api, type IncidentItem } from '../lib/api';

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

export function Incidents() {
    const [incidents, setIncidents] = useState<IncidentItem[]>([]);
    const [loading, setLoading] = useState(true);
    // Track failure separately from "empty": a failed read is not "no incidents".
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        const fetchOnce = async () => {
            try {
                // Show only currently-open and recently-acknowledged incidents.
                const res = await api.getIncidents({
                    status: 'open',
                    page: 1,
                    page_size: 5,
                });
                if (cancelled) return;
                if (res.error) {
                    // Keep the last good list (flagged stale below) rather than blanking it.
                    setError(res.error.message || 'Failed to load incidents.');
                } else {
                    setIncidents(res.data?.items ?? []);
                    setError(null);
                }
            } catch (err) {
                if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load incidents.');
            } finally {
                if (!cancelled) setLoading(false);
            }
        };
        void fetchOnce();
        // Light poll — incidents update on a slow cadence.
        const id = window.setInterval(fetchOnce, 30_000);
        return () => {
            cancelled = true;
            window.clearInterval(id);
        };
    }, []);

    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title">Incidents</h3>
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
                        <button className="btn btn-alert">
                            <AlertTriangle size={12} />
                            {incident.severity === 'critical' ? 'Critical' : 'Alert'}
                        </button>
                    </div>
                ))}
            </div>
        </div>
    );
}
