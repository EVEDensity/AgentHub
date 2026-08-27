"""
Guardrails Service — Safety Scanner + Risk Classifier

Two-tier protection for AgentHub:

  Tier 1 — Safety Redlines (auto-block):
    PII leaks, code/prompt injection, harmful content.
    These are detected via deterministic regex patterns and blocked
    before content reaches the agent or the user.

  Tier 2 — High-Risk Operation Approval (human confirm):
    File delete, code execution, deploy, payment operations.
    These pause the agent pipeline and require explicit user
    confirmation via WebSocket before proceeding.

Design principle: "宁可给一个有瑕疵的答案，也不要什么都不给"
— Block only when safety is at stake, not when quality is questionable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════════
# Types
# ═══════════════════════════════════════════════════════════════════════


class GuardrailCategory(str, Enum):
    PII = "pii"
    INJECTION = "injection"
    HARMFUL = "harmful"
    HIGH_RISK_OP = "high_risk_op"


class Severity(str, Enum):
    BLOCK = "block"        # auto-block — safety redline
    CONFIRM = "confirm"    # pause for human confirmation
    WARN = "warn"          # flag but don't interrupt


@dataclass
class GuardrailFlag:
    """A single guardrail finding."""
    category: GuardrailCategory
    severity: Severity
    rule: str              # rule name, e.g. "pii_credit_card"
    message: str           # human-readable description
    match_sample: str = "" # redacted sample of the matched text


@dataclass
class GuardrailResult:
    """Result of a guardrail scan."""
    passed: bool = True
    flags: list[GuardrailFlag] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(f.severity == Severity.BLOCK for f in self.flags)

    @property
    def requires_confirmation(self) -> bool:
        return any(f.severity == Severity.CONFIRM for f in self.flags)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.flags if f.severity == Severity.WARN)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "blocked": self.blocked,
            "requiresConfirmation": self.requires_confirmation,
            "warningCount": self.warning_count,
            "flags": [
                {
                    "category": f.category.value,
                    "severity": f.severity.value,
                    "rule": f.rule,
                    "message": f.message,
                }
                for f in self.flags
            ],
        }


# ═══════════════════════════════════════════════════════════════════════
# PII Detection Patterns (Tier 1 — auto-block)
# ═══════════════════════════════════════════════════════════════════════

PII_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    # (rule_name, description, pattern)
    (
        "pii_ssn",
        "社会安全号码 (SSN)",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    ),
    (
        "pii_credit_card",
        "信用卡号",
        re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
    ),
    (
        "pii_phone_cn",
        "中国手机号",
        re.compile(r"\b1[3-9]\d{9}\b"),
    ),
    (
        "pii_id_card_cn",
        "中国身份证号",
        re.compile(r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"),
    ),
    (
        "pii_email",
        "邮箱地址",
        re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"),
    ),
    (
        "pii_ipv4",
        "IPv4 地址",
        re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"),
    ),
    (
        "pii_api_key_openai",
        "OpenAI API Key",
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b"),
    ),
    (
        "pii_api_key_github",
        "GitHub Token",
        re.compile(r"\bgh[po]_[A-Za-z0-9]{36,}\b"),
    ),
    (
        "pii_aws_key",
        "AWS Access Key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    (
        "pii_jwt",
        "JWT Token",
        re.compile(r"\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\b"),
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Code / Prompt Injection Patterns (Tier 1 — auto-block)
# ═══════════════════════════════════════════════════════════════════════

INJECTION_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    (
        "injection_ignore_instructions",
        "提示注入：忽略指令",
        re.compile(
            r"(?:ignore|disregard|forget|override)\s+(?:all\s+)?(?:previous|above|prior|your)\s+(?:instructions?|prompts?|rules?|guidelines?)",
            re.IGNORECASE,
        ),
    ),
    (
        "injection_role_switch",
        "提示注入：角色切换",
        re.compile(
            r"(?:you\s+are\s+now|you're\s+now|act\s+as\s+(?:a\s+)?(?:different|new)|from\s+now\s+on\s+you\s+are)",
            re.IGNORECASE,
        ),
    ),
    (
        "injection_bypass_safety",
        "提示注入：绕过安全限制",
        re.compile(
            r"(?:bypass|disable|remove|strip|turn\s+off)\s+(?:your\s+)?(?:safety|content|ethical|security)\s+(?:filters?|guidelines?|restrictions?|protocols?)",
            re.IGNORECASE,
        ),
    ),
    (
        "injection_jailbreak",
        "提示注入：越狱尝试",
        re.compile(
            r"(?:DAN|jailbreak|developer\s*mode|god\s*mode|sudo\s*mode)",
            re.IGNORECASE,
        ),
    ),
    (
        "injection_system_cmd",
        "系统命令注入",
        re.compile(
            r"(?:;\s*(?:rm\s+-rf|mkfs\.|dd\s+if=|:\(\)\s*\{|chmod\s+777|wget\s+\S+\s+-O\s+|curl\s+\S+\s*\|\s*(?:ba)?sh))",
            re.IGNORECASE,
        ),
    ),
    (
        "injection_sql",
        "SQL 注入模式",
        re.compile(
            r"(?:'\s*OR\s+'1'='1|'\s*OR\s+1=1--|UNION\s+SELECT\s+(?:NULL|@@version)|DROP\s+TABLE\s+|--\s*[a-z_]+\s*=\s*)",
            re.IGNORECASE,
        ),
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Harmful Content Patterns (Tier 1 — auto-block)
# ═══════════════════════════════════════════════════════════════════════

HARMFUL_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    (
        "harmful_malware_gen",
        "恶意代码生成请求",
        re.compile(
            r"(?:write|create|generate|build|code\s+for)\s+(?:me\s+)?(?:a\s+)?(?:ransomware|malware|virus|trojan|worm|keylogger|rootkit|backdoor)",
            re.IGNORECASE,
        ),
    ),
    (
        "harmful_phishing",
        "钓鱼页面生成请求",
        re.compile(
            r"(?:create|make|build|generate)\s+(?:me\s+)?(?:a\s+)?(?:phishing|fake\s+login|credential\s+harvest)",
            re.IGNORECASE,
        ),
    ),
    (
        "harmful_doxxing",
        "人肉搜索/社工请求",
        re.compile(
            r"(?:find|get|look\s*up|search\s+for)\s+(?:someone's|their|his|her|personal)\s+(?:address|phone\s*number|social\s*security|private\s*information)",
            re.IGNORECASE,
        ),
    ),
    (
        "harmful_exploit_dev",
        "漏洞利用开发请求",
        re.compile(
            r"(?:write|develop|create)\s+(?:me\s+)?(?:an?\s+)?(?:exploit|zero[-\s]day|RCE|remote\s+code\s+execution|privilege\s+escalation)",
            re.IGNORECASE,
        ),
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# High-Risk Operations (Tier 2 — human confirm)
# ═══════════════════════════════════════════════════════════════════════

HIGH_RISK_TOOL_PATTERNS: dict[str, list[tuple[str, str, Severity]]] = {
    "file_write": [
        ("high_risk_delete_dir", "删除目录操作", Severity.CONFIRM),
        ("high_risk_system_file", "修改系统文件", Severity.CONFIRM),
    ],
    "file_delete": [
        ("high_risk_delete", "文件删除操作", Severity.CONFIRM),
    ],
    "code_execute": [
        ("high_risk_code_exec", "代码执行", Severity.CONFIRM),
    ],
    "command_execute": [
        ("high_risk_shell", "Shell 命令执行", Severity.CONFIRM),
    ],
}

# Args patterns that trigger confirmation even for tools usually L1/L2
HIGH_RISK_ARG_PATTERNS: dict[str, list[tuple[str, str, re.Pattern]]] = {
    "file_write": [
        (
            "high_risk_system_path",
            "操作系统关键路径",
            re.compile(r"^(?:/etc/|/boot/|/sys/|C:\\Windows\\System32|/System/Library/)"),
        ),
        (
            "high_risk_db_path",
            "数据库文件路径",
            re.compile(r".*\.(?:db|sqlite|sqlite3|mdb|frm|ibd)$", re.IGNORECASE),
        ),
    ],
    "code_execute": [
        (
            "high_risk_shell_cmd",
            "危险Shell命令",
            re.compile(
                r"(?:rm\s+-rf|sudo\s+|chmod\s+777|mkfs\.|dd\s+if=|>\/dev\/sda|format\s+[cdefg]:)",
                re.IGNORECASE,
            ),
        ),
        (
            "high_risk_network",
            "网络监听/扫描命令",
            re.compile(
                r"(?:nmap|tcpdump|wireshark|netcat|nc\s+-[ln]|iptables|ufw\s+disable)",
                re.IGNORECASE,
            ),
        ),
    ],
}


# ═══════════════════════════════════════════════════════════════════════
# Scanner
# ═══════════════════════════════════════════════════════════════════════

def _redact_match(match_text: str, max_len: int = 40) -> str:
    """Redact a matched string for safe display in guardrail messages."""
    if len(match_text) <= 8:
        return "***"
    return match_text[:4] + "***" + match_text[-4:]


def _scan_patterns(
    text: str,
    patterns: list[tuple[str, str, re.Pattern]],
    category: GuardrailCategory,
    severity: Severity,
) -> list[GuardrailFlag]:
    """Scan text against a list of compiled regex patterns."""
    flags: list[GuardrailFlag] = []
    seen_rules: set[str] = set()
    for rule_name, description, pattern in patterns:
        match = pattern.search(text)
        if match:
            if rule_name in seen_rules:
                continue
            seen_rules.add(rule_name)
            flags.append(GuardrailFlag(
                category=category,
                severity=severity,
                rule=rule_name,
                message=f"{description}: {_redact_match(match.group())}",
                match_sample=_redact_match(match.group()),
            ))
    return flags


def scan_input(text: str) -> GuardrailResult:
    """Scan user input for safety redlines before routing to agents.

    Checks:
      - PII leaks (SSN, credit cards, API keys, etc.) → BLOCK
      - Prompt/code injection attempts → BLOCK
      - Harmful content requests → BLOCK

    Returns a GuardrailResult. If blocked=True, the message MUST NOT
    reach the agent.
    """
    if not text or not text.strip():
        return GuardrailResult(passed=True)

    all_flags: list[GuardrailFlag] = []

    # Tier 1 — BLOCK on sight
    all_flags.extend(_scan_patterns(text, PII_PATTERNS, GuardrailCategory.PII, Severity.BLOCK))
    all_flags.extend(_scan_patterns(text, INJECTION_PATTERNS, GuardrailCategory.INJECTION, Severity.BLOCK))
    all_flags.extend(_scan_patterns(text, HARMFUL_PATTERNS, GuardrailCategory.HARMFUL, Severity.BLOCK))

    return GuardrailResult(
        passed=len(all_flags) == 0,
        flags=all_flags,
    )


def scan_output(text: str) -> GuardrailResult:
    """Scan agent output for safety redlines before sending to frontend.

    Currently checks for PII leaks only — the agent shouldn't be
    outputting SSNs or API keys in its responses.
    """
    if not text or not text.strip():
        return GuardrailResult(passed=True)

    all_flags: list[GuardrailFlag] = []
    all_flags.extend(_scan_patterns(text, PII_PATTERNS, GuardrailCategory.PII, Severity.BLOCK))

    return GuardrailResult(
        passed=len(all_flags) == 0,
        flags=all_flags,
    )


def classify_tool_risk(tool_name: str, arguments: dict[str, Any]) -> GuardrailResult:
    """Classify the risk level of a tool invocation.

    Returns a GuardrailResult indicating whether:
      - The operation is blocked (safety redline in arguments)
      - The operation requires user confirmation (high-risk tool)
      - The operation is safe to execute

    Design: Safe tools (file_read, web_search, memory_search) always
    pass. Write tools check their arguments for dangerous patterns.
    Execution tools always require confirmation.
    """
    all_flags: list[GuardrailFlag] = []

    # Check tool-level risk classification
    tool_patterns = HIGH_RISK_TOOL_PATTERNS.get(tool_name, [])
    for rule_name, description, severity in tool_patterns:
        all_flags.append(GuardrailFlag(
            category=GuardrailCategory.HIGH_RISK_OP,
            severity=severity,
            rule=rule_name,
            message=description,
        ))

    # Check argument-level risk patterns
    arg_patterns = HIGH_RISK_ARG_PATTERNS.get(tool_name, [])
    for rule_name, description, pattern in arg_patterns:
        # Check string arguments against dangerous patterns
        for key, value in arguments.items():
            if isinstance(value, str) and pattern.search(value):
                all_flags.append(GuardrailFlag(
                    category=GuardrailCategory.HIGH_RISK_OP,
                    severity=Severity.CONFIRM,
                    rule=rule_name,
                    message=f"{description}: {key}={_redact_match(value)}",
                ))
                break

    return GuardrailResult(
        passed=not any(f.severity == Severity.BLOCK for f in all_flags),
        flags=all_flags,
    )


# ═══════════════════════════════════════════════════════════════════════
# Multimodal hygiene (MM-5 / ADR-0105) — fail-closed image policy
# ═══════════════════════════════════════════════════════════════════════

def scan_image_source(uri: str) -> GuardrailResult:
    """Validate one image source against the multimodal policy.

    Delegates to the central validators (MIME whitelist, size caps, data-URI
    shape) so the policy has a single source of truth; a violation maps to a
    BLOCK flag naming the constraint. Audit events must carry only hash/
    size metadata — never the payload.
    """
    import hashlib

    from app.services.tools.multimodal.content_parts import validate_image_uri

    try:
        part = validate_image_uri(str(uri or ""))
    except Exception as exc:  # noqa: BLE001 — any policy violation blocks
        return GuardrailResult(passed=False, flags=[GuardrailFlag(
            category=GuardrailCategory.INJECTION,
            severity=Severity.BLOCK,
            rule="image_hygiene",
            message=f"image rejected by multimodal policy: {exc}",
        )])
    size = len(uri)
    digest = hashlib.sha256(uri.encode("utf-8", errors="ignore")).hexdigest()[:16]
    # informational only — WARN never blocks, audit keeps hash/size not payload
    return GuardrailResult(passed=True, flags=[GuardrailFlag(
        category=GuardrailCategory.INJECTION,
        severity=Severity.WARN,
        rule="image_hygiene_ok",
        message=f"image ok size={size} sha256[:16]={digest} mime={part.mime or 'remote-url'}",
    )])


def scan_multimodal_content(content: Any) -> GuardrailResult:
    """Scan a dual-track content value (str | parts list) for image hygiene."""
    if not isinstance(content, list):
        return GuardrailResult(passed=True)
    flags: list[GuardrailFlag] = []
    for index, part in enumerate(content):
        if isinstance(part, dict) and part.get("type") == "image_url":
            uri = str((part.get("image_url") or {}).get("url", ""))
            result = scan_image_source(uri)
            flags.extend(result.flags)
            if not result.passed:
                # tag which slot failed so callers can drop just that part
                flags[-1] = GuardrailFlag(
                    category=flags[-1].category,
                    severity=flags[-1].severity,
                    rule=f"{flags[-1].rule}@content[{index}]",
                    message=flags[-1].message,
                )
    return GuardrailResult(passed=all(f.severity != Severity.BLOCK for f in flags), flags=flags)


# ═══════════════════════════════════════════════════════════════════════
# Convenience: single-call safety check
# ═══════════════════════════════════════════════════════════════════════

def safety_check(text: str) -> GuardrailResult:
    """Run the full input safety scan. Alias for scan_input()."""
    return scan_input(text)


def tool_safety_check(tool_name: str, arguments: dict[str, Any]) -> GuardrailResult:
    """Run the full tool risk classification. Alias for classify_tool_risk()."""
    return classify_tool_risk(tool_name, arguments)
