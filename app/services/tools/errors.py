from __future__ import annotations

from enum import Enum
from typing import Any
from app.errors import ErrorEnvelope, error_envelope


class ToolErrorType(str, Enum):
    """Classification of tool execution errors.

    Modeled on the classifyToolError function from
    FUNCTION_CALLING_IMPLEMENTATION.md §3.3.2.
    """
    VALIDATION = "validation"    # Missing params, bad types, file not found
    PERMISSION = "permission"    # Access denied, path outside workspace
    EXECUTION = "execution"      # Runtime failure (subprocess crash, etc.)
    TIMEOUT = "timeout"          # Execution exceeded time limit


class TelemetrySafeError(Exception):
    """Error wrapper that separates user-safe message from internal details.

    The ``safe_message`` is shown to the user. The ``internal_detail``
    is logged for debugging but never exposed externally.

    Modeled on FUNCTION_CALLING_IMPLEMENTATION.md §3.3.2 line 349-362.
    """

    def __init__(self, safe_message: str, internal_detail: str = "") -> None:
        self.safe_message = safe_message
        self.internal_detail = internal_detail
        super().__init__(safe_message)


# ── errno-code classification ────────────────────────────────────────
# Maps OS/Python errno codes and common exception patterns to
# ToolErrorType for consistent error reporting.

ERRNO_CLASSIFICATION: dict[str, ToolErrorType] = {
    # File/directory errors
    "ENOENT": ToolErrorType.VALIDATION,    # File not found
    "EACCES": ToolErrorType.PERMISSION,    # Permission denied
    "EPERM": ToolErrorType.PERMISSION,     # Operation not permitted
    "EISDIR": ToolErrorType.VALIDATION,    # Is a directory
    "ENOTDIR": ToolErrorType.VALIDATION,   # Not a directory
    "EEXIST": ToolErrorType.EXECUTION,     # File already exists
    "ENOSPC": ToolErrorType.EXECUTION,     # No space left on device
    "ENAMETOOLONG": ToolErrorType.VALIDATION,  # Filename too long
    "EROFS": ToolErrorType.PERMISSION,     # Read-only file system

    # Network errors
    "ECONNREFUSED": ToolErrorType.EXECUTION,
    "ECONNRESET": ToolErrorType.EXECUTION,
    "ENETUNREACH": ToolErrorType.EXECUTION,
    "ETIMEDOUT": ToolErrorType.TIMEOUT,

    # Process errors
    "ETIME": ToolErrorType.TIMEOUT,        # Timer expired
}

# Exception class name patterns that indicate specific error types
EXCEPTION_CLASSIFICATION: dict[str, ToolErrorType] = {
    "ValidationError": ToolErrorType.VALIDATION,
    "ValueError": ToolErrorType.VALIDATION,
    "TypeError": ToolErrorType.VALIDATION,
    "KeyError": ToolErrorType.VALIDATION,
    "PermissionError": ToolErrorType.PERMISSION,
    "FileNotFoundError": ToolErrorType.VALIDATION,
    "NotADirectoryError": ToolErrorType.VALIDATION,
    "IsADirectoryError": ToolErrorType.VALIDATION,
    "TimeoutError": ToolErrorType.TIMEOUT,
    "asyncio.TimeoutError": ToolErrorType.TIMEOUT,
    "subprocess.TimeoutExpired": ToolErrorType.TIMEOUT,
    "RuntimeError": ToolErrorType.EXECUTION,
    "OSError": ToolErrorType.EXECUTION,
    "ConnectionError": ToolErrorType.EXECUTION,
    "HTTPError": ToolErrorType.EXECUTION,
    "httpx.TimeoutException": ToolErrorType.TIMEOUT,
    "LLMAdapterError": ToolErrorType.EXECUTION,
}


def get_errno_code(error: Exception) -> str | None:
    """Extract the errno code from an OSError, if available."""
    if isinstance(error, OSError):
        if error.errno is not None:
            import errno as _errno
            try:
                return _errno.errorcode.get(error.errno)
            except (KeyError, TypeError):
                pass
    return None


def classify_tool_error(
    error: Exception,
    tool_name: str = "",
) -> tuple[ToolErrorType, str]:
    """Classify an error from tool execution into one of four types.

    Returns ``(error_type, safe_message_for_user)``.

    Modeled on FUNCTION_CALLING_IMPLEMENTATION.md §3.3.2:
    classifyToolError function.

    Classification priority:
      1. TelemetrySafeError → use its safe_message directly
      2. OSError with errno → use ERRNO_CLASSIFICATION mapping
      3. Exception class name → use EXCEPTION_CLASSIFICATION mapping
      4. Fallback → EXECUTION with generic message
    """
    # ── 1. TelemetrySafeError ───────────────────────────────────────
    if isinstance(error, TelemetrySafeError):
        # Determine the type from internal detail pattern
        errno_code = get_errno_code(error) if isinstance(error, OSError) else None  # Won't trigger for TelemetrySafeError
        error_type = ToolErrorType.EXECUTION
        # Check the safe message for permission-related wording
        msg_lower = error.safe_message.lower()
        if any(w in msg_lower for w in ("permission", "denied", "授权", "拒绝", "超出", "路径")):
            error_type = ToolErrorType.PERMISSION
        elif any(w in msg_lower for w in ("not found", "不存在", "missing", "缺少", "invalid", "无效")):
            error_type = ToolErrorType.VALIDATION
        elif any(w in msg_lower for w in ("timeout", "超时", "timed out")):
            error_type = ToolErrorType.TIMEOUT
        return error_type, error.safe_message

    # ── 2. OSError with errno code ─────────────────────────────────
    errno_code = get_errno_code(error)
    if errno_code:
        error_type = ERRNO_CLASSIFICATION.get(errno_code, ToolErrorType.EXECUTION)
        return error_type, f"工具 '{tool_name}' 执行错误 ({errno_code}): {error}"

    # ── 3. Exception class name matching ────────────────────────────
    # Check the full class hierarchy
    exc_type = type(error)
    for cls_name, error_type in EXCEPTION_CLASSIFICATION.items():
        if exc_type.__name__ == cls_name:
            return error_type, f"工具 '{tool_name}' 错误: {error}"
        # Also check qualified name (e.g. asyncio.TimeoutError)
        qual_name = getattr(exc_type, "__qualname__", "")
        if qual_name == cls_name:
            return error_type, f"工具 '{tool_name}' 错误: {error}"

    # Also check base classes
    for base in exc_type.__mro__:
        if base.__name__ in EXCEPTION_CLASSIFICATION:
            error_type = EXCEPTION_CLASSIFICATION[base.__name__]
            return error_type, f"工具 '{tool_name}' 错误: {error}"

    # ── 4. Fallback ─────────────────────────────────────────────────
    error_msg = str(error)[:200] if str(error) else f"{type(error).__name__}"
    return ToolErrorType.EXECUTION, f"工具 '{tool_name}' 执行异常: {error_msg}"


def wrap_tool_error(
    tool_name: str,
    error: Exception,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Enrich a failed tool result with classified error information.

    Adds ``error_type`` and a user-safe ``error`` message to the result dict.
    The original ``error`` field (stack trace / internal detail) is preserved
    under ``error_detail`` for logging.
    """
    error_type, safe_message = classify_tool_error(error, tool_name)
    envelope = error_envelope(error, message=safe_message)
    # Preserve the stable top-level envelope while retaining legacy aliases.
    result["success"] = False
    result.update(envelope.to_dict())
    result["error_type"] = envelope.error_type
    result["error"] = envelope.message
    result["error_detail"] = str(error)[:500]
    return result
