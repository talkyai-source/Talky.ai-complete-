import { useState, useEffect, useCallback, useRef, useSyncExternalStore } from 'react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import {
    Users, UserPlus, Search, Filter, RefreshCw, Shield, ShieldCheck,
    Ban, CheckCircle2, Trash2, Pencil, Building2, X, KeyRound, Lock,
} from 'lucide-react';
import { api } from '../lib/api';
import { useAuth } from '../lib/auth';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { ConfirmationModal } from '../components/ConfirmationModal';
import { FormalHoldPanel } from '../components/FormalHoldPanel';
import { TenantFilter } from '../components/TenantFilter';
import { useDialogFocus } from '../lib/use-dialog-focus';
import type {
    AdminUserItem, CreateUserRequest, UpdateUserRequest, UserMutationReceipt, UserDeleteReceipt, UserCreateState, RbacRole, RbacPermission, TenantListItem,
    UserOperationPrepare, UserOperationView, UserOperationList, PrimaryMembershipReview, PrimaryMembershipRequest, PrimaryMembershipReceipt,
} from '../lib/api';

const EDITABLE_ROLES = ['platform_admin', 'partner_admin', 'tenant_admin', 'campaign_manager', 'user', 'agent', 'billing_user', 'readonly'];

const ROLE_LABELS: Record<string, string> = {
    platform_admin: 'Platform Admin',
    partner_admin: 'Partner Admin',
    tenant_admin: 'Tenant Admin',
    user: 'User',
    readonly: 'Read-only',
};

function formatDate(d?: string | null): string {
    if (!d) return '-';
    try { return new Date(d).toLocaleDateString(); } catch { return d; }
}

function isActive(u: AdminUserItem): boolean {
    return u.is_active === true;
}

function isUserDirectory(value: unknown): value is AdminUserItem[] {
    return Array.isArray(value) && value.every((item) => item !== null && typeof item === 'object'
        && ['id', 'email', 'role'].every((key) => typeof item[key] === 'string' && item[key].trim().length > 0)
        && typeof item.is_active === 'boolean');
}

const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const textValue = (value: unknown, max = 320): value is string => typeof value === 'string' && value.trim().length > 0 && value.length <= max;
const exactKeys = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).every(key => keys.includes(key));
const versionToken = (value: unknown) => typeof value === 'string' && /^v1:[0-9a-f]{64}$/.test(value);
const membershipToken = (value: unknown) => typeof value === 'string' && /^pm1:[0-9a-f]{64}$/.test(value);
const reviewedUser = (value: unknown): value is AdminUserItem => isUserDirectory([value]) && record(value)
    && versionToken(value.review_token) && (value.name === null || typeof value.name === 'string')
    && (value.tenant_id === null || typeof value.tenant_id === 'string');
type DraftUpdate = Omit<UpdateUserRequest, 'operation_id'>;
type UserIntent = { kind: 'create'; request: Omit<CreateUserRequest, 'password' | 'operation_id' | 'request_id'> }
    | { kind: 'edit' | 'block'; user: AdminUserItem; request: DraftUpdate }
    | { kind: 'delete'; user: AdminUserItem }
    | { kind: 'primary_membership'; user: AdminUserItem; request: Omit<PrimaryMembershipRequest, 'operation_id'> };
type UserOperation = { intent: UserIntent; generation: number; status: 'preparing' | 'pending' | 'unknown'; view?: UserOperationView; error?: string };
type UserOutcome = { operation: UserOperation | null; pending: UserOperationView[]; discovery: 'loading' | 'ready' | 'unavailable'; discoveryError?: string;
    recoveryBusy?: string; receipts: { message: string; auditId: string }[] };
let userOutcome: UserOutcome = { operation: null, pending: [], discovery: 'loading', receipts: [] };
let discoveryRevision = 0;
const outcomeListeners = new Set<() => void>();
const publishOutcome = (next: UserOutcome) => { userOutcome = next; for (const listener of outcomeListeners) listener(); };
const subscribeOutcome = (listener: () => void) => { outcomeListeners.add(listener); return () => { outcomeListeners.delete(listener); }; };
api.onAuthGenerationChanged(() => {
    discoveryRevision++;
    publishOutcome({ operation: null, pending: [], discovery: 'loading', receipts: [] });
});
const useUserOutcome = () => useSyncExternalStore(subscribeOutcome, () => userOutcome);
const writesBlocked = () => userOutcome.discovery !== 'ready' || !!userOutcome.operation || userOutcome.pending.length > 0 || !!userOutcome.recoveryBusy;
const intentLabel = (intent: UserIntent) => intent.kind === 'create' ? intent.request.email : intent.user.email;
const operationLabel = (view: UserOperationView) => view.kind === 'create' ? String(view.intent.email) : 'account ' + view.target_user_id;
const sameIntent = (left: Record<string, unknown>, right: Record<string, unknown>) => Object.keys(left).length === Object.keys(right).length
    && Object.keys(left).every(key => left[key] === right[key]);
