// Shape checks for the Support console, modelled on the real backend
// responses (projection.py, staff_service.py, article_admin.py, attachments.py).
import assert from 'node:assert/strict';
import test from 'node:test';

import {
    isVersionConflict, validArticle, validArticles, validMetrics, validQueueItem, validQueuePage,
    validStaffMessage, validTicketDetail, validUploadReceipt, SUPPORT_CATEGORIES, categoryLabel,
} from '../src/lib/support-contracts.ts';
import {
    EMPTY_QUEUE_FILTERS, hasActiveFilter, parseQueueFilters, queueQueryString, serializeQueueFilters, withFilterChange,
} from '../src/lib/support-queue-filters.ts';
import { deadlineState, nearestDeadline, sortPageByDeadline } from '../src/lib/support-deadlines.ts';
import { formatStamp, metricText } from '../src/lib/support-format.ts';
import { articleDraftProblems } from '../src/lib/support-article-draft.ts';

const stamp = '2026-10-30T10:00:00+00:00';
const sla = { first_response_due_at: '2026-10-31T10:00:00+00:00', resolution_due_at: '2026-11-02T10:00:00+00:00', breached: false };
const queueItem = (over = {}) => ({
    id: '6b1d0d8e-1111-4bbb-9ccc-000000000001', number: 'SUP-000123', subject: 'Campaign stuck', status: 'open', priority: 'high',
    category: 'calls_campaigns', updated_at: stamp, created_at: stamp, unread: true,
    tenant: { id: '6b1d0d8e-2222-4bbb-9ccc-000000000002', name: 'Acme' }, assignee: null, sla, ...over,
});
const message = (over = {}) => ({
    id: 'm1', author_type: 'staff', author_name: 'Sam', body: 'Checked the trunk.', attachments: [], created_at: stamp, is_internal: true, ...over,
});
const detail = (over = {}) => ({
    id: queueItem().id, number: 'SUP-000123', subject: 'Campaign stuck', category: 'calls_campaigns', priority: 'high', status: 'open',
    created_at: stamp, updated_at: stamp, version: 3,
    requester: { id: 'u1', name: 'Pat', email: 'pat@example.invalid' }, related: { campaign_id: 'c1' },
    messages: [message({ is_internal: false, author_type: 'customer' }), message()], csat: null, allowed_actions: [],
    tenant: { id: queueItem().tenant.id, name: 'Acme', plan: 'Growth' }, assignee: { id: 'admin-A', name: 'Admin A', email: 'a@example.invalid' },
    sla: { ...sla, first_responded_at: null },
    conversation: [{ id: 'b1', role: 'user', content: 'help', sources: [], created_at: stamp },
        { id: 'b2', role: 'assistant', content: 'Try this', sources: [{ slug: 'docs-x', title: 'X', url: '/docs/x#y' }], created_at: stamp }],
    diagnostics: { source: 'ticket_form', priority_reason: 'category' },
    events: [{ id: 'e1', type: 'created', from_value: null, to_value: 'high', actor_id: 'u1', actor_name: 'Pat', created_at: stamp }],
    ...over,
});
const article = (over = {}) => ({
    id: 'a1', slug: 'refunds', title: 'Refunds', section: '', body_md: '# Refunds', category: 'billing', source: 'staff', published: true,
    url: '/support/articles/refunds', updated_by: 'admin-A', created_at: stamp, updated_at: stamp, ...over,
});
const metrics = (over = {}) => ({
    days: 7, conversations: 12, bot_solved_pct: 41.7, tickets_created: 5, open_by_priority: { urgent: 1, high: 2, normal: 0, low: 0 },
    sla_breaches: 1, median_first_response_minutes: 38.5, avg_csat: 4.25, ...over,
});

test('queue page: the real list item (tenant, assignee, sla, created_at, no requester) is valid; broken items are not', () => {
    assert.equal(validQueuePage({ items: [queueItem(), queueItem({ assignee: { id: 'admin-A', name: null } })], next_cursor: null }), true);
    assert.equal(validQueuePage({ items: [queueItem()], next_cursor: 'MjAyNi0xMC0zMFQxMDowMDowMCswMDowMHw2YjFk' }), true);
    assert.equal(validQueuePage({ items: [queueItem()] }), false, 'next_cursor must be present');
    assert.equal(validQueueItem(queueItem({ status: 'pending' })), false, 'unknown status');
    assert.equal(validQueueItem(queueItem({ priority: 'p1' })), false, 'unknown priority');
    assert.equal(validQueueItem(queueItem({ tenant: null })), false, 'tenant is required');
    assert.equal(validQueueItem(queueItem({ sla: { ...sla, breached: 'no' } })), false, 'breached must be boolean');
    assert.equal(validQueueItem(queueItem({ unread: 1 })), false);
    assert.equal(validQueuePage({ items: 'none', next_cursor: null }), false);
    assert.equal(validQueuePage(null), false);
});

