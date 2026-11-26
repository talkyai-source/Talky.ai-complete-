// The list's status and position live in the page URL so a view survives a
// reload. Parsing is strict: an unknown value becomes the default, never a
// request the backend would reject.
import { NUMBER_REQUEST_STATUSES, type NumberRequestStatus } from './number-request-contracts.ts';

export interface NumberRequestFilters { status: NumberRequestStatus; offset: number }

export const NUMBER_REQUEST_LIMIT = 20;
/** The backend refuses an offset above this. */
export const NUMBER_REQUEST_MAX_OFFSET = 1000;
export const DEFAULT_NUMBER_REQUEST_FILTERS: NumberRequestFilters = { status: 'submitted', offset: 0 };

export function parseNumberRequestFilters(search: string | URLSearchParams): NumberRequestFilters {
    const params = typeof search === 'string' ? new URLSearchParams(search) : search;
    const status = params.get('status');
    const offset = params.get('offset') ?? '';
    const position = /^\d{1,4}$/.test(offset) ? Number(offset) : 0;
    return {
        status: NUMBER_REQUEST_STATUSES.includes(status as NumberRequestStatus) ? status as NumberRequestStatus : 'submitted',
        offset: position <= NUMBER_REQUEST_MAX_OFFSET ? position : 0,
    };
}

/** Query string for the page URL (no leading "?"); defaults are left out. */
export function serializeNumberRequestFilters(filters: NumberRequestFilters): string {
    const params = new URLSearchParams();
    if (filters.status !== 'submitted') params.set('status', filters.status);
    if (filters.offset > 0) params.set('offset', String(filters.offset));
    return params.toString();
}

/** Query string for GET /admin/telephony/number-requests: always all three values. */
export function numberRequestQuery(filters: NumberRequestFilters): string {
    return `status=${filters.status}&limit=${NUMBER_REQUEST_LIMIT}&offset=${filters.offset}`;
}

/** A status change restarts paging: the offset belongs to the previous list. */
export function withStatus(filters: NumberRequestFilters, status: NumberRequestStatus): NumberRequestFilters {
    return { ...filters, status, offset: 0 };
}

export const nextOffset = (filters: NumberRequestFilters): number => filters.offset + NUMBER_REQUEST_LIMIT;
export const previousOffset = (filters: NumberRequestFilters): number => Math.max(0, filters.offset - NUMBER_REQUEST_LIMIT);
/** "Next page" is off on the last page and when the next offset would pass the backend's limit. */
export function canGoNext(filters: NumberRequestFilters, hasMore: boolean): boolean {
    return hasMore && nextOffset(filters) <= NUMBER_REQUEST_MAX_OFFSET;
}
export const pageNumber = (filters: NumberRequestFilters): number => Math.floor(filters.offset / NUMBER_REQUEST_LIMIT) + 1;
