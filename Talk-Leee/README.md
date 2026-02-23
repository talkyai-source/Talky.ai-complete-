# Talk-Leee

Talk-Lee  (Next.js App Router)

## Getting Started

### Requirements

- Node.js 20+
- npm 10+

### Install

```bash
npm install
```

### Dev server

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

### Scripts

```bash
npm run lint
npm test
npm run build
npm run start

npm run storybook
npm run build-storybook

npm run docs:openapi
npm run docs:openapi:check
```

### Environment variables

- `NEXT_PUBLIC_API_BASE_URL` (optional): API base URL for generated docs and client configuration.

## Notifications

### UI routes

- `/notifications`: Notification Center (grouped by type, read/unread, timestamps, “mark all read”).
- `/settings`: Settings panel for notifications, privacy, integrations, and account.

### UI components

- `NotificationToaster`: global toast renderer (success/warning/error/info).
- `NotificationBell`: header bell with unread badge + drawer.
- `NotificationCenter`: chronological history grouped by type, read/unread state.

### State and persistence

Notifications are managed client-side via a small external store:

- Store: `src/lib/notifications.ts`
- React hooks: `src/lib/notifications-client.tsx`
- Persistence: `localStorage`
  - `talklee.notifications.v1`: notification history (if enabled)
  - `talklee.notifications.settings.v1`: settings

### Integration (webhook) API

If all of the following are true:

- Category routing is `webhook` or `both`
- Third-party consent is enabled
- Webhook integration is enabled
- Webhook URL is set

Then a `POST` is sent to the configured webhook URL with:

- Headers: `content-type: application/json`
- Body:

```json
{
  "id": "string",
  "type": "success|warning|error|info",
  "priority": "low|normal|high",
  "title": "string",
  "message": "string|null",
  "createdAt": 1730000000000,
  "data": {}
}
```

## API Documentation (OpenAPI)

- Generated spec: `public/openapi.json`
- Regenerate: `npm run docs:openapi`
- CI check: `npm run docs:openapi:check`

The spec is generated from:

- Schemas: `src/lib/models.ts`
- Endpoint registry: `src/lib/backend-endpoints.ts`
- Generator: `scripts/generate-openapi.ts`

## Storybook

Run a component workbench locally:

```bash
npm run storybook
```

Build static Storybook output (used in CI):

```bash
npm run build-storybook
```

## Development workflows

- Add/modify endpoints: update `src/lib/backend-endpoints.ts` and `src/lib/models.ts`, then run `npm run docs:openapi`.
- UI components: add stories in `src/**\/*.stories.tsx` and verify with `npm run storybook`.

## Code standards

- TypeScript-first; prefer explicit props and exported types for shared components.
- Keep API contracts in `src/lib/models.ts` and keep `public/openapi.json` in sync.
- Run `npm run lint` and `npm test` before opening a PR.

## Deploy

```bash
npm run build
npm run start
```

## Learn More

- [Next.js Documentation](https://nextjs.org/docs)