function validOperationIntent(kind: UserOperationView['kind'], value: unknown): value is Record<string, unknown> {
    if (!record(value)) return false;
    if (kind === 'create') return exactKeys(value, ['name', 'email', 'role', 'tenant_id']) && textValue(value.name, 200) && textValue(value.email)
        && typeof value.role === 'string' && EDITABLE_ROLES.includes(value.role) && (value.tenant_id === null || uuid(value.tenant_id));
    if (kind === 'primary_membership') return exactKeys(value, ['tenant_id', 'role', 'expected_review_token']) && uuid(value.tenant_id)
        && typeof value.role === 'string' && value.role !== 'platform_admin' && EDITABLE_ROLES.includes(value.role) && membershipToken(value.expected_review_token);
    if (!versionToken(value.expected_review_token)) return false;
    if (kind === 'delete') return exactKeys(value, ['expected_review_token']);
    return kind === 'update' && exactKeys(value, ['expected_review_token', 'name', 'role', 'expected_role', 'is_active'])
        && (value.name === undefined || typeof value.name === 'string' && value.name.length <= 200)
        && (value.role === undefined || typeof value.role === 'string' && EDITABLE_ROLES.includes(value.role))
        && (value.expected_role === undefined || typeof value.expected_role === 'string' && EDITABLE_ROLES.includes(value.expected_role))
        && (value.is_active === undefined || typeof value.is_active === 'boolean')
        && (value.name !== undefined || value.role !== undefined || value.is_active !== undefined);
}
function matchingOperationReceipt(value: unknown, view: Pick<UserOperationView, 'kind' | 'intent' | 'operation_id' | 'target_user_id'>): value is UserMutationReceipt | UserDeleteReceipt | UserCreateState | PrimaryMembershipReceipt {
    if (!record(value) || !uuid(value.audit_event_id)) return false;
    if (view.kind !== 'create' && value.operation_id !== view.operation_id) return false;
    const intended = view.intent;
    if (view.kind === 'create') return value.request_id === view.operation_id && value.state === 'created' && uuid(value.user_id)
        && record(value.intent) && sameIntent(value.intent, intended)
        && reviewedUser(value.user) && value.user.id === value.user_id && value.user.email === intended.email
        && value.user.name === intended.name && value.user.role === intended.role && value.user.tenant_id === intended.tenant_id
        && value.user.is_active === true && value.user.is_verified === true;
    if (view.kind === 'primary_membership') return value.operation === 'primary_membership_configured' && reviewedUser(value.user)
        && value.user.id === view.target_user_id && value.user.tenant_id === intended.tenant_id && value.user.role === intended.role
        && value.tenant_id === intended.tenant_id && record(value.membership) && uuid(value.membership.id)
        && value.membership.role === intended.role && value.membership.status === 'active' && value.membership.is_primary === true
        && record(value.credential_cutoff) && ['revoked_sessions', 'revoked_refresh_tokens', 'invalidated_mfa_challenges', 'invalidated_passkey_challenges']
            .every(key => Number.isSafeInteger(value.credential_cutoff && (value.credential_cutoff as Record<string, unknown>)[key]) && Number((value.credential_cutoff as Record<string, unknown>)[key]) >= 0);
    if (view.kind === 'delete') return value.id === view.target_user_id && textValue(value.detail, 4000)
        && (value.outcome === 'deleted' && value.user === null || value.outcome === 'deactivated' && reviewedUser(value.user)
            && value.user.id === view.target_user_id && value.user.is_active === false && value.user.review_token !== intended.expected_review_token);
    return value.operation === 'updated' && reviewedUser(value) && value.id === view.target_user_id
        && value.review_token !== intended.expected_review_token
        && (intended.name === undefined || value.name === intended.name) && (intended.role === undefined || value.role === intended.role)
        && (intended.is_active === undefined || value.is_active === intended.is_active);
}
const awareDate = (value: unknown) => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
function validOperationView(value: unknown): value is UserOperationView {
    if (!record(value) || !uuid(value.operation_id) || !['create', 'update', 'delete', 'primary_membership'].includes(String(value.kind))
        || !(value.kind === 'create' ? value.target_user_id === null : uuid(value.target_user_id))
        || !validOperationIntent(value.kind as UserOperationView['kind'], value.intent)
        || !awareDate(value.prepared_at) || !awareDate(value.expires_at) || Date.parse(String(value.expires_at)) < Date.parse(String(value.prepared_at))
        || typeof value.dispatch_eligible !== 'boolean') return false;
    if (value.state === 'prepared') return value.terminal_audit_event_id === null && value.receipt === null;
    if (!uuid(value.terminal_audit_event_id) || value.dispatch_eligible !== false) return false;
    return value.state === 'cancelled' ? value.receipt === null
        : value.state === 'committed' && matchingOperationReceipt(value.receipt, value as unknown as UserOperationView)
            && value.receipt.audit_event_id === value.terminal_audit_event_id;
}
function matchingView(value: unknown, expected: UserOperationView): value is UserOperationView {
    return validOperationView(value) && value.operation_id === expected.operation_id && value.kind === expected.kind
        && value.target_user_id === expected.target_user_id && sameIntent(value.intent, expected.intent)
        && value.prepared_at === expected.prepared_at && value.expires_at === expected.expires_at;
}
function validOperationList(value: unknown, state: 'pending' | 'terminal'): value is UserOperationList {
    return record(value) && Array.isArray(value.items) && value.items.length <= 25 && value.items.every(item => validOperationView(item)
        && (state === 'pending' ? item.state === 'prepared' : item.state !== 'prepared'))
        && (value.next_cursor === null || textValue(value.next_cursor, 4096))
        && new Set(value.items.map(item => item.operation_id)).size === value.items.length;
}
function receiptMessage(view: UserOperationView, receipt: UserMutationReceipt | UserDeleteReceipt | UserCreateState | PrimaryMembershipReceipt): string {
    const target = operationLabel(view);
    if (view.kind === 'create') return target + ' created. This is the original saved receipt, not a current-account read.';
    if (view.kind === 'primary_membership') return 'Primary membership configured for ' + target + '. Previous credentials were retired; fresh sign-in is required.';
    if (view.kind === 'delete') return target + ((receipt as UserDeleteReceipt).outcome === 'deleted' ? ' permanently deleted.' : ' deactivated instead of hard-deleted.');
    return target + (view.intent.is_active === false ? ' blocked.' : view.intent.is_active === true ? ' unblocked.' : ' updated.');
}
function receiveTerminal(view: UserOperationView) {
    if (view.state === 'prepared' || userOutcome.receipts.some(receipt => receipt.auditId === view.terminal_audit_event_id)) return userOutcome.receipts;
    return [...userOutcome.receipts, { auditId: view.terminal_audit_event_id!, message: view.state === 'cancelled'
        ? 'Operation ' + view.operation_id + ' was cancelled. No account change is claimed.'
        : receiptMessage(view, view.receipt as UserMutationReceipt) }];
}
async function discoverOperations(owns: () => boolean) {
    if (!owns() || userOutcome.operation?.status === 'preparing' || userOutcome.operation?.status === 'pending' || userOutcome.recoveryBusy) return;
    const generation = api.getAuthGeneration(), revision = ++discoveryRevision;
    const current = () => owns() && generation === api.getAuthGeneration() && revision === discoveryRevision;
    const previous = userOutcome.operation?.view;
    let previouslyPrepared = false;
    publishOutcome({ ...userOutcome, discovery: 'loading', discoveryError: undefined });
    try {
        if (previous) {
            const exact = await api.getUserOperation(previous.operation_id);
            if (!current()) return;
            if (exact.error || !matchingView(exact.data, previous)) throw new Error(exact.error?.message ?? 'The exact operation receipt is unavailable or inconsistent.');
            previouslyPrepared = exact.data.state === 'prepared';
            publishOutcome({ ...userOutcome, receipts: receiveTerminal(exact.data) });
        }
        const pending: UserOperationView[] = [], cursors = new Set<string>();
        let cursor: string | undefined;
        do {
            const result = await api.getUserOperations('pending', cursor);
            if (!current()) return;
            if (result.error || !validOperationList(result.data, 'pending')) throw new Error(result.error?.message ?? 'The server returned invalid pending-operation evidence.');
            if (result.data.items.some(item => pending.some(previous => previous.operation_id === item.operation_id))) throw new Error('Pending-operation pages overlap. Retry discovery.');
            pending.push(...result.data.items);
            if (pending.length > 100 || cursors.size >= 20) throw new Error('Pending-operation discovery was incomplete. New writes remain disabled.');
            cursor = result.data.next_cursor ?? undefined;
            if (cursor && cursors.has(cursor)) throw new Error('Pending-operation pagination did not advance.');
            if (cursor) cursors.add(cursor);
        } while (cursor);
        // A transaction may commit between the exact read and pending-list
        // snapshot. Absence from that list is not its immutable result.
        if (previous && previouslyPrepared && !pending.some(item => item.operation_id === previous.operation_id)) {
            const exact = await api.getUserOperation(previous.operation_id);
            if (!current()) return;
            if (exact.error || !matchingView(exact.data, previous) || exact.data.state === 'prepared')
                throw new Error(exact.error?.message ?? 'The original operation left the pending list, but its terminal result is not confirmed. Retry discovery.');
            publishOutcome({ ...userOutcome, receipts: receiveTerminal(exact.data) });
        }
        publishOutcome({ ...userOutcome, operation: null, pending, discovery: 'ready', discoveryError: undefined });
    } catch (error) {
        if (current()) publishOutcome({ ...userOutcome, discovery: 'unavailable', discoveryError: error instanceof Error ? error.message : 'Pending user operations are unavailable.' });
    }
}
function prepareIntent(intent: UserIntent, password?: string): UserOperationPrepare {
    if (intent.kind === 'create') return { kind: 'create', target_user_id: null, intent: { ...intent.request, tenant_id: intent.request.tenant_id ?? null }, password };
    return { kind: intent.kind === 'delete' ? 'delete' : intent.kind === 'primary_membership' ? 'primary_membership' : 'update',
        target_user_id: intent.user.id, intent: intent.kind === 'delete' ? { expected_review_token: intent.user.review_token } : { ...intent.request } };
}
async function performUserOperation(intent: UserIntent, ownsComponent: () => boolean, password?: string): Promise<boolean> {
    if (!ownsComponent() || writesBlocked()) return false;
    discoveryRevision++;
    let operation: UserOperation = { intent, generation: api.getAuthGeneration(), status: 'preparing' };
    publishOutcome({ ...userOutcome, operation });
    const sameOperation = () => api.getAuthGeneration() === operation.generation && userOutcome.operation === operation;
    const preparedIntent = prepareIntent(intent, password);
    try {
        const preparation = await api.prepareUserOperation(preparedIntent);
        if (!sameOperation()) return false;
        if (preparation.error || !validOperationView(preparation.data) || preparation.data.kind !== preparedIntent.kind
            || preparation.data.target_user_id !== preparedIntent.target_user_id || !sameIntent(preparation.data.intent, preparedIntent.intent)
            || preparation.data.state !== 'prepared') {
            publishOutcome({ ...userOutcome, discovery: 'unavailable', operation: { ...operation, status: 'unknown',
                error: preparation.error?.message ?? 'Preparation was not confirmed. No account mutation was sent. Discover server operations before a new action.' } });
            return false;
        }
        operation = { ...operation, view: preparation.data, status: 'pending' };
        publishOutcome({ ...userOutcome, operation });
        // The native prompt/form lifetime is not the server operation lifetime.
        // A late prepare may be recorded, but cannot dispatch its second request.
        if (!ownsComponent() || !preparation.data.dispatch_eligible) {
            publishOutcome({ ...userOutcome, operation: { ...operation, status: 'unknown', error: 'The prepared action was not dispatched. Read or cancel its exact server operation before another action.' } });
            return false;
        }
        const operationId = preparation.data.operation_id;
        const result = intent.kind === 'create' ? await api.createUser({ ...intent.request, operation_id: operationId, request_id: operationId, password: password ?? '' })
            : intent.kind === 'delete' ? await api.deleteUser(intent.user.id, intent.user.review_token, operationId)
                : intent.kind === 'primary_membership' ? await api.configurePrimaryMembership(intent.user.id, { ...intent.request, operation_id: operationId })
                    : await api.updateUser(intent.user.id, { ...intent.request, operation_id: operationId });
        if (!sameOperation()) return false;
        if (result.error || !matchingOperationReceipt(result.data, preparation.data)) {
            publishOutcome({ ...userOutcome, operation: { ...operation, status: 'unknown',
                error: result.error?.message ?? 'The mutation response did not confirm the exact operation. Its preparation still needs reconciliation.' } });
            return false;
        }
        const completed: UserOperationView = { ...preparation.data, state: 'committed', dispatch_eligible: false,
            terminal_audit_event_id: result.data.audit_event_id!, receipt: result.data };
        publishOutcome({ ...userOutcome, operation: null, pending: userOutcome.pending.filter(item => item.operation_id !== operationId), receipts: receiveTerminal(completed) });
        return true;
    } catch {
        if (sameOperation()) publishOutcome({ ...userOutcome, discovery: 'unavailable', operation: { ...operation, status: 'unknown',
            error: 'The response was unavailable. Discover the server operation; no request will be repeated automatically.' } });
        return false;
    }
}

