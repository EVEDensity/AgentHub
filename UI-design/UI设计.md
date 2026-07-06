---

## AgentHub 设计系统提示词 · 完整版

### 一、全局令牌（CSS 变量，直接复制到 `:root`）

```css
:root {
  /* 暖调深色底色 — Claude Desktop 配色 */
  --bg: #1C1A18;
  --bg-sidebar: #151412;
  --surface: #24221F;
  --surface-elevated: #2B2925;
  --surface-overlay: #302E29;

  /* 文本层级 */
  --fg: #EBE8E3;
  --fg-secondary: #A8A49D;
  --fg-muted: #7A7670;
  --fg-dim: #4E4B46;

  /* 边框 */
  --border: #2E2C28;
  --border-subtle: #272522;
  --border-focus: #3D3A34;

  /* 琥珀橙主色 — 克制使用，每屏 ≤2 处 */
  --accent: #C4944A;
  --accent-hover: #D4A85C;
  --accent-muted: rgba(196, 148, 74, 0.10);

  /* 状态色 */
  --success: #6B9B6A;
  --warning: #C4A35A;
  --danger: #C4675A;
  --info: #7B9CB8;

  /* 字体 — 无衬线工程师字体 + 等宽代码字体 */
  --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', 'Cascadia Code', 'Fira Code', ui-monospace, monospace;

  /* 统一 4px 小圆角 — Codex 工程风 */
  --radius: 4px;
}
```

---

### 二、布局系统 — Codex 三栏 IDE 架构

```
┌──────────┬─────────────────────────┬──────────┐
│ 220px    │ 1fr (弹性)              │ 400px    │
│ 侧边栏    │ 主工作区                  │ 控制台    │
│ 深色固定   │ 核心操作画布               │ 终端面板   │
│ 图标导航   │ 拖拽/编排/可视化           │ 日志/命令  │
└──────────┴─────────────────────────┴──────────┘
```

**CSS Grid 实现：**
```css
.app {
  display: grid;
  grid-template-columns: var(--sidebar-w) 1fr var(--console-w);
  height: 100vh;
  width: 100vw;
}
```

**关键约束：**
- 侧边栏 220px（可拖拽至 160–320px），背景 `--bg-sidebar`，比主区更深一层
- 控制台 400px（可拖拽至 260–600px），与侧边栏同色
- 每个面板之间有 4px 拖拽手柄（resize handle），hover 时变琥珀色
- 主工作区 header 使用 `--bg-sidebar` 与工具栏区统一

---

### 三、核心组件模式

#### 3.1 侧边栏导航
```
- 分组标题: 10px / 600 / uppercase / 0.08em letter-spacing / --fg-dim
- 导航项: 13px / 450 / 7px 10px padding / 4px radius
  - 默认: --fg-muted 文字
  - hover: --surface 背景 + --fg-secondary 文字
  - active: --accent-muted 背景 + --accent 文字 + 510 weight
- 徽章: --accent-muted 背景 / --accent 文字 / --font-mono / 10px
```

#### 3.2 控制台/终端面板
```
- 头部: tab 切换 + 操作按钮（清空/折叠）
  - tab: 11px / 510 / 4px 12px padding / --fg-dim → active 时 --accent
- 内容区: --font-mono / 11px / 1.7 line-height / 留白舒适
- 日志条目: 时间戳(fg-dim) + 标签(彩色badge) + 消息
- 底部命令输入行: $ 琥珀色提示符 + 透明输入框
- 支持指令: step/next/reset/status/help/search/logs/metrics/alerts
```

#### 3.3 工具栏按钮
```
- 默认: 透明背景 / 1px --border / --fg-muted / 11px / 5px 12px
- hover: --surface 背景 / --fg-secondary / --border-focus
- 主操作(.primary): --accent 背景 / #1C1A18 文字（暗底亮字反色）
```

#### 3.4 指标卡片
```
- 背景: --surface / 1px --border-subtle 边框
- 标签: 10px / 600 / uppercase / 0.08em / --fg-dim
- 数值: --font-mono / 24px / 600 / -0.02em tracking
- 趋势: --font-mono / 10px / 彩色(up=success, warn=warning, down=danger)
```

