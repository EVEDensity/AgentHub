# AgentHub 本地可运行版本

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
uvicorn main:app --reload --host 0.0.0.0 --port 8000
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
- @Agent 路由：Orchestrator、Architect、CodeGen、Review、Test、Deploy
- DAG 任务进度条
- 符号消息与保真度展示
- 模型配置接口与抽屉页面
- Git 分支/提交演示接口
- 预览面板
- SQLite 本地持久化
