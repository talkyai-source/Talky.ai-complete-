import { AlertTriangle, Ban, CheckCircle, Clock, Loader2, RefreshCw, XCircle } from 'lucide-react';
import type { ActionStatus } from '../lib/api';

const statuses: Record<ActionStatus, { icon: typeof Clock; label: string }> = {
    pending: { icon: Loader2, label: 'Pending' },
    running: { icon: RefreshCw, label: 'Running' },
    scheduled: { icon: Clock, label: 'Scheduled' },
    unknown: { icon: AlertTriangle, label: 'Unknown' },
    completed: { icon: CheckCircle, label: 'Completed' },
    failed: { icon: XCircle, label: 'Failed' },
    cancelled: { icon: Ban, label: 'Cancelled' },
};

export function ActionStatusBadge({ status }: { status: ActionStatus }) {
    const known = Object.hasOwn(statuses, status);
    const { icon: Icon, label } = known ? statuses[status] : { icon: AlertTriangle, label: 'Unrecognized status' };
    return <span className={`action-status-badge status-${known ? status : 'unknown'}`}>
        <Icon size={12} className={status === 'pending' || status === 'running' ? 'spinning' : ''} />
        {label}
    </span>;
}
