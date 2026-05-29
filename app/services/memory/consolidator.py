from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from app.config import MEMORY_DIR
from app.services.adapter_manager import adapter_manager
from app.services.memory.models import MemoryHeader, MemoryDocument
from app.services.memory.storage import MemoryStorage
from app.services.memory.scanner import MemoryScanner

logger = logging.getLogger("agenthub.memory.consolidator")

CONSOLIDATION_PROMPT = """你是一个记忆维护系统。请分析以下记忆列表，识别需要合并、删除或更新的条目。

## 判断规则

1. **合并（merge）**：两个或多个记忆描述了同一事实、同一偏好或同一事件，应合并为一条
2. **删除（delete）**：信息已完全过时、内容为空、或仅为临时状态且已无关
3. **更新（update）**：信息仍然有效但描述不准确，需要修正
4. **保留（keep）**：信息独立、有效、无需修改

## 输出要求

返回一个纯 JSON 对象（不要包裹 markdown 代码块）：

{{
  "actions": [
    {{
      "action": "merge|delete|update|keep",
      "targets": ["file1.md", "file2.md"],
      "reason": "中文说明（30字以内）",
      "merged_name": "合并后的名称（仅 merge 需要）",
      "merged_description": "合并后的描述（仅 merge 需要）",
      "merged_body": "合并后的完整内容（仅 merge 需要）"
    }}
  ],
  "summary": "本次分析的整体摘要（100字以内）"
}}

## 记忆列表

{memory_list}
"""


