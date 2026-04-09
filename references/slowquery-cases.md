# MongoDB Slow Query Case Library (yc114)

> 来源：项目慢查询日志（应用侧记录）。
> 说明：应用侧慢查询耗时可能包含网络/连接池等待/选主重试等；需要结合服务端 profiler/explain 交叉验证。

## Case: _id findOne still takes seconds (suspect client-side or cluster issues)

### Pattern
- Query shape: `find` with `filter: {_id: <value>}`, `limit: 1`, `skip: 0`
- Expected: milliseconds (default _id index)
- Observed: 2–8 seconds

### Samples (2026-03-25)
- `post` `_id:"347138fa48048e02"` duration_ms ~3366
- `post_tag` `_id:58` duration_ms ~3188
- `post` `_id:"04e4457479472fc3"` duration_ms ~8200
- `post` `_id:"31f1ae973c871765"` duration_ms ~2723
- `post` `_id:"effebbd29ce963c3"` duration_ms ~2760

### Likely root causes (ranked)
1) **应用侧计时包含等待**：连接池排队、重连、选主/重试、网络抖动
2) **分片 scatter-gather**：按 `_id` 查但 shard key ≠ `_id`
3) **实例资源瓶颈**：磁盘 IO、cache 冷、WiredTiger cache 压力、CPU/线程排队
4) **类型/编码异常**：极少见（例如 `_id` 真实类型不一致导致无法走理想路径），需 explain 验证

### Verification checklist
- 取同形态查询在服务端执行：`explain("executionStats")` 看 `executionTimeMillis` 是否同样很高
- 确认是否 sharded collection，shard key 是什么
- 拉同时间段 Mongo 监控：connections/queue/CPU/IO/primary stepdown
- 检查应用 Mongo client：是否复用 client、连接池大小、是否频繁重建连接

### Fix ideas
- 若是连接池/重连：修复连接复用、调大 pool、排查泄漏、设置合理超时/重试
- 若是 sharding：引入 shard key 过滤条件或调整 shard key（成本较高）
- 若是资源瓶颈：扩容/优化磁盘/提升 cache 命中、避免热点抖动

### Project-specific note
- 在 yc114 项目里，这类慢查询不一定都是 Mongo 实例本身异常；也经常是**项目代码查询方式**导致，例如在循环中按 id/mid 逐条查详情（N+1 查询模式）。
- 另一类情况是**业务确实需要单条补查**，此时不要误判成代码缺陷，应优先区分“可批量改写”与“业务必要单查”。

---

## Case: Repeated single-row lookup caused by code query pattern (N+1 style)

### Pattern
- 单条查询本身看似简单：按 `_id` / `mid` / 唯一键 `findOne`
- 但在代码中被放在循环里反复执行，最终在慢日志中大量出现

### Common examples
- 先查列表，再在循环中逐条按 `_id` / `mid` 查标签、分类、详情
- 评论/资源聚合展示时，对每一项分别补查关联集合

### Diagnosis hints
- 同时间窗内，同一集合大量出现结构几乎一致的 `findOne`
- 集合名分布在 `post_tag` / `movie_tag` / `comics_tag` / `user` / `category` 等“字典/关联表”上尤其常见

### Preferred optimization
1) 能批量查就批量查：`$in` 一次取回后在内存映射
2) 能预加载就预加载：先收集 ids，再做一次查询
3) 能缓存的字典表/标签表尽量缓存
4) 若确实必须单查，至少确认有正确索引，避免误伤主流程

### Not all cases are bugs
- 有些单查是业务必需，例如详情页核心对象读取、写入前校验、权限判断
- 这类情况应标记为“业务必要单查”，重点看索引、实例状态、日志口径，而不是强行改批量

---

## Case: Multi-condition count without compound index

### Pattern
- `count` / 统计类查询按多个字段组合过滤
- 文档定义中只���单列索引，无对应复合索引

### Verified example (yc114)
- collection: `comment`
- query: `{object_id, object_type, status}`
- schema/index file: `common/comment.json`
- current indexes: `object_id`, `object_type`, `status`（单列）

### Diagnosis
- 这是典型的“业务查询条件稳定，但索引设计未跟上”
- 单列索引难以高效支撑三字段组合 count

### Recommended index
```javascript
db.comment.createIndex({ object_id: 1, object_type: 1, status: 1 })
```

### Caveat
- 若后续还有排序/分页，需要结合实际查询再评估索引顺序，而不是只按 count 盲加

---

## Case: Query field mismatches resource definition or lacks schema confirmation

### Pattern
- 慢查询使用的过滤字段，与当前资源定义文件中的字段不一致

### Verified example (yc114)
- query: `movie_category` filter `{ code: "3" }`
- resource file: `movie/movie_category.json`
- current schema fields do **not** include `code`

### Diagnosis possibilities
1) 代码查错字段
2) 线上文档已有该字段，但资源定义未更新
3) 历史兼容字段仍在使用，但未建索引

### Preferred handling
- 先校对“代码查询字段”与“资源定义字段”是否一致
- 再决定是修代码、补文档定义，还是补索引
- 这类问题不要直接按“缺索引”处理，先确保结构认知正确
