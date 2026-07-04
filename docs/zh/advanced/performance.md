# 性能调优

AgentHub 的三层性能保障体系确保从开发到生产的高性能运行。

## 关键性能指标

| 指标 | 开发环境 | 生产环境 (≥3 副本) |
|------|---------|-------------------|
| 拓扑图渲染 (200 节点) | 60fps | 60fps |
| 首 Token 延迟 (P95) | < 2s | < 1.2s |
| 记忆检索延迟 (P95) | < 50ms | < 30ms |
| 知识库检索延迟 (P95) | < 200ms | < 80ms |
| WebSocket 消息延迟 | < 100ms | < 50ms |
| API 延迟 (P95) | < 200ms | < 200ms |

## 前端性能

### 代码分割

AgentHub 前端使用 Next.js 的 `dynamic import` 对 22 个管理模块进行懒加载，首屏 bundle size 从 ~800KB 优化到 ~200KB。

### WebGL 渲染

粒子拓扑可视化使用 PixiJS v8 的 WebGL 2.0 渲染：

- **批量渲染**：同类粒子通过 BatchRenderer 单次 draw call 渲染
- **视口剔除**：视口外节点不渲染粒子，减少 60% GPU 负载
- **对象池复用**：ParticlePool 避免 GC 抖动，内存稳定 < 50MB

### 自适应质量

```typescript
// frontend/lib/performance/adaptiveQuality.ts
function getQualityTier(fps: number) {
  if (fps >= 55) return { maxParticles: 3000, glow: true };    // Ultra
  if (fps >= 40) return { maxParticles: 1000, glow: true };    // High
  if (fps >= 25) return { maxParticles: 400, glow: false };    // Medium
  return { maxParticles: 200, glow: false };                    // Low
}
```

### Web Worker

物理模拟（力导向布局计算）运行在 `physics.worker.ts` 中，通过 SharedArrayBuffer 实现零拷贝通信，主线程完全不受物理计算影响。

## Go 服务层

### 连接池配置

```bash
# PostgreSQL 连接池（推荐配置）
PG_MAX_CONNS=20
PG_MIN_CONNS=5
PG_MAX_IDLE_TIME=30m
```

### Redis 热缓存

```
L0 工作记忆：Redis Hash, TTL 24h
会话状态：Redis String, TTL 1h
限流 Token Bucket：Redis Lua Script
```

### 并发控制

- **Gateway**：Goroutine per WebSocket connection，semaphore 限流（最大 10000）
- **Orchestrator**：DeepSearchPool 信号量并发限制（默认 50）
- **Agent Runtime**：Worker pool 按池限流（LLM 300/快速 150/推理 100）

### 熔断器

```go
// services/go/realtime-orchestrator/cmd/realtime-orchestrator/resilience.go
CircuitBreaker {
  State:       CLOSED | OPEN | HALF_OPEN
  Threshold:   5 (连续失败次数)
  OpenDuration: 30s
}
```

双熔断器分别覆盖 model-adapter 和 retrieval-core。

## Rust 性能核心

5 个 Rust crate 通过 NATS 与 Go 服务解耦通信：

| Crate | 核心优化 |
|-------|---------|
| **stream-core** | tokio async I/O, 背压窗口 (1024), 零拷贝 chunk 合并 |
| **retrieval-core** | Qdrant HNSW 索引 < 10ms, OpenSearch BM25 < 20ms, RRF 融合 |
| **fanout-core** | 高基数通道分区, 慢订阅者自动降级 |
| **patch-merge-core** | LCS 差分算法 O(n*m), diff3 三路合并 |
| **memory-segment-core** | 滑动窗口压缩 (60s), 定期 checkpoint |

## 降级链

4 级降级链保证系统韧性：

```
主线路（正常）
  │
  ▼ 失败
备用线路（功能等价）
  │
  ▼ 失败  
轻量规则引擎（无 LLM 依赖）
  │
  ▼ 失败
人工接管（通知用户 + 保留上下文）
```

## 数据层

| 组件 | 优化 | 说明 |
|------|------|------|
| **PostgreSQL** | 时间分区表 + 复合索引 | 查询优化 |
| **Qdrant** | HNSW 索引参数调优 | m=16, ef_construct=200 |
| **OpenSearch** | BM25 标准化 | k1=1.2, b=0.75 |
| **Redis** | Pipeline 批量写入 | 减少 RTT |
| **MinIO** | 预签名 URL 直传 | 绕过 Gateway |

## K8s 自动扩缩

```yaml
# KEDA 基于 Prometheus 指标的自动扩缩
- WS 连接数 > 8000/实例 → 扩容
- NATS 消费延迟 > 5s → 扩容
- SSE 积压 > 500 → 扩容

# HPA 基于资源指标的扩缩
- CPU > 70% → 扩容
- Memory > 80% → 扩容
```

## 下一步

- [安全架构](/zh/advanced/security)
- [K8s 生产部署](/zh/advanced/k8s-deployment)
