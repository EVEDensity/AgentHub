from __future__ import annotations

"""Tool definition objects for all built-in tools.

Each definition specifies the fixed metadata fields:
  name, description, category, parameters[], return_type, examples[]
"""

from app.services.tool_registry import ToolDefinition, ToolParameter, ToolExample
from app.services.tools.builtin_tools import (
    web_search_handler,
    file_read_handler,
    file_write_handler,
    file_write_batch_handler,
    code_execute_handler,
    memory_search_handler,
    file_search_handler,
    file_patch_handler,
    memory_save_handler,
    file_edit_handler,
    file_glob_handler,
    mkdir_handler,
)
from app.services.tools.browser_tools import (
    browser_navigate_handler,
    browser_screenshot_handler,
    browser_extract_handler,
    browser_click_handler,
    browser_type_handler,
)
from app.services.tools.skill_tools import (
    skill_list_handler,
    skill_load_handler,
    command_execute_handler,
)
from app.services.tools.agent_tools import (
    invoke_agent_handler,
    invoke_agents_parallel_handler,
    task_handler,
)
from app.services.tools.network_tools import (
    http_request_handler,
)
from app.services.tools.utility_tools import (
    current_date_handler,
    current_time_handler,
    weather_handler,
)
from app.services.tools.change_set import apply_change_set_handler
from app.services.tools.git_tools import (
    git_branch_handler,
    git_branch_create_handler,
    git_cherry_pick_handler,
    git_commit_handler,
    git_diff_handler,
    git_log_handler,
    git_revert_handler,
    git_status_handler,
)
from app.services.tools.developer_tools import (
    ast_symbols_handler,
    audit_report_handler,
    change_plan_handler,
    formatter_handler,
    log_tail_handler,
    package_manager_handler,
    port_check_handler,
    process_list_handler,
    service_health_handler,
    test_discover_handler,
    type_check_handler,
)
from app.services.tools.session_tools import (
    artifact_list_handler,
    artifact_read_handler,
    conversation_search_handler,
    memory_recall_handler,
    memory_retain_handler,
)

# ── web_search ────────────────────────────────────────────────────────

WEB_SEARCH = ToolDefinition(
    name="web_search",
    description="搜索互联网获取实时信息、新闻、资料。当需要最新数据或超出模型知识截止日期时使用此工具。支持按域名过滤结果。",
    category="search",
    parameters=[
        ToolParameter(name="query", type="string", required=True,
                      description="搜索关键词或问题，越具体越好"),
        ToolParameter(name="max_results", type="number", required=False,
                      description="返回结果数量，默认5", default=5),
        ToolParameter(name="language", type="string", required=False,
                      description="搜索结果语言，默认zh（中文）", default="zh"),
        ToolParameter(name="allowed_domains", type="array", required=False,
                      description="只返回这些域名的结果，例如 ['github.com', 'python.org']"),
        ToolParameter(name="blocked_domains", type="array", required=False,
                      description="排除这些域名的结果，例如 ['example.com']"),
    ],
    return_type='{"results": [{"title": "str", "url": "str", "snippet": "str"}], "total": "int", "source": "str", "duration_seconds": "float", "note": "str"}',
    examples=[
        ToolExample(
            user_question="今天有哪些科技新闻？",
            parameters={"query": "科技新闻", "max_results": 5, "language": "zh"},
        ),
        ToolExample(
            user_question="Python 3.13 有什么新特性？从官方文档查找",
            parameters={"query": "Python 3.13 new features", "max_results": 5, "language": "zh", "allowed_domains": ["python.org", "docs.python.org"]},
        ),
        ToolExample(
            user_question="搜索 React 19 相关信息，但排除 CSDN",
            parameters={"query": "React 19 new features", "max_results": 5, "language": "zh", "blocked_domains": ["csdn.net"]},
        ),
    ],
    risk_level="L1",
    handler=web_search_handler,
    is_concurrency_safe=True,  # Read-only, no side effects
)

# ── current_time / current_date / weather ────────────────────────────

CURRENT_TIME = ToolDefinition(
    name="current_time",
    description="读取运行 CLI 的系统时钟，返回当前时间和时区；不得用模型记忆猜测时间。",
    category="system",
    parameters=[
        ToolParameter(name="timezone", type="string", required=False,
                      description="IANA 时区，例如 Asia/Shanghai；为空使用本机时区"),
    ],
    return_type='{"formatted": "YYYY-MM-DD HH:mm:ss", "iso": "ISO-8601", "timezone": "str"}',
    examples=[
        ToolExample(user_question="现在几点？", parameters={}),
        ToolExample(user_question="现在东京几点？", parameters={"timezone": "Asia/Tokyo"}),
    ],
    risk_level="L1",
    handler=current_time_handler,
    is_concurrency_safe=True,
)

CURRENT_DATE = ToolDefinition(
    name="current_date",
    description="读取运行 CLI 的系统日期，返回当前日期和星期；不得用模型记忆猜测日期。",
    category="system",
    parameters=[
        ToolParameter(name="timezone", type="string", required=False,
                      description="IANA 时区，例如 Asia/Shanghai；为空使用本机时区"),
    ],
    return_type='{"formatted": "YYYY-MM-DD", "weekday": "str", "timezone": "str"}',
    examples=[
        ToolExample(user_question="今天是几号？", parameters={}),
    ],
    risk_level="L1",
    handler=current_date_handler,
    is_concurrency_safe=True,
)

WEATHER = ToolDefinition(
    name="weather",
    description="通过 Open-Meteo 查询指定地点的实时天气；没有地点时必须向用户询问城市，不得编造天气。",
    category="search",
    parameters=[
        ToolParameter(name="location", type="string", required=False,
                      description="城市或地点名称，例如 Beijing、上海"),
        ToolParameter(name="latitude", type="number", required=False,
                      description="纬度；与 longitude 一起提供时跳过地理编码"),
        ToolParameter(name="longitude", type="number", required=False,
                      description="经度；与 latitude 一起提供时跳过地理编码"),
    ],
    return_type='{"location": "str", "condition": "str", "temperature": "number", "time": "str"}',
    examples=[
        ToolExample(user_question="北京今天天气怎么样？", parameters={"location": "北京"}),
        ToolExample(user_question="查询上海天气", parameters={"location": "上海"}),
    ],
    risk_level="L1",
    handler=weather_handler,
    is_concurrency_safe=True,
)

# ── file_read ─────────────────────────────────────────────────────────

FILE_READ = ToolDefinition(
    name="file_read",
    description="读取工作区中的文件内容。支持文本文件和目录列表。用于查看代码、配置、日志等文件。",
    category="file",
    parameters=[
        ToolParameter(name="path", type="string", required=True,
                      description="文件相对路径（相对于项目根目录），例如 'README.md' 或 'app/main.py'"),
        ToolParameter(name="encoding", type="string", required=False,
                      description="文件编码，默认utf-8", default="utf-8"),
        ToolParameter(name="max_lines", type="number", required=False,
                      description="最大读取行数，默认500", default=500),
    ],
    return_type='"文件内容文本" 或 目录列表，以及 metadata（path, total_lines, size_bytes, sha256 等）。sha256 可用于后续 file_write 的冲突检测。',
    examples=[
        ToolExample(
            user_question="帮我读一下 README.md 的内容",
            parameters={"path": "README.md", "max_lines": 100},
        ),
        ToolExample(
            user_question="查看 app/services 目录下有哪些文件",
            parameters={"path": "app/services"},
        ),
    ],
    risk_level="L1",
    handler=file_read_handler,
    is_concurrency_safe=True,  # Read-only, no side effects
)

