# meowgent Memory API Worker

Cloudflare Worker + D1 foundation for meowgent's long-term memory. The Worker is intentionally independent from the Python Discord bot; Active Memory accesses it over HTTPS through the Python `MemoryClient`.

## Environments

The Wrangler environments are deliberately separate:

| Environment | Worker | D1 |
| --- | --- | --- |
| `dev` | `meowgent-memory-dev` | `meowgent-dev` |
| `prod` | `meowgent-memory-prod` | `yukari` |

The placeholder UUID in the `dev` environment is a local-only ID. The `prod` environment is configured for the existing `yukari` D1 database. This keeps dev and prod local state separate and prevents a first deployment from accidentally pointing at an unknown database.

```sh
cd memory-worker
npm install

npx wrangler d1 create meowgent-dev
# The prod environment uses the existing `yukari` database.
# Copy the returned dev database_id into the matching env in wrangler.jsonc.
```

For local development, create `memory-worker/.dev.vars.dev` from `worker-env.example` and apply the same migrations used in every environment:

```sh
npm run db:migrate:local
npm run dev
```

The local D1 database is stored by Wrangler under `.wrangler/` and is not committed. To apply migrations remotely, use the database name explicitly:

```sh
npm run db:migrate:dev
npm run deploy:dev

npm run db:migrate:prod
npm run deploy:prod
```

Apply schema changes by adding a numbered migration and promoting that same migration through local, development, and production. Do not make ad-hoc schema changes directly in a remote database.

Set a different encrypted API token for each deployed Worker. Do not put these values in `wrangler.jsonc`:

```sh
npx wrangler secret put MEMORY_API_TOKEN --env dev
npx wrangler secret put MEMORY_API_TOKEN --env prod
```

## API

All `/memories` endpoints require:

```http
Authorization: Bearer <MEMORY_API_TOKEN>
```

`POST /memories` creates one `episode` or `note`. `access_scope` is required so a caller cannot accidentally create a memory with an ambiguous privacy policy. `source_message_ids` is a convenience form for Discord sources; the normalized source rows are returned in `sources`. Search accepts an optional `subject_user_id` filter.

Timestamp fields use `YYYY-MM-DDTHH:mm:ss[.SSS](Z|+/-HH:mm)`. Impossible dates, timezone-less values, and non-ISO date strings are rejected.

```json
{
  "kind": "episode",
  "content": "sota bought a PCD1000.",
  "subject_user_id": "123456789",
  "guild_id": "987654321",
  "channel_id": "555555555",
  "happened_at": "2026-08-25T12:00:00+09:00",
  "importance": 0.8,
  "confidence": 0.98,
  "access_scope": "GUILD",
  "source_message_ids": ["111111111", "222222222"]
}
```

Available routes:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/memories` | Create a memory and optional source rows |
| `POST` | `/memories/search` | ACL-filtered lexical search; default limit is 5 |
| `GET` | `/memories/:id` | Read a memory visible in the supplied context |
| `PATCH` | `/memories/:id` | Update metadata or set `archived_at` |
| `GET` | `/memories` | ACL-filtered listing for operational use |
| `GET` | `/health` | Unauthenticated health check |

For search and direct reads, pass the Discord context. A guild context is inferred when `guild_id` is supplied; DM access must be explicit:

```json
{
  "query": "PCD1000",
  "requester_user_id": "123456789",
  "guild_id": "987654321",
  "channel_id": "555555555"
}
```

```json
{
  "query": "character preference",
  "requester_user_id": "123456789",
  "context_type": "dm",
  "channel_id": "444444444"
}
```

The Worker applies visibility conditions in the D1 query. `USER_PRIVATE` and `DM` memories are only considered for an explicit DM context, so private memories are not fetched and filtered after the result has already been assembled.

`PATCH` supports metadata, timestamps, `recall_count`, and `archived_at`. It does not modify `kind` or source rows. Episode state changes should be represented by a new episode; content patching is intended for correction and maintenance.

## Search and embeddings

PR1 intentionally does not provision [Vectorize](https://developers.cloudflare.com/vectorize/) or Workers AI. Cloudflare Vectorize is the appropriate semantic-search binding for a later retrieval PR, but it is a separate resource with a fixed vector dimension and requires a second environment-specific index. The current endpoint provides a small deterministic lexical search so the CRUD and ACL contract can be exercised without adding that infrastructure. The Python retriever should depend on this API contract rather than D1 or Vectorize details.

## Tests

The test suite uses Cloudflare's Workers Vitest integration and applies the committed D1 migrations to a local database:

```sh
npm test
npm run check
```
