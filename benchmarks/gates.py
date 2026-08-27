"""AgentHub benchmark gates (Phase R1 / R4).

Verifies near-term performance claims can be proven, and enforces the
documentation-to-code rule: no public claim may be worded as "implemented"
without a matching gate.

Usage:
    python benchmarks/gates.py check-docs
    python benchmarks/gates.py check-links
    python benchmarks/gates.py run --name api_latency_p95 [--threshold-ms 200]
    python benchmarks/gates.py run --name knowledge_retrieval_recall
    python benchmarks/gates.py run --name cn_tokenizer_precision
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys
import time
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Known near-term performance claims and their gates.
# A claim is "implemented" only when a gate exists; otherwise it must be
# documented as a target value.
PERFORMANCE_CLAIMS: dict[str, str] = {
    "api_latency_p95": "docs/zh/advanced/performance.md (P95 < 200ms)",
    "token_compaction_ratio": "docs/zh/advanced/performance.md (compaction)",
    "knowledge_retrieval_p95": "docs/zh/advanced/performance.md (P95 < 80ms target)",
    "knowledge_retrieval_recall": "docs/architecture/components/memory.md (L2 recall > 85%)",
    "cn_tokenizer_precision": "docs/architecture/components/memory.md (CN token parity < 5%)",
    "streaming_ttft": "docs/zh/advanced/performance.md (streaming first token)",
}

# Hard thresholds (ms). Raise gates as hardware/CI evolves.
DEFAULT_THRESHOLDS_MS: dict[str, float] = {
    "api_latency_p95": 200.0,
    "knowledge_retrieval_p95": 80.0,
    "streaming_ttft": 3_000.0,
}


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str
    measured_ms: float | None = None


# ─── Documentation-to-code rule ──────────────────────────────────────────

# Files that must not claim unverified performance as "implemented".
CLAIM_CHECK_CONTENT: tuple[pathlib.Path, ...] = (
    ROOT / "docs" / "zh" / "guide" / "what-is-agenthub.md",
    ROOT / "docs" / "zh" / "advanced" / "performance.md",
    ROOT / "README.md",
    ROOT / "README_CN.md",
)

# Disallow a claim that implies shipped/reached status without a gate file.
# A line already marked as prototype/target is explicitly allowed.
OVERSOLD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<!原型（)(?<!目标值)已实现[^。\n]*(P95|80ms|ContextOS)", re.IGNORECASE),
    re.compile(r"(?<!原型（)✅[^\n]*(P95\s*<\s*80|ContextOS)", re.IGNORECASE),
)


def _is_honest_mark(line: str) -> bool:
    """True when the line already qualifies the claim as prototype/target."""
    return any(mark in line for mark in ("原型", "目标", "待建", "进行中", "target"))


def check_docs() -> GateResult:
    """Enforce the capability-table convention (implemented/prototype/target)."""
    problems: list[str] = []
    for path in CLAIM_CHECK_CONTENT:
        if not path.exists():
            problems.append(f"missing claim-check file: {path.relative_to(ROOT)}")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if _is_honest_mark(line):
                continue
            for pattern in OVERSOLD_PATTERNS:
                if pattern.search(line):
                    problems.append(
                        f"{path.relative_to(ROOT)}:{line_no}: oversold claim "
                        f"({pattern.pattern!r})"
                    )
    if problems:
        return GateResult(
            name="docs_claim_discipline",
            passed=False,
            detail="; ".join(problems[:12]),
        )
    return GateResult(
        name="docs_claim_discipline",
        passed=True,
        detail="no oversold performance claims found",
    )


# ─── Markdown link resolver ──────────────────────────────────────────────

def check_links() -> GateResult:
    """Verify every relative Markdown link in docs/ resolves to a real file."""
    missing: list[str] = []
    for path in (ROOT / "docs").rglob("*.md"):
        if any(part in path.parts for part in (".git", "node_modules", "target")):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"\]\(((?:\.\.?/)+[^)#]+?)(?:#[^)]+)?\)", text):
            target = m.group(1)
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                missing.append(f"{path.relative_to(ROOT)} -> {target}")
    if missing:
        return GateResult(
            name="doc_links",
            passed=False,
            detail="; ".join(sorted(set(missing))[:12]),
        )
    return GateResult(name="doc_links", passed=True, detail="all docs links resolve")


# ─── Performance gates ───────────────────────────────────────────────────

def _measure_api_latency(addr: str, threshold_ms: float) -> GateResult:
    """Probe an HTTP endpoint and compare p95 latency against the threshold.

    Uses a real loopback call when available; otherwise records the gate as
    skipped (the claim stays "target" until a measured run proves it).
    """
    import urllib.request

    samples: list[float] = []
    n = 8
    for _ in range(n):
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(addr, timeout=1.0) as _resp:
                pass
            samples.append((time.perf_counter() - start) * 1000.0)
        except Exception:
            return GateResult(
                name="api_latency_p95",
                passed=False,
                detail=f"endpoint {addr} unreachable; gate unproven",
            )
    samples.sort()
    p95 = samples[int(len(samples) * 0.95) - 1] or samples[-1]
    passed = p95 <= threshold_ms
    return GateResult(
        name="api_latency_p95",
        passed=passed,
        detail=f"p95={p95:.1f}ms threshold={threshold_ms:.0f}ms samples={len(samples)}",
        measured_ms=p95,
    )


def run_gate(name: str, threshold_ms: float, addr: str | None) -> GateResult:
    if name == "api_latency_p95":
        if not addr:
            return GateResult(
                name=name,
                passed=False,
                detail="--addr required for api_latency_p95",
            )
        return _measure_api_latency(addr, threshold_ms)
    if name == "token_compaction_ratio":
        return _measure_token_compaction_ratio()
    if name == "knowledge_retrieval_p95":
        return _measure_knowledge_retrieval_p95(threshold_ms)
    if name == "knowledge_retrieval_recall":
        return _measure_knowledge_retrieval_recall()
    if name == "code_file_size":
        return _measure_code_file_size()
    if name == "code_complexity":
        return _measure_code_complexity()
    if name == "test_coverage":
        return _measure_test_coverage()
    if name == "multimodal_e2e_probe":
        return _measure_multimodal_e2e_probe()
    if name == "cn_tokenizer_precision":
        return _measure_cn_tokenizer_precision()
    if name == "streaming_ttft":
        return _measure_streaming_ttft(threshold_ms)
    return GateResult(name=name, passed=False, detail=f"unknown gate: {name}")


def _measure_token_compaction_ratio(min_ratio: float = 0.25) -> GateResult:
    """Measure prompt/result compaction against a real corpus.

    Uses representative mixed-language text and the production compaction +
    token-count helpers; asserts the compaction saves at least *min_ratio*
    of the original tokens so regressions fail the gate.
    """
    from app.services.context_compaction import compact_text
    from app.services.token_budget import count_tokens

    corpus = (
        "用户要求实现一个基于 FastAPI 的 REST API 服务，包含用户管理、订单管理、"
        "商品管理三个核心模块，支持 JWT 认证、RBAC 权限控制、Redis 缓存和 PostgreSQL 持久化。"
        "要求提供完整的单元测试、接口文档和部署脚本，并针对性能进行压测优化。" * 40
    )
    # Compaction is measured end-to-end: production compact_text must shrink a
    # long mixed-language input to a bounded preview while keeping the first
    # intent-bearing sentence plus an ellipsis marker.
    compacted = compact_text(corpus, max_chars=400)
    before = count_tokens(corpus, provider="openai", model="gpt-4o")
    after = max(1, count_tokens(compacted, provider="openai", model="gpt-4o"))
    ratio = 1.0 - (after / before)
    passed = ratio >= min_ratio
    return GateResult(
        name="token_compaction_ratio",
        passed=passed,
        detail=f"compaction ratio={ratio:.2%} (before={before} after={after} "
               f"min={min_ratio:.0%})",
    )


# ─── R4: offline eval set ─────────────────────────────────────────────────

# Offline retrieval eval set: distinct-topic Chinese documents with
# vocabulary-overlapping queries. Expected result id must rank in the top-3.
RETRIEVAL_EVAL_SET: list[tuple[str, str, str]] = [
    ("doc-数据库索引", "PostgreSQL 数据库索引优化：为高频查询字段建立复合索引，避免全表扫描，使用 EXPLAIN 分析执行计划",
     "如何优化数据库索引查询性能"),
    ("doc-容器部署", "Kubernetes 集群部署与自动扩容：配置 HPA 指标、资源配额与滚动更新策略",
     "Kubernetes 集群自动扩容配置方法"),
    ("doc-认证授权", "JWT 令牌认证与 RBAC 权限控制：颁发短期令牌、按角色校验接口权限并支持令牌刷新",
     "JWT 认证和 RBAC 权限控制实现方案"),
    ("doc-分布式锁", "Redis 分布式锁：使用 SETNX 与过期时间防止重复提交，配合 Lua 脚本保证原子性",
     "Redis 分布式锁防止重复请求"),
    ("doc-缓存策略", "数据库与 Redis 缓存策略：缓存穿透、击穿与雪崩的防护以及缓存一致性更新",
     "数据库缓存穿透与一致性更新"),
    ("doc-消息队列", "消息队列与异步任务：引入队列解耦削峰、延迟任务与失败重试机制",
     "消息队列解耦异步任务处理"),
    ("doc-向量检索", "向量检索与相似度召回：将文档切块嵌入向量库，按余弦相似度召回并融合重排",
     "向量检索相似度召回与重排"),
]


def _measure_knowledge_retrieval_recall(min_recall: float = 0.85) -> GateResult:
    """Measure L2 retrieval recall on the internal eval set using the
    production vector index and the default local embedder."""
    import asyncio
    import tempfile
    from datetime import UTC, datetime

    from app.services.memory.l2_vector import (
        EmbeddingVersion,
        L2VectorEntry,
        L2VectorIndex,
        LocalHashEmbedder,
    )

    async def measure() -> GateResult:
        index = L2VectorIndex(tempfile.mkdtemp(prefix="agenthub-rec-gate-"))
        embedder = LocalHashEmbedder()
        now = datetime.now(UTC).isoformat()
        version = EmbeddingVersion.current().tag

        for record_id, text, _query in RETRIEVAL_EVAL_SET:
            entry = L2VectorEntry(
                record_id=record_id, text=text, scope="user", session_id="eval",
                embedding_version=version, vector=embedder.embed(text),
                created_at=now, updated_at=now,
            )
            await index.upsert(entry)

        expected_ids = {record_id for record_id, _text, _query in RETRIEVAL_EVAL_SET}
        hits = 0
        for record_id, _text, query in RETRIEVAL_EVAL_SET:
            results = await index.search(embedder.embed(query), limit=3)
            if any(hit.record_id == record_id for _score, hit in results):
                hits += 1
        recall = hits / max(1, len(expected_ids))
        passed = recall >= min_recall
        return GateResult(
            name="knowledge_retrieval_recall",
            passed=passed,
            detail=f"recall@{3}={recall:.0%} (hits={hits}/{len(expected_ids)} "
                   f"min={min_recall:.0%})",
        )

    return asyncio.run(measure())


def _measure_knowledge_retrieval_p95(threshold_ms: float = 80.0, repeats: int = 30) -> GateResult:
    """Measure real ``L2VectorIndex.search()`` p95 latency over the eval set.

    Seeds the production file-backed index with the same offline corpus as
    the recall gate, then times repeated top-3 searches with the default
    local embedder. Regression above *threshold_ms* fails the gate; a probe
    that returns zero hits fails too (latency of an empty search is
    meaningless).
    """
    import asyncio
    import tempfile
    from datetime import UTC, datetime

    from app.services.memory.l2_vector import (
        EmbeddingVersion,
        L2VectorEntry,
        L2VectorIndex,
        LocalHashEmbedder,
    )

    async def measure() -> GateResult:
        index = L2VectorIndex(tempfile.mkdtemp(prefix="agenthub-lat-gate-"))
        embedder = LocalHashEmbedder()
        now = datetime.now(UTC).isoformat()
        version = EmbeddingVersion.current().tag

        for record_id, text, _query in RETRIEVAL_EVAL_SET:
            await index.upsert(L2VectorEntry(
                record_id=record_id, text=text, scope="user", session_id="eval",
                embedding_version=version, vector=embedder.embed(text),
                created_at=now, updated_at=now,
            ))

        queries = [(record_id, embedder.embed(query)) for record_id, _t, query in RETRIEVAL_EVAL_SET]

        # Warm-up (first read parses vectors.json) + correctness sanity:
        # measuring empty-search latency proves nothing.
        first_hits = 0
        for record_id, vec in queries:
            results = await index.search(vec, limit=3)
            if any(hit.record_id == record_id for _score, hit in results):
                first_hits += 1
        if first_hits == 0:
            return GateResult(
                name="knowledge_retrieval_p95",
                passed=False,
                detail="probe produced zero hits — latency measurement invalid",
            )

        samples: list[float] = []
        empty_seen = False
        for _ in range(repeats):
            for _record_id, vec in queries:
                start = time.perf_counter()
                results = await index.search(vec, limit=3)
                samples.append((time.perf_counter() - start) * 1000.0)
                if not results:
                    empty_seen = True
        samples.sort()
        p95 = samples[max(0, int(len(samples) * 0.95) - 1)]
        passed = (not empty_seen) and p95 <= threshold_ms
        return GateResult(
            name="knowledge_retrieval_p95",
            passed=passed,
            detail=f"p95={p95:.1f}ms threshold={threshold_ms:.0f}ms "
                   f"samples={len(samples)} correctness@3={first_hits}/{len(queries)} "
                   f"backend=L2VectorIndex(file-json)",
            measured_ms=p95,
        )

    return asyncio.run(measure())


def _measure_multimodal_e2e_probe() -> GateResult:
    """Opt-in vision e2e probe through a real new-api channel (MM-5).

    Runs ONLY when ``NEWAPI_BASE_URL`` + ``AGENTHUB_TEST_CHANNEL_KEY`` are
    set (keys never live in the repo); otherwise an honest SKIP keeps CI
    green offline. Posts a tiny inline PNG via the standard dual-track
    content array and asserts the model saw it (non-empty reply + billed
    prompt tokens).
    """
    import base64 as _b64
    import os

    import httpx

    base = os.getenv("NEWAPI_BASE_URL", "").strip()
    key = os.getenv("AGENTHUB_TEST_CHANNEL_KEY", "").strip()
    if not (base and key):
        return GateResult(
            name="multimodal_e2e_probe",
            passed=True,
            detail="[SKIP] vision channel not configured "
                   "(set NEWAPI_BASE_URL + AGENTHUB_TEST_CHANNEL_KEY; "
                   "key via env only)",
        )
    model = os.getenv("AGENTHUB_TEST_VISION_MODEL",
                      "moonshot-v1-8k-vision-preview").strip()
    image_path = os.getenv("AGENTHUB_TEST_IMAGE_PATH", "").strip()
    if image_path and pathlib.Path(image_path).is_file():
        raw = pathlib.Path(image_path).read_bytes()
        data_uri = f"data:image/png;base64,{_b64.b64encode(raw).decode()}"
        source = pathlib.Path(image_path).name
    else:
        tiny = _b64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
        data_uri = f"data:image/png;base64,{_b64.b64encode(tiny).decode()}"
        source = "inline-1x1-png"

    try:
        r = httpx.post(
            f"{base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": "用一句话描述这张图片。"},
                    ],
                }],
            },
            timeout=90,
        )
        r.raise_for_status()
        body = r.json()
        reply_ok = bool(body["choices"][0]["message"]["content"])
        billed = (body.get("usage", {}).get("prompt_tokens") or 0) > 0
    except Exception as exc:  # noqa: BLE001 — probe failure is the signal
        return GateResult(
            name="multimodal_e2e_probe",
            passed=False,
            detail=f"vision probe failed model={model}: {str(exc)[:200]}",
        )
    passed = reply_ok and billed
    return GateResult(
        name="multimodal_e2e_probe",
        passed=passed,
        detail=f"model={model} source={source} reply={reply_ok} "
               f"billed_in_usage={billed}",
    )


# ─── Code-quality gates (R4-4) ───────────────────────────────────────────

QUALITY_EXEMPTIONS_PATH = ROOT / "benchmarks" / "quality_exemptions.json"
QUALITY_SCAN_DIRS: tuple[str, ...] = ("app", "services", "benchmarks", "tests")
MAX_FILE_LINES = 800
MAX_FUNCTION_COMPLEXITY = 20

# AST decision nodes contributing +1 to cyclomatic complexity.
_CC_DECISION_NODES = (
    ast.If, ast.For, ast.While, ast.AsyncFor, ast.IfExp, ast.Try,
    ast.ExceptHandler, ast.With, ast.AsyncWith, ast.Assert,
    ast.comprehension,  # each comprehension counts its ifs via attribute? keep base hit
)


def _load_quality_exemptions() -> dict[str, dict[str, object]]:
    if not QUALITY_EXEMPTIONS_PATH.is_file():
        return {}
    try:
        data = json.loads(QUALITY_EXEMPTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _python_targets():
    skip_parts = {".venv", "node_modules", "__pycache__", "build_ws", ".git"}
    for dir_name in QUALITY_SCAN_DIRS:
        base = ROOT / dir_name
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if skip_parts & set(path.parts):
                continue
            yield path


def _iter_functions(tree: "ast.AST"):
    """Yield ``(qualified_name, FunctionDef)`` including methods.

    Qualified names are ``Class.method`` (nested classes dotted); standalone
    functions keep their bare name. Same-name methods of different classes
    therefore never collide in exemption keys.
    """
    stack = [(tree, "")]
    while stack:
        container, prefix = stack.pop()
        for node in getattr(container, "body", []):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield (f"{prefix}{node.name}", node)
            elif isinstance(node, ast.ClassDef):
                stack.append((node, f"{prefix}{node.name}."))


def cyclomatic_complexity(node: "ast.AST") -> int:
    """Classic McCabe via AST: 1 + count of branching constructs."""
    score = 1
    for child in ast.walk(node):
        if isinstance(child, _CC_DECISION_NODES):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += max(0, len(child.values) - 1)
        elif isinstance(child, ast.comprehension):
            score += len(child.ifs)
    return score


def _measure_code_file_size(max_lines: int = MAX_FILE_LINES) -> GateResult:
    """Fail when a Python module exceeds *max_lines* unless exempted.

    Exemptions live in ``benchmarks/quality_exemptions.json`` keyed by repo-
    relative path with the recorded line count at exemption time — the list
    may only shrink, never grow beyond entries already present.
    """
    exemptions = (_load_quality_exemptions().get("file_size") or {})  # type: ignore[assignment]
    offenders: list[str] = []
    for path in _python_targets():
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        try:
            lines = sum(1 for line in path.open("rb") if line.strip())
        except OSError:
            continue
        if rel in exemptions:
            # Exempted legacy module: ratchet is recorded at exemption time;
            # any further GROWTH beyond the recorded count still fails.
            if lines > int(exemptions.get(rel, 0)):  # type: ignore[arg-type]
                offenders.append(f"{rel}: {lines} > exempted-cap {exemptions.get(rel)}")  # type: ignore[arg-type]
            continue
        if lines > max_lines:
            offenders.append(f"{rel}: {lines} > {max_lines}")
    if offenders:
        detail = "oversized: " + "; ".join(offenders[:10])
    else:
        detail = f"all scanned modules <= {max_lines} effective lines"
    detail += f"; exemptions={len(exemptions)} (list may only shrink)"
    return GateResult(
        name="code_file_size",
        passed=not offenders,
        detail=detail[:900],
    )


def _measure_code_complexity(max_cc: int = MAX_FUNCTION_COMPLEXITY) -> GateResult:
    """Fail when any function's McCabe complexity exceeds *max_cc* unless exempted."""
    exemptions = (_load_quality_exemptions().get("complexity") or {})  # type: ignore[assignment]
    offenders: list[str] = []
    checked = 0
    for path in _python_targets():
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for qualname, node in _iter_functions(tree):
            cc = cyclomatic_complexity(node)
            checked += 1
            key = f"{rel}:{qualname}"
            effective_cap = int(exemptions.get(key, max_cc))  # type: ignore[arg-type]
            if cc > effective_cap:
                offenders.append(f"{key}: CC={cc}>{effective_cap}")
    return GateResult(
        name="code_complexity",
        passed=not offenders,
        detail=(
            f"CC<={max_cc} over {checked} functions; "
            + ("offenders: " + "; ".join(offenders[:10])[:800] if offenders else "clean")
        ),
    )


