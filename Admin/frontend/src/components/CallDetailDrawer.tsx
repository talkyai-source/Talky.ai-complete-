import { useCallback, useState, useEffect, useRef, useLayoutEffect } from 'react';
import {
    X,
    Phone,
    Clock,
    Building2,
    Calendar,
    FileText,
    MessageSquare,
    Coins,
    Target,
    AudioLines,
    Mic2,
    RefreshCw,
    Trash2,
    AlertCircle,
    PhoneIncoming,
    PhoneOutgoing,
    Route,
    ShieldAlert,
    ShieldCheck,
} from 'lucide-react';
import { api } from '../lib/api';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { formatCallCost } from '../lib/call-cost';
import type {
    AdminCallDetail,
    AdminCRMInspection,
    AdminCRMReceipt,
    AdminFeedbackItem,
    AdminRecordingItem,
    AdminTransferLeg,
    TranscriptTurn,
} from '../lib/api';
import { AdminMediaPlayer } from './AdminMediaPlayer';
import { ConfirmationModal } from './ConfirmationModal';
import { MediaDeletionHistory } from './MediaDeletionHistory';
import { mediaDeletionTarget, matchesMediaDeletionReceipt, recoveredMediaDeletion, matchesFeedbackRetry, type MediaDeletionNotice } from '../lib/media-deletion-contracts';

interface CallDetailDrawerProps {
    callId: string | null;
    onClose: () => void;
}

function formatDate(dateStr: string | null | undefined): string {
    if (!dateStr) return '-';
    try {
        const date = new Date(dateStr);
        return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
    } catch {
        return dateStr;
    }
}

