import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { AlertTriangle, RefreshCw, X } from 'lucide-react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { api, type IncidentItem, type IncidentsParams } from '../lib/api';
import { useAdminRead } from '../lib/use-admin-read';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { useDialogFocus } from '../lib/use-dialog-focus';
import { observedAt } from '../lib/health-display';
import { validIncident, validIncidentList, validIncidentTransitionReceipt } from '../lib/incident-contracts';
import './IncidentsPage.css';

export function IncidentsPage() {
    const generation = useAuthGeneration();
    return <IncidentManagement key={generation} generation={generation} />;
}

function IncidentManagement({ generation }: { generation: number }) {
    const [status, setStatus] = useState<IncidentsParams['status']>('open');
    const [severity, setSeverity] = useState<IncidentsParams['severity']>();
    const [page, setPage] = useState(1);
    const [revision, setRevision] = useState(0);
    const [selected, setSelected] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const refreshButton = useRef<HTMLButtonElement>(null);
    const filterChanged = () => { setPage(1); setSelected(null); };
    return <div className="app-layout">
        <Sidebar />
        <main className="main-content"><Header />
            <div className="dashboard-content">
                <div className="page-header"><AlertTriangle /><div>
                    <h1 className="page-title">Incidents</h1>
                    <p className="page-description">Review recorded incidents, acknowledge ownership and record resolution.</p>
                </div></div>
                {notice && <div role="status">{notice} <button onClick={() => setNotice(null)}>Dismiss</button></div>}
                <div className="card"><div className="card-header">
                    <label>Status <select aria-label="Incident status" value={status ?? ''} onChange={(e) => { setStatus((e.target.value || undefined) as IncidentsParams['status']); filterChanged(); }}>
                        <option value="">All statuses</option><option value="open">Open</option><option value="acknowledged">Acknowledged</option><option value="resolved">Resolved</option>
                    </select></label>
                    <label>Severity <select aria-label="Incident severity" value={severity ?? ''} onChange={(e) => { setSeverity((e.target.value || undefined) as IncidentsParams['severity']); filterChanged(); }}>
                        <option value="">All severities</option><option value="critical">Critical</option><option value="warning">Warning</option><option value="info">Info</option>
                    </select></label>
                    <button ref={refreshButton} className="btn btn-secondary" onClick={() => setRevision((v) => v + 1)}><RefreshCw size={14} /> Refresh list</button>
                </div>
                    <IncidentResults key={`${status}:${severity}:${page}:${revision}`} params={{ status, severity, page, page_size: 20 }} onSelect={setSelected} onPage={setPage} />
                </div>
                {selected && <IncidentDetail key={selected} id={selected} generation={generation} returnFallback={refreshButton} onClose={() => setSelected(null)}
                    onChanged={(message) => { setNotice(message); setRevision((v) => v + 1); }} />}
            </div>
        </main>
    </div>;
}

function IncidentResults({ params, onSelect, onPage }: { params: IncidentsParams; onSelect: (id: string) => void; onPage: (page: number) => void }) {
    const { status, severity, page, page_size } = params;
    const load = useCallback(() => api.getIncidents({ status, severity, page, page_size }), [status, severity, page, page_size]);
    const { data, error, loading } = useAdminRead(load, 30000, validIncidentList);
    return <div className="card-body">
        {loading && <p>Loading incidents…</p>}
        {error && <p role="alert">Incident records unavailable: {error}{data ? ' Last retrieved records may be stale; refresh before acting.' : ''}</p>}
        {data && <>
            <p>{data.open_count} open across all severities; {data.critical_count} critical and open. {data.total} matching records.</p>
            {!data.items.length && !error && <p>No incidents match these filters.</p>}
            <table className="data-table"><thead><tr><th>Incident</th><th>Severity</th><th>Status</th><th>Triggered</th><th>Details</th></tr></thead>
                <tbody>{data.items.map((item) => <tr key={item.id}>
                    <td>{item.title}</td><td>{item.severity}</td><td>{item.status}</td><td>{observedAt(item.triggered_at)}</td>
                    <td><button className="btn btn-secondary" disabled={Boolean(error)} onClick={() => onSelect(item.id)} aria-label={`View incident ${item.title}`}>View</button></td>
                </tr>)}</tbody>
            </table>
            <div className="pagination">
                <button disabled={data.page <= 1} onClick={() => onPage(data.page - 1)}>Previous</button>
                <span>Page {data.page} of {Math.max(1, Math.ceil(data.total / data.page_size))}</span>
                <button disabled={data.page * data.page_size >= data.total} onClick={() => onPage(data.page + 1)}>Next</button>
            </div>
        </>}
    </div>;
}

