import { useCallback, useEffect, useRef, useState } from 'react';
import {
    X,
    Link2,
    Calendar,
    Mail,
    Database,
    Cloud,
    CheckCircle,
    XCircle,
    AlertTriangle,
    Clock,
    Building2,
    RefreshCw,
    Ban,
    Shield,
    Key,
    ExternalLink
} from 'lucide-react';
import { api } from '../lib/api';
import type { AdminConnectorItem, AdminConnectorDetail } from '../lib/api';
import { ConfirmationModal } from './ConfirmationModal';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { useDialogFocus } from '../lib/use-dialog-focus';

interface ConnectorDetailDrawerProps {
    connector: AdminConnectorItem | null;
    onClose: () => void;
    onRefresh?: () => void;
}

const providerIcons: Record<string, React.ReactNode> = {
    google_calendar: <Calendar size={20} />,
    outlook_calendar: <Calendar size={20} />,
    gmail: <Mail size={20} />,
    hubspot: <Database size={20} />,
    google_drive: <Cloud size={20} />,
};

const providerLabels: Record<string, string> = {
    google_calendar: 'Google Calendar',
    outlook_calendar: 'Microsoft Outlook',
    gmail: 'Gmail',
    hubspot: 'HubSpot',
    google_drive: 'Google Drive',
};

const typeLabels: Record<string, string> = {
    calendar: 'Calendar Integration',
    email: 'Email Integration',
    crm: 'CRM Integration',
    drive: 'Cloud Storage',
};

function TokenStatusIndicator({ status, expiresAt }: { status: string; expiresAt: string | null }) {
    const config = {
        valid: { icon: <CheckCircle size={16} />, label: 'Valid', color: 'var(--accent-green)' },
        expiring_soon: { icon: <AlertTriangle size={16} />, label: 'Expiring Soon', color: 'var(--accent-orange)' },
        expired: { icon: <XCircle size={16} />, label: 'Expired', color: 'var(--accent-red)' },
        unknown: { icon: <Clock size={16} />, label: 'Unknown', color: 'var(--text-secondary)' },
    };
    const c = config[status as keyof typeof config] || config.unknown;

    let expiryText = '';
    if (expiresAt) {
        const expiryDate = new Date(expiresAt);
        const now = new Date();
        const diff = expiryDate.getTime() - now.getTime();
        if (diff > 0) {
            const hours = Math.floor(diff / (1000 * 60 * 60));
            if (hours > 24) {
                expiryText = `Expires in ${Math.floor(hours / 24)} days`;
            } else {
                expiryText = `Expires in ${hours} hours`;
            }
        } else {
            expiryText = 'Token expired';
        }
    }

    return (
        <div className="token-status-indicator" style={{ color: c.color }}>
            {c.icon}
            <span>{c.label}</span>
            {expiryText && <span className="expiry-text">({expiryText})</span>}
        </div>
    );
}

export function ConnectorDetailDrawer(props: ConnectorDetailDrawerProps) {
    const generation = useAuthGeneration();
    if (!props.connector) return null;
    return <OwnedConnectorDrawer key={`${generation}:${props.connector.tenant_id}:${props.connector.id}`}
        {...props} connector={props.connector} generation={generation} />;
}