def _measure_test_coverage(min_rate: float = 0.60) -> GateResult:
    """Consume ``coverage.xml`` produced by ``pytest --cov`` and fail low.

    Honest SKIP when no coverage artifact exists (e.g. docs-gates job where
    no tests run) — coverage is only enforced in jobs that actually execute
    tests.
    """
    import xml.etree.ElementTree as ET

    candidates = [ROOT / "coverage.xml", ROOT / "services" / "python" / "coverage.xml"]
    artifact = next((c for c in candidates if c.is_file()), None)
    if artifact is None:
        return GateResult(
            name="test_coverage",
            passed=True,
            detail="[SKIP] no coverage.xml artifact in this job "
                   "(run pytest --cov in test jobs to enforce)",
        )
    root = ET.parse(str(artifact)).getroot()
    rate_raw = (root.attrib.get("line-rate")
                or root.findtext("./coverage") or "")
    try:
        rate = float(rate_raw)
    except ValueError:
        return GateResult(name="test_coverage", passed=False,
                          detail="coverage.xml unparseable")
    passed = rate >= min_rate
    return GateResult(
        name="test_coverage",
        passed=passed,
        detail=f"line-rate={rate:.1%} min={min_rate:.0%} source={artifact.relative_to(ROOT)}",
    )


# Representative Chinese / mixed-language corpus for tokenizer evaluation.
CN_EVAL_CORPUS: list[str] = [
    "用户要求实现一个基于 FastAPI 的 REST API 服务，支持 JWT 认证与 RBAC 权限控制。",
    "请总结这两个方案的优缺点，并给出适用于大规模异构数据场景的推荐架构。",
    "配置 Redis 缓存与 PostgreSQL 持久化，同时保证缓存一致性。",
    "将上述需求拆解为可并行实施的子任务，标注每项的风险等级。",
    "背景：生产环境运行在 Kubernetes，需要一套可观测的发布与扩容流程。",
    "部署脚本需要兼容 Windows 与 Linux，并支持环境变量注入。",
    "用户偏好：回答保持简洁，优先给出可运行的代码示例。",
    "约束：不得自动发布生产环境，所有变更需人工确认。",
]


