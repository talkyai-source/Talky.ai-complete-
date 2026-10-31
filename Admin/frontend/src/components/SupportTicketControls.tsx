import { useState } from 'react';
import { ConfirmationModal } from './ConfirmationModal';
import {
    PRIORITY_LABELS, STATUS_LABELS, SUPPORT_CATEGORIES, SUPPORT_PRIORITIES, SUPPORT_STATUSES,
    type StaffTicketDetail, type StaffTicketPatch, type SupportCategory, type SupportPriority, type SupportStatus,
} from '../lib/support-contracts';
import './SupportConsole.css';

export type TicketChanges = Omit<StaffTicketPatch, 'request_id' | 'version'>;

/**
 * Status, priority, category and assignee edits for one ticket. The parent
 * owns the PATCH (request_id + version); mount with key={ticket.version} so
 * the drafts restart from the saved record after every write or reload.
 */
export function SupportTicketControls({ ticket, currentUserId, busy, onSave }: {
    ticket: StaffTicketDetail; currentUserId: string | null; busy: boolean; onSave: (changes: TicketChanges) => void;
}) {
    const [status, setStatus] = useState<SupportStatus>(ticket.status);
    const [priority, setPriority] = useState<SupportPriority>(ticket.priority);
    const [category, setCategory] = useState<string>(ticket.category);
    const [confirmClose, setConfirmClose] = useState(false);
    const closed = ticket.status === 'closed';

    const changes = (): TicketChanges => {
        const next: TicketChanges = {};
        if (status !== ticket.status) next.status = status;
        if (priority !== ticket.priority) next.priority = priority;
        if (category !== ticket.category) next.category = category as SupportCategory;
        return next;
    };
    const dirty = Object.keys(changes()).length > 0;
    const submit = () => {
        const next = changes();
        if (next.status === 'closed') { setConfirmClose(true); return; }
        onSave(next);
    };
    const assignedToMe = Boolean(currentUserId && ticket.assignee?.id === currentUserId);

    return (
        <div className="card">
            <div className="card-header"><h2 className="card-title">Ticket controls</h2></div>
            <div className="card-body">
                <div className="support-controls">
                    <div className="support-field">
                        <label htmlFor="support-status">Status</label>
                        <select id="support-status" value={status} disabled={busy || closed} onChange={(event) => setStatus(event.target.value as SupportStatus)}>
                            {SUPPORT_STATUSES.map((value) => <option key={value} value={value}>{STATUS_LABELS[value]}</option>)}
                        </select>
                    </div>
                    <div className="support-field">
                        <label htmlFor="support-priority">Priority</label>
                        <select id="support-priority" value={priority} disabled={busy || closed} onChange={(event) => setPriority(event.target.value as SupportPriority)}>
                            {SUPPORT_PRIORITIES.map((value) => <option key={value} value={value}>{PRIORITY_LABELS[value]}</option>)}
                        </select>
                    </div>
                    <div className="support-field">
                        <label htmlFor="support-category">Category</label>
                        <select id="support-category" value={category} disabled={busy || closed} onChange={(event) => setCategory(event.target.value)}>
                            {SUPPORT_CATEGORIES.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
                        </select>
                    </div>
                    <div className="support-field">
                        <span>Assignee</span>
                        <div className="support-actions">
                            <span>{ticket.assignee ? ticket.assignee.name || ticket.assignee.email || ticket.assignee.id : 'Unassigned'}</span>
                            <button type="button" className="btn btn-secondary btn-sm" disabled={busy || closed || assignedToMe || !currentUserId}
                                onClick={() => currentUserId && onSave({ assignee_user_id: currentUserId })}>Assign to me</button>
                            {ticket.assignee && <button type="button" className="btn btn-secondary btn-sm" disabled={busy || closed}
                                onClick={() => onSave({ assignee_user_id: null })}>Unassign</button>}
                        </div>
                    </div>
                    <div className="support-field">
                        <span aria-hidden="true">&nbsp;</span>
                        <button type="button" className="btn btn-primary" disabled={busy || closed || !dirty} onClick={submit}>
                            {busy ? 'Saving…' : 'Save changes'}
                        </button>
                    </div>
                </div>
                {closed && <p className="support-note">A closed ticket cannot be changed.</p>}
                <p className="support-note">Saved against version {ticket.version}. If someone else changes the ticket first, the save is refused and you reload before deciding again.</p>
            </div>
            <ConfirmationModal
                isOpen={confirmClose}
                title="Close this ticket?"
                message="Closed is final for everyone: neither staff nor the customer can reopen or reply to a closed ticket."
                confirmLabel="Close ticket"
                variant="warning"
                loading={busy}
                onCancel={() => setConfirmClose(false)}
                onConfirm={() => { setConfirmClose(false); onSave(changes()); }}
            />
        </div>
    );
}
