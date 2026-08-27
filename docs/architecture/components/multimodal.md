# 多模态（视觉输入）架构设计

> Status: proposed
> Owner: backend maintainers
> Last reviewed: 2026-08-27
> 关联：[memory.md](memory.md)、ADR-0104（LLM 网关）、[newapi-rollout-plan](../../operations/newapi-rollout-plan.md)

## 1. 现状审计结论（2026-08-27，代码核实）

当前项目为纯文本链路，但并非零基础——状态是 **"UX 完整、通路为零"**：

| 层 | 现状 | 证据 |
|---|---|---|
| 前端 | 选图/粘贴/预览/分块上传完整；≤2MB 图片内联 data URL | frontend/hooks/useFileUpload.ts、ChatInput 粘贴处理 |
| 消息协议 | WS→后端全程把消息当纯文本；前端还把 base64 以代码块拼进正文污染 prompt | frontend/lib/outgoingMessageDraft.ts |
| 附件消费 | 图片被截成 180 字符前缀当文本拼接 | [agent_prompt_context.py](../../../app/services/agent_prompt_context.py) `build_attachment_context` |
| 模型适配 | messages 构造全部为 `content: str`；无 content 数组解析 | [adapter_manager.py](../../../app/services/adapter_manager.py)、model_adapter_service schema |
| token 预算 | 无图像计费项；混入正文的 base64 反而被当文本计数 | [token_budget.py](../../../app/services/token_budget.py) |
| 工具系统 | 有截图产出（browser_screenshot 返回 base64），但结果只被序列化为文本注入，无视觉回传 | [browser_tools.py](../../../app/services/tools/browser_tools.py) |
| 半成品基建 | 离线知识服务已有标准 `image_url` content 数组构造；OCR 仅存在于文档管道且默认关闭 | multimodal_embedding_client.py、document_pipeline extractors |

## 2. 网关层已验证可用（2026-08-27 实测）

经 new-api 网关 + Moonshot 渠道（type=25）+ `moonshot-v1-8k-vision-preview` 实测：

- 标准 OpenAI 多模态请求体（`content: [{image_url}, {text}]`，736KB base64 data URI）**网关原样透传成功**；
- 模型正确识别 AgentHub logo 内容与主色调，图像计费进入 usage（prompt=1049 tokens）；
- 结论：**网关无需任何改动**，多模态是纯应用侧改造。

## 3. 业界方案调研结论

| 来源 | 可借鉴的模式 |
|---|---|
| LangChain 标准内容块 / Deep Agents | 统一 content blocks 协议；工具可直接返回图片块作为下一轮模型可见内容；**大媒体存后端传引用而非内联**；上下文压缩按纯文本处理，旧轮次媒体块在摘要时丢弃 |
| langchain-go issue #11（视觉支持 RFC） | **向后兼容双轨**：保留 `content: str` 字段、新增可选 parts 数组；parts 为空回退字符串；不支持的供应商返回明确错误而非静默失败 |
| LangChain 论坛（image tool 模式） | 不支持视觉的模型的替代路径：结构化输出描述工具（子 LLM 把图转结构化文本） |

对本项目的映射：采用 OpenAI `content: str | list` 作为内部传输标准（vLLM/Ollama/OpenAI/Kimi 全兼容），Anthropic 走自有 blocks 由适配器转换；不做自创协议。

## 4. 目标设计：五步垂切

原则：最小改动面（不动数据库与会话持久化，attachments 元数据已在存储链路），每步独立可验证，默认降级安全。

```
M1 协议层     messages.content: str | list[part]        （adapter_manager ~4处 + model_adapter_service schema）
M2 附件管线   前端停发 base64 进正文；build_attachment_context 产出结构化 image part
M3 预算计费   图像固定 token 计费项 + fit_prompt 图像分支
M4 工具回传   browser_screenshot 结果可作为下一轮视觉输入（或结构化描述降级）
M5 门禁护栏   guardrails 图片卫生（类型/尺寸上限）+ 视觉 e2e 探针门禁 + 能力表链接
```

关键决策点：

- **路由约束**：只有 vision 能力模型（channel/model 元数据标注）才允许携带 image part；纯文本模型收到图片时返回明确错误并触发降级（提示用户该模型不支持看图，或走 OCR 结构化描述降级，OCR 复用 document_pipeline 的 tesseract 基建但默认关闭不变）。
- **预算硬上限**：单轮 ≤4 张图、内联总量 ≤6MB（复用现有 2MB/张 前端限制的上层聚合）、图像按固定 1024 tokens/张 计入预算（实测 moonshot-v1-8k 对 736KB logo 计 1049 prompt tokens，取整保守）。
- **历史压缩语义对齐 Deep Agents**：compaction/摘要发生时旧轮次图片块不进摘要正文，仅保留"用户曾发送 N 张图片"占位标记。
- **大小媒体走引用**：>2MB 已有分块上传落盘 artifact，prompt 中传 workspace 文件路径 + 少量文字说明（配合 file_read 类工具按需取用），避免巨型内联。

## 5. 安全与合规

- 图片 base64 进入 prompt 前过 guardrails 既有 PII/注入扫描的扩展（图片场景主要防提示注入图片—— Illusionist attack）：首版仅做类型白名单（png/jpg/webp/gif）+ 尺寸上限 + 可选 OCR 抽帧文本再扫描；
- 图片不写入任何日志/审计明文，审计事件仅记 hash 与尺寸。

## 6. 参考

- LangChain 标准内容块与多模态消息（docs.langchain.com）
- Deep Agents 多模态指南（媒体存后端传引用；压缩丢图语义）
- langchain-go Vision RFC（双轨向后兼容 + 供应商不支持时的显式错误）
- 本项目实测记录：2026-08-27 new-api 网关 Moonshot vision 透传（见上文 §2）