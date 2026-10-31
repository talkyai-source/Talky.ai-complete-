import { Link } from 'react-router-dom';
import { PRIORITY_LABELS, STATUS_LABELS, categoryLabel, type StaffQueueItem } from '../lib/support-contracts';
import { deadlineState, nearestDeadline, sortPageByDeadline } from '../lib/support-deadlines';
import { formatStamp } from '../lib/support-format';
import './SupportConsole.css';

const DEADLINE_TEXT = { overdue: 'Overdue', at_risk: 'Due within 1 hour', ok: '', none: '' } as const;

/** One queue page. The requester column stays empty: the list endpoint does not return one. */
export function SupportQueueTable({ items, now, sortByDeadline }: { items: StaffQueueItem[]; now: number; sortByDeadline: boolean }) {
    const rows = sortByDeadline ? sortPageByDeadline(items) : items;
    return (
        <div className="table-container">
            <table className="data-table">
                <thead>
                    <tr>
                        <th scope="col">Number</th>
                        <th scope="col">Subject</th>
                        <th scope="col">Company</th>
                        <th scope="col">Requester <small>(not provided by the server)</small></th>
                        <th scope="col">Category</th>
                        <th scope="col">Priority</th>
                        <th scope="col">Status</th>
                        <th scope="col">Assignee</th>
                        <th scope="col">Deadline</th>
                        <th scope="col">Last update</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((item) => {
                        const state = deadlineState(item.sla, item.status, now);
                        const deadline = nearestDeadline(item.sla);
                        const stateClass = state === 'at_risk' ? 'at-risk' : state;
                        return (
                            <tr key={item.id}>
                                <td><Link to={`/support/${encodeURIComponent(item.id)}`}>{item.number}</Link>{item.unread ? <span className="status-badge status-active" style={{ marginLeft: 6 }}>Unread</span> : null}</td>
                                <td>{item.subject}</td>
                                <td><Link to="/tenants" title="Opens the Tenants list">{item.tenant.name || item.tenant.id}</Link></td>
                                <td aria-label="Requester not provided by the server">—</td>
                                <td>{categoryLabel(item.category)}</td>
                                <td>{PRIORITY_LABELS[item.priority]}</td>
                                <td><span className="status-badge status-inactive">{STATUS_LABELS[item.status]}</span></td>
                                <td>{item.assignee ? item.assignee.name || item.assignee.id : 'Unassigned'}</td>
                                <td className={`support-deadline ${stateClass}`}>
                                    {state === 'none' ? '—' : formatStamp(deadline)}
                                    {DEADLINE_TEXT[state] ? <small> · {DEADLINE_TEXT[state]}</small> : null}
                                </td>
                                <td>{formatStamp(item.updated_at)}</td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}
