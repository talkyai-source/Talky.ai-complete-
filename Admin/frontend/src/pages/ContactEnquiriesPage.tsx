import { useEffect, useRef, useState } from 'react';
import { Inbox, RefreshCw } from 'lucide-react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { api } from '../lib/api';
import type { ContactEnquiriesResponse, ContactEnquiry } from '../lib/api';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { useOperationOwner } from '../lib/use-operation-owner';

function validEnquiries(value: ContactEnquiriesResponse): boolean {
    return Boolean(value && Array.isArray(value.items)
        && [value.total, value.offset].every(number => Number.isSafeInteger(number) && number >= 0)
        && Number.isSafeInteger(value.limit) && value.limit > 0
        && value.items.every(item => item && typeof item.id === 'string' && item.id.trim()
            && [item.name, item.email, item.company, item.message, item.created_at].every(text => typeof text === 'string')
            && ['new', 'handled'].includes(item.status)
            && Number.isSafeInteger(item.revision) && item.revision >= 0
            && (item.handled_at === null || typeof item.handled_at === 'string')));
}

type UnconfirmedChange = { id: string; revision: number };

export function ContactEnquiriesPage() {
    const generation = useAuthGeneration();
    return <ContactEnquiriesContent key={generation} generation={generation} />;
}

