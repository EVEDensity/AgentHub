# Web Search 联网查询实现文档

> 本文档详细阐述 cc-haha 项目中 Web Search（联网查询）的实现原理、架构设计与核心代码逻辑。

---

## 一、概述

Web Search 允许 AI 模型在对话中通过内置工具调用进行联网搜索，获取实时信息。支持三种搜索模式：

| 提供商 | 类型 | 描述 |
|--------|------|------|
| **Anthropic** | 原生搜索 | 通过 Claude API 内置的 `web_search_20250305` 工具，无需额外 API Key |
| **Tavily** | 第三方搜索 | 需要配置 Tavily API Key，通过其 REST API 搜索 |
| **Brave** | 第三方搜索 | 需要配置 Brave Search API Key，通过其 REST API 搜索 |

**设计特点：**
- **智能降级**：原生搜索失败时自动降级到第三方搜索
- **多模式选择**：支持 `auto`/`anthropic`/`tavily`/`brave`/`disabled`
- **域名过滤**：支持 `allowed_domains`/`blocked_domains`
- **流式进度**：实时显示搜索进度更新

---

## 二、整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    Web Search 整体架构                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌─────────────┐     ┌───────────────────────┐                │
│   │   用户请求  │ ──→ │  WebSearchTool (call) │                │
│   └─────────────┘     └───────────┬───────────┘                │
│                                   │                            │
│           ┌───────────────────────┼───────────────────────┐   │
│           │                       │                       │   │
│           ▼                       ▼                       ▼   │
│  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐ │
│  │   Anthropic    │   │     Tavily      │   │     Brave      │ │
│  │  原生搜索      │   │   第三方搜索     │   │   第三方搜索     │ │
│  └────────┬────────┘   └────────┬────────┘   └────────┬────────┘ │
│           │                    │                    │          │
│           ▼                    ▼                    ▼          │
│  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐ │
│  │ @anthropic-ai  │   │  fetch Tavily   │   │  fetch Brave    │ │
│  │ sdk messages   │   │  API endpoint    │   │  API endpoint    │ │
│  └────────┬────────┘   └────────┬────────┘   └────────┬────────┘ │
│           │                    │                    │          │
│           └────────────────────┼────────────────────┘          │
│                                ▼                               │
│                   ┌───────────────────────┐                   │
│                   │   输出格式化返回      │                   │
│                   └───────────────────────┘                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 三、核心组件详解

### 1. WebSearchTool 主工具

**文件：** `src/tools/WebSearchTool/WebSearchTool.ts`

#### 输入输出 Schema

```typescript
const inputSchema = z.strictObject({
  query: z.string().min(2),
  allowed_domains: z.array(z.string()).optional(),
  blocked_domains: z.array(z.string()).optional(),
})

const outputSchema = z.object({
  query: z.string(),
  results: z.array(z.union([searchResultSchema, z.string()])),
  durationSeconds: z.number(),
})
```

#### 工具定义

```typescript
export const WebSearchTool = buildTool({
  name: WEB_SEARCH_TOOL_NAME,  // "web_search"
  searchHint: 'search the web for current information',
  maxResultSizeChars: 100_000,
  
  isEnabled() {
    return isWebSearchEnabledForModel(getMainLoopModel())
  },
  
  isConcurrencySafe() { return true },
  isReadOnly() { return true },
  
  async call(input, context, _canUseTool, _parentMessage, onProgress) {
    // 核心调用逻辑
  },
})
```

#### call 方法执行流程

```typescript
async call(input, context, _canUseTool, _parentMessage, onProgress) {
  const startTime = performance.now()
  const { query } = input
  const model = context.options.mainLoopModel
  const resolved = resolveWebSearchProvider(model)

  // 1. 检查是否禁用
  if (resolved.provider === 'disabled') {
    return { data: makeWebSearchUnavailableOutput(...) }
  }

  // 2. 使用第三方搜索
  if (resolved.provider === 'tavily' || resolved.provider === 'brave') {
    const apiKey = getApiKeyForProvider(resolved.provider, resolved.settings)
    const data = await searchWithExternalProvider(
      resolved.provider,
      input,
      apiKey,
      context.abortController.signal,
    )
    return { data }
  }

  // 3. 使用 Anthropic 原生搜索（带降级）
  try {
    return await callAnthropicNativeWebSearch(...)
  } catch (error) {
    if (!shouldFallbackFromNativeError(error)) throw error
    markAnthropicNativeUnsupported(model)
    
    const fallbackProvider = getFallbackProvider(resolved.settings)
    const apiKey = getApiKeyForProvider(fallbackProvider, resolved.settings)
    const data = await searchWithExternalProvider(...)
    return { data }
  }
}
```