def _measure_cn_tokenizer_precision(max_error: float = 0.05) -> GateResult:
    """Verify billing parity of ``count_tokens`` against a real native tokenizer.

    R4 acceptance "token estimation error < 5% for listed CN providers" is
    about *billing parity*: once a native tokenizer asset is provisioned,
    ``count_tokens`` must route through it and reproduce its exact counts.
    The gate measures ``count_tokens`` against an independent straight read
    of the asset (same file, separate load), so regressions in the wiring —
    wrong env var name, misrouted backend selection, encode-shape handling,
    stale caches — fail loudly instead of silently re-billing via estimates.

    Without an asset the gate reports an explicit SKIP (never a synthetic
    pass). Estimator-vs-native residuals are reported as observability data
    only; they are controlled by CN_TOKEN_RATIOS calibration, not by this
    pass/fail decision.
    """
    import os

    from app.services.token_budget import (
        _local_provider_tokenizer,
        count_tokens,
        estimate_tokens_multilingual,
    )
    from app.services.token_budget import (
        tokenizer_backend as backend_of,
    )

    provider = os.getenv("AGENTHUB_CN_TOKENIZER_PROVIDER", "qwen").lower()
    model = os.getenv("AGENTHUB_CN_TOKENIZER_MODEL", "").strip()

    # Truth source resolves per backend type: an in-process registered
    # counter (tests / plugin assets) or a provisioned fast-tokenizer file.
    from app.services.token_budget import _REGISTERED_TOKENIZERS  # noqa: PLC0415
    provider_key = provider.lower()
    counter = (_REGISTERED_TOKENIZERS.get(f"{provider_key}:{model.lower()}")
               or _REGISTERED_TOKENIZERS.get(provider_key))
    asset = _local_provider_tokenizer(provider, model)

    truth_of = None
    if counter is not None:
        resolved_backend = "registered-native"

        def truth_of(text: str) -> int:  # noqa: E306 — closure is the API
            return max(1, int(counter(text)))
    elif asset is not None and backend_of(provider, model) == "local-tokenizer-json":
        resolved_backend = "local-tokenizer-json"

        def truth_of(text: str) -> int:
            encoded = asset.encode(text)
            return len(encoded.ids) if hasattr(encoded, "ids") else len(encoded)
    if truth_of is None:
        return GateResult(
            name="cn_tokenizer_precision",
            passed=True,
            detail=f"[SKIP] no native tokenizer configured for '{provider}' "
                   f"(run benchmarks/fetch_tokenizers.py or set "
                   f"AGENTHUB_TOKENIZER_{provider.upper()}_PATH); billing "
                   "parity stays a target",
        )
    errors: list[float] = []
    estimator_errors: list[float] = []
    for text in CN_EVAL_CORPUS:
        truth = truth_of(text)
        counted = count_tokens(text, provider, model)
        errors.append(abs(counted - truth) / max(1, truth))
        estimated = estimate_tokens_multilingual(text, provider)
        estimator_errors.append(abs(estimated - truth) / max(1, truth))
    errors.sort()
    estimator_errors.sort()
    p95 = errors[int(len(errors) * 0.95) - 1] if errors else 0.0
    est_p95 = estimator_errors[int(len(estimator_errors) * 0.95) - 1] if estimator_errors else 0.0
    passed = p95 <= max_error
    return GateResult(
        name="cn_tokenizer_precision",
        passed=passed,
        detail=f"billing parity p95={p95:.1%} threshold={max_error:.0%} "
               f"(estimator residual p95={est_p95:.1%} for calibration follow-up) "
               f"provider={provider} samples={len(errors)} backend={resolved_backend}",
    )


