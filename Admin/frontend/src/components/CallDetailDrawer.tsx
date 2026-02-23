import { useState, useEffect } from 'react';
import {
    X,
    Phone,
    Clock,
    Building2,
    Calendar,
    FileText,
    Play,
    MessageSquare,
    DollarSign,
    Target
} from 'lucide-react';
import { api } from '../lib/api';
import type { AdminCallDetail, TranscriptTurn } from '../lib/api';

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

export function CallDetailDrawer({ callId, onClose }: CallDetailDrawerProps) {
    const [call, setCall] = useState<AdminCallDetail | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [activeTab, setActiveTab] = useState<'timeline' | 'transcript'>('timeline');

    useEffect(() => {
        if (!callId) {
            setCall(null);
            return;
        }

        const fetchCallDetail = async () => {
            setLoading(true);
            setError(null);
            try {
                const response = await api.getAdminCallDetail(callId);
                if (response.data) {
                    setCall(response.data);
                }
            } catch (err) {
                setError(err instanceof Error ? err.message : 'Failed to fetch call details');
            } finally {
                setLoading(false);
            }
        };

        fetchCallDetail();
    }, [callId]);

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
                    ) : error ? (
                        <div className="error-banner">
                            <p>{error}</p>
                        </div>
                    ) : call ? (
                        <>
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

                            {/* Recording */}
                            {call.recording_url && (
                                <div className="call-recording">
                                    <h4>
                                        <Play size={16} />
                                        Recording
                                    </h4>
                                    <audio controls src={call.recording_url} className="audio-player">
                                        Your browser does not support audio playback.
                                    </audio>
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
                            </div>

                            {/* Tab Content */}
                            <div className="tab-content">
                                {activeTab === 'timeline' ? (
                                    <TimelineSection timeline={call.timeline} />
                                ) : (
                                    <TranscriptSection
                                        transcript={call.transcript}
                                        transcriptJson={call.transcript_json}
                                    />
                                )}
                            </div>
                        </>
                    ) : null}
                </div>
            </div>
        </>
    );
}
