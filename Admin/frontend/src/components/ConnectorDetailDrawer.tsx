import { useState, useEffect } from 'react';
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

export function ConnectorDetailDrawer({ connector, onClose, onRefresh }: ConnectorDetailDrawerProps) {
    const [detail, setDetail] = useState<AdminConnectorDetail | null>(null);
    const [loading, setLoading] = useState(false);
    const [actionLoading, setActionLoading] = useState<string | null>(null);
    const [showRevokeConfirm, setShowRevokeConfirm] = useState(false);

    useEffect(() => {
        if (connector) {
            fetchDetail();
        }
    }, [connector]);

    const fetchDetail = async () => {
        if (!connector) return;
        setLoading(true);
        try {
            const response = await api.getConnectorDetail(connector.id);
            if (response.data) {
                setDetail(response.data);
            }
        } catch (error) {
            console.error('Failed to fetch connector detail:', error);
        } finally {
            setLoading(false);
        }
    };

    const handleReconnect = async () => {
        if (!connector) return;
        setActionLoading('reconnect');
        try {
            await api.forceReconnect(connector.id);
            await fetchDetail();
            onRefresh?.();
        } catch (error) {
            console.error('Failed to reconnect:', error);
        } finally {
            setActionLoading(null);
        }
    };

    const handleRevokeClick = () => {
        setShowRevokeConfirm(true);
    };

    const handleRevokeConfirm = async () => {
        if (!connector) return;
        setActionLoading('revoke');
        try {
            await api.revokeConnector(connector.id);
            onRefresh?.();
            setShowRevokeConfirm(false);
            onClose();
        } catch (error) {
            console.error('Failed to revoke:', error);
        } finally {
            setActionLoading(null);
        }
    };

    if (!connector) return null;

    const data = detail || connector;
    const providerIcon = providerIcons[data.provider] || <Link2 size={20} />;
    const providerLabel = providerLabels[data.provider] || data.provider;
    const typeLabel = typeLabels[data.type] || data.type;

    return (
        <div className="drawer-overlay" onClick={onClose}>
            <div className="drawer drawer-right" onClick={(e) => e.stopPropagation()}>
                <div className="drawer-header">
                    <div className="drawer-header-content">
                        <div className="drawer-icon">
                            {providerIcon}
                        </div>
                        <div>
                            <h2 className="drawer-title">{data.name || providerLabel}</h2>
                            <p className="drawer-subtitle">{typeLabel}</p>
                        </div>
                    </div>
                    <button className="btn btn-icon" onClick={onClose}>
                        <X size={20} />
                    </button>
                </div>

                <div className="drawer-body">
                    {loading ? (
                        <div className="loading-state">
                            <RefreshCw className="spinner" size={24} />
                            <span>Loading details...</span>
                        </div>
                    ) : (
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
                    )}
                </div>

                {/* Actions Footer */}
                <div className="drawer-footer">
                    {data.status !== 'disconnected' && (
                        <>
                            <button
                                className="btn btn-secondary"
                                onClick={handleReconnect}
                                disabled={actionLoading !== null}
                            >
                                {actionLoading === 'reconnect' ? (
                                    <RefreshCw className="spinner" size={16} />
                                ) : (
                                    <RefreshCw size={16} />
                                )}
                                Force Reconnect
                            </button>
                            <button
                                className="btn btn-danger"
                                onClick={handleRevokeClick}
                                disabled={actionLoading !== null}
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
                    <button className="btn btn-primary" onClick={onClose}>
                        <ExternalLink size={16} />
                        Close
                    </button>
                </div>
            </div>

            {/* Revoke Confirmation Modal */}
            <ConfirmationModal
                isOpen={showRevokeConfirm}
                title="Revoke Connector Access"
                message="Are you sure you want to revoke this connector? This will disconnect the integration and the tenant will need to reconnect."
                confirmLabel="Revoke Access"
                cancelLabel="Cancel"
                variant="danger"
                onConfirm={handleRevokeConfirm}
                onCancel={() => setShowRevokeConfirm(false)}
                loading={actionLoading === 'revoke'}
            />
        </div>
    );
}