---

### 2. 搜索后端管理

**文件：** `src/tools/WebSearchTool/backend.ts`

#### 类型定义

```typescript
export type WebSearchMode = 'auto' | 'anthropic' | 'tavily' | 'brave' | 'disabled'

export type ResolvedWebSearch = {
  provider: WebSearchProvider  // 'anthropic' | 'tavily' | 'brave' | 'disabled'
  settings: WebSearchSettings
}
```

#### 提供商解析

```typescript
export function resolveWebSearchProvider(
  model: string | undefined,
  settings: WebSearchSettings = getConfiguredWebSearchSettings(),
): ResolvedWebSearch {
  const mode = settings.mode ?? 'auto'

  // 显式模式优先
  if (mode === 'disabled') return { provider: 'disabled', settings }
  if (mode === 'tavily') return { 
    provider: settings.tavilyApiKey ? 'tavily' : 'disabled', 
    settings 
  }
  if (mode === 'brave') return { 
    provider: settings.braveApiKey ? 'brave' : 'disabled', 
    settings 
  }
  if (mode === 'anthropic') return {
    provider: canUseAnthropicNativeWebSearch(model) ? 'anthropic' : 'disabled',
    settings
  }

  // auto 模式优先级：anthropic → tavily → brave → disabled
  if (canUseAnthropicNativeWebSearch(model)) return { provider: 'anthropic', settings }
  if (settings.tavilyApiKey) return { provider: 'tavily', settings }
  if (settings.braveApiKey) return { provider: 'brave', settings }

  return { provider: 'disabled', settings }
}
```

---

### 3. Anthropic 原生搜索实现

```typescript
async function callAnthropicNativeWebSearch(
  input: Input,
  context: ToolUseContext,
  onProgress: ToolCallProgress<WebSearchProgress> | undefined,
  startTime: number,
) {
  const { query } = input
  const userMessage = createUserMessage({
    content: 'Perform a web search for the query: ' + query,
  })
  
  // 构造原生搜索工具 Schema
  const toolSchema = {
    type: 'web_search_20250305',
    name: 'web_search',
    allowed_domains: input.allowed_domains,
    blocked_domains: input.blocked_domains,
    max_uses: 8,  // 限制最多 8 次搜索
  }

  // 使用小模型加速（根据特性开关）
  const useHaiku = getFeatureValue_CACHED_MAY_BE_STALE('tengu_plum_vx3', false)
  
  // 流式调用
  const queryStream = queryModelWithStreaming({
    messages: [userMessage],
    systemPrompt: asSystemPrompt([...]),
    tools: [],
    extraToolSchemas: [toolSchema],
    querySource: 'web_search_tool',
    options: {
      model: useHaiku ? getSmallFastModel() : context.options.mainLoopModel,
    },
  })

  const allContentBlocks: BetaContentBlock[] = []
  const toolUseQueries = new Map<string, string>()
  let progressCounter = 0

  // 处理流式响应
  for await (const event of queryStream) {
    // 收集所有内容块
    if (event.type === 'assistant') {
      allContentBlocks.push(...event.message.content)
    }
    
    // 跟踪 tool_use 进度（用于 UI 更新）
    if (
      event.type === 'stream_event' &&
      event.event?.type === 'content_block_start'
    ) {
      const contentBlock = event.event.content_block
      if (contentBlock.type === 'server_tool_use') {
        currentToolUseId = contentBlock.id
        currentToolUseJson = ''
      }
    }
    
    // 提取查询内容用于进度显示
    if (currentToolUseId && event.type === 'stream_event') {
      // ... 解析 JSON delta，提取 query 字段
      onProgress({
        toolUseID: `search-progress-${progressCounter}`,
        data: { type: 'query_update', query: parsedQuery },
      })
    }
    
    // 搜索结果到达
    if (
      event.type === 'stream_event' &&
      event.event?.type === 'content_block_start'
    ) {
      const contentBlock = event.event.content_block
      if (contentBlock.type === 'web_search_tool_result') {
        onProgress({
          toolUseID: contentBlock.tool_use_id,
          data: {
            type: 'search_results_received',
            resultCount: Array.isArray(contentBlock.content) ? contentBlock.content.length : 0,
            query: actualQuery,
          },
        })
      }
    }
  }

  // 处理最终结果
  return makeOutputFromSearchResponse(allContentBlocks, query, durationSeconds)
}
```

