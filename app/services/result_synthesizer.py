from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from app.schemas.dag import DAGConfig
from app.services.context_compaction import compact_text

logger = logging.getLogger("agenthub.result_synthesizer")

SYNTHESIZE_PROMPT = """你是一个技术项目经理，负责整合多个专家Agent的输出，形成完整方案。

## 原始用户需求
{original_request}

## 各Agent执行结果（按执行顺序排列）
{agent_results}

## 整合要求
1. 按逻辑顺序组织输出：需求分析 → 架构设计 → 实现方案 → 质量保障 → 部署建议
2. 标注每个部分的贡献Agent（例如 [Architect]、[CodeGen]）
3. 如果存在矛盾或冲突，明确指出并给出建议
4. 输出最终的**完整可执行方案**，而非简单的摘要拼接
5. 使用清晰的Markdown格式，包含标题、列表、代码块等

## 输出格式
直接输出整合后的完整方案，不需要JSON包装。"""


class ResultSynthesizer:
    """Integrate multi-agent outputs into a coherent final solution.

    Takes the raw outputs from all DAG nodes and produces a polished,
    unified response that reads as a single expert deliverable.

    Usage::

        synthesizer = ResultSynthesizer()
        final = await synthesizer.synthesize(
            dag=dag_config,
            node_results={"node_1": "...", "node_2": "..."},
            original_request="帮我开发用户登录页面",
        )
    """

    MAX_SYNTHESIS_TOKENS = 4000
    SYNTHESIS_TIMEOUT = 60.0

    def __init__(self) -> None:
        pass

    # ── Public API ─────────────────────────────────────────────────────

    async def synthesize(
        self,
        dag: DAGConfig,
        node_results: dict[str, str],
        original_request: str,
        invoke_fn: Any = None,
    ) -> str:
        """Synthesize multi-agent results into a final response.

        Args:
            dag: The executed DAG configuration.
            node_results: Mapping of node_id -> agent output text.
            original_request: The user's original message.
            invoke_fn: Optional async function to call Architect for synthesis.
                       Signature: async fn(content: str) -> str

        Returns:
            A synthesized final response string.
        """
        if not node_results:
            return "所有子任务均未产生有效输出。"

        # Build ordered agent results section
        agent_results_text = self._build_results_section(dag, node_results)

        # Detect conflicts
        conflicts = self._detect_conflicts(dag, node_results)

        # Build the synthesis prompt
        prompt = SYNTHESIZE_PROMPT.format(
            original_request=original_request,
            agent_results=agent_results_text,
        )

        if conflicts:
            prompt += f"\n\n## ⚠️ 检测到的潜在冲突\n{conflicts}"

        # If we have an invoke function, use it for LLM synthesis
        if invoke_fn:
            try:
                logger.info("result_synthesizer: using LLM for synthesis")
                result = await invoke_fn(prompt)
                if result and len(result.strip()) > 50:
                    return result
            except Exception as exc:
                logger.warning("result_synthesizer: LLM synthesis failed: %s", exc)

        # Fallback: structured concatenation
        return self._fallback_synthesize(dag, node_results, original_request, conflicts)

    # ── Internal helpers ───────────────────────────────────────────────

    def _build_results_section(
        self, dag: DAGConfig, node_results: dict[str, str]
    ) -> str:
        """Build the agent results section ordered by DAG topology."""
        # Topological sort for ordering
        ordered = self._topological_order(dag)
        sections = []
        for node in ordered:
            if node.id in node_results:
                output = node_results[node.id]
                # Truncate very long outputs
                if len(output) > 1800:
                    output = output[:1800] + "\n\n... (输出过长，已截断)"
                sections.append(
                    f"### [{node.agent}] {compact_text(node.description, max_chars=72)}\n"
                    f"{output}\n"
                )
        return "\n\n".join(sections)

    @staticmethod
    def _topological_order(dag: DAGConfig) -> list[Any]:
        """Return nodes in topological order."""
        result = []
        visited = set()
        temp_visited = set()

        def visit(node_id: str):
            if node_id in temp_visited:
                return  # Cycle detected; skip
            if node_id in visited:
                return
            temp_visited.add(node_id)
            node = next((n for n in dag.nodes if n.id == node_id), None)
            if node:
                for dep_id in node.dependencies:
                    visit(dep_id)
            temp_visited.discard(node_id)
            visited.add(node_id)
            if node:
                result.append(node)

        for n in dag.nodes:
            visit(n.id)
        return result

    def _detect_conflicts(
        self, dag: DAGConfig, node_results: dict[str, str]
    ) -> str:
        """Detect potential contradictions between agent outputs."""
        conflicts = []

        # Simple heuristic: check if Review output contains warning/issue keywords
        # and CodeGen output doesn't address them
        review_output = ""
        codegen_output = ""
        architect_output = ""

        for node in dag.nodes:
            if node.id in node_results:
                if node.agent == "Review":
                    review_output = node_results[node.id]
                elif node.agent == "CodeGen":
                    codegen_output = node_results[node.id]
                elif node.agent == "Architect":
                    architect_output = node_results[node.id]

        # Check Review vs CodeGen
        if review_output and codegen_output:
            review_issues = self._extract_issues(review_output)
            for issue in review_issues[:3]:
                if issue.lower() not in codegen_output.lower():
                    conflicts.append(
                        f"- **Review提出但CodeGen未处理**: {issue}"
                    )

        # Check Architect vs CodeGen
        if architect_output and codegen_output:
            arch_key_files = self._extract_file_targets(architect_output)
            cg_key_files = self._extract_file_targets(codegen_output)
            missing = arch_key_files - cg_key_files
            for f in list(missing)[:3]:
                if len(f) > 1:
                    conflicts.append(
                        f"- **Architect建议修改但CodeGen未覆盖的文件**: `{f}`"
                    )

        return "\n".join(conflicts) if conflicts else ""

    @staticmethod
    def _extract_issues(text: str) -> list[str]:
        """Extract issue/suggestion lines from review text."""
        issues = []
        for line in text.split("\n"):
            line = line.strip()
            if any(kw in line for kw in ["问题", "建议", "需修改", "风险", "漏洞", "不符合"]):
                issues.append(line[:200])
        return issues

    @staticmethod
    def _extract_file_targets(text: str) -> set[str]:
        """Extract file paths from architect/codegen text."""
        files = set()
        # Match patterns like `path/to/file.ext` or `/src/file.ts`
        for match in re.finditer(r'[\w/.-]+\.[a-z]{1,6}', text):
            f = match.group(0)
            if len(f) > 2 and not f.startswith(("http", "www")):
                files.add(f)
        return files

    def _fallback_synthesize(
        self,
        dag: DAGConfig,
        node_results: dict[str, str],
        original_request: str,
        conflicts: str,
    ) -> str:
        """Produce a structured synthesis without LLM (fallback)."""
        parts = [
            f"## 任务总结\n\n原始需求: {original_request}\n",
        ]

        if dag.analysis:
            parts.append(f"**分析**: {dag.analysis}\n")

        # Results by agent in topological order
        ordered = self._topological_order(dag)
        for node in ordered:
            if node.id in node_results:
                status = node.status
                icon = {"SUCCESS": "✓", "FAILED": "✗", "RUNNING": "⏳"}.get(status, "○")
                parts.append(
                    f"### {icon} [{node.agent}] {node.description}\n\n"
                    f"{node_results[node.id]}\n"
                )

        if conflicts:
            parts.append(f"### ⚠️ 潜在冲突\n\n{conflicts}\n")

        # Summary stats
        success_count = sum(1 for n in dag.nodes if n.status == "SUCCESS")
        failed_count = sum(1 for n in dag.nodes if n.status == "FAILED")
        parts.append(
            f"\n---\n*执行统计: {success_count}/{dag.total} 节点成功"
            + (f", {failed_count} 失败" if failed_count else "")
            + "*"
        )

        return "\n\n".join(parts)


# ── Module-level singleton ────────────────────────────────────────────

result_synthesizer = ResultSynthesizer()
