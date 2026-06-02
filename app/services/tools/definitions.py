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
    code_execute_handler,
    memory_search_handler,
)
from app.services.tools.browser_tools import (
    browser_navigate_handler,
    browser_screenshot_handler,
)
from app.services.tools.skill_tools import (
    skill_list_handler,
    skill_load_handler,
    command_execute_handler,
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
    return_type='"文件内容文本" 或 目录列表，以及 metadata（total_lines, size_bytes 等）',
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
    description="将内容写入工作区的文件。可以创建新文件或追加到现有文件。用于保存代码、配置、文档等。",
    category="file",
    parameters=[
        ToolParameter(name="path", type="string", required=True,
                      description="文件相对路径（相对于项目根目录）"),
        ToolParameter(name="content", type="string", required=True,
                      description="要写入的完整文本内容"),
        ToolParameter(name="mode", type="string", required=False,
                      description="写入模式: 'overwrite' 覆写(默认) 或 'append' 追加", default="overwrite"),
    ],
    return_type='"写入结果描述文本" 及 metadata（path, size_bytes, mode）',
    examples=[
        ToolExample(
            user_question="帮我在 data 目录下创建一个 config.json 文件，内容是 {...}",
            parameters={"path": "data/config.json", "content": "{...}", "mode": "overwrite"},
        ),
    ],
    risk_level="L2",
    handler=file_write_handler,
    is_concurrency_safe=False,  # Has side effects, needs exclusivity
)

# ── code_execute ──────────────────────────────────────────────────────

CODE_EXECUTE = ToolDefinition(
    name="code_execute",
    description="在沙箱环境中执行 Python 或 Bash 代码。用于运行计算、数据处理、脚本测试等。有30秒超时限制。",
    category="code",
    parameters=[
        ToolParameter(name="code", type="string", required=True,
                      description="要执行的完整代码（Python 或 Bash 脚本）"),
        ToolParameter(name="language", type="string", required=False,
                      description="编程语言: 'python'(默认) 或 'bash'", default="python"),
        ToolParameter(name="timeout", type="number", required=False,
                      description="超时秒数，最大30秒", default=30),
    ],
    return_type='"标准输出和标准错误文本" 及 metadata（language, exit_code, timeout_seconds）',
    examples=[
        ToolExample(
            user_question="帮我计算 1 到 100 的和",
            parameters={"code": "print(sum(range(1, 101)))", "language": "python"},
        ),
        ToolExample(
            user_question="列出当前目录的文件",
            parameters={"code": "ls -la", "language": "bash"},
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

# ── All built-in tools list ───────────────────────────────────────────

BUILTIN_TOOLS: list[ToolDefinition] = [
    WEB_SEARCH,
    FILE_READ,
    FILE_WRITE,
    CODE_EXECUTE,
    MEMORY_SEARCH,
    BROWSER_NAVIGATE,
    BROWSER_SCREENSHOT,
    SKILL_LIST,
    SKILL_LOAD,
    COMMAND_EXECUTE,
]