---

### 4. 第三方搜索实现

#### Tavily 搜索

```typescript
async function searchWithTavily(
  input: Input,
  apiKey: string,
  signal: AbortSignal,
): Promise<ExternalSearchHit[]> {
  const response = await fetch('https://api.tavily.com/search', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      query: input.query,
      max_results: 8,
      search_depth: 'basic',
      include_answer: false,
      include_domains: input.allowed_domains,
      exclude_domains: input.blocked_domains,
    }),
    signal,
  })

  if (!response.ok) throw new Error(`Tavily search failed: ${response.status}`)

  const body = await response.json()
  return (body.results ?? [])
    .map(hit => normalizeHit(hit.title, hit.url))
    .filter(Boolean)
}
```

#### Brave 搜索

```typescript
async function searchWithBrave(
  input: Input,
  apiKey: string,
  signal: AbortSignal,
): Promise<ExternalSearchHit[]> {
  const url = new URL('https://api.search.brave.com/res/v1/web/search')
  url.searchParams.set('q', applyDomainFiltersToQuery(input))
  url.searchParams.set('count', '8')

  const response = await fetch(url, {
    headers: {
      Accept: 'application/json',
      'X-Subscription-Token': apiKey,
    },
    signal,
  })

  if (!response.ok) throw new Error(`Brave search failed: ${response.status}`)

  const body = await response.json()
  return (body.web?.results ?? [])
    .map(hit => normalizeHit(hit.title, hit.url))
    .filter(Boolean)
}

// Brave 不支持原生域名过滤，需在 query 中添加 site: 语法
function applyDomainFiltersToQuery(input: Input): string {
  const allowedClause = input.allowed_domains?.length
    ? `(${input.allowed_domains.map(domain => `site:${domain}`).join(' OR ')}) `
    : ''
  
  const blockedClause = input.blocked_domains?.length
    ? `${input.blocked_domains.map(domain => `-site:${domain}`).join(' ')} `
    : ''

  return `${allowedClause}${blockedClause}${input.query}`.trim()
}
```

---

## 四、配置系统

### 1. 配置结构

```typescript
export type WebSearchSettings = {
  mode?: WebSearchMode
  tavilyApiKey?: string
  braveApiKey?: string
}

// 在全局 SettingsJson 中
export type SettingsJson = {
  // ...
  webSearch?: WebSearchSettings
  // ...
}
```

### 2. 配置获取

```typescript
export function getConfiguredWebSearchSettings(
  settings: Pick<SettingsJson, 'webSearch'> = getSettings_DEPRECATED(),
): WebSearchSettings {
  const raw = settings.webSearch
  if (!raw || typeof raw !== 'object') return {}

  const modeCandidate = raw.mode ?? 'auto'
  
  return {
    mode: WEB_SEARCH_MODES.has(modeCandidate) ? modeCandidate : 'auto',
    tavilyApiKey: normalizeApiKey(raw.tavilyApiKey),
    braveApiKey: normalizeApiKey(raw.braveApiKey),
  }
}
```

---

## 五、降级与容错机制

### 1. 原生搜索降级判断

