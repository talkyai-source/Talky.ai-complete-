import { useLayoutEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import type { AdminMediaDeletionItem, AdminMediaDeletionList, AdminMediaDeletionParams, AdminMediaResourceType } from '../lib/api';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { useOperationOwner } from '../lib/use-operation-owner';
import { sameMediaDeletionTarget, validMediaDeletion, validMediaDeletionList } from '../lib/media-deletion-contracts';
import type { MediaDeletionNotice, MediaDeletionTarget } from '../lib/media-deletion-contracts';
import './MediaDeletionHistory.css';

interface Props {
    resourceType: AdminMediaResourceType;
    tenantId?: string;
    callId?: string;
    notice?: MediaDeletionNotice | null;
    onRecovered?: (notice: MediaDeletionNotice, saved: AdminMediaDeletionItem) => void;
}

const labels = {
    intent_committed: 'Deletion requested', object_deleted: 'Storage removal recorded — metadata completion pending',
    failed: 'Deletion incomplete — a storage effect may already have occurred', completed: 'Deletion completed',
};
const when = (value: string | null) => value ? new Date(value).toLocaleString() : 'Not recorded';

function SavedDeletion({ item }: { item: AdminMediaDeletionItem }) {
    return <article className="media-deletion-record" aria-label="Server-saved deletion">
        <h4>Server-saved deletion</h4>
        <p><strong>{labels[item.status]}</strong></p>
        <dl>
            <div><dt>Media</dt><dd>{item.resource_type === 'recording' ? 'Recording' : 'Feedback'} · {item.resource_id}</dd></div>
            <div><dt>Call</dt><dd>{item.call_id}</dd></div>
            <div><dt>Tenant</dt><dd>{item.tenant_id}</dd></div>
            <div><dt>Deletion record</dt><dd>{item.id}</dd></div>
            <div><dt>Original operator</dt><dd>{item.original_actor_id}</dd></div>
            <div><dt>Original reason</dt><dd>{item.reason}</dd></div>
            <div><dt>Requested</dt><dd>{when(item.created_at)}</dd></div>
            <div><dt>Last saved update</dt><dd>{when(item.updated_at)}</dd></div>
            <div><dt>Storage removal recorded</dt><dd>{when(item.object_deleted_at)}</dd></div>
            <div><dt>Completion recorded</dt><dd>{when(item.completed_at)}</dd></div>
            <div><dt>Recorded attempts</dt><dd>{item.attempt_count}</dd></div>
        </dl>
        {item.status === 'completed' && !item.completed_at && <p>Saved as completed; the original completion time is unavailable.</p>}
        <p>This is the saved deletion state. Reading it does not repeat deletion or certify a separate audit delivery.</p>
    </article>;
}

export function MediaDeletionHistory(props: Props) {
    const generation = useAuthGeneration();
    const scope = JSON.stringify([props.resourceType, props.tenantId || '', props.callId || '']);
    return <OwnedMediaDeletionHistory key={`${generation}:${scope}`} {...props} generation={generation} scope={scope} />;
}

function OwnedMediaDeletionHistory({ resourceType, tenantId, callId, notice, onRecovered, generation, scope }: Props & { generation: number; scope: string }) {
    const capture = useOperationOwner(generation, scope);
    const [page, setPage] = useState<AdminMediaDeletionList | null>(null);
    const pageRef = useRef<AdminMediaDeletionList | null>(null);
    const [cursors, setCursors] = useState<Array<string | undefined>>([]);
    const [cursor, setCursor] = useState<string | undefined>();
    const [historyLoading, setHistoryLoading] = useState(false);
    const historyFlight = useRef(false);
    const [historyError, setHistoryError] = useState<string | null>(null);
    const [selected, setSelected] = useState<AdminMediaDeletionItem | null>(null);
    const selectedRef = useRef<AdminMediaDeletionItem | null>(null);
    const [readLoading, setReadLoading] = useState(false);
    const [readError, setReadError] = useState<string | null>(null);
    const readSequence = useRef(0);
    const readFlight = useRef<string | null>(null);
    const noticeRef = useRef(notice);
    useLayoutEffect(() => { noticeRef.current = notice; }, [notice]);

    const loadHistory = async (expected: AdminMediaDeletionList | null, next?: string, movement: 'first' | 'next' | 'previous' = 'first') => {
        const owns = capture();
        if (!owns() || historyFlight.current || pageRef.current !== expected) return;
        historyFlight.current = true; setHistoryLoading(true); setHistoryError(null);
        const filters: AdminMediaDeletionParams = { resource_type: resourceType, tenant_id: tenantId || undefined,
            call_id: callId || undefined, status: 'all', limit: 20, cursor: next };
        try {
            const result = await api.getAdminMediaDeletions(filters);
            if (!owns()) return;
            if (result.error) throw new Error(result.error.message);
            if (!validMediaDeletionList(result.data, filters, 20)
                || (result.data.has_more && (result.data.next_cursor === next || cursors.includes(result.data.next_cursor!)))) {
                throw new Error('Deletion history is incomplete or does not match this media section.');
            }
            pageRef.current = result.data; setPage(result.data); setCursor(next);
            setCursors(movement === 'next' ? [...cursors, cursor] : movement === 'previous' ? cursors.slice(0, -1) : []);
        } catch (caught) {
            if (owns()) setHistoryError(caught instanceof Error ? caught.message : 'Deletion history could not be read.');
        } finally {
            if (owns()) { historyFlight.current = false; setHistoryLoading(false); }
        }
    };

    const checkSaved = async (target: MediaDeletionTarget, intentId?: string, expectedNotice?: MediaDeletionNotice,
        expectedPage?: AdminMediaDeletionList) => {
        const owns = capture();
        const key = `${target.resource_type}:${target.resource_id}:${intentId || ''}`;
        if (!owns() || readFlight.current === key || (expectedNotice && noticeRef.current !== expectedNotice)
            || (expectedPage && pageRef.current !== expectedPage)) return;
        readFlight.current = key;
        const sequence = ++readSequence.current;
        const current = () => owns() && sequence === readSequence.current
            && (!expectedNotice || noticeRef.current === expectedNotice)
            && (!expectedPage || pageRef.current === expectedPage);
        setReadLoading(true); setReadError(null);
        if (!selectedRef.current || !sameMediaDeletionTarget(selectedRef.current, target)) {
            selectedRef.current = null; setSelected(null);
        }
        try {
            let saved: unknown;
            if (intentId) {
                const result = await api.getAdminMediaDeletion(intentId);
                if (!current()) return;
                if (result.error) throw new Error(result.error.message);
                saved = result.data;
            } else {
                const filters = { ...target, status: 'all' as const, limit: 2 };
                const result = await api.getAdminMediaDeletions(filters);
                if (!current()) return;
                if (result.error) throw new Error(result.error.message);
                if (!validMediaDeletionList(result.data, filters, 2) || result.data.has_more || result.data.items.length > 1) {
                    throw new Error('A unique saved deletion could not be verified. The result remains unconfirmed.');
                }
                if (!result.data.items.length) throw new Error('No saved deletion was found. This does not prove the earlier request had no effect.');
                saved = result.data.items[0];
            }
            if (!validMediaDeletion(saved, target) || (intentId && saved.id !== intentId)
                || (expectedNotice?.state === 'completed' && saved.status !== 'completed')
                || (selectedRef.current?.id === saved.id && selectedRef.current.status === 'completed' && saved.status !== 'completed')) {
                throw new Error('The saved deletion did not match the reviewed media or its confirmed completion.');
            }
            selectedRef.current = saved; setSelected(saved);
            if (expectedNotice) onRecovered?.(expectedNotice, saved);
        } catch (caught) {
            if (current()) setReadError(caught instanceof Error ? caught.message : 'Saved deletion could not be checked.');
        } finally {
            if (owns() && sequence === readSequence.current) { readFlight.current = null; setReadLoading(false); }
        }
    };

    return <section className="media-deletion-history" aria-label={`${resourceType === 'recording' ? 'Recording' : 'Feedback'} deletion recovery`}>
        {notice && <div className="media-deletion-notice" aria-live="polite">
            <h4>{notice.state === 'completed' ? 'Deletion confirmed' : notice.state === 'absent' ? 'Media already absent'
                : notice.state === 'pending' ? 'Deletion request sent' : 'Deletion result unconfirmed'}</h4>
            <p>{notice.target.resource_type === 'recording' ? 'Recording' : 'Feedback'} {notice.target.resource_id} · Call {notice.target.call_id}</p>
            {notice.receipt && <p>{notice.receipt.detail}</p>}
            {notice.receipt?.deletion_intent_id && <p>Saved deletion ID: {notice.receipt.deletion_intent_id}</p>}
            {notice.state === 'absent' && <p>The media record and a saved deletion record were absent. This does not confirm an earlier deletion request.</p>}
            {notice.message && <p>{notice.message}</p>}
            {notice.state === 'unconfirmed' && <p>Check the saved state before deciding what to do next. Closing a review does not cancel a sent deletion.</p>}
            {notice.state !== 'pending' && <button type="button" className="btn btn-secondary btn-sm"
                aria-disabled={readLoading} onClick={() => void checkSaved(notice.target,
                    notice.receipt?.deletion_intent_id || notice.saved?.id, notice)}>
                {readLoading ? 'Checking saved deletion…' : 'Check saved deletion'}
            </button>}
        </div>}
        {readError && <p className="error-banner" role="alert">{readError}</p>}
        {selected && <SavedDeletion item={selected} />}
        <div className="media-deletion-history-header">
            <h4>Deletion history</h4>
            <button type="button" className="btn btn-secondary btn-sm" aria-disabled={historyLoading}
                onClick={() => void loadHistory(page)}>{historyLoading ? 'Reading deletion history…' : page ? 'Refresh deletion history' : 'Load deletion history'}</button>
        </div>
        <p>Read saved {resourceType === 'recording' ? 'recording' : 'feedback'} deletions{callId ? ' for this call' : tenantId ? ' for the selected tenant' : ' across tenants'}, including media no longer in the list. History is ordered by request time and may change between pages.</p>
        {historyError && <p className="error-banner" role="alert">{historyError}</p>}
        {page && !historyLoading && !historyError && page.items.length === 0 && <p>No saved deletions in this history page.</p>}
        {page && <>
            <ul className="media-deletion-history-list">
                {page.items.map(item => <li key={item.id}>
                    <span>{labels[item.status]} · {when(item.created_at)}</span>
                    <span>{item.resource_id} · Call {item.call_id}</span>
                    <button type="button" className="btn btn-secondary btn-sm" onClick={() => void checkSaved({
                        resource_type: item.resource_type, resource_id: item.resource_id, call_id: item.call_id, tenant_id: item.tenant_id,
                    }, item.id, undefined, page)} aria-label={`View saved deletion ${item.resource_id}`}>View saved deletion</button>
                </li>)}
            </ul>
            <div className="pagination-controls">
                <button type="button" className="btn btn-secondary btn-sm" disabled={historyLoading || !cursors.length}
                    onClick={() => void loadHistory(page, cursors.at(-1), 'previous')}>Previous deletions</button>
                <button type="button" className="btn btn-secondary btn-sm" disabled={historyLoading || !page.has_more}
                    onClick={() => void loadHistory(page, page.next_cursor || undefined, 'next')}>Next deletions</button>
            </div>
        </>}
    </section>;
}
