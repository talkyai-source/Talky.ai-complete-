import { useState, useEffect, useRef } from 'react';
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
    AlertTriangle
} from 'lucide-react';
import { api } from '../lib/api';
import { ActionStatusBadge } from './ActionStatusBadge';
import { ActionReceiptPanel } from './ActionReceiptPanel';
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

export function ActionDetailDrawer({ actionId, onClose, onRetry }: ActionDetailDrawerProps) {
    const [action, setAction] = useState<ActionDetail | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [retrying, setRetrying] = useState(false);
    const [cancelling, setCancelling] = useState(false);
    const [confirmCancel, setConfirmCancel] = useState(false);
    const [confirmRetry, setConfirmRetry] = useState(false);

    const selection = useRef<{ id: string | null; active: boolean } | null>(null);

    useEffect(() => {
        const owner = { id: actionId, active: true };
        selection.current = owner;
        setAction(null);
        setError(null);
        setConfirmCancel(false);
        setConfirmRetry(false);
        setCancelling(false);
        setRetrying(false);
        setLoading(Boolean(actionId));
        if (actionId) {
            const fetchAction = async () => {
                try {
                    const response = await api.getActionDetail(actionId);
                    if (!owner.active) return;
                    if (!response.data || response.data.id !== actionId) {
                        throw new Error('The selected action receipt is unavailable.');
                    }
                    setAction(response.data);
                } catch (err) {
                    if (owner.active) setError(err instanceof Error ? err.message : 'Failed to fetch action details');
                } finally {
                    if (owner.active) setLoading(false);
                }
            };
            void fetchAction();
        }
        return () => { owner.active = false; };
    }, [actionId]);

    const handleRetry = async () => {
        const owner = selection.current;
        if (!owner?.active || owner.id !== actionId || action?.id !== actionId || retrying) return;
        if (!confirmRetry) {
            setConfirmRetry(true);
            return;
        }
        setRetrying(true);
        try {
            const response = await api.retryAction(actionId);
            if (!owner.active) return;
            if (response.error) throw new Error(response.error.message);
            onRetry?.();
            onClose();
        } catch (err) {
            if (owner.active) setError(err instanceof Error ? err.message : 'Failed to retry action');
        } finally {
            if (owner.active) { setRetrying(false); setConfirmRetry(false); }
        }
    };

    const handleCancel = async () => {
        const owner = selection.current;
        if (!owner?.active || owner.id !== actionId || action?.id !== actionId || cancelling) return;
        if (!confirmCancel) {
            setConfirmCancel(true);
            return;
        }
        setCancelling(true);
        try {
            const cancelled = await api.cancelAction(actionId);
            if (!owner.active) return;
            if (cancelled.error) throw new Error(cancelled.error.message);
            const response = await api.getActionDetail(actionId);
            if (!owner.active) return;
            if (!response.data || response.data.id !== actionId) {
                throw new Error('The selected action receipt is unavailable.');
            }
            setAction(response.data);
        } catch (err) {
            if (owner.active) setError(err instanceof Error ? err.message : 'Failed to cancel action');
        } finally {
            if (owner.active) { setCancelling(false); setConfirmCancel(false); }
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
                    ) : action?.id === actionId ? (
                        <>
                            {/* Action Header */}
                            <div className="action-info-header">
                                <div className="action-type-header">
                                    <ActionIcon size={24} />
                                    <span>{ACTION_LABELS[action.type] || action.type}</span>
                                </div>
                                <ActionStatusBadge status={action.status} />
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
                                            <span className="label">Current connector display name</span>
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

                            <ActionReceiptPanel action={action} />

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