```typescript
export function shouldFallbackFromNativeError(error: unknown): boolean {
  const message = String(error instanceof Error ? error.message : error)
  return (
    // HTTP 错误码
    /\b(400|422)\b/.test(message) ||
    // 错误关键词匹配
    /web_search|server tool|tool schema|input_schema|extra input|unsupported/i.test(message)
  )
}

// 记录不支持原生搜索的模型（避免重复尝试）
const unsupportedNativeModels = new Set<string>()

export function markAnthropicNativeUnsupported(model: string | undefined): void {
  const key = normalizeModelKey(model)
  if (key) unsupportedNativeModels.add(key)
}
```

### 2. 降级流程

```
┌─────────────────┐
│   调用原生搜索    │
└────────┬────────┘
         │
    成功 / 失败
         │
    ┌────┴────┐
    ↓         ↓
  成功    ┌─────────────┐
          │ 检查是否降级 │
          └─────┬───────┘
                │
           ┌────┴────┐
           ↓         ↓
        ┌─────────────┐
        │ 获取 fallback │
        │  提供商      │
        └─────┬───────┘
              │
         ┌────┴────┐
         ↓         ↓
      ┌─────────────┐
      │  调用第三方   │
      │  搜索 API     │
      └─────┬───────┘
            │
         ┌──┴──┐
         ↓     ↓
      成功  失败
```

---

## 六、进度更新机制

### 1. 进度类型

```typescript
type WebSearchProgress =
  | {
      type: 'query_update'
      query: string
    }
  | {
      type: 'search_results_received'
      query: string
      resultCount: number
    }
```

### 2. 进度触发

```typescript
// 在搜索流程中调用 onProgress
onProgress?.({
  toolUseID: `${resolved.provider}-web-search`,
  data: {
    type: 'query_update',
    query: input.query,
  },
})

onProgress?.({
  toolUseID: `${resolved.provider}-web-search`,
  data: {
    type: 'search_results_received',
    resultCount: hits.length,
    query: input.query,
  },
})
```

---

## 七、结果格式化

### 1. 原生搜索响应解析

```typescript
function makeOutputFromSearchResponse(
  result: BetaContentBlock[],
  query: string,
  durationSeconds: number,
): Output {
  // 原生搜索返回块序列：
  // - text (可选)
  // - server_tool_use
  // - web_search_tool_result
  // - text + citation 块

  const results: (SearchResult | string)[] = []
  let textAcc = ''
  let inText = true

  for (const block of result) {
    if (block.type === 'server_tool_use') {
      if (inText) {
        inText = false
        if (textAcc.trim()) results.push(textAcc.trim())
        textAcc = ''
      }
      continue
    }

    if (block.type === 'web_search_tool_result') {
      if (!Array.isArray(block.content)) {
        results.push(`Web search error: ${block.content.error_code}`)
        continue
      }
      const hits = block.content.map(r => ({ title: r.title, url: r.url }))
      results.push({
        tool_use_id: block.tool_use_id,
        content: hits,
      })
    }

    if (block.type === 'text') {
      if (inText) textAcc += block.text
      else { inText = true; textAcc = block.text }
    }
  }

  if (textAcc) results.push(textAcc.trim())

  return { query, results, durationSeconds }
}
```

### 2. 第三方搜索输出

```typescript
function makeExternalSearchOutput(
  provider: 'tavily' | 'brave',
  query: string,
  hits: ExternalSearchHit[],
  durationSeconds: number,
): Output {
  const result: SearchResult = {
    tool_use_id: `${provider}-web-search`,
    content: hits,
  }

  return {
    query,
    results: [`Search provider: ${provider}`, result],
    durationSeconds,
  }
}
```

---

## 八、结果映射到工具调用块

```typescript
mapToolResultToToolResultBlockParam(output, toolUseID) {
  const { query, results } = output
  let formattedOutput = `Web search results for query: "${query}"\n\n`

  (results ?? []).forEach(result => {
    if (result == null) return

    if (typeof result === 'string') {
      // 文本摘要
      formattedOutput += result + '\n\n'
    } else {
      // 带链接的搜索结果
      if (result.content?.length) {
        formattedOutput += `Links: ${jsonStringify(result.content)}\n\n`
      } else {
        formattedOutput += 'No links found.\n\n'
      }
    }
  })

  formattedOutput += '\nREMINDER: You MUST include the sources above in your response using markdown hyperlinks.'

  return {
    tool_use_id: toolUseID,
    type: 'tool_result',
    content: formattedOutput.trim(),
  }
}
```

