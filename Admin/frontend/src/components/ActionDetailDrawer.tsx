import { useState, useEffect } from 'react';
import {
    X,
    Mail,
    MessageSquare,
    Phone,
    Calendar,
    Bell,
    Play,
    Building2,
    Clock,
    User,
    Zap,
    RefreshCw,
    Ban,
    ChevronDown,
    ChevronUp,
    Copy,
    CheckCircle,
    XCircle,
    Loader2,
    AlertTriangle
} from 'lucide-react';
import { api } from '../lib/api';
import type { ActionDetail, ActionType } from '../lib/api';

interface ActionDetailDrawerProps {
    actionId: string | null;
    onClose: () => void;
    onRetry?: () => void;
}

const ACTION_ICONS: Record<ActionType, typeof Mail> = {
    'send_email': Mail,
    'send_sms': MessageSquare,
    'initiate_call': Phone,
    'book_meeting': Calendar,
    'set_reminder': Bell,
    'start_campaign': Play
};

const ACTION_LABELS: Record<ActionType, string> = {
    'send_email': 'Send Email',
    'send_sms': 'Send SMS',
    'initiate_call': 'Initiate Call',
    'book_meeting': 'Book Meeting',
    'set_reminder': 'Set Reminder',
    'start_campaign': 'Start Campaign'
};

function formatDate(dateStr: string | null | undefined): string {
    if (!dateStr) return '-';
    try {
        const date = new Date(dateStr);
        return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
    } catch {
        return dateStr;
    }
}

function formatDuration(ms: number | null): string {
    if (ms === null) return '-';
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
}

function JsonViewer({ data, title }: { data: Record<string, unknown> | null; title: string }) {
    const [expanded, setExpanded] = useState(false);
    const [copied, setCopied] = useState(false);

    if (!data || Object.keys(data).length === 0) {
        return (
            <div className="json-viewer empty">
                <h4>{title}</h4>
                <p className="no-data">No data available</p>
            </div>
        );
    }

    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch {
            console.error('Failed to copy');
        }
    };

    return (
        <div className="json-viewer">
            <div className="json-header" onClick={() => setExpanded(!expanded)}>
                <h4>
                    {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                    {title}
                </h4>
                <button className="btn btn-ghost btn-sm" onClick={(e) => { e.stopPropagation(); handleCopy(); }}>
                    {copied ? <CheckCircle size={14} /> : <Copy size={14} />}
                    {copied ? 'Copied!' : 'Copy'}
                </button>
            </div>
            {expanded && (
                <pre className="json-content">
                    {JSON.stringify(data, null, 2)}
                </pre>
            )}
        </div>
    );
}

