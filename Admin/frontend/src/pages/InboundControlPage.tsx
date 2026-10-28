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
import { TenantFilter } from '../components/TenantFilter';
import { useAuth } from '../lib/auth';
import { api } from '../lib/api';
import { useAuthGeneration } from '../lib/use-auth-generation';
import type {
    AdminInboundAssignment,
    AdminInboundReassignmentRequest,
    AdminInboundRuntimeControls,
} from '../lib/api';

type AssignmentAction = 'quarantine' | 'unquarantine';

interface PendingAssignmentAction {
    assignment: AdminInboundAssignment;
    action: AssignmentAction;
}

interface DecisionAttempt {
    kind: 'approve' | 'cancel';
    request: AdminInboundReassignmentRequest;
    reason: string;
    key: string;
    signature: string;
}

interface RequestSelection {
    original: AdminInboundReassignmentRequest;
    current: AdminInboundReassignmentRequest;
    reading: boolean;
    ready: boolean;
    confirmed: boolean;
}

interface CancellationReview {
    selection: RequestSelection;
    reason: string;
}

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const identifier = (value: unknown): value is string => typeof value === 'string' && uuid.test(value);
const timestamp = (value: unknown): value is string => typeof value === 'string'
    && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));

function validReassignment(value: unknown): value is AdminInboundReassignmentRequest {
    if (!value || typeof value !== 'object') return false;
    const row = value as AdminInboundReassignmentRequest;
    if (![row.id, row.assignment_id, row.source_tenant_id, row.target_tenant_id, row.target_campaign_id, row.requested_by].every(identifier)
        || !['pending', 'approved', 'rejected', 'cancelled'].includes(row.status)
        || typeof row.reason !== 'string' || !row.reason.trim() || !timestamp(row.requested_at)
        || (row.approved_assignment_id != null && !identifier(row.approved_assignment_id))
        || (row.approved_by != null && !identifier(row.approved_by))
        || (row.decision_reason != null && typeof row.decision_reason !== 'string')
        || (row.decided_at != null && !timestamp(row.decided_at))) return false;
    if (row.status === 'pending') return row.approved_assignment_id == null && row.approved_by == null
        && row.decision_reason == null && row.decided_at == null;
    if (row.status === 'approved') return identifier(row.approved_assignment_id) && identifier(row.approved_by)
        && row.approved_by !== row.requested_by && typeof row.decision_reason === 'string'
        && Boolean(row.decision_reason.trim()) && timestamp(row.decided_at)
        && Date.parse(row.decided_at) >= Date.parse(row.requested_at);
    if (row.status === 'cancelled') return row.approved_assignment_id == null && row.approved_by == null
        && typeof row.decision_reason === 'string' && Boolean(row.decision_reason.trim())
        && timestamp(row.decided_at) && Date.parse(row.decided_at) >= Date.parse(row.requested_at);
    return true;
}

function sameRequest(left: AdminInboundReassignmentRequest, right: AdminInboundReassignmentRequest): boolean {
    return (['id', 'assignment_id', 'source_tenant_id', 'target_tenant_id', 'target_campaign_id',
        'requested_by', 'requested_at', 'reason'] as const).every(key => left[key] === right[key]);
}