---

## 九、完整复刻指南

### 1. 依赖清单

```typescript
// package.json
{
  "dependencies": {
    "zod": "^4.3.6",  // 用于参数验证
    "@anthropic-ai/sdk": "^0.80.0"  // 原生搜索需要（可选）
  }
}
```

### 2. 最小可运行示例（只含第三方搜索）

```typescript
// standalone-web-search.ts
import { z } from 'zod'

type WebSearchMode = 'auto' | 'tavily' | 'brave' | 'disabled'
type WebSearchProvider = 'tavily' | 'brave' | 'disabled'

type WebSearchSettings = {
  mode?: WebSearchMode
  tavilyApiKey?: string
  braveApiKey?: string
}

type Input = {
  query: string
  allowed_domains?: string[]
  blocked_domains?: string[]
}

type SearchResult = {
  tool_use_id: string
  content: Array<{ title: string; url: string }>
}

type Output = {
  query: string
  results: Array<SearchResult | string>
  durationSeconds: number
}

// ========== 后端实现 ==========
const WEB_SEARCH_MODES = new Set<WebSearchMode>(['auto', 'tavily', 'brave', 'disabled'])

function normalizeApiKey(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  const trimmed = value.trim()
  return trimmed.length ? trimmed : undefined
}

function getConfiguredWebSearchSettings(settings: { webSearch?: WebSearchSettings } = {}): WebSearchSettings {
  const raw = settings.webSearch
  if (!raw || typeof raw !== 'object') return {}

  const modeCandidate = raw.mode ?? 'auto'
  return {
    mode: WEB_SEARCH_MODES.has(modeCandidate) ? modeCandidate : 'auto',
    tavilyApiKey: normalizeApiKey(raw.tavilyApiKey),
    braveApiKey: normalizeApiKey(raw.braveApiKey),
  }
}

function isLikelyClaudeModel(model: string | undefined): boolean {
  if (!model) return false
  return /(^|[/:._-])claude([/:._-]|$)/i.test(model)
}

function resolveWebSearchProvider(
  model: string | undefined,
  settings: WebSearchSettings = getConfiguredWebSearchSettings(),
): { provider: WebSearchProvider; settings: WebSearchSettings } {
  const mode = settings.mode ?? 'auto'

  if (mode === 'disabled') return { provider: 'disabled', settings }
  if (mode === 'tavily') return {
    provider: settings.tavilyApiKey ? 'tavily' : 'disabled',
    settings
  }
  if (mode === 'brave') return {
    provider: settings.braveApiKey ? 'brave' : 'disabled',
    settings
  }

  if (settings.tavilyApiKey) return { provider: 'tavily', settings }
  if (settings.braveApiKey) return { provider: 'brave', settings }

  return { provider: 'disabled', settings }
}

function makeWebSearchUnavailableOutput(query: string, durationSeconds: number, reason: string): Output {
  return {
    query,
    results: [reason],
    durationSeconds,
  }
}

function getApiKeyForProvider(
  provider: Exclude<WebSearchProvider, 'disabled'>,
  settings: WebSearchSettings,
): string | null {
  return provider === 'tavily'
    ? settings.tavilyApiKey ?? null
    : settings.braveApiKey ?? null
}

function normalizeHit(title: unknown, url: unknown): { title: string; url: string } | null {
  if (typeof title !== 'string' || typeof url !== 'string') return null
  return { title, url }
}

function applyDomainFiltersToQuery(input: Input): string {
  const allowedClause = input.allowed_domains?.length
    ? `(${input.allowed_domains.map(domain => `site:${domain}`).join(' OR ')}) `
    : ''
  
  const blockedClause = input.blocked_domains?.length
    ? `${input.blocked_domains.map(domain => `-site:${domain}`).join(' ')} `
    : ''

  return `${allowedClause}${blockedClause}${input.query}`.trim()
}

async function searchWithTavily(
  input: Input,
  apiKey: string,
  signal?: AbortSignal,
): Promise<Array<{ title: string; url: string }>> {
  const response = await fetch('https://api.tavily.com/search', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      query: input.query,
      max_results: 8,
      search_depth: 'basic',
      include_answer: false,
      include_domains: input.allowed_domains,
      exclude_domains: input.blocked_domains,
    }),
    signal,
  })

  if (!response.ok) throw new Error(`Tavily search failed: ${response.status}`)

  const body = await response.json() as { results?: Array<{ title?: unknown; url?: unknown }> }
  return (body.results ?? [])
    .map(hit => normalizeHit(hit.title, hit.url))
    .filter((hit): hit is { title: string; url: string } => hit != null)
}

async function searchWithBrave(
  input: Input,
  apiKey: string,
  signal?: AbortSignal,
): Promise<Array<{ title: string; url: string }>> {
  const url = new URL('https://api.search.brave.com/res/v1/web/search')
  url.searchParams.set('q', applyDomainFiltersToQuery(input))
  url.searchParams.set('count', '8')

  const response = await fetch(url, {
    headers: {
      Accept: 'application/json',
      'X-Subscription-Token': apiKey,
    },
    signal,
  })

  if (!response.ok) throw new Error(`Brave search failed: ${response.status}`)

  const body = await response.json() as { web?: { results?: Array<{ title?: unknown; url?: unknown }> } }
  return (body.web?.results ?? [])
    .map(hit => normalizeHit(hit.title, hit.url))
    .filter((hit): hit is { title: string; url: string } => hit != null)
}

function makeExternalSearchOutput(
  provider: 'tavily' | 'brave',
  query: string,
  hits: Array<{ title: string; url: string }>,
  durationSeconds: number,
): Output {
  const result: SearchResult = {
    tool_use_id: `${provider}-web-search`,
    content: hits,
  }

  return {
    query,
    results: [`Search provider: ${provider}`, result],
    durationSeconds,
  }
}

async function searchWithExternalProvider(
  provider: 'tavily' | 'brave',
  input: Input,
  apiKey: string,
  signal?: AbortSignal,
): Promise<Output> {
  const startTime = performance.now()
  const hits = provider === 'tavily'
    ? await searchWithTavily(input, apiKey, signal)
    : await searchWithBrave(input, apiKey, signal)
  const durationSeconds = (performance.now() - startTime) / 1000
  return makeExternalSearchOutput(provider, input.query, hits, durationSeconds)
}

// ========== 主搜索函数 ==========
export async function standaloneWebSearch(
  input: Input,
  settings: WebSearchSettings = {},
  options?: { model?: string; signal?: AbortSignal },
): Promise<Output> {
  const startTime = performance.now()
  const { query } = input
  const model = options?.model

  const resolved = resolveWebSearchProvider(model, settings)

  if (resolved.provider === 'disabled') {
    const durationSeconds = (performance.now() - startTime) / 1000
    return makeWebSearchUnavailableOutput(
      query,
      durationSeconds,
      'Web search is not configured. Set mode to "tavily" or "brave" and provide an API key.',
    )
  }

  if (resolved.provider === 'tavily' || resolved.provider === 'brave') {
    const apiKey = getApiKeyForProvider(resolved.provider, resolved.settings)
    if (!apiKey) {
      const durationSeconds = (performance.now() - startTime) / 1000
      return makeWebSearchUnavailableOutput(
        query,
        durationSeconds,
        `Web search provider ${resolved.provider} is selected but its API key is missing.`,
      )
    }
    return searchWithExternalProvider(
      resolved.provider,
      input,
      apiKey,
      options?.signal,
    )
  }

  const durationSeconds = (performance.now() - startTime) / 1000
  return makeWebSearchUnavailableOutput(query, durationSeconds, 'Unknown provider.')
}

// ========== 使用示例 ==========
/*
// 示例：Tavily 搜索
const result = await standaloneWebSearch(
  { query: '最新 TypeScript 版本' },
  { mode: 'tavily', tavilyApiKey: 'tvly-xxx' }
)
console.log(result)

// 示例：Brave 搜索带域名过滤
const result = await standaloneWebSearch(
  { 
    query: 'React 19 新特性',
    allowed_domains: ['react.dev', 'github.com']
  },
  { mode: 'brave', braveApiKey: 'BSAxkxxxxx' }
)
*/
```

