import { Building2 } from 'lucide-react';
import { formatMinorAmount } from '../lib/money-format';
import type { BillingReconciliationEntry } from '../lib/api';

export const NOT_AVAILABLE = 'Not available';

/** The ledger kinds the server sends, in plain words. */
export const PAYMENT_KINDS = [
    ['topup', 'Top-up'],
    ['refund', 'Refund'],
    ['dispute', 'Dispute'],
    ['adjustment', 'Adjustment'],
] as const;

const kindLabel = (kind: string) => PAYMENT_KINDS.find(([value]) => value === kind)?.[1] ?? (kind || NOT_AVAILABLE);

// Same status styling the Calls table uses: green for money in, red for money taken back, grey for usage.
const kindClass = (kind: string) => (kind === 'topup' ? 'status-completed' : kind === 'refund' || kind === 'dispute' ? 'status-failed' : 'status-ended');

/** An amount in its own currency using the exponent the server sent; no exponent means no guess. */
function formatPaymentAmount(minor: number, currency: string | null, exponent: number | null | undefined): string {
    if (!currency || exponent === null || exponent === undefined) return NOT_AVAILABLE;
    return formatMinorAmount(minor, currency.toUpperCase(), exponent);
}

// Same date format as the Campaigns table.
function formatDate(dateStr: string | null): string {
    if (!dateStr) return NOT_AVAILABLE;
    try {
        const date = new Date(dateStr);
        return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
        return dateStr;
    }
}

/** The payment list. Text from customers (names, notes) is only ever rendered as text. */
export function PaymentsTable({ entries, exponents, filtered }: {
    entries: BillingReconciliationEntry[];
    /** Exponent per lower-case currency code, from the server's totals. */
    exponents: Record<string, number | null>;
    /** True when the browser filters hid some rows. */
    filtered: boolean;
}) {
    if (entries.length === 0) {
        return (
            <div className="empty-state">
                <p>{filtered ? 'No payments match these filters.' : 'No payments in this period.'}</p>
            </div>
        );
    }
    return (
        <table className="data-table campaigns-table" aria-label="Payments">
            <thead>
                <tr>
                    <th>Date</th>
                    <th>Account</th>
                    <th>Kind</th>
                    <th>Minutes</th>
                    <th>Amount</th>
                    <th>Package</th>
                    <th>Order status</th>
                    <th>Payment reference</th>
                    <th>Event reference</th>
                    <th>Note</th>
                </tr>
            </thead>
            <tbody>
                {entries.map((entry, index) => (
                    <tr key={`${entry.provider_event_id}-${entry.created_at}-${index}`}>
                        <td>{formatDate(entry.created_at)}</td>
                        <td className="campaigns-table-wrap">
                            <div className="tenant-name-cell">
                                <Building2 size={14} />
                                <span>{entry.business_name || NOT_AVAILABLE}</span>
                            </div>
                        </td>
                        <td><span className={`call-status-badge ${kindClass(entry.kind)}`}>{kindLabel(entry.kind)}</span></td>
                        <td>{entry.minutes_delta}</td>
                        <td>{entry.amount_cents === 0 && !entry.currency ? 'No money moved' : formatPaymentAmount(entry.amount_cents, entry.currency, exponents[(entry.currency || '').toLowerCase()])}</td>
                        <td>{entry.package_code || NOT_AVAILABLE}</td>
                        <td>{entry.order_status || NOT_AVAILABLE}</td>
                        <td className="campaigns-table-wrap">{entry.provider_payment_id || NOT_AVAILABLE}</td>
                        <td className="campaigns-table-wrap">{entry.provider_event_id || NOT_AVAILABLE}</td>
                        <td className="campaigns-table-wrap">{entry.note || 'No note'}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}