function OwnedConnectorDrawer({ connector, onClose, onRefresh, generation }: ConnectorDetailDrawerProps & {
    connector: AdminConnectorItem; generation: number;
}) {
    const [detail, setDetail] = useState<AdminConnectorDetail | null>(null);
    const [loading, setLoading] = useState(true);
    const [actionLoading, setActionLoading] = useState<string | null>(null);
    const [showRevokeConfirm, setShowRevokeConfirm] = useState(false);
    // The API client never throws; a failed revoke/reconnect comes back as { error }.
    const [actionError, setActionError] = useState<string | null>(null);
    const [detailError, setDetailError] = useState<string | null>(null);
    const [receipt, setReceipt] = useState<string | null>(null);
    const mounted = useRef(false);
    const request = useRef(0);
    const mutation = useRef(false);
    const drawerRef = useRef<HTMLDivElement>(null);
    const owns = useCallback(() => mounted.current && generation === api.getAuthGeneration(), [generation]);
    useDialogFocus(!showRevokeConfirm, drawerRef, onClose, actionLoading !== null);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; request.current += 1; };
    }, []);

    const fetchDetail = useCallback(async () => {
        if (!owns()) return;
        const sequence = ++request.current;
        const current = () => owns() && sequence === request.current;
        setLoading(true);
        setDetailError(null);
        try {
            const response = await api.getConnectorDetail(connector.id);
            if (!current()) return;
            if (response.error) throw new Error(response.error.message);
            if (!response.data || response.data.id !== connector.id || response.data.tenant_id !== connector.tenant_id)
                throw new Error('The server returned details for a different connector. Refresh to verify this connection.');
            setDetail(response.data);
        } catch (error) {
            if (current()) setDetailError(error instanceof Error ? error.message : 'Connector details are unavailable.');
        } finally {
            if (current()) setLoading(false);
        }
    }, [connector.id, connector.tenant_id, owns]);

    useEffect(() => {
        void fetchDetail();
    }, [fetchDetail]);

    const canRefreshToken = detail !== null
        && ['active', 'expired', 'error'].includes(detail.status)
        && detail.refresh_state === 'idle';

    const handleReconnect = async () => {
        if (!owns() || mutation.current || !canRefreshToken || detailError || loading) return;
        mutation.current = true;
        setActionLoading('reconnect');
        setActionError(null);
        setReceipt(null);
        try {
            const res = await api.forceReconnect(connector.id);
            if (!owns()) return;
            if (res.error) throw new Error(res.error.message);
            if (!res.data?.success || res.data.connector_id !== connector.id)
                throw new Error('Token refresh was not confirmed. Reload details before trying again.');
            setReceipt('Token refresh confirmed.');
            await fetchDetail();
            if (owns()) onRefresh?.();
        } catch (error) {
            if (owns()) {
                setActionError(error instanceof Error ? error.message : 'Token refresh was not confirmed. Reload details before trying again.');
                setDetailError('Reload the connection to verify its state before another token refresh.');
            }
        } finally {
            if (owns()) { mutation.current = false; setActionLoading(null); }
        }
    };

    const handleRevokeClick = () => {
        if (!owns() || mutation.current || !detail || detailError || loading) return;
        setActionError(null);
        setShowRevokeConfirm(true);
    };

    const handleRevokeConfirm = async () => {
        if (!owns() || mutation.current || !showRevokeConfirm || !detail) return;
        mutation.current = true;
        setActionLoading('revoke');
        setActionError(null);
        try {
            const res = await api.revokeConnector(connector.id);
            if (!owns()) return;
            if (res.error) throw new Error(res.error.message);
            if (!res.data?.success || res.data.connector_id !== connector.id)
                throw new Error('Disconnection was not confirmed. Reload details to check its state.');
            const confirmed = res.data.provider_revoked === true && res.data.provider_revocation_status === 'confirmed';
            setDetail({ ...detail, status: 'disconnected', token_status: 'unknown',
                provider_revocation_status: confirmed ? 'confirmed' : 'unconfirmed' });
            setReceipt(confirmed ? 'Disabled locally. Provider access revocation confirmed.'
                : 'Disabled locally. Provider access revocation is unconfirmed; access at the provider may remain.');
            onRefresh?.();
            setShowRevokeConfirm(false);
        } catch (error) {
            if (owns()) setActionError(error instanceof Error ? error.message : 'Disconnection was not confirmed. Reload details to check its state.');
        } finally {
            if (owns()) { mutation.current = false; setActionLoading(null); }
        }
    };

    const data = detail || connector;
    const providerIcon = providerIcons[data.provider] || <Link2 size={20} />;
    const providerLabel = providerLabels[data.provider] || data.provider;
    const typeLabel = typeLabels[data.type] || data.type;

    return (
        <div className="drawer-overlay" onClick={() => { if (!mutation.current && !showRevokeConfirm) onClose(); }}>
            <div className="drawer drawer-right" ref={drawerRef} tabIndex={-1} role="dialog" aria-modal="true"
                aria-labelledby="connector-drawer-title" onClick={(e) => e.stopPropagation()}>
                <div className="drawer-header">
                    <div className="drawer-header-content">
                        <div className="drawer-icon">
                            {providerIcon}
                        </div>
                        <div>
                            <h2 id="connector-drawer-title" className="drawer-title">{data.name || providerLabel}</h2>
                            <p className="drawer-subtitle">{typeLabel}</p>
                        </div>
                    </div>
                    <button className="btn btn-icon" onClick={onClose} aria-label="Close connector details" disabled={actionLoading !== null}>
                        <X size={20} />
                    </button>
                </div>

                <div className="drawer-body">
                    {detailError && <div role="alert" className="error-banner"><p>{detailError}</p>
                        {detail && <p>Previously loaded details are shown. Actions require a successful refresh.</p>}
                        <button className="btn btn-secondary" disabled={actionLoading !== null || loading} onClick={() => void fetchDetail()}>Reload details</button>
                    </div>}
                    {loading ? (
                        <div className="loading-state">
                            <RefreshCw className="spinner" size={24} />
                            <span>Loading details...</span>
                        </div>
                    ) : detail ? (
                        <>
                            {/* Status Card */}
                            <div className="detail-section">
                                <h3 className="section-title">Connection Status</h3>
                                <div className="info-grid">
                                    <div className="info-item">
                                        <label>Status</label>
                                        <span className={`status-badge status-${data.status}`}>
                                            {data.status.charAt(0).toUpperCase() + data.status.slice(1)}
                                        </span>
                                    </div>
                                    <div className="info-item">
                                        <label>Token Status</label>
                                        <TokenStatusIndicator
                                            status={data.token_status}
                                            expiresAt={data.token_expires_at}
                                        />
                                    </div>
                                </div>
                                <div className="info-grid">
                                    <div className="info-item"><label>Refresh state</label><span>{detail.refresh_state?.replaceAll('_', ' ') ?? 'Unknown'}</span></div>
                                    <div className="info-item"><label>Provider revocation</label><span>{detail.provider_revocation_status?.replaceAll('_', ' ') ?? 'Unknown'}</span></div>
                                    <div className="info-item"><label>Recorded refreshes</label><span>{detail.refresh_count ?? 'Unknown'}</span></div>
                                </div>
                                {detail.refresh_state === 'uncertain' && <p role="status">The last provider refresh has an uncertain outcome. Do not retry it; the tenant may need to authorize the connection again.</p>}
                                {detail.refresh_state === 'in_flight' && <p role="status">A token refresh is in progress. Reload details to check its outcome.</p>}
                                {detail.status === 'pending' && <p role="status">Authorization is pending. The tenant must finish connecting this account before its token can be refreshed.</p>}
                            </div>

                            {/* Account Info */}
                            <div className="detail-section">
                                <h3 className="section-title">Account Details</h3>
                                <div className="info-grid">
                                    <div className="info-item full-width">
                                        <label><Building2 size={14} /> Tenant</label>
                                        <span>{data.tenant_name}</span>
                                    </div>
                                    {data.account_email && (
                                        <div className="info-item full-width">
                                            <label><Mail size={14} /> Account Email</label>
                                            <span>{data.account_email}</span>
                                        </div>
                                    )}
                                    <div className="info-item">
                                        <label><Calendar size={14} /> Created</label>
                                        <span>{new Date(data.created_at).toLocaleDateString()}</span>
                                    </div>
                                    <div className="info-item">
                                        <label><RefreshCw size={14} /> Last Refreshed</label>
                                        <span>
                                            {data.last_refreshed_at
                                                ? new Date(data.last_refreshed_at).toLocaleString()
                                                : 'Never'}
                                        </span>
                                    </div>
                                </div>
                            </div>

                            {/* OAuth Scopes */}
                            {detail?.scopes && detail.scopes.length > 0 && (
                                <div className="detail-section">
                                    <h3 className="section-title">
                                        <Shield size={16} /> OAuth Scopes
                                    </h3>
                                    <div className="scopes-list">
                                        {detail.scopes.map((scope, index) => (
                                            <div key={index} className="scope-item">
                                                <Key size={12} />
                                                <span>{scope}</span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Error Message */}
                            {detail?.error_message && (
                                <div className="detail-section">
                                    <h3 className="section-title error-title">
                                        <XCircle size={16} /> Error Details
                                    </h3>
                                    <div className="error-message">
                                        {detail.error_message}
                                    </div>
                                </div>
                            )}
                        </>
                    ) : null}
                </div>

                {receipt && <div className="info-banner" role="status">{receipt}</div>}

                {actionError && !showRevokeConfirm && (
                    <div className="error-banner" role="alert">
                        <p>{actionError}</p>
                    </div>
                )}

                {/* Actions Footer */}
                <div className="drawer-footer">
                    {data.status !== 'disconnected' && (
                        <>
                            <button
                                className="btn btn-secondary"
                                onClick={handleReconnect}
                                disabled={actionLoading !== null || loading || !canRefreshToken || Boolean(detailError)}
                            >
                                {actionLoading === 'reconnect' ? (
                                    <RefreshCw className="spinner" size={16} />
                                ) : (
                                    <RefreshCw size={16} />
                                )}
                                Refresh token
                            </button>
                            <button
                                className="btn btn-danger"
                                onClick={handleRevokeClick}
                                disabled={actionLoading !== null || loading || !detail || Boolean(detailError)}
                            >
                                {actionLoading === 'revoke' ? (
                                    <RefreshCw className="spinner" size={16} />
                                ) : (
                                    <Ban size={16} />
                                )}
                                Revoke Access
                            </button>
                        </>
                    )}
                    <button className="btn btn-secondary" disabled={loading || actionLoading !== null} onClick={() => void fetchDetail()}>Reload details</button>
                    <button className="btn btn-primary" onClick={onClose} disabled={actionLoading !== null}>
                        <ExternalLink size={16} />
                        Close
                    </button>
                </div>
            </div>

            {/* Revoke Confirmation Modal */}
            <ConfirmationModal
                isOpen={showRevokeConfirm}
                title="Revoke Connector Access"
                message="Disable this integration locally and request revocation at the provider? Provider confirmation will be shown separately. The tenant will need to reconnect."
                confirmLabel="Revoke Access"
                cancelLabel="Cancel"
                variant="danger"
                onConfirm={handleRevokeConfirm}
                onCancel={() => { setShowRevokeConfirm(false); setActionError(null); }}
                loading={actionLoading === 'revoke'}
                error={showRevokeConfirm ? actionError : null}
            />
        </div>
    );
}