### 3. 配置示例

```typescript
// config.ts
export const webSearchConfig = {
  mode: 'auto' as WebSearchMode,  // 或 'tavily' / 'brave' / 'disabled'
  tavilyApiKey: process.env.TAVILY_API_KEY || '',
  braveApiKey: process.env.BRAVE_API_KEY || '',
}

// .env
TAVILY_API_KEY=tvly-xxx
BRAVE_API_KEY=BSAxxxxx
```

### 4. 与 LLM 集成示例

```typescript
// integration-example.ts
import { standaloneWebSearch, type Input } from './standalone-web-search'

type LLMFunction = {
  name: string
  description: string
  parameters: any
}

// 定义工具供 LLM 使用
export const webSearchTool: LLMFunction = {
  name: 'web_search',
  description: 'Search the web for up-to-date information',
  parameters: {
    type: 'object',
    properties: {
      query: {
        type: 'string',
        description: 'Search query',
      },
      allowed_domains: {
        type: 'array',
        items: { type: 'string' },
        description: 'Only include results from these domains',
        optional: true,
      },
      blocked_domains: {
        type: 'array',
        items: { type: 'string' },
        description: 'Exclude results from these domains',
        optional: true,
      },
    },
    required: ['query'],
  },
}

// 执行工具调用
export async function executeWebSearch(input: Input, settings: any) {
  try {
    const result = await standaloneWebSearch(input, settings)
    
    // 格式化结果给 LLM
    let formatted = `Web search results for: "${result.query}"\n\n`
    
    for (const item of result.results) {
      if (typeof item === 'string') {
        formatted += item + '\n\n'
      } else {
        if (item.content?.length) {
          formatted += 'Results:\n'
          for (const hit of item.content) {
            formatted += `- [${hit.title}](${hit.url})\n`
          }
          formatted += '\n'
        }
      }
    }
    
    formatted += '\nIMPORTANT: Please include the sources in your response as markdown hyperlinks.'
    
    return formatted
  } catch (error) {
    return `Web search failed: ${error instanceof Error ? error.message : String(error)}`
  }
}
```