# ── file_write ────────────────────────────────────────────────────────

FILE_WRITE = ToolDefinition(
    name="file_write",
    description="将内容写入工作区的文件。可以创建新文件或追加到现有文件。写入后返回校验哈希和变更信息；Git 提交必须由用户显式调用 git_commit。",
    category="file",
    parameters=[
        ToolParameter(name="path", type="string", required=True,
                      description="文件相对路径（相对于项目根目录）"),
        ToolParameter(name="content", type="string", required=True,
                      description="要写入的完整文本内容"),
        ToolParameter(name="mode", type="string", required=False,
                      description="写入模式: 'overwrite' 覆写(默认) 或 'append' 追加", default="overwrite"),
        ToolParameter(name="expected_sha256", type="string", required=True,
                      description="file_read 返回的完整 sha256；新文件传空字符串。缺失或不匹配时拒绝写入。"),
    ],
    return_type='"写入结果描述文本" 及 metadata（path, size_bytes, mode, sha256, conflict 等）',
    examples=[
        ToolExample(
            user_question="帮我在 data 目录下创建一个 config.json 文件，内容是 {...}",
                parameters={"path": "data/config.json", "content": "{...}", "mode": "overwrite", "expected_sha256": ""},
        ),
    ],
    risk_level="L2",
    handler=file_write_handler,
    is_concurrency_safe=False,  # Has side effects, needs exclusivity
)

# ── file_write_batch ──────────────────────────────────────────────────

FILE_WRITE_BATCH = ToolDefinition(
    name="file_write_batch",
    description="兼容入口：以 apply_change_set 的原子事务契约批量写入多个文件。每项必须携带 expected_sha256；推荐新代码直接使用 apply_change_set。",
    category="file",
    parameters=[
        ToolParameter(name="paths_contents", type="array", required=True,
                      description="文件列表。每项必须为 {\"path\": \"相对路径\", \"content\": \"文件内容\", \"expected_sha256\": \"...\"}；新文件 hash 传空字符串。最多 20 个文件。"),
    ],
    return_type='批量写入结果摘要及每个文件的写入状态（success/fail + metadata）',
    examples=[
        ToolExample(
            user_question="帮我创建一个博客项目的基础结构，包括前端和后端代码",
            parameters={
                "paths_contents": [
                    {"path": "backend/app.py", "content": "from flask import Flask\napp = Flask(__name__)\n...", "expected_sha256": ""},
                    {"path": "frontend/index.html", "content": "<!DOCTYPE html>\n<html>...</html>", "expected_sha256": ""},
                    {"path": "README.md", "content": "# Blog Project\n...", "expected_sha256": ""},
                ],
            },
        ),
        ToolExample(
            user_question="创建 src/utils 目录并在其中写入 helpers.py 和 config.py",
            parameters={
                "paths_contents": [
                    {"path": "src/utils/helpers.py", "content": "def add(a, b): return a + b", "expected_sha256": ""},
                    {"path": "src/utils/config.py", "content": "DEBUG = True", "expected_sha256": ""},
                ],
            },
        ),
    ],
    risk_level="L2",
    handler=file_write_batch_handler,
    is_concurrency_safe=False,  # Has side effects
)

APPLY_CHANGE_SET = ToolDefinition(
    name="apply_change_set",
    description="以一次可回滚事务写入多个文本文件。每项必须提供完整 expected_sha256；任一文件冲突或写入失败都会拒绝或回滚整个变更集。",
    category="file",
    parameters=[
        ToolParameter(
            name="changes", type="array", required=True,
            description="变更数组，每项包含 path、content、expected_sha256；新文件的 expected_sha256 传空字符串。",
        ),
    ],
    return_type='事务结果及 metadata（transaction_id, files[path, sha256, size_bytes]）',
    examples=[
        ToolExample(
            user_question="同时更新两个模块，任何一个冲突就整体回滚",
            parameters={"changes": [{"path": "a.py", "content": "print(1)\\n", "expected_sha256": ""}]},
        ),
    ],
    risk_level="L2",
    handler=apply_change_set_handler,
    is_concurrency_safe=False,
)

_GIT_CWD = ToolParameter(name="cwd", type="string", required=False, description="工作区内目录", default=".")

GIT_STATUS = ToolDefinition("git_status", "查看工作区状态和当前分支。", "git", [_GIT_CWD], "git status", [], handler=git_status_handler)
GIT_DIFF = ToolDefinition("git_diff", "查看未暂存或已暂存的代码差异。", "git", [_GIT_CWD, ToolParameter("staged", "boolean", False, "是否查看暂存区", False), ToolParameter("path", "string", False, "可选文件路径")], "unified diff", [], handler=git_diff_handler)
GIT_LOG = ToolDefinition("git_log", "查看最近提交记录。", "git", [_GIT_CWD, ToolParameter("count", "number", False, "提交数量", 10)], "commit log", [], handler=git_log_handler)
GIT_BRANCH = ToolDefinition("git_branch", "列出当前仓库分支。", "git", [_GIT_CWD], "branch list", [], handler=git_branch_handler)
GIT_BRANCH_CREATE = ToolDefinition("git_branch_create", "创建并切换到新分支。", "git", [ToolParameter("name", "string", True, "新分支名"), _GIT_CWD], "branch result", [], risk_level="L2", handler=git_branch_create_handler, is_concurrency_safe=False)
GIT_COMMIT = ToolDefinition("git_commit", "显式创建 Git 提交；文件工具不会自动提交。", "git", [ToolParameter("message", "string", True, "提交说明"), _GIT_CWD], "commit result", [], risk_level="L2", handler=git_commit_handler, is_concurrency_safe=False)
GIT_REVERT = ToolDefinition("git_revert", "为指定提交创建可审计的 revert 提交。", "git", [ToolParameter("commit", "string", True, "单个 commit id"), _GIT_CWD], "revert result", [], risk_level="L2", handler=git_revert_handler, is_concurrency_safe=False)
GIT_CHERRY_PICK = ToolDefinition("git_cherry_pick", "应用指定提交并保留 Git 冲突状态。", "git", [ToolParameter("commit", "string", True, "单个 commit id"), _GIT_CWD], "cherry-pick result", [], risk_level="L2", handler=git_cherry_pick_handler, is_concurrency_safe=False)