function UserRecoveryPanel({ onReviewed }: { onReviewed: () => void }) {
    const outcome = useUserOutcome();
    const owns = useOperationOwner();
    const check = async (view: UserOperationView, cancel: boolean) => {
        if (!owns() || userOutcome.recoveryBusy || !userOutcome.pending.some(item => item === view)) return;
        const generation = api.getAuthGeneration();
        publishOutcome({ ...userOutcome, recoveryBusy: view.operation_id });
        const current = () => generation === api.getAuthGeneration() && userOutcome.recoveryBusy === view.operation_id;
        try {
            const result = cancel ? await api.cancelUserOperation(view.operation_id) : await api.getUserOperation(view.operation_id);
            if (!current()) return;
            if (result.error || !matchingView(result.data, view) || cancel && result.data.state === 'prepared')
                throw new Error(result.error?.message ?? 'The exact operation response is unavailable or inconsistent.');
            const next = result.data;
            publishOutcome({ ...userOutcome, recoveryBusy: undefined, discoveryError: undefined,
                pending: next.state === 'prepared' ? userOutcome.pending.map(item => item === view ? next : item)
                    : userOutcome.pending.filter(item => item !== view), receipts: receiveTerminal(next) });
            if (next.state !== 'prepared') { await discoverOperations(owns); if (owns() && !writesBlocked()) onReviewed(); }
        } catch (error) {
            if (current()) publishOutcome({ ...userOutcome, recoveryBusy: undefined, discoveryError: error instanceof Error ? error.message : 'Operation recovery unavailable.' });
        }
    };
    if (outcome.discovery === 'ready' && !outcome.operation && !outcome.pending.length && !outcome.discoveryError) return null;
    const inFlight = outcome.operation?.status === 'preparing' || outcome.operation?.status === 'pending' || !!outcome.recoveryBusy;
    return <div className="error-banner inline" role="alert" aria-label="User action recovery">
        {outcome.operation && <p>{outcome.operation.status === 'preparing' ? 'Preparing the reviewed action.' : outcome.operation.status === 'pending' ? 'Waiting for the account change response.'
            : 'The action for ' + intentLabel(outcome.operation.intent) + ' is not confirmed. No write will be repeated automatically.'}</p>}
        {outcome.operation?.error && <p>{outcome.operation.error}</p>}
        {outcome.discovery === 'loading' && <p>Checking unresolved server operations before enabling new user actions…</p>}
        {outcome.discoveryError && <p>{outcome.discoveryError}</p>}
        {outcome.pending.map(view => <div key={view.operation_id}>
            <p>Unresolved {view.kind.replaceAll('_', ' ')} for {operationLabel(view)}. Operation: {view.operation_id}</p>
            <p>Prepared {view.prepared_at}. {view.dispatch_eligible ? 'The original change may still be sent by its owning request.' : 'The allowed time to send this change expired or is unavailable; expiry is not cancellation or proof of no effect.'} This recovery view will never resend it.</p>
            <button type="button" className="btn btn-secondary" disabled={inFlight} onClick={() => void check(view, false)}>Check exact operation</button>
            <button type="button" className="btn btn-secondary" disabled={inFlight} onClick={() => void check(view, true)}>Cancel unresolved operation</button>
        </div>)}
        <button type="button" className="btn btn-secondary" disabled={inFlight || outcome.discovery === 'loading'} onClick={() => void discoverOperations(owns).then(() => { if (owns() && !writesBlocked()) onReviewed(); })}>Discover server operations</button>
    </div>;
}

