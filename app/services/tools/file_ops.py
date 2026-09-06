"""File-operation builtin tools (read, write, search, patch, edit,
glob, mkdir, write_batch).

Split out of ``builtin_tools.py`` — three original code blocks were
recombined into this single module.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from app.services.tools._common import (
    MAX_FILE_READ_BYTES,
    _auto_git_commit,
    _broadcast_workspace_change,
    _compute_unified_diff,
    file_sha256,
    validate_expected_sha256,
    _get_sid_fast,
    _get_uid_fast,
)
from app.utils.async_file import (
    aexists,
    aisfile,
    aisdir,
    aread_text,
    awrite_text,
    astat_size,
    aiterdir,
    amkdir,
)

logger = logging.getLogger("agenthub.tools.builtin.file_ops")

async def file_read_handler(path: str, encoding: str = "utf-8", max_lines: int = 500) -> dict[str, Any]:
    """Read a file from the user's per-session workspace."""
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    ws_root = get_workspace_root()
    safe = resolve_workspace_path(path)
    if safe is None:
        return {"success": False, "error": f"路径 '{path}' 超出工作区允许范围"}

    if not await aexists(safe):
        parent = safe.parent
        similar: list[str] = []
        try:
            name_lower = safe.name.lower()
            for child in (await aiterdir(parent))[:20]:
                if await aisfile(child) and name_lower[:4] in child.name.lower():
                    similar.append(str(child.relative_to(ws_root)))
        except OSError:
            pass
        hint = f"\n目录 '{parent.relative_to(ws_root)}' 中相似文件: {similar}" if similar else ""
        return {"success": False, "error": f"文件不存在: {path}{hint}"}

    if await aisdir(safe):
        try:
            listing = (await aiterdir(safe))[:50]
            names = [str(p.relative_to(ws_root)) + ("/" if await aisdir(p) else "") for p in listing]
            return {
                "success": True,
                "result": f"目录 '{path}' 内容 ({len(names)} 项):\n" + "\n".join(names),
            }
        except OSError as exc:
            return {"success": False, "error": f"无法列出目录: {exc}"}

    # Check file size
    try:
        size = await astat_size(safe)
        if size > MAX_FILE_READ_BYTES:
            return {
                "success": False,
                "error": f"文件过大 ({size / 1024 / 1024:.1f}MB)。最大允许: {MAX_FILE_READ_BYTES / 1024 / 1024:.0f}MB",
            }
    except OSError as exc:
        return {"success": False, "error": f"无法读取文件信息: {exc}"}

    try:
        content = await aread_text(safe, encoding=encoding)
        lines = content.split("\n")
        total_lines = len(lines)
        truncated = lines[:min(max_lines, len(lines))]
        result_text = "\n".join(truncated)

        if total_lines > max_lines:
            result_text += f"\n\n... [已截断，显示前 {max_lines} 行，共 {total_lines} 行]"

        # ── Record version hash for conflict detection ──────────────────
        sha256_hash = ""
        try:
            from app.services.file_version_tracker import file_version_tracker
            sid = _get_sid_fast()
            uid = _get_uid_fast()
            fv = file_version_tracker.record_read(sid, path, content, uid)
            sha256_hash = file_sha256(safe) or fv.sha256
        # noqa: BLE001 - file-version tracking is best-effort, never block write
        except Exception:
            pass

        return {
            "success": True,
            "result": result_text,
            "metadata": {
                "path": str(safe.relative_to(ws_root)),
                "total_lines": total_lines,
                "displayed_lines": len(truncated),
                "size_bytes": size,
                "encoding": encoding,
                "sha256": sha256_hash,
            },
        }
    except UnicodeDecodeError:
        return {"success": False, "error": f"文件不是有效的 {encoding} 文本文件，可能是二进制文件"}
    except OSError as exc:
        return {"success": False, "error": f"读取文件失败: {exc}"}


# ── file_write ────────────────────────────────────────────────────────

