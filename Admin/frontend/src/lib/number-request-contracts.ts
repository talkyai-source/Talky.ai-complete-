// Shapes the Phone Number Requests screen reads from /admin/telephony/number-requests,
// checked field by field against what the backend returns
// (backend/app/api/v1/endpoints/admin/telephony_number_requests.py). A wrong or
// incomplete answer is an error, never an empty list.

export const NUMBER_REQUEST_STATUSES = ['submitted', 'approved', 'rejected'] as const;
export type NumberRequestStatus = typeof NUMBER_REQUEST_STATUSES[number];
export const NUMBER_REQUEST_USES = ['business', 'personal'] as const;
export type NumberRequestUse = typeof NUMBER_REQUEST_USES[number];
export const NUMBER_REQUEST_KINDS = ['business_registration', 'business_photo_id', 'utility_bill', 'personal_photo_id'] as const;
export type NumberRequestKind = typeof NUMBER_REQUEST_KINDS[number];
export const NUMBER_REQUEST_SCANS = ['not_scanned', 'clean', 'infected'] as const;
export type NumberRequestScan = typeof NUMBER_REQUEST_SCANS[number];
export const NUMBER_REQUEST_EMAIL_OUTCOMES = ['sent', 'failed', 'skipped', 'off'] as const;
export type NumberRequestEmailOutcome = typeof NUMBER_REQUEST_EMAIL_OUTCOMES[number];
export type NumberRequestDecision = 'approved' | 'rejected';

export const NUMBER_REQUEST_STATUS_LABELS: Record<NumberRequestStatus, string> = {
    submitted: 'Submitted', approved: 'Approved', rejected: 'Rejected',
};
export const NUMBER_REQUEST_USE_LABELS: Record<NumberRequestUse, string> = { business: 'Business Use', personal: 'Personal Use' };
/** Copied from the backend's KIND_LABELS (number_requests/config.py); a unit test compares the two. */
export const NUMBER_REQUEST_KIND_LABELS: Record<NumberRequestKind, string> = {
    business_registration: 'Business registration',
    business_photo_id: 'Photo ID matching the business registration',
    utility_bill: 'Utility bill (proof of address)',
    personal_photo_id: 'Photo ID matching the utility bill',
};
export const NUMBER_REQUEST_SCAN_LABELS: Record<NumberRequestScan, string> = {
    not_scanned: 'Not scanned', clean: 'Clean', infected: 'Infected',
};
export const NUMBER_REQUEST_NOTE_MAX = 1000;
export const INCOMPLETE_ANSWER = 'The server returned an incomplete or unexpected response.';
export const DOCUMENT_DELETED_TEXT = 'That document has been deleted.';
export const DOCUMENT_NOT_CLEARED_TEXT = 'That document has not been cleared by a virus scan.';

export interface NumberRequestListItem {
    id: string; tenant_id: string; business_name: string | null; requested_by_user_id: string;
    use_type: NumberRequestUse; status: NumberRequestStatus; submitted_at: string; reviewed_at: string | null;
    files_deleted: boolean;
}
export interface NumberRequestPage { items: NumberRequestListItem[]; limit: number; offset: number; has_more: boolean }
export interface NumberRequestDocument {
    id: string; kind: NumberRequestKind; file_name: string | null; content_type: string; size_bytes: number;
    sha256: string; scan_status: NumberRequestScan; created_at: string; available: boolean;
}
export interface NumberRequestDetail {
    id: string; tenant_id: string; business_name: string | null; requested_by_user_id: string;
    requester_email: string | null; use_type: NumberRequestUse; status: NumberRequestStatus;
    review_note: string | null; reviewed_by_user_id: string | null; reviewed_at: string | null;
    submitted_at: string; files_deleted: boolean; documents: NumberRequestDocument[];
}
export interface NumberRequestDecisionAnswer {
    id: string; status: NumberRequestStatus; reviewed_at: string; email: NumberRequestEmailOutcome;
}
export type NumberRequestDecisionInput = { decision: 'approved' } | { decision: 'rejected'; note: string };

const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown) => typeof value === 'string' && value !== '';
const nullableText = (value: unknown) => value === null || typeof value === 'string';
const nullableStamp = (value: unknown) => value === null || text(value);
const flag = (value: unknown) => typeof value === 'boolean';
const count = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const oneOf = (values: readonly string[]) => (value: unknown) => typeof value === 'string' && values.includes(value);
const fields = (value: Record<string, unknown>, names: string[], check: (item: unknown) => boolean) => names.every((name) => check(value[name]));

