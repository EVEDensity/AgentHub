# AgentHub Sandbox Image

沙盒执行环境镜像，由 `sandbox-service` 在 Agent 调用 `code_execute` 工具时启动为一次性容器。

## 构建命令

```bash
# 从项目根目录执行
docker build -t agenthub/sandbox:latest \
  -f deploy/sandbox-image/Dockerfile deploy/sandbox-image/
```

## 镜像内容

| 组件 | 版本 | 用途 |
|---|---|---|
| Python | 3.11-slim | 代码执行运行时 |
| numpy | 2.2.1 | 数值计算 |
| pandas | 2.2.3 | 数据处理 |
| httpx | 0.28.1 | HTTP 客户端 |
| requests | 2.32.3 | HTTP 客户端（同步） |
| curl + jq | 系统包 | Shell 工具 |

## 安全特性

- **非 root 用户**：以 `sandbox`（uid 1000）身份运行
- **无编译器**：slim 基础镜像不含 gcc/g++
- **网络隔离**：sandbox-service 默认以 `--network none` 启动容器
- **资源限制**：CPU/内存/磁盘由 sandbox-service 创建时指定

## 自定义沙盒镜像

如需添加更多库（如 matplotlib、scipy），修改本 Dockerfile 后重新构建：

```dockerfile
RUN pip install --no-cache-dir \
    numpy==2.2.1 pandas==2.2.3 httpx==0.28.1 requests==2.32.3 \
    matplotlib==3.9.2 scipy==1.14.1
```

然后在 `.env` 中指定自定义镜像名：

```
SANDBOX_IMAGE=agenthub/sandbox:custom
```

## 与 sandbox-service 的关系

```
Agent (code_execute)
    ↓
sandbox-service (Go, :8097)
    ↓ POST /containers + POST /containers/{id}/exec
Docker Engine (/var/run/docker.sock)
    ↓
agenthub/sandbox:latest (本镜像，一次性容器)
```

sandbox-service 本身**不包含** Python 运行时——它只是 Docker 容器的生命周期管理器。实际代码在 sandbox 镜像中执行。
