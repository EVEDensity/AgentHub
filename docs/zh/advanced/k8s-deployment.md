# K8s 生产部署

在生产环境中使用 Kubernetes 部署 AgentHub 的完整指南。

## 架构概览

```
┌─────────────────────────────────────────────┐
│              K8s Cluster (≥ 3 nodes)         │
├─────────────────────────────────────────────┤
│                                             │
│  Ingress (nginx-ingress / Traefik)          │
│    ├ TLS 1.3 termination                    │
│    ├ Rate limiting                          │
│    └ WAF (ModSecurity)                      │
│                                             │
│  ├─ agenthub-gateway (3 replicas)           │
│  ├─ agenthub-orchestrator (2 replicas)       │
│  ├─ agenthub-rust-* (2 replicas each)        │
│  └─ agenthub-python-* (2 replicas each)      │
│                                             │
│  Stateful Services:                         │
│  ├─ PostgreSQL 16 (3-node HA)               │
│  ├─ Redis 7.2 (Sentinel)                    │
│  ├─ Qdrant (2 replicas)                     │
│  ├─ OpenSearch (3-node)                     │
│  ├─ NATS JetStream (3-node cluster)         │
│  └─ MinIO (4-node)                          │
│                                             │
│  Observability:                             │
│  ├─ Prometheus + Grafana                    │
│  ├─ Loki + Promtail (日志)                   │
│  └─ Tempo (链路追踪)                         │
│                                             │
└─────────────────────────────────────────────┘
```

## 快速部署

### 前置条件

- K8s v1.28+
- Helm v3.14+
- kubectl 已配置
- cert-manager (TLS 证书管理)
- Ingress Controller (nginx/traefik)

### 一键部署

```bash
# 添加 Helm 仓库
helm repo add agenthub https://charts.agenthub.dev
helm repo update

# 创建命名空间
kubectl create namespace agenthub

# 安装 (使用生产配置)
helm install agenthub agenthub/agenthub \
  -f values-prod.yaml \
  -n agenthub

# 查看状态
kubectl get pods -n agenthub -w
```

### Kustomize 部署

```bash
kubectl apply -k k8s/overlays/prod/
```

## values-prod.yaml 示例

```yaml
# 副本数
gateway:
  replicas: 3
  resources:
    requests: { cpu: 500m, memory: 512Mi }
    limits: { cpu: 2, memory: 2Gi }

orchestrator:
  replicas: 2

rustServices:
  replicas: 2
  resources:
    limits: { cpu: 4, memory: 4Gi }

# 自动扩缩
autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70

# Ingress
ingress:
  enabled: true
  className: nginx
  hosts:
    - agenthub.example.com
  tls:
    - secretName: agenthub-tls
      hosts: [agenthub.example.com]

# 数据库连接
postgresql:
  host: pg-cluster.agenthub.svc.cluster.local
  port: 5432
  database: agenthub
  secretName: agenthub-pg-credentials

# 对象存储
minio:
  endpoint: minio.agenthub.svc.cluster.local:9000
  bucket: agenthub
  secretName: agenthub-minio-credentials

# 监控
monitoring:
  enabled: true
  serviceMonitor:
    enabled: true
    interval: 30s
```

## 扩缩容

### KEDA 事件驱动

```yaml
# KEDA ScaledObject
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: gateway-scaler
  namespace: agenthub
spec:
  scaleTargetRef:
    name: agenthub-gateway
  minReplicaCount: 3
  maxReplicaCount: 20
  triggers:
    - type: prometheus
      metadata:
        serverAddress: http://prometheus.monitoring:9090
        metricName: http_requests_per_second
        threshold: "1000"
```

### 手动扩容

```bash
kubectl scale deployment agenthub-gateway --replicas=5 -n agenthub
```

## Canary 发布

```bash
# Argo Rollouts Canary
kubectl argo rollouts set canary agenthub-gateway \
  --set-weight=10 \
  --set-stable-weight=90 \
  -n agenthub

# 验证 1 小时后全量发布
kubectl argo rollouts promote agenthub-gateway -n agenthub
```

## 备份策略

```yaml
# Velero 备份
schedule: "0 2 * * *"     # 每天凌晨 2 点
ttl: 720h                  # 保留 30 天
includedNamespaces: [agenthub]
storageLocation: s3-backup
```

## SLO / SLA 目标

| 指标 | SLO | SLA | 测量窗口 |
|------|-----|-----|---------|
| API 可用性 | 99.9% | 99.5% | 30 天 |
| API 延迟 P95 | < 200ms | < 500ms | 1 小时 |
| WebSocket 延迟 P95 | < 100ms | < 300ms | 1 小时 |
| RPO | < 1 min | < 15 min | — |
| RTO | < 5 min | < 30 min | — |

## 下一步

- [CI/CD 流水线](/zh/advanced/cicd) — 自动化部署
- [性能调优](/zh/advanced/performance) — 优化延迟和吞吐
