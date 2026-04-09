# MongoDB slow query triage cheatsheet (for the agent)

Keep this file short and practical.

## What to ask for (minimum)
- `db.<coll>.getIndexes()`
- `explain("executionStats")` for the exact query/pipeline
- MongoDB version + whether sharded

## Explain fields to interpret
- `winningPlan` contains `COLLSCAN` vs `IXSCAN`
- `executionStats.nReturned`
- `executionStats.totalDocsExamined` and `executionStats.totalKeysExamined`
- Presence of `SORT` stage (or `hasSortStage` in logs)

## Common patterns → suggestions

### Pure `sort + limit`
- Index on the sort key. Add `_id` as tie-breaker if order must be stable.

### `filter(eq) + sort + limit`
- Compound index: equality fields first, then sort fields.

### `filter(range) + sort`
- You often can’t satisfy both perfectly. Consider:
  - compound index starting with equality fields, then sort
  - rethinking data model (derived flag like `is_active`)

### Deep pagination with `skip`
- Prefer range/cursor pagination using last seen values.

### Two-sided time window: `start <= t AND end >= t`
- Hard to index efficiently.
- Options:
  - derived `is_active`
  - precompute active IDs per slot
  - add another selective filter (e.g. position_code) + compound index

### `_id` point query is slow
- Not normal. Check:
  - sharding scatter-gather
  - type mismatch of `_id`
  - connection pool / IO pressure

### `count` is slow
- Ensure index on filter fields.
- Avoid exact counts in hot paths; consider cached counters.
