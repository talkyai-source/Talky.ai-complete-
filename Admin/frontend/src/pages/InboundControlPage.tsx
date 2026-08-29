import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    AlertTriangle,
    ArrowRightLeft,
    Building2,
    CheckCircle2,
    Clock3,
    PauseCircle,
    PhoneIncoming,
    Power,
    RefreshCw,
    Search,
    ShieldAlert,
} from 'lucide-react';

import { Header } from '../components/Header';
import { Sidebar } from '../components/Sidebar';
import { useAuth } from '../lib/auth';
import { api } from '../lib/api';
import type {
    AdminInboundAssignment,
    AdminInboundReassignmentRequest,
    AdminInboundRuntimeControls,
    TenantListItem,
} from '../lib/api';

type AssignmentAction = 'quarantine' | 'unquarantine';

interface PendingAssignmentAction {
    assignment: AdminInboundAssignment;
    action: AssignmentAction;
}

function newMutationKey(scope: string): string {
    const id = typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    return `admin-inbound:${scope}:${id}`;
}

function formatDate(value: string | null | undefined): string {
    if (!value) return 'Never';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function StatusBadge({ value }: { value: string }) {
    const safe = value.toLowerCase().replace(/[^a-z0-9_-]/g, '');
    return <span className={`call-status-badge status-${safe}`}>{value.replaceAll('_', ' ')}</span>;
}

export function InboundControlPage() {
    const { user } = useAuth();
    const [assignments, setAssignments] = useState<AdminInboundAssignment[]>([]);
    const [pendingReassignments, setPendingReassignments] = useState<AdminInboundReassignmentRequest[]>([]);
    const [tenants, setTenants] = useState<TenantListItem[]>([]);
    const [controls, setControls] = useState<AdminInboundRuntimeControls | null>(null);
    const [draftControls, setDraftControls] = useState<AdminInboundRuntimeControls | null>(null);
    const [counts, setCounts] = useState({ active: 0, paused: 0, quarantined: 0, denied: 0 });
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [success, setSuccess] = useState<string | null>(null);
    const [search, setSearch] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');
    const [tenantId, setTenantId] = useState('');
    const [status, setStatus] = useState('');
    const [controlReason, setControlReason] = useState('');
    const [pendingAction, setPendingAction] = useState<PendingAssignmentAction | null>(null);
    const [actionReason, setActionReason] = useState('');
    const [reassigning, setReassigning] = useState<AdminInboundAssignment | null>(null);
    const [targetTenantId, setTargetTenantId] = useState('');
    const [targetCampaignId, setTargetCampaignId] = useState('');
    const [reassignmentReason, setReassignmentReason] = useState('');
    const mutationKeys = useRef(new Map<string, string>());

    function mutationToken(scope: string, payload: unknown) {
        const signature = `${scope}:${JSON.stringify(payload)}`;
        let key = mutationKeys.current.get(signature);
        if (!key) {
            key = newMutationKey(scope);
            mutationKeys.current.set(signature, key);
        }
        return { key, signature };
    }

    function completeMutation(signature: string) {
        mutationKeys.current.delete(signature);
    }

    const isPlatformAdmin = Boolean(user && [
        'platform_admin',
        'super_admin',
    ].includes(user.role));

    useEffect(() => {
        const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 300);
        return () => window.clearTimeout(timer);
    }, [search]);

    const loadData = useCallback(async () => {
        setLoading(true);
        setError(null);
        const [overviewResponse, assignmentResponse, tenantResponse, reassignmentResponse] = await Promise.all([
            api.getAdminInboundOverview(),
            api.getAdminInboundAssignments({
                tenant_id: tenantId || undefined,
                status: status || undefined,
                search: debouncedSearch || undefined,
            }),
            api.getTenants(),
            api.getAdminInboundReassignments(),
        ]);

        const firstError = overviewResponse.error
            ?? assignmentResponse.error
            ?? tenantResponse.error
            ?? reassignmentResponse.error;
        if (firstError) {
            setError(firstError.message);
        }
        if (overviewResponse.data) {
            const overview = overviewResponse.data;
            setCounts({
                active: overview.active_assignments,
                paused: overview.paused_assignments,
                quarantined: overview.quarantined_assignments,
                denied: overview.denied_last_24h,
            });
            setControls(overview.controls);
            setDraftControls(overview.controls);
        }
        if (assignmentResponse.data) setAssignments(assignmentResponse.data.items);
        if (tenantResponse.data) setTenants(tenantResponse.data);
        if (reassignmentResponse.data) setPendingReassignments(reassignmentResponse.data.items);
        setLoading(false);
    }, [debouncedSearch, status, tenantId]);

    useEffect(() => {
        // The request is intentionally synchronized with the complete filter snapshot.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        void loadData();
    }, [loadData]);

    const controlsChanged = useMemo(() => {
        if (!controls || !draftControls) return false;
        return controls.inbound_enabled !== draftControls.inbound_enabled
            || controls.recording_enabled !== draftControls.recording_enabled
            || controls.transfer_enabled !== draftControls.transfer_enabled
            || controls.settlement_enabled !== draftControls.settlement_enabled;
    }, [controls, draftControls]);

    const saveControls = async () => {
        if (!controls || !draftControls || !controlsChanged) return;
        if (controlReason.trim().length < 8) {
            setError('Enter an operational reason of at least 8 characters before changing a kill switch.');
            return;
        }
        setSaving(true);
        setError(null);
        setSuccess(null);
        const payload = {
            inbound_enabled: draftControls.inbound_enabled,
            recording_enabled: draftControls.recording_enabled,
            // New transfer enablement is intentionally impossible. A legacy
            // true value can only transition to false.
            transfer_enabled: controls.transfer_enabled && draftControls.transfer_enabled,
            settlement_enabled: draftControls.settlement_enabled,
            expected_version: controls.version,
            reason: controlReason.trim(),
        };
        const operation = mutationToken('controls', payload);
        const response = await api.updateAdminInboundControls(payload, operation.key);
        if (response.error) {
            setError(response.error.code === 'VERSION_CONFLICT'
                ? 'Runtime controls changed in another session. Reloaded the latest values; review and try again.'
                : response.error.message);
        } else {
            completeMutation(operation.signature);
            setSuccess('Inbound runtime controls updated and recorded in the audit log.');
            setControlReason('');
        }
        setSaving(false);
        await loadData();
    };

    const runAssignmentAction = async () => {
        if (!pendingAction || actionReason.trim().length < 8) {
            setError('Enter an operational reason of at least 8 characters.');
            return;
        }
        setSaving(true);
        setError(null);
        setSuccess(null);
        const { assignment, action } = pendingAction;
        const payload = {
            assignment_id: assignment.id,
            expected_version: assignment.version,
            action,
            reason: actionReason.trim(),
        };
        const operation = mutationToken(`assignment:${action}`, payload);
        const response = action === 'quarantine'
            ? await api.quarantineAdminInboundAssignment(
                assignment.id,
                assignment.version,
                actionReason.trim(),
                operation.key,
            )
            : await api.unquarantineAdminInboundAssignment(
                assignment.id,
                assignment.version,
                actionReason.trim(),
                operation.key,
            );
        if (response.error) {
            setError(response.error.message);
        } else {
            completeMutation(operation.signature);
            setSuccess(action === 'quarantine'
                ? `${assignment.masked_did} is quarantined; new calls will fail closed.`
                : `${assignment.masked_did} was released from quarantine.`);
            setPendingAction(null);
            setActionReason('');
        }
        setSaving(false);
        await loadData();
    };

    const requestReassignment = async () => {
        if (!reassigning || !targetTenantId || !targetCampaignId.trim() || reassignmentReason.trim().length < 8) {
            setError('Target tenant, target inbound campaign, and an 8-character reason are required.');
            return;
        }
        setSaving(true);
        setError(null);
        const payload = {
            assignment_id: reassigning.id,
            target_tenant_id: targetTenantId,
            target_campaign_id: targetCampaignId.trim(),
            expected_version: reassigning.version,
            reason: reassignmentReason.trim(),
        };
        const operation = mutationToken('reassignment', payload);
        const response = await api.requestAdminInboundReassignment(payload, operation.key);
        if (response.error) {
            setError(response.error.message);
        } else {
            completeMutation(operation.signature);
            setSuccess('Reassignment requested. A different platform administrator must approve it.');
            setReassigning(null);
            setTargetTenantId('');
            setTargetCampaignId('');
            setReassignmentReason('');
        }
        setSaving(false);
        await loadData();
    };

    const approveReassignment = async (request: AdminInboundReassignmentRequest) => {
        setSaving(true);
        setError(null);
        const reason = 'Approved after independent DID ownership and target readiness review.';
        const operation = mutationToken('approve-reassignment', { request_id: request.id, reason });
        const response = await api.approveAdminInboundReassignment(
            request.id,
            reason,
            operation.key,
        );
        if (response.error) {
            setError(response.error.message);
        } else {
            completeMutation(operation.signature);
            setSuccess('Reassignment approved and applied atomically.');
        }
        setSaving(false);
        await loadData();
    };

    if (!isPlatformAdmin) {
        return (
            <div className="access-denied">
                <ShieldAlert size={42} />
                <h1>Platform administrator access required</h1>
                <p>Global inbound switches and cross-tenant DID ownership cannot be changed by tenant or partner administrators.</p>
            </div>
        );
    }

    return (
        <div className="app-layout">
            <Sidebar />
            <main className="main-content">
                <Header />
                <div className="dashboard-content inbound-control-page">
                    <div className="page-header">
                        <div className="page-header-icon"><PhoneIncoming /></div>
                        <div>
                            <h1 className="page-title">Inbound Control</h1>
                            <p className="page-description">Fail-closed ingress controls, DID ownership, quarantine, and four-eye reassignment.</p>
                        </div>
                        <button className="btn btn-secondary" onClick={() => void loadData()} disabled={loading || saving}>
                            <RefreshCw size={16} className={loading ? 'spinning' : ''} /> Refresh
                        </button>
                    </div>

                    {error && <div className="error-banner" role="alert"><p>{error}</p></div>}
                    {success && <div className="success-banner" role="status">{success}</div>}

                    <div className="stats-grid stats-grid-4 inbound-stat-grid" aria-label="Inbound status summary">
                        <div className="stat-card"><CheckCircle2 /><div><span>Active DIDs</span><strong>{counts.active}</strong></div></div>
                        <div className="stat-card"><PauseCircle /><div><span>Paused</span><strong>{counts.paused}</strong></div></div>
                        <div className="stat-card"><ShieldAlert /><div><span>Quarantined</span><strong>{counts.quarantined}</strong></div></div>
                        <div className="stat-card"><AlertTriangle /><div><span>Denied (24h)</span><strong>{counts.denied}</strong></div></div>
                    </div>

                    <section className="card inbound-control-section" aria-labelledby="runtime-controls-heading">
                        <div className="card-header">
                            <div>
                                <h2 id="runtime-controls-heading" className="card-title">Independent runtime kill switches</h2>
                                <p className="section-help">Admission remains the master switch. Recording, transfer, and settlement can be stopped independently.</p>
                            </div>
                            <StatusBadge value={draftControls?.inbound_enabled ? 'inbound enabled' : 'inbound disabled'} />
                        </div>
                        <div className="card-body">
                            {draftControls && controls ? (
                                <>
                                    <div className="inbound-switch-grid">
                                        {([
                                            ['inbound_enabled', 'Inbound admission', 'Accept eligible PSTN calls after deterministic pre-answer routing.'],
                                            ['recording_enabled', 'Recording', 'Permit recording only after disclosure succeeds. Turning this off blocks new recordings, clears locally owned live buffers immediately, and reaches every media worker within 30 seconds. Upload also rechecks the switch.'],
                                            ['transfer_enabled', 'Human transfer', 'Permit allowlisted transfers within hop and duration limits.'],
                                            ['settlement_enabled', 'Billing settlement', 'Finalize reserved usage into the append-only ledger.'],
                                        ] as const).map(([key, label, description]) => {
                                            const transferControl = key === 'transfer_enabled';
                                            const transferCanOnlyBeDisabled = transferControl && draftControls.transfer_enabled;
                                            const disabled = saving || (transferControl && !transferCanOnlyBeDisabled);
                                            const visibleLabel = transferControl
                                                ? draftControls.transfer_enabled
                                                    ? `${label} · disable only`
                                                    : `${label} · unavailable`
                                                : label;
                                            const visibleDescription = transferControl
                                                ? 'New enablement is blocked. If this legacy switch is on, it can only be turned off.'
                                                : description;
                                            return (
                                                <label className="inbound-switch" key={key} title={transferControl ? visibleDescription : undefined}>
                                                    <input
                                                        type="checkbox"
                                                        checked={draftControls[key]}
                                                        onChange={(event) => {
                                                            if (transferControl && event.target.checked) return;
                                                            setDraftControls({ ...draftControls, [key]: event.target.checked });
                                                        }}
                                                        disabled={disabled}
                                                    />
                                                    <span><strong>{visibleLabel}</strong><small>{visibleDescription}</small></span>
                                                </label>
                                            );
                                        })}
                                    </div>
                                    <div className="inbound-control-save">
                                        <div className="form-group">
                                            <label htmlFor="control-reason">Required audit reason</label>
                                            <input
                                                id="control-reason"
                                                value={controlReason}
                                                onChange={(event) => setControlReason(event.target.value)}
                                                placeholder="Incident, rollout, or approval reference"
                                                minLength={8}
                                            />
                                        </div>
                                        <button className="btn btn-primary" disabled={!controlsChanged || saving} onClick={() => void saveControls()}>
                                            <Power size={16} /> Apply version {controls.version + 1}
                                        </button>
                                    </div>
                                    <p className="control-version">Current version {controls.version} · last changed {formatDate(controls.updated_at)}</p>
                                </>
                            ) : <p>Loading runtime controls…</p>}
                        </div>
                    </section>

                    <section className="card inbound-control-section" aria-labelledby="did-inventory-heading">
                        <div className="card-header">
                            <div>
                                <h2 id="did-inventory-heading" className="card-title">DID assignment inventory</h2>
                                <p className="section-help">Only masked numbers are shown. Active ownership is globally unique and versioned.</p>
                            </div>
                        </div>
                        <div className="card-body">
                            <div className="table-toolbar">
                                <div className="search-box"><Search size={18} /><input className="search-input" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="DID, tenant, campaign, or error…" /></div>
                                <select className="filter-select" value={tenantId} onChange={(e) => setTenantId(e.target.value)} aria-label="Filter inbound assignments by tenant">
                                    <option value="">All tenants</option>
                                    {tenants.map((tenant) => <option key={tenant.id} value={tenant.id}>{tenant.business_name}</option>)}
                                </select>
                                <select className="filter-select" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter inbound assignments by status">
                                    <option value="">All states</option>
                                    <option value="active">Active</option><option value="paused">Paused</option><option value="quarantined">Quarantined</option><option value="archived">Archived</option>
                                </select>
                            </div>
                            <div className="table-container">
                                {loading ? <div className="table-loading"><div className="loading-spinner" /><p>Loading DID inventory…</p></div>
                                    : assignments.length === 0 ? <div className="empty-state"><PhoneIncoming size={44} /><h3>No assignments found</h3><p>No DID assignments match these filters.</p></div>
                                        : (
                                            <table className="data-table inbound-assignment-table">
                                                <thead><tr><th>DID / tenant</th><th>Campaign</th><th>State</th><th>Readiness</th><th>Last activity</th><th>Version</th><th>Controls</th></tr></thead>
                                                <tbody>{assignments.map((assignment) => (
                                                    <tr key={assignment.id}>
                                                        <td><div className="stacked-cell"><strong>{assignment.masked_did}</strong><span><Building2 size={13} /> {assignment.tenant_name}</span></div></td>
                                                        <td><div className="stacked-cell"><strong>{assignment.campaign_name}</strong><span>{assignment.campaign_id.slice(0, 8)}…</span></div></td>
                                                        <td><StatusBadge value={assignment.status} /></td>
                                                        <td><StatusBadge value={assignment.readiness.ready ? 'ready' : 'blocked'} />{assignment.readiness.blockers[0] && <p className="transcript-error" title={assignment.readiness.blockers[0].remediation}>{assignment.readiness.blockers[0].message}</p>}{assignment.last_error && <p className="transcript-error">{assignment.last_error}</p>}</td>
                                                        <td><span className="stacked-cell"><span><Clock3 size={13} /> {formatDate(assignment.last_call_at)}</span></span></td>
                                                        <td>route {assignment.version}<br /><span className="storage-kind">config {assignment.config_version ?? '—'}</span></td>
                                                        <td><div className="row-actions vertical">
                                                            {assignment.status === 'quarantined' ? (
                                                                <button className="btn btn-secondary btn-sm" onClick={() => { setPendingAction({ assignment, action: 'unquarantine' }); setActionReason(''); }} disabled={saving}>Unquarantine</button>
                                                            ) : assignment.status !== 'archived' && (
                                                                <button className="btn btn-danger btn-sm" onClick={() => { setPendingAction({ assignment, action: 'quarantine' }); setActionReason(''); }} disabled={saving}>Quarantine</button>
                                                            )}
                                                            {['paused', 'quarantined'].includes(assignment.status) && <button className="btn btn-secondary btn-sm" onClick={() => setReassigning(assignment)} disabled={saving}><ArrowRightLeft size={14} /> Reassign</button>}
                                                        </div></td>
                                                    </tr>
                                                ))}</tbody>
                                            </table>
                                        )}
                            </div>
                        </div>
                    </section>

                    {pendingAction && (
                        <section className="card inbound-action-panel" aria-labelledby="assignment-action-heading">
                            <div className="card-body">
                                <h2 id="assignment-action-heading" className="card-title">{pendingAction.action === 'quarantine' ? 'Quarantine' : 'Unquarantine'} {pendingAction.assignment.masked_did}</h2>
                                <p className="section-help">This is a version-checked, audited operation. Quarantine immediately denies new calls before answer.</p>
                                <div className="form-group"><label htmlFor="assignment-action-reason">Required reason</label><textarea id="assignment-action-reason" value={actionReason} onChange={(e) => setActionReason(e.target.value)} rows={3} /></div>
                                <div className="row-actions"><button className="btn btn-secondary" onClick={() => setPendingAction(null)} disabled={saving}>Cancel</button><button className={pendingAction.action === 'quarantine' ? 'btn btn-danger' : 'btn btn-primary'} onClick={() => void runAssignmentAction()} disabled={saving}>Confirm {pendingAction.action}</button></div>
                            </div>
                        </section>
                    )}

                    {reassigning && (
                        <section className="card inbound-action-panel" aria-labelledby="reassign-heading">
                            <div className="card-body">
                                <h2 id="reassign-heading" className="card-title">Request reassignment of {reassigning.masked_did}</h2>
                                <p className="section-help">The assignment stays fail-closed until a different platform administrator approves the request.</p>
                                <div className="form-row"><div className="form-group"><label htmlFor="target-tenant">Target tenant</label><select id="target-tenant" value={targetTenantId} onChange={(e) => setTargetTenantId(e.target.value)}><option value="">Select target</option>{tenants.filter((tenant) => tenant.id !== reassigning.tenant_id).map((tenant) => <option key={tenant.id} value={tenant.id}>{tenant.business_name}</option>)}</select></div><div className="form-group"><label htmlFor="target-campaign">Target inbound campaign ID</label><input id="target-campaign" value={targetCampaignId} onChange={(e) => setTargetCampaignId(e.target.value)} placeholder="UUID" /></div></div>
                                <div className="form-group"><label htmlFor="reassignment-reason">Required reason</label><textarea id="reassignment-reason" value={reassignmentReason} onChange={(e) => setReassignmentReason(e.target.value)} rows={3} /></div>
                                <div className="row-actions"><button className="btn btn-secondary" onClick={() => setReassigning(null)} disabled={saving}>Cancel</button><button className="btn btn-warning" onClick={() => void requestReassignment()} disabled={saving}><ArrowRightLeft size={15} /> Submit for approval</button></div>
                            </div>
                        </section>
                    )}

                    <section className="card inbound-control-section" aria-labelledby="pending-reassignments-heading">
                        <div className="card-header"><div><h2 id="pending-reassignments-heading" className="card-title">Pending four-eye approvals</h2><p className="section-help">The requester can never approve their own reassignment.</p></div></div>
                        <div className="card-body">
                            {pendingReassignments.length === 0 ? <p className="no-data-inline">No pending reassignment requests.</p> : (
                                <div className="pending-reassignment-list">{pendingReassignments.map((request) => {
                                    const ownRequest = request.requested_by === user?.id;
                                    return <article key={request.id} className="pending-reassignment"><div><strong>{request.assignment_id.slice(0, 8)}…</strong><p>{request.source_tenant_id.slice(0, 8)}… → {request.target_tenant_id.slice(0, 8)}…</p><small>{request.reason} · requested {formatDate(request.requested_at)}</small></div><button className="btn btn-primary btn-sm" disabled={saving || ownRequest} title={ownRequest ? 'A different platform administrator must approve this request.' : undefined} onClick={() => void approveReassignment(request)}>{ownRequest ? 'Second admin required' : 'Approve reassignment'}</button></article>;
                                })}</div>
                            )}
                        </div>
                    </section>
                </div>
            </main>
        </div>
    );
}
