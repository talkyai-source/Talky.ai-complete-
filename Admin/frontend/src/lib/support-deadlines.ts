// Deadline reading for the queue: which deadline is nearest, whether it is
// missed or due within the hour, and a per-page sort. The backend orders the
// queue by last update with a keyset cursor, so sorting here covers only the
// page that was fetched; the UI says so.
import type { QueueSla, StaffQueueItem, SupportStatus } from './support-contracts.ts';

export type DeadlineState = 'overdue' | 'at_risk' | 'ok' | 'none';
export const AT_RISK_WINDOW_MS = 60 * 60 * 1000;

const parse = (value: string | null): number | null => {
    if (!value) return null;
    const time = Date.parse(value);
    return Number.isFinite(time) ? time : null;
};

/** The earliest deadline still standing. The list has no first_responded_at, so both deadlines count. */
export function nearestDeadline(sla: QueueSla & { first_responded_at?: string | null }): string | null {
    const candidates = [sla.resolution_due_at, sla.first_responded_at ? null : sla.first_response_due_at]
        .map((value) => ({ value, time: parse(value ?? null) }))
        .filter((item): item is { value: string; time: number } => item.time !== null)
        .sort((a, b) => a.time - b.time);
    return candidates[0]?.value ?? null;
}

export function deadlineState(sla: QueueSla & { first_responded_at?: string | null }, status: SupportStatus, now: number): DeadlineState {
    if (status === 'resolved' || status === 'closed') return 'none';
    if (sla.breached) return 'overdue';
    const nearest = parse(nearestDeadline(sla));
    if (nearest === null) return 'none';
    if (nearest <= now) return 'overdue';
    if (nearest - now <= AT_RISK_WINDOW_MS) return 'at_risk';
    return 'ok';
}

/** Nearest deadline first within the fetched page; finished tickets last. */
export function sortPageByDeadline(items: StaffQueueItem[]): StaffQueueItem[] {
    const key = (item: StaffQueueItem) => {
        if (item.status === 'resolved' || item.status === 'closed') return Number.POSITIVE_INFINITY;
        return parse(nearestDeadline(item.sla)) ?? Number.MAX_SAFE_INTEGER;
    };
    return [...items].sort((a, b) => key(a) - key(b) || a.updated_at.localeCompare(b.updated_at));
}
