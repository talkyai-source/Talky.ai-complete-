import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { FileCheck, RefreshCw } from 'lucide-react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { NumberRequestDocuments } from '../components/NumberRequestDocuments';
import { NumberRequestDecision } from '../components/NumberRequestDecision';
import { formatStamp } from '../lib/support-format';
import { api } from '../lib/api';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { useOperationOwner } from '../lib/use-operation-owner';
import {
    INCOMPLETE_ANSWER, NUMBER_REQUEST_STATUS_LABELS, NUMBER_REQUEST_USE_LABELS, companyText, validNumberRequestDetail,
    type NumberRequestDetail,
} from '../lib/number-request-contracts';
import '../components/NumberRequests.css';

export function NumberRequestPage() {
    const generation = useAuthGeneration();
    const { tenantId = '', requestId = '' } = useParams();
    return <NumberRequestContent key={`${generation}:${tenantId}:${requestId}`} generation={generation} tenantId={tenantId} requestId={requestId} />;
}

function NumberRequestContent({ generation, tenantId, requestId }: { generation: number; tenantId: string; requestId: string }) {
    const [request, setRequest] = useState<NumberRequestDetail | null>(null);
    // The read that last finished, and the error of a read, are tied to their revision:
    // a new read is "loading" and hides an older error without setting state in the effect.
    const [settled, setSettled] = useState(-1);
    const [failure, setFailure] = useState<{ revision: number; message: string } | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [revision, setRevision] = useState(0);
    const captureOwner = useOperationOwner(generation, `${tenantId}:${requestId}:${revision}`);
    const loading = settled !== revision;
    const readError = failure && failure.revision === revision ? failure.message : null;

    useEffect(() => {
        let current = true;
        const owns = captureOwner();
        void api.getNumberRequest(tenantId, requestId).then((response) => {
            if (!current || !owns()) return;
            if (response.error || !validNumberRequestDetail(response.data)) {
                setFailure({ revision, message: response.error?.message ?? INCOMPLETE_ANSWER });
                return;
            }
            setRequest(response.data);
        }).catch(() => {
            if (current && owns()) setFailure({ revision, message: INCOMPLETE_ANSWER });
        }).finally(() => { if (current && owns()) setSettled(revision); });
        return () => { current = false; };
    }, [tenantId, requestId, revision, captureOwner]);

    const reload = () => { setNotice(null); setRevision((value) => value + 1); };
    // After a decision the result line stays while the request is read again.
    const decided = (result: string) => { setNotice(result); setRevision((value) => value + 1); };
    const owns = () => captureOwner()();

    return (
        <div className="app-layout">
            <Sidebar />
            <main className="main-content">
                <Header />
                <div className="dashboard-content number-requests" aria-busy={loading}>
                    <div className="page-header">
                        <div className="page-header-icon"><FileCheck /></div>
                        <div className="number-requests-heading">
                            <h1 className="page-title">Phone number request</h1>
                            <p className="page-description"><Link to="/number-requests">Back to the queue</Link></p>
                        </div>
                        <div className="page-header-actions">
                            <button type="button" className="btn btn-secondary" disabled={loading} onClick={reload}><RefreshCw size={16} /> Reload</button>
                        </div>
                    </div>

                    {readError && <div className="error-banner number-request-banner" role="alert"><p>{readError}</p></div>}
                    {notice && <p role="status" className="number-request-notice">{notice}</p>}
                    {loading && !request && <p role="status">Loading request…</p>}

                    {request && (
                        <>
                            <div className="card number-request-summary">
                                <dl>
                                    <div><dt>Company</dt><dd>{companyText(request)}</dd></div>
                                    <div><dt>Use</dt><dd>{NUMBER_REQUEST_USE_LABELS[request.use_type]}</dd></div>
                                    <div><dt>Status</dt><dd>{NUMBER_REQUEST_STATUS_LABELS[request.status]}</dd></div>
                                    <div><dt>Requester</dt><dd>{request.requester_email || '—'}</dd></div>
                                    <div><dt>Submitted</dt><dd>{formatStamp(request.submitted_at)}</dd></div>
                                    <div><dt>Reviewed</dt><dd>{formatStamp(request.reviewed_at)}</dd></div>
                                    {request.review_note && <div className="number-request-reason"><dt>Reason</dt><dd>{request.review_note}</dd></div>}
                                </dl>
                            </div>
                            {request.status === 'submitted' && (
                                <NumberRequestDecision key={revision} tenantId={tenantId} requestId={requestId}
                                    disabled={loading} owns={owns} onDecided={decided} onReload={reload} />
                            )}
                            <NumberRequestDocuments tenantId={tenantId} requestId={requestId} documents={request.documents} owns={owns} />
                        </>
                    )}
                </div>
            </main>
        </div>
    );
}