function IncidentDetail({ id, generation, returnFallback, onClose, onChanged }: { id: string; generation: number;
    returnFallback: RefObject<HTMLButtonElement | null>; onClose: () => void; onChanged: (message: string) => void }) {
    const dialog = useRef<HTMLDivElement>(null);
    const mounted = useRef(false), sequence = useRef(0), pending = useRef(false);
    const [item, setItem] = useState<IncidentItem | null>(null);
    const [readError, setReadError] = useState<string | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [needsRead, setNeedsRead] = useState(false);
    const [confirmResolve, setConfirmResolve] = useState(false);
    const owns = useCallback(() => mounted.current && generation === api.getAuthGeneration(), [generation]);
    const load = useCallback(async () => {
        if (!owns()) return false;
        const request = ++sequence.current;
        setLoading(true);
        try {
            const result = await api.getIncident(id);
            if (!owns() || sequence.current !== request) return false;
            if (result.error || !result.data || result.data.id !== id) {
                setReadError(result.error?.message ?? 'No matching incident record was returned.'); return false;
            }
            if (!validIncident(result.data)) {
                setReadError('The server returned an incomplete or unexpected incident record.'); return false;
            }
            setItem(result.data); setReadError(null); setNeedsRead(false);
            setActionError(null); setConfirmResolve(false); return true;
        } catch (error) {
            if (owns() && sequence.current === request) setReadError(error instanceof Error ? error.message : 'Incident unavailable.');
            return false;
        } finally { if (owns() && sequence.current === request) setLoading(false); }
    }, [id, owns]);
    useEffect(() => {
        mounted.current = true; void load();
        return () => { mounted.current = false; sequence.current += 1; };
    }, [load]);
    useDialogFocus(true, dialog, onClose, busy, returnFallback);

    const transition = async (target: 'acknowledged' | 'resolved') => {
        if (!owns() || pending.current || loading || readError || needsRead || !item || item.status === 'resolved') return;
        if (target === 'acknowledged' && item.status !== 'open') return;
        pending.current = true; setBusy(true); setActionError(null); sequence.current += 1;
        try {
            const result = target === 'acknowledged' ? await api.acknowledgeIncident(id) : await api.resolveIncident(id);
            if (!owns()) return;
            if (result.error || !validIncidentTransitionReceipt(result.data, id, target)) {
                setActionError(`${result.error?.message ?? 'The server did not return a matching receipt.'} Refresh the record to confirm its current state.`);
                setNeedsRead(true); return;
            }
            const receipt = result.data;
            setItem((current) => current && ({ ...current, status: receipt.status,
                acknowledged_at: receipt.acknowledged_at, acknowledged_by: receipt.acknowledged_by,
                resolved_at: receipt.resolved_at, resolved_by: receipt.resolved_by }));
            setConfirmResolve(false);
            onChanged(`Incident ${target}. The server confirmed the saved state.`);
            await load();
        } catch (error) {
            if (owns()) { setActionError(`${error instanceof Error ? error.message : 'The result is unknown.'} Refresh the record before another action.`); setNeedsRead(true); }
        } finally { if (owns()) { pending.current = false; setBusy(false); } }
    };

    return <div className="modal-overlay"><div className="modal-content incidents-dialog" role="dialog" aria-modal="true" aria-labelledby="incident-detail-title" ref={dialog} tabIndex={-1}>
        <div className="modal-header"><h2 id="incident-detail-title">Incident details</h2><button aria-label="Close incident" disabled={busy} onClick={onClose}><X size={18} /></button></div>
        <div className="modal-body">
            {loading && <p>Loading current record…</p>}
            {readError && <p role="alert">{readError}{item ? ' Showing the last confirmed record; its state may have changed since.' : ''}</p>}
            {actionError && <p role="alert">{actionError}</p>}
            {item && <>
                <h3>{item.title}</h3><p>{item.description || 'No description recorded.'}</p>
                <dl><dt>Severity</dt><dd>{item.severity}</dd><dt>Operator status</dt><dd>{item.status}</dd>
                    <dt>Triggered</dt><dd>{observedAt(item.triggered_at)}</dd>
                    <dt>Acknowledged</dt><dd>{item.acknowledged_at ? `${observedAt(item.acknowledged_at)} by ${item.acknowledged_by}` : 'Not recorded'}</dd>
                    <dt>Resolved</dt><dd>{item.resolved_at ? `${observedAt(item.resolved_at)} by ${item.resolved_by}` : 'Not recorded'}</dd>
                </dl>
                {item.source_system && <div>
                    <p>Monitoring source: {item.source_system}</p>
                    <p>Recorded episode state: {item.source_status === 'recovered' ? 'Recovery reported' : item.source_status === 'firing' ? 'Alert firing' : 'Unknown'}.</p>
                    <p>Last delivery received: {observedAt(item.source_last_received_at)}.</p>
                    {item.source_recovered_at && <p>Source recovery: {observedAt(item.source_recovered_at)}.</p>}
                    <p>Monitoring recovery and operator resolution are recorded separately. Delivery time does not establish current system health.</p>
                </div>}
                {confirmResolve && <p>Resolve “{item.title}” after verifying that the underlying problem is addressed. This records resolution; it does not repair the system.</p>}
            </>}
        </div>
        <div className="modal-footer">
            <button className="btn btn-secondary" disabled={busy || loading} onClick={() => void load()}>Refresh record</button>
            {item?.status === 'open' && <button className="btn btn-primary" disabled={busy || loading || Boolean(readError) || needsRead} onClick={() => void transition('acknowledged')}>Acknowledge</button>}
            {item && item.status !== 'resolved' && <button className="btn btn-primary" disabled={busy || loading || Boolean(readError) || needsRead} onClick={() => confirmResolve ? void transition('resolved') : setConfirmResolve(true)}>{confirmResolve ? 'Confirm resolution' : 'Resolve'}</button>}
            {confirmResolve && <button disabled={busy} onClick={() => setConfirmResolve(false)}>Cancel resolution</button>}
        </div>
    </div></div>;
}
