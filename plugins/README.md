# AgentHub 插件目录

本目录存放用户自定义的 AgentHub 工具系统插件。插件基于 [pluggy](https://pluggy.readthedocs.io/) 框架，通过实现 `@hookimpl` 方法注入工具执行生命周期。

## 快速开始

### 1. 加载示例插件

通过环境变量 `PLUGINS_PATH` 指向插件文件或目录：

```bash
# 加载单个文件
PLUGINS_PATH=plugins/example_plugin/plugin.py python -m uvicorn app.main:app

# 加载整个目录（扫描所有 *.py）
PLUGINS_PATH=plugins/ python -m uvicorn app.main:app
```

启动后日志会显示：
```
plugin_manager: registered user.ExamplePlugin from plugins/example_plugin/plugin.py
```

### 2. 编写自己的插件

创建一个 Python 文件，定义一个类，用 `@hookimpl` 装饰方法：

```python
# plugins/my_plugin/plugin.py
from app.services.tools.plugin_spec import hookimpl

class MyPlugin:
    @hookimpl
    def pre_tool_use(self, tool_name, arguments, context):
        if tool_name == "code_execute" and "rm -rf" in arguments.get("code", ""):
            return {"blocked": True, "reason": "dangerous command detected"}
        return None

    @hookimpl
    def post_tool_use(self, tool_name, arguments, result, context):
        print(f"tool {tool_name} finished, success={result.get('success')}")
        return None
```

然后用 `PLUGINS_PATH=plugins/my_plugin/plugin.py` 启动即可。

## 目录结构

```
plugins/
├── README.md              ← 本文件
└── example_plugin/
    ├── __init__.py
    └── plugin.py          ← ExamplePlugin: 统计工具调用次数
```

## 三种加载方式

| 方式 | 配置 | 适用场景 |
|---|---|---|
| 内置插件 | 放入 `app/services/tools/plugins/` | 随 AgentHub 发布的核心插件 |
| entry_point | `pyproject.toml` 声明 `agenthub.plugins` group | 第三方 pip 包 |
| 路径加载 | `PLUGINS_PATH` 环境变量 | 本地开发、临时插件、企业自定义 |

## 完整文档

详见 [docs/plugin-development.md](../docs/plugin-development.md)，包含：
- 4 个 hookspec 的完整签名与返回值契约
- hook 执行顺序与生命周期图
- 调试技巧（`plugin_manager.list_plugins()`、日志级别）
- 内置插件（audit / permission / sanitize）行为说明
