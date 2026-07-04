# 接入知识库

AgentHub 的知识库系统基于 RAG（检索增强生成）技术，支持多种文档格式的智能检索。

## 支持的知识源

| 知识源类型 | 支持格式 | 分块策略 | 示例 |
|-----------|---------|---------|------|
| 项目文档 | Markdown, MDX | 按 `##` 标题分块 (512 tokens) | `docs/` 目录 |
| API 文档 | OpenAPI JSON/YAML | 按 endpoint 分块 | Swagger 导出 |
| 上传文档 | PDF, DOCX, PPTX | 递归分块 (512 tokens, overlap 64) | 产品需求文档 |
| 代码仓库 | .ts/.go/.py/.rs | 按函数/类分块 (CodeBERT) | GitHub 仓库 |
| 会话记录 | 聊天历史 | 按消息轮次分块 | Agent 聊天记录 |

## 检索架构

```
用户查询 "AgentNet DAG 调度"
    │
    ▼
Query 改写 (多视角 + 关键词提取)
    │
    ▼
混合检索
├ Qdrant 向量检索 (all-MiniLM-L6-v2, cosine)
├ OpenSearch BM25 全文检索
└ RRF 融合 (k=60)
    │
    ▼
BGE-Reranker-v2 重排序
    │
    ▼
返回带 citation 的检索结果
```

## 快速接入

### 1. 上传文档

管理后台 → **知识库** → **上传文档** → 选择文件拖拽上传。

支持批量上传，自动分块和向量化。

### 2. 配置检索参数

```typescript
// 在知识库节点中配置
{
  collectionName: "project-docs",
  queryTemplate: "{{user_message}} 的架构设计",
  topK: 5,
  minScore: 0.5,          // 最低相似度阈值
  includeImages: true     // 是否包含图片检索
}
```

### 3. API 检索

```bash
GET /platform/knowledge/rag-search?q=DAG调度策略&source=project_docs&top_k=5
```

返回带 `source_id`、`chunk_id`、`score` 的结构化检索结果。

## 图片检索 (v5.1)

支持 CLIP/BGE-V 多模态 embedding：

- 上传文档中的图片自动提取和向量化
- 支持以图搜图
- 支持文本查询返回相关图片

## 引用语法

检索结果使用 `{{artifact:source_id:chunk_id}}` 语法引用，前端渲染为可点击的引用芯片。

```
@CodeGen 基于 {{artifact:doc-42:c-3}} 重写这段代码
```

## 下一步

- [RAG 混合检索详解](/zh/advanced/contextos)
- [知识库 API 参考](/zh/api/knowledge)
