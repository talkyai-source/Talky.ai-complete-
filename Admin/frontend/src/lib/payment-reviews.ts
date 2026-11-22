import { formatMinorAmount } from './money-format';

export interface PaymentReview {
    provider: 'stripe' | 'paypal';
    event_id: string;
    event_type: string | null;
    state: 'needs_review' | 'stuck';
    received_at: string;
    waiting_hours: number;
    kind: 'minutes' | 'plan' | 'unknown';
    tenant_id: string | null;
    business_name: string | null;
    amount_minor: number | null;
    currency: string | null;
    currency_exponent: number | null;
    reason_code: string | null;
    reason_text: string;
    provider_reference: string | null;
    attempt_count: number;
    can_close: boolean;
    close_blocked_reason: string | null;
}

export interface PaymentReviewPage { items: PaymentReview[]; truncated: boolean; limit: number }

const object = (value: unknown): value is Record<string, unknown> =>
    typeof value === 'object' && value !== null && !Array.isArray(value);
const whole = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value);
const orNull = (value: unknown, check: (v: unknown) => boolean) => value === null || check(value);
const text = (value: unknown): value is string => typeof value === 'string';

function paymentReview(value: unknown): PaymentReview | null {
    if (!object(value)) return null;
    const ok = ['stripe', 'paypal'].includes(String(value.provider))
        && typeof value.event_id === 'string' && value.event_id !== ''
        && orNull(value.event_type, text)
        && ['needs_review', 'stuck'].includes(String(value.state))
        && text(value.received_at) && Number.isFinite(Date.parse(value.received_at))
        && whole(value.waiting_hours) && value.waiting_hours >= 0
        && ['minutes', 'plan', 'unknown'].includes(String(value.kind))
        && orNull(value.tenant_id, text) && orNull(value.business_name, text)
        && orNull(value.amount_minor, whole) && orNull(value.currency, text) && orNull(value.currency_exponent, whole)
        && orNull(value.reason_code, text)
        && text(value.reason_text) && value.reason_text !== ''
        && orNull(value.provider_reference, text)
        && whole(value.attempt_count) && value.attempt_count >= 0
        && typeof value.can_close === 'boolean'
        && orNull(value.close_blocked_reason, text);
    return ok ? value as unknown as PaymentReview : null;
}

/** Strict: null on any wrong or missing field, so a bad answer can never read as "nothing to review". */
export function paymentReviewPage(value: unknown): PaymentReviewPage | null {
    if (!object(value) || !Array.isArray(value.items) || typeof value.truncated !== 'boolean'
        || !whole(value.limit)) return null;
    const items = value.items.map(paymentReview);
    return items.every((item): item is PaymentReview => item !== null)
        ? { items, truncated: value.truncated, limit: value.limit } : null;
}

export function waitingText(hours: number): string {
    if (hours < 1) return 'less than an hour';
    if (hours === 1) return '1 hour';
    if (hours < 48) return `${hours} hours`;
    return `${Math.floor(hours / 24)} days`;
}

export const accountText = (row: Pick<PaymentReview, 'business_name' | 'tenant_id'>): string =>
    row.business_name || (row.tenant_id ? row.tenant_id.slice(0, 8) : 'Not known');

export const amountText = (row: Pick<PaymentReview, 'amount_minor' | 'currency' | 'currency_exponent'>): string =>
    row.amount_minor === null || !row.currency || row.currency_exponent === null
        ? 'Not known' : formatMinorAmount(row.amount_minor, row.currency.toUpperCase(), row.currency_exponent);

export const kindText = (kind: PaymentReview['kind']) =>
    kind === 'minutes' ? 'Minutes' : kind === 'plan' ? 'Plan' : 'Not known';