DEV_CWD = ToolParameter(name="path", type="string", required=False, description="工作区内路径", default=".")
AST_SYMBOLS = ToolDefinition("ast_symbols", "解析 Python AST，列出类和函数符号及行号。", "code", [ToolParameter("path", "string", True, "Python 文件路径"), ToolParameter("include_private", "boolean", False, "是否包含下划线私有符号", False)], "symbols", [], handler=ast_symbols_handler)
TEST_DISCOVER = ToolDefinition("test_discover", "发现项目测试文件和 package.json scripts，不执行测试。", "code", [DEV_CWD], "test inventory", [], handler=test_discover_handler)
FORMATTER = ToolDefinition("formatter", "运行已安装的 ruff/black/prettier；默认 check 模式不修改文件。", "code", [DEV_CWD, ToolParameter("formatter", "string", False, "auto/ruff/black/prettier", "auto"), ToolParameter("check", "boolean", False, "仅检查不写入", True)], "formatter result", [], risk_level="L2", handler=formatter_handler, is_concurrency_safe=False)
TYPE_CHECK = ToolDefinition("type_check", "运行已安装的 mypy/pyright/tsc 类型检查器。", "code", [DEV_CWD, ToolParameter("checker", "string", False, "auto/mypy/pyright/tsc", "auto")], "type check result", [], handler=type_check_handler)
PACKAGE_MANAGER = ToolDefinition("package_manager", "执行受限的 npm/pnpm/yarn/pip 依赖操作；install/update 默认 dry-run。", "integration", [ToolParameter("manager", "string", True, "npm/pnpm/yarn/pip"), ToolParameter("action", "string", False, "list/install/update", "list"), ToolParameter("package", "string", False, "可选包名"), ToolParameter("apply", "boolean", False, "是否真正修改依赖", False)], "package manager result", [], risk_level="L2", handler=package_manager_handler, is_concurrency_safe=False)
LOG_TAIL = ToolDefinition("log_tail", "读取工作区内日志文件末尾内容。", "diagnostic", [ToolParameter("path", "string", True, "日志路径"), ToolParameter("lines", "number", False, "行数", 100)], "log text", [], handler=log_tail_handler)
PROCESS_LIST = ToolDefinition("process_list", "列出当前操作系统进程。", "diagnostic", [], "process list", [], handler=process_list_handler)
PORT_CHECK = ToolDefinition("port_check", "检查 TCP 端口是否可连接。", "diagnostic", [ToolParameter("host", "string", False, "主机", "127.0.0.1"), ToolParameter("port", "number", True, "端口"), ToolParameter("timeout", "number", False, "超时秒数", 1.0)], "port result", [], handler=port_check_handler)
SERVICE_HEALTH = ToolDefinition("service_health", "对指定 HTTP 服务执行 GET 健康检查。", "diagnostic", [ToolParameter("url", "string", True, "HTTP URL"), ToolParameter("timeout", "number", False, "超时秒数", 5.0)], "health result", [], handler=service_health_handler)
CHANGE_PLAN = ToolDefinition("change_plan", "将多文件修改整理为可审计的步骤和验证计划。", "workflow", [ToolParameter("changes", "array", True, "变更项列表")], "change plan", [], handler=change_plan_handler)
AUDIT_REPORT = ToolDefinition("audit_report", "聚合 Attempt 文件来源和恢复审计记录。", "workflow", [ToolParameter("attempt_id", "string", False, "可选 Attempt ID")], "audit report", [], handler=audit_report_handler)

# ── code_execute ──────────────────────────────────────────────────────

CODE_EXECUTE = ToolDefinition(
    name="code_execute",
    description="在用户工作区中执行 Python 或 Bash 代码/命令。工作目录为工作区根目录（或指定的子目录），脚本可以访问、导入、测试 Agent 已写入的文件。支持 pip install、npm install 等依赖安装命令（自动延长超时到120秒）。",
    category="code",
    parameters=[
        ToolParameter(name="code", type="string", required=True,
                      description="要执行的代码。Python 脚本或 Bash 命令/脚本。单行 Bash 命令（如 pip install flask）会直接在 shell 中执行。"),
        ToolParameter(name="language", type="string", required=False,
                      description="编程语言: 'python'(默认) 或 'bash'", default="python"),
        ToolParameter(name="timeout", type="number", required=False,
                      description="超时秒数。普通脚本最大30秒，安装命令最大120秒。", default=30),
        ToolParameter(name="cwd", type="string", required=False,
                      description="工作区内的相对工作目录。默认为 '.'（工作区根目录）。例如 'src'、'backend'。该目录下的文件可直接引用。", default="."),
    ],
    return_type='"标准输出和标准错误文本" 及 metadata（language, exit_code, timeout_seconds, cwd, is_install）',
    examples=[
        ToolExample(
            user_question="帮我计算 1 到 100 的和",
            parameters={"code": "print(sum(range(1, 101)))", "language": "python"},
        ),
        ToolExample(
            user_question="列出当前目录的文件",
            parameters={"code": "ls -la", "language": "bash"},
        ),
        ToolExample(
            user_question="安装 Flask 依赖",
            parameters={"code": "pip install flask", "language": "bash"},
        ),
        ToolExample(
            user_question="运行刚写的 app.py",
            parameters={"code": "import app; app.main()", "language": "python", "cwd": "."},
        ),
    ],
    risk_level="L3",
    handler=code_execute_handler,
    is_concurrency_safe=False,  # Has side effects, resource-intensive
    requires_user_confirmation=True,  # L3 risk: always confirm
)

# ── memory_search ─────────────────────────────────────────────────────

MEMORY_SEARCH = ToolDefinition(
    name="memory_search",
    description="搜索 Agent 的持久化记忆库，查找相关的上下文、偏好、决策和知识。用于获取之前会话中存储的信息。",
    category="memory",
    parameters=[
        ToolParameter(name="query", type="string", required=True,
                      description="搜索关键词或问题"),
        ToolParameter(name="max_results", type="number", required=False,
                      description="最大返回结果数，默认5", default=5),
    ],
    return_type='{"results": [{"name": "str", "type": "str", "description": "str", "relevance_score": "float"}], "total": "int"}',
    examples=[
        ToolExample(
            user_question="我之前让你记住的数据库密码是什么？",
            parameters={"query": "数据库 密码", "max_results": 3},
        ),
        ToolExample(
            user_question="这个项目的截止日期是什么？",
            parameters={"query": "截止日期 项目", "max_results": 5},
        ),
    ],
    risk_level="L1",
    handler=memory_search_handler,
    is_concurrency_safe=True,  # Read-only, no side effects
)

# ── browser_navigate ──────────────────────────────────────────────────

BROWSER_NAVIGATE = ToolDefinition(
    name="browser_navigate",
    description="使用无头浏览器导航到指定 URL 并提取页面文本内容。用于访问网页、阅读文章、查看文档等需要真实浏览器渲染的场景。",
    category="search",
    parameters=[
        ToolParameter(name="url", type="string", required=True,
                      description="目标网页的完整 URL，例如 'https://example.com'"),
        ToolParameter(name="wait_until", type="string", required=False,
                      description="等待策略: 'domcontentloaded'(默认), 'load', 'networkidle'",
                      default="domcontentloaded"),
        ToolParameter(name="timeout", type="number", required=False,
                      description="页面加载超时秒数，默认30", default=30),
    ],
    return_type='{"url": "str", "title": "str", "text_preview": "str", "total_chars": "int"}',
    examples=[
        ToolExample(
            user_question="帮我打开 https://example.com 看看内容",
            parameters={"url": "https://example.com", "wait_until": "domcontentloaded"},
        ),
        ToolExample(
            user_question="查看 GitHub 上 Python 的最新发布",
            parameters={"url": "https://github.com/python/cpython/releases", "wait_until": "networkidle"},
        ),
    ],
    risk_level="L1",
    handler=browser_navigate_handler,
    is_concurrency_safe=True,  # Read-only page visit
)

