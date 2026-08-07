# vLLM 本地推理部署指南

> 文档日期：2026-07-07
> 适用组件：[services/python/model_adapter_service/main.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/python/model_adapter_service/main.py) 中的 `VLLMProvider`

---

## 一、概览

AgentHub 通过 `VLLMProvider` 接入 [vLLM](https://docs.vllm.ai) 本地推理服务。vLLM 暴露 OpenAI 兼容 HTTP API，因此 `VLLMProvider` 继承 `OpenAICompatibleProvider`，仅覆盖环境变量读取逻辑。

**核心特性**：
- 复用 OpenAI 兼容协议（`/v1/chat/completions`、`/v1/embeddings`、`/v1/models`）
- 自动剥离 `vllm-` / `vllm/` 路由前缀，vLLM 服务接收真实 HF model id
- 优先读 `VLLM_*` 环境变量，fallback 到 `OPENAI_COMPATIBLE_*`
- 支持 chat（同步+SSE 流式）+ embedding，不支持 rerank

**适用场景**：
- 数据隐私要求高（不出内网）
- 离线环境（无外网 API 访问）
- 高并发推理降低 API 成本
- 模型定制（微调后的 HF 模型）

---

## 二、硬件要求

| 模式 | GPU | VRAM | 推荐型号 | 性能 |
|---|---|---|---|---|
| GPU 生产 | 必需 | 8GB+ | T4 / A10 / A100 / L40S | 高（首 token < 1s） |
| GPU 入门 | 必需 | 8GB | RTX 4090 / RTX 3090 | 中（7B 模型可用） |
| CPU 验证 | 不需要 | — | — | 低（仅功能验证，不可生产） |

**模型规模建议**：
- 7B 模型（Qwen2.5-7B、Llama-3-8B）：VRAM ≥ 16GB（FP16）或 8GB（INT4 量化）
- 14B 模型（Qwen2.5-14B）：VRAM ≥ 32GB
- 70B 模型（Llama-3-70B）：VRAM ≥ 140GB（需多卡张量并行）

---

## 三、部署方式

### 3.1 Docker Compose 部署（推荐）

**前置条件**：
- Docker 24+
- （GPU 模式）NVIDIA Driver + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/)

**启动命令**：

```bash
# CPU 模式（仅功能验证）
docker compose -f deploy/docker-compose.platform.yml --profile vllm up -d vllm

# GPU 模式：编辑 docker-compose.platform.yml 取消 vllm 服务的 deploy 段注释
# 然后启动
docker compose -f deploy/docker-compose.platform.yml --profile vllm up -d vllm
```

**环境变量配置**（在 `.env` 或 docker-compose 启动前导出）：

```bash
# 必填
VLLM_MODEL=Qwen/Qwen2.5-7B-Instruct       # HF model id 或本地路径

# 可选
HF_TOKEN=hf_xxxxxxxx                       # 拉取 gated 模型时需要
VLLM_API_KEY=not-needed                    # vLLM 服务 API Key
VLLM_TENSOR_PARALLEL_SIZE=1                # GPU 数（多卡张量并行）
VLLM_MAX_MODEL_LEN=8192                    # 最大序列长度
VLLM_GPU_MEMORY_UTILIZATION=0.9            # GPU 内存利用率（0.0-1.0）
```

**健康检查**：

```bash
# vLLM 服务直接健康检查
curl http://localhost:8106/health

# 通过 model-adapter 调用
curl http://localhost:8091/healthz
curl http://localhost:8091/v1/models | grep vllm
```

### 3.2 配置 model-adapter 连接 vLLM

在 model-adapter-service 的环境变量中设置：

```bash
# 优先使用 VLLM_BASE_URL（推荐）
VLLM_BASE_URL=http://vllm:8000/v1
VLLM_API_KEY=not-needed

# 或使用 OPENAI_COMPATIBLE_BASE_URL（兼容旧配置）
# OPENAI_COMPATIBLE_BASE_URL=http://vllm:8000/v1
# OPENAI_COMPATIBLE_API_KEY=not-needed
```

### 3.3 调用示例

**Chat 调用**（通过 model-adapter）：

```bash
curl -X POST http://localhost:8091/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "vllm-Qwen/Qwen2.5-7B-Instruct",
    "messages": [{"role": "user", "content": "你好，介绍一下你自己"}],
    "temperature": 0.7,
    "max_tokens": 512
  }'
```

**流式 Chat**：

```bash
curl -X POST http://localhost:8091/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "vllm-Qwen/Qwen2.5-7B-Instruct",
    "messages": [{"role": "user", "content": "写一首关于编程的诗"}],
    "stream": true
  }'
```

**Embedding**：

```bash
curl -X POST http://localhost:8091/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "vllm-bge-m3",
    "input": "这是一段需要embedding的文本"
  }'
```

---

## 四、模型命名约定

调用时 `model` 字段必须以 `vllm-` 或 `vllm/` 开头，后接 HuggingFace model id：

