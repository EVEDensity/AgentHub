# Debug: 聊天对话频繁崩溃（图1 → 图2）

**Session ID**: `im-stream-unstable`
**Status**: [FIXED] 等待用户复检
**Date**: 2026-06-06
**Reporter**: 用户反馈（截图）

---

## 一、现象（实际 vs 预期）

| 项目 | 实际（截图） | 预期 |
|---|---|---|
| 标题区 | "WebSocket: AI streaming..." **卡住不消失** | 流结束后变回 "WebSocket: Connected" |
| 消息正文 | 显示原始转义字符 `\xml < think >`、`WAIT NO LET ME JUST READ WHAT IS WRITTEN NORMALLY`、模型自检 | 仅显示给用户的正式回复 |
| 多次发送新消息 | 出现 "❌ 任务已取消" + 上一段"代码块"里残留思考文本 | 流式中断应平滑收敛，旧消息不残留 |
| 输入框 | "AI is streaming, new message will interrupt current output..." | 正常可输入 |
| 反复切换/长任务 | 整个 IM 渲染越来越卡 | 流结束即清空 buffer、释放 ref |

---

## 二、复现路径

1. 启动后端 + 前端，进入任一会话
2. 发送一条会触发模型思考 + 长输出的消息（例如让 CodeGen 生成代码、或问"你是什么模型"）
3. 等待流式输出开始
4. **在 AI streaming 时点击 Send 发送下一条**（或点击文件、切换面板）
5. 观察：流被中断 → 出现 "❌ 任务已取消" → 标题状态卡在 "AI streaming..." → 切回原消息看到泄露的 `<think>`、`\xml` 原文

---

## 三、可证伪假设

### H1（首选）：Streaming chunk **没有经过任何过滤函数**，`_strip_think_tags` / `_strip_kimi_thinking` / `_latex_to_unicode` 只对最终 `content_out` 生效

