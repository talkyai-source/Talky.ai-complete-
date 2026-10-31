# Support system: what the frontend needs from the backend

Written for the backend developer, from the Support frontend build on branch
`feat/support-frontend` (30 October 2026). Each item says what the frontend
needs, what the backend does now and where, what the user sees as a result,
and the change that would resolve it. Nothing here is ordered by priority.

The frontend was built against what the backend returns today, not against
the brief's contract. Where the two differ the frontend encodes the real
shape; those shapes are listed in section 2 so nothing silently drifts.

## 1. Gaps that only the backend can close

### 1.1 The staff queue has no requester

- **Needed:** the staff queue (Admin, `/support`) has a "Requester" column.
- **Now:** `GET /admin/support/tickets` items are the customer list item plus
  `tenant`, `assignee`, `sla` and `created_at`. There is no requester
  ([staff_service.py:96-115](../../backend/app/domain/services/support/staff_service.py#L96-L115)).
  The requester is only in the detail
  ([projection.py:99-100](../../backend/app/domain/services/support/projection.py#L99-L100)).
- **User sees:** an empty column with the note "not provided by the server";
  staff must open each ticket to see who raised it.
- **Change:** add `requester: {id, name, email}` to each queue item; the list
  query already joins `tenants`, so a join on `user_profiles` by
  `requester_user_id` is the same shape as the assignee join.

### 1.2 The queue cannot sort by deadline

- **Needed:** "Default sort: nearest deadline first" for the whole queue.
- **Now:** the list is ordered `updated_at DESC, id DESC` with a keyset cursor
  on those two columns
  ([staff_service.py:89-103](../../backend/app/domain/services/support/staff_service.py#L89-L103),
  [pagination.py](../../backend/app/domain/services/support/pagination.py)).
- **User sees:** a "Nearest deadline first (this page only)" control that sorts
  the fetched page; the page says so. The first page may not hold the most
  urgent ticket.
- **Change:** a `sort=deadline` query parameter ordering by
  `LEAST(first_response_due_at, resolution_due_at) ASC, id ASC` for open
  statuses, with a cursor encoding that pair.

### 1.3 The customer "Open" tab needs two requests

- **Needed:** an "Open" tab covering tickets the customer thinks of as open.
- **Now:** `GET /support/tickets?status=` filters exactly one status
  ([tickets.py:57-61](../../backend/app/api/v1/endpoints/support/tickets.py#L57-L61)),
  and "open" spans `open` and `in_progress` (`waiting_on_customer` has its
  own tab).
- **How the frontend handled it:** the Open tab issues one request per
  status, merges the pages newest first and keeps one cursor per status
  (`Talk-Leee/src/lib/support-ticket-queries.ts`). "Load more" advances every
  status that still has a cursor. Ordering across the two lists is therefore
  only as good as the pages fetched so far.
- **Change:** accept a repeated or comma-separated `status` (for example
  `status=open,in_progress`), or a `group=open` alias, with one cursor.

### 1.4 Staff article links point nowhere

- **Needed:** source chips under an assistant reply, and search results on
  `/support`, link to the article.
- **Now:** a staff-written article's `url` is `/support/articles/<slug>`
  ([article_admin.py:30-31](../../backend/app/domain/services/support/article_admin.py#L30-L31)).
  No customer route exists there, and no endpoint returns one article's body
  (the backend record, section 24, says the same).
- **User sees:** the chip or result as plain text with the title "This article
  has no page yet". Only docs-sourced articles (`/docs/<page>#<heading>`)
  are links.
- **Change:** `GET /support/articles/{slug}` returning
  `{slug, title, body_md, category, updated_at}` for a published article,
  support:report required, so the customer app can add the page.

### 1.5 "Talk-Lee Support" is hard-coded on the server

- **Needed:** no hard-coded product name in anything a customer sees; the
  customer app is white-labelled per partner.
- **Now:** staff messages are shown to customers as `SUPPORT_TEAM_NAME =
  "Talk-Lee Support"` and system messages as `SYSTEM_NAME = "Talk-Lee"`
  ([projection.py:16-17](../../backend/app/domain/services/support/projection.py#L16-L17)
  and [:31-36](../../backend/app/domain/services/support/projection.py#L31-L36)).
  The greeting ([conversation_service.py:38](../../backend/app/domain/services/support/conversation_service.py#L38))
  also names the product.
- **User sees:** the product name inside a white-labelled app. The frontend
  cannot rewrite server text safely.
- **Change:** return `author_type` and a neutral `author_name` (for example
  `"Support"` / `null` for system), or resolve the display name from the
  tenant's partner branding on the server. The greeting text could come from
  the same place.

### 1.6 The unread route returns ids, not numbers

- **Needed:** the in-app notification says "New reply on SUP-000123".
- **Now:** `GET /support/unread` returns `{count, ticket_ids}`
  ([ticket_service.py:251-255](../../backend/app/domain/services/support/ticket_service.py#L251-L255)).
- **How the frontend handled it:** when the count rises it fetches the first
  page of the customer's tickets to map ids to numbers; a ticket not on that
  page is announced as "New reply on a support ticket". Reading the ticket
  itself would mark it read ([ticket_service.py:146](../../backend/app/domain/services/support/ticket_service.py#L146)).
- **Change:** return `items: [{id, number, subject}]` alongside `count`.

### 1.7 Queue items lack `first_responded_at`

- **Needed:** the deadline cell colours the first-response deadline only
  while it is still unanswered.
- **Now:** queue items carry `sla.first_response_due_at`,
  `resolution_due_at` and `breached` but not `first_responded_at`; the detail
  has it ([projection.py:108-113](../../backend/app/domain/services/support/projection.py#L108-L113)).
- **User sees:** on the queue, an answered first-response deadline in the
  past still shows red until the ticket is resolved; the detail view is right.
- **Change:** add `first_responded_at` to the queue item's `sla`.

### 1.8 Staff detail returns `allowed_actions: []`

- **Needed:** the staff console gates its controls on what the server will
  accept.
- **Now:** `allowed_actions` is always empty in the staff projection
  ([projection.py:104](../../backend/app/domain/services/support/projection.py#L104));
  the transition table lives only in
  [lifecycle.py:18-24](../../backend/app/domain/services/support/lifecycle.py#L18-L24).
- **How the frontend handled it:** the console derives what is possible from
  the status (closed is read-only) and lets the server refuse the rest with
  409 `invalid_status_transition`, which it shows.
- **Change:** fill `allowed_actions` for staff with the statuses reachable
  from the current one, or a `transitions: [status]` field.

### 1.9 No way to list assignable staff

- **Needed:** an assignee picker in the ticket controls.
- **Now:** `PATCH /admin/support/tickets/{id}` accepts any
  `assignee_user_id` that is an active, verified platform admin
  ([staff_service.py:169-172](../../backend/app/domain/services/support/staff_service.py#L169-L172)),
  but the contract has no endpoint listing those users.
- **User sees:** only "Assign to me" and "Unassign".
- **Change:** `GET /admin/support/assignees` → `{items: [{id, name}]}`.

### 1.10 Upload progress

- **Needed:** per-file progress while uploading.
- **Now:** `POST /support/attachments` is one multipart request with no
  progress channel; the frontend's shared HTTP client is fetch-based, and the
  structural rule forbids a second client.
- **User sees:** per-file state only: uploading, done, failed.
- **Change:** none required server-side; noted so nobody expects a percentage.

## 2. Shapes the frontend encodes as the backend returns them

These differ from the brief's contract. If any of them change, the frontend
schemas in `Talk-Leee/src/lib/support-api.ts` and
`Admin/frontend/src/lib/support-contracts.ts` must change with them.

| Route | What the frontend models |
|---|---|
| `GET /support/config` while off | 200, `enabled: false`, zeroed lists and limits; every other support route 404 `not_found` |
| `POST /support/conversations`, `POST /support/attachments`, `POST /support/tickets`, `POST …/tickets/{id}/messages`, staff messages, article create | 201 on a first write |
| Any replayed write | 200 with header `Idempotent-Replay: true`; the frontend reads the header |
| `POST …/conversations/{id}/messages` | 429 `rate_limited` past 10 per minute per user; 409 `request_id_conflict` for a reused id with different content; 409 `request_in_progress` for a retry inside 60 s; 409 `conversation_closed` |
| `GET /admin/support/tickets` items | list item + `tenant {id,name}`, `assignee {id,name}|null`, `sla {first_response_due_at, resolution_due_at, breached}`, `created_at`; no requester |
| `GET /admin/support/tickets/{id}` | adds `version`, `tenant {id,name,plan}`, `assignee {id,name,email}|null`, `sla` with `first_responded_at`, `conversation: [BotMessage]`, free-form `diagnostics`, `events: [{id, type, from_value, to_value, actor_id, actor_name, created_at}]`, `requester.email`, `is_internal` on every message, `allowed_actions: []` |
| `PATCH /admin/support/tickets/{id}` | returns the full staff detail including the new `version`; 409 `version_conflict` on a stale version |
| `GET /admin/support/metrics` | adds `days`; `bot_solved_pct`, `median_first_response_minutes`, `avg_csat` are null with no data |
| Article | `{id, slug, title, section, body_md, category, source, published, url, updated_by, created_at, updated_at}`; 409 `article_read_only` for `source=docs` |
| Vocabulary | statuses `open, in_progress, waiting_on_customer, resolved, closed`; priorities `urgent, high, normal, low`; the ten category ids in `vocabulary.py`; attachment types `png jpg webp pdf txt log csv`; at most 5 attachments per message; search returns nothing under 2 characters |

## 3. Every place the frontend works around the backend

Customer app (Talk-Leee):

1. The Open tab merges `status=open` and `status=in_progress` client-side with one cursor per status (1.3).
2. Staff-article sources and search results are shown without a link (1.4).
3. The unread notifier fetches the first ticket page to turn ids into numbers (1.6).
4. Writes go through the shared client's raw request so the `Idempotent-Replay` header is visible; a replayed answer is shown as the saved result.
5. A lost conversation write (network, timeout, 5xx) first re-reads `GET /support/conversations/{id}`; if the server already holds the reply it is shown, otherwise Retry resends the same request_id. A lost ticket write offers only Retry with the same id, because no read can find a ticket by request_id.
6. A failed ticket-page write re-reads `GET /support/tickets/{id}` before offering Retry with the same id.
7. Attachment downloads are plain links to `GET /support/attachments/{id}`, relying on the session cookie for the 302.
8. The server's staff display name "Talk-Lee Support" is shown as received (1.5).
9. Assistant text is passed through a link policy (external links to plain text, images to alt text) before rendering, because the markdown renderer has no link allow-list.

Staff console (Admin):

10. Requester column empty with a note (1.1).
11. Deadline sort is per page and says so (1.2).
12. Queue deadline colouring cannot see `first_responded_at` (1.7).
13. Controls are derived from status, not `allowed_actions` (1.8).
14. Assignee: only "Assign to me" / "Unassign" (1.9).
15. Attachment links rely on the session cookie behind the 302.
16. Docs-sourced articles are read-only with "Edit this in the repository"; the list's `body_md` is used because no single-article read exists.
17. Null metrics render as "—".
