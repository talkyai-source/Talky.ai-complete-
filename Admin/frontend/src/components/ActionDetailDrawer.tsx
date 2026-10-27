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
import { useAuthGeneration } from '../lib/use-auth-generation';
import { useOperationOwner } from '../lib/use-operation-owner';
import { useDialogFocus } from '../lib/use-dialog-focus';

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
    const session = useAuthGeneration();
    const scope = `${session}:${actionId ?? ''}`;
    const [loadedScope, setLoadedScope] = useState(scope);
    const captureOwner = useOperationOwner(session, actionId ?? '');
    const [action, setAction] = useState<ActionDetail | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [retrying, setRetrying] = useState(false);
    const [cancelling, setCancelling] = useState(false);
    const [confirmCancel, setConfirmCancel] = useState(false);
    const [confirmRetry, setConfirmRetry] = useState(false);
    const [cancelledScope, setCancelledScope] = useState<string | null>(null);
    const [receiptReadError, setReceiptReadError] = useState<string | null>(null);
    const [receiptReading, setReceiptReading] = useState(false);
    const cancelledReceipt = useRef<string | null>(null);
    const receiptReadFlight = useRef(false);
    const cancellationConfirmed = cancelledScope === scope;

    const selection = useRef<{ id: string | null; active: boolean } | null>(null);
    const mutationInFlight = useRef(false);
    const drawerRef = useRef<HTMLDivElement>(null);
    useDialogFocus(Boolean(actionId), drawerRef, onClose, cancelling || retrying);

    useEffect(() => {
        const owner = { id: actionId, active: true };
        const owns = () => owner.active && selection.current === owner && session === api.getAuthGeneration();
        selection.current = owner;
        setLoadedScope(`${session}:${actionId ?? ''}`);
        mutationInFlight.current = false;
        cancelledReceipt.current = null;
        receiptReadFlight.current = false;
        setCancelledScope(null);
        setReceiptReadError(null);
        setReceiptReading(false);
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
                    if (!owner.active || !owns()) return;
                    if (response.error || !response.data || response.data.id !== actionId) {
                        throw new Error('The selected action receipt is unavailable.');
                    }
                    setAction(response.data);
                } catch (err) {
                    if (owner.active && owns()) setError(err instanceof Error ? err.message : 'Failed to fetch action details');
                } finally {
                    if (owner.active && owns()) setLoading(false);
                }
            };
            void fetchAction();
        }
        return () => { owner.active = false; };
    }, [actionId, session]);

    const reloadReceipt = async (): Promise<ActionDetail | null> => {
        const owner = selection.current;
        const owns = captureOwner();
        if (!owns() || !owner?.active || owner.id !== actionId || action?.id !== actionId) return null;
        const response = await api.getActionDetail(actionId);
        if (!owner.active || !owns() || response.error || !response.data || response.data.id !== actionId) return null;
        // A stale or mismatched read cannot undo an exact cancellation receipt.
        if (cancelledReceipt.current === scope && response.data.status !== 'cancelled') return null;
        setAction(response.data);
        return response.data;
    };

    const reloadCancelledReceipt = async () => {
        const owns = captureOwner();
        if (!owns() || cancelledReceipt.current !== scope || receiptReadFlight.current) return;
        receiptReadFlight.current = true;
        setReceiptReading(true);
        setReceiptReadError(null);
        try {
            const refreshed = await reloadReceipt();
            if (owns() && !refreshed) {
                setReceiptReadError('Cancellation is confirmed. The latest action details could not be verified; reload the saved details.');
            }
        } catch {
            if (owns()) setReceiptReadError('Cancellation is confirmed. The latest action details could not be loaded; reload the saved details.');
        } finally {
            if (owns()) { receiptReadFlight.current = false; setReceiptReading(false); }
        }
    };

    const handleRetry = async () => {
        const owner = selection.current;
        const owns = captureOwner();
        if (!owns() || !owner?.active || owner.id !== actionId || action?.id !== actionId || mutationInFlight.current || cancelledReceipt.current === scope) return;
        if (!confirmRetry) {
            setConfirmRetry(true);
            return;
        }
        mutationInFlight.current = true;
        setRetrying(true);
        try {
            const response = await api.retryAction(actionId);
            if (!owner.active || !owns()) return;
            if (response.error) throw new Error(response.error.message);
            onRetry?.();
            onClose();
        } catch (err) {
            if (owner.active && owns()) setError(err instanceof Error ? err.message : 'Failed to retry action');
        } finally {
            if (owner.active && owns()) { mutationInFlight.current = false; setRetrying(false); setConfirmRetry(false); }
        }
    };

    const handleCancel = async () => {
        const owner = selection.current;
        const owns = captureOwner();
        if (!owns() || !owner?.active || owner.id !== actionId || action?.id !== actionId || mutationInFlight.current || cancelledReceipt.current === scope) return;
        if (!confirmCancel) {
            setConfirmCancel(true);
            return;
        }
        mutationInFlight.current = true;
        setCancelling(true);
        try {
            const cancelled = await api.cancelAction(actionId);
            if (!owner.active || !owns()) return;
            if (cancelled.error) throw new Error(cancelled.error.message);
            if (cancelled.data?.action_id !== actionId || cancelled.data.new_status !== 'cancelled') {
                throw new Error('Cancellation is not confirmed. Reload the saved action before retrying.');
            }
            cancelledReceipt.current = scope;
            setCancelledScope(scope);
            setConfirmCancel(false);
            await reloadCancelledReceipt();
        } catch (err) {
            if (owner.active && owns()) setError(err instanceof Error ? err.message : 'Failed to cancel action');
        } finally {
            if (owner.active && owns()) { mutationInFlight.current = false; setCancelling(false); setConfirmCancel(false); }
        }
    };

    if (!actionId) return null;

    const ActionIcon = action ? ACTION_ICONS[action.type] || Play : Play;

    return (
        <>
            <div className="drawer-overlay" onClick={() => { if (!cancelling && !retrying) onClose(); }}></div>
            <div className="drawer action-detail-drawer" ref={drawerRef} role="dialog" aria-modal="true"
                aria-labelledby="action-details-title" tabIndex={-1}>
                <div className="drawer-header">
                    <h2 id="action-details-title">Action Details</h2>
                    <button type="button" className="drawer-close" aria-label="Close action details" onClick={onClose} disabled={cancelling || retrying}>
                        <X size={20} />
                    </button>
                </div>

                <div className="drawer-body">
                    {loadedScope !== scope || loading ? (
                        <div className="drawer-loading">
                            <div className="loading-spinner"></div>
                            <p>Loading action details...</p>
                        </div>
                    ) : error && !cancellationConfirmed ? (
                        <div className="error-banner">
                            <p>{error}</p>
                        </div>
                    ) : action?.id === actionId ? (
                        <>
                            {cancellationConfirmed && <div aria-label="Confirmed cancellation">
                                <p role="status">Cancellation confirmed.</p>
                                {receiptReading && <p role="status">Loading the saved action details…</p>}
                                {receiptReadError && <div className="error-banner" role="alert">
                                    <p>{receiptReadError}</p>
                                    <button type="button" className="btn btn-secondary btn-sm" disabled={receiptReading}
                                        onClick={() => void reloadCancelledReceipt()}>Reload saved action details</button>
                                </div>}
                            </div>}
                            {/* Action Header */}
                            <div className="action-info-header">
                                <div className="action-type-header">
                                    <ActionIcon size={24} />
                                    <span>{ACTION_LABELS[action.type] || action.type}</span>
                                </div>
                                <ActionStatusBadge status={cancellationConfirmed ? 'cancelled' : action.status} />
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

                            {(!cancellationConfirmed || action.status === 'cancelled') &&
                                <ActionReceiptPanel key={scope} action={action} onReloadReceipt={reloadReceipt} />}

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
                            {!cancellationConfirmed && (action.is_cancellable || action.is_retryable) && (
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
