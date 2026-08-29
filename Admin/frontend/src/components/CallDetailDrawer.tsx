import { useCallback, useState, useEffect } from 'react';
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
import { formatCallCost } from '../lib/call-cost';
import type {
    AdminCallDetail,
    AdminFeedbackItem,
    AdminRecordingItem,
    AdminTransferLeg,
    TranscriptTurn,
} from '../lib/api';
import { AdminMediaPlayer } from './AdminMediaPlayer';
import { ConfirmationModal } from './ConfirmationModal';

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
type MediaDeleteTarget =
    | { kind: 'recording'; item: AdminRecordingItem }
    | { kind: 'feedback'; item: AdminFeedbackItem };

export function CallDetailDrawer({ callId, onClose }: CallDetailDrawerProps) {
    const [call, setCall] = useState<AdminCallDetail | null>(null);
    const [recordings, setRecordings] = useState<AdminRecordingItem[]>([]);
    const [feedback, setFeedback] = useState<AdminFeedbackItem | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [activeTab, setActiveTab] = useState<DetailTab>('timeline');
    const [retryingFeedback, setRetryingFeedback] = useState(false);
    const [deleteTarget, setDeleteTarget] = useState<MediaDeleteTarget | null>(null);
    const [deleteReason, setDeleteReason] = useState('');
    const [deleteIdempotencyKey, setDeleteIdempotencyKey] = useState('');
    const [deleting, setDeleting] = useState(false);

    const loadMedia = useCallback(async (selectedCallId: string) => {
        const [recordingResponse, feedbackResponse] = await Promise.all([
            api.getAdminRecordings({ call_id: selectedCallId, page_size: 20 }),
            api.getAdminFeedback({ call_id: selectedCallId, page_size: 1 }),
        ]);
        if (recordingResponse.error) throw new Error(recordingResponse.error.message);
        if (feedbackResponse.error) throw new Error(feedbackResponse.error.message);
        setRecordings(recordingResponse.data?.items ?? []);
        setFeedback(feedbackResponse.data?.items[0] ?? null);
    }, []);

    useEffect(() => {
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
                if (response.error) throw new Error(response.error.message);
                if (!response.data) throw new Error('Call details were empty');
                setCall(response.data);
                await loadMedia(callId);
            } catch (err) {
                setError(err instanceof Error ? err.message : 'Failed to fetch call details');
            } finally {
                setLoading(false);
            }
        };

        void fetchCallDetail();
    }, [callId, loadMedia]);

    const retryFeedback = async () => {
        if (!feedback) return;
        setRetryingFeedback(true);
        setError(null);
        const response = await api.retryAdminFeedbackTranscription(feedback.id);
        if (response.error) setError(response.error.message);
        if (response.data) setFeedback(response.data);
        setRetryingFeedback(false);
    };

    const deleteMedia = async () => {
        if (!deleteTarget || !callId || !deleteIdempotencyKey) return;
        setDeleting(true);
        const response = deleteTarget.kind === 'recording'
            ? await api.deleteAdminRecording(
                deleteTarget.item.id,
                deleteReason.trim(),
                deleteIdempotencyKey,
            )
            : await api.deleteAdminFeedback(
                deleteTarget.item.id,
                deleteReason.trim(),
                deleteIdempotencyKey,
            );
        if (response.error) {
            setError(response.error.message);
        } else {
            setDeleteTarget(null);
            setDeleteReason('');
            setDeleteIdempotencyKey('');
            try {
                await loadMedia(callId);
            } catch (caught) {
                setError(caught instanceof Error ? caught.message : 'Failed to refresh call media');
            }
        }
        setDeleting(false);
    };

    const openMediaDelete = (target: MediaDeleteTarget) => {
        if (target.item.legal_hold) return;
        const id = typeof crypto !== 'undefined' && 'randomUUID' in crypto
            ? crypto.randomUUID()
            : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
        setDeleteTarget(target);
        setDeleteReason('');
        setDeleteIdempotencyKey(`admin-media:${target.kind}:${target.item.id}:${id}`);
    };

    const closeMediaDelete = () => {
        if (deleting) return;
        setDeleteTarget(null);
        setDeleteReason('');
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
                    ) : call ? (
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
                                {activeTab === 'recordings' && (
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
                                                        disabled={!recording.playable}
                                                        filename={`call-${call.id}.wav`}
                                                        load={() => api.getAdminRecordingAudio(recording.id)}
                                                    />
                                                    <div className="drawer-media-actions">
                                                        <span>{formatDuration(recording.duration_seconds)} · {recording.storage}</span>
                                                        <button
                                                            className="btn btn-danger btn-sm"
                                                            onClick={() => openMediaDelete({ kind: 'recording', item: recording })}
                                                            disabled={recording.legal_hold}
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
                                {activeTab === 'feedback' && (
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
                                                filename={`feedback-${call.id}.webm`}
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
                                                            disabled={retryingFeedback}
                                                            onClick={() => void retryFeedback()}
                                                        >
                                                            <RefreshCw size={14} className={retryingFeedback ? 'spinning' : ''} />
                                                            Retry
                                                        </button>
                                                    )}
                                                    <button
                                                        className="btn btn-danger btn-sm"
                                                        onClick={() => openMediaDelete({ kind: 'feedback', item: feedback })}
                                                        disabled={feedback.legal_hold}
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
                onReasonChange={setDeleteReason}
                reasonLabel="Deletion reason"
                onConfirm={() => void deleteMedia()}
                onCancel={closeMediaDelete}
            />
        </>
    );
}
