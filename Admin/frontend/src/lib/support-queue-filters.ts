// The queue's filters live in the page URL so a view can be shared and
// survives a reload. Parsing is strict: an unknown value becomes "any",
// never a request the backend would reject.
import { SUPPORT_CATEGORY_IDS, SUPPORT_PRIORITIES, SUPPORT_STATUSES } from './support-contracts.ts';

export interface QueueFilters {
    status: string; priority: string; category: string;
    /** '' = anyone, 'me', 'unassigned'. */
    assignee: '' | 'me' | 'unassigned';
    tenant_id: string;
    sla: '' | 'breached' | 'at_risk';
    q: string;
    cursor: string;
}

export const EMPTY_QUEUE_FILTERS: QueueFilters = {
    status: '', priority: '', category: '', assignee: '', tenant_id: '', sla: '', q: '', cursor: '',
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CURSOR = /^[A-Za-z0-9_-]{1,200}$/;
const pick = <T extends string>(value: string | null, allowed: readonly string[]): T | '' =>
    value !== null && allowed.includes(value) ? value as T : '';

export function parseQueueFilters(search: string | URLSearchParams): QueueFilters {
    const params = typeof search === 'string' ? new URLSearchParams(search) : search;
    const tenant = params.get('tenant_id') ?? '';
    const cursor = params.get('cursor') ?? '';
    return {
        status: pick(params.get('status'), SUPPORT_STATUSES),
        priority: pick(params.get('priority'), SUPPORT_PRIORITIES),
        category: pick(params.get('category'), SUPPORT_CATEGORY_IDS),
        assignee: pick<'me' | 'unassigned'>(params.get('assignee'), ['me', 'unassigned']),
        tenant_id: UUID.test(tenant) ? tenant : '',
        sla: pick<'breached' | 'at_risk'>(params.get('sla'), ['breached', 'at_risk']),
        q: (params.get('q') ?? '').trim().slice(0, 100),
        cursor: CURSOR.test(cursor) ? cursor : '',
    };
}

const ORDER: Array<keyof QueueFilters> = ['status', 'priority', 'category', 'assignee', 'tenant_id', 'sla', 'q', 'cursor'];

/** Stable, empty-free query string for the page URL (no leading "?"). */
export function serializeQueueFilters(filters: QueueFilters): string {
    const params = new URLSearchParams();
    for (const name of ORDER) if (filters[name]) params.set(name, filters[name]);
    return params.toString();
}

/** The same values as the /admin/support/tickets query string; empty = not sent. */
export function queueQueryString(filters: QueueFilters): string {
    return serializeQueueFilters(filters);
}

/** A filter change restarts paging: the cursor belongs to the previous query. */
export function withFilterChange(filters: QueueFilters, change: Partial<Omit<QueueFilters, 'cursor'>>): QueueFilters {
    return { ...filters, ...change, cursor: '' };
}

export function hasActiveFilter(filters: QueueFilters): boolean {
    return ORDER.some((name) => name !== 'cursor' && Boolean(filters[name]));
}