function ContactEnquiriesContent({ generation }: { generation: number }) {
    const [status, setStatus] = useState<'' | 'new' | 'handled'>('new');
    const [offset, setOffset] = useState(0);
    const [revision, setRevision] = useState(0);
    const [result, setResult] = useState<ContactEnquiriesResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [readError, setReadError] = useState<string | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [savingId, setSavingId] = useState<string | null>(null);
    const [unconfirmed, setUnconfirmed] = useState<UnconfirmedChange | null>(null);
    const [reconciling, setReconciling] = useState(false);
    const unconfirmedRef = useRef<UnconfirmedChange | null>(null);
    const saving = useRef(false);
    const recoveryHeading = useRef<HTMLHeadingElement>(null);
    const captureOwner = useOperationOwner(generation, `${status}:${offset}:${revision}`);

    useEffect(() => {
        let current = true;
        const owns = captureOwner();
        setLoading(true);
        setReadError(null);
        setResult(null);
        void api.getContactEnquiries(status, offset).then((response) => {
            if (!current || !owns()) return;
            if (response.error || !response.data || !validEnquiries(response.data)) {
                setReadError(response.error?.message || 'Could not load enquiries: incomplete or unexpected response. Please retry.');
            } else if (offset > 0 && response.data.items.length === 0) {
                setOffset(Math.max(0, offset - 50));
            } else {
                setResult(response.data);
            }
        }).catch(() => {
            if (current && owns()) setReadError('Could not load enquiries. Please retry.');
        }).finally(() => {
            if (current && owns()) setLoading(false);
        });
        return () => { current = false; };
    }, [status, offset, revision, captureOwner]);

    async function changeStatus(enquiry: ContactEnquiry, trigger?: HTMLButtonElement) {
        const owns = captureOwner();
        if (!owns() || saving.current || loading || readError || unconfirmedRef.current) return;
        const ownedFocus = Boolean(trigger && document.activeElement === trigger);
        saving.current = true;
        setSavingId(enquiry.id);
        setActionError(null);
        setNotice(null);
        const nextStatus = enquiry.status === 'new' ? 'handled' : 'new';
        const requireReview = () => {
            const review = { id: enquiry.id, revision: enquiry.revision };
            unconfirmedRef.current = review;
            setUnconfirmed(review);
        };
        try {
            const response = await api.updateContactEnquiry(enquiry.id, nextStatus, enquiry.revision);
            if (!owns()) return;
            if (response.error || response.data?.id !== enquiry.id || response.data.status !== nextStatus
                || response.data.revision !== enquiry.revision + 1) {
                requireReview();
                setActionError(response.error?.message || 'The update could not be confirmed. Refresh before retrying.');
                return;
            }
            setNotice(nextStatus === 'handled' ? 'Enquiry marked as handled.' : 'Enquiry reopened.');
            // The filtered card is about to disappear. Keep keyboard recovery
            // connected, without taking focus from a control chosen meanwhile.
            if (ownedFocus && (document.activeElement === trigger || document.activeElement === document.body)) {
                recoveryHeading.current?.focus();
            }
            setRevision((value) => value + 1);
        } catch {
            if (owns()) {
                requireReview();
                setActionError('The update could not be confirmed. Refresh before retrying.');
            }
        } finally {
            if (owns()) {
                saving.current = false;
                setSavingId(null);
            }
        }
    }

    async function refresh(trigger?: HTMLButtonElement) {
        const owns = captureOwner();
        if (!owns() || saving.current || loading) return;
        // The focused Refresh control becomes disabled during this read.
        // Move first, so native browsers keep a connected keyboard location.
        if (trigger && document.activeElement === trigger) recoveryHeading.current?.focus();
        const review = unconfirmedRef.current;
        if (!review) { setRevision(value => value + 1); return; }
        saving.current = true;
        setReconciling(true);
        try {
            // The filtered/paginated list can omit a saved result. Observe this
            // exact reference without a status filter before another decision.
            const response = await api.getContactEnquiry(review.id);
            if (!owns()) return;
            const saved = response.data?.items?.[0];
            if (response.error || !response.data || !validEnquiries(response.data)
                || response.data.total !== 1 || response.data.items.length !== 1
                || response.data.offset !== 0 || response.data.limit !== 1
                || saved?.id !== review.id || saved.revision < review.revision) {
                setActionError(response.error?.message || 'Saved status is unavailable. Refresh again to check this exact enquiry; no change will be resent.');
                return;
            }
            unconfirmedRef.current = null;
            setUnconfirmed(null);
            setNotice(`Current saved status is ${saved.status === 'handled' ? 'Handled' : 'New'} (revision ${saved.revision}). Review the refreshed record before another decision. This read does not confirm the earlier request.`);
            setRevision(value => value + 1);
        } catch {
            if (owns()) setActionError('Saved status is unavailable. Refresh again to check this exact enquiry; no change will be resent.');
        } finally {
            if (owns()) {
                saving.current = false;
                setReconciling(false);
            }
        }
    }

    return (
        <div className="app-layout">
            <Sidebar />
            <main className="main-content">
                <Header />
                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon"><Inbox /></div>
                        <div>
                            <h1 className="page-title" ref={recoveryHeading} tabIndex={-1}>Website Enquiries</h1>
                            <p className="page-description">Saved contact requests. Follow up using your team’s communication tools, then mark as handled.</p>
                        </div>
                        <div className="page-header-actions">
                            <button className="btn btn-secondary" disabled={loading || Boolean(savingId) || reconciling} onClick={(event) => void refresh(event?.currentTarget)}>
                                <RefreshCw size={16} /> Refresh
                            </button>
                        </div>
                    </div>
                    <div className="enquiry-toolbar">
                        <label htmlFor="enquiry-status">Status </label>
                        <select id="enquiry-status" value={status} disabled={Boolean(savingId) || reconciling} onChange={(event) => {
                            setStatus(event.target.value as typeof status);
                            setOffset(0);
                            setNotice(null);
                        }}>
                            <option value="new">New</option>
                            <option value="handled">Handled</option>
                            <option value="">All</option>
                        </select>
                    </div>
                    {readError && <div className="error-banner" role="alert">{readError}</div>}
                    {actionError && <div className="error-banner" role="alert">{actionError}</div>}
                    {unconfirmed && <p role="status">Refresh to inspect the saved status of enquiry {unconfirmed.id} before another change. No update will be sent automatically.</p>}
                    {notice && <p role="status">{notice}</p>}
                    {loading && <p role="status">Loading enquiries…</p>}
                    {!loading && result?.items.length === 0 && <p>No enquiries match this status.</p>}
                    {!loading && result && <>
                        <div className="enquiry-list">
                            {result.items.map((enquiry) => (
                                <article className="card enquiry-card" key={enquiry.id}>
                                    <div className="card-header">
                                        <h2 className="card-title">{enquiry.name}</h2>
                                        <span>{enquiry.status === 'handled' ? 'Handled' : 'New'}</span>
                                    </div>
                                    <div className="card-body">
                                        <dl>
                                            <div><dt>Email</dt><dd>{enquiry.email}</dd></div>
                                            {enquiry.company && <div><dt>Company</dt><dd>{enquiry.company}</dd></div>}
                                            <div><dt>Received</dt><dd>{new Date(enquiry.created_at).toLocaleString()}</dd></div>
                                            <div><dt>Reference</dt><dd>{enquiry.id}</dd></div>
                                            {enquiry.handled_at && <div><dt>Handled</dt><dd>{new Date(enquiry.handled_at).toLocaleString()}</dd></div>}
                                        </dl>
                                        <p className="enquiry-message">{enquiry.message}</p>
                                        <button className="btn btn-secondary" disabled={Boolean(savingId) || Boolean(unconfirmed) || reconciling} onClick={(event) => void changeStatus(enquiry, event?.currentTarget)}>
                                            {savingId === enquiry.id ? 'Saving…' : enquiry.status === 'new' ? 'Mark handled' : 'Reopen'}
                                        </button>
                                    </div>
                                </article>
                            ))}
                        </div>
                        <div className="enquiry-toolbar">
                            <button className="btn btn-secondary" disabled={offset === 0 || Boolean(savingId) || reconciling} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</button>
                            <span>{result.total === 0 ? '0' : `${offset + 1}–${offset + result.items.length}`} of {result.total}</span>
                            <button className="btn btn-secondary" disabled={offset + result.items.length >= result.total || Boolean(savingId) || reconciling} onClick={() => setOffset(offset + 50)}>Next</button>
                        </div>
                    </>}
                </div>
            </main>
        </div>
    );
}
