# 向量数据库迁移评估清单（Qdrant → Milvus）

> 文档日期：2026-07-07
> 当前实现：Qdrant（[services/rust/crates/retrieval-core/src/qdrant.rs](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/rust/crates/retrieval-core/src/qdrant.rs) + [services/python/offline_knowledge_service/qdrant_repo.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/python/offline_knowledge_service/qdrant_repo.py)）
> 备选方案：Milvus（[deploy/docker-compose.platform.yml](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/deploy/docker-compose.platform.yml) 已含注释配置，`--profile milvus` 启用）

---

## 一、当前结论

**当前阶段保留 Qdrant**。Milvus 仅在以下场景评估迁移：
- 向量规模超过 1000 万条
- 需要多索引类型（IVF / HNSW / DiskANN）
- 需要分布式部署（多节点水平扩展）
- 有专职运维团队（Milvus 依赖 etcd + MinIO，运维复杂度高）

---

## 二、对比矩阵

| 维度 | Qdrant | Milvus |
|---|---|---|
| 实现语言 | Rust | Go + C++ |
| 部署复杂度 | 单容器 | 3 容器（etcd + MinIO + Milvus） |
| 单机性能 | 优秀（HNSW） | 优秀 |
| 分布式扩展 | 支持（分片） | 原生支持 |
| 索引类型 | HNSW + 量化 | IVF / HNSW / DiskANN / Binary |
| 十亿级向量 | 弱 | 强 |
| API 简洁度 | 高 | 中 |
| 内存占用 | 低 | 中（依赖 MinIO 缓存） |
| 运维成本 | 低 | 高 |
| 社区活跃度 | 高（20k+ star） | 极高（28k+ star） |
| 与 Rust retrieval-core 集成 | 已实现 | 需新建 milvus.rs |

---

## 三、迁移评估检查点

### 3.1 规模评估

- [ ] 当前向量总数：__________ 条
- [ ] 月增长率：__________ 条/月
- [ ] 预计 1 年后规模：__________ 条
- [ ] 单向量维度：__________ 维（当前 384/768/1024）
- [ ] 单向量大小：__________ 字节
- [ ] 总存储占用：__________ GB

**迁移触发阈值**：
- 向量总数 > 1000 万 → 评估迁移
- 向量总数 > 1 亿 → 强烈建议迁移
- 单机内存不足 → 评估迁移（Milvus 支持 DiskANN 磁盘索引）

### 3.2 查询延迟评估

- [ ] 当前 P50 查询延迟：__________ ms
- [ ] 当前 P95 查询延迟：__________ ms
- [ ] 当前 P99 查询延迟：__________ ms
- [ ] 业务可接受 P95：__________ ms

**Qdrant 性能基线**（本项目实测，19ms 入库 / 507ms 检索含 rerank）：
- 10 万向量：P95 < 50ms
- 100 万向量：P95 < 200ms
- 1000 万向量：P95 < 1s（单机，HNSW）

若 Qdrant 实测 P95 超过业务阈值 2 倍，评估迁移。

### 3.3 索引类型需求

- [ ] 是否需要 DiskANN（磁盘索引，超大规模）？是 → Milvus
- [ ] 是否需要 IVF_FLAT（精确检索 + 聚类）？是 → Milvus
- [ ] 是否需要 Binary 索引（二值向量）？是 → Milvus
- [ ] 是否仅需 HNSW？是 → 保留 Qdrant

### 3.4 运维能力评估

- [ ] 是否有专职运维团队？否 → 保留 Qdrant
- [ ] 是否熟悉 etcd 运维？否 → 保留 Qdrant
- [ ] 是否熟悉 MinIO 运维？否 → 保留 Qdrant（可复用现有 MinIO，但增加复杂度）
- [ ] 是否有 K8s 分布式部署经验？否 → 保留 Qdrant

### 3.5 业务连续性

- [ ] 是否支持迁移期间的停机窗口？否 → 必须双写过渡
- [ ] 数据备份频率要求：__________
- [ ] RTO（恢复时间目标）：__________
- [ ] RPO（恢复点目标）：__________

---

## 四、迁移工作量估算

| 阶段 | 工作量 | 说明 |
|---|---|---|
| Phase 1：环境搭建 | 2 人日 | docker-compose 启用 milvus profile + 验证 |
| Phase 2：Rust 客户端 | 5 人日 | 新建 [milvus.rs](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/rust/crates/retrieval-core/src/qdrant.rs)，复用 retrieval-core 接口 |
| Phase 3：Python 客户端 | 3 人日 | 新建 milvus_repo.py，复用 qdrant_repo.py 接口 |
| Phase 4：双写过渡 | 5 人日 | 写入时同时写 Qdrant + Milvus，读仍走 Qdrant |
| Phase 5：数据迁移 | 3 人日 | 历史向量从 Qdrant 导出到 Milvus |
| Phase 6：切读验证 | 5 人日 | 读切到 Milvus，比对结果一致性 |
| Phase 7：下线 Qdrant | 2 人日 | 停止 Qdrant 双写，归档数据 |
| **总计** | **25 人日** | 约 5 周 |

---

## 五、迁移风险

| 风险 | 概率 | 影响 | 规避 |
|---|---|---|---|
| Rust milvus-sdk 客户端不成熟 | 中 | 高 | 评估 [milvus-sdk-rust](https://github.com/milvus-io/milvus-sdk-rust) star 数和活跃度 |
| 双写期间数据不一致 | 中 | 中 | 用向量 id 做幂等 upsert，定时对账 |
| 切读后性能不达预期 | 中 | 高 | 切读前做完整压测，保留 Qdrant 7 天回退窗口 |
| Milvus 配置复杂导致运维事故 | 高 | 中 | 先在 staging 环境跑 2 周，监控 etcd/MinIO 健康 |
| 迁移期间业务停摆 | 低 | 高 | 双写过渡，读不切换，业务无感 |

---

## 六、推荐决策流程

```
1. 当前是否满足业务需求？
   ├─ 是 → 保留 Qdrant，每年复查本清单
   └─ 否 → 进入 2

2. 瓶颈是规模、延迟还是索引类型？
   ├─ 规模 > 1 亿 → 评估 Milvus + DiskANN
   ├─ 延迟 P95 超标 → 先优化 Qdrant（量化、分片、调 HNSW 参数）
   └─ 需特殊索引 → 评估 Milvus

3. 团队是否有运维能力？
   ├─ 是 → 启动迁移（参考第四节工作量）
   └─ 否 → 招聘/培训运维，或外包托管 Milvus 服务

4. 迁移前必做：
   - [ ] staging 环境完整压测
   - [ ] 双写方案设计评审
   - [ ] 回退预案（Qdrant 保留 7 天）
   - [ ] 监控告警接入（etcd/MinIO/Milvus 三组件）
```

---

## 七、参考资源

- [Qdrant 官方文档](https://qdrant.tech/documentation/)
- [Milvus 官方文档](https://milvus.io/docs)
- [Qdrant vs Milvus 性能对比](https://qdrant.tech/benchmarks/)
- [Milvus 架构白皮书](https://milvus.io/docs/architecture_overview.md)
- 本项目 Qdrant 实现：[qdrant.rs](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/rust/crates/retrieval-core/src/qdrant.rs) + [qdrant_repo.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/python/offline_knowledge_service/qdrant_repo.py)
