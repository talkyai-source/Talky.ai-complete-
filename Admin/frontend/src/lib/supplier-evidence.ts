export interface SupplierStatement {
    id: string;
    request_id: string;
    account_id: string;
    reference: string;
    period_start: string;
    period_end: string;
    currency: 'USD';
    reported_charges: string | null;
    source_sha256: string;
    imported_by: string;
    imported_at: string;
    stage: 'imported' | 'reviewed' | 'reconciled';
    superseded_by: string | null;
    reviews: { id: string; request_id: string; stage: 'reviewed' | 'reconciled'; review_reference: string;
        notes: string; reviewer_id: string; recorded_at: string }[];
}

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const decimal = /^-?\d+(?:\.\d{1,8})?$/;
const day = /^\d{4}-\d{2}-\d{2}$/;
const object = (value: unknown): value is Record<string, unknown> =>
    typeof value === 'object' && value !== null && !Array.isArray(value);
const id = (value: unknown): value is string => typeof value === 'string' && uuid.test(value);

export function supplierStatement(value: unknown): SupplierStatement | null {
    if (!object(value) || value.supplier !== 'deepgram' || value.source_kind !== 'deepgram_billing_breakdown_v1'
        || !['id', 'request_id', 'account_id', 'imported_by'].every(key => id(value[key]))
        || typeof value.reference !== 'string' || !value.reference.trim()
        || typeof value.period_start !== 'string' || !day.test(value.period_start)
        || typeof value.period_end !== 'string' || !day.test(value.period_end) || value.period_start > value.period_end
        || value.currency !== 'USD' || !(value.reported_charges === null
            || (typeof value.reported_charges === 'string' && decimal.test(value.reported_charges)))
        || typeof value.source_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(value.source_sha256)
        || typeof value.imported_at !== 'string' || !Number.isFinite(Date.parse(value.imported_at))
        || !['imported', 'reviewed', 'reconciled'].includes(String(value.stage))
        || !(value.superseded_by === null || id(value.superseded_by)) || !Array.isArray(value.reviews)
        || !value.reviews.every(review => object(review) && id(review.id) && id(review.request_id) && id(review.reviewer_id)
            && ['reviewed', 'reconciled'].includes(String(review.stage))
            && typeof review.review_reference === 'string' && typeof review.notes === 'string'
            && typeof review.recorded_at === 'string' && Number.isFinite(Date.parse(review.recorded_at)))) return null;
    return value as unknown as SupplierStatement;
}

export function supplierPage(value: unknown): { items: SupplierStatement[]; hasMore: boolean } | null {
    if (!object(value) || !Array.isArray(value.items) || typeof value.has_more !== 'boolean'
        || value.net_spend !== null || value.coverage !== 'listed_account_periods_only') return null;
    const items = value.items.map(supplierStatement);
    return items.every((item): item is SupplierStatement => item !== null)
        ? { items, hasMore: value.has_more } : null;
}

export function supplierStage(row: SupplierStatement) {
    if (row.superseded_by) return 'Superseded; original retained';
    return row.stage === 'imported' ? 'Imported; source not reviewed'
        : row.stage === 'reviewed' ? 'Source review recorded'
            : 'Reconciliation recorded; charges remain unallocated';
}
