AgentHub 安装说明 / AgentHub install notes

唯一入口：AgentHub.exe
The only entry point is AgentHub.exe.

它会自行启动、健康检查并管理捆绑的本地服务（Mission Control、Gateway、
MCP Gateway、管理前端）。请勿直接运行 local-services 目录下的任何程序。
It starts, health-checks, and supervises the bundled local services
(Mission Control, Gateway, MCP Gateway, admin frontend) itself. Never run
any binary under local-services directly.

本地数据 / Local data: %LOCALAPPDATA%\AgentHub
高级配置与凭据 / Advanced settings and credentials: 应用内 设置 → 高级设置