function UserOperationHistory() {
    const owns = useOperationOwner();
    const sequence = useRef(0);
    const busy = useRef(false);
    const [items, setItems] = useState<UserOperationView[] | null>(null);
    const [cursor, setCursor] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const renderedRevision = sequence.current;
    const load = async (more = false) => {
        if (!owns() || busy.current || sequence.current !== renderedRevision || more && !cursor) return;
        busy.current = true; setLoading(true); setError(null);
        const revision = ++sequence.current;
        try {
            const result = await api.getUserOperations('terminal', more ? cursor! : undefined);
            if (!owns() || sequence.current !== revision) return;
            if (result.error || !validOperationList(result.data, 'terminal') || more && (result.data.next_cursor === cursor
                || result.data.items.some(item => items?.some(old => old.operation_id === item.operation_id))))
                throw new Error(result.error?.message ?? 'Operation history is incomplete or invalid.');
            setItems(more ? [...items!, ...result.data.items] : result.data.items); setCursor(result.data.next_cursor);
        } catch (err) {
            if (owns() && sequence.current === revision) setError(err instanceof Error ? err.message : 'Operation history unavailable.');
        } finally { busy.current = false; if (owns() && sequence.current === revision) setLoading(false); }
    };
    return <section className="card" aria-label="User operation history">
        <div className="card-header"><h2>Recorded user operations</h2><button type="button" className="btn btn-secondary" disabled={loading} onClick={() => void load()}>View operation history</button></div>
        <div className="card-body">
            <p>Original receipts for this signed-in operator. These are recorded outcomes, not current account state.</p>
            {error && <p role="alert">{error}</p>}
            {loading && <p role="status">Loading operation history…</p>}
            {items?.length === 0 && <p>No terminal operations were returned.</p>}
            {items?.map(view => <div key={view.operation_id}><p>{view.state === 'cancelled' ? 'Cancelled operation; no account change is claimed.' : receiptMessage(view, view.receipt as UserMutationReceipt)}</p>
                <p>Operation: {view.operation_id}. Audit: {view.terminal_audit_event_id}. Prepared: {view.prepared_at}</p></div>)}
            {cursor && <button type="button" className="btn btn-secondary" disabled={loading} onClick={() => void load(true)}>Load more operation history</button>}
        </div>
    </section>;
}

function UserRecoveryDialog({ onReviewed, onClose }: { onReviewed: () => void; onClose: () => void }) {
    const dialog = useRef<HTMLDivElement>(null);
    useDialogFocus(true, dialog, onClose);
    return <div className="modal-overlay"><div ref={dialog} className="modal-content user-modal" role="dialog" aria-modal="true" aria-labelledby="user-recovery-title" tabIndex={-1}>
        <div className="modal-title-row"><h3 id="user-recovery-title">Review user action</h3><button type="button" className="icon-btn" aria-label="Close user recovery" onClick={onClose}><X size={18} /></button></div>
        <UserRecoveryPanel onReviewed={onReviewed} />
    </div></div>;
}

function validMembershipReview(value: unknown, user: AdminUserItem, tenantId: string): value is PrimaryMembershipReview {
    if (!record(value) || !reviewedUser(value.user) || value.user.id !== user.id || value.user.email !== user.email
        || value.user.tenant_id !== user.tenant_id || !record(value.tenant) || value.tenant.id !== tenantId
        || !(value.tenant.name === null || typeof value.tenant.name === 'string') || !membershipToken(value.review_token)
        || typeof value.eligible !== 'boolean' || !(value.reason === null || typeof value.reason === 'string')) return false;
    if (value.membership !== null && !(record(value.membership) && uuid(value.membership.id) && textValue(value.membership.role, 100)
        && textValue(value.membership.status, 100) && typeof value.membership.is_primary === 'boolean')) return false;
    return !value.eligible || (user.tenant_id === null || user.tenant_id === tenantId && value.membership === null)
        && (value.membership === null || (value.membership as Record<string, unknown>).status === 'active');
}

function PrimaryMembershipModal({ user, roleNames, onClose, onSaved, onReviewed }: {
    user: AdminUserItem; roleNames: string[]; onClose: () => void; onSaved: () => void; onReviewed: () => void;
}) {
    const owns = useOperationOwner();
    useUserOutcome();
    const dialog = useRef<HTMLFormElement>(null);
    const selection = useRef(0), pending = useRef(false);
    const [tenantId, setTenantId] = useState(user.tenant_id ?? '');
    const [role, setRole] = useState(roleNames.includes(user.role) && user.role !== 'platform_admin' ? user.role : 'user');
    const [review, setReview] = useState<PrimaryMembershipReview | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [confirmed, setConfirmed] = useState(false);
    const confirmation = useRef(false);
    const renderedSelection = selection.current;
    useDialogFocus(true, dialog, onClose, submitting);
    const invalidate = () => { selection.current++; confirmation.current = false; setReview(null); setConfirmed(false); setError(null); setLoading(false); };
    const load = async () => {
        if (!owns() || pending.current || selection.current !== renderedSelection || !uuid(tenantId)) return;
        const revision = ++selection.current;
        confirmation.current = false; setLoading(true); setReview(null); setConfirmed(false); setError(null);
        try {
            const result = await api.getPrimaryMembershipReview(user.id, tenantId);
            if (!owns() || selection.current !== revision) return;
            if (result.error || !validMembershipReview(result.data, user, tenantId)) throw new Error(result.error?.message ?? 'The exact membership review is unavailable or invalid.');
            setReview(result.data);
        } catch (err) {
            if (owns() && selection.current === revision) setError(err instanceof Error ? err.message : 'Membership review unavailable.');
        } finally { if (owns() && selection.current === revision) setLoading(false); }
    };
    const submit = async (event: React.FormEvent) => {
        event.preventDefault();
        if (!owns() || pending.current || selection.current !== renderedSelection || writesBlocked() || !review?.eligible || !confirmed || !confirmation.current || !roleNames.includes(role) || role === 'platform_admin') return;
        const revision = selection.current;
        const ownsReview = () => owns() && selection.current === revision;
        pending.current = true; setSubmitting(true); setError(null);
        try {
            const saved = await performUserOperation({ kind: 'primary_membership', user: review.user,
                request: { tenant_id: tenantId, role, expected_review_token: review.review_token } }, ownsReview);
            if (ownsReview() && saved) onSaved();
        } finally { pending.current = false; if (owns()) setSubmitting(false); }
    };
    return <div className="modal-overlay"><form ref={dialog} className="modal-content user-modal" role="dialog" aria-modal="true" aria-labelledby="primary-membership-title"
        tabIndex={-1} onSubmit={submit}>
        <div className="modal-title-row"><h3 id="primary-membership-title">Set primary membership</h3><button type="button" className="icon-btn" disabled={submitting} aria-label="Close primary membership" onClick={onClose}><X size={18} /></button></div>
        <p>{user.email} · {user.id}</p>
        <p>This repairs an absent primary association or a missing membership at the existing primary tenant. It cannot transfer an account or reactivate an inactive membership.</p>
        <UserRecoveryPanel onReviewed={onReviewed} />
        {error && <p role="alert">{error}</p>}
        <div className="form-group"><label htmlFor="primary-tenant">Primary tenant</label>
            {user.tenant_id ? <p>Existing primary tenant: {user.tenant_name || user.tenant_id} · {user.tenant_id}</p>
                : <TenantFilter value={tenantId} selectId="primary-tenant" label="Primary membership tenant" searchLabel="Search primary membership tenants" emptyLabel="Select tenant"
                    disabled={submitting} onChange={value => { invalidate(); setTenantId(value); }} />}
        </div>
        <div className="form-group"><label htmlFor="primary-role">Membership role</label><select id="primary-role" className="form-input" value={role} disabled={submitting}
            onChange={event => { invalidate(); setRole(event.target.value); }}>
            {roleNames.filter(value => value !== 'platform_admin').map(value => <option key={value} value={value}>{ROLE_LABELS[value] || value}</option>)}
        </select></div>
        <button type="button" className="btn btn-secondary" disabled={loading || submitting || !uuid(tenantId)} onClick={() => void load()}>{loading ? 'Reviewing membership…' : 'Review exact membership'}</button>
        {review && <div><p>Reviewed tenant: {review.tenant.name || 'Unnamed tenant'} · {review.tenant.id}</p>
            <p>{review.membership ? 'Existing membership: ' + review.membership.id + ' · ' + review.membership.role + ' · ' + review.membership.status : 'No membership exists at this tenant.'}</p>
            {review.eligible ? <><p>The saved primary membership will use role {role}. Existing sessions, refresh tokens and sign-in challenges will be retired. Fresh sign-in is required.</p>
                <label><input type="checkbox" checked={confirmed} disabled={submitting} onChange={event => { confirmation.current = event.target.checked; setConfirmed(event.target.checked); }} /> I reviewed this account, tenant and role, including the fresh sign-in requirement.</label></>
                : <p role="alert">{review.reason || 'This association is not eligible for the limited repair.'}</p>}
        </div>}
        <div className="modal-actions"><button type="button" className="btn btn-secondary" disabled={submitting} onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={submitting || writesBlocked() || !review?.eligible || !confirmed}>Set primary membership</button></div>
    </form></div>;
}

