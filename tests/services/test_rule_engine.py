"""Rule engine tests for T1-1 Subscribe/Rule Trigger."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.services.rule_engine import (
    AgentRule,
    RuleAction,
    RuleHit,
    RuleSyntaxError,
    RuleTrigger,
    RulesCache,
    clear_rules_cache_registry,
    discover_rules_file,
    evaluate_rules,
    get_or_create_rules_cache,
    load_rules,
    load_rules_from_path,
)


# ── YAML loading & validation ────────────────────────────────────


VALID_YAML = """
version: 1
rules:
  - id: auto-deploy
    description: Deploy when keywords hit
    trigger:
      kind: keyword
      keywords: [deploy, release, 部署]
    action:
      kind: create_mission
      target_agent: devops
      objective_template: "trigger {rule.id}"
      require_confirmation: true

  - id: monitor-error
    description: Alert on error
    trigger:
      kind: regex
      pattern: '(?i)\\b(error|exception|traceback)\\b'
    action:
      kind: reply_only

  - id: ping-dev
    description: Pings dev agent
    trigger:
      kind: mention
      mention: dev
    action:
      kind: reply_only
"""


class TestLoadRules:
    def test_parses_three_rules(self):
        rules = load_rules(VALID_YAML)
        assert len(rules) == 3
        assert [r.id for r in rules] == ["auto-deploy", "monitor-error", "ping-dev"]

    def test_empty_text_returns_empty(self):
        assert load_rules("") == []
        assert load_rules("   \n\n  ") == []

    def test_no_rules_key_returns_empty(self):
        assert load_rules("version: 1") == []
        assert load_rules("rules: []") == []

    def test_unknown_version_rejected(self):
        with pytest.raises(RuleSyntaxError, match="version"):
            load_rules("version: 99\nrules: []")

    def test_bad_yaml_raises(self):
        with pytest.raises(RuleSyntaxError, match="not valid YAML"):
            load_rules("rules: [{{broken]")

    def test_missing_id_rejected(self):
        with pytest.raises(RuleSyntaxError, match="id"):
            load_rules('rules: [{trigger: {kind: keyword, keywords: [a]}}]')

    def test_duplicate_id_rejected(self):
        with pytest.raises(RuleSyntaxError, match="duplicate"):
            load_rules(
                """
                rules:
                  - id: dup
                    trigger: {kind: keyword, keywords: [a]}
                  - id: dup
                    trigger: {kind: keyword, keywords: [b]}
                """
            )

    def test_bad_trigger_kind_rejected(self):
        with pytest.raises(RuleSyntaxError, match="trigger.kind"):
            load_rules('rules: [{id: "x", trigger: {kind: "weird"}}]')

    def test_bad_regex_pattern_rejected(self):
        with pytest.raises(RuleSyntaxError, match="regex"):
            load_rules('rules: [{id: "x", trigger: {kind: regex, pattern: "["}}]')

    def test_keyword_kind_requires_keywords(self):
        with pytest.raises(RuleSyntaxError):
            load_rules('rules: [{id: "x", trigger: {kind: keyword}}]')

    def test_mention_kind_requires_mention(self):
        with pytest.raises(RuleSyntaxError):
            load_rules('rules: [{id: "x", trigger: {kind: mention}}]')

    def test_action_defaults_to_reply_only(self):
        rules = load_rules(
            """
            rules:
              - id: r1
                trigger: {kind: keyword, keywords: [x]}
            """
        )
        assert rules[0].action.kind == "reply_only"
        assert rules[0].action.require_confirmation is True


# ── Trigger matching ──────────────────────────────────────────────


class TestRuleTriggerMatches:
    def test_keyword_any_match(self):
        t = RuleTrigger(kind="keyword", keywords=("deploy", "release"))
        assert t.matches("please deploy it")
        assert t.matches("Release notes are out")
        assert t.matches("DEPLOY now")

    def test_keyword_case_insensitive(self):
        t = RuleTrigger(kind="keyword", keywords=("deploy",))
        assert t.matches("DEPLOY THE THING")

    def test_keyword_no_match(self):
        t = RuleTrigger(kind="keyword", keywords=("deploy",))
        assert not t.matches("nothing here")

    def test_keyword_cjk(self):
        t = RuleTrigger(kind="keyword", keywords=("部署",))
        assert t.matches("需要部署一下")

    def test_regex_match(self):
        t = RuleTrigger(kind="regex", pattern=r"(?i)\berror\b")
        assert t.matches("got an error here")
        assert t.matches("ERROR occurred")
        # hyphen is a non-word char so \b still matches — that's fine;
        # rules use patterns at their own risk.
        assert t.matches("error-prone")
        # but embedded inside a word is correctly excluded
        t2 = RuleTrigger(kind="regex", pattern=r"(?i)\berror\b")
        assert not t2.matches("erroring-out")  # 'ing' is word char, no trailing \b

    def test_mention_at_prefix(self):
        t = RuleTrigger(kind="mention", mention="dev")
        assert t.matches("@dev fix this")

    def test_mention_word_boundary(self):
        t = RuleTrigger(kind="mention", mention="dev")
        assert t.matches("ping dev please")
        assert not t.matches("developer meeting")

    def test_empty_message_never_matches(self):
        t = RuleTrigger(kind="keyword", keywords=("x",))
        assert not t.matches("")
        assert not t.matches("   \n  ")


# ── evaluate_rules orchestration ───────────────────────────────────


class TestEvaluateRules:
    def test_keyword_hits(self):
        rules = load_rules(VALID_YAML)
        hits = evaluate_rules(rules, "Please deploy the new version")
        ids = [h.rule.id for h in hits]
        assert "auto-deploy" in ids

    def test_regex_hits(self):
        rules = load_rules(VALID_YAML)
        hits = evaluate_rules(rules, "got a NullPointerException error")
        ids = [h.rule.id for h in hits]
        assert "monitor-error" in ids

    def test_mention_hits(self):
        rules = load_rules(VALID_YAML)
        hits = evaluate_rules(rules, "@dev can you look at this")
        ids = [h.rule.id for h in hits]
        assert "ping-dev" in ids

    def test_no_hits(self):
        rules = load_rules(VALID_YAML)
        hits = evaluate_rules(rules, "nothing special here")
        assert hits == []

    def test_multi_match(self):
        rules = load_rules(VALID_YAML)
        hits = evaluate_rules(rules, "deploy the fix and ping @dev about the error")
        ids = {h.rule.id for h in hits}
        assert ids >= {"auto-deploy", "monitor-error", "ping-dev"}

    def test_limit_caps_hits(self):
        rules = load_rules(VALID_YAML)
        hits = evaluate_rules(rules, "deploy error @dev", limit=2)
        assert len(hits) <= 2

    def test_limit_default_is_five(self):
        rules = load_rules(VALID_YAML)
        hits = evaluate_rules(rules, "deploy error @dev")
        assert len(hits) == 3  # exactly 3 rules all match

    def test_empty_message_returns_empty(self):
        rules = load_rules(VALID_YAML)
        assert evaluate_rules(rules, "") == []

    def test_empty_rules_returns_empty(self):
        assert evaluate_rules([], "deploy now") == []


# ── T4: File loading + hot-reload cache ──────────────────────────


VALID_FILE_YAML = """
version: 1
rules:
  - id: file-rule-a
    description: From YAML file
    trigger:
      kind: keyword
      keywords: [deploy, release]
    action:
      kind: create_mission
      target_agent: devops
