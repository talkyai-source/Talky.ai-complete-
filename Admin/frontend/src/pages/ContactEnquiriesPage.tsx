import { useEffect, useRef, useState } from 'react';
import { Inbox, RefreshCw } from 'lucide-react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { api } from '../lib/api';
import type { ContactEnquiriesResponse, ContactEnquiry } from '../lib/api';

export function ContactEnquiriesPage() {
    const [status, setStatus] = useState<'' | 'new' | 'handled'>('new');
    const [offset, setOffset] = useState(0);
    const [revision, setRevision] = useState(0);
    const [result, setResult] = useState<ContactEnquiriesResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [savingId, setSavingId] = useState<string | null>(null);
    const saving = useRef(false);

    useEffect(() => {
        let current = true;
        setLoading(true);
        setError(null);
        setResult(null);
        void api.getContactEnquiries(status, offset).then((response) => {
            if (!current) return;
            if (response.error || !response.data) {
                setError(response.error?.message || 'Could not load enquiries. Please retry.');
            } else if (offset > 0 && response.data.items.length === 0) {
                setOffset(Math.max(0, offset - 50));
            } else {
                setResult(response.data);
            }
            setLoading(false);
        });
        return () => { current = false; };
    }, [status, offset, revision]);

    async function changeStatus(enquiry: ContactEnquiry) {
        if (saving.current) return;
        saving.current = true;
        setSavingId(enquiry.id);
        setError(null);
        setNotice(null);
        const nextStatus = enquiry.status === 'new' ? 'handled' : 'new';
        try {
            const response = await api.updateContactEnquiry(enquiry.id, nextStatus);
            if (response.error || response.data?.id !== enquiry.id || response.data.status !== nextStatus) {
                setError(response.error?.message || 'The update could not be confirmed. Refresh before retrying.');
                return;
            }
            setNotice(nextStatus === 'handled' ? 'Enquiry marked as handled.' : 'Enquiry reopened.');
            setRevision((value) => value + 1);
        } finally {
            saving.current = false;
            setSavingId(null);
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
                            <h1 className="page-title">Website Enquiries</h1>
                            <p className="page-description">Saved contact requests. Follow up using your team’s communication tools, then mark as handled.</p>
                        </div>
                        <div className="page-header-actions">
                            <button className="btn btn-secondary" disabled={loading || Boolean(savingId)} onClick={() => setRevision((value) => value + 1)}>
                                <RefreshCw size={16} /> Refresh
                            </button>
                        </div>
                    </div>
                    <div className="enquiry-toolbar">
                        <label htmlFor="enquiry-status">Status </label>
                        <select id="enquiry-status" value={status} disabled={Boolean(savingId)} onChange={(event) => {
                            setStatus(event.target.value as typeof status);
                            setOffset(0);
                            setNotice(null);
                        }}>
                            <option value="new">New</option>
                            <option value="handled">Handled</option>
                            <option value="">All</option>
                        </select>
                    </div>
                    {error && <div className="error-banner" role="alert">{error}</div>}
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
                                        <button className="btn btn-secondary" disabled={Boolean(savingId)} onClick={() => void changeStatus(enquiry)}>
                                            {savingId === enquiry.id ? 'Saving…' : enquiry.status === 'new' ? 'Mark handled' : 'Reopen'}
                                        </button>
                                    </div>
                                </article>
                            ))}
                        </div>
                        <div className="enquiry-toolbar">
                            <button className="btn btn-secondary" disabled={offset === 0 || Boolean(savingId)} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</button>
                            <span>{result.total === 0 ? '0' : `${offset + 1}–${offset + result.items.length}`} of {result.total}</span>
                            <button className="btn btn-secondary" disabled={offset + result.items.length >= result.total || Boolean(savingId)} onClick={() => setOffset(offset + 50)}>Next</button>
                        </div>
                    </>}
                </div>
            </main>
        </div>
    );
}
