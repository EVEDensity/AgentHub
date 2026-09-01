"""Flat key-scoped project facts (ADR-0107 storage shape item 3).

`.agenthub/memory.md` keeps durable project facts as a flat Markdown
file: one ``## <section>`` header per topic, one ``- <key>: <value>``
line per fact. Writing an existing (section, key) supersedes the old
value in place — the file is never re-written from scratch and unrelated
facts keep their original order and wording.

Injection is gated (ADR-0107 item 4): only facts whose section, key, or
value shares a keyword with the current objective enter the prompt; the
whole store is never injected by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MEMORY_FILE_NAME = "memory.md"

_STOP_WORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "has", "have", "in", "is", "it", "of", "on", "or", "that", "the",
        "this", "to", "was", "were", "will", "with", "you", "the", "and",
        "的", "了", "在", "是", "和", "与", "用", "并", "一个",
    }
)


@dataclass(frozen=True)
class Fact:
    section: str
    key: str
    value: str

    @property
    def dotted(self) -> str:
        return f"{self.section}.{self.key}"


def memory_file_path(state_dir: Path) -> Path:
    """Location of the project facts file inside the state directory."""
    return state_dir / MEMORY_FILE_NAME


def parse_facts(text: str) -> list[Fact]:
    """Parse the facts file. Unknown lines are skipped, never guessed."""
    facts: list[Fact] = []
    section = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if not line.startswith("- "):
            continue
        body = line[2:]
        if ":" not in body or not section:
            continue
        key, _, value = body.partition(":")
        key = key.strip()
        value = value.strip()
        if key and value:
            facts.append(Fact(section=section, key=key, value=value))
    return facts


def render_facts(facts: list[Fact]) -> str:
    """Render facts grouped by section as a Markdown block."""
    if not facts:
        return ""
    sections: dict[str, list[Fact]] = {}
    for fact in facts:
        sections.setdefault(fact.section, []).append(fact)
    blocks = []
    for section, items in sections.items():
        lines = [f"## {section}"]
        lines.extend(f"- {item.key}: {item.value}" for item in items)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _write_facts(path: Path, facts: list[Fact]) -> None:
    blocks = ["# AgentHub Project Facts", ""]
    rendered = render_facts(facts)
    if rendered:
        blocks.append(rendered)
    blocks.append("")
    path.write_text("\n".join(blocks), encoding="utf-8")


def load_facts(path: Path) -> list[Fact]:
    """Read facts from disk; a missing file is an empty store."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return parse_facts(text)


def set_fact(path: Path, section: str, key: str, value: str) -> str:
    """Upsert one fact. Same (section, key) supersedes in place.

    Returns ``"set"`` when a new fact was appended and ``"updated"`` when
    an existing fact's value was replaced.
    """
    facts = load_facts(path)
    for index, fact in enumerate(facts):
        if fact.section == section and fact.key == key:
            if fact.value == value:
                return "unchanged"
            facts[index] = Fact(section=section, key=key, value=value)
            _write_facts(path, facts)
            return "updated"
    facts.append(Fact(section=section, key=key, value=value))
    _write_facts(path, facts)
    return "set"


def remove_fact(path: Path, section: str, key: str) -> bool:
    """Remove one fact. Returns whether it existed."""
    facts = load_facts(path)
    remaining = [
        fact
        for fact in facts
        if not (fact.section == section and fact.key == key)
    ]
    if len(remaining) == len(facts):
        return False
    _write_facts(path, remaining)
    return True


def _terms(text: str) -> set[str]:
    """Lowercased significant terms for gated matching."""
    terms: set[str] = set()
    for token in text.replace(",", " ").replace(".", " ").split():
        token = token.strip().lower()
        if len(token) >= 2 and token not in _STOP_WORDS:
            terms.add(token)
    return terms


def select_facts_for_objective(
    facts: list[Fact], objective: str, *, limit: int = 8
) -> list[Fact]:
    """Gated injection: keep facts sharing a term with the objective.

    Scored by overlap count (section/key/value all count); facts with no
    overlap are dropped. With no objective terms everything is scored
    zero, so nothing is injected — the store never leaks wholesale.
    """
    objective_terms = _terms(objective)
    if not objective_terms:
        return []
    scored: list[tuple[int, int, Fact]] = []
    for order, fact in enumerate(facts):
        fact_terms = _terms(
            f"{fact.section} {fact.key} {fact.value}"
        )
        overlap = len(objective_terms & fact_terms)
        if overlap:
            scored.append((overlap, -order, fact))
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return [entry[2] for entry in scored[: max(limit, 0)]]


def facts_block_for_objective(
    state_dir: Path, objective: str, *, limit: int = 8
) -> str:
    """Load the store and return the gated facts block (may be empty)."""
    facts = load_facts(memory_file_path(state_dir))
    selected = select_facts_for_objective(facts, objective, limit=limit)
    if not selected:
        return ""
    return "### 项目事实（.agenthub/memory.md，按当前目标门控注入）\n\n" + render_facts(
        selected
    )