function sameDecision(left: AdminInboundReassignmentRequest, right: AdminInboundReassignmentRequest): boolean {
    return sameRequest(left, right) && (['status', 'approved_assignment_id', 'approved_by', 'decision_reason', 'decided_at'] as const)
        .every(key => (left[key] ?? null) === (right[key] ?? null));
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
    const [reassignments, setReassignments] = useState<AdminInboundReassignmentRequest[]>([]);
    const [requestStatus, setRequestStatus] = useState('all');
    const [requestCursors, setRequestCursors] = useState<(string | null)[]>([null]);
    const [nextRequestCursor, setNextRequestCursor] = useState<string | null>(null);
    const [historyLoading, setHistoryLoading] = useState(true);
    const [historyError, setHistoryError] = useState<string | null>(null);
    const [controls, setControls] = useState<AdminInboundRuntimeControls | null>(null);
    const [draftControls, setDraftControls] = useState<AdminInboundRuntimeControls | null>(null);
    const [counts, setCounts] = useState({ active: 0, paused: 0, quarantined: 0, denied: 0 });
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [readError, setReadError] = useState<string | null>(null);
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
    const [reviewing, setReviewing] = useState<AdminInboundReassignmentRequest | null>(null);
    const [reviewReason, setReviewReason] = useState('');
    const [reviewLoading, setReviewLoading] = useState(false);
    const [reviewError, setReviewError] = useState<string | null>(null);
    const [reviewAttempt, setReviewAttempt] = useState<DecisionAttempt | null>(null);
    const [cancellationReview, setCancellationReview] = useState<CancellationReview | null>(null);
    const [reviewTenants, setReviewTenants] = useState<{ requestId: string; source: string; target: string; error: string | null; loading: boolean } | null>(null);
    const [refreshIndex, setRefreshIndex] = useState(0);
    const mutationKeys = useRef(new Map<string, string>());
    const draftDirty = useRef(false);
    const readGeneration = useRef(0);
    const historyGeneration = useRef(0);
    const reviewGeneration = useRef(0);
    const reviewSelection = useRef<RequestSelection | null>(null);
    const decisionAttempts = useRef(new Map<string, DecisionAttempt>());
    const cancellationReviewRef = useRef<CancellationReview | null>(null);
    const cancellationReasonElement = useRef<HTMLTextAreaElement>(null);
    const cancellationConfirmElement = useRef<HTMLButtonElement>(null);
    const cancellationTriggerElement = useRef<HTMLButtonElement>(null);
    const cancellationFocus = useRef<'entry' | 'return' | null>(null);
    const assignmentReasonElement = useRef<HTMLTextAreaElement>(null);
    const reassignmentPanelElement = useRef<HTMLElement>(null);
    const reviewHeadingElement = useRef<HTMLHeadingElement>(null);
    const inventoryFilterElement = useRef<HTMLSelectElement>(null);
    const historyFilterElement = useRef<HTMLSelectElement>(null);
    const inlineOrigins = useRef<{ assignment: HTMLButtonElement | null; reassignment: HTMLButtonElement | null; review: HTMLButtonElement | null }>({ assignment: null, reassignment: null, review: null });
    const inlineFocus = useRef<{ kind: 'assignment' | 'reassignment' | 'review'; direction: 'entry' | 'return' } | null>(null);
    const reviewReasonRef = useRef('');
    const mutationInFlight = useRef(false);
    const ownerRef = useRef<{ active: boolean; principal: string | undefined } | null>(null);
    const session = useAuthGeneration();
    const principal = user?.id;
    const renderedSelection = reviewSelection.current;
    const renderedHistoryGeneration = historyGeneration.current;

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

    function clearCancellationReview(restoreFocus = false) {
        cancellationFocus.current = restoreFocus ? 'return' : null;
        cancellationReviewRef.current = null;
        setCancellationReview(null);
    }

    const isPlatformAdmin = Boolean(user && [
        'platform_admin',
        'super_admin',
    ].includes(user.role));

    useEffect(() => {
        const owner = { active: true, principal };
        ownerRef.current = owner;
        mutationInFlight.current = false;
        mutationKeys.current.clear();
        draftDirty.current = false;
        // Identity changes retire both outstanding reads and local mutation drafts.
        setAssignments([]);
        setReassignments([]);
        setRequestStatus('all'); setRequestCursors([null]); setNextRequestCursor(null);
        setHistoryError(null); setHistoryLoading(true);
        reviewSelection.current = null; decisionAttempts.current.clear(); reviewGeneration.current += 1;
        inlineFocus.current = null;
        inlineOrigins.current = { assignment: null, reassignment: null, review: null };
        clearCancellationReview();
        setReviewAttempt(null); setReviewLoading(false); setReviewError(null); reviewReasonRef.current = '';
        setControls(null);
        setDraftControls(null);
        setPendingAction(null);
        setReassigning(null);
        setReviewing(null);
        setControlReason('');
        setActionReason('');
        setReassignmentReason('');
        setReviewReason('');
        setError(null);
        setReadError(null);
        setSuccess(null);
        setSaving(false);
        return () => { owner.active = false; readGeneration.current += 1; historyGeneration.current += 1; reviewGeneration.current += 1; };
    }, [principal, session]);

    useEffect(() => {
        const focus = cancellationFocus.current;
        cancellationFocus.current = null;
        if (!ownerRef.current?.active || session !== api.getAuthGeneration()) return;
        if (focus === 'entry' && cancellationReview === cancellationReviewRef.current) {
            const target = cancellationReasonElement.current?.disabled
                ? cancellationConfirmElement.current : cancellationReasonElement.current;
            target?.focus();
        } else if (focus === 'return' && !cancellationReview) {
            cancellationTriggerElement.current?.focus();
        }
    }, [cancellationReview, session]);

    useEffect(() => {
        const focus = inlineFocus.current;
        inlineFocus.current = null;
        if (!focus || !ownerRef.current?.active || ownerRef.current.principal !== principal || session !== api.getAuthGeneration()) return;
        const open = focus.kind === 'assignment' ? pendingAction : focus.kind === 'reassignment' ? reassigning : reviewing;
        if (focus.direction === 'entry' && open) {
            const target = focus.kind === 'assignment' ? assignmentReasonElement.current
                : focus.kind === 'reassignment' ? reassignmentPanelElement.current?.querySelector<HTMLInputElement>('[aria-label="Search target tenant options"]')
                    : reviewHeadingElement.current;
            target?.focus();
        } else if (focus.direction === 'return' && !open) {
            const origin = inlineOrigins.current[focus.kind];
            const fallback = focus.kind === 'review' ? historyFilterElement.current : inventoryFilterElement.current;
            (origin?.isConnected && !origin.disabled ? origin : fallback)?.focus();
            inlineOrigins.current[focus.kind] = null;
        }
    }, [pendingAction, reassigning, reviewing, principal, session]);

    useEffect(() => {
        const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 300);
        return () => window.clearTimeout(timer);
    }, [search]);

    const loadData = useCallback(async () => {
        const owner = ownerRef.current;
        if (!isPlatformAdmin || mutationInFlight.current || !owner?.active || owner.principal !== principal) return;
        const generation = ++readGeneration.current;
        const isCurrent = () => owner.active && session === api.getAuthGeneration() && generation === readGeneration.current;
        setLoading(true);
        setReadError(null);
        try {
        const [overviewResponse, assignmentResponse] = await Promise.all([
            api.getAdminInboundOverview(),
            api.getAdminInboundAssignments({
                tenant_id: tenantId || undefined,
                status: status || undefined,
                search: debouncedSearch || undefined,
            }),
        ]);
        if (!isCurrent()) return;

        const firstError = overviewResponse.error ?? assignmentResponse.error;
        if (firstError) {
            setReadError(`Some inbound data could not be refreshed: ${firstError.message}`);
        }
        if (overviewResponse.data) {
            const overview = overviewResponse.data;
            setCounts({
                active: overview.active_assignments,
                paused: overview.paused_assignments,
                quarantined: overview.quarantined_assignments,
                denied: overview.denied_last_24h,
            });
            setControls((current) => current && current.version > overview.controls.version ? current : overview.controls);
            // A refresh must not silently overwrite or rebase an operator's draft.
            if (!draftDirty.current) setDraftControls((current) => current && current.version > overview.controls.version ? current : overview.controls);
        }
        if (assignmentResponse.data) setAssignments(assignmentResponse.data.items);
        } catch (cause) {
            if (isCurrent()) setReadError(cause instanceof Error ? cause.message : 'Could not refresh inbound data.');
        } finally {
            if (isCurrent()) setLoading(false);
        }
    }, [debouncedSearch, status, tenantId, isPlatformAdmin, principal, session]);

    useEffect(() => {
        // The request is intentionally synchronized with the complete filter snapshot.
        void loadData();
        return () => { readGeneration.current += 1; };
    }, [loadData, refreshIndex]);

    useEffect(() => {
        const owner = ownerRef.current;
        if (!isPlatformAdmin || !owner?.active || owner.principal !== principal || mutationInFlight.current) return;
        const generation = ++historyGeneration.current;
        const cursor = requestCursors.at(-1) ?? null;
        const owns = () => owner.active && owner === ownerRef.current && session === api.getAuthGeneration()
            && generation === historyGeneration.current;
        setHistoryLoading(true); setHistoryError(null); setNextRequestCursor(null);
        void api.getAdminInboundReassignments(requestStatus, 50, cursor ?? undefined).then(response => {
            if (!owns()) return;
            const page = response.data;
            if (response.error) { setHistoryError(response.error.message); return; }
            if (!page || !Array.isArray(page.items) || page.items.length > 50 || !page.items.every(validReassignment)
                || new Set(page.items.map(row => row.id)).size !== page.items.length
                || page.items.some(row => requestStatus !== 'all' && row.status !== requestStatus)
                || typeof page.has_more !== 'boolean'
                || (page.has_more ? typeof page.next_cursor !== 'string' || !page.next_cursor || page.next_cursor.length > 2048
                    || page.items.length === 0 || requestCursors.includes(page.next_cursor) : page.next_cursor !== null)) {
                setHistoryError('The reassignment history response could not be verified. Retry the read.'); return;
            }
            setReassignments(page.items); setNextRequestCursor(page.next_cursor);
        }).catch(() => { if (owns()) setHistoryError('Reassignment history is unavailable. Retry the read.'); })
            .finally(() => { if (owns()) setHistoryLoading(false); });
        return () => { historyGeneration.current += 1; };
    }, [isPlatformAdmin, principal, session, requestStatus, requestCursors, refreshIndex]);

    async function readReviewedRequest(selection: RequestSelection) {
        const owner = ownerRef.current;
        if (!owner?.active || owner.principal !== principal || !isPlatformAdmin || mutationInFlight.current
            || reviewSelection.current !== selection || selection.reading || session !== api.getAuthGeneration()) return;
        const generation = ++reviewGeneration.current;
        clearCancellationReview();
        const owns = () => owner.active && owner === ownerRef.current && session === api.getAuthGeneration()
            && reviewSelection.current === selection && generation === reviewGeneration.current;
        selection.reading = true; selection.ready = false;
        setReviewLoading(true); setReviewError(null);
        try {
            const response = await api.getAdminInboundReassignment(selection.original.id);
            if (!owns()) return;
            if (response.error) { setReviewError(response.error.message); return; }
            const row = response.data;
            if (!validReassignment(row) || !sameRequest(row, selection.original)
                || (selection.current.status !== 'pending' && !sameDecision(row, selection.current))) {
                setReviewError('The saved decision does not match this request. Its outcome could not be verified.'); return;
            }
            selection.current = row; selection.ready = true;
            setReviewing(row);
            if (row.status !== 'pending') {
                selection.confirmed = true;
                const attempt = decisionAttempts.current.get(row.id);
                if (attempt) completeMutation(attempt.signature);
                decisionAttempts.current.delete(row.id); setReviewAttempt(null);
            }
        } catch {
            if (owns()) setReviewError('The saved decision is unavailable. Retry this read; no approval or cancellation was resent.');
        } finally {
            if (owns()) { selection.reading = false; setReviewLoading(false); }
        }
    }

    function openRequest(request: AdminInboundReassignmentRequest, trigger?: HTMLButtonElement) {
        if (!ownerRef.current?.active || !isPlatformAdmin || mutationInFlight.current || session !== api.getAuthGeneration()
            || renderedHistoryGeneration !== historyGeneration.current) return;
        const selection: RequestSelection = { original: request, current: request, reading: false, ready: false, confirmed: false };
        inlineOrigins.current.review = trigger ?? null;
        inlineFocus.current = { kind: 'review', direction: 'entry' };
        clearCancellationReview();
        reviewSelection.current = selection;
        const attempt = decisionAttempts.current.get(request.id) ?? null;
        setReviewAttempt(attempt); setReviewing(request); setReviewError(null);
        reviewReasonRef.current = attempt?.kind === 'approve' ? attempt.reason : ''; setReviewReason(reviewReasonRef.current);
        void readReviewedRequest(selection);
    }

    function closeReview() {
        if (mutationInFlight.current || reviewSelection.current !== renderedSelection) return;
        reviewSelection.current = null; reviewGeneration.current += 1;
        inlineFocus.current = { kind: 'review', direction: 'return' };
        clearCancellationReview();
        setReviewing(null); setReviewAttempt(null); setReviewError(null); setReviewLoading(false);
    }

    function refreshInbound() {
        if (mutationInFlight.current || session !== api.getAuthGeneration()) return;
        setRefreshIndex(value => value + 1);
        const selection = reviewSelection.current;
        if (selection) void readReviewedRequest(selection);
    }

    useEffect(() => {
        const owner = ownerRef.current;
        let active = true;
        setReviewTenants(current => current && reviewing && current.requestId === reviewing.id ? { ...current, loading: true } : null);
        if (!reviewing || !isPlatformAdmin || !owner?.active) return;
        const request = reviewing;
        const owns = () => active && owner.active && owner === ownerRef.current && session === api.getAuthGeneration();
        void Promise.all([api.getTenantById(request.source_tenant_id).catch(() => null), api.getTenantById(request.target_tenant_id).catch(() => null)])
            .then(([source, target]) => {
                if (!owns()) return;
                const sourceName = !source?.error && source?.data?.id === request.source_tenant_id
                    && typeof source.data.business_name === 'string' && source.data.business_name.trim() ? source.data.business_name : '';
                const targetName = !target?.error && target?.data?.id === request.target_tenant_id
                    && typeof target.data.business_name === 'string' && target.data.business_name.trim() ? target.data.business_name : '';
                const error = source?.error?.message ?? target?.error?.message
                    ?? (!sourceName || !targetName ? 'Tenant details do not match the reviewed request.' : null);
                setReviewTenants({ requestId: request.id, source: sourceName, target: targetName, error, loading: false });
            }).catch(() => {
                if (owns()) setReviewTenants({ requestId: request.id, source: '', target: '', error: 'Tenant details are unavailable.', loading: false });
            });
        return () => { active = false; };
    }, [reviewing, principal, session, isPlatformAdmin, refreshIndex]);

    const tenantReviewReady = Boolean(reviewing && reviewTenants?.requestId === reviewing.id && !reviewTenants.error && !reviewTenants.loading);

    const openAssignmentAction = (assignment: AdminInboundAssignment, action: PendingAssignmentAction['action'], trigger?: HTMLButtonElement) => {
        if (!ownerRef.current?.active || ownerRef.current.principal !== principal || mutationInFlight.current || session !== api.getAuthGeneration()) return;
        inlineOrigins.current.assignment = trigger ?? null;
        inlineFocus.current = { kind: 'assignment', direction: 'entry' };
        setPendingAction({ assignment, action }); setActionReason('');
    };

    const openReassignment = (assignment: AdminInboundAssignment, trigger?: HTMLButtonElement) => {
        if (!ownerRef.current?.active || ownerRef.current.principal !== principal || mutationInFlight.current || session !== api.getAuthGeneration()) return;
        inlineOrigins.current.reassignment = trigger ?? null;
        inlineFocus.current = { kind: 'reassignment', direction: 'entry' };
        setTargetTenantId(''); setTargetCampaignId(''); setReassignmentReason('');
        setReassigning(assignment);
    };

    function closeAssignmentForm(kind: 'assignment' | 'reassignment') {
        if (!ownerRef.current?.active || ownerRef.current.principal !== principal || mutationInFlight.current || session !== api.getAuthGeneration()) return;
        inlineFocus.current = { kind, direction: 'return' };
        if (kind === 'assignment') setPendingAction(null);
        else setReassigning(null);
    }

    const controlsChanged = useMemo(() => {
        if (!controls || !draftControls) return false;
        return controls.inbound_enabled !== draftControls.inbound_enabled
            || controls.recording_enabled !== draftControls.recording_enabled
            || controls.transfer_enabled !== draftControls.transfer_enabled
            || controls.settlement_enabled !== draftControls.settlement_enabled;
    }, [controls, draftControls]);

    const draftOutdated = Boolean(controls && draftControls && controls.version !== draftControls.version);

    async function runMutation<T>(
        scope: string,
        payload: unknown,
        execute: (key: string) => Promise<{ data?: T; error?: { message: string } }>,
        accepted: (result: T) => boolean,
        onSuccess: (result: T) => void,
    ) {
        const owner = ownerRef.current;
        if (!owner?.active || mutationInFlight.current || !isPlatformAdmin || session !== api.getAuthGeneration()) return;
        const isCurrent = () => owner.active && session === api.getAuthGeneration();
        mutationInFlight.current = true;
        readGeneration.current += 1;
        setLoading(false);
        setSaving(true);
        setError(null);
        setSuccess(null);
        const operation = mutationToken(scope, payload);
        try {
            const response = await execute(operation.key);
            if (!isCurrent()) return;
            if (response.error) {
                setError(response.error.message);
            } else if (!response.data || !accepted(response.data)) {
                setError('The operation result could not be verified. Review the refreshed state before retrying; the same request keeps its idempotency key.');
            } else {
                completeMutation(operation.signature);
                onSuccess(response.data);
            }
        } catch (cause) {
            if (isCurrent()) setError(`${cause instanceof Error ? cause.message : 'Request interrupted.'} The outcome may be unknown; review the refreshed state before retrying.`);
        } finally {
            if (isCurrent()) {
                mutationInFlight.current = false;
                setSaving(false);
                // Refresh with the latest filters; read errors never replace the mutation result.
                setRefreshIndex((value) => value + 1);
            }
        }
    }

    const saveControls = async () => {
        if (!controls || !draftControls || !controlsChanged || draftOutdated) return;
        if (controlReason.trim().length < 8) {
            setError('Enter an operational reason of at least 8 characters before changing a kill switch.');
            return;
        }
        const payload = {
            inbound_enabled: draftControls.inbound_enabled,
            recording_enabled: draftControls.recording_enabled,
            // New transfer enablement is intentionally impossible. A legacy
            // true value can only transition to false.
            transfer_enabled: controls.transfer_enabled && draftControls.transfer_enabled,
            settlement_enabled: draftControls.settlement_enabled,
            expected_version: draftControls.version,
            reason: controlReason.trim(),
        };
        await runMutation('controls', payload,
            (key) => api.updateAdminInboundControls(payload, key),
            (result) => result.version === payload.expected_version + 1
                && result.inbound_enabled === payload.inbound_enabled
                && result.recording_enabled === payload.recording_enabled
                && result.transfer_enabled === payload.transfer_enabled
                && result.settlement_enabled === payload.settlement_enabled,
            (result) => {
            draftDirty.current = false;
            setControls(result);
            setDraftControls(result);
            setSuccess('Inbound runtime controls updated and recorded in the audit log.');
            setControlReason('');
        });
    };

    const runAssignmentAction = async () => {
        if (!pendingAction || actionReason.trim().length < 8) {
            setError('Enter an operational reason of at least 8 characters.');
            return;
        }
        const { assignment, action } = pendingAction;
        const payload = {
            assignment_id: assignment.id,
            expected_version: assignment.version,
            action,
            reason: actionReason.trim(),
        };
        await runMutation(`assignment:${action}`, payload, (key) => action === 'quarantine'
            ? api.quarantineAdminInboundAssignment(
                assignment.id,
                assignment.version,
                actionReason.trim(),
                key,
            )
            : api.unquarantineAdminInboundAssignment(
                assignment.id,
                assignment.version,
                actionReason.trim(),
                key,
            ),
            // The quarantine receipt is intentionally partial; tenant ownership
            // is checked by the service, and unquarantine restores active or paused.
            (result) => result.id === assignment.id && result.version === assignment.version + 1
                && (action === 'quarantine' ? result.status === 'quarantined' : ['active', 'paused'].includes(result.status)),
            () => {
            setSuccess(action === 'quarantine'
                ? `${assignment.masked_did} is quarantined; new calls will fail closed.`
                : `${assignment.masked_did} was released from quarantine.`);
            setPendingAction(null);
            setActionReason('');
        });
    };

    const requestReassignment = async () => {
        if (!reassigning || !targetTenantId || targetTenantId === reassigning.tenant_id || !targetCampaignId.trim() || reassignmentReason.trim().length < 8) {
            setError('Target tenant, target inbound campaign, and an 8-character reason are required.');
            return;
        }
        const payload = {
            assignment_id: reassigning.id,
            target_tenant_id: targetTenantId,
            target_campaign_id: targetCampaignId.trim(),
            expected_version: reassigning.version,
            reason: reassignmentReason.trim(),
        };
        await runMutation('reassignment', payload,
            (key) => api.requestAdminInboundReassignment(payload, key),
            (result) => result.status === 'pending' && result.assignment_id === payload.assignment_id
                && result.source_tenant_id === reassigning.tenant_id
                && result.target_tenant_id === payload.target_tenant_id && result.target_campaign_id === payload.target_campaign_id,
            () => {
            setSuccess('Reassignment requested. A different platform administrator must approve it.');
            setReassigning(null);
            setTargetTenantId('');
            setTargetCampaignId('');
            setReassignmentReason('');
        });
    };

    function openCancellationReview() {
        const selection = renderedSelection;
        if (!selection || selection !== reviewSelection.current || !ownerRef.current?.active || !isPlatformAdmin
            || mutationInFlight.current || session !== api.getAuthGeneration() || selection.reading || !selection.ready
            || selection.current.status !== 'pending') return;
        const attempt = decisionAttempts.current.get(selection.original.id);
        const confirmation = { selection, reason: attempt?.kind === 'cancel' ? attempt.reason : '' };
        cancellationFocus.current = 'entry';
        cancellationReviewRef.current = confirmation; setCancellationReview(confirmation);
    }

    const decideReassignment = async (kind: DecisionAttempt['kind']) => {
        const selection = renderedSelection, owner = ownerRef.current;
        if (!selection || selection !== reviewSelection.current || !owner?.active || !isPlatformAdmin
            || mutationInFlight.current || session !== api.getAuthGeneration() || selection.reading || !selection.ready
            || !reviewing || selection.current !== reviewing || reviewing.status !== 'pending') return;
        const existing = decisionAttempts.current.get(reviewing.id);
        let reason: string;
        if (kind === 'approve') {
            if (cancellationReviewRef.current || !tenantReviewReady || reviewing.requested_by === principal || existing?.kind === 'cancel'
                || reviewReason !== reviewReasonRef.current || reviewReason.trim().length < 8) return;
            reason = existing?.reason ?? reviewReason.trim();
        } else {
            if (!cancellationReview || cancellationReview !== cancellationReviewRef.current
                || cancellationReview.selection !== selection) return;
            reason = existing?.kind === 'cancel' ? existing.reason : cancellationReview.reason.trim();
            if (reason.length < 3 || reason.length > 2000) return;
        }
        const operation: DecisionAttempt = existing?.kind === kind ? existing : { kind, request: reviewing, reason,
            ...mutationToken(`${kind}-reassignment`, { request_id: reviewing.id, reason }) };
        decisionAttempts.current.set(reviewing.id, operation); setReviewAttempt(operation);
        const owns = () => owner.active && owner === ownerRef.current && session === api.getAuthGeneration()
            && selection === reviewSelection.current;
        const label = kind === 'approve' ? 'approval' : 'cancellation';
        mutationInFlight.current = true; readGeneration.current += 1; historyGeneration.current += 1;
        selection.ready = false; reviewGeneration.current += 1; clearCancellationReview();
        setSaving(true); setLoading(false); setHistoryLoading(false); setReviewError(null); setError(null); setSuccess(null);
        try {
            const response = kind === 'approve'
                ? await api.approveAdminInboundReassignment(operation.request.id, operation.reason, operation.key)
                : await api.cancelAdminInboundReassignment(operation.request.id, operation.reason, operation.key);
            if (!owns()) return;
            const result = response.data;
            const matchesDecision = result && (kind === 'approve'
                ? result.status === 'approved' && result.approved_by === principal
                : result.status === 'cancelled' && result.approved_by == null && result.approved_assignment_id == null
                    && timestamp(result.decided_at) && Date.parse(result.decided_at) >= Date.parse(operation.request.requested_at));
            if (response.error) {
                const status = response.error.status;
                setReviewError(status && status >= 400 && status < 500
                    ? `The server rejected this ${label} (${status}): ${response.error.message} Check the server-saved decision before trying again.`
                    : `${response.error.message} The ${label} is not confirmed. Check the server-saved decision before trying again.`);
            } else if (!validReassignment(result) || !sameRequest(result, operation.request) || !matchesDecision
                || result.decision_reason !== operation.reason) {
                setReviewError(`The ${label} response could not be verified. Check the server-saved decision before trying again.`);
            } else {
                completeMutation(operation.signature); decisionAttempts.current.delete(result.id); setReviewAttempt(null);
                selection.current = result; selection.ready = true; selection.confirmed = true; setReviewing(result);
                setSuccess(kind === 'approve' ? 'Reassignment approved and applied atomically.'
                    : 'Reassignment request cancelled. The DID assignment is unchanged.');
            }
        } catch (cause) {
            if (owns()) setReviewError(`${cause instanceof Error ? cause.message : 'The decision result is unavailable.'} The ${label} is not confirmed. Check the server-saved decision; no decision is automatically resent.`);
        } finally {
            if (owner.active && owner === ownerRef.current && session === api.getAuthGeneration()) {
                mutationInFlight.current = false; setSaving(false); setRefreshIndex(value => value + 1);
            }
        }
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
                        <button className="btn btn-secondary" onClick={refreshInbound} disabled={loading || saving}>
                            <RefreshCw size={16} className={loading ? 'spinning' : ''} /> Refresh
                        </button>
                    </div>

                    {error && <div className="error-banner" role="alert"><p>{error}</p></div>}
                    {readError && <div className="error-banner" role="alert"><p>{readError} Previously loaded data may be stale.</p></div>}
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
                                                            draftDirty.current = true;
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
                                                disabled={saving}
                                            />
                                        </div>
                                        <button className="btn btn-primary" disabled={!controlsChanged || saving || draftOutdated || loading} onClick={() => void saveControls()}>
                                            <Power size={16} /> Apply version {draftControls.version + 1}
                                        </button>
                                    </div>
                                    <p className="control-version">Current version {controls.version} · last changed {formatDate(controls.updated_at)}</p>
                                    {draftOutdated && <p role="alert">Your draft is based on version {draftControls.version}; the server is now version {controls.version}. Review the latest values before applying changes.</p>}
                                    {(controlsChanged || draftOutdated) && <button className="btn btn-secondary btn-sm" disabled={saving} onClick={() => { draftDirty.current = false; setDraftControls(controls); setControlReason(''); }}>Discard draft and use latest values</button>}
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
                                <div className="search-box"><Search size={18} /><input className="search-input" value={search} onChange={(e) => setSearch(e.target.value)} disabled={saving} placeholder="DID, tenant, campaign, or error…" /></div>
                                <TenantFilter value={tenantId} onChange={setTenantId} disabled={saving} includeArchived
                                    label="Filter inbound assignments by tenant" searchLabel="Search inbound tenant options" />
                                <select ref={inventoryFilterElement} className="filter-select" value={status} onChange={(e) => setStatus(e.target.value)} disabled={saving} aria-label="Filter inbound assignments by status">
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
                                                                <button className="btn btn-secondary btn-sm" onClick={event => openAssignmentAction(assignment, 'unquarantine', event?.currentTarget)} disabled={saving}>Unquarantine</button>
                                                            ) : assignment.status !== 'archived' && (
                                                                <button className="btn btn-danger btn-sm" onClick={event => openAssignmentAction(assignment, 'quarantine', event?.currentTarget)} disabled={saving}>Quarantine</button>
                                                            )}
                                                            {['paused', 'quarantined'].includes(assignment.status) && <button className="btn btn-secondary btn-sm" onClick={event => openReassignment(assignment, event?.currentTarget)} disabled={saving}><ArrowRightLeft size={14} /> Reassign</button>}
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
                                <div className="form-group"><label htmlFor="assignment-action-reason">Required reason</label><textarea ref={assignmentReasonElement} id="assignment-action-reason" value={actionReason} onChange={(e) => setActionReason(e.target.value)} rows={3} /></div>
                                <div className="row-actions"><button className="btn btn-secondary" onClick={() => closeAssignmentForm('assignment')} disabled={saving}>Cancel</button><button className={pendingAction.action === 'quarantine' ? 'btn btn-danger' : 'btn btn-primary'} onClick={() => void runAssignmentAction()} disabled={saving}>Confirm {pendingAction.action}</button></div>
                            </div>
                        </section>
                    )}

                    {reassigning && (
                        <section ref={reassignmentPanelElement} className="card inbound-action-panel" aria-labelledby="reassign-heading">
                            <div className="card-body">
                                <h2 id="reassign-heading" className="card-title">Request reassignment of {reassigning.masked_did}</h2>
                                <p className="section-help">The assignment stays fail-closed until a different platform administrator approves the request.</p>
                                <div className="form-row"><div className="form-group"><label htmlFor="target-tenant">Target tenant</label>
                                    <TenantFilter key={reassigning.id} selectId="target-tenant" value={targetTenantId} onChange={setTargetTenantId}
                                        disabled={saving} label="Target tenant" searchLabel="Search target tenant options" emptyLabel="Select target"
                                        excludeTenantId={reassigning.tenant_id} />
                                </div><div className="form-group"><label htmlFor="target-campaign">Target inbound campaign ID</label><input id="target-campaign" value={targetCampaignId} onChange={(e) => setTargetCampaignId(e.target.value)} placeholder="UUID" /></div></div>
                                <div className="form-group"><label htmlFor="reassignment-reason">Required reason</label><textarea id="reassignment-reason" value={reassignmentReason} onChange={(e) => setReassignmentReason(e.target.value)} rows={3} /></div>
                                <div className="row-actions"><button className="btn btn-secondary" onClick={() => closeAssignmentForm('reassignment')} disabled={saving}>Cancel</button><button className="btn btn-warning" onClick={() => void requestReassignment()} disabled={saving}><ArrowRightLeft size={15} /> Submit for approval</button></div>
                            </div>
                        </section>
                    )}

                    <section className="card inbound-control-section" aria-labelledby="pending-reassignments-heading">
                        <div className="card-header"><div><h2 id="pending-reassignments-heading" className="card-title">Reassignment requests</h2><p className="section-help">Server-saved requests and decisions remain available after a reload. The requester can never approve their own reassignment.</p></div>
                            <select ref={historyFilterElement} className="filter-select" aria-label="Filter reassignment requests by status" value={requestStatus} disabled={saving}
                                onChange={event => {
                                    historyGeneration.current += 1; setReassignments([]); setNextRequestCursor(null);
                                    setRequestStatus(event.target.value); setRequestCursors([null]);
                                }}>
                                <option value="all">All requests</option><option value="pending">Pending</option><option value="approved">Approved</option>
                                <option value="rejected">Rejected</option><option value="cancelled">Cancelled</option>
                            </select>
                        </div>
                        <div className="card-body">
                            {historyError && <p role="alert">Reassignment history could not be refreshed: {historyError} Previously loaded rows may be stale. <button className="btn btn-secondary btn-sm" disabled={saving || historyLoading} onClick={() => setRefreshIndex(value => value + 1)}>Retry request history</button></p>}
                            {historyLoading ? <p role="status">Loading reassignment requests…</p> : reassignments.length === 0 && !historyError ? <p className="no-data-inline">No reassignment requests in this view.</p> : (
                                <div className="pending-reassignment-list">{reassignments.map((request) => {
                                    const ownRequest = request.requested_by === user?.id;
                                    const pending = request.status === 'pending';
                                    return <article key={request.id} className="pending-reassignment"><div><strong>{request.id}</strong><p>Assignment {request.assignment_id}</p><p>{request.source_tenant_id} → {request.target_tenant_id}</p><StatusBadge value={request.status} /><small>{request.reason} · requested {formatDate(request.requested_at)}</small></div><button className="btn btn-primary btn-sm" disabled={saving || historyLoading} title={pending && ownRequest ? 'You can review or cancel this request. A different platform administrator must approve it.' : undefined} onClick={event => openRequest(request, event?.currentTarget)}>{pending ? 'Review reassignment' : 'View saved decision'}</button></article>;
                                })}</div>
                            )}
                            <div className="row-actions">
                                <button className="btn btn-secondary btn-sm" disabled={saving || historyLoading || requestCursors.length === 1}
                                    onClick={() => { historyGeneration.current += 1; setReassignments([]); setRequestCursors(values => values.slice(0, -1)); }}>Previous requests</button>
                                <span>Page {requestCursors.length} · {reassignments.length} shown</span>
                                <button className="btn btn-secondary btn-sm" disabled={saving || historyLoading || Boolean(historyError) || !nextRequestCursor}
                                    onClick={() => { if (!nextRequestCursor) return; historyGeneration.current += 1; setReassignments([]); setRequestCursors(values => [...values, nextRequestCursor]); }}>Next requests</button>
                            </div>
                        </div>
                    </section>
                    {reviewing && <section className="card inbound-action-panel" aria-labelledby="reassignment-review-heading"><div className="card-body">
                        <h2 ref={reviewHeadingElement} tabIndex={-1} id="reassignment-review-heading" className="card-title">{reviewing.status === 'pending' ? 'Review reassignment' : renderedSelection?.confirmed ? 'Server-saved decision' : 'Reassignment details'}</h2>
                        <p>Saved status: <StatusBadge value={reviewing.status} /></p>
                        {reviewLoading && <p role="status">Checking this exact request…</p>}
                        {reviewError && <p role="alert">{reviewError}</p>}
                        <dl>
                            <dt>Request ID</dt><dd>{reviewing.id}</dd>
                            <dt>Source assignment ID</dt><dd>{reviewing.assignment_id}</dd>
                            <dt>Source tenant</dt><dd>{reviewTenants?.requestId === reviewing.id && reviewTenants.source ? reviewTenants.source : 'Name unavailable'} · {reviewing.source_tenant_id}</dd>
                            <dt>Target tenant</dt><dd>{reviewTenants?.requestId === reviewing.id && reviewTenants.target ? reviewTenants.target : 'Name unavailable'} · {reviewing.target_tenant_id}</dd>
                            <dt>Target campaign ID</dt><dd>{reviewing.target_campaign_id}</dd>
                            <dt>Requested by</dt><dd>{reviewing.requested_by}</dd>
                            <dt>Requested at</dt><dd>{formatDate(reviewing.requested_at)}</dd>
                            <dt>Requester reason</dt><dd>{reviewing.reason}</dd>
                            {reviewing.status !== 'pending' && <>
                                {reviewing.status === 'approved' && <><dt>Approved by</dt><dd>{reviewing.approved_by ?? 'Not recorded'}</dd></>}
                                <dt>Decision reason</dt><dd>{reviewing.decision_reason ?? 'Not recorded'}</dd>
                                <dt>Decided at</dt><dd>{reviewing.decided_at ? formatDate(reviewing.decided_at) : 'Not recorded'}</dd>
                                {reviewing.status === 'approved' && <><dt>Resulting assignment ID</dt><dd>{reviewing.approved_assignment_id ?? 'Not recorded'}</dd></>}
                            </>}
                        </dl>
                        {reviewing.status === 'pending' ? <>
                            {(!reviewTenants || reviewTenants.loading) && <p role="status">Loading the exact source and target tenant details…</p>}
                            {reviewTenants?.error && <p role="alert">Tenant details unavailable: {reviewTenants.error} <button type="button" className="btn btn-secondary btn-sm" disabled={saving} onClick={() => setRefreshIndex(value => value + 1)}>Retry tenant details</button></p>}
                            <p className="section-help">Verify these ownership and target details before approving. The service rechecks the request, assignment and target in one transaction. The target remains paused after approval.</p>
                            {reviewAttempt && <p role="status">The earlier {reviewAttempt.kind === 'approve' ? 'approval' : 'cancellation'} is not confirmed. Checking the saved decision does not send another decision. If it is still pending, an explicit retry uses the original reason and request key.</p>}
                            {reviewing.requested_by === principal && <p className="section-help">You can cancel your request. A different platform administrator must approve it.</p>}
                            <div className="form-group"><label htmlFor="reassignment-review-reason">Your review reason</label><textarea id="reassignment-review-reason" value={reviewReason} onChange={event => { reviewReasonRef.current = event.target.value; setReviewReason(event.target.value); }} minLength={8} rows={3} disabled={saving || Boolean(reviewAttempt) || Boolean(cancellationReview)} /></div>
                            {cancellationReview && <div>
                                <h3>Cancel this saved request?</h3>
                                <p>This cancels the reassignment request. It does not move, pause, unquarantine or otherwise change the DID assignment.</p>
                                <div className="form-group"><label htmlFor="reassignment-cancellation-reason">Cancellation reason</label>
                                    <textarea id="reassignment-cancellation-reason" ref={cancellationReasonElement} value={cancellationReview.reason} minLength={3} maxLength={2000} rows={3}
                                        disabled={saving || reviewAttempt?.kind === 'cancel'} onChange={event => {
                                            if (cancellationReviewRef.current !== cancellationReview) return;
                                            const next = { ...cancellationReview, reason: event.target.value };
                                            cancellationReviewRef.current = next; setCancellationReview(next);
                                        }} /></div>
                                <div className="row-actions"><button className="btn btn-secondary" disabled={saving} onClick={() => {
                                    if (cancellationReviewRef.current === cancellationReview) clearCancellationReview(true);
                                }}>Back to review</button>
                                    <button ref={cancellationConfirmElement} className="btn btn-warning" disabled={saving || reviewLoading || !renderedSelection?.ready || cancellationReview.reason.trim().length < 3 || cancellationReview.reason.trim().length > 2000}
                                        onClick={() => void decideReassignment('cancel')}>Confirm cancellation</button></div>
                            </div>}
                        </> : <>
                            <p className="section-help">These are the saved request and decision fields, not a statement about current DID ownership. No approval is sent when reading this record.</p>
                            {reviewing.status === 'cancelled' && <p className="section-help">Cancellation does not move or change the DID assignment.</p>}
                            {reviewTenants?.requestId === reviewing.id && reviewTenants.error && <p role="status">Some tenant names are unavailable. The recorded tenant IDs still identify this decision.</p>}
                        </>}
                        <div className="row-actions"><button className="btn btn-secondary" disabled={saving} onClick={closeReview}>Cancel review</button>
                            <button className="btn btn-secondary" disabled={saving} aria-disabled={saving || reviewLoading} aria-busy={reviewLoading} onClick={() => {
                                if (renderedSelection && renderedSelection === reviewSelection.current) void readReviewedRequest(renderedSelection);
                            }}>Check saved decision</button>
                            {reviewing.status === 'pending' && <>
                                <button className="btn btn-primary" disabled={saving || reviewLoading || !renderedSelection?.ready || !tenantReviewReady || reviewReason.trim().length < 8 || reviewing.requested_by === user?.id || reviewAttempt?.kind === 'cancel' || Boolean(cancellationReview)} onClick={() => void decideReassignment('approve')}>{reviewAttempt?.kind === 'approve' ? 'Retry original approval' : 'Approve reassignment'}</button>
                                {!cancellationReview && <button ref={cancellationTriggerElement} className="btn btn-warning" disabled={saving || reviewLoading || !renderedSelection?.ready}
                                    onClick={openCancellationReview}>{reviewAttempt?.kind === 'cancel' ? 'Retry original cancellation' : 'Cancel saved request'}</button>}
                            </>}
                        </div>
                    </div></section>}
                </div>
            </main>
        </div>
    );
}