# ── browser_screenshot ────────────────────────────────────────────────

BROWSER_SCREENSHOT = ToolDefinition(
    name="browser_screenshot",
    description="使用无头浏览器截取网页截图（返回 base64 编码图片）。用于查看网页外观、验证 UI 布局等场景。",
    category="search",
    parameters=[
        ToolParameter(name="url", type="string", required=True,
                      description="目标网页的完整 URL"),
        ToolParameter(name="full_page", type="boolean", required=False,
                      description="是否截取整个页面（默认只截取视口）", default=False),
        ToolParameter(name="selector", type="string", required=False,
                      description="CSS 选择器，只截取匹配的元素（可选）"),
        ToolParameter(name="timeout", type="number", required=False,
                      description="页面加载超时秒数，默认30", default=30),
    ],
    return_type='{"url": "str", "screenshot_base64": "str", "size_bytes": "int"}',
    examples=[
        ToolExample(
            user_question="帮我截个 https://example.com 的图",
            parameters={"url": "https://example.com", "full_page": True},
        ),
    ],
    risk_level="L1",
    handler=browser_screenshot_handler,
    is_concurrency_safe=True,  # Read-only screenshot
)


# ── skill_list ─────────────────────────────────────────────────────────

SKILL_LIST = ToolDefinition(
    name="skill_list",
    description="列出所有可用的技能(Skills)。技能是可复用的脚本、工具和工作流，用于扩展 AI 能力。使用此工具查看有哪些技能可以加载和执行。",
    category="system",
    parameters=[
        ToolParameter(name="source", type="string", required=False,
                      description="过滤来源: 'all'(全部,默认), 'user'(用户级), 'project'(项目级)", default="all"),
    ],
    return_type='{"skills": [{"name": "str", "description": "str", "version": "str", "scripts": [...]}], "total": "int"}',
    examples=[
        ToolExample(
            user_question="有哪些可用的技能？",
            parameters={"source": "all"},
        ),
        ToolExample(
            user_question="列出项目级技能",
            parameters={"source": "project"},
        ),
    ],
    risk_level="L1",
    handler=skill_list_handler,
    is_concurrency_safe=True,  # Read-only listing
)

# ── skill_load ─────────────────────────────────────────────────────────

SKILL_LOAD = ToolDefinition(
    name="skill_load",
    description="加载指定技能的完整文档(SKILL.md)。返回技能的详细说明、使用方法和可用脚本列表。在通过 skill_list 找到需要的技能后，使用此工具获取完整文档以了解如何正确调用。",
    category="system",
    parameters=[
        ToolParameter(name="name", type="string", required=True,
                      description="技能名称(目录名)，例如 'anysearch'、'skill-creator'"),
    ],
    return_type='{"name": "str", "description": "str", "version": "str", "body": "str (SKILL.md全文)", "scripts": [...], "scripts_dir": "str"}',
    examples=[
        ToolExample(
            user_question="加载 anysearch 技能的文档",
            parameters={"name": "anysearch"},
        ),
        ToolExample(
            user_question="查看 skill-creator 的详细说明",
            parameters={"name": "skill-creator"},
        ),
    ],
    risk_level="L1",
    handler=skill_load_handler,
    is_concurrency_safe=True,  # Read-only document loading
)

# ── command_execute ────────────────────────────────────────────────────

COMMAND_EXECUTE = ToolDefinition(
    name="command_execute",
    description="在指定目录中执行 shell 命令（Python/Bash/Node.js）。用于运行技能脚本、执行后台任务或运行系统命令。支持超时控制和输出截断。注意：交互式命令（vim、less等）被禁止。",
    category="system",
    parameters=[
        ToolParameter(name="command", type="string", required=True,
                      description="要执行的完整 shell 命令，例如 'python ./scripts/search.py \"关键词\"'"),
        ToolParameter(name="cwd", type="string", required=False,
                      description="工作目录路径，默认为项目根目录。执行技能脚本时应设置为技能目录。"),
        ToolParameter(name="timeout", type="number", required=False,
                      description="超时秒数，默认60，最大120", default=60),
    ],
    return_type='"标准输出和标准错误文本" 及 metadata（command, cwd, exit_code, stdout_length, timeout_seconds）',
    examples=[
        ToolExample(
            user_question="运行 anysearch 的文档命令",
            parameters={
                "command": "python scripts/anysearch_cli.py doc",
                "cwd": "~/.claude/skills/anysearch",
                "timeout": 30,
            },
        ),
        ToolExample(
            user_question="查看当前目录的文件列表",
            parameters={
                "command": "ls -la",
                "cwd": ".",
                "timeout": 10,
            },
        ),
        ToolExample(
            user_question="执行 Python 脚本",
            parameters={
                "command": "python generate_report.py --output report.json",
                "cwd": "./scripts",
                "timeout": 60,
            },
        ),
    ],
    risk_level="L3",  # High risk: executes arbitrary shell commands
    handler=command_execute_handler,
    is_concurrency_safe=False,  # Has side effects, resource-intensive
    requires_user_confirmation=True,  # L3 risk: always confirm before executing
)

# ── browser_extract ────────────────────────────────────────────────────

BROWSER_EXTRACT = ToolDefinition(
    name="browser_extract",
    description="导航到指定 URL，使用 CSS 选择器精确提取页面内容。支持提取文本、HTML 或元素属性。当只需要页面特定部分而非整个页面时使用此工具，比 browser_navigate 更精确。",
    category="search",
    parameters=[
        ToolParameter(name="url", type="string", required=True,
                      description="目标网页的完整 URL"),
        ToolParameter(name="selector", type="string", required=False,
                      description="CSS 选择器，提取匹配的元素内容，默认 'body'", default="body"),
        ToolParameter(name="extract_type", type="string", required=False,
                      description="提取类型: 'text'(文本), 'html'(HTML源码), 'attribute'(属性值)", default="text"),
        ToolParameter(name="wait_for_selector", type="string", required=False,
                      description="等待此 CSS 选择器出现后再提取（用于动态页面）"),
        ToolParameter(name="timeout", type="number", required=False,
                      description="页面加载超时秒数，默认30", default=30),
    ],
    return_type='{"url": "str", "title": "str", "selector": "str", "content": "str", "total_chars": "int"}',
    examples=[
        ToolExample(
            user_question="提取 https://example.com 页面中 #main-content 的内容",
            parameters={"url": "https://example.com", "selector": "#main-content", "extract_type": "text"},
        ),
    ],
    risk_level="L1",
    handler=browser_extract_handler,
    is_concurrency_safe=True,
)

# ── browser_click ──────────────────────────────────────────────────────

BROWSER_CLICK = ToolDefinition(
    name="browser_click",
    description="导航到指定 URL，点击页面上的元素，返回点击后的页面信息。用于模拟用户点击按钮、链接等交互操作，获取交互后的页面内容。",
    category="search",
    parameters=[
        ToolParameter(name="url", type="string", required=True,
                      description="目标网页的完整 URL"),
        ToolParameter(name="selector", type="string", required=True,
                      description="要点击的元素的 CSS 选择器，例如 'button.submit' 或 'a.more'"),
        ToolParameter(name="timeout", type="number", required=False,
                      description="超时秒数，默认30", default=30),
    ],
    return_type='{"url": "str", "title": "str", "text_preview": "str", "total_chars": "int"}',
    examples=[
        ToolExample(
            user_question="点击 https://example.com 页面上的 '加载更多' 按钮",
            parameters={"url": "https://example.com", "selector": "button.load-more", "timeout": 30},
        ),
    ],
    risk_level="L2",
    handler=browser_click_handler,
    is_concurrency_safe=False,
)