"""


class TestLoadRulesFromPath:
    def test_reads_and_parses(self, tmp_path):
        f = tmp_path / ".agenthub" / "rules.yaml"
        f.parent.mkdir()
        f.write_text(VALID_FILE_YAML, encoding="utf-8")
        rules = load_rules_from_path(f)
        assert len(rules) == 1
        assert rules[0].id == "file-rule-a"

    def test_missing_file_returns_empty(self, tmp_path):
        rules = load_rules_from_path(tmp_path / "no_such_file.yaml")
        assert rules == []

    def test_syntax_error_raises(self, tmp_path):
        f = tmp_path / ".agenthub" / "rules.yaml"
        f.parent.mkdir()
        f.write_text("rules: [{{broken]", encoding="utf-8")
        with pytest.raises(RuleSyntaxError):
            load_rules_from_path(f)


class TestDiscoverRulesFile:
    def test_finds_in_workspace_root(self, tmp_path, monkeypatch):
        rules_dir = tmp_path / ".agenthub"
        rules_dir.mkdir()
        (rules_dir / "rules.yaml").write_text("version: 1\nrules: []\n")
        # Also make sure cwd doesn't have one (it shouldn't in tmp_path)
        result = discover_rules_file(tmp_path)
        assert result is not None
        assert result.name == "rules.yaml"
        assert ".agenthub" in str(result)

    def test_falls_back_to_cwd(self, tmp_path, monkeypatch):
        # Arrange: no workspace_root rules, but cwd has one.
        (tmp_path / ".agenthub").mkdir()
        (tmp_path / ".agenthub" / "rules.yaml").write_text("version: 1\nrules: []\n")
        monkeypatch.chdir(tmp_path)
        result = discover_rules_file(Path("/nonexistent"))
        assert result is not None
        assert result.parent.name == ".agenthub"

    def test_none_when_no_candidate(self, tmp_path):
        result = discover_rules_file(tmp_path)
        assert result is None

    def test_workspace_root_wins_over_cwd(self, tmp_path, monkeypatch):
        ws_root = tmp_path / "project"
        ws_root.mkdir()
        (ws_root / ".agenthub").mkdir()
        (ws_root / ".agenthub" / "rules.yaml").write_text("version: 1\nrules:\n  - id: ws\n")

        cwd_dir = tmp_path / "cwd"
        cwd_dir.mkdir()
        (cwd_dir / ".agenthub").mkdir()
        (cwd_dir / ".agenthub" / "rules.yaml").write_text("version: 1\nrules:\n  - id: cwd\n")
        monkeypatch.chdir(cwd_dir)

        result = discover_rules_file(ws_root)
        assert result is not None
        # Should be the workspace_root one, not the cwd one
        assert "project" in str(result)
        assert "cwd" not in str(result)


class TestRulesCache:
    def test_returns_empty_when_missing(self, tmp_path):
        cache = RulesCache(tmp_path / "rules.yaml")
        rules, err = cache.get_rules()
        assert rules == []
        assert err is None

    def test_loads_on_first_access(self, tmp_path):
        f = tmp_path / ".agenthub" / "rules.yaml"
        f.parent.mkdir()
        f.write_text(VALID_FILE_YAML, encoding="utf-8")
        cache = RulesCache(f)
        rules, err = cache.get_rules()
        assert err is None
        assert len(rules) == 1
        assert rules[0].id == "file-rule-a"

    def test_hot_reload_on_mtime_change(self, tmp_path):
        f = tmp_path / "rules.yaml"
        f.write_text(VALID_FILE_YAML, encoding="utf-8")
        cache = RulesCache(f)

        rules1, _ = cache.get_rules()
        assert len(rules1) == 1

        # Sleep to ensure a different mtime (filesystem granularity).
        time.sleep(0.05)
        f.write_text(
            VALID_FILE_YAML
            + "\n  - id: second-rule\n    description: added later\n"
            + "    trigger:\n      kind: keyword\n      keywords: [extra]\n",
            encoding="utf-8",
        )
        # Touch mtime explicitly for filesystems with low precision.
        f.touch()

        rules2, _ = cache.get_rules()
        assert len(rules2) == 2
        ids = {r.id for r in rules2}
        assert ids == {"file-rule-a", "second-rule"}

    def test_keeps_last_good_on_syntax_error(self, tmp_path):
        f = tmp_path / "rules.yaml"
        f.write_text(VALID_FILE_YAML, encoding="utf-8")
        cache = RulesCache(f)
        rules1, err1 = cache.get_rules()
        assert len(rules1) == 1
        assert err1 is None

        # Corrupt the file
        time.sleep(0.05)
        f.write_text("rules: [{{broken]", encoding="utf-8")
        f.touch()

        rules2, err2 = cache.get_rules()
        # Keeps previous good rules
        assert len(rules2) == 1
        assert "not valid YAML" in (err2 or "") or "rules.yaml" in (err2 or "")

    def test_file_removed_resets_to_empty(self, tmp_path):
        f = tmp_path / "rules.yaml"
        f.write_text(VALID_FILE_YAML, encoding="utf-8")
        cache = RulesCache(f)
        rules1, _ = cache.get_rules()
        assert len(rules1) == 1

        f.unlink()
        rules2, err = cache.get_rules()
        assert rules2 == []
        assert err is None

    def test_invalidate_forces_reload(self, tmp_path):
        f = tmp_path / "rules.yaml"
        f.write_text(VALID_FILE_YAML, encoding="utf-8")
        cache = RulesCache(f)
        cache.get_rules()  # warm up

        f.write_text(
            VALID_FILE_YAML
            + "\n  - id: more\n    description: extra\n"
            + "    trigger:\n      kind: keyword\n      keywords: [x]\n",
            encoding="utf-8",
        )
        # Without invalidate, same mtime → no reload
        # (We can't easily force same mtime, so just verify invalidate works)
        cache.invalidate()
        rules, _ = cache.get_rules()
        assert len(rules) == 2

    def test_registry_memoises_per_path(self, tmp_path):
        clear_rules_cache_registry()
        f = tmp_path / "rules.yaml"
        f.write_text(VALID_FILE_YAML, encoding="utf-8")

        c1 = get_or_create_rules_cache(f)
        c2 = get_or_create_rules_cache(f)
        assert c1 is c2  # same object

        # Different path → different cache
        f2 = tmp_path / "other.yaml"
        f2.write_text(VALID_FILE_YAML, encoding="utf-8")
        c3 = get_or_create_rules_cache(f2)
        assert c3 is not c1

    def test_clear_registry_works(self, tmp_path):
        clear_rules_cache_registry()
        f = tmp_path / "rules.yaml"
        f.write_text(VALID_FILE_YAML, encoding="utf-8")
        c1 = get_or_create_rules_cache(f)
        clear_rules_cache_registry()
        c2 = get_or_create_rules_cache(f)
        assert c1 is not c2
