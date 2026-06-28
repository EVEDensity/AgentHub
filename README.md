# AgentHub 本地可运行版本

> 📘 配套文档：[AgentHub AI 辅助开发协作总结](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/AgentHub%20AI%20%E8%BE%85%E5%8A%A9%E5%BC%80%E5%8F%91%E5%8D%8F%E4%BD%9C%E6%80%BB%E7%BB%93.md) — 详细描述本项目如何落地 AI 协作范式、Spec 文档、Skills 体系、Rules 防护与可观测审计。

## 启动

### 方式一：Windows 一键启动

```bat
start.bat
```

### 方式二：手动启动

后端：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

前端：

```bash
cd frontend
npm install
npm run dev
```

访问：

- 前端：http://localhost:3000
- 后端文档：http://localhost:8000/docs

## 已实现

- FastAPI 后端
- WebSocket `/ws/session-1`
- IM 聊天页面
- Dify 风格 Agent 低代码画布 `/canvas`
- 画布拖拽、缩放、选中、删除、复制、图层管理、撤销重做、保存加载、PNG 导出
- 画布 API：`/api/canvas/{id}`、`/api/canvas/save`、`/api/canvas/export`
- @Agent 路由：Orchestrator、Architect、CodeGen、Review、Test、Deploy
- DAG 任务进度条
- 符号消息与保真度展示
- 模型配置接口与抽屉页面
- Git 分支/提交演示接口
- 预览面板
- SQLite 本地持久化
