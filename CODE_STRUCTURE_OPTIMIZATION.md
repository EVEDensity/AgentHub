# AgentHub 代码结构优化文档

## 一、优化概述

本次重构旨在提升项目的可维护性和扩展性，按照**功能职责、业务领域和技术分层**原则对代码进行了系统性重组。

## 二、优化前结构

```
AgentHub/
├── app/
│   ├── api/
│   │   ├── admin.py
│   │   ├── agent.py
│   │   ├── auth.py
│   │   ├── chat.py
│   │   └── ...
│   ├── services/
│   │   ├── auth_service.py
│   │   ├── agent_service.py
│   │   └── ...
│   ├── db/
│   ├── schemas/
│   └── config.py
└── frontend/
    ├── components/
    │   ├── AgentCanvas.tsx
    │   ├── AgentFlowCanvas.tsx
    │   ├── DiffBubble.tsx
    │   ├── TokenUsageHeatmap.tsx
    │   └── ...
    ├── pages/
    ├── types/
    └── styles/
```

## 三、优化后结构

```
AgentHub/
├── app/
│   ├── api/
│   │   ├── v1/
│   │   │   ├── admin/          # 管理后台 API
│   │   │   ├── agent/          # Agent 相关 API
│   │   │   ├── auth/           # 认证 API
│   │   │   └── chat/           # 聊天 API
│   │   ├── websocket.py
│   │   └── routes.py
│   ├── services/
│   │   ├── auth/               # 认证服务
│   │   │   └── service.py
│   │   ├── agent/              # Agent 服务
│   │   │   └── service.py
│   │   ├── chat/               # 聊天服务
│   │   │   └── service.py
│   │   ├── workflow/           # 工作流服务
│   │   │   └── service.py
│   │   ├── auth_service.py     # 向后兼容层
│   │   └── ...
│   ├── db/
│   ├── schemas/
│   └── config.py
└── frontend/
    ├── components/
    │   ├── chat/               # 聊天相关组件
    │   │   ├── DiffBubble.tsx
    │   │   └── FidelityScore.tsx
    │   ├── flow/               # 工作流画布组件
    │   │   ├── AgentCanvas.tsx
    │   │   └── AgentFlowCanvas.tsx
    │   ├── git/                # Git 操作组件
    │   │   ├── GitOperation.tsx
    │   │   └── GeneratedFilesPanel.tsx
    │   ├── heatmap/            # 热力图组件
    │   │   └── TokenUsageHeatmap.tsx
    │   ├── shared/             # 共享通用组件
    │   │   └── PreviewSidebar.tsx
    │   └── ...
    ├── pages/
    ├── types/
    ├── utils/                  # 工具函数（新增）
    └── styles/
```

## 四、关键文件迁移路径

### 前端组件迁移

| 原路径 | 新路径 | 说明 |
|--------|--------|------|
| `components/DiffBubble.tsx` | `components/chat/DiffBubble.tsx` | 聊天气泡组件 |
| `components/FidelityScore.tsx` | `components/chat/FidelityScore.tsx` | 保真度评分组件 |
| `components/AgentCanvas.tsx` | `components/flow/AgentCanvas.tsx` | Agent 画布组件 |
| `components/AgentFlowCanvas.tsx` | `components/flow/AgentFlowCanvas.tsx` | 工作流画布组件 |
| `components/GitOperation.tsx` | `components/git/GitOperation.tsx` | Git 操作组件 |
| `components/GeneratedFilesPanel.tsx` | `components/git/GeneratedFilesPanel.tsx` | 生成文件面板 |
| `components/TokenUsageHeatmap.tsx` | `components/heatmap/TokenUsageHeatmap.tsx` | Token 热力图组件 |
| `components/PreviewSidebar.tsx` | `components/shared/PreviewSidebar.tsx` | 预览侧边栏组件 |

### 后端服务迁移

| 原路径 | 新路径 | 说明 |
|--------|--------|------|
| `services/auth_service.py` | `services/auth/service.py` | 认证服务（重构为类） |
| `services/auth_service.py` | `services/auth_service.py` | 向后兼容层（保留） |
| `services/agent_service.py` | `services/agent/service.py` | Agent 服务（重构为类） |

## 五、导入路径调整

### 前端页面更新

**index.tsx**:
```typescript
// 优化前
import DiffBubble from '../components/DiffBubble';
import GeneratedFilesPanel from '../components/GeneratedFilesPanel';

// 优化后
import DiffBubble from '../components/chat/DiffBubble';
import GeneratedFilesPanel from '../components/git/GeneratedFilesPanel';
```

**admin.tsx**:
```typescript
// 优化前
import TokenUsageHeatmap from '../components/TokenUsageHeatmap';
import AgentFlowCanvas from '../components/AgentFlowCanvas';

// 优化后
import TokenUsageHeatmap from '../components/heatmap/TokenUsageHeatmap';
import AgentFlowCanvas from '../components/flow/AgentFlowCanvas';
```

**canvas.tsx**:
```typescript
// 优化前
const AgentCanvas = dynamic(() => import('../components/AgentCanvas'), { ... });

// 优化后
const AgentCanvas = dynamic(() => import('../components/flow/AgentCanvas'), { ... });
```

**GeneratedFilesPanel.tsx**:
```typescript
// 优化前
import DiffBubble from './DiffBubble';
import type { GeneratedData } from '../types';

// 优化后
import DiffBubble from '../chat/DiffBubble';
import type { GeneratedData } from '../../types';
```

## 六、优化收益

### 1. 可维护性提升
- 组件按功能领域分组，便于定位和维护
- 服务层重构为类结构，提高代码复用性
- 清晰的目录结构降低认知负担

### 2. 扩展性增强
- 模块化设计便于新增功能模块
- 服务层标准化接口便于扩展新服务
- 预留 `utils/` 目录用于存放通用工具函数

### 3. 向后兼容性
- 保留 `auth_service.py` 作为转发层
- 原有导入路径继续有效，无需修改调用方代码

### 4. 团队协作优化
- 明确的模块边界减少代码冲突
- 新开发者更容易理解项目结构

## 七、验证结果

- ✅ 前端构建成功 (`npm run build`)
- ✅ 后端导入验证通过
- ✅ 所有组件路径调整完成
- ✅ 向后兼容性保持

## 八、后续建议

1. **逐步迁移**：新代码应使用新的导入路径，旧代码可逐步迁移
2. **清理计划**：建议在稳定运行一段时间后移除旧的 `auth_service.py` 转发层
3. **持续优化**：后续新增功能应遵循新的模块结构
4. **文档更新**：同步更新相关技术文档和 README
