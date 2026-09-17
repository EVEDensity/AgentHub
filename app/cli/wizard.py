"""交互式 init 向导 — 选 provider → 填 API key → 开始用。

用法:
  python -m app.cli              # 无参数 → 自动引导 init 或进入 chat
  python -m app.cli init         # 同样触发交互式向导
  python -m app.cli init --provider deepseek --model deepseek-v4-flash
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# 可选依赖：prompt_toolkit 没装就用 input()
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter
    _HAS_PROMPT_TOOLKIT = True
except ImportError:
    _HAS_PROMPT_TOOLKIT = False


# 精选 provider 列表（按国内可用性排序）
_PROVIDER_CHOICES = [
    ("deepseek",   "DeepSeek (国产，推荐)",     "deepseek-v4-flash",  "DEEPSEEK_API_KEY"),
    ("qwen",       "阿里通义 (Qwen)",          "qwen-plus",          "DASHSCOPE_API_KEY"),
    ("zhipu",      "智谱 AI (GLM)",             "glm-4-plus",         "ZHIPUAI_API_KEY"),
    ("doubao",     "豆包 (字节跳动)",           "doubao-pro-32k",     "DOUBAO_API_KEY"),
    ("openai",     "OpenAI (国际)",             "gpt-4o-mini",        "OPENAI_API_KEY"),
    ("anthropic",  "Anthropic Claude (国际)",   "claude-sonnet-4-20250514", "ANTHROPIC_API_KEY"),
    ("minimax",    "MiniMax",                   "abab6.5s-chat",      "MINIMAX_API_KEY"),
    ("custom",     "自定义 OpenAI 兼容端点",     "your-model",         "CUSTOM_API_KEY"),
]


def _input_provider() -> tuple[str, str, str]:
    """交互式选择 provider → 返回 (provider, default_model, env_var_name)。"""
    print("\n  请选择大模型服务商：\n")
    for i, (provider, desc, model, env) in enumerate(_PROVIDER_CHOICES, 1):
        marker = " ← 推荐" if provider == "deepseek" else ""
        print(f"    {i}. {desc}  ({provider}){marker}")
    print()
    
    while True:
        try:
            choice = input("  输入编号 [1-8，默认 1]: ").strip()
            if not choice:
                choice = "1"
            idx = int(choice) - 1
            if 0 <= idx < len(_PROVIDER_CHOICES):
                p, desc, model, env = _PROVIDER_CHOICES[idx]
                print(f"  已选择: {desc}")
                return p, model, env
        except (ValueError, EOFError):
            pass
        print("  请输入 1-8 之间的数字")


def _normalize_model(name: str, provider: str) -> str:
    """规范化模型名：把空格→短横线，去首尾空白。
    DeepSeek 额外识别常见别名（v4-flash/coder/v4-pro）。"""
    name = name.strip()
    # 空格 → 短横线
    normalized = name.replace(" ", "-")
    
    # DeepSeek 常见别名 → 完整模型 ID
    if provider == "deepseek":
        alias_map = {
            "v4-flash": "deepseek-v4-flash",
            "v4-pro": "deepseek-v4-pro",
            "coder": "deepseek-coder",
            "chat": "deepseek-chat",
            "reasoner": "deepseek-reasoner",
        }
        # 用户可能输入 "deepseek v4-flash" 或 "v4-flash"
        if normalized in alias_map:
            normalized = alias_map[normalized]
        for alias, full in alias_map.items():
            if normalized == alias or normalized.endswith(f"-{alias}"):
                normalized = full
                break
    
    if normalized != name:
        print(f"  🛠️  规范化模型名: {name} → {normalized}")
    return normalized


def _input_model(default: str, provider: str) -> str:
    """交互式输入模型名（默认值可回车跳过，自动规范化）。"""
    try:
        val = input(f"  模型名 [默认 {default}]: ").strip()
        raw = val or default
    except EOFError:
        raw = default
    return _normalize_model(raw, provider)


def _input_api_key(env_var: str, provider: str) -> str:
    """交互式输入 API key（带占位提示）。"""
    # 先检查环境变量里有没有
    existing = os.environ.get(env_var, "").strip()
    if existing:
        masked = existing[:6] + "..." + existing[-4:]
        try:
            val = input(f"  检测到环境变量 {env_var}={masked}，回车保留 / 输入新 key: ").strip()
            return val or existing
        except EOFError:
            return existing
    
    try:
        val = input(f"  API Key (将存入环境变量 {env_var}): ").strip()
    except EOFError:
        val = ""
    
    if not val:
        print(f"  ⚠️  未输入 API key — 下次可用 `$env:{env_var}='sk-xxx'` 手动设置")
    return val


def _save_env_var(env_var: str, key: str, scope: str = "user") -> bool:
    """Windows: 存到用户级环境变量（注册表），下次新开终端自动生效。
    返回 True 表示存成功。"""
    if not key:
        return False
    if sys.platform != "win32":
        # POSIX: 只存当前进程，提示用户手动加 shell rc
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, env_var, 0, winreg.REG_SZ, key)
        return True
    except Exception:
        return False


def _setup_interactive(cwd: Path) -> int:
    """完整交互式引导：选 provider → 填 model → 填 API key → 写 config → 启动。"""
    from app.cli.runtime import state_dir, CONFIG_FILE_NAME, EXIT_OK, EXIT_INFRA_ERROR
    import json
    
    print()
    print("  ╭───────────────────────────────────────╮")
    print("  │  AgentHub — 首次启动向导               │")
    print("  │  选择大模型服务商，开始你的第一个任务   │")
    print("  ╰───────────────────────────────────────╯")
    
    # 1. 选 provider
    provider, default_model, env_var = _input_provider()
    
    # 2. 自定义端点时额外问 base_url
    base_url = None
    if provider == "custom":
        try:
            base_url = input("  API Base URL (如 https://api.deepseek.com/v1): ").strip()
        except EOFError:
            pass
    
    # 3. 模型名
    model = _input_model(default_model, provider)
    
    # 4. API key
    api_key = _input_api_key(env_var, provider)
    
    # 5. 存环境变量（进程内 + 持久化）
    if api_key:
        os.environ[env_var] = api_key
        saved = _save_env_var(env_var, api_key)
        if saved:
            print(f"  ✅ API key 已保存到用户环境变量 {env_var}")
        else:
            print(f"  ⚠️  API key 仅在当前终端有效（{env_var}）")
            if sys.platform == "win32":
                print(f"      持久化失败 — 请手动执行: [Environment]::SetEnvironmentVariable('{env_var}', 'sk-xxx', 'User')")
    
    # 6. 写 config.json（只写 provider/model，不写 key）
    directory = state_dir(cwd)
    directory.mkdir(parents=True, exist_ok=True)
    
    config: dict[str, Any] = {"provider": provider, "model": model}
    if base_url:
        config["base_url"] = base_url
    config_path = directory / CONFIG_FILE_NAME
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    
    print()
    print(f"  ✅ 配置已保存: {config_path}")
    print(f"     provider : {provider}")
    print(f"     model    : {model}")
    print(f"     workspace: {cwd}")
    if not api_key:
        print(f"     ⚠️  缺少 API key — 设置后才能真实调用 LLM")
    print()
    print("  下一步:")
    print("    agenthub exec \"你的任务描述\"    # 跑一个任务")
    print("    agenthub                        # 进入交互式 chat")
    print("    agenthub chat                   # 同上")
    print()
    
    return EXIT_OK


def _has_config(cwd: Path) -> bool:
    """检查 .agenthub/config.json 是否存在。"""
    from app.cli.runtime import state_dir, CONFIG_FILE_NAME
    return (state_dir(cwd) / CONFIG_FILE_NAME).is_file()


def _config_has_key_in_env(cwd: Path) -> bool:
    """检查 config 里配置的 provider 对应的 env_var 是否有值。"""
    import json
    from app.cli.runtime import state_dir, CONFIG_FILE_NAME
    cfg_path = state_dir(cwd) / CONFIG_FILE_NAME
    if not cfg_path.is_file():
        return False
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        provider = cfg.get("provider", "")
    except Exception:
        return False
    # 找对应的 env_var
    for p, _, _, env in _PROVIDER_CHOICES:
        if p == provider:
            return bool(os.environ.get(env, "").strip())
    # 兜底：检查通用的
    return bool(os.environ.get("AGENTHUB_CLI_MODEL_API_KEY", "").strip())


def maybe_launch_wizard(cwd: Path) -> bool:
    """如果没有配置或没有 API key，就启动交互式向导。
    返回 True 表示向导已处理（调用方应该退出），False 表示已配置好可以继续。"""
    has_cfg = _has_config(cwd)
    has_key = _config_has_key_in_env(cwd) if has_cfg else False
    
    if not has_cfg or not has_key:
        # 问用户要不要引导
        try:
            if not has_cfg:
                print("  未检测到配置，进入首次启动向导…")
            else:
                print("  配置存在但 API key 未设置，重新引导…")
            _setup_interactive(cwd)
            return True
        except (EOFError, KeyboardInterrupt):
            print("\n  已取消。可用 `agenthub init --provider deepseek --model deepseek-chat` 手动配置。")
            return True
    return False