# ── browser_type ────────────────────────────────────────────────────────

BROWSER_TYPE = ToolDefinition(
    name="browser_type",
    description="导航到指定 URL，在输入框中键入文本，可选按回车提交。用于模拟表单填写、搜索框输入等交互操作。",
    category="search",
    parameters=[
        ToolParameter(name="url", type="string", required=True,
                      description="目标网页的完整 URL"),
        ToolParameter(name="selector", type="string", required=True,
                      description="输入框的 CSS 选择器，例如 'input[name=\"q\"]'"),
        ToolParameter(name="text", type="string", required=True,
                      description="要输入的文本内容"),
        ToolParameter(name="press_enter", type="boolean", required=False,
                      description="是否在输入后按回车提交", default=False),
        ToolParameter(name="timeout", type="number", required=False,
                      description="超时秒数，默认30", default=30),
    ],
    return_type='{"url": "str", "title": "str", "text_preview": "str", "total_chars": "int"}',
    examples=[
        ToolExample(
            user_question="在 https://www.google.com 搜索框中输入 'Python 3.13' 并搜索",
            parameters={"url": "https://www.google.com", "selector": "input[name=\"q\"]", "text": "Python 3.13", "press_enter": True},
        ),
    ],
    risk_level="L2",
    handler=browser_type_handler,
    is_concurrency_safe=False,
)

# ── file_search ────────────────────────────────────────────────────────

FILE_SEARCH = ToolDefinition(
    name="file_search",
    description="在项目文件中搜索匹配正则表达式的内容（类似 grep）。返回匹配行及其文件路径、行号和上下文。用于查找代码、配置、文档中的特定内容。",
    category="file",
    parameters=[
        ToolParameter(name="pattern", type="string", required=True,
                      description=r"搜索的正则表达式，例如 'function\s+\w+' 或 'TODO'"),
        ToolParameter(name="path", type="string", required=False,
                      description="搜索的目录路径（相对于工作区），默认 '.'", default="."),
        ToolParameter(name="glob", type="string", required=False,
                      description="文件过滤模式，例如 '*.py' 或 '*.{ts,tsx}'。用逗号分隔多个", default="*"),
        ToolParameter(name="max_results", type="number", required=False,
                      description="最大返回结果数，默认30", default=30),
        ToolParameter(name="context_lines", type="number", required=False,
                      description="每个匹配周围显示的上下文行数，默认2", default=2),
        ToolParameter(name="ignore_case", type="boolean", required=False,
                      description="是否忽略大小写，默认 true", default=True),
    ],
    return_type='{"pattern": "str", "matches": [{"file": "str", "line": "int", "match": "str", "context": "str"}], "total_matches": "int", "scanned_files": "int"}',
    examples=[
        ToolExample(
            user_question="搜索项目里所有 Python 文件中包含 'TODO' 的地方",
            parameters={"pattern": "TODO", "path": ".", "glob": "*.py", "max_results": 20},
        ),
        ToolExample(
            user_question="查找所有引用 'UserService' 的文件",
            parameters={"pattern": "UserService", "path": ".", "glob": "*.ts,*.tsx,*.py", "max_results": 30},
        ),
    ],
    risk_level="L1",
    handler=file_search_handler,
    is_concurrency_safe=True,
)

# ── file_patch ─────────────────────────────────────────────────────────

FILE_PATCH = ToolDefinition(
    name="file_patch",
    description="将 unified diff 格式的补丁应用到文件上，实现精确的增量修改。当文件较大时，使用此工具仅修改目标区域比覆写整个文件更高效。支持标准 unified diff 格式（git diff / diff -u 输出）。",
    category="file",
    parameters=[
        ToolParameter(name="path", type="string", required=True,
                      description="要打补丁的文件路径（相对于工作区），例如 'app/main.py'"),
        ToolParameter(name="diff", type="string", required=True,
                      description="Unified diff 格式的补丁内容，包含 @@ -a,n +b,m @@ 块头"),
        ToolParameter(name="expected_sha256", type="string", required=True,
                      description="file_read 返回的完整 sha256；文件被修改或缺失时拒绝应用补丁。"),
    ],
    return_type='补丁应用结果描述 + 文件预览 + metadata（lines_added, lines_removed, total_lines）',
    examples=[
        ToolExample(
            user_question="在 app/main.py 的第 42-45 行处应用以下修改：@@ -42,4 +42,5 @@ ...",
            parameters={
                "path": "app/main.py",
                "diff": "@@ -42,4 +42,5 @@\n def main():\n-    pass\n+    print('hello')\n+    return 0",
                "expected_sha256": "<full sha256 from file_read>",
            },
        ),
    ],
    risk_level="L2",
    handler=file_patch_handler,
    is_concurrency_safe=False,
)

# ── http_request ────────────────────────────────────────────────────────

HTTP_REQUEST = ToolDefinition(
    name="http_request",
    description="发送 HTTP 请求到外部 URL，用于查询 API、触发 webhook、获取外部数据。支持 GET/POST/PUT/DELETE/PATCH 方法。自动阻止内网地址（SSRF 防护）。返回状态码、响应头和正文。",
    category="integration",
    parameters=[
        ToolParameter(name="url", type="string", required=True,
                      description="请求的目标 URL，例如 'https://api.github.com/repos/python/cpython'"),
        ToolParameter(name="method", type="string", required=False,
                      description="HTTP 方法: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS。默认 GET", default="GET"),
        ToolParameter(name="headers", type="object", required=False,
                      description="请求头，例如 {\"Authorization\": \"Bearer token123\", \"Content-Type\": \"application/json\"}"),
        ToolParameter(name="body", type="string", required=False,
                      description="请求正文（用于 POST/PUT/PATCH）"),
        ToolParameter(name="timeout", type="number", required=False,
                      description="请求超时秒数，默认30，最大30", default=30),
    ],
    return_type='{"status_code": "int", "headers": "dict", "body": "str", "json": "dict (if applicable)", "duration_ms": "float"}',
    examples=[
        ToolExample(
            user_question="查询 GitHub 上 Python 仓库的信息",
            parameters={"url": "https://api.github.com/repos/python/cpython", "method": "GET", "timeout": 15},
        ),
        ToolExample(
            user_question="发送一个 POST 请求到 webhook",
            parameters={
                "url": "https://hooks.example.com/webhook",
                "method": "POST",
                "headers": {"Content-Type": "application/json"},
                "body": '{"event": "deploy", "status": "success"}',
            },
        ),
    ],
    risk_level="L2",
    handler=http_request_handler,
    is_concurrency_safe=True,
)

# ── memory_save ─────────────────────────────────────────────────────────

