# 知识库 API

知识库文档的上传、检索和管理的完整 API 参考。

## 端点总览

| Method | Path | 说明 |
|--------|------|------|
| `GET` | `/platform/knowledge/rag-search` | RAG 混合检索 |
| `POST` | `/api/knowledge/documents` | 上传文档 |
| `GET` | `/api/knowledge/documents` | 列出文档 |
| `GET` | `/api/knowledge/documents/:id` | 获取文档详情 |
| `DELETE` | `/api/knowledge/documents/:id` | 删除文档 |
| `POST` | `/api/knowledge/collections` | 创建集合 |
| `GET` | `/api/knowledge/collections` | 列出集合 |

## RAG 检索

```bash
GET /platform/knowledge/rag-search?q=AgentNet+DAG调度策略&source=project_docs,code&top_k=10&include_images=true&time_range=30d&sort=relevance
```

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `q` | string | (必填) | 搜索查询 |
| `source` | string[] | `["project_docs"]` | 知识源列表 |
| `top_k` | int | 10 | 返回结果数 (1-50) |
| `include_images` | bool | true | 是否包含图片检索 |
| `time_range` | string | `"30d"` | 时间范围 (`7d`/`30d`/`90d`/`all`) |
| `sort` | string | `"relevance"` | 排序方式 (`relevance`/`date`/`hybrid`) |

### 响应

```json
{
  "query": "AgentNet DAG调度策略",
  "rewrites": [
    "AgentNet DAG 任务调度策略 架构设计",
    "AgentNet DAG 调度 实现原理",
    "AgentNet DAG 调度策略 最佳实践"
  ],
  "results": [
    {
      "source_id": "docs-agentnet-v5",
      "chunk_id": "c-3",
      "text": "AgentNet 采用 BFS 就绪节点检测算法...",
      "score": 0.92,
      "source_type": "project_docs",
      "metadata": {
        "file_path": "docs/agentnet-design.md",
        "title": "AgentNet DAG 调度策略"
      },
      "highlights": ["<mark>DAG</mark> 任务分配策略..."]
    }
  ],
  "images": [],
  "fusion": "rrf",
  "latency_ms": 45.3
}
```

## 支持的知识源

| source 参数值 | 对应集合 | 说明 |
|--------------|---------|------|
| `project_docs` | docs | 项目文档 (Markdown, MDX) |
| `api_docs` | docs | API 文档 (OpenAPI) |
| `uploaded_docs` | docs | 用户上传文档 (PDF, DOCX, PPTX) |
| `code_repos` | code | 代码仓库 (CodeBERT embedding) |
| `sessions` | memory | 会话历史 |
| `artifacts` | artifacts | Agent 产物 |

## 上传文档

```bash
POST /api/knowledge/documents
Content-Type: multipart/form-data

file: @docs/agentnet-design.md
collection: project-docs
metadata: {"title": "AgentNet 设计文档", "version": "v5.1"}
```

## 下一步

- [接入知识库教程](/zh/guide/knowledge-base)
- [RAG 检索架构](/zh/guide/knowledge-base#检索架构)