export function validNumberRequestItem(value: unknown): value is NumberRequestListItem {
    return record(value) && fields(value, ['id', 'tenant_id', 'requested_by_user_id', 'submitted_at'], text)
        && nullableText(value.business_name) && nullableStamp(value.reviewed_at)
        && oneOf(NUMBER_REQUEST_USES)(value.use_type) && oneOf(NUMBER_REQUEST_STATUSES)(value.status)
        && flag(value.files_deleted);
}
export function validNumberRequestPage(value: unknown): value is NumberRequestPage {
    return record(value) && Array.isArray(value.items) && value.items.every(validNumberRequestItem)
        && count(value.limit) && count(value.offset) && flag(value.has_more);
}
export function validNumberRequestDocument(value: unknown): value is NumberRequestDocument {
    return record(value) && fields(value, ['id', 'content_type', 'sha256', 'created_at'], text)
        && oneOf(NUMBER_REQUEST_KINDS)(value.kind) && nullableText(value.file_name)
        && count(value.size_bytes) && (value.size_bytes as number) > 0
        && oneOf(NUMBER_REQUEST_SCANS)(value.scan_status) && flag(value.available);
}
export function validNumberRequestDetail(value: unknown): value is NumberRequestDetail {
    return record(value) && fields(value, ['id', 'tenant_id', 'requested_by_user_id', 'submitted_at'], text)
        && fields(value, ['business_name', 'requester_email', 'review_note', 'reviewed_by_user_id'], nullableText)
        && nullableStamp(value.reviewed_at)
        && oneOf(NUMBER_REQUEST_USES)(value.use_type) && oneOf(NUMBER_REQUEST_STATUSES)(value.status)
        && flag(value.files_deleted) && Array.isArray(value.documents) && value.documents.every(validNumberRequestDocument);
}
export function validNumberRequestDecision(value: unknown): value is NumberRequestDecisionAnswer {
    return record(value) && fields(value, ['id', 'reviewed_at'], text)
        && oneOf(NUMBER_REQUEST_STATUSES)(value.status) && oneOf(NUMBER_REQUEST_EMAIL_OUTCOMES)(value.email);
}

type ErrorLike = { code?: string; status?: number } | undefined;
const coded = (status: number, code: string) => (error: ErrorLike): boolean =>
    Boolean(error && error.status === status && error.code === code);
// api.ts upper-cases the server's envelope codes.
/** 409: the request is no longer "submitted"; a decision is never sent again. */
export const isAlreadyDecided = coded(409, 'ALREADY_DECIDED');
/** 422: a rejection was sent without a reason. */
export const isNoteRequired = coded(422, 'NOTE_REQUIRED');
/** 410: the document's file has been deleted. */
export const isDocumentDeleted = coded(410, 'DOCUMENT_DELETED');
/** 409: the document is infected, or a clean scan is required and it has none. */
export const isDocumentNotCleared = coded(409, 'DOCUMENT_NOT_CLEARED');
/** 503: the server could not confirm the decision; the request is read again before another try. */
export const isDecisionUnconfirmed = coded(503, 'DECISION_UNCONFIRMED');

const TYPES: Record<string, { label: string; extension: string }> = {
    'application/pdf': { label: 'PDF', extension: '.pdf' },
    'image/jpeg': { label: 'JPG', extension: '.jpg' },
    'image/png': { label: 'PNG', extension: '.png' },
};
export const documentTypeLabel = (contentType: string): string => TYPES[contentType]?.label ?? '—';
/** The same name the server puts in Content-Disposition, which a cross-origin page cannot read. */
export function documentDownloadName(document: Pick<NumberRequestDocument, 'kind' | 'id' | 'content_type'>): string {
    return `${document.kind}-${document.id.slice(0, 8)}${TYPES[document.content_type]?.extension ?? ''}`;
}
/** KB below 1 MB, else MB with one decimal. */
export function documentSizeText(bytes: number): string {
    return bytes < 1048576 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
}
export const companyText = (row: { business_name: string | null; tenant_id: string }): string => row.business_name || row.tenant_id;

const EMAIL_RESULT: Record<NumberRequestEmailOutcome, string> = {
    sent: 'The customer was emailed.',
    failed: 'The email to the customer failed.',
    skipped: 'No email address is on file, so no email was sent.',
    off: 'Decision emails are turned off.',
};
export function decisionResultText(decision: NumberRequestDecision, email: NumberRequestEmailOutcome): string {
    return `${decision === 'approved' ? 'Approved.' : 'Rejected.'} ${EMAIL_RESULT[email]}`;
}