| 调用 model 字段 | vLLM 实际接收 | 说明 |
|---|---|---|
| `vllm-Qwen/Qwen2.5-7B-Instruct` | `Qwen/Qwen2.5-7B-Instruct` | 标准格式 |
| `vllm/Qwen/Qwen2.5-14B-Instruct` | `Qwen/Qwen2.5-14B-Instruct` | 斜杠分隔 |
| `vllm-meta-llama/Meta-Llama-3-8B-Instruct` | `meta-llama/Meta-Llama-3-8B-Instruct` | Llama 系列 |

**已注册模型**（见 [main.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/python/model_adapter_service/main.py) `/v1/models` 端点）：
- `vllm-Qwen/Qwen2.5-7B-Instruct`
- `vllm-Qwen/Qwen2.5-14B-Instruct`
- `vllm-meta-llama/Meta-Llama-3-8B-Instruct`
- `vllm-meta-llama/Meta-Llama-3-70B-Instruct`
- `vllm-microsoft/Phi-3-medium-4k-instruct`

实际可用模型由 vLLM 服务启动时 `--model` 参数决定，上述列表仅作注册声明。

---

## 五、性能调优

### 5.1 vLLM 服务端调优

| 参数 | 默认值 | 调优建议 |
|---|---|---|
| `--tensor-parallel-size` | 1 | 多 GPU 时设为 GPU 数 |
| `--gpu-memory-utilization` | 0.9 | 显存紧张时降到 0.7-0.8 |
| `--max-model-len` | 8192 | 长文本场景提到 32768，但显存占用增加 |
| `--quantization` | 无 | awq / gptq / squeezellm 降低显存 |
| `--enforce-eager` | false | 诊断 CUDA graph 问题时设 true |
| `--swap-space` | 4 (GB) | KV cache 溢出到 CPU 的内存 |

### 5.2 model-adapter 调优

- `_httpx_client()` 超时 120s（vLLM 长生成场景足够）
- 流式调用使用 SSE，无超时限制
- 若 vLLM 服务慢，可在 model-adapter 前加 Redis 缓存层

### 5.3 监控指标

vLLM 暴露 Prometheus 指标在 `http://vllm:8000/metrics`：

- `vllm:num_requests_running` — 运行中请求数
- `vllm:num_requests_waiting` — 排队请求数
- `vllm:gpu_cache_usage_perc` — KV cache 使用率
- `vllm:time_to_first_token_seconds` — 首 token 延迟
- `vllm:e2e_request_latency_seconds` — 端到端延迟

可在 [prometheus.yml](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/deploy/prometheus.yml) 添加 scrape config 接入 Grafana。

---

## 六、故障排查

### 6.1 启动失败

**症状**：`docker compose --profile vllm up` 后容器立即退出

**排查**：
```bash
docker logs agenthub-vllm-1
```

常见原因：
- GPU 驱动不匹配 → 升级 NVIDIA driver
- 模型不存在 → 检查 `VLLM_MODEL` 拼写
- 显存不足 → 降低 `VLLM_GPU_MEMORY_UTILIZATION` 或换更小模型
- gated 模型未授权 → 设置 `HF_TOKEN`

### 6.2 healthcheck 失败

**症状**：容器启动但 healthcheck 一直 unhealthy

**原因**：vLLM 模型加载需要 2-5 分钟，healthcheck `start_period: 120s` 可能不够

**解决**：调大 `start_period` 到 300s，或在 docker-compose.yml 中暂时禁用 healthcheck

### 6.3 调用返回 404

**症状**：`curl /v1/chat/completions` 返回 404

**原因**：vLLM 服务未启动，或 `VLLM_BASE_URL` 配置错误

**排查**：
```bash
# 直接访问 vLLM 服务
curl http://vllm:8000/v1/models

# 检查 model-adapter 路由
curl http://localhost:8091/profile  # 应包含 "vllm" in providers
```

### 6.4 model 字段错误

**症状**：返回 `model not found`

**原因**：vLLM 服务加载的模型与调用 model 字段不匹配

**解决**：检查 `docker compose up` 时 `VLLM_MODEL` 环境变量，确保调用方传的 `vllm-<model-id>` 与之一致

---

## 七、与 OpenAICompatibleProvider 的关系

`VLLMProvider` 继承 `OpenAICompatibleProvider`，两者协议完全兼容。区别仅在环境变量：

| 维度 | OpenAICompatibleProvider | VLLMProvider |
|---|---|---|
| 用途 | 通用 OpenAI 兼容端点（Ollama / LiteLLM / 任意兼容服务） | vLLM 专用 |
| 环境变量 | `OPENAI_COMPATIBLE_*` | `VLLM_*`（fallback `OPENAI_COMPATIBLE_*`） |
| model 前缀剥离 | 不剥离 | 自动剥离 `vllm-` / `vllm/` |
| 路由优先级 | 低 | 高（`get_provider()` 先查 `VLLM_BASE_URL`） |

**建议**：生产环境用 vLLM 时优先配置 `VLLM_*` 环境变量，避免与 Ollama 等其他兼容服务混淆。