### 5. 避坑要点

| 问题 | 解决方案 |
|------|----------|
| CORS 错误（浏览器环境） | 使用代理服务器，或只在 Node.js 中使用 |
| API Key 暴露 | 不要在前端直接调用，通过后端代理 |
| Brave 域名过滤无效 | Brave 不支持原生域名过滤，需使用 `site:` 语法（已在 `applyDomainFiltersToQuery` 中实现） |
| 搜索超时 | 添加 `signal` 参数支持中断 |
| 结果过多 | 限制 `max_results` 为 8（默认） |

### 6. 测试验证

```typescript
// test-web-search.ts
async function testTavily() {
  const result = await standaloneWebSearch(
    { query: 'test' },
    { mode: 'tavily', tavilyApiKey: 'your-key-here' }
  )
  console.log('Tavily test:', result)
}

async function testBrave() {
  const result = await standaloneWebSearch(
    { query: 'test' },
    { mode: 'brave', braveApiKey: 'your-key-here' }
  )
  console.log('Brave test:', result)
}
```

---

## 十、相关文件索引

| 文件路径 | 描述 |
|----------|------|
| `src/tools/WebSearchTool/WebSearchTool.ts` | Web Search 工具主实现 |
| `src/tools/WebSearchTool/backend.ts` | 搜索提供商管理与第三方搜索实现 |
| `src/tools/WebSearchTool/prompt.ts` | 工具使用提示词 |
| `src/tools/WebSearchTool/UI.tsx` | UI 渲染组件 |
| `src/tools/WebSearchTool/backend.test.ts` | 后端逻辑测试 |
| `src/utils/settings/types.ts` | 设置类型定义 |
| `src/types/tools.ts` | WebSearchProgress 等共享类型 |

---

*最后更新：2026-06-03*
