---
name: mongodb-slowquery-triage
description: Parse MongoDB slow query logs / profiler entries / explain output and generate actionable optimization suggestions (indexes, query rewrite, aggregation pipeline order, sort+limit, pagination). Use when a TG/IM alert or log line says MongoDbConnection 慢查询 / slow query and includes a command (find/aggregate/count) and duration_ms, or when the user pastes MongoDB explain("executionStats") output and wants diagnosis + next steps.
---

# Workflow (MongoDB slow query triage)

## 0) Goal
From a pasted slow query (often from TG alerts), produce **actionable** output:
- What query ran (normalized)
- Most likely performance root causes (ranked)
- Concrete fixes (indexes / query changes) with **risk & validation steps**
- Follow-up questions only when necessary (minimal)

Output should be safe-by-default: suggest changes, do not apply them.

## 1) Parse & normalize the input
Accept any of these:
- A log line containing JSON like: `慢查询 {"command":{...},"duration_ms":1234}`
- A profiler entry (from `system.profile`)
- `explain("executionStats")` output

Extract when present:
- collection (`find`, `aggregate`, `count`)
- filter / pipeline
- sort / limit / skip
- projection
- duration (ms)
- namespace / database (`ns`) if present

Normalize into a canonical summary:
- Query shape: equality filters vs range filters vs $in/$or/$regex
- Sort pattern
- Pagination pattern: `skip/limit` or cursor/range

If key fields are missing (e.g., no filter/sort), ask for only the missing pieces.

## 2) Quick classification (pick the most likely)
Classify into one (or more) buckets:
- **Missing index for filter** (COLLSCAN symptoms)
- **Missing index for sort** (in-memory sort / hasSortStage)
- **Sort+limit not supported by index** (needs compound index)
- **Skip with large offset** (deep pagination)
- **Inefficient time-window query** (e.g., `start_time <= t` AND `end_time >= t`)
- **Count/aggregation too expensive** (need index / approximation / pre-agg)
- **Unexpected slowness despite _id query** (sharding / type mismatch / connection/IO)

## 3) Generate suggestions (ranked)
Always provide:
- 1–3 highest ROI fixes first
- Recommended index definitions (single/compound) in Mongo shell syntax
- Optional alternative design suggestions (denormalize / derived fields / precompute)
- Tradeoffs: write amplification, index size, uniqueness/partial index opportunities

Rules of thumb for index suggestions:
- Equality fields first, then sort fields, then range fields (when applicable)
- If query is `filter + sort + limit`, prefer a compound index that matches both filter and sort
- If field is low-cardinality and always filtered (e.g. `is_public=1`), consider **partial index** or put it first depending on selectivity
- For stable sorting/pagination, consider tie-breaker `_id`

Special handling patterns:
- **Pure sort + limit**: index on sort field (and optional `_id` tie-breaker)
- **filter A + sort B**: compound index `{A:1, B:-1}`
- **time window active query**: consider derived `is_active` / bucketing rather than two-sided range
- **deep pagination**: switch from `skip` to range/cursor pagination using last seen key

## 4) Validation steps (must include)
Suggest verifying with:
- `explain("executionStats")` before/after
- Compare `totalDocsExamined`, `totalKeysExamined`, `nReturned`, and whether a SORT stage exists
- In production: rollout plan (create index in background, monitor CPU/IO, then deploy query change)

If the user can run commands, ask for:
- `db.<coll>.getIndexes()`
- `db.<coll>.find(...).sort(...).limit(...).explain("executionStats")`

## 5) Output template
Return sections in this order:
1) 摘要（collection / duration / query shape）
2) 关键信息（filter/sort/limit/skip/projection）
3) 初步诊断（Top 3，按概率）
4) 优化建议（按收益排序，含建议索引）
5) 验证与上线步骤（explain 对比、灰度、监控指标）
6) 需要补充的信息（若必要，最少问题）

## 6) Optional: use bundled helpers
If many slow logs are pasted at once, use `scripts/parse_mongo_slowlog.py` to extract JSON payloads and group by query shape.