test('queue page: a full 50-item page validates', () => {
    const items = Array.from({ length: 50 }, (_, index) => queueItem({ id: `${index}`, number: `SUP-${String(index).padStart(6, '0')}` }));
    assert.equal(validQueuePage({ items, next_cursor: 'abc' }), true);
});

test('ticket detail: the real staff shape validates; a customer-only shape or stale version does not', () => {
    assert.equal(validTicketDetail(detail()), true);
    assert.equal(validTicketDetail(detail({ assignee: null, tenant: null, csat: { score: 4, comment: null } })), true);
    assert.equal(validTicketDetail(detail({ version: undefined })), false, 'version is required');
    assert.equal(validTicketDetail(detail({ version: 0 })), false, 'version starts at 1');
    assert.equal(validTicketDetail(detail({ messages: [message({ is_internal: undefined })] })), false, 'is_internal on every message');
    assert.equal(validTicketDetail(detail({ sla })), false, 'first_responded_at is part of the staff sla');
    assert.equal(validTicketDetail(detail({ events: [{ id: 'e', type: 'x' }] })), false);
    assert.equal(validTicketDetail(detail({ diagnostics: [] })), false, 'diagnostics is an object');
    assert.equal(validTicketDetail(detail({ conversation: [{ id: 'b', role: 'user' }] })), false);
    assert.equal(validTicketDetail({ ...detail(), requester: { id: 'u1', name: 'Pat' } }), false, 'requester.email is present (may be null)');
});

test('staff message: is_internal and attachments are checked', () => {
    assert.equal(validStaffMessage(message({ attachments: [{ id: 'f', filename: 'log.txt', size_bytes: 12, content_type: 'text/plain' }] })), true);
    assert.equal(validStaffMessage(message({ attachments: [{ id: 'f', filename: 'log.txt' }] })), false);
    assert.equal(validStaffMessage(message({ is_internal: 'yes' })), false);
    assert.equal(validStaffMessage(message({ body: null })), false);
});

test('articles: the row shape validates; docs rows too; missing fields fail', () => {
    assert.equal(validArticle(article()), true);
    assert.equal(validArticle(article({ source: 'docs', updated_by: null, section: 'Getting Help' })), true);
    assert.equal(validArticle(article({ published: 'true' })), false);
    assert.equal(validArticle({ ...article(), body_md: undefined }), false);
    assert.equal(validArticles({ items: [article(), article({ id: 'a2' })] }), true);
    assert.equal(validArticles({ items: [article(), { id: 'a3' }] }), false);
    assert.equal(validArticles([]), false);
});

test('metrics: nulls for the three ratio figures are valid; a missing priority bucket or days is not', () => {
    assert.equal(validMetrics(metrics()), true);
    assert.equal(validMetrics(metrics({ bot_solved_pct: null, median_first_response_minutes: null, avg_csat: null })), true);
    assert.equal(validMetrics(metrics({ days: undefined })), false);
    assert.equal(validMetrics(metrics({ open_by_priority: { urgent: 1, high: 2, normal: 0 } })), false);
    assert.equal(validMetrics(metrics({ sla_breaches: -1 })), false);
    assert.equal(validMetrics(metrics({ avg_csat: 'n/a' })), false);
    assert.equal(metricText(null, '%'), '—');
    assert.equal(metricText(41.7, '%'), '41.7%');
    assert.equal(metricText(5), '5');
});

test('upload receipt: attachment_id, filename, content_type, size_bytes', () => {
    assert.equal(validUploadReceipt({ attachment_id: 'f1', filename: 'log.txt', content_type: 'text/plain', size_bytes: 10 }), true);
    assert.equal(validUploadReceipt({ id: 'f1', filename: 'log.txt', content_type: 'text/plain', size_bytes: 10 }), false);
    assert.equal(validUploadReceipt({ attachment_id: 'f1', filename: 'log.txt', content_type: 'text/plain', size_bytes: -1 }), false);
});