MEMORY_SAVE = ToolDefinition(
    name="memory_save",
    description="将信息保存到持久化记忆库中。记忆跨会话保留，可通过 memory_search 搜索。用于保存用户偏好、项目上下文、重要决策等需要长期保留的信息。",
    category="memory",
    parameters=[
        ToolParameter(name="name", type="string", required=True,
                      description="记忆名称（kebab-case slug），例如 'user-pref-theme' 或 'project-deadline'"),
        ToolParameter(name="content", type="string", required=True,
                      description="记忆的完整内容（markdown 格式），包含要记住的事实"),
        ToolParameter(name="type", type="string", required=False,
                      description="记忆类型: 'user'(用户角色/偏好), 'feedback'(纠正/反馈), 'project'(项目上下文), 'reference'(外部引用)。默认 'reference'",
                      default="reference"),
        ToolParameter(name="description", type="string", required=False,
                      description="一行摘要，用于 MEMORY.md 索引"),
    ],
    return_type='{"name": "str", "filename": "str", "type": "str", "description": "str", "updated_at": "str"}',
    examples=[
        ToolExample(
            user_question="记住：用户偏好使用蓝色主题，数据库用 PostgreSQL",
            parameters={
                "name": "user-preferences",
                "content": "## 用户偏好\n- 主题颜色: 蓝色\n- 数据库: PostgreSQL\n\n这些偏好在所有项目中保持一致。",
                "type": "user",
                "description": "用户技术栈偏好（蓝色主题、PostgreSQL）",
            },
        ),
    ],
    risk_level="L1",
    handler=memory_save_handler,
    is_concurrency_safe=True,
)

# ── artifact_list ───────────────────────────────────────────────────────

ARTIFACT_LIST = ToolDefinition(
    name="artifact_list",
    description="列出当前会话中生成的所有产物（代码文件、文档、计划等）。产物是代码生成Agent的输出文件，自动记录到数据库。用于了解当前会话已生成了哪些文件。",
    category="system",
    parameters=[
        ToolParameter(name="max_results", type="number", required=False,
                      description="最大返回数量，默认20，最大50", default=20),
    ],
    return_type='{"artifacts": [{"id": "str", "file_path": "str", "version": "int", "content_length": "int", "created_at": "str"}], "total": "int"}',
    examples=[
        ToolExample(
            user_question="列出当前会话生成了哪些文件",
            parameters={"max_results": 20},
        ),
    ],
    risk_level="L1",
    handler=artifact_list_handler,
    is_concurrency_safe=True,
)

# ── artifact_read ───────────────────────────────────────────────────────

ARTIFACT_READ = ToolDefinition(
    name="artifact_read",
    description="读取指定产物的完整内容。先使用 artifact_list 获取产物 ID，再用此工具读取内容。用于查看之前生成的代码、文档或计划的详细内容。",
    category="system",
    parameters=[
        ToolParameter(name="artifact_id", type="string", required=True,
                      description="产物 ID（UUID 格式，从 artifact_list 获取）"),
    ],
    return_type='{"id": "str", "file_path": "str", "version": "int", "content": "str", "content_length": "int"}',
    examples=[
        ToolExample(
            user_question="查看产物 abc-123-def 的完整内容",
            parameters={"artifact_id": "abc-123-def-456"},
        ),
    ],
    risk_level="L1",
    handler=artifact_read_handler,
    is_concurrency_safe=True,
)

# ── conversation_search ─────────────────────────────────────────────────

CONVERSATION_SEARCH = ToolDefinition(
    name="conversation_search",
    description="搜索当前会话的对话历史，查找包含特定关键词的历史消息。用于在长对话中回溯之前讨论的内容、决策或上下文。",
    category="memory",
    parameters=[
        ToolParameter(name="query", type="string", required=True,
                      description="搜索关键词（空格分隔多个词），例如 'API 设计 数据库'"),
        ToolParameter(name="max_results", type="number", required=False,
                      description="最大返回结果数，默认10，最大30", default=10),
        ToolParameter(name="sender", type="string", required=False,
                      description="按发送者过滤，例如 'user'（用户消息）、'Architect'、'Orchestrator'"),
    ],
    return_type='{"query": "str", "results": [{"message_id": "str", "sender": "str", "content_preview": "str", "timestamp": "str", "relevance": "float"}], "total": "int"}',
    examples=[
        ToolExample(
            user_question="之前讨论过数据库选型吗？",
            parameters={"query": "数据库 选型 PostgreSQL MySQL", "max_results": 10},
        ),
        ToolExample(
            user_question="我之前提到过什么技术偏好吗？",
            parameters={"query": "偏好 技术栈", "sender": "user", "max_results": 10},
        ),
    ],
    risk_level="L1",
    handler=conversation_search_handler,
    is_concurrency_safe=True,
)

# ── memory_recall ─────────────────────────────────────────────────────

MEMORY_RECALL = ToolDefinition(
    name="memory_recall",
    description="只读暴露多层记忆（L0 工作记忆 / L1 情景摘要 / 语义持久化记忆 / 项目事实）。Agent 执行 Mission 时调用此工具获取用户偏好、历史上下文、项目 ADR 等信息，避免重复询问或违反既有约定。支持关键词过滤。",
    category="memory",
    parameters=[
        ToolParameter(name="query", type="string", required=False,
                      description="搜索关键词（可选），匹配语义记忆的 name/description/body 字段和项目事实", default=""),
        ToolParameter(name="scope", type="string", required=False,
                      description="记忆范围: 'session'(默认, 当前会话) 或 'global'(跨会话聚合)", default="session"),
        ToolParameter(name="max_results", type="number", required=False,
                      description="语义记忆/事实的最大返回数量，默认10，最大30", default=10),
    ],
    return_type='{"query": "str", "scope": "str", "layers": {"L0": {...}, "L1": {...}, "semantic": {...}, "facts": {...}}}',
    examples=[
        ToolExample(
            user_question="回忆一下用户之前提过什么技术偏好？",
            parameters={"query": "偏好 技术栈", "max_results": 5},
        ),
        ToolExample(
            user_question="这个项目有哪些已知的 ADR 或架构决策？",
            parameters={"query": "ADR 架构", "scope": "global", "max_results": 10},
        ),
    ],
    risk_level="L1",
    handler=memory_recall_handler,
    is_concurrency_safe=True,  # Read-only — no side effects
)

# ── memory_retain ─────────────────────────────────────────────────────

MEMORY_RETAIN = ToolDefinition(
    name="memory_retain",
    description="将一个请求级别的工作记忆事实追加到当前会话的 working memory 文件。Agent 在 Mission 执行中观察到用户偏好、项目约束等时调用此工具即时记录，信息会在下一次 memory_recall 中可见。区别于 memory_save（写持久化 MEMORY.md 跨会话保留），memory_retain 仅会话内有效。",
    category="memory",
    parameters=[
        ToolParameter(name="fact", type="string", required=True,
                      description="要保留的事实内容（20-500 字符），如 '用户偏好 TypeScript 严格模式'"),
        ToolParameter(name="note", type="string", required=False,
                      description="上下文备注（why / where 观察到的）", default=""),
    ],
    return_type='{"sessionId": "str", "fact": "str", "recordedAt": "str", "path": "str"}',
    examples=[
        ToolExample(
            user_question="（Agent 在 Mission 执行中观察到）用户提到所有 API 必须用 FastAPI + Pydantic v2，需要记住这个偏好",
            parameters={"fact": "项目 API 必须使用 FastAPI + Pydantic v2", "note": "用户在 Mission mission_xyz 对话中明确"},
        ),
    ],
    risk_level="L1",
    handler=memory_retain_handler,
    is_concurrency_safe=False,  # Has side effects (appends to working memory file)
)