function formatDuration(seconds: number | null): string {
    if (seconds === null) return '-';
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}m ${secs}s`;
}

function summaryString(summary: Record<string, unknown> | null, key: string): string | null {
    const value = summary?.[key];
    if (typeof value !== 'string') return null;
    const normalized = value.trim();
    if (!normalized || ['unknown', 'none'].includes(normalized.toLowerCase())) return null;
    return normalized;
}

function humanize(value: string): string {
    return value.replace(/_/g, ' ');
}

function StatusBadgeInline({ value }: { value: string }) {
    const safe = value.toLowerCase().replace(/[^a-z0-9_-]/g, '');
    return <span className={`call-status-badge status-${safe}`}>{humanize(value)}</span>;
}

function TimelineSection({ timeline }: { timeline: AdminCallDetail['timeline'] }) {
    if (!timeline || timeline.length === 0) {
        return <p className="no-data">No timeline data available</p>;
    }

    return (
        <div className="call-timeline">
            {timeline.map((event, index) => (
                <div key={index} className="timeline-item">
                    <div className="timeline-dot"></div>
                    <div className="timeline-content">
                        <span className="timeline-event">{event.event}</span>
                        <span className="timeline-time">{formatDate(event.timestamp)}</span>
                    </div>
                </div>
            ))}
        </div>
    );
}

function TranscriptSection({ transcript, transcriptJson }: {
    transcript: string | null;
    transcriptJson: TranscriptTurn[] | null
}) {
    if (transcriptJson && transcriptJson.length > 0) {
        return (
            <div className="transcript-chat">
                {transcriptJson.map((turn, index) => (
                    <div key={index} className={`chat-bubble ${turn.role}`}>
                        <span className="chat-role">
                            {turn.role === 'assistant' ? 'AI Agent' : 'Customer'}
                        </span>
                        <p className="chat-content">{turn.content}</p>
                    </div>
                ))}
            </div>
        );
    }

    if (transcript) {
        return (
            <div className="transcript-text">
                <pre>{transcript}</pre>
            </div>
        );
    }

    return <p className="no-data">No transcript available</p>;
}

function transferMetadataNumber(leg: AdminTransferLeg, key: string): number | null {
    const value = leg.metadata?.[key];
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value);
    return null;
}

function TransferLegsSection({ legs }: { legs: AdminTransferLeg[] }) {
    if (legs.length === 0) return null;
    return (
        <section className="transfer-legs-panel" aria-labelledby="transfer-legs-heading">
            <div className="qualification-panel-header">
                <strong id="transfer-legs-heading"><Route size={15} /> Transfer legs</strong>
                <span>{legs.length}</span>
            </div>
            <div className="transfer-leg-list">
                {legs.map((leg, index) => {
                    const attempt = transferMetadataNumber(leg, 'attempt');
                    const hop = transferMetadataNumber(leg, 'hop');
                    return (
                        <article className="transfer-leg-card" key={leg.id || index}>
                            <div className="transfer-leg-card-header">
                                <strong>{leg.to_number || 'Unknown destination'}</strong>
                                <StatusBadgeInline value={leg.status || 'unknown'} />
                            </div>
                            <dl>
                                <div><dt>Provider</dt><dd>{leg.provider || 'Unknown'}</dd></div>
                                <div><dt>Started</dt><dd>{formatDate(leg.started_at)}</dd></div>
                                <div><dt>Answered</dt><dd>{formatDate(leg.answered_at)}</dd></div>
                                <div><dt>Ended</dt><dd>{formatDate(leg.ended_at)}</dd></div>
                                <div><dt>Duration</dt><dd>{formatDuration(leg.duration_seconds ?? null)}</dd></div>
                                {(attempt !== null || hop !== null) && <div><dt>Policy position</dt><dd>{attempt !== null ? `attempt ${attempt}` : ''}{attempt !== null && hop !== null ? ' · ' : ''}{hop !== null ? `hop ${hop}` : ''}</dd></div>}
                            </dl>
                        </article>
                    );
                })}
            </div>
        </section>
    );
}

type DetailTab = 'timeline' | 'transcript' | 'recordings' | 'feedback';
const inspectionLabels: Record<AdminCRMInspection['outcome'], string> = {
    observed_reference: 'Matching reference observed',
    no_match: 'No matching reference observed — inconclusive',
    ambiguous: 'Multiple matching references — unresolved',
    unavailable: 'Inspection unavailable',
};

const inspectionReasons: Record<AdminCRMInspection['reason'], string> = {
    reference_observed_only: 'This does not verify the payload, contact, or completion of this delivery.',
    absence_is_inconclusive: 'An empty search does not prove the action was not executed. Do not resend on this basis.',
    multiple_references: 'The provider returned more than one matching activity. The receipt remains unresolved.',
    original_receipt_unavailable: 'The saved receipt does not contain the required original call-creation evidence.',
    original_authorization_unavailable: 'A unique, active original account with an unexpired token is unavailable.',
    provider_read_unavailable: 'The provider observation could not be obtained. The receipt is unchanged.',
};

export function CRMReceiptsSection({ callId, receipts, available }: {
    callId: string; receipts: AdminCRMReceipt[]; available: boolean;
}) {
    const [observations, setObservations] = useState<Partial<Record<AdminCRMReceipt['provider'], AdminCRMInspection>>>({});
    const [pending, setPending] = useState<Partial<Record<AdminCRMReceipt['provider'], boolean>>>({});
    const [errors, setErrors] = useState<Partial<Record<AdminCRMReceipt['provider'], string>>>({});
    const generation = useRef(0);
    const inFlight = useRef(new Set<AdminCRMReceipt['provider']>());
    const session = api.getAuthGeneration();

    useEffect(() => {
        generation.current += 1;
        setObservations({}); setPending({}); setErrors({}); inFlight.current.clear();
        return () => { generation.current += 1; };
    }, [callId, session]);

    const inspect = async (provider: AdminCRMReceipt['provider']) => {
        if (inFlight.current.has(provider) || session !== api.getAuthGeneration()) return;
        const ownGeneration = generation.current;
        const owns = () => generation.current === ownGeneration && session === api.getAuthGeneration();
        inFlight.current.add(provider);
        setPending((before) => ({ ...before, [provider]: true }));
        setErrors((before) => ({ ...before, [provider]: undefined }));
        setObservations((before) => ({ ...before, [provider]: undefined }));
        try {
            const result = await api.inspectAdminCRMDelivery(callId, provider);
            if (!owns()) return;
            if (result.error || !result.data || result.data.call_id !== callId || result.data.provider !== provider
                || !Object.hasOwn(inspectionLabels, result.data.outcome) || !Object.hasOwn(inspectionReasons, result.data.reason)) {
                setErrors((before) => ({ ...before, [provider]: 'Inspection unavailable. Saved receipt unchanged.' }));
            } else {
                setObservations((before) => ({ ...before, [provider]: result.data }));
            }
        } catch {
            if (owns()) {
                setErrors((before) => ({ ...before, [provider]: 'Inspection unavailable. Saved receipt unchanged.' }));
            }
        } finally {
            if (owns()) {
                inFlight.current.delete(provider);
                setPending((before) => ({ ...before, [provider]: false }));
            }
        }
    };

    return <section className="qualification-panel" aria-label="Saved CRM delivery evidence">
        <h4>CRM delivery evidence</h4>
        <p>Read-only evidence. Inspection does not complete, retry, or change the saved delivery.</p>
        {!available ? <p>Saved CRM receipts are unavailable.</p> : receipts.length === 0 ? <p>No saved CRM delivery receipts.</p> : receipts.map((receipt) => {
            const observed = observations[receipt.provider];
            return <article key={receipt.provider}>
                <strong>{receipt.provider === 'hubspot' ? 'HubSpot' : 'Salesforce'}</strong>
                <dl>
                    <div><dt>Saved status / phase</dt><dd>{humanize(receipt.status)} / {humanize(receipt.phase)}</dd></div>
                    <div><dt>Original connector</dt><dd>{receipt.destination_connector_id || 'Unavailable'}</dd></div>
                    <div><dt>Original account</dt><dd>{receipt.destination_account_id || 'Unavailable'}</dd></div>
                    <div><dt>Saved contact ID</dt><dd>{receipt.remote_contact_id || 'Unavailable'}</dd></div>
                    <div><dt>Saved activity ID</dt><dd>{receipt.remote_call_id || 'Unavailable'}</dd></div>
                    <div><dt>Receipt updated</dt><dd>{formatDate(receipt.updated_at)}</dd></div>
                </dl>
                {receipt.inspection_available ? <button type="button" className="btn btn-secondary btn-sm"
                    disabled={Boolean(pending[receipt.provider])} onClick={() => void inspect(receipt.provider)}>
                    {pending[receipt.provider] ? 'Inspecting original account…' : 'Inspect original account'}
                </button> : <p>Reference inspection is unavailable for this saved phase or missing original identity.</p>}
                <div aria-live="polite">
                    {errors[receipt.provider] && <p>{errors[receipt.provider]}</p>}
                    {observed && <>
                        <p><strong>{inspectionLabels[observed.outcome]}</strong></p>
                        <p>{inspectionReasons[observed.reason]}</p>
                        {observed.observed_remote_id && <p>Observed activity ID: {observed.observed_remote_id}</p>}
                        <p>Observed at: {formatDate(observed.observed_at)}</p>
                    </>}
                </div>
            </article>;
        })}
    </section>;
}

type MediaDeleteTarget =
    | { kind: 'recording'; item: AdminRecordingItem }
    | { kind: 'feedback'; item: AdminFeedbackItem };

export function CallDetailDrawer({ callId, onClose }: CallDetailDrawerProps) {
    const session = useAuthGeneration();
    if (!callId) return null;
    return <OwnedCallDetailDrawer key={`${session}:${callId}`} callId={callId} onClose={onClose} session={session} />;
}

function OwnedCallDetailDrawer({ callId, onClose, session }: CallDetailDrawerProps & { session: number }) {
    const [call, setCall] = useState<AdminCallDetail | null>(null);
    const [recordings, setRecordings] = useState<AdminRecordingItem[]>([]);
    const [feedback, setFeedback] = useState<AdminFeedbackItem | null>(null);
    const [loading, setLoading] = useState(false);
    const [recordingError, setRecordingError] = useState<string | null>(null);
    const [feedbackError, setFeedbackError] = useState<string | null>(null);
    const [deletionNotice, setDeletionNotice] = useState<MediaDeletionNotice | null>(null);
    const mediaRead = useRef(0);
    const [error, setError] = useState<string | null>(null);
    const [activeTab, setActiveTab] = useState<DetailTab>('timeline');
    const [retryingFeedback, setRetryingFeedback] = useState(false);
    const [deleteTarget, setDeleteTarget] = useState<MediaDeleteTarget | null>(null);
    const [deleteReason, setDeleteReason] = useState('');
    const [reasonReadOnly, setReasonReadOnly] = useState(false);
    const submittedReason = useRef<string | null>(null);
    const [deleteIdempotencyKey, setDeleteIdempotencyKey] = useState('');
    const [deleting, setDeleting] = useState(false);
    const mounted = useRef(false);
    const deletionInFlight = useRef(false);
    const retryInFlight = useRef(false);
    const currentMedia = useRef({ recordings, feedback });
    useLayoutEffect(() => { currentMedia.current = { recordings, feedback }; }, [recordings, feedback]);

    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; };
    }, []);
    const owns = useCallback(() => mounted.current && session === api.getAuthGeneration(), [session]);

    const loadMedia = useCallback(async (selectedCall: AdminCallDetail, isCurrent: () => boolean = owns) => {
        if (!owns() || !isCurrent()) return;
        const sequence = ++mediaRead.current;
        const current = () => owns() && isCurrent() && sequence === mediaRead.current;
        try {
            const [recordingResponse, feedbackResponse] = await Promise.all([
                api.getAdminRecordings({ call_id: selectedCall.id, page_size: 20 }),
                api.getAdminFeedback({ call_id: selectedCall.id, page_size: 1 }),
            ]);
            if (!current()) return;
            const matches = (items: unknown): items is Array<AdminRecordingItem | AdminFeedbackItem> => Array.isArray(items)
                && items.every(item => item && typeof item.id === 'string' && item.id.trim()
                    && selectedCall.tenant_id && item.call_id === selectedCall.id && item.tenant_id === selectedCall.tenant_id);
            const recordingItems = recordingResponse.data?.items;
            const feedbackItems = feedbackResponse.data?.items;
            if ((!recordingResponse.error && !matches(recordingItems)) || (!feedbackResponse.error && !matches(feedbackItems))) {
                const message = 'Call media ownership could not be verified. Reload the call before continuing.';
                setRecordings([]); setFeedback(null); setRecordingError(message); setFeedbackError(message);
                return;
            }
            if (!recordingResponse.error && matches(recordingItems)) {
                setRecordings(recordingItems as AdminRecordingItem[]); setRecordingError(null);
            } else {
                setRecordings([]); setRecordingError(recordingResponse.error?.message ?? 'Call media ownership could not be verified. Reload the call before continuing.');
            }
            if (!feedbackResponse.error && matches(feedbackItems)) {
                setFeedback((feedbackItems[0] as AdminFeedbackItem | undefined) ?? null); setFeedbackError(null);
            } else {
                setFeedback(null); setFeedbackError(feedbackResponse.error?.message ?? 'Call media ownership could not be verified. Reload the call before continuing.');
            }
        } catch (caught) {
            if (current()) {
                const message = caught instanceof Error ? caught.message : 'Call media could not be loaded.';
                setRecordings([]); setFeedback(null); setRecordingError(message); setFeedbackError(message);
            }
        }
    }, [owns]);

    useEffect(() => {
        let cancelled = false;
        setCall(null);
        if (!callId) {
            setCall(null);
            setRecordings([]);
            setFeedback(null);
            return;
        }

        const fetchCallDetail = async () => {
            setLoading(true);
            setError(null);
            setActiveTab('timeline');
            try {
                const response = await api.getAdminCallDetail(callId);
                if (cancelled || !owns()) return;
                if (response.error) throw new Error(response.error.message);
                if (!response.data || response.data.id !== callId) throw new Error('Call details were unavailable');
                setCall(response.data);
                await loadMedia(response.data, () => !cancelled);
            } catch (err) {
                if (!cancelled && owns()) setError(err instanceof Error ? err.message : 'Failed to fetch call details');
            } finally {
                if (!cancelled && owns()) setLoading(false);
            }
        };

        void fetchCallDetail();
        return () => { cancelled = true; };
    }, [callId, loadMedia, owns]);

    const retryFeedback = async () => {
        if (!owns() || !feedback || !call || retryInFlight.current || deletionInFlight.current
            || currentMedia.current.feedback !== feedback) return;
        const selectedFeedback = feedback;
        retryInFlight.current = true;
        ++mediaRead.current;
        setRetryingFeedback(true);
        setError(null);
        try {
            const response = await api.retryAdminFeedbackTranscription(selectedFeedback.id);
            if (!owns()) return;
            if (response.error) setError(response.error.message);
            else if (!matchesFeedbackRetry(response.data, selectedFeedback)) {
                setError('Feedback ownership could not be verified. Reload the call before continuing.');
            } else {
                ++mediaRead.current;
                setFeedback(response.data);
            }
        } catch (caught) {
            if (owns()) setError(caught instanceof Error ? caught.message : 'Feedback retry could not be confirmed.');
        } finally {
            retryInFlight.current = false;
            if (owns()) setRetryingFeedback(false);
        }
    };

    const deleteMedia = async () => {
        if (!owns() || !deleteTarget || !call || !deleteIdempotencyKey || deletionInFlight.current || retryInFlight.current) return;
        deletionInFlight.current = true;
        ++mediaRead.current;
        const target = mediaDeletionTarget(deleteTarget.kind === 'recording' ? 'recording' : 'call_feedback', deleteTarget.item);
        setDeletionNotice({ target, state: 'pending' });
        submittedReason.current ??= deleteReason.trim();
        setReasonReadOnly(true);
        setDeleting(true);
        setError(null);
        try {
            const response = deleteTarget.kind === 'recording'
                ? await api.deleteAdminRecording(
                    deleteTarget.item.id,
                    submittedReason.current,
                    deleteIdempotencyKey,
                )
                : await api.deleteAdminFeedback(
                    deleteTarget.item.id,
                    submittedReason.current,
                    deleteIdempotencyKey,
                );
            if (!owns()) return;
            if (response.error || !matchesMediaDeletionReceipt(response.data, target)) {
                const message = response.error?.message ?? 'Deletion was not confirmed. Check the saved deletion or explicitly retry the same reviewed request.';
                setError(message); setDeletionNotice({ target, state: 'unconfirmed', message });
            } else {
                setDeletionNotice({ target, state: response.data.outcome, receipt: response.data });
                setDeleteTarget(null);
                setDeleteReason(''); submittedReason.current = null; setReasonReadOnly(false);
                setDeleteIdempotencyKey('');
                try {
                    await loadMedia(call);
                } catch (caught) {
                    if (owns()) setError(caught instanceof Error ? caught.message : 'Failed to refresh call media');
                }
            }
        } catch (caught) {
            if (owns()) {
                const message = caught instanceof Error ? caught.message : 'Deletion could not be confirmed. Check the saved deletion.';
                setError(message); setDeletionNotice(previous => previous?.state === 'completed' || previous?.state === 'absent' ? previous : { target, state: 'unconfirmed', message });
            }
        } finally {
            deletionInFlight.current = false;
            if (owns()) setDeleting(false);
        }
    };

    const openMediaDelete = (target: MediaDeleteTarget) => {
        if (!owns() || !call || retryInFlight.current || deletionInFlight.current || target.item.legal_hold
            || (target.kind === 'recording' ? !currentMedia.current.recordings.includes(target.item) : currentMedia.current.feedback !== target.item)
            || target.item.call_id !== call.id
            || target.item.tenant_id !== call.tenant_id) return;
        const id = typeof crypto !== 'undefined' && 'randomUUID' in crypto
            ? crypto.randomUUID()
            : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
        setDeleteTarget(target);
        setError(null);
        setDeleteReason(''); submittedReason.current = null; setReasonReadOnly(false);
        setDeleteIdempotencyKey(`admin-media:${target.kind}:${target.item.id}:${id}`);
    };

    const closeMediaDelete = () => {
        if (deleting) return;
        setDeleteTarget(null);
        setDeleteReason(''); submittedReason.current = null; setReasonReadOnly(false);
        setDeleteIdempotencyKey('');
    };

    const qualification = call ? summaryString(call.summary_json, 'qualification_status') : null;
    const qualificationDetails = call ? [
        ['Need', summaryString(call.summary_json, 'identified_need')],
        ['Decision role', summaryString(call.summary_json, 'decision_maker_status')],
        ['Timeline', summaryString(call.summary_json, 'timeline')],
        ['Budget', summaryString(call.summary_json, 'budget_information')],
    ].filter((entry): entry is [string, string] => Boolean(entry[1])) : [];

    if (!callId) return null;

    return (
        <>
            <div className="drawer-overlay" onClick={onClose}></div>
            <div className="drawer call-detail-drawer" role="dialog" aria-modal="true" aria-labelledby="call-detail-title">
                <div className="drawer-header">
                    <h2 id="call-detail-title">Call Details</h2>
                    <button className="drawer-close" onClick={onClose} aria-label="Close call details">
                        <X size={20} />
                    </button>
                </div>

                <div className="drawer-body">
                    {loading ? (
                        <div className="drawer-loading">
                            <div className="loading-spinner"></div>
                            <p>Loading call details...</p>
                        </div>
                    ) : !call && error ? (
                        <div className="error-banner">
                            <p>{error}</p>
                        </div>
                    ) : call && call.id === callId ? (
                        <>
                            {error && (
                                <div className="error-banner call-action-error">
                                    <AlertCircle size={16} />
                                    <p>{error}</p>
                                    <button onClick={() => setError(null)}>Dismiss</button>
                                </div>
                            )}
                            {/* Call Info Header */}
                            <div className="call-info-header">
                                <div className="call-phone">
                                    <Phone size={20} />
                                    <span>{call.caller_ani || call.phone_number}</span>
                                    <span className={`direction-badge direction-${call.direction || 'outbound'}`}>
                                        {call.direction === 'inbound' ? <PhoneIncoming size={13} /> : <PhoneOutgoing size={13} />}
                                        {call.direction || 'outbound'}
                                    </span>
                                </div>
                                <span className={`call-status-badge status-${call.status}`}>
                                    {call.outcome || call.status}
                                </span>
                            </div>

                            {/* Quick Stats */}
                            <div className="call-quick-stats">
                                <div className="stat-item">
                                    <Building2 size={16} />
                                    <span>{call.tenant_name}</span>
                                </div>
                                <div className="stat-item">
                                    <Clock size={16} />
                                    <span>{formatDuration(call.duration_seconds)}</span>
                                </div>
                                <div className="stat-item">
                                    <Calendar size={16} />
                                    <span>{formatDate(call.created_at)}</span>
                                </div>
                                {call.goal_achieved && (
                                    <div className="stat-item goal-achieved">
                                        <Target size={16} />
                                        <span>Goal Achieved</span>
                                    </div>
                                )}
                            </div>

                            {/* Campaign & Cost */}
                            <div className="call-meta">
                                {call.campaign_name && (
                                    <div className="meta-item">
                                        <span className="meta-label">Campaign</span>
                                        <span className="meta-value">{call.campaign_name}</span>
                                    </div>
                                )}
                                {call.cost !== null && (
                                    <div className="meta-item">
                                        <Coins size={14} />
                                        <span className="meta-value">
                                            {formatCallCost(call.cost, call.currency)}
                                        </span>
                                    </div>
                                )}
                            </div>

                            {(call.direction === 'inbound' || call.called_did || call.admission_status) && (
                                <div className="inbound-call-operations" aria-label="Inbound routing and processing details">
                                    <div className="qualification-panel-header">
                                        <strong><Route size={15} /> Inbound route</strong>
                                        <StatusBadgeInline value={call.admission_status || 'unknown'} />
                                    </div>
                                    <dl>
                                        <div><dt>Caller ANI</dt><dd>{call.caller_ani || 'Private / unavailable'}</dd></div>
                                        <div><dt>Called DID</dt><dd>{call.called_did || 'Unknown'}</dd></div>
                                        <div><dt>Provider / ingress</dt><dd>{[call.provider, call.ingress].filter(Boolean).join(' / ') || 'Unknown'}</dd></div>
                                        <div><dt>Route version</dt><dd>{call.route_version ?? '—'}</dd></div>
                                        <div><dt>Config version</dt><dd>{call.config_version ?? '—'}</dd></div>
                                        <div><dt>Admission reason</dt><dd>{call.admission_reason || 'Accepted'}</dd></div>
                                    </dl>
                                    <div className="inbound-processing-states">
                                        <span><ShieldCheck size={13} /> consent: {call.consent_status || 'unknown'}</span>
                                        <span>processing: {call.processing_status || 'unknown'}</span>
                                        <span>billing: {call.billing_status || 'unknown'}</span>
                                        {call.billing_hold_reason && <span>hold: {call.billing_hold_reason}</span>}
                                        {call.reserved_seconds !== null && call.reserved_seconds !== undefined && <span>reserved: {call.reserved_seconds}s</span>}
                                    </div>
                                </div>
                            )}

                            <TransferLegsSection legs={call.transfer_legs ?? []} />
                            <CRMReceiptsSection key={call.id} callId={call.id} receipts={call.crm_deliveries ?? []}
                                available={call.crm_receipts_available === true} />

                            {/* Summary */}
                            {call.summary && (
                                <div className="call-summary">
                                    <h4>
                                        <FileText size={16} />
                                        Summary
                                    </h4>
                                    <p>{call.summary}</p>
                                </div>
                            )}

                            {(qualification || qualificationDetails.length > 0) && (
                                <div className="qualification-panel">
                                    <div className="qualification-panel-header">
                                        <strong>Lead qualification</strong>
                                        {qualification && (
                                            <span className={`call-status-badge status-${qualification}`}>
                                                {humanize(qualification)}
                                            </span>
                                        )}
                                    </div>
                                    {qualificationDetails.length > 0 && (
                                        <dl>
                                            {qualificationDetails.map(([label, value]) => (
                                                <div key={label}>
                                                    <dt>{label}</dt>
                                                    <dd>{label === 'Decision role' ? humanize(value) : value}</dd>
                                                </div>
                                            ))}
                                        </dl>
                                    )}
                                </div>
                            )}

                            {(recordingError || feedbackError) && <div role="alert" className="error-banner">
                                {recordingError && <p>Recordings: {recordingError}</p>}
                                {feedbackError && <p>Feedback: {feedbackError}</p>}
                                <button className="btn btn-secondary btn-sm" onClick={() => { if (!retryInFlight.current && !deletionInFlight.current) void loadMedia(call); }}>Retry media</button>
                            </div>}
                            {/* Tabs */}
                            <div className="drawer-tabs" role="tablist" aria-label="Call detail sections">
                                <button
                                    className={`tab-btn ${activeTab === 'timeline' ? 'active' : ''}`}
                                    onClick={() => setActiveTab('timeline')}
                                    role="tab"
                                    aria-selected={activeTab === 'timeline'}
                                >
                                    <Clock size={14} />
                                    Timeline
                                </button>
                                <button
                                    className={`tab-btn ${activeTab === 'transcript' ? 'active' : ''}`}
                                    onClick={() => setActiveTab('transcript')}
                                    role="tab"
                                    aria-selected={activeTab === 'transcript'}
                                >
                                    <MessageSquare size={14} />
                                    Transcript
                                </button>
                                <button
                                    className={`tab-btn ${activeTab === 'recordings' ? 'active' : ''}`}
                                    onClick={() => setActiveTab('recordings')}
                                    role="tab"
                                    aria-selected={activeTab === 'recordings'}
                                >
                                    <AudioLines size={14} />
                                    Recordings {recordings.length > 0 && `(${recordings.length})`}
                                </button>
                                <button
                                    className={`tab-btn ${activeTab === 'feedback' ? 'active' : ''}`}
                                    onClick={() => setActiveTab('feedback')}
                                    role="tab"
                                    aria-selected={activeTab === 'feedback'}
                                >
                                    <Mic2 size={14} />
                                    Feedback {feedback && '(1)'}
                                </button>
                            </div>

                            {/* Tab Content */}
                            <div className="tab-content">
                                {activeTab === 'timeline' && (
                                    <TimelineSection timeline={call.timeline} />
                                )}
                                {activeTab === 'transcript' && (
                                    <TranscriptSection
                                        transcript={call.transcript}
                                        transcriptJson={call.transcript_json}
                                    />
                                )}
                                {activeTab === 'recordings' && !recordingError && (
                                    recordings.length === 0 ? (
                                        <p className="no-data">No recording is available for this call.</p>
                                    ) : (
                                        <div className="drawer-media-list">
                                            {recordings.map((recording, index) => (
                                                <div className="drawer-media-card" key={recording.id}>
                                                    <div className="drawer-media-card-header">
                                                        <div>
                                                            <strong>Recording {index + 1}</strong>
                                                            <span>{formatDate(recording.created_at)}</span>
                                                        </div>
                                                        <span className={`call-status-badge status-${recording.status}`}>
                                                            {recording.status}
                                                        </span>
                                                    </div>
                                                    <AdminMediaPlayer
                                                        resourceId={`recording:${recording.id}`}
                                                        disabled={!recording.playable}
                                                        filenameBase={`call-${call.id}`}
                                                        mimeType={recording.mime_type}
                                                        fallbackExtension="wav"
                                                        load={() => api.getAdminRecordingAudio(recording.id)}
                                                    />
                                                    <div className="drawer-media-actions">
                                                        <span>{formatDuration(recording.duration_seconds)} · {recording.storage}</span>
                                                        <button
                                                            className="btn btn-danger btn-sm"
                                                            onClick={() => openMediaDelete({ kind: 'recording', item: recording })}
                                                            disabled={recording.legal_hold || retryingFeedback || deleting}
                                                            title={recording.legal_hold ? 'Deletion blocked by an active compliance/legal hold' : undefined}
                                                        >
                                                            {recording.legal_hold ? <ShieldAlert size={14} /> : <Trash2 size={14} />}
                                                            {recording.legal_hold ? 'Legal hold' : 'Delete'}
                                                        </button>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    )
                                )}
                                {activeTab === 'feedback' && !feedbackError && (
                                    !feedback ? (
                                        <p className="no-data">No reviewer feedback note has been submitted.</p>
                                    ) : (
                                        <div className="drawer-media-card feedback-detail-card">
                                            <div className="drawer-media-card-header">
                                                <div>
                                                    <strong>{feedback.created_by_name || 'Reviewer feedback'}</strong>
                                                    <span>{feedback.created_by_email || formatDate(feedback.created_at)}</span>
                                                </div>
                                                <span className={`call-status-badge status-${feedback.transcript_status}`}>
                                                    {feedback.transcript_status}
                                                </span>
                                            </div>
                                            <AdminMediaPlayer
                                                resourceId={`feedback:${feedback.id}`}
                                                filenameBase={`feedback-${call.id}`}
                                                mimeType={feedback.audio_mime_type}
                                                fallbackExtension="webm"
                                                load={() => api.getAdminFeedbackAudio(feedback.id)}
                                            />
                                            <div className="feedback-detail-transcript">
                                                <strong>Transcript</strong>
                                                {feedback.transcript ? (
                                                    <p>{feedback.transcript}</p>
                                                ) : feedback.transcript_error ? (
                                                    <p className="transcript-error">{feedback.transcript_error}</p>
                                                ) : (
                                                    <p className="no-data-inline">No transcript yet.</p>
                                                )}
                                            </div>
                                            <div className="drawer-media-actions">
                                                <span>Transcription attempts: {feedback.transcription_attempts}</span>
                                                <div className="row-actions">
                                                    {(feedback.retryable || feedback.transcript_status === 'pending') && (
                                                        <button
                                                            className="btn btn-secondary btn-sm"
                                                            disabled={retryingFeedback || deleting}
                                                            onClick={() => void retryFeedback()}
                                                        >
                                                            <RefreshCw size={14} className={retryingFeedback ? 'spinning' : ''} />
                                                            Retry
                                                        </button>
                                                    )}
                                                    <button
                                                        className="btn btn-danger btn-sm"
                                                        onClick={() => openMediaDelete({ kind: 'feedback', item: feedback })}
                                                        disabled={feedback.legal_hold || retryingFeedback || deleting}
                                                        title={feedback.legal_hold ? 'Deletion blocked by an active compliance/legal hold' : undefined}
                                                    >
                                                        {feedback.legal_hold ? <ShieldAlert size={14} /> : <Trash2 size={14} />}
                                                        {feedback.legal_hold ? 'Legal hold' : 'Delete'}
                                                    </button>
                                                </div>
                                            </div>
                                        </div>
                                    )
                                )}
                                {(activeTab === 'recordings' || activeTab === 'feedback') && <MediaDeletionHistory
                                    resourceType={activeTab === 'recordings' ? 'recording' : 'call_feedback'} tenantId={call.tenant_id} callId={call.id}
                                    notice={deletionNotice?.target.resource_type === (activeTab === 'recordings' ? 'recording' : 'call_feedback') ? deletionNotice : null}
                                    onRecovered={(expected, saved) => {
                                        if (!owns()) return;
                                        setDeletionNotice(previous => previous === expected ? recoveredMediaDeletion(previous, saved) : previous);
                                    }}
                                />}
                            </div>
                        </>
                    ) : null}
                </div>
            </div>
            <ConfirmationModal
                isOpen={Boolean(deleteTarget)}
                title={`Permanently delete ${deleteTarget?.kind || 'media'}?`}
                message="The audio and its metadata will be removed from storage. This cannot be undone."
                confirmLabel="Delete permanently"
                variant="danger"
                loading={deleting}
                reason={deleteReason}
                reasonReadOnly={reasonReadOnly}
                onReasonChange={value => { if (submittedReason.current === null) setDeleteReason(value); }}
                reasonLabel="Deletion reason"
                onConfirm={() => void deleteMedia()}
                onCancel={closeMediaDelete}
                error={deleteTarget ? error : null}
            />
        </>
    );
}