test('a 409 is a version conflict only with the backend code version_conflict (upper-cased by api.ts)', () => {
    assert.equal(isVersionConflict({ status: 409, code: 'VERSION_CONFLICT' }), true);
    assert.equal(isVersionConflict({ status: 409, code: 'INVALID_STATUS_TRANSITION' }), false);
    assert.equal(isVersionConflict({ status: 200, code: 'VERSION_CONFLICT' }), false);
    assert.equal(isVersionConflict(undefined), false);
});

test('queue filters: parse strictly, serialise stably, and a filter change drops the cursor', () => {
    const parsed = parseQueueFilters('status=open&priority=high&category=billing&assignee=me&tenant_id=6b1d0d8e-2222-4bbb-9ccc-000000000002&sla=at_risk&q=SUP-000123&cursor=abc_DEF-123');
    assert.deepEqual(parsed, { status: 'open', priority: 'high', category: 'billing', assignee: 'me',
        tenant_id: '6b1d0d8e-2222-4bbb-9ccc-000000000002', sla: 'at_risk', q: 'SUP-000123', cursor: 'abc_DEF-123' });
    assert.equal(serializeQueueFilters(parsed), 'status=open&priority=high&category=billing&assignee=me&tenant_id=6b1d0d8e-2222-4bbb-9ccc-000000000002&sla=at_risk&q=SUP-000123&cursor=abc_DEF-123');
    assert.equal(queueQueryString(parsed), serializeQueueFilters(parsed));
    assert.deepEqual(parseQueueFilters('status=bogus&priority=p0&category=nope&assignee=someone&tenant_id=not-a-uuid&sla=late&cursor=a%20b'), EMPTY_QUEUE_FILTERS);
    assert.equal(serializeQueueFilters(EMPTY_QUEUE_FILTERS), '');
    assert.equal(hasActiveFilter(EMPTY_QUEUE_FILTERS), false);
    assert.equal(hasActiveFilter({ ...EMPTY_QUEUE_FILTERS, cursor: 'x' }), false, 'a cursor alone is not a filter');
    assert.deepEqual(withFilterChange(parsed, { status: 'closed' }), { ...parsed, status: 'closed', cursor: '' });
    assert.equal(parseQueueFilters(new URLSearchParams('q=' + 'x'.repeat(200))).q.length, 100);
});

test('deadlines: nearest deadline, overdue, due within the hour, finished tickets have none; page sort puts nearest first', () => {
    const now = Date.parse('2026-10-31T09:30:00+00:00');
    assert.equal(nearestDeadline(sla), sla.first_response_due_at);
    assert.equal(nearestDeadline({ ...sla, first_responded_at: stamp }), sla.resolution_due_at);
    assert.equal(deadlineState(sla, 'open', now), 'at_risk');
    assert.equal(deadlineState(sla, 'open', now - 2 * 60 * 60 * 1000), 'ok');
    assert.equal(deadlineState(sla, 'open', now + 60 * 60 * 1000), 'overdue');
    assert.equal(deadlineState({ ...sla, breached: true }, 'in_progress', now - 86400000), 'overdue');
    assert.equal(deadlineState(sla, 'resolved', now), 'none');
    assert.equal(deadlineState({ first_response_due_at: null, resolution_due_at: null, breached: false }, 'open', now), 'none');
    const later = queueItem({ id: 'later', sla: { ...sla, first_response_due_at: '2026-11-05T10:00:00+00:00', resolution_due_at: '2026-11-06T10:00:00+00:00' } });
    const done = queueItem({ id: 'done', status: 'resolved' });
    assert.deepEqual(sortPageByDeadline([later, done, queueItem()]).map((item) => item.id), [queueItem().id, 'later', 'done']);
});

test('formatting and article draft checks', () => {
    assert.equal(formatStamp(null), '—');
    assert.equal(formatStamp('not a date'), 'not a date');
    assert.equal(categoryLabel('billing'), 'Billing and payments');
    assert.equal(SUPPORT_CATEGORIES.length, 10);
    assert.deepEqual(articleDraftProblems({ slug: 'refunds', title: 'Refunds', body_md: 'x', category: 'billing', published: true }, true), []);
    assert.equal(articleDraftProblems({ slug: 'Bad Slug', title: '', body_md: '', category: 'billing', published: true }, true).length, 3);
    assert.equal(articleDraftProblems({ slug: '', title: 'ok', body_md: 'ok', category: 'billing', published: true }, false).length, 0, 'slug is fixed when editing');
});