function RoleBadge({ role }: { role: string }) {
    const elevated = role === 'platform_admin' || role === 'partner_admin' || role === 'tenant_admin';
    return (
        <span className={`role-badge ${elevated ? 'role-admin' : 'role-user'}`}>
            {elevated ? <ShieldCheck size={12} /> : <Shield size={12} />}
            {ROLE_LABELS[role] || role}
        </span>
    );
}

export function UsersPage() {
    const generation = useAuthGeneration();
    return <OwnedUsersPage key={generation} />;
}

function useOperationOwner() {
    const mounted = useRef(false);
    const generation = useAuthGeneration();
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; };
    }, []);
    return useCallback(() => mounted.current && generation === api.getAuthGeneration(), [generation]);
}

function OwnedUsersPage() {
    const formalReturnFocus = useRef<HTMLInputElement>(null);
    const owns = useOperationOwner();
    const { user: actor, isAuthenticated } = useAuth();
    const outcome = useUserOutcome();
    const actionsBlocked = writesBlocked();
    const readVersion = useRef(0);
    const mutationPending = useRef(false);
    const [users, setUsers] = useState<AdminUserItem[]>([]);
    const [roles, setRoles] = useState<RbacRole[]>([]);
    const [permissions, setPermissions] = useState<RbacPermission[]>([]);
    const [tenants, setTenants] = useState<TenantListItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [mutationError, setMutationError] = useState<string | null>(null);
    const [referenceError, setReferenceError] = useState<string | null>(null);
    const [referenceRetry, setReferenceRetry] = useState(0);
    const [referencesReady, setReferencesReady] = useState(false);
    const [search, setSearch] = useState('');
    const [roleFilter, setRoleFilter] = useState('');
    const [page, setPage] = useState(1);
    const [hasMore, setHasMore] = useState(false);
    const [busyId, setBusyId] = useState<string | null>(null);

    const [showAdd, setShowAdd] = useState(false);
    const [editUser, setEditUser] = useState<AdminUserItem | null>(null);
    const [confirmDelete, setConfirmDelete] = useState<AdminUserItem | null>(null);
    const deleteSelection = useRef<AdminUserItem | null>(null);
    const [showRecovery, setShowRecovery] = useState(false);
    const [formalUser, setFormalUser] = useState<AdminUserItem | null>(null);
    const [membershipUser, setMembershipUser] = useState<AdminUserItem | null>(null);

    useEffect(() => {
        if (!isAuthenticated || !actor || actor.role !== 'platform_admin') return;
        void discoverOperations(owns);
    }, [actor, isAuthenticated, owns]);

    const fetchUsers = useCallback(async () => {
        if (!owns()) return;
        const version = ++readVersion.current;
        const current = () => owns() && readVersion.current === version;
        setLoading(true);
        setError(null);
        try {
            const res = await api.getUsersList({
                search: search || undefined,
                role: roleFilter || undefined,
                limit: '51', offset: String((page - 1) * 50),
            });
            if (!current()) return;
            if (res.error || !isUserDirectory(res.data)) {
                setError(res.error?.message ?? 'The server returned an invalid user list. Refresh to retry.');
                setUsers([]);
                setHasMore(false);
            } else {
                setUsers(res.data.slice(0, 50));
                setHasMore(res.data.length > 50);
            }
        } catch (err) {
            if (!current()) return;
            setError(err instanceof Error ? err.message : 'Failed to fetch users');
            setUsers([]);
            setHasMore(false);
        } finally {
            if (current()) setLoading(false);
        }
    }, [search, roleFilter, page, owns]);
    const refreshLatest = useRef(fetchUsers);
    useEffect(() => { refreshLatest.current = fetchUsers; }, [fetchUsers]);

    // One-time reference data: roles, permissions, tenants (for the add form).
    useEffect(() => {
        let current = true;
        (async () => {
            setReferencesReady(false);
            setReferenceError(null);
            try {
            const [r, p, t] = await Promise.all([
                api.getRoles(), api.getPermissions(), api.getTenants(),
            ]);
            if (!current || !owns()) return;
            if (r.data) setRoles(r.data);
            if (p.data) setPermissions(p.data);
            if (t.data) setTenants(t.data);
            setReferencesReady(Boolean(r.data?.length && t.data && !r.error && !t.error));
            const failures = [r.error && `Roles: ${r.error.message}`, p.error && `Permissions: ${p.error.message}`, t.error && `Tenants: ${t.error.message}`].filter(Boolean);
            if (failures.length) setReferenceError(failures.join(' '));
            } catch {
                if (current && owns()) setReferenceError('Could not load role and tenant reference data.');
            }
        })();
        return () => { current = false; };
    }, [owns, referenceRetry]);

    useEffect(() => {
        const t = setTimeout(fetchUsers, 250);
        return () => { clearTimeout(t); readVersion.current += 1; };
    }, [fetchUsers]);

    const flashNotice = (msg: string) => {
        setNotice(msg);
    };

    const roleNames = roles.filter((r) => EDITABLE_ROLES.includes(r.name)).map((r) => r.name);
    const displayedReadVersion = readVersion.current;

    const toggleBlock = async (u: AdminUserItem) => {
        if (!owns() || displayedReadVersion !== readVersion.current || mutationPending.current || userOutcome.operation || !reviewedUser(u)) return;
        mutationPending.current = true;
        setBusyId(u.id);
        setMutationError(null);
        try {
        const saved = await performUserOperation({ kind: 'block', user: u, request: { is_active: !isActive(u), expected_review_token: u.review_token } },
            () => owns() && displayedReadVersion === readVersion.current);
        if (!owns()) return;
        if (saved) void refreshLatest.current();
        } catch {
            if (owns()) setMutationError('The user update could not be confirmed. Refresh before retrying.');
        } finally {
            mutationPending.current = false;
            if (owns()) setBusyId(null);
        }
    };

    const doDelete = async () => {
        if (!confirmDelete || deleteSelection.current !== confirmDelete || !owns() || mutationPending.current || userOutcome.operation || !reviewedUser(confirmDelete)) return;
        mutationPending.current = true;
        const u = confirmDelete;
        setBusyId(u.id);
        setMutationError(null);
        try {
        const saved = await performUserOperation({ kind: 'delete', user: u }, () => owns() && deleteSelection.current === u);
        if (!owns()) return;
        if (!saved) return;
        deleteSelection.current = null;
        setConfirmDelete(null);
        void refreshLatest.current();
        } catch {
            if (owns()) setMutationError('Removal could not be confirmed. Refresh before retrying.');
        } finally {
            mutationPending.current = false;
            if (owns()) setBusyId(null);
        }
    };

    const reviewNewAction = () => {
        if (!owns() || writesBlocked()) return;
        setShowAdd(false); setEditUser(null); setConfirmDelete(null); setShowRecovery(false); setMembershipUser(null); deleteSelection.current = null;
        flashNotice('No unresolved server operations were returned. Review a fresh action before saving.');
        void refreshLatest.current();
    };

    // Group permissions by resource for the reference panel.
    const permsByResource = permissions.reduce<Record<string, RbacPermission[]>>((acc, p) => {
        (acc[p.resource] ||= []).push(p);
        return acc;
    }, {});

    return (
        <div className="app-layout">
            <Sidebar />
            <main className="main-content">
                <Header />
                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon"><Users /></div>
                        <div>
                            <h1 className="page-title">Users &amp; Roles</h1>
                            <p className="page-description">Add users, assign roles, and block or remove access</p>
                        </div>
                        <div className="page-header-actions">
                            <button className="btn btn-primary" onClick={() => { if (owns()) setShowAdd(true); }} disabled={!referencesReady || busyId !== null || actionsBlocked}>
                                <UserPlus size={16} /> Add User
                            </button>
                            <button className="btn btn-secondary" onClick={fetchUsers} disabled={loading}>
                                <RefreshCw size={16} className={loading ? 'spinning' : ''} /> Refresh
                            </button>
                        </div>
                    </div>

                    {notice && <div className="success-banner" role="status">{notice}<button onClick={() => setNotice(null)}>Dismiss</button></div>}
                    {outcome.receipts.map((receipt) => <div className="success-banner" role="status" key={receipt.auditId}>{receipt.message} Audit: {receipt.auditId}</div>)}
                    {!showAdd && !editUser && !confirmDelete && !showRecovery && !membershipUser && <>
                        <UserRecoveryPanel onReviewed={reviewNewAction} />
                        {(outcome.operation || outcome.pending.length > 0) && <button type="button" className="btn btn-secondary" onClick={() => { if (owns()) setShowRecovery(true); }}>Review unresolved action</button>}
                    </>}
                    {mutationError && <div className="error-banner" role="alert">{mutationError}</div>}
                    {referenceError && <div className="error-banner" role="alert">{referenceError}<button onClick={() => setReferenceRetry((n) => n + 1)}>Reload reference data</button></div>}
                    {error && (
                        <div className="error-banner">
                            <p>{error}</p>
                            <button onClick={fetchUsers}>Retry</button>
                        </div>
                    )}

                    {/* Users table */}
                    <div className="card">
                        <div className="card-header">
                            <h3 className="card-title">All Users</h3>
                            <span className="card-count">{users.length} shown · Page {page}</span>
                        </div>
                        <div className="card-body">
                            <div className="table-toolbar">
                                <div className="search-box">
                                    <Search size={16} />
                                    <input
                                        type="text"
                                        placeholder="Search by name or email…"
                                        value={search}
                                        onChange={(e) => { readVersion.current += 1; setSearch(e.target.value); setPage(1); }}
                                        aria-label="Search users"
                                        ref={formalReturnFocus}
                                        className="search-input"
                                    />
                                </div>
                                <div className="filter-group">
                                    <Filter size={16} />
                                    <select
                                        value={roleFilter}
                                        onChange={(e) => { readVersion.current += 1; setRoleFilter(e.target.value); setPage(1); }}
                                        aria-label="Filter users by role"
                                        className="filter-select"
                                    >
                                        <option value="">All Roles</option>
                                        {roleNames.map((r) => (
                                            <option key={r} value={r}>{ROLE_LABELS[r] || r}</option>
                                        ))}
                                    </select>
                                </div>
                            </div>

                            <div className="table-container">
                                {loading ? (
                                    <div className="table-loading">
                                        <RefreshCw className="spinning" size={20} />
                                        <span>Loading users…</span>
                                    </div>
                                ) : error ? <div role="status">User list unavailable.</div> : users.length === 0 ? (
                                    <div className="empty-state">
                                        <Users size={44} strokeWidth={1} />
                                        <h3>No users found</h3>
                                        <p>Add a user to get started, or adjust your filters.</p>
                                    </div>
                                ) : (
                                    <table className="data-table">
                                        <thead>
                                            <tr>
                                                <th>Name</th>
                                                <th>Email</th>
                                                <th>Current role assignment</th>
                                                <th>Tenant</th>
                                                <th>Account status</th>
                                                <th>MFA</th>
                                                <th>Created</th>
                                                <th style={{ textAlign: 'right' }}>Actions</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {users.map((u) => {
                                                const active = isActive(u);
                                                return (
                                                    <tr key={u.id} className={active ? '' : 'row-blocked'}>
                                                        <td>{u.name || '-'}</td>
                                                        <td>{u.email}</td>
                                                        <td>
                                                            {u.effective_role ? <RoleBadge role={u.effective_role} /> : <>
                                                                <span className="status-badge status-error">
                                                                    {u.effective_role === null ? 'No active assignment' : 'Assignment unverified'}
                                                                </span>
                                                                <div className="text-muted">Stored assignment: {ROLE_LABELS[u.role] || u.role}</div>
                                                                {u.membership_status && <div className="text-muted">Membership: {u.membership_status}</div>}
                                                            </>}
                                                        </td>
                                                        <td>
                                                            <div className="tenant-name-cell">
                                                                <Building2 size={14} />
                                                                <span>{u.tenant_name || u.tenant_id || '—'}</span>
                                                            </div>
                                                        </td>
                                                        <td>
                                                            <span className={`status-badge ${active ? 'status-active' : 'status-error'}`}>
                                                                <span className={`status-dot ${active ? 'green' : 'red'}`} />
                                                                {active ? 'Active' : 'Blocked'}
                                                            </span>
                                                        </td>
                                                        <td>{u.mfa_enabled ? 'On' : 'Off'}</td>
                                                        <td>{formatDate(u.created_at)}</td>
                                                        <td>
                                                            <div className="user-actions">
                                                                {!reviewedUser(u) && <span className="text-muted">Review data unavailable; refresh before changing this account.</span>}
                                                                <button className="icon-btn" title="Formal holds" aria-label={`Formal holds for ${u.email}`}
                                                                    disabled={busyId !== null || actionsBlocked} onClick={() => setFormalUser(u)}><Lock size={15} /></button>
                                                                {(u.tenant_id === null || u.membership_status === 'missing' || u.membership_status === 'not_required') && <button
                                                                    className="icon-btn" title="Set primary membership" aria-label={`Set primary membership for ${u.email}`}
                                                                    disabled={busyId !== null || actionsBlocked || !referencesReady || !reviewedUser(u)}
                                                                    onClick={() => { if (owns()) setMembershipUser(u); }}><Building2 size={15} /></button>}
                                                                <button
                                                                    className="icon-btn"
                                                                    title="Edit role / name"
                                                                    aria-label={`Edit ${u.email}`}
                                                                    disabled={busyId !== null || actionsBlocked || !referencesReady || !reviewedUser(u)}
                                                                    onClick={() => setEditUser(u)}
                                                                >
                                                                    <Pencil size={15} />
                                                                </button>
                                                                <button
                                                                    className={`icon-btn ${active ? 'warn' : 'ok'}`}
                                                                    title={active ? 'Block user' : 'Unblock user'}
                                                                    aria-label={`${active ? 'Block' : 'Unblock'} ${u.email}`}
                                                                    disabled={busyId !== null || actionsBlocked || !reviewedUser(u)}
                                                                    onClick={() => toggleBlock(u)}
                                                                >
                                                                    {active ? <Ban size={15} /> : <CheckCircle2 size={15} />}
                                                                </button>
                                                                <button
                                                                    className="icon-btn danger"
                                                                    title="Delete user"
                                                                    aria-label={`Delete ${u.email}`}
                                                                    disabled={busyId !== null || actionsBlocked || !reviewedUser(u)}
                                                                    onClick={() => { const selected = { ...u }; deleteSelection.current = selected; setConfirmDelete(selected); }}
                                                                >
                                                                    <Trash2 size={15} />
                                                                </button>
                                                            </div>
                                                        </td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                )}
                            </div>
                            <div className="table-toolbar" aria-label="User pages">
                                <button className="btn btn-secondary" disabled={page === 1 || loading} onClick={() => { readVersion.current += 1; setPage((value) => value - 1); }}>Previous users</button>
                                <button className="btn btn-secondary" disabled={!hasMore || loading || Boolean(error)} onClick={() => { readVersion.current += 1; setPage((value) => value + 1); }}>Next users</button>
                            </div>
                            <p className="form-help">Role assignment reflects the current profile and membership. Review Formal holds for additional account restrictions.</p>
                        </div>
                    </div>

                    <UserOperationHistory />
                    {/* Roles & Permissions reference */}
                    <div className="card">
                        <div className="card-header">
                            <h3 className="card-title"><KeyRound size={18} /> Roles &amp; Permissions</h3>
                            <span className="card-count">{roleNames.length} roles</span>
                        </div>
                        <div className="card-body roles-panel">
                            <div className="roles-list">
                                {[...roles]
                                    .sort((a, b) => b.level - a.level)
                                    .map((r) => (
                                        <div key={r.id} className="role-row">
                                            <div className="role-row-main">
                                                <RoleBadge role={r.name} />
                                                <span className="role-level">level {r.level}</span>
                                            </div>
                                            <p className="role-desc">
                                                {r.description || 'No description provided.'}
                                            </p>
                                        </div>
                                    ))}
                            </div>

                            {permissions.length > 0 && (
                                <div className="perms-block">
                                    <h4 className="perms-title"><Lock size={14} /> Permissions ({permissions.length})</h4>
                                    <div className="perms-grid">
                                        {Object.entries(permsByResource).sort().map(([res, perms]) => (
                                            <div key={res} className="perm-group">
                                                <span className="perm-resource">{res}</span>
                                                <div className="perm-actions">
                                                    {perms.map((p) => (
                                                        <span key={p.id} className="perm-chip" title={p.description || p.name}>
                                                            {p.action}
                                                        </span>
                                                    ))}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            </main>

            {formalUser && <FormalHoldPanel target={{ type: 'user', id: formalUser.id, label: formalUser.email }}
                returnFocusFallback={formalReturnFocus} onClose={() => setFormalUser(null)} onChanged={() => { void fetchUsers(); }} />}
            {membershipUser && <PrimaryMembershipModal key={membershipUser.id} user={membershipUser} roleNames={roleNames}
                onClose={() => setMembershipUser(null)} onSaved={() => { setMembershipUser(null); void fetchUsers(); }} onReviewed={reviewNewAction} />}
            {showAdd && (
                <AddUserModal
                    roleNames={roleNames}
                    tenants={tenants}
                    onClose={() => setShowAdd(false)}
                    onCreated={() => { setShowAdd(false); fetchUsers(); }}
                    onReviewed={reviewNewAction}
                />
            )}

            {editUser && (
                <EditUserModal
                    key={editUser.id}
                    user={editUser}
                    roleNames={roleNames}
                    onClose={() => setEditUser(null)}
                    onSaved={() => { setEditUser(null); fetchUsers(); }}
                    onReviewed={reviewNewAction}
                />
            )}

            {confirmDelete && !actionsBlocked && (
                <ConfirmationModal isOpen title="Delete user"
                    message={`Remove ${confirmDelete.email}? Unreferenced accounts are permanently deleted; accounts with linked records are deactivated to preserve their history.`}
                    confirmLabel="Delete" onConfirm={() => void doDelete()}
                    onCancel={() => { if (!mutationPending.current) { deleteSelection.current = null; setConfirmDelete(null); } }}
                    loading={busyId !== null} error={mutationError} />
            )}
            {(confirmDelete && actionsBlocked || showRecovery && actionsBlocked) && <UserRecoveryDialog onReviewed={reviewNewAction} onClose={() => { deleteSelection.current = null; setConfirmDelete(null); setShowRecovery(false); }} />}
        </div>
    );
}

// ── Add User modal ──────────────────────────────────────────────────────────
function AddUserModal({ roleNames, tenants, onClose, onCreated, onReviewed }: {
    roleNames: string[];
    tenants: TenantListItem[];
    onClose: () => void;
    onCreated: (email: string) => void;
    onReviewed: () => void;
}) {
    const owns = useOperationOwner();
    useUserOutcome();
    const blocked = writesBlocked();
    const pending = useRef(false);
    const dialog = useRef<HTMLFormElement>(null);
    const [form, setForm] = useState<Omit<CreateUserRequest, 'request_id' | 'operation_id'>>({
        name: '', email: '', password: '', role: 'user', tenant_id: '',
    });
    const [submitting, setSubmitting] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const [tenantSearch, setTenantSearch] = useState('');
    const [tenantPage, setTenantPage] = useState(0);
    const [tenantRevision, setTenantRevision] = useState(0);
    const [tenantMore, setTenantMore] = useState(tenants.length > 50);
    const [tenantLabel, setTenantLabel] = useState('Selected tenant');
    const [tenantOptions, setTenantOptions] = useState(tenants.slice(0, 50));
    const [tenantSearchError, setTenantSearchError] = useState<string | null>(null);
    const [tenantLoading, setTenantLoading] = useState(true);
    useDialogFocus(true, dialog, onClose, submitting);

    useEffect(() => {
        let current = true;
        const timer = setTimeout(async () => {
            setTenantLoading(true); setTenantSearchError(null);
            try {
                const result = await api.getTenants(tenantSearch || undefined, undefined, 51, tenantPage * 50);
                if (!current || !owns()) return;
                if (result.error || !Array.isArray(result.data)) { setTenantSearchError(result.error?.message ?? 'Tenant search unavailable.'); setTenantOptions([]); setTenantMore(false); }
                else { setTenantOptions(result.data.slice(0, 50)); setTenantMore(result.data.length > 50); }
            } catch {
                if (current && owns()) { setTenantSearchError('Tenant search unavailable.'); setTenantOptions([]); }
            } finally { if (current && owns()) setTenantLoading(false); }
        }, 250);
        return () => { current = false; clearTimeout(timer); };
    }, [tenantSearch, tenantPage, tenantRevision, owns]);

    const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }));

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!owns() || pending.current || userOutcome.operation) return;
        if (!form.name.trim() || !form.email || !form.password) {
            setErr('Name, email and a temporary password are required.');
            return;
        }
        if (form.role !== 'platform_admin' && (!form.tenant_id || tenantLoading || tenantSearchError)) {
            setErr('Select a tenant for this account role.'); return;
        }
        pending.current = true;
        setSubmitting(true);
        setErr(null);
        try {
        const saved = await performUserOperation({ kind: 'create', request: {
            role: form.role,
            name: form.name.trim(), email: form.email.trim().toLowerCase(),
            tenant_id: form.tenant_id || null,
        } }, owns, form.password);
        if (!owns()) return;
        if (saved) onCreated(form.email);
        } catch {
            if (owns()) setErr('Account creation could not be confirmed. Check the user list before retrying.');
        } finally {
            pending.current = false;
            if (owns()) setSubmitting(false);
        }
    };

    return (
        <div className="modal-overlay" onClick={() => { if (!pending.current) onClose(); }}>
            <form ref={dialog} role="dialog" aria-modal="true" aria-labelledby="add-user-title" tabIndex={-1} className="modal-content user-modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
                <div className="modal-title-row">
                    <h3 id="add-user-title" className="modal-title">Add user</h3>
                    <button type="button" className="icon-btn" onClick={onClose} disabled={submitting} aria-label="Close add user"><X size={18} /></button>
                </div>

                {err && <div className="error-banner inline" role="alert"><p>{err}</p></div>}
                <UserRecoveryPanel onReviewed={onReviewed} />

                <div className="form-group">
                    <label htmlFor="add-user-name">Full name</label>
                    <input id="add-user-name" data-dialog-autofocus className="form-input" value={form.name} required disabled={submitting}
                        onChange={(e) => set('name', e.target.value)} placeholder="Jane Doe" />
                </div>
                <div className="form-group">
                    <label htmlFor="add-user-email">Email</label>
                    <input id="add-user-email" className="form-input" type="email" value={form.email} required disabled={submitting}
                        onChange={(e) => set('email', e.target.value)} placeholder="jane@company.com" />
                </div>
                <div className="form-group">
                    <label htmlFor="add-user-password">Temporary password</label>
                    <input id="add-user-password" className="form-input" type="password" value={form.password} required minLength={8} maxLength={128} disabled={submitting} autoComplete="new-password"
                        onChange={(e) => set('password', e.target.value)} placeholder="They can change it after first login" />
                    <span className="form-help">Share this with the user securely; they sign in with it.</span>
                </div>
                <div className="form-row">
                    <div className="form-group">
                        <label htmlFor="add-user-role">Role</label>
                        <select id="add-user-role" className="form-input" value={form.role} onChange={(e) => set('role', e.target.value)} disabled={submitting}>
                            {roleNames.map((r) => <option key={r} value={r}>{ROLE_LABELS[r] || r}</option>)}
                        </select>
                    </div>
                    <div className="form-group">
                        <label htmlFor="add-user-tenant-search">Find tenant by name</label>
                        <input id="add-user-tenant-search" className="form-input" value={tenantSearch} disabled={submitting}
                            onChange={(e) => { setTenantSearch(e.target.value); setTenantPage(0); set('tenant_id', ''); setTenantLoading(true); }} />
                        <label htmlFor="add-user-tenant">Tenant {form.role === 'platform_admin' ? '(optional)' : '(required)'}</label>
                        <select id="add-user-tenant" className="form-input" value={form.tenant_id || ''} onChange={(e) => { set('tenant_id', e.target.value); setTenantLabel(e.target.selectedOptions[0]?.textContent ?? 'Selected tenant'); }} required={form.role !== 'platform_admin'} disabled={submitting || tenantLoading}>
                            <option value="">— Select tenant —</option>
                            {form.tenant_id && !tenantOptions.some((t) => t.id === form.tenant_id) && <option value={form.tenant_id}>{tenantLabel}</option>}
                            {tenantOptions.map((t) => <option key={t.id} value={t.id}>{t.business_name} · {t.id}</option>)}
                        </select>
                        {tenantLoading && <span role="status">Finding tenants…</span>}
                        {tenantSearchError && <span role="alert">{tenantSearchError} <button type="button" disabled={submitting} onClick={() => { setTenantLoading(true); setTenantRevision((v) => v + 1); }}>Retry tenant lookup</button></span>}
                        <div className="pagination">
                            <button type="button" disabled={submitting || tenantLoading || tenantPage === 0} onClick={() => { setTenantLoading(true); setTenantPage((v) => v - 1); }}>Previous tenant options</button>
                            <span>Page {tenantPage + 1}</span>
                            <button type="button" disabled={submitting || tenantLoading || !tenantMore} onClick={() => { setTenantLoading(true); setTenantPage((v) => v + 1); }}>Next tenant options</button>
                        </div>
                        <span className="form-help">Search or page through tenants. Use the ID to distinguish matching names.</span>
                    </div>
                </div>

                <div className="modal-actions">
                    <button type="button" className="btn btn-secondary" onClick={onClose} disabled={submitting}>Cancel</button>
                    <button type="submit" className="btn btn-primary" disabled={submitting || blocked}>
                        <UserPlus size={15} /> {submitting ? 'Creating…' : 'Create user'}
                    </button>
                </div>
            </form>
        </div>
    );
}

// ── Edit User modal (name + role) ───────────────────────────────────────────
function EditUserModal({ user, roleNames, onClose, onSaved, onReviewed }: {
    user: AdminUserItem;
    roleNames: string[];
    onClose: () => void;
    onSaved: (email: string) => void;
    onReviewed: () => void;
}) {
    const owns = useOperationOwner();
    useUserOutcome();
    const pending = useRef(false);
    const dialog = useRef<HTMLFormElement>(null);
    const [name, setName] = useState(user.name ?? '');
    const [role, setRole] = useState(user.role);
    const [submitting, setSubmitting] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const nameChanged = name.trim() !== (user.name ?? '').trim();
    const roleChanged = role !== user.role;
    useDialogFocus(true, dialog, onClose, submitting);

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!owns() || pending.current || userOutcome.operation || !reviewedUser(user)) return;
        if (!nameChanged && !roleChanged) return;
        const changes: DraftUpdate = { expected_review_token: user.review_token };
        if (nameChanged) changes.name = name.trim();
        if (roleChanged) { changes.role = role; changes.expected_role = user.role; }
        pending.current = true;
        setSubmitting(true);
        setErr(null);
        try {
        const saved = await performUserOperation({ kind: 'edit', user, request: changes }, owns);
        if (!owns()) return;
        if (saved) onSaved(user.email);
        } catch {
            if (owns()) setErr('The update could not be confirmed. Refresh before retrying.');
        } finally {
            pending.current = false;
            if (owns()) setSubmitting(false);
        }
    };

    return (
        <div className="modal-overlay" onClick={() => { if (!pending.current) onClose(); }}>
            <form ref={dialog} role="dialog" aria-modal="true" aria-labelledby="edit-user-title" tabIndex={-1} className="modal-content user-modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
                <div className="modal-title-row">
                    <h3 id="edit-user-title" className="modal-title">Edit user</h3>
                    <button type="button" className="icon-btn" onClick={onClose} disabled={submitting} aria-label="Close edit user"><X size={18} /></button>
                </div>
                <p className="modal-description">{user.email}</p>

                {err && <div className="error-banner inline" role="alert"><p>{err}</p></div>}
                <UserRecoveryPanel onReviewed={onReviewed} />

                <div className="form-group">
                    <label htmlFor="edit-user-name">Full name</label>
                    <input id="edit-user-name" data-dialog-autofocus className="form-input" value={name} onChange={(e) => setName(e.target.value)} disabled={submitting} />
                </div>
                <div className="form-group">
                    <label htmlFor="edit-user-role">Role</label>
                    <select id="edit-user-role" className="form-input" value={role} onChange={(e) => setRole(e.target.value)} disabled={submitting}>
                        {roleNames.map((r) => <option key={r} value={r}>{ROLE_LABELS[r] || r}</option>)}
                    </select>
                </div>

                <div className="modal-actions">
                    <button type="button" className="btn btn-secondary" onClick={onClose} disabled={submitting}>Cancel</button>
                    <button type="submit" className="btn btn-primary" disabled={submitting || writesBlocked() || (!nameChanged && !roleChanged)}>
                        {submitting ? 'Saving…' : 'Save changes'}
                    </button>
                </div>
            </form>
        </div>
    );
}