class MemoryConsolidator:
    """LLM-driven memory consolidation: dedup, merge, prune.

    Reviews all memory files and uses an LLM to identify:
    - Duplicate/similar memories to merge
    - Outdated memories to delete
    - Stale descriptions to update

    State file: .claude/memory/.consolidation_state.json
    """

    def __init__(self, storage: Optional[MemoryStorage] = None) -> None:
        self._storage = storage or MemoryStorage(MEMORY_DIR)
        self._scanner = MemoryScanner(self._storage)
        self._state_path = self._storage.base / ".consolidation_state.json"
        self._state: dict[str, Any] = self._load_state()

    # ── public API ──────────────────────────────────────────────────

    async def consolidate(self, dry_run: bool = False) -> dict[str, Any]:
        """Analyze all memories and optionally execute merge/delete actions.

        Returns a dict with keys: merged, deleted, updated, unchanged, summary.
        When dry_run=True, returns the proposed actions without executing them.
        """
        headers = self._scanner.scan(max_files=200)
        if len(headers) < 2:
            return {
                "merged": [], "deleted": [], "updated": [], "unchanged": [],
                "summary": "记忆文件不足2条，无需合并。",
                "dry_run": dry_run,
            }

        # Read full content for each memory
        memories = self._read_all_memories(headers)
        memory_list = self._format_memory_list(memories)

        # Call LLM for analysis
        raw = await self._call_consolidation_llm(memory_list)
        if not raw:
            return {
                "merged": [], "deleted": [], "updated": [], "unchanged": [],
                "summary": "LLM调用失败，无法分析。",
                "dry_run": dry_run,
            }

        parsed = self._parse_result(raw)
        if not parsed:
            return {
                "merged": [], "deleted": [], "updated": [], "unchanged": [],
                "summary": "LLM响应解析失败。",
                "dry_run": dry_run,
            }

        actions = parsed.get("actions", [])
        summary = parsed.get("summary", "")

        result: dict[str, Any] = {
            "merged": [], "deleted": [], "updated": [], "unchanged": [],
            "summary": summary, "dry_run": dry_run, "actions": actions,
        }

        if dry_run:
            return result

        # Execute actions
        for action in actions:
            act_type = action.get("action", "keep")
            targets = action.get("targets", [])
            reason = action.get("reason", "")

            if act_type == "merge" and len(targets) >= 2:
                merged = self._execute_merge(action)
                if merged:
                    result["merged"].append({"file": merged, "targets": targets, "reason": reason})

            elif act_type == "delete" and targets:
                deleted = self._execute_deletes(targets)
                for d in deleted:
                    result["deleted"].append({"file": d, "reason": reason})

            elif act_type == "update" and targets:
                for t in targets:
                    result["updated"].append({"file": t, "reason": reason})

            else:
                for t in targets:
                    result["unchanged"].append({"file": t, "reason": reason})

        # Rebuild index
        try:
            self._storage.rebuild_index()
        except Exception:
            pass

        # Update state
        self._state["last_consolidation"] = datetime.now().isoformat(timespec="seconds")
        self._state["consolidation_count"] = self._state.get("consolidation_count", 0) + 1
        merged_files = self._state.setdefault("merged_files", [])
        for m in result["merged"]:
            merged_files.append(m)
        self._save_state()

        logger.info(
            "consolidation complete: merged=%d deleted=%d updated=%d",
            len(result["merged"]), len(result["deleted"]), len(result["updated"]),
        )
        return result

    def get_status(self) -> dict[str, Any]:
        return {
            "last_consolidation": self._state.get("last_consolidation", ""),
            "consolidation_count": self._state.get("consolidation_count", 0),
            "merged_files": self._state.get("merged_files", []),
        }

    # ── internal helpers ────────────────────────────────────────────

    def _read_all_memories(self, headers: list[MemoryHeader]) -> list[dict[str, Any]]:
        memories: list[dict[str, Any]] = []
        for h in headers:
            doc = self._storage.get(h.filename)
            if doc:
                memories.append({
                    "filename": h.filename,
                    "name": h.name,
                    "type": h.type.value if h.type else "reference",
                    "description": h.description,
                    "body": doc.body[:800],  # truncated for LLM
                    "mtime": datetime.fromtimestamp(h.mtime).isoformat(timespec="seconds"),
                })
        return memories

    @staticmethod
    def _format_memory_list(memories: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for i, m in enumerate(memories, 1):
            lines.append(
                f"{i}. [{m['type']}] {m['filename']}\n"
                f"   名称: {m['name']}\n"
                f"   描述: {m['description']}\n"
                f"   内容: {m['body'][:300]}\n"
                f"   修改时间: {m['mtime']}"
            )
        return "\n\n".join(lines)

    async def _call_consolidation_llm(self, memory_list: str) -> str | None:
        prompt = CONSOLIDATION_PROMPT.format(memory_list=memory_list)
        candidates = self._list_consolidation_models()

        for model in candidates:
            provider = model.get("provider", "")
            model_name = model.get("model_name", "")
            api_key = model.get("api_key", "")
            base_url = model.get("base_url", "")

            if provider == "mock" or not api_key:
                continue

            adapter = adapter_manager.get_adapter(provider)
            try:
                result = await adapter.execute_prompt(
                    prompt=prompt, model=model_name,
                    api_key=api_key, base_url=base_url,
                )
                if result and result.strip():
                    return result.strip()
            except Exception as exc:
                logger.warning("consolidation LLM failed (%s/%s): %s", provider, model_name, exc)
                continue

        logger.error("all consolidation LLM candidates failed")
        return None

    def _list_consolidation_models(self) -> list[dict[str, str]]:
        from app.services.secret_service import decrypt_secret

        candidates: list[dict[str, str]] = []
        seen: set[str] = set()
        try:
            from app.db.session import dict_rows
            rows = dict_rows(
                "SELECT provider, model_name, api_key, base_url "
                "FROM model_configs WHERE is_active=1 ORDER BY id DESC LIMIT 5"
            )
            if rows:
                for row in rows:
                    decrypted = decrypt_secret(row.get("api_key") or "")
                    if decrypted and row.get("provider") != "mock":
                        key = f"{row['provider']}/{row['model_name']}"
                        if key not in seen:
                            seen.add(key)
                            candidates.append({**row, "api_key": decrypted})
        except Exception:
            pass
        try:
            agent_rows = dict_rows(
                "SELECT DISTINCT adapter_type AS provider, base_model_name AS model_name, "
                "api_key, base_url "
                "FROM agent_registry WHERE api_key IS NOT NULL AND api_key != '' "
                "AND adapter_type != '' AND adapter_type IS NOT NULL"
            )
            for row in agent_rows:
                decrypted = decrypt_secret(row.get("api_key") or "")
                if decrypted and row.get("provider") and row.get("provider") != "mock":
                    key = f"{row['provider']}/{row['model_name']}"
                    if key not in seen:
                        seen.add(key)
                        candidates.append({**row, "api_key": decrypted})
        except Exception:
            pass
        from app.config import OPENAI_API_KEY, ANTHROPIC_API_KEY
        if OPENAI_API_KEY:
            candidates.append({"provider": "openai", "model_name": "gpt-4o-mini", "api_key": OPENAI_API_KEY, "base_url": ""})
        if ANTHROPIC_API_KEY:
            candidates.append({"provider": "anthropic", "model_name": "claude-sonnet-4-6", "api_key": ANTHROPIC_API_KEY, "base_url": ""})
        return candidates

    def _execute_merge(self, action: dict[str, Any]) -> str | None:
        """Merge memory files: combine bodies, create new file, delete originals."""
        merged_name = action.get("merged_name", "").strip()
        merged_desc = action.get("merged_description", "").strip()
        merged_body = action.get("merged_body", "").strip()
        targets = action.get("targets", [])

        if not merged_name or not targets:
            return None

        try:
            from app.services.memory.models import MemoryType, sanitize_filename

            # Combine existing body content with LLM's merged result
            bodies = []
            for t in targets:
                doc = self._storage.get(t)
                if doc:
                    bodies.append(doc.body)

            # Use LLM's merged body; fall back to combining all
            final_body = merged_body if merged_body else "\n\n---\n\n".join(bodies)

            # Detect type from first target
            first = self._storage.get(targets[0])
            mem_type = MemoryType.REFERENCE
            if first and first.meta:
                mem_type = first.meta.type

            self._storage.save(
                name=merged_name,
                description=merged_desc,
                type_=mem_type,
                body=final_body,
            )

            # Delete merged originals
            for t in targets:
                self._storage.delete(t)

            return sanitize_filename(merged_name)
        except Exception as exc:
            logger.error("merge failed for %s: %s", targets, exc)
            return None

    def _execute_deletes(self, targets: list[str]) -> list[str]:
        deleted: list[str] = []
        for t in targets:
            try:
                self._storage.delete(t)
                deleted.append(t)
            except Exception as exc:
                logger.error("delete failed for %s: %s", t, exc)
        return deleted

    @staticmethod
    def _parse_result(raw: str) -> dict[str, Any] | None:
        text = raw.strip()
        if text.startswith("```"):
            start = text.find("{")
            if start < 0:
                start = text.find("[")
            if start >= 0:
                text = text[start:]
            end = text.rfind("}")
            if end < 0:
                end = text.rfind("]")
            if end >= 0:
                text = text[:end + 1]
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
        import re
        obj_match = re.search(r"\{(?:[^{}]|(?:\{[^{}]*\}))*\}", text, re.DOTALL)
        if obj_match:
            try:
                result = json.loads(obj_match.group(0))
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass
        return None

    # ── state persistence ───────────────────────────────────────────

    def _load_state(self) -> dict[str, Any]:
        try:
            if self._state_path.exists():
                return json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("failed to load consolidation state: %s", exc)
        return {}

    def _save_state(self) -> None:
        try:
            self._state_path.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("failed to save consolidation state: %s", exc)
