import { useCallback, useState, useEffect } from 'react';
import {
    X,
    Phone,
    Clock,
    Building2,
    Calendar,
    FileText,
    MessageSquare,
    DollarSign,
    Target,
    AudioLines,
    Mic2,
    RefreshCw,
    Trash2,
    AlertCircle,
} from 'lucide-react';
import { api } from '../lib/api';
import type {
    AdminCallDetail,
    AdminFeedbackItem,
    AdminRecordingItem,
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
        if (!deleteTarget || !callId) return;
        setDeleting(true);
        const response = deleteTarget.kind === 'recording'
            ? await api.deleteAdminRecording(deleteTarget.item.id)
            : await api.deleteAdminFeedback(deleteTarget.item.id);
        if (response.error) {
            setError(response.error.message);
        } else {
            setDeleteTarget(null);
            try {
                await loadMedia(callId);
            } catch (caught) {
                setError(caught instanceof Error ? caught.message : 'Failed to refresh call media');
            }
        }
        setDeleting(false);
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
            <div className="drawer call-detail-drawer">
                <div className="drawer-header">
                    <h2>Call Details</h2>
                    <button className="drawer-close" onClick={onClose}>
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
                                    <span>{call.phone_number}</span>
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
                                        <DollarSign size={14} />
                                        <span className="meta-value">${call.cost.toFixed(4)}</span>
                                    </div>
                                )}
                            </div>

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
                            <div className="drawer-tabs">
                                <button
                                    className={`tab-btn ${activeTab === 'timeline' ? 'active' : ''}`}
                                    onClick={() => setActiveTab('timeline')}
                                >
                                    <Clock size={14} />
                                    Timeline
                                </button>
                                <button
                                    className={`tab-btn ${activeTab === 'transcript' ? 'active' : ''}`}
                                    onClick={() => setActiveTab('transcript')}
                                >
                                    <MessageSquare size={14} />
                                    Transcript
                                </button>
                                <button
                                    className={`tab-btn ${activeTab === 'recordings' ? 'active' : ''}`}
                                    onClick={() => setActiveTab('recordings')}
                                >
                                    <AudioLines size={14} />
                                    Recordings {recordings.length > 0 && `(${recordings.length})`}
                                </button>
                                <button
                                    className={`tab-btn ${activeTab === 'feedback' ? 'active' : ''}`}
                                    onClick={() => setActiveTab('feedback')}
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
                                                            onClick={() => setDeleteTarget({ kind: 'recording', item: recording })}
                                                        >
                                                            <Trash2 size={14} /> Delete
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
                                                        onClick={() => setDeleteTarget({ kind: 'feedback', item: feedback })}
                                                    >
                                                        <Trash2 size={14} /> Delete
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
                onConfirm={() => void deleteMedia()}
                onCancel={() => setDeleteTarget(null)}
            />
        </>
    );
}