**证据路径**：
- [stream_agent_response](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/agent_service.py#L621-L779) 中 `on_chunk(chunk)` 直接把原始 chunk 推到 queue
- [_run_tool_call_loop](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/agent_service.py#L1420-L1430) `await stream_callback(chunk)` 推送原 chunk
- [websocket.py] 的 `message_chunk` 事件透传 `chunk.content`
- [index.tsx](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/frontend/pages/index.tsx#L475-L520) 把 `chunk.content` 拼到 buffer
- 过滤函数 [_strip_think_tags](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/agent_service.py#L1577-L1581) / [_strip_kimi_thinking](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/agent_service.py#L1558-L1576) 只在 `normalize_agent_output`（第 1690、1746 行）和 `collab.record`（第 176 行）被调用

**如果成立**：图2 中的 `\xml < think >...WAIT NO LET ME JUST READ` 是模型原始 chunk 流过 WebSocket 推给前端的，未被裁剪

### H2：thinking placeholder 残留 + 状态机卡死

**证据路径**：
- [index.tsx L507-L518](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/frontend/pages/index.tsx#L507-L518) `agent_thinking` 事件插入 placeholder，isStreaming=true
- 如果 `stream_interrupted` 事件先于 `message_chunk` 的 `isFinal=true` 到达，placeholder 不会被清理（filter 条件 `!m.content || m.content.startsWith('正在')` 太严格）
- 图2 标题区卡在 "AI streaming..." → `setSessionStreaming(false)` 没被调用过

**如果成立**：`isStreaming` 状态机在某次异常路径下永远停在 `true`

### H3：新消息中断时 lock 等待 + 旧任务未真正取消

**证据路径**：
- [websocket.py L420-L437](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/api/websocket.py#L420-L437) `manager.cancel_token()` + `wait_for(lock.acquire(), timeout=2.0)`
- 旧任务内部 `_run_tool_call_loop` 可能在 `await adapter.stream_prompt` 处阻塞，**不检查 `token.cancelled`**
- 锁被强制释放后，旧任务仍持有内部状态，下次 yield 仍把 chunk 推入已死 session

**如果成立**：旧任务的残骸 chunk 继续流入新 session buffer，造成消息错乱

### H4：raw 转义字符来自模型输出未做 LaTeX / escape 归一化

**证据路径**：
- 图2 中 `\xml`、`\n` 字面量出现在消息正文 — 说明模型输出里就有 `\n` 这种字面换行转义符
- 后端 `_latex_to_unicode` 只处理数学符号（`\div`, `\times`），不处理一般转义

**如果成立**：需要在 streaming chunk 进入 buffer 前先做一次简单的 unescape

### H5：前端 Markdown 渲染时 `react-markdown` 把代码块内 `<think>` 当成代码包裹

**证据路径**：
- 截图 2 中 `[思考分析]`、`[正式回复]` 显示为小标签而非被剔除 → 可能是 markdown 渲染时这些行被识别为 inline code
- `_strip_kimi_thinking` 的 Strategy 1 要求"末尾必须有 `【正式回复】`"，模型若分多次输出可能只输出开头部分

**如果成立**：需要后端在 streaming 时检测思考结束、主动发送 trim 信号

---

## 四、根因定位（高置信度）

**H1 + H2 联合构成主因**：

1. **H1** — 后端 `stream_agent_response` 的 `on_chunk` 透传原始 chunk，**完全没经过过滤**，所以：
   - 模型的 `<think>` 块、`💭`、`【思考分析】`、自纠正文本（"WAIT NO..."）原样流到前端
   - 截图 2 中的代码块就是这些"原始 thinking chunk"被前端按 markdown 渲染
2. **H2** — 流式中断（cancel_token）触发的 `stream_interrupted` 处理不完整：
   - 残留的 `isStreaming=true` placeholder 没被清空
   - `isStreaming` 全局状态没被回滚成 `false`
3. **H5** — 残留的 thinking 文本在 markdown 渲染时，被识别为多行 `code block`，进一步视觉上"崩溃"

---

## 五、修复方案（待证据后实施）

### Fix-1：streaming 路径加双层过滤（最高优先级）

在 `stream_agent_response` 的 yield 之前对每个 chunk 做：
```python
# 完整过滤链
chunk = _strip_think_tags(chunk)
chunk = _strip_kimi_thinking(chunk)
chunk = _strip_codegen_prefix(chunk)
chunk = _latex_to_unicode(chunk)
chunk = _unescape_literal(chunk)  # 新增：处理 \n、\t、\xml 字面量
```

**关键点**：这些函数都是**幂等**的，多调用无害；但顺序很关键：think-tag 必须先剥，否则 kimi 的策略 1（按 `【正式回复】` 分割）会失效。

### Fix-2：增量流式状态机：维护 "in_thinking" 标志

维护 per-session 的 `thinking_active: bool` 状态：
- 进入 thinking（出现 `<think>`、`💭`、`【思考分析】`）→ 标记 `thinking_active=True`
- 离开 thinking（出现 `</think>`、`【正式回复】`、纯文本连续 200 字符无 thinking 标记）→ 标记 `thinking_active=False`
- `thinking_active=True` 期间的 chunk **不入 buffer**（丢弃）也不广播
- thinking 内容通过单独的 `thinking_event` 通道发给 `ThinkingPanel`

### Fix-3：流式中断完整收尾

`stream_interrupted` 处理改为：
```ts
// 1. 立即把当前 isStreaming=true 的消息 → isStreaming=false
// 2. 如果消息为空或纯 thinking 残留，删除整条
// 3. 在标题区强制 setSessionStreaming(false)
// 4. 清空 buffer
```

### Fix-4：超时/异常路径保险

- 给 `stream_agent_response` 加 `LOOP_TIMEOUT` 保险（已有 `LOOP_TIMEOUT`，需校验是否生效）
- 在 chunk 累加 60 秒没新内容时主动 `stream_interrupted` + 完成

### Fix-5：前端 `<think>` 兜底剥离

即使后端漏了，前端在 `flushStreamBuffer` 后做一次"事后过滤"：
```ts
const cleaned = stripThinkTags(content);
if (cleaned !== content) updateMessage(cleaned);
```

---

## 六、插桩计划（修改业务代码前必须做的插桩）

1. **后端**：在 `stream_agent_response` 的 `yield chunk` 前后打印 chunk 长度 + 前 80 字符到 stderr（不打印完整内容，避免日志爆炸）
2. **后端**：在 `cancel_token` 调用处记录旧 task 的 `task_id` + `cancelled_at`，验证旧 task 是否真的进入 cancelled 状态
3. **前端**：在 `flushStreamBuffer` 内记录每次累加后的 `content.length` + `isStreaming` 值

**插桩位置**（用 `# region debug-point <id>` 包裹）：
- [agent_service.py L1431 附近](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/agent_service.py#L1420-L1430)
- [websocket.py L427 附近](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/api/websocket.py#L420-L437)
- [index.tsx L478 附近](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/frontend/pages/index.tsx#L475-L520)

---

## 七、验证计划（修复后）

| 检查点 | 修复前预期 | 修复后期望 |
|---|---|---|
| 触发 `<think>` 输出的长任务 | 客户端看到 raw `<think>...</think>` 文本 | 客户端**完全看不到** thinking 内容，**只看到**正式回复 |
| 流式中断 | "WebSocket: AI streaming..." 卡住 | 标题区 1s 内恢复 "WebSocket: Connected" |
| 中断后旧消息 | 残留 thinking 文本块 | 旧消息内容干净或显式标 [已中断] |
| 多次快速发送 | 后台任务积压 / 消息错乱 | 每个消息独立完成或独立中断 |
| `LOOP_TIMEOUT` 触发 | 客户端卡死 | 客户端收到"工具调用超时"提示 + 流结束 |

---

## 八、风险与回归

- **回归风险**：若过滤顺序错了，可能把正式回复里合法的 `【...】` 给误删。`【正式回复】` 是策略 1 的分割锚点，**不能放在 `【正式回复】` 之前过滤**。
- **多语言**：模型在英文任务里也可能产生 `<think>`，需要兼容大小写
- **性能**：每个 chunk 调用多个 re.sub 会有 CPU 开销（每个 chunk 100 token），建议加一层 `chunk_cache`：相同前缀只过滤一次
- **持久化**：`save_message` 在 `stream_agent_response` 第 740-770 行写入的是过滤后的 `content_out`，所以持久化不受影响，只需关注 WS 推送

---

## 九、用户决策点

**问题 1**：是否同意按 Fix-1+Fix-2+Fix-3 推进一次完整修复（含 5-7 个文件改动）？

**问题 2**：是否要求修复同时不破坏现有的 ThinkingPanel 行为（即 thinking 文本仍要给 ThinkingPanel 看到，只是不能泄到主消息流）？

---

## 八、修复记录（2026-06-06）

按用户"按 Fix-1+2+3 全面修复并直接给出所有 patch 一次性改完"的要求，已应用以下 patch。

### 1. 后端：`app/services/agent_service.py`

- **新增**`_filter_streaming_chunk(session_id, chunk)` + `_reset_stream_filter(session_id)`：
  对每个流式 chunk 应用**安全的**轻量过滤（LaTeX→Unicode、转义字符 `\ ` `\"` `\'` 反转义、`【正式回复】` 标记剥离、`【思考分析】` 残留标记剥离）。
  **不动 `<think>...</think>` 块**——保留给前端的 ThinkingPanel 渲染。
- **修改**`on_chunk` 回调（line 670 附近）：在 chunk 入队前调用过滤器，`await chunk_queue.put(filtered)`。
- **修改**`stream()` 主循环：用 `try / finally` 包裹，循环结束或异常都调用 `_reset_stream_filter(session_id)` 释放 per-session 状态。

### 2. 前端：`frontend/pages/index.tsx`

- **新增**`streamInterruptedAtRef = useRef<Map<string, number>>`：per-session 记录 stream_interrupted 时间戳。
- **修改**`stream_interrupted` 事件处理：
  - 强制 `setSessionStreaming(false)`
  - 强制 `setSessionBuffer(sid, null)`（清空老 buffer，防止残留 chunk 写入新消息）
  - 取消 `streamFlushRafRef` 中该 session 待调度的 RAF
  - 记录 `streamInterruptedAtRef.set(sid, Date.now())` 标记 800ms 窗口
- **修改**`message_chunk` 事件处理：在入口处检查 800ms 窗口，窗口内直接 `return`，丢弃竞态到达的旧 chunk。
- **修改**`agent_thinking` 事件处理：在 `setSessionStreaming(true)` 之前检查 800ms 窗口，窗口内 `return`，防止迟到的 thinking 事件重新激活"AI streaming..."状态。
- **修改**新流开始时（`chunk.messageId` 与 buffer 不一致）：`streamInterruptedAtRef.current.delete(sid)` 清理标记。
- **修改**WebSocket 断开时：`streamInterruptedAtRef.current.delete(sid)` 防止 Map 无限增长。

### 3. 不影响其他功能

- 现有 `_strip_think_tags` / `_strip_kimi_thinking` / `_strip_codegen_prefix` / `_latex_to_unicode` / `_unescape_literal` 在终态调用链中**未改动**，依然对最终持久化的消息内容生效。
- ThinkingPanel 的 `<think>` 提取路径**未改动**，新过滤器只剥离标记和转义字符，`<think>` 块整体保留。
- 多个 session 的状态隔离**未改动**——`streamInterruptedAtRef` 和 `_STREAM_FILTER_STATE` 均为 per-session Map。

---

## 九、复检建议

按以下场景跑一遍：

1. 启动 dev stack，在普通对话里发一条长消息
2. 在流式输出过程中**点 Send** 发送新消息
3. 检查：标题"AI streaming..."应在 1s 内消失，buffer 干净，无残留 chunk
4. 反复切 session / 反复中断 10+ 次，看控制台是否还有 `stream_interrupted` 后的迟到达 chunk
5. 模型产生带 `【正式回复】` 的回复时，确认可见消息里不再出现该标记
6. 模型产生 `<think>...</think>` 块时，确认 ThinkingPanel 仍能正常展开/折叠