# ── invoke_agent ───────────────────────────────────────────────────────
# THIS is the key tool that transforms the Orchestrator from a "fake
# dispatcher" into a REAL orchestrator.  It allows the default agent
# to dynamically spawn sub-agents (Architect, CodeGen, Review, Test,
# Deploy) during a conversation and synthesize their outputs.

INVOKE_AGENT = ToolDefinition(
    name="invoke_agent",
    description=(
        "调用指定的专业 Agent 执行子任务。将复杂任务委派给领域专家 Agent（Architect 架构设计、"
        "CodeGen 代码生成、Review 代码审查、Test 测试验证、Deploy 部署发布），获取其输出结果。"
        "适用于需要多 Agent 协作的复杂需求。每次调用一个 Agent，可多次调用不同 Agent 完成多步骤任务。"
    ),
    category="system",
    parameters=[
        ToolParameter(name="agent_name", type="string", required=True,
                      description="要调用的 Agent 名称: Architect | CodeGen | Review | Test | Deploy"),
        ToolParameter(name="task", type="string", required=True,
                      description="分配给该 Agent 的详细任务描述，越具体越好。包含需求、约束和预期产出。"),
        ToolParameter(name="context", type="string", required=False,
                      description="参考上下文：前面 Agent 的输出摘要或其他相关背景信息"),
        ToolParameter(name="require_confirmation", type="boolean", required=False,
                      description="是否需要用户确认后才执行。高风险操作（部署、删除）设为 true", default=False),
    ],
    return_type='{"success": bool, "result": "Agent 的完整输出文本", "agent_name": "str", "agent_domain": "str", "duration_ms": float, "result_length": int}',
    examples=[
        ToolExample(
            user_question="我要做一个博客网站，帮我规划一下",
            parameters={
                "agent_name": "Architect",
                "task": "用户要做一个博客网站。请分析需求，输出技术方案：包括推荐的技术栈（前端框架、后端框架、数据库）、系统架构设计、模块划分、数据流设计。用户没有指定具体技术栈，请根据最佳实践推荐。",
            },
        ),
        ToolExample(
            user_question="基于 Architect 的方案，帮我生成前端代码",
            parameters={
                "agent_name": "CodeGen",
                "task": "基于 Architect 的技术方案，生成博客网站的前端代码。包括：首页文章列表、文章详情页、发布文章页面。使用 React + TypeScript。",
                "context": "[Architect 的输出摘要] 推荐使用 React + TypeScript + FastAPI + PostgreSQL...",
            },
        ),
    ],
    risk_level="L2",
    handler=invoke_agent_handler,
    is_concurrency_safe=False,  # Agent calls have side effects (saving messages, etc.)
)

# ── invoke_agents_parallel ──────────────────────────────────────────────

INVOKE_AGENTS_PARALLEL = ToolDefinition(
    name="invoke_agents_parallel",
    description=(
        "并行调用多个专业 Agent 执行独立的子任务。当多个子任务之间没有依赖关系时，"
        "使用此工具同时调用以节省时间。每个 Agent 收到独立的任务描述，并行执行后汇总所有结果。"
    ),
    category="system",
    parameters=[
        ToolParameter(name="calls", type="array", required=True,
                      description="要并行调用的 Agent 任务列表。每项包含: agent_name(必填), task(必填), context(选填), require_confirmation(选填)"),
    ],
    return_type='{"success": bool, "results": [...], "total_duration_ms": float, "success_count": int, "total_count": int}',
    examples=[
        ToolExample(
            user_question="帮我同时做代码审查和测试",
            parameters={
                "calls": [
                    {"agent_name": "Review", "task": "审查 app/api/users.py 的代码质量和安全性"},
                    {"agent_name": "Test", "task": "为 app/api/users.py 的 API 端点编写测试用例"},
                ],
            },
        ),
    ],
    risk_level="L2",
    handler=invoke_agents_parallel_handler,
    is_concurrency_safe=False,
)

# ── file_edit ────────────────────────────────────────────────────────────

FILE_EDIT = ToolDefinition(
    name="file_edit",
    description="执行精确字符串替换（类似 sed）。读取文件，查找完全匹配的 old_string，替换为 new_string。这是对文件进行定点修改的首选方式——比 file_patch（unified diff）更简单可靠，LLM 不容易出错。适合修改函数名、修改变量值、插入代码片段等场景。",
    category="file",
    parameters=[
        ToolParameter(name="path", type="string", required=True,
                      description="要编辑的文件路径（相对于工作区），例如 'app/main.py'"),
        ToolParameter(name="old_string", type="string", required=True,
                      description="要查找并替换的原始文本。必须与文件内容完全一致（包括空格、缩进、换行）。"),
        ToolParameter(name="new_string", type="string", required=True,
                      description="替换后的新文本。如果不想做任何修改，设置与 old_string 相同。"),
        ToolParameter(name="replace_all", type="boolean", required=False,
                      description="是否替换所有匹配项。默认 false（只替换第一处）。当 old_string 在文件中出现多次且 replace_all=false 时，工具会拒绝执行并返回所有匹配位置。", default=False),
        ToolParameter(name="expected_sha256", type="string", required=True,
                      description="file_read 返回的完整 sha256；新文件传空字符串。缺失或不匹配时拒绝编辑。"),
    ],
    return_type='"替换结果描述文本" 及 metadata（path, occurrences, replaced, size_bytes, sha256 等）',
    examples=[
        ToolExample(
            user_question="把 app/main.py 中所有的 'user_name' 改成 'username'",
            parameters={"path": "app/main.py", "old_string": "user_name", "new_string": "username", "replace_all": True, "expected_sha256": "<full sha256 from file_read>"},
        ),
        ToolExample(
            user_question="在 config.py 的 DEBUG = False 改为 DEBUG = True",
            parameters={"path": "config.py", "old_string": "DEBUG = False", "new_string": "DEBUG = True"},
        ),
        ToolExample(
            user_question="在 README.md 开头插入一行项目描述",
            parameters={"path": "README.md", "old_string": "# My Project", "new_string": "# My Project\n\n> A modern web application built with React and FastAPI."},
        ),
    ],
    risk_level="L2",
    handler=file_edit_handler,
    is_concurrency_safe=False,  # Has side effects
)

# ── file_glob ────────────────────────────────────────────────────────────