#### 3.5 表单控件
```
- 输入框: --surface 背景 / 1px --border / --font-mono / focus→--accent
- 开关: 36×20px / 圆角10px / off=--border / on=--accent
- 模型标签(chip): --font-mono / 1px --border / active→--accent边框+浅底
- 配置分区: 14px标题 / --border-subtle 底部分割线
```

#### 3.6 Toast 通知
```
- fixed / top:16px / left:50% / translateX(-50%)
- --surface-elevated 背景 / 1px --border
- opacity 0→1 过渡 / 2s 自动消失
- 用于: 保存成功、任务提交、状态变更
```

---

### 四、交互规范

| 交互           | 实现方式                                                     |
| -------------- | ------------------------------------------------------------ |
| **面板拖拽**   | mousedown→mousemove→mouseup，范围限制260-600px（控制台）/ 160-320px（侧边栏） |
| **控制台折叠** | display:none/flex 切换，按钮文字 ⟩/⟨ 交替                    |
| **键盘快捷键** | 全局监听，排除INPUT/TEXTAREA/editable，←→步进、Ctrl+S保存、1-5导航 |
| **Toast**      | 2s自动消失，clearTimeout防重叠                               |
| **自动刷新**   | setInterval 15s，模拟实时事件流入控制台                      |
| **结果展开**   | click切换.expanded类，display:none/block切换详情区           |

---

### 五、文案与数据规范

```
✓ 所有文案使用真实产品术语: Router/Planner/Executor/Critic/Summarizer/Search
✓ 日志消息包含具体数据: "1,248行" "耗时1.2s" "Token 3,241" 而非 "执行成功"
✓ 来源引用使用真实路径格式: financial/q3_2025_report.pdf
✓ 状态使用中英双语: "观察 OBSERVE" 方便国际化
✗ 禁止: "Feature One/Two/Three"、lorem ipsum、虚构的"10× faster"
✗ 禁止: emoji 功能图标（✨🚀🎯）、渐变卡片、大圆角、玻璃拟态
```

---

### 六、避坑清单

```
禁止:
- 大面积渐变背景、玻璃拟态、大圆角卡片(>4px)
- 炫酷动效、悬浮缩放、装饰性插画
- 冷色科技蓝调(indigo #6366f1)、高饱和色彩
- 仪表盘堆砌式卡片布局
- emoji作为功能图标
- 左右彩色边框的圆角信息卡片
- 虚构指标("99.9% uptime" 无来源)
- Inter/Roboto作为展示字体

必须:
- 功能优先、极简留白、暖灰哑光深色
- 琥珀橙专属高亮、暖调细线条分割
- 状态清晰、数据可视化克制
- 终端质感、工程化专业属性
- 4px统一圆角、细线条、哑光磨砂
- 一屏不超过2处accent使用
- 等宽字体用于代码/日志/数字
```

---

### 七、创新点（本次优化新增）

1. **步进式状态机调试** — ReAct 页面支持 ▶步进 / ↺重置 / ←→键盘 / 命令行 `step` 指令
2. **知识库多选芯片** — RAG 页面支持按知识库过滤检索范围
3. **时间范围选择器** — 监控页面支持 1h/24h/7d/30d 切换
4. **配置预设卡片** — 设置页面支持一键切换生产/预发布/本地开发三套配置
5. **全局键盘导航** — 首页 1-5 数字键直达各子页面、Ctrl+S 保存配置
6. **实时事件流** — 监控控制台 15s 自动注入心跳/指标事件
7. **可拖拽面板** — 所有三栏页面侧边栏和控制台宽度可拖拽调整
8. **折叠控制台** — 右侧面板可完全折叠以扩展工作区视野
9. **命令行终端** — 每个页面控制台底部均有 `$` 提示符，支持交互指令
10. **两步确认危险操作** — 设置页"重置所有"需连续两次 confirm

---

这套提示词可直接用作后续 Claude 对话的 system prompt 前缀，或作为设计系统的 reference 文档。每次迭代只需在对应模块追加新规则即可。