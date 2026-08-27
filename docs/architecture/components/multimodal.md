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

## 6. 工具生态评估与解耦实现（2026-08-27）

### 6.1 现有工具支持矩阵（27 个内置工具逐项核实）

| 能力 | 工具 | 结论 |
|---|---|---|
| 图像输出 | browser_screenshot | **唯一**能产出图像（screenshot_base64），但结果被 ResultStorage 10k 字符预算截断、按纯文本注入，无视觉消费者 |
| 图像输入 | （无） | 全部 27 个工具入参 schema 不接受 image_url/base64；file_read 遇二进制显式报错；web_search 仅 title/url/snippet 三字段；memory 只收 markdown |
| 半成品机制 | pluggy `register_tools()` | hookspec 已声明但**生产代码从未消费**——插件动态注册工具是断头路（本次已接通，见下） |
| 双轨并行 | MCP 工具 | 经 StatelessMCPToolAdapter 包装进 Harness FunctionTool，不进 ToolRegistry |

### 6.2 解耦架构：`app/services/tools/multimodal/`（已落地）

```
multimodal/
├── content_parts.py   内容分片模型：text_part/image_url_part 构造器、
│                      MIME 白名单（png/jpeg/webp/gif）、8MB URI 上限、
│                      单轮 ≤4 张 ≤6MB assert、固定 token 计费常量
├── capability.py      视觉能力注册表：默认 pattern 规则 + in-code 注册 +
│                      AGENTHUB_VISION_MODELS env 覆盖；fail-closed
│                      （未知模型一律视为纯文本，报错含降级指引）
├── tools.py           具体工具 handler：image_describe（视觉子模型出
│                      结构化文字描述——文本主模型 TODAY 可用的降级路径）
└── plugin.py          ModalityToolPlugin 插件基类 + 内置 MultimodalityPlugin
```

配套通用件：
* [plugin_tools.py](../../../app/services/tools/plugin_tools.py) —— **接通 register_tools() 断点**的桥：任何插件（内置/entry-point/PLUGINS_PATH）声明的工具字典 → ToolDefinition → tool_registry；handler 支持可调用与点分导入路径双形态。
* [tools/__init__.py](../../../app/services/tools/__init__.py) `register_modality_tools()` + main.py 启动调用。

解耦保证：
1. **增删不影响核心**——删除 multimodal 包仅损失模态工具，definitions/adapter/registry 零改动；
2. **新模态即插即用**——音频/视频插件只需继承 `ModalityToolPlugin` 并覆写 `tool_definitions()`；
3. **能力注册开放**——第三方经 `register_vision_model` 或 env 声明自家视觉模型；
4. **测试**：tests/services/test_multimodal_tools.py 覆盖内容校验、能力注册表、桥接端到端（插件→registry）、handler 成功与错误路径，11 用例全绿。

### 6.3 GitHub 趋势项目调研结论

| 项目 | 与本方案的关系 |
|---|---|
| LangChain/LangGraph 标准内容块（116k★） | 多模态协议事实标准，本项目 wire format 直接对齐其 content blocks |
| OpenAI Agents SDK（26k★） | guardrails/tracing 作为一等公民的设计验证了 MM-5 把图片卫生放护栏层的做法 |
| AutoGen / CrewAI / Smolagents | 无统一多模态工具层——各自在 message 层处理；本项目的"模态工具独立成包"是差异化超集 |
| Deep Agents（LangChain 官方） | 大媒体"存后端传引用 + 摘要丢图"语义为本 M3/M2 决策背书 |
| langchain-go Vision RFC #11 | `content: str \| list` 双轨向后兼容的模式来源 |
| HuggingFace transformers/TIMM 生态 | 本地视觉模型适配路线备选（vLLM/Ollama 已覆盖主流 VL 开源模型，暂不需要直连） |

## 7. 成功标准与验收度量

### 功能正确性
- SC-1 图片可达性：用户上传 → 前端不发 base64 进正文 → image part 抵达模型请求体（e2e 探针绿）。
- SC-2 文本模型零回归：携图发给纯文本模型返回带降级指引的显式错误（不是 500）；全量既有套件通过。
- SC-3 结构化描述质量：image_describe 在标注集（≥20 张：截图/照片/表格图/图表）上描述命中关键对象 ≥85%。
- SC-4 截图闭环：browser_screenshot → image_describe（或直接回传）→ 模型答对页面要素 ≥80% 样本。

### 性能与预算
- SC-5 预算可信度：usage.prompt_tokens 实测 vs 本地图像计费常数偏差记录；单轮超限拒绝率 100%。
- SC-6 时延：image_describe p95 <8s（网关+视觉模型链路）；多图单轮首字节 <3s。

### 安全与治理
- SC-7 卫生拦截率：非法 MIME/超大文件 100% 在 content_parts 层拒绝并留审计 hash（不含明文）。
- SC-8 门禁在 CI：多模态 e2e 探针（mock 上游离线版 + 可选真实渠道）作为可选 gate；文档能力行链接实现与测试。

### 生态健康
- SC-9 解耦验证：删除 multimodal 包后核心启动与全套件仍绿（负向测试纳入 R4-4 质量门禁设计）。
- SC-10 第三方接入工时：新模态插件从空文件到注册进 registry ≤50 行代码（以 audio 为样本复评一次）。

## 8. 参考

- LangChain 标准内容块与多模态消息（docs.langchain.com）
- Deep Agents 多模态指南（媒体存后端传引用；压缩丢图语义）
- langchain-go Vision RFC（双轨向后兼容 + 供应商不支持时的显式错误）
- 本项目实测记录：2026-08-27 new-api 网关 Moonshot vision 透传（见上文 §2）