async def file_write_handler(
    path: str,
    content: str,
    mode: str = "overwrite",
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Write content to a file in the user's per-session workspace.

    Args:
        path: Relative path within the session workspace.
        content: The text content to write.
        mode: "overwrite" (default) replaces the file; "append" adds to the end.
        expected_sha256: Optional hash from a prior ``file_read`` call.
            When provided, conflict detection compares it against the
            tracked version and warns if another user modified the file
            in the meantime.
    """
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    ws_root = get_workspace_root()
    safe = resolve_workspace_path(path)
    if safe is None:
        return {"success": False, "error": f"路径 '{path}' 超出工作区允许范围"}

    expected_ok, expected_error = validate_expected_sha256(safe, expected_sha256)
    if not expected_ok:
        return {"success": False, "error": expected_error, "error_type": "conflict"}

    if await aexists(safe) and await aisdir(safe):
        return {"success": False, "error": f"'{path}' 是一个目录，无法写入"}

    # ── Context ─────────────────────────────────────────────────────────
    sid = _get_sid_fast()
    uid = _get_uid_fast()

    # ── Pre-read original content (for diff + conflict + backup) ────────
    original_text = ""
    if await aexists(safe):
        try:
            original_text = await aread_text(safe, encoding="utf-8")
        except UnicodeDecodeError:
            original_text = ""

    # The filesystem hash gate above is authoritative.  In-memory version
    # tracking remains useful for attribution, but must never downgrade a
    # detected conflict into an advisory warning.
    conflict_warning = ""

    # ── Advisory locking ────────────────────────────────────────────────
    lock_acquired = False
    try:
        from app.services.file_lock import file_lock_manager
        lock_result = file_lock_manager.acquire(sid, path, uid)
        lock_acquired = lock_result["ok"]
        if not lock_result["ok"]:
            existing_lock = lock_result["lock"]
            if not conflict_warning:
                conflict_warning = ""
            conflict_warning += (
                f" 🔒 文件被 {existing_lock.holder_name or existing_lock.holder_user_id} 锁定"
                f"（{existing_lock.remaining_seconds:.0f}秒后过期）。"
            )
    # noqa: BLE001 - file-version tracking is best-effort, never block write
    except Exception:
        pass

    # ── Write ───────────────────────────────────────────────────────────
    try:
        await amkdir(safe.parent)

        if mode == "append" and original_text:
            new_full = original_text + "\n" + content
            await awrite_text(safe, new_full, encoding="utf-8")
            action = "追加"
        else:
            await awrite_text(safe, content, encoding="utf-8")
            action = "覆写"

        size = await astat_size(safe)

        # ── Post-write: track version ───────────────────────────────────
        sha256_hash = ""
        try:
            from app.services.file_version_tracker import file_version_tracker
            fv = file_version_tracker.record_write(
                sid, path,
                content if mode == "overwrite" else (original_text + "\n" + content),
                uid, "",
            )
            sha256_hash = file_sha256(safe) or fv.sha256
        # noqa: BLE001 - file-version tracking is best-effort, never block write
        except Exception:
            pass

        # ── Post-write: broadcast workspace_change ──────────────────────
        diff_preview = ""
        if mode == "overwrite" and original_text:
            diff_preview = _compute_unified_diff(original_text, content, path)
        # Fire-and-forget — don't await, don't block
        import asyncio as _asyncio
        _asyncio.ensure_future(
            _broadcast_workspace_change(sid, path, "write", size, diff_preview, user_id=uid)
        )

        # ── Post-write: auto git commit ─────────────────────────────────

        # ── Build result ────────────────────────────────────────────────
        result_msg = f"文件 '{path}' {action}成功 ({size} 字节)"
        if conflict_warning:
            result_msg = conflict_warning + "\n" + result_msg

        metadata: dict[str, Any] = {
            "path": str(safe.relative_to(ws_root)),
            "size_bytes": size,
            "mode": mode,
            "sha256": sha256_hash if sha256_hash else "",
        }
        if conflict_warning:
            metadata["conflict"] = True
        if lock_acquired:
            metadata["lock_held"] = True

        return {"success": True, "result": result_msg, "metadata": metadata}
    except OSError as exc:
        return {"success": False, "error": f"写入文件失败: {exc}"}
    finally:
        # ── Release lock ────────────────────────────────────────────────
        if lock_acquired:
            try:
                from app.services.file_lock import file_lock_manager
                file_lock_manager.release(sid, path, uid)
            # noqa: BLE001 - file-version tracking is best-effort, never block write
            except Exception:
                pass


# ── file_write_batch ──────────────────────────────────────────────────

async def file_write_batch_handler(
    paths_contents: list[dict[str, str]],
) -> dict[str, Any]:
    """Write multiple files to the workspace in a single call.

    Creates parent directories automatically (acts as mkdir -p for each
    file's parent path).  Each item in *paths_contents* must have:

        - ``path`` (str, required) — relative path within the workspace
        - ``content`` (str, required) — text content to write

    Example::

        [
            {"path": "src/main.py", "content": "print('hello')"},
            {"path": "src/utils/helpers.py", "content": "def add(a,b): return a+b"},
        ]
    """
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    if not isinstance(paths_contents, list) or len(paths_contents) == 0:
        return {"success": False, "error": "paths_contents 必须是非空数组，每项包含 path 和 content 字段"}

    ws_root = get_workspace_root()
    sid = _get_sid_fast()
    uid = _get_uid_fast()

    results: list[dict] = []
    created_dirs: set[str] = set()

    for i, item in enumerate(paths_contents):
        if not isinstance(item, dict):
            results.append({"index": i, "success": False, "error": "数组项必须是对象 {path, content}"})
            continue

        path = item.get("path", "")
        content = item.get("content", "")
        if not path:
            results.append({"index": i, "success": False, "error": "缺少必填字段 path"})
            continue
        if not isinstance(content, str):
            results.append({"index": i, "success": False, "error": "content 必须是字符串"})
            continue

        safe = resolve_workspace_path(path)
        if safe is None:
            results.append({"index": i, "path": path, "success": False, "error": f"路径 '{path}' 超出工作区允许范围"})
            continue

        if await aexists(safe) and await aisdir(safe):
            results.append({"index": i, "path": path, "success": False, "error": f"'{path}' 是一个目录，无法作为文件写入"})
            continue

        # ── Auto-create parent directories (folder creation) ──────────
        parent = safe.parent
        parent_rel = str(parent.relative_to(ws_root))
        try:
            if not await aexists(parent):
                await amkdir(parent)
                if parent_rel not in created_dirs:
                    created_dirs.add(parent_rel)
        except OSError as exc:
            results.append({"index": i, "path": path, "success": False, "error": f"无法创建目录 '{parent_rel}': {exc}"})
            continue

        # ── Pre-read original for diff (if overwriting) ───────────────
        original_text = ""
        if await aexists(safe):
            try:
                original_text = await aread_text(safe, encoding="utf-8")
            except UnicodeDecodeError:
                original_text = ""

        # ── Write ─────────────────────────────────────────────────────
        try:
            await awrite_text(safe, content, encoding="utf-8")
            size = await astat_size(safe)
        except OSError as exc:
            results.append({"index": i, "path": path, "success": False, "error": f"写入失败: {exc}"})
            continue

        # ── Post-write: track version ─────────────────────────────────
        try:
            from app.services.file_version_tracker import file_version_tracker
            file_version_tracker.record_write(sid, path, content, uid, "")
        # noqa: BLE001 - file-version tracking is best-effort, never block write
        except Exception:
            pass

        # ── Post-write: broadcast + git (fire-and-forget) ────────────
        diff_preview = ""
        if original_text:
            diff_preview = _compute_unified_diff(original_text, content, path)
        import asyncio as _asyncio
        _asyncio.ensure_future(
            _broadcast_workspace_change(sid, path, "write", size, diff_preview, user_id=uid)
        )

        results.append({
            "index": i,
            "path": path,
            "success": True,
            "result": f"'{path}' 写入成功 ({size} 字节)",
            "metadata": {"path": path, "size_bytes": size},
        })

    # ── Summary ───────────────────────────────────────────────────────
    success_count = sum(1 for r in results if r.get("success"))
    fail_count = len(results) - success_count

    summary_parts: list[str] = [
        f"批量写入完成: {success_count}/{len(results)} 成功",
    ]
    if fail_count > 0:
        failed_paths = [r.get("path", f"index {r.get('index')}") for r in results if not r.get("success")]
        summary_parts.append(f"，{fail_count} 失败: {', '.join(failed_paths[:5])}")
    if created_dirs:
        summary_parts.append(f"。自动创建目录: {', '.join(sorted(created_dirs)[:10])}")

    return {
        "success": fail_count == 0,
        "result": "".join(summary_parts),
        "metadata": {
            "total": len(results),
            "success_count": success_count,
            "fail_count": fail_count,
            "created_dirs": sorted(created_dirs),
            "files": results,
        },
    }


# ── code_execute ──────────────────────────────────────────────────────


async def file_search_handler(
    pattern: str,
    path: str = ".",
    glob: str = "*",
    max_results: int = 30,
    context_lines: int = 2,
    ignore_case: bool = True,
) -> dict[str, Any]:
    """Search file contents using regex pattern (grep-like).

    Walks the workspace directory tree, filters files by glob pattern,
    and searches each file's content for matches.  Returns matching lines
    with file path, line number, and surrounding context.

    Args:
        pattern: Regex pattern to search for.
        path: Relative directory path to search (default: workspace root).
        glob: File glob filter (e.g. ``*.py``, ``*.{ts,tsx}``).
        max_results: Maximum number of matches to return.
        context_lines: Lines of context before/after each match.
        ignore_case: Case-insensitive matching (default True).
    """
    import re as _re
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    if not pattern or not pattern.strip():
        return {"success": False, "error": "搜索模式不能为空"}

    pattern = pattern.strip()
    ws_root = get_workspace_root()
    search_path = resolve_workspace_path(path)
    if search_path is None:
        return {"success": False, "error": f"路径 '{path}' 超出工作区允许范围"}

    if not await aisdir(search_path):
        return {"success": False, "error": f"目录不存在: {path}"}

    # Build glob pattern
    import fnmatch as _fnmatch
    glob_parts = [g.strip() for g in glob.split(",") if g.strip()]

    # Compile regex
    try:
        flags = _re.IGNORECASE if ignore_case else 0
        regex = _re.compile(pattern, flags)
    except _re.error as exc:
        return {"success": False, "error": f"正则表达式无效: {exc}"}

    matches: list[dict[str, Any]] = []
    scanned_files = 0
    max_files = 200  # safety limit

    try:
        for root_dir, dirs, files in _walk_sync(search_path):
            # Skip hidden dirs
            dirs[:] = [d for d in dirs if not d.startswith(".")]

            for fname in files:
                if fname.startswith("."):
                    continue
                if max_files <= 0:
                    break
                max_files -= 1

                # Glob filter
                if glob_parts and not any(_fnmatch.fnmatch(fname, gp) for gp in glob_parts):
                    continue

                file_path = Path(root_dir) / fname
                # Skip binary/large files
                try:
                    size = file_path.stat().st_size
                    if size > 500_000:  # 500KB limit
                        continue
                except OSError:
                    continue

                scanned_files += 1
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                # noqa: BLE001 - file-version tracking is best-effort, never block write
                except Exception:
                    continue

                lines = content.split("\n")
                for i, line in enumerate(lines):
                    if regex.search(line):
                        ctx_start = max(0, i - context_lines)
                        ctx_end = min(len(lines), i + context_lines + 1)
                        context_block = "\n".join(
                            f"{j+1}: {lines[j]}" for j in range(ctx_start, ctx_end)
                        )
                        matches.append({
                            "file": str(file_path.relative_to(ws_root)),
                            "line": i + 1,
                            "match": line.strip()[:200],
                            "context": context_block[:800],
                        })
                        if len(matches) >= max_results:
                            break
                if len(matches) >= max_results:
                    break
            if max_files <= 0 or len(matches) >= max_results:
                break
    except Exception as exc:
        logger.exception("file_search walk failed")
        return {"success": False, "error": f"搜索文件失败: {exc}"}

    return {
        "success": True,
        "result": {
            "pattern": pattern,
            "path": str(search_path.relative_to(ws_root)),
            "matches": matches,
            "total_matches": len(matches),
            "scanned_files": scanned_files,
        },
    }


def _walk_sync(root: Path) -> Any:
    """Synchronous walk wrapper — simple implementation."""
    import os as _os
    for dirpath_str, dirnames, filenames in _os.walk(str(root)):
        dirpath = Path(dirpath_str)
        yield dirpath, dirnames, filenames


# ── file_patch ──────────────────────────────────────────────────────────

async def file_patch_handler(
    path: str,
    diff: str,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Apply a unified diff patch to a file.

    Supports the standard unified diff format (output from ``diff -u``,
    ``git diff``, etc.).  Each hunk header ``@@ -a,n +b,m @@`` is parsed
    and applied to the target file.

    Args:
        path: Relative path to the file to patch (within workspace).
        diff: Unified diff text (one or more hunks).

    Returns:
        Result with patched content preview and change summary.
    """
    import re as _re
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    if not path or not path.strip():
        return {"success": False, "error": "文件路径不能为空"}
    if not diff or not diff.strip():
        return {"success": False, "error": "diff 内容不能为空"}

    ws_root = get_workspace_root()
    safe = resolve_workspace_path(path)
    if safe is None:
        return {"success": False, "error": f"路径 '{path}' 超出工作区允许范围"}

    expected_ok, expected_error = validate_expected_sha256(safe, expected_sha256)
    if not expected_ok:
        return {"success": False, "error": expected_error, "error_type": "conflict"}

    # Read original file
    if not await aexists(safe):
        return {"success": False, "error": f"文件不存在: {path}"}

    try:
        original = await aread_text(safe, encoding="utf-8")
    except UnicodeDecodeError:
        return {"success": False, "error": f"文件不是有效的 UTF-8 文本文件"}

    original_lines = original.split("\n")

    # Parse diff hunks
    hunk_pattern = _re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
    hunks: list[dict] = []
    current_hunk = None
    lines_consumed = 0

    for line in diff.splitlines():
        m = hunk_pattern.match(line)
        if m:
            if current_hunk:
                hunks.append(current_hunk)
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) else 1
            current_hunk = {
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "context": m.group(5).strip(),
                "edits": [],
            }
            lines_consumed = 0
        elif current_hunk is not None:
            if line.startswith(" ") or line == "" or (not line.startswith(("+", "-"))):
                # Context line
                current_hunk["edits"].append(("context", line[1:] if line.startswith(" ") else line))
                lines_consumed += 1
            elif line.startswith("-"):
                current_hunk["edits"].append(("remove", line[1:]))
            elif line.startswith("+"):
                current_hunk["edits"].append(("add", line[1:]))
            # Skip other lines (e.g. "\ No newline at end of file")

    if current_hunk:
        hunks.append(current_hunk)

    if not hunks:
        return {"success": False, "error": "无法解析 diff，未找到有效的 hunk 头（@@ 行）"}

    # Apply hunks (in reverse order to preserve line numbers)
    result_lines = list(original_lines)
    added = 0
    removed = 0

    for hunk in reversed(hunks):
        old_start = hunk["old_start"] - 1  # 0-indexed

        # Calculate how many old lines this hunk covers
        old_line_count = sum(1 for e in hunk["edits"] if e[0] in ("context", "remove"))

        if old_start > len(result_lines):
            continue

        # Validate every context/removal line against the current file before
        # applying.  A stale patch must fail closed instead of replacing by
        # line number and silently corrupting unrelated code.
        old_segment = [text for action, text in hunk["edits"] if action in ("context", "remove")]
        if result_lines[old_start:old_start + len(old_segment)] != old_segment:
            return {"success": False, "error": f"补丁上下文不匹配，文件可能已被修改: {path}", "error_type": "conflict"}
        if hunk["old_count"] != len(old_segment):
            return {"success": False, "error": "补丁 old 行数与 hunk 内容不一致", "error_type": "protocol"}

        # Build replacement lines
        replacement: list[str] = []
        for action, text in hunk["edits"]:
            if action in ("context", "add"):
                replacement.append(text)
                if action == "add":
                    added += 1
            # "remove" lines are skipped
            if action == "remove":
                removed += 1

        if hunk["new_count"] != len(replacement):
            return {"success": False, "error": "补丁 new 行数与 hunk 内容不一致", "error_type": "protocol"}

        # Replace the hunk segment
        result_lines[old_start:old_start + old_line_count] = replacement

    patched = "\n".join(result_lines)

    # ── Write the patched file ──────────────────────────────────────────
    try:
        await awrite_text(safe, patched, encoding="utf-8")
    except OSError as exc:
        return {"success": False, "error": f"写入补丁文件失败: {exc}"}

    # ── Post-patch: track version, broadcast, git ──────────────────────
    sid = _get_sid_fast()
    uid = _get_uid_fast()

    # Record version
    sha256_hash = ""
    try:
        from app.services.file_version_tracker import file_version_tracker
        fv = file_version_tracker.record_write(sid, path, patched, uid, "")
        sha256_hash = file_sha256(safe) or fv.sha256
    # noqa: BLE001 - file-version tracking is best-effort, never block write
    except Exception:
        pass

    # Broadcast
    size = len(patched.encode("utf-8"))
    import asyncio as _asyncio
    _asyncio.ensure_future(
        _broadcast_workspace_change(sid, path, "write", size, diff, user_id=uid)
    )

    # Preview
    preview = patched[:2000]
    if len(patched) > 2000:
        preview += "\n\n... [已截断]"

    return {
        "success": True,
        "result": f"补丁应用成功。{added} 行新增，{removed} 行删除。\n\n[文件预览]\n{preview}",
        "metadata": {
            "path": str(safe.relative_to(ws_root)),
            "lines_added": added,
            "lines_removed": removed,
            "total_lines": len(result_lines),
            "total_chars": len(patched),
            "sha256": sha256_hash if sha256_hash else "",
        },
    }


# ── memory_save ─────────────────────────────────────────────────────────


# ── file_edit ─────────────────────────────────────────────────────────

async def file_edit_handler(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Perform exact string replacement in a file.

    Reads the file, finds *old_string*, replaces it with *new_string*,
    and writes the file back.  This is the preferred way to make surgical
    edits — safer and more reliable than ``file_patch`` (unified diff)
    for most single-change scenarios.

    Args:
        path: Relative path within the session workspace.
        old_string: The exact text to find and replace. Must match
            exactly, including whitespace and indentation.
        new_string: The text to replace *old_string* with.
        replace_all: If True, replace every occurrence.  If False
            (default) and *old_string* appears more than once, the
            edit is refused and the user is asked to be more specific.
    """
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    ws_root = get_workspace_root()
    safe = resolve_workspace_path(path)
    if safe is None:
        return {"success": False, "error": f"路径 '{path}' 超出工作区允许范围"}

    expected_ok, expected_error = validate_expected_sha256(safe, expected_sha256)
    if not expected_ok:
        return {"success": False, "error": expected_error, "error_type": "conflict"}

    if await aisdir(safe):
        return {"success": False, "error": f"'{path}' 是一个目录，无法编辑"}

    if not old_string:
        return {"success": False, "error": "old_string 不能为空"}

    # ── Context ────────────────────────────────────────────────────────
    sid = _get_sid_fast()
    uid = _get_uid_fast()

    # ── Handle new file creation ─────────────────────────────────────
    # When the file doesn't exist yet, auto-create it with new_string as
    # the full content.  This makes file_edit a universal "write-or-edit"
    # tool — agents no longer need to remember to switch to file_write
    # for new files.  Matches the behaviour of the native Claude Edit tool.
    if not await aexists(safe):
        try:
            await amkdir(safe.parent)
            await awrite_text(safe, new_string, encoding="utf-8")
            size = await astat_size(safe)
        except OSError as exc:
            return {"success": False, "error": f"创建文件失败: {exc}"}

        # Track version + broadcast (fire-and-forget)
        sha256_hash = ""
        try:
            from app.services.file_version_tracker import file_version_tracker
            fv = file_version_tracker.record_write(sid, path, new_string, uid, "")
            sha256_hash = file_sha256(safe) or fv.sha256
        # noqa: BLE001 - file-version tracking is best-effort, never block write
        except Exception:
            pass
        import asyncio as _asyncio
        _asyncio.ensure_future(
            _broadcast_workspace_change(sid, path, "write", size, "", user_id=uid)
        )

        return {
            "success": True,
            "result": f"文件 '{path}' 创建成功（{size} 字节）。old_string 忽略——文件之前不存在。",
            "metadata": {
                "path": path,
                "size_bytes": size,
                "created": True,
                "sha256": sha256_hash if sha256_hash else "",
            },
        }

    # ── Read original content ─────────────────────────────────────────
    try:
        original_text = await aread_text(safe, encoding="utf-8")
    except UnicodeDecodeError:
        return {"success": False, "error": f"文件不是有效的 UTF-8 文本文件，可能是二进制文件"}
    except OSError as exc:
        return {"success": False, "error": f"读取文件失败: {exc}"}

    # ── Find matches ──────────────────────────────────────────────────
    occurrences = original_text.count(old_string)
    if occurrences == 0:
        # Provide helpful diagnostic: show the file snippet around where
        # the user might be looking, so they can spot formatting issues.
        snippet_lines = original_text.split("\n")[:20]
        snippet = "\n".join(snippet_lines)
        hint = ""
        # Check if old_string with different line endings would match
        if "\r\n" in original_text and "\n" in old_string:
            hint = " (提示: 文件使用 CRLF 换行符，old_string 是否使用了 LF？)"
        return {
            "success": False,
            "error": (
                f"在文件 '{path}' 中未找到指定的文本。"
                f"请确认 old_string 与文件内容完全一致（包括空格和缩进）。"
                f"文件开头预览:\n{snippet[:500]}"
                f"{hint}"
            ),
            "metadata": {"path": path, "occurrences": 0},
        }

    if not replace_all and occurrences > 1:
        # Show context around each match to help the user disambiguate
        match_contexts: list[str] = []
        lines = original_text.split("\n")
        for idx, line in enumerate(lines):
            if old_string in line:
                ctx = f"  行 {idx + 1}: {line.strip()[:120]}"
                match_contexts.append(ctx)
        return {
            "success": False,
            "error": (
                f"在文件 '{path}' 中找到了 {occurrences} 处匹配的文本，"
                f"但 replace_all 为 false。请提供更具体的 old_string "
                f"（包含更多上下文行）以唯一定位要修改的位置。\n"
                f"匹配位置:\n" + "\n".join(match_contexts[:10])
            ),
            "metadata": {"path": path, "occurrences": occurrences},
        }

    # ── Perform replacement ───────────────────────────────────────────
    new_text = original_text.replace(old_string, new_string) if replace_all else original_text.replace(old_string, new_string, 1)

    if new_text == original_text:
        return {"success": True, "result": f"文件 '{path}' 未发生变化（old_string 与 new_string 相同）", "metadata": {"path": path, "occurrences": occurrences, "changed": False}}

    # ── Write ──────────────────────────────────────────────────────────
    try:
        await awrite_text(safe, new_text, encoding="utf-8")
        size = await astat_size(safe)

        # ── Track version ─────────────────────────────────────────────
        sha256_hash = ""
        try:
            from app.services.file_version_tracker import file_version_tracker
            fv = file_version_tracker.record_write(sid, path, new_text, uid, "")
            sha256_hash = file_sha256(safe) or fv.sha256
        # noqa: BLE001 - file-version tracking is best-effort, never block write
        except Exception:
            pass

        # ── Broadcast workspace change ────────────────────────────────
        diff_preview = _compute_unified_diff(original_text, new_text, path)
        import asyncio as _asyncio
        _asyncio.ensure_future(
            _broadcast_workspace_change(sid, path, "write", size, diff_preview, user_id=uid)
        )

        # ── Auto git commit ───────────────────────────────────────────

        replaced_count = occurrences if replace_all else 1
        return {
            "success": True,
            "result": (
                f"文件 '{path}' 编辑成功。替换了 {replaced_count} 处匹配。"
            ),
            "metadata": {
                "path": path,
                "size_bytes": size,
                "occurrences": occurrences,
                "replaced": replaced_count,
                "sha256": sha256_hash if sha256_hash else "",
            },
        }
    except OSError as exc:
        return {"success": False, "error": f"写入文件失败: {exc}"}


# ── file_glob ─────────────────────────────────────────────────────────

async def file_glob_handler(
    pattern: str,
    path: str = ".",
) -> dict[str, Any]:
    """Find files matching a glob pattern.

    Uses standard shell-style wildcards:
      - ``*`` matches any number of characters (except path separator)
      - ``**`` matches any number of characters across directories
      - ``?`` matches a single character
      - ``[abc]`` matches one character in the brackets

    Args:
        pattern: Glob pattern, e.g. ``**/*.py``, ``src/**/*.tsx``,
            ``*.md``, ``app/services/*.py``.
        path: Directory to search within (relative to workspace root).
            Defaults to ``"."`` (workspace root).
    """
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    ws_root = get_workspace_root()
    search_root = resolve_workspace_path(path)
    if search_root is None:
        return {"success": False, "error": f"路径 '{path}' 超出工作区允许范围"}

    if not await aexists(search_root):
        return {"success": False, "error": f"目录不存在: {path}"}

    if not await aisdir(search_root):
        return {"success": False, "error": f"'{path}' 不是目录"}

    try:
        # Use pathlib's glob — recursive if pattern contains **
        matches = list(search_root.glob(pattern))
        # Filter to files only (skip directories)
        file_matches = [m for m in matches if m.is_file()]
        # Sort for deterministic output
        file_matches.sort(key=lambda p: (str(p.parent), p.name.lower()))

        result_files: list[dict[str, Any]] = []
        for f in file_matches[:200]:  # cap at 200 results
            try:
                sz = f.stat().st_size
            except OSError:
                sz = 0
            rel = str(f.relative_to(ws_root)).replace("\\", "/")
            result_files.append({
                "path": rel,
                "size_bytes": sz,
                "size_display": f"{sz:,} B" if sz < 1024 else f"{sz / 1024:.1f} KB",
            })

        total = len(file_matches)
        truncated = total > 200
        display = result_files[:200]

        return {
            "success": True,
            "result": {
                "pattern": pattern,
                "search_path": str(search_root.relative_to(ws_root)).replace("\\", "/") or ".",
                "matches": display,
                "total_matches": total,
                "truncated": truncated,
            },
        }
    except Exception as exc:
        return {"success": False, "error": f"Glob 匹配失败: {exc}"}


# ── mkdir ─────────────────────────────────────────────────────────────

async def mkdir_handler(
    path: str,
    parents: bool = True,
) -> dict[str, Any]:
    """Create a directory in the user's per-session workspace.

    Creates the specified directory (and any missing parent directories
    when *parents* is True — the default).  This is the canonical way to
    scaffold a project directory tree without writing placeholder files.

    Args:
        path: Relative directory path within the session workspace
              (e.g. ``src/components/`` or ``src/utils``).
        parents: If True (default), create intermediate directories
                 like ``mkdir -p``.  If False, fail when the parent
                 does not exist.
    """
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    if not path or not path.strip():
        return {"success": False, "error": "目录路径不能为空"}

    path = path.strip()
    ws_root = get_workspace_root()
    safe = resolve_workspace_path(path)
    if safe is None:
        return {"success": False, "error": f"路径 '{path}' 超出工作区允许范围"}

    # ── Already exists ──────────────────────────────────────────────
    if await aexists(safe):
        if await aisdir(safe):
            try:
                listing = (await aiterdir(safe))[:20]
                names = [str(p.relative_to(ws_root)) + ("/" if await aisdir(p) else "") for p in listing]
            except OSError:
                names = []
            return {
                "success": True,
                "result": f"目录 '{path}' 已存在（{len(names)} 项）",
                "metadata": {"path": str(safe.relative_to(ws_root)), "existed": True, "items": names},
            }
        else:
            return {"success": False, "error": f"'{path}' 已存在但是一个文件，无法创建同名目录"}

    # ── Create ──────────────────────────────────────────────────────
    try:
        await amkdir(safe, parents=parents, exist_ok=False)
    except FileNotFoundError:
        return {
            "success": False,
            "error": f"无法创建目录 '{path}'：父目录不存在（设置 parents=True 可自动创建父目录）",
        }
    except FileExistsError:
        return {"success": True, "result": f"目录 '{path}' 已存在", "metadata": {"path": str(safe.relative_to(ws_root)), "existed": True}}
    except OSError as exc:
        return {"success": False, "error": f"创建目录失败: {exc}"}

    # ── Broadcast + git ─────────────────────────────────────────────
    sid = _get_sid_fast()
    uid = _get_uid_fast()
    import asyncio as _asyncio
    _asyncio.ensure_future(
        _broadcast_workspace_change(sid, path, "mkdir", 0, "", user_id=uid)
    )

    return {
        "success": True,
        "result": f"目录 '{path}' 创建成功",
        "metadata": {"path": str(safe.relative_to(ws_root)), "existed": False, "parents_created": parents},
    }