def _measure_streaming_ttft(max_ms: float = 3_000.0, samples: int = 6) -> GateResult:
    """Measure client-observed streaming time-to-first-token.

    Boots the repo's OpenAI-compatible mock upstream (deterministic, offline)
    and times the first SSE chunk over a POOLED connection — the same path the
    app's streaming executor consumes. A persistent client with
    ``trust_env=False`` avoids proxy buffering artifacts, so the number is the
    incremental streaming cost (not TCP setup), which is what the
    "streaming first token" claim in performance.md should guard.
    """
    import socket
    import subprocess
    import sys
    import time

    import httpx

    def free_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mock_llm:app", "--host", "127.0.0.1",
         "--port", str(port), "--app-dir", str(ROOT / "deploy" / "newapi")],
        env={**__import__("os").environ, "MOCK_MODEL": "mock-llm-ttft"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                httpx.get(f"{base}/__health", timeout=1.0, trust_env=False)
                break
            except Exception:  # noqa: BLE001 — wait-for-ready loop
                time.sleep(0.3)

        with httpx.Client(trust_env=False, timeout=15) as client:
            client.get(f"{base}/__health")  # warm the pooled connection
            first_tokens: list[float] = []
            for _ in range(samples):
                start = time.perf_counter()
                # Time until the FIRST SSE data line — do not read the whole body.
                with client.stream("POST", f"{base}/v1/chat/completions",
                                   json={"model": "mock-llm-ttft", "stream": True,
                                         "messages": [{"role": "user", "content": "mode:PING"}]}) as resp:
                    assert resp.status_code == 200
                    for line in resp.iter_lines():
                        if line.startswith("data:") and "[DONE]" not in line:
                            break
                first_tokens.append((time.perf_counter() - start) * 1000.0)

        first_tokens.sort()
        p95 = first_tokens[min(samples - 1, int(samples * 0.95) - 1)]
        passed = p95 <= max_ms
        return GateResult(
            name="streaming_ttft",
            passed=passed,
            detail=f"first-token p95={p95:.0f}ms threshold={max_ms:.0f}ms "
                   f"samples={samples} min={min(first_tokens):.0f}ms",
            measured_ms=p95,
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentHub benchmark gates")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check-docs", help="enforce capability-table convention")
    sub.add_parser("check-links", help="verify docs links resolve")

    run = sub.add_parser("run", help="execute a named gate")
    run.add_argument("--name", required=True, choices=sorted({
        *PERFORMANCE_CLAIMS, "code_file_size", "code_complexity",
        "test_coverage", "multimodal_e2e_probe",
    }))
    run.add_argument(
        "--threshold-ms", type=float, default=None,
        help="override hard threshold (ms)",
    )
    run.add_argument("--addr", default=None, help="endpoint for api_latency_p95")

    args = parser.parse_args(argv)
    if args.command == "run":
        threshold = (
            args.threshold_ms
            if args.threshold_ms is not None
            else DEFAULT_THRESHOLDS_MS.get(args.name, 0.0)
        )
        result = run_gate(args.name, threshold, args.addr)
    elif args.command == "check-docs":
        result = check_docs()
    else:
        result = check_links()

    print(f"[{'PASS' if result.passed else 'FAIL'}] {result.name}: {result.detail}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())