FILE_GLOB = ToolDefinition(
    name="file_glob",
    description="使用 glob 模式匹配文件路径。支持通配符 *（匹配任意字符）、**（递归匹配目录）、?（匹配单个字符）、[abc]（字符组）。用于快速查找项目中的文件，了解项目结构。注意：此工具只返回文件名，不返回文件内容。要读取文件内容请使用 file_read。",
    category="file",
    parameters=[
        ToolParameter(name="pattern", type="string", required=True,
                      description="Glob 匹配模式，例如 '**/*.py'（所有 Python 文件）、'src/**/*.tsx'（src 下所有 TSX）、'*.md'（根目录 Markdown 文件）、'app/services/**/*.py'（services 目录下所有 Python）"),
        ToolParameter(name="path", type="string", required=False,
                      description="搜索起始目录（相对于工作区）。默认为 '.'（工作区根目录）", default="."),
    ],
    return_type='{"pattern": "str", "search_path": "str", "matches": [{"path": "str", "size_bytes": "int", "size_display": "str"}], "total_matches": "int", "truncated": "bool"}',
    examples=[
        ToolExample(
            user_question="列出项目中所有的 Python 文件",
            parameters={"pattern": "**/*.py", "path": "."},
        ),
        ToolExample(
            user_question="列出 app/services 目录下的所有 TypeScript 文件",
            parameters={"pattern": "**/*.ts", "path": "app/services"},
        ),
        ToolExample(
            user_question="查看前端组件目录结构",
            parameters={"pattern": "frontend/components/**/*.tsx", "path": "."},
        ),
        ToolExample(
            user_question="查找所有配置文件",
            parameters={"pattern": "**/*.{json,yaml,yml,toml,cfg,ini}", "path": "."},
        ),
    ],
    risk_level="L1",
    handler=file_glob_handler,
    is_concurrency_safe=True,  # Read-only
)

# ── mkdir ────────────────────────────────────────────────────────────────

MKDIR = ToolDefinition(
    name="mkdir",
    description="在用户的工作区中创建目录（类似 mkdir -p）。Git 提交必须由用户显式调用 git_commit。",
    category="file",
    parameters=[
        ToolParameter(name="path", type="string", required=True,
                      description="工作区内要创建的目录的相对路径。例如 'src/components'、'app/api/routes'。支持嵌套路径，父目录默认会自动创建。"),
        ToolParameter(name="parents", type="boolean", required=False,
                      description="是否自动创建缺失的父目录（类似 mkdir -p）。默认为 true。当设为 false 时，如果父目录不存在则报错。", default=True),
    ],
    return_type="成功时返回创建确认和路径信息。目录已存在时也返回成功（幂等操作）。失败时返回错误原因。",
    examples=[
        ToolExample(
            user_question="帮我搭建一个 Flask 项目的目录结构",
            parameters={"path": "src/routes", "parents": True},
        ),
        ToolExample(
            user_question="创建 components 目录",
            parameters={"path": "frontend/components", "parents": True},
        ),
        ToolExample(
            user_question="初始化项目，创建 app/api 和 app/models 目录",
            parameters={"path": "app/api", "parents": True},
        ),
    ],
    risk_level="L1",
    handler=mkdir_handler,
    is_concurrency_safe=False,  # Has side effects
)

# ── task ──────────────────────────────────────────────────────────────────

TASK = ToolDefinition(
    name="task",
    description="启动一个临时的、独立的 Agent 来处理复杂子任务。与 invoke_agent（只能调用预定义的 7 个 Agent）不同，task 会动态创建一个全新的 Agent 实例，拥有完整的工具访问权限（文件操作、搜索、代码执行等），专门完成你指定的任务。每个 task 调用都是独立的，不共享上下文。适用于：需要全新视角的分析任务、不适合现有 Agent 角色的特殊任务、并行分解大型任务的子问题。",
    category="system",
    parameters=[
        ToolParameter(name="description", type="string", required=True,
                      description="任务的简短描述（3-5个词），用作标识标签。例如 '查找认证漏洞'、'重构数据库层'"),
        ToolParameter(name="prompt", type="string", required=True,
                      description="完整的任务描述。越具体越好——包含期望输出、约束条件、相关上下文。Agent 将此作为其唯一的用户消息，并获得全部工具的访问权。"),
        ToolParameter(name="subagent_type", type="string", required=False,
                      description="Agent 行为模式提示: 'general-purpose'(默认,全部工具), 'Explore'(只读搜索), 'Plan'(架构思考,无文件写入)", default="general-purpose"),
        ToolParameter(name="model", type="string", required=False,
                      description="可选的模型覆盖。留空则继承会话默认模型。"),
    ],
    return_type='{"success": bool, "result": "Agent 的最终文本输出", "description": "str", "duration_ms": "float", "result_length": "int", "subagent_type": "str"}',
    examples=[
        ToolExample(
            user_question="帮我全面审查这个项目的安全性，找出所有潜在漏洞",
            parameters={
                "description": "安全审查",
                "prompt": "请全面审查当前项目的安全性。检查：1) SQL 注入风险 2) XSS 漏洞 3) 认证/授权缺陷 4) 敏感信息泄露 5) 不安全的依赖。对每个发现的问题给出风险等级和修复建议。使用 file_search 和 file_read 工具查看代码。",
                "subagent_type": "Explore",
            },
        ),
        ToolExample(
            user_question="重构数据库访问层，从原始 SQL 迁移到 ORM",
            parameters={
                "description": "重构数据库层",
                "prompt": "分析当前项目中的数据库访问模式（使用 file_search 查找原始 SQL 查询），输出一份详细的重构方案，包括：1) 当前使用原始 SQL 的文件清单 2) 推荐的 ORM 方案 3) 迁移步骤 4) 风险评估。不要编写代码，只输出方案。",
                "subagent_type": "Plan",
            },
        ),
        ToolExample(
            user_question="并行分析前端和后端的性能瓶颈",
            parameters={
                "description": "性能分析",
                "prompt": "分析项目中可能导致性能问题的代码模式。搜索：1) N+1 查询 2) 未优化的循环 3) 大文件未分页读取 4) 阻塞操作。使用 file_search 和 file_read 查看相关代码，给出具体文件和行号。",
                "subagent_type": "general-purpose",
            },
        ),
    ],
    risk_level="L2",
    handler=task_handler,
    is_concurrency_safe=False,  # Agent calls have side effects
)

# ── All built-in tools list ───────────────────────────────────────────

BUILTIN_TOOLS: list[ToolDefinition] = [
    WEB_SEARCH,
    CURRENT_TIME,
    CURRENT_DATE,
    WEATHER,
    GIT_STATUS,
    GIT_DIFF,
    GIT_LOG,
    GIT_BRANCH,
    GIT_BRANCH_CREATE,
    GIT_COMMIT,
    GIT_REVERT,
    GIT_CHERRY_PICK,
    AST_SYMBOLS,
    TEST_DISCOVER,
    FORMATTER,
    TYPE_CHECK,
    PACKAGE_MANAGER,
    LOG_TAIL,
    PROCESS_LIST,
    PORT_CHECK,
    SERVICE_HEALTH,
    CHANGE_PLAN,
    AUDIT_REPORT,
    FILE_READ,
    FILE_WRITE,
    FILE_WRITE_BATCH,
    APPLY_CHANGE_SET,
    FILE_SEARCH,
    FILE_PATCH,
    CODE_EXECUTE,
    MEMORY_SEARCH,
    MEMORY_SAVE,
    MEMORY_RECALL,
    MEMORY_RETAIN,
    BROWSER_NAVIGATE,
    BROWSER_SCREENSHOT,
    BROWSER_EXTRACT,
    BROWSER_CLICK,
    BROWSER_TYPE,
    HTTP_REQUEST,
    SKILL_LIST,
    SKILL_LOAD,
    COMMAND_EXECUTE,
    INVOKE_AGENT,
    INVOKE_AGENTS_PARALLEL,
    ARTIFACT_LIST,
    ARTIFACT_READ,
    CONVERSATION_SEARCH,
    FILE_EDIT,
    FILE_GLOB,
    MKDIR,
    TASK,
]