export function ActionDetailDrawer({ actionId, onClose, onRetry }: ActionDetailDrawerProps) {
    const [action, setAction] = useState<ActionDetail | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [retrying, setRetrying] = useState(false);
    const [cancelling, setCancelling] = useState(false);
    const [confirmCancel, setConfirmCancel] = useState(false);
    const [confirmRetry, setConfirmRetry] = useState(false);

    useEffect(() => {
        if (!actionId) {
            setAction(null);
            return;
        }

        const fetchAction = async () => {
            setLoading(true);
            setError(null);
            try {
                const response = await api.getActionDetail(actionId);
                if (response.data) {
                    setAction(response.data);
                }
            } catch (err) {
                setError(err instanceof Error ? err.message : 'Failed to fetch action details');
            } finally {
                setLoading(false);
            }
        };

        fetchAction();
    }, [actionId]);

    const handleRetry = async () => {
        if (!actionId || !confirmRetry) {
            setConfirmRetry(true);
            return;
        }

        setRetrying(true);
        try {
            await api.retryAction(actionId);
            onRetry?.();
            onClose();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to retry action');
        } finally {
            setRetrying(false);
            setConfirmRetry(false);
        }
    };

    const handleCancel = async () => {
        if (!actionId || !confirmCancel) {
            setConfirmCancel(true);
            return;
        }

        setCancelling(true);
        try {
            await api.cancelAction(actionId);
            // Refresh the action data
            const response = await api.getActionDetail(actionId);
            if (response.data) {
                setAction(response.data);
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to cancel action');
        } finally {
            setCancelling(false);
            setConfirmCancel(false);
        }
    };

    if (!actionId) return null;

    const ActionIcon = action ? ACTION_ICONS[action.type] || Play : Play;

    return (
        <>
            <div className="drawer-overlay" onClick={onClose}></div>
            <div className="drawer action-detail-drawer">
                <div className="drawer-header">
                    <h2>Action Details</h2>
                    <button className="drawer-close" onClick={onClose}>
                        <X size={20} />
                    </button>
                </div>

                <div className="drawer-body">
                    {loading ? (
                        <div className="drawer-loading">
                            <div className="loading-spinner"></div>
                            <p>Loading action details...</p>
                        </div>
                    ) : error ? (
                        <div className="error-banner">
                            <p>{error}</p>
                        </div>
                    ) : action ? (
                        <>
                            {/* Action Header */}
                            <div className="action-info-header">
                                <div className="action-type-header">
                                    <ActionIcon size={24} />
                                    <span>{ACTION_LABELS[action.type] || action.type}</span>
                                </div>
                                <span className={`action-status-badge status-${action.status}`}>
                                    {action.status === 'running' && <Loader2 size={12} className="spinning" />}
                                    {action.status === 'completed' && <CheckCircle size={12} />}
                                    {action.status === 'failed' && <XCircle size={12} />}
                                    {action.status === 'cancelled' && <Ban size={12} />}
                                    {action.status}
                                </span>
                            </div>

                            {/* Quick Stats */}
                            <div className="action-quick-stats">
                                <div className="stat-item">
                                    <Building2 size={16} />
                                    <span>{action.tenant_name}</span>
                                </div>
                                {action.lead_name && (
                                    <div className="stat-item">
                                        <User size={16} />
                                        <span>{action.lead_name}</span>
                                    </div>
                                )}
                                <div className="stat-item">
                                    <Clock size={16} />
                                    <span>{formatDuration(action.duration_ms)}</span>
                                </div>
                                <div className="stat-item">
                                    <Zap size={16} />
                                    <span>{action.triggered_by || 'Unknown'}</span>
                                </div>
                            </div>

                            {/* Timestamps */}
                            <div className="action-timestamps">
                                <div className="timestamp-row">
                                    <span className="label">Created</span>
                                    <span className="value">{formatDate(action.created_at)}</span>
                                </div>
                                {action.started_at && (
                                    <div className="timestamp-row">
                                        <span className="label">Started</span>
                                        <span className="value">{formatDate(action.started_at)}</span>
                                    </div>
                                )}
                                {action.completed_at && (
                                    <div className="timestamp-row">
                                        <span className="label">Completed</span>
                                        <span className="value">{formatDate(action.completed_at)}</span>
                                    </div>
                                )}
                                {action.scheduled_at && (
                                    <div className="timestamp-row">
                                        <span className="label">Scheduled</span>
                                        <span className="value">{formatDate(action.scheduled_at)}</span>
                                    </div>
                                )}
                            </div>

                            {/* Related Entities */}
                            {(action.campaign_name || action.connector_name) && (
                                <div className="action-related">
                                    {action.campaign_name && (
                                        <div className="related-item">
                                            <span className="label">Campaign</span>
                                            <span className="value">{action.campaign_name}</span>
                                        </div>
                                    )}
                                    {action.connector_name && (
                                        <div className="related-item">
                                            <span className="label">Connector</span>
                                            <span className="value">{action.connector_name}</span>
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* Error Display */}
                            {action.error && (
                                <div className="action-error">
                                    <AlertTriangle size={16} />
                                    <div>
                                        <strong>Error</strong>
                                        <p>{action.error}</p>
                                    </div>
                                </div>
                            )}

                            {/* Input/Output JSON */}
                            <JsonViewer data={action.input_data} title="Input Payload" />
                            <JsonViewer data={action.output_data} title="Output / Result" />

                            {/* Audit Info */}
                            {(action.ip_address || action.idempotency_key) && (
                                <div className="action-audit">
                                    <h4>Audit Info</h4>
                                    {action.ip_address && (
                                        <div className="audit-row">
                                            <span className="label">IP Address</span>
                                            <span className="value mono">{action.ip_address}</span>
                                        </div>
                                    )}
                                    {action.request_id && (
                                        <div className="audit-row">
                                            <span className="label">Request ID</span>
                                            <span className="value mono">{action.request_id}</span>
                                        </div>
                                    )}
                                    {action.idempotency_key && (
                                        <div className="audit-row">
                                            <span className="label">Idempotency Key</span>
                                            <span className="value mono">{action.idempotency_key}</span>
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* Action Buttons */}
                            {(action.is_cancellable || action.is_retryable) && (
                                <div className="action-buttons">
                                    {action.is_cancellable && (
                                        confirmCancel ? (
                                            <div className="confirm-inline">
                                                <span>Cancel action?</span>
                                                <button
                                                    className="btn btn-danger btn-sm"
                                                    onClick={handleCancel}
                                                    disabled={cancelling}
                                                >
                                                    {cancelling ? 'Cancelling...' : 'Yes, Cancel'}
                                                </button>
                                                <button
                                                    className="btn btn-secondary btn-sm"
                                                    onClick={() => setConfirmCancel(false)}
                                                >
                                                    No
                                                </button>
                                            </div>
                                        ) : (
                                            <button
                                                className="btn btn-danger"
                                                onClick={handleCancel}
                                            >
                                                <Ban size={16} />
                                                Cancel Action
                                            </button>
                                        )
                                    )}
                                    {action.is_retryable && (
                                        confirmRetry ? (
                                            <div className="confirm-inline">
                                                <span>Retry action?</span>
                                                <button
                                                    className="btn btn-primary btn-sm"
                                                    onClick={handleRetry}
                                                    disabled={retrying}
                                                >
                                                    {retrying ? 'Retrying...' : 'Yes, Retry'}
                                                </button>
                                                <button
                                                    className="btn btn-secondary btn-sm"
                                                    onClick={() => setConfirmRetry(false)}
                                                >
                                                    No
                                                </button>
                                            </div>
                                        ) : (
                                            <button
                                                className="btn btn-primary"
                                                onClick={handleRetry}
                                            >
                                                <RefreshCw size={16} />
                                                Retry Action
                                            </button>
                                        )
                                    )}
                                </div>
                            )}
                        </>
                    ) : null}
                </div>
            </div>
        </>
    );
}
