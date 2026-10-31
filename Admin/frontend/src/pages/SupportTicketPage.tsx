import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { LifeBuoy, RefreshCw } from 'lucide-react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { SupportTicketControls, type TicketChanges } from '../components/SupportTicketControls';
import { SupportTicketTabs } from '../components/SupportTicketTabs';
import { SupportComposer } from '../components/SupportComposer';
import { formatStamp } from '../lib/support-format';
import { api } from '../lib/api';
import { useAuth } from '../lib/auth';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { useOperationOwner } from '../lib/use-operation-owner';
import { PRIORITY_LABELS, STATUS_LABELS, categoryLabel, isVersionConflict, validTicketDetail, type StaffTicketDetail } from '../lib/support-contracts';
import { deadlineState } from '../lib/support-deadlines';
import '../components/SupportConsole.css';

export function SupportTicketPage() {
    const generation = useAuthGeneration();
    const { id = '' } = useParams();
    return <SupportTicketContent key={`${generation}:${id}`} generation={generation} ticketId={id} />;
}

type PendingPatch = { request_id: string; version: number; changes: TicketChanges };

function SupportTicketContent({ generation, ticketId }: { generation: number; ticketId: string }) {
    const { user } = useAuth();
    const [ticket, setTicket] = useState<StaffTicketDetail | null>(null);
    const [loadedAt, setLoadedAt] = useState(0);
    const [loading, setLoading] = useState(true);
    const [readError, setReadError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [actionError, setActionError] = useState<string | null>(null);
    const [conflict, setConflict] = useState(false);
    const [notice, setNotice] = useState<string | null>(null);
    const [revision, setRevision] = useState(0);
    const pending = useRef<PendingPatch | null>(null);
    const captureOwner = useOperationOwner(generation, `${ticketId}:${revision}`);

    useEffect(() => {
        let current = true;
        const owns = captureOwner();
        setLoading(true);
        setReadError(null);
        void api.getSupportTicket(ticketId).then((response) => {
            if (!current || !owns()) return;
            if (response.error || !validTicketDetail(response.data)) {
                setReadError(response.error?.message ?? 'Could not load this ticket: incomplete or unexpected response. Reload to retry.');
                return;
            }
            setTicket(response.data);
            setLoadedAt(Date.now());
            setConflict(false);
            pending.current = null;
        }).catch(() => {
            if (current && owns()) setReadError('Could not load this ticket. Reload to retry.');
        }).finally(() => { if (current && owns()) setLoading(false); });
        return () => { current = false; };
    }, [ticketId, revision, captureOwner]);

    const reload = () => { setNotice(null); setActionError(null); setRevision((value) => value + 1); };

    async function save(changes: TicketChanges) {
        if (!ticket || saving || conflict) return;
        const owns = captureOwner();
        // A retry of the same decision reuses its request_id; a new decision gets a new one.
        const same = pending.current && pending.current.version === ticket.version
            && JSON.stringify(pending.current.changes) === JSON.stringify(changes);
        const patch: PendingPatch = same && pending.current ? pending.current
            : { request_id: crypto.randomUUID(), version: ticket.version, changes };
        pending.current = patch;
        setSaving(true);
        setActionError(null);
        setNotice(null);
        try {
            const response = await api.patchSupportTicket(ticketId, { request_id: patch.request_id, version: patch.version, ...patch.changes });
            if (!owns()) return;
            if (response.error) {
                if (isVersionConflict(response.error)) {
                    // Never overwrite: the draft is discarded and the saved record is re-read on demand.
                    pending.current = null;
                    setConflict(true);
                    return;
                }
                setActionError(`${response.error.message} Retry sends the same request; nothing different is sent.`);
                return;
            }
            if (!validTicketDetail(response.data)) {
                setActionError('The save could not be confirmed. Reload to see what was stored before deciding again.');
                return;
            }
            pending.current = null;
            setTicket(response.data);
            setLoadedAt(Date.now());
            setNotice('Saved.');
        } catch {
            if (owns()) setActionError('The save could not be confirmed. Reload to see what was stored before deciding again.');
        } finally {
            if (owns()) setSaving(false);
        }
    }

    const owns = () => captureOwner()();
    const state = ticket ? deadlineState(ticket.sla, ticket.status, loadedAt) : 'none';

    return (
        <div className="app-layout">
            <Sidebar />
            <main className="main-content">
                <Header />
                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon"><LifeBuoy /></div>
                        <div>
                            <h1 className="page-title">{ticket ? `${ticket.number} · ${ticket.subject}` : 'Support ticket'}</h1>
                            <p className="page-description"><Link to="/support">Back to the queue</Link></p>
                        </div>
                        <div className="page-header-actions">
                            <button type="button" className="btn btn-secondary" disabled={loading || saving} onClick={reload}><RefreshCw size={16} /> Reload</button>
                        </div>
                    </div>

                    {conflict && (
                        <div className="error-banner" role="alert">
                            This ticket was changed by someone else. Your change was not saved.{' '}
                            <button type="button" className="btn btn-secondary btn-sm" onClick={reload}>Reload</button>
                        </div>
                    )}
                    {readError && <div className="error-banner" role="alert">{readError}</div>}
                    {actionError && <div className="error-banner" role="alert">{actionError}</div>}
                    {notice && <p role="status">{notice}</p>}
                    {loading && !ticket && <p role="status">Loading ticket…</p>}

                    {ticket && (
                        <>
                            <div className="card support-ticket-head">
                                <dl>
                                    <div><dt>Status</dt><dd>{STATUS_LABELS[ticket.status]}</dd></div>
                                    <div><dt>Priority</dt><dd>{PRIORITY_LABELS[ticket.priority]}</dd></div>
                                    <div><dt>Category</dt><dd>{categoryLabel(ticket.category)}</dd></div>
                                    <div><dt>Company</dt><dd><Link to="/tenants" title="Opens the Tenants list">{ticket.tenant?.name || ticket.tenant?.id || '—'}</Link>{ticket.tenant?.plan ? ` · ${ticket.tenant.plan}` : ''}</dd></div>
                                    <div><dt>Requester</dt><dd>{ticket.requester.name || '—'}{ticket.requester.email ? ` · ${ticket.requester.email}` : ''}</dd></div>
                                    <div><dt>Created</dt><dd>{formatStamp(ticket.created_at)}</dd></div>
                                    <div><dt>Updated</dt><dd>{formatStamp(ticket.updated_at)}</dd></div>
                                    <div><dt>First response due</dt><dd className={`support-deadline ${state === 'at_risk' ? 'at-risk' : state}`}>{formatStamp(ticket.sla.first_response_due_at)}{ticket.sla.first_responded_at ? ' · answered' : ''}</dd></div>
                                    <div><dt>Resolution due</dt><dd className={`support-deadline ${state === 'at_risk' ? 'at-risk' : state}`}>{formatStamp(ticket.sla.resolution_due_at)}{ticket.sla.breached ? ' · breached' : ''}</dd></div>
                                    {ticket.related.call_id && <div><dt>Related call</dt><dd><Link to="/calls" title="Opens the Calls list">{ticket.related.call_id}</Link></dd></div>}
                                    {ticket.related.campaign_id && <div><dt>Related campaign</dt><dd>{ticket.related.campaign_id}</dd></div>}
                                    {ticket.csat && <div><dt>Rating</dt><dd>{ticket.csat.score} / 5{ticket.csat.comment ? ` · ${ticket.csat.comment}` : ''}</dd></div>}
                                </dl>
                            </div>
                            <SupportTicketControls key={ticket.version} ticket={ticket} currentUserId={user?.id ?? null}
                                busy={saving || conflict} onSave={(changes) => void save(changes)} />
                            <div className="card">
                                <div className="card-body">
                                    <SupportTicketTabs key={`${ticket.version}:${ticket.messages.length}`} ticket={ticket} />
                                </div>
                            </div>
                            <SupportComposer ticketId={ticketId} disabled={ticket.status === 'closed' || conflict} owns={owns}
                                onSent={() => { setNotice('Message saved. Reloading the ticket.'); reload(); }} />
                        </>
                    )}
                </div>
            </main>
        </div>
    );
}
