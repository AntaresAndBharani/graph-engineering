"""
Unit test suite for Antigravity Tri-Party Architect & QA Cross-Review Script.
Tests:
  - Iteration counting (Author, Architect incl. legacy Gemini headings, QA incl. legacy Claude QA headings)
  - Plan archiving and scoped reading guides
  - Prompt construction with QA mandates and BLOCKING/NON-BLOCKING verdict rules
  - Dual verdict parsing (Architect & QA)
  - Disagreement extraction
  - Subprocess invocation flags for claude (Architect) and agy (QA), always fresh sessions
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Add skill script directory to sys.path
_repo_root = Path(__file__).resolve().parent.parent
SKILL_SCRIPT_DIR = _repo_root / "skills" / "agy-architect-review" / "scripts"
if not SKILL_SCRIPT_DIR.exists():
    SKILL_SCRIPT_DIR = _repo_root / ".agents" / "skills" / "agy-architect-review" / "scripts"
if str(SKILL_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPT_DIR))

import agy_cross_review  # noqa: E402


def _claude_json(result: str, *, is_error: bool = False, duration_ms: int = 1000, num_turns: int = 1, spawned: int = 0) -> str:
    """A `claude -p --output-format json` payload with the fields the script reads."""
    return json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": is_error,
        "result": result,
        "duration_ms": duration_ms,
        "num_turns": num_turns,
        "total_cost_usd": 0.12,
        "usage": {
            "input_tokens": 10,
            "cache_read_input_tokens": 500,
            "cache_creation_input_tokens": 2000,
            "output_tokens": 300,
        },
        "subagent_stats": {"spawned": spawned},
    })


class TestAgyCrossReview:

    def test_count_iterations_all_parties(self):
        sample_plan = """
# 📋 Implementation Plan: Feature X

## 🔍 Review Iteration 1 (Author Perspective)
Initial thoughts.

## 🏛️ Gemini Architect Review Iteration 1
Architect critique.

## 🧪 Claude QA Review Iteration 1 (Requirements & UX/UI Guardian)
QA critique.

## 🔍 Review Iteration 2 (Author Response)
Addressed concerns.

## 🏛️ Architect Review Iteration 2
Second architect critique.

## 🧪 Claude QA Review Iteration 2 (Requirements & UX/UI Guardian)
Second QA critique.
"""
        author, architect, qa, chief_fa = agy_cross_review.count_iterations(sample_plan)
        assert author == 2
        assert architect == 2
        assert qa == 2
        assert chief_fa == 0

    def test_count_iterations_partial_rounds(self):
        sample_plan = """
# 📋 Implementation Plan: Feature Y

## 🔍 Review Iteration 1 (Author Perspective)
Initial.

## 🏛️ Architect Review Iteration 1
Architect reviewed first.
"""
        author, architect, qa, chief_fa = agy_cross_review.count_iterations(sample_plan)
        assert author == 1
        assert architect == 1
        assert qa == 0
        assert chief_fa == 0

    def test_count_iterations_active_plan_only(self):
        plan = """
# 📋 Implementation Plan: Old Feature
## 🏛️ Gemini Architect Review Iteration 1
## 🏛️ Gemini Architect Review Iteration 2

# 📋 Implementation Plan: New Feature
## 🔍 Review Iteration 1 (Author Perspective)
## 🏛️ Architect Review Iteration 1
## 🧪 Claude QA Review Iteration 1 (Requirements & UX/UI Guardian)
"""
        assert agy_cross_review.count_iterations(plan) == (1, 1, 1, 0)

    def test_build_qa_prompt_mandates(self, tmp_path):
        dummy_plan = tmp_path / "implementation-plan.md"
        dummy_plan.write_text("# Plan", encoding="utf-8")

        prompt = agy_cross_review.build_qa_prompt(round_num=2, plan_path=dummy_plan, guide=["- Active plan: lines 1-1"])

        # Verify key mandates are present
        assert "QA Lead & Requirements Guardian" in prompt
        assert "Round 2" in prompt
        assert "REQUIREMENTS FIDELITY (ANTI-DRIFT GUARDIAN)" in prompt
        assert "UX/UI EXPERIENCE AUDIT" in prompt
        assert "FUNCTIONAL RIGOR AUDIT" in prompt
        assert "BDD ACCEPTANCE CRITERIA COMPLETENESS" in prompt
        assert "## 🧪 QA Review Iteration 2 (Requirements & UX/UI Guardian)" in prompt
        assert "Claude QA" not in prompt
        assert "VERDICT: AGREED" in prompt
        assert "VERDICT: DISAGREED" in prompt
        assert "[BLOCKING]" in prompt
        assert "[NON-BLOCKING]" in prompt
        assert "- Active plan: lines 1-1" in prompt

    def test_build_architect_prompt_structure(self, tmp_path):
        dummy_plan = tmp_path / "implementation-plan.md"
        dummy_plan.write_text("# Plan", encoding="utf-8")

        prompt = agy_cross_review.build_architect_prompt(
            round_num=1, plan_path=dummy_plan, guide=["- Latest author iteration: lines 3-9"]
        )

        assert "System Architect" in prompt
        assert "Round 1" in prompt
        assert "## 🏛️ Architect Review Iteration 1" in prompt
        assert "VERDICT: AGREED" in prompt
        assert "no BLOCKING objections" in prompt
        assert "Read ONLY these line ranges" in prompt
        assert "- Latest author iteration: lines 3-9" in prompt
        # The old framing forced a DISAGREED verdict on every round
        assert "hyper-critical" not in prompt
        assert "zero reservations" not in prompt

    def test_reading_guide_targets_active_plan_sections(self):
        plan = "\n".join([
            "# 📋 Implementation Plan: Old",          # 1
            "## 🎯 Final Decision Plan (old)",        # 2
            "old",                                    # 3
            "# 📋 Implementation Plan: New",          # 4
            "## 📝 Initial Draft Proposal",           # 5
            "operator request",                       # 6
            "## 🔍 Review Iteration 1 (Author)",      # 7
            "```",                                    # 8
            "## not a heading inside a fence",        # 9
            "```",                                    # 10
            "## 🏛️ Architect Review Iteration 1",     # 11
            "VERDICT: DISAGREED",                     # 12
            "## 🔍 Review Iteration 2 (Author)",      # 13
            "response",                               # 14
            "## 🎯 Final Decision Plan",              # 15
            "spec",                                   # 16
        ])
        guide = "\n".join(agy_cross_review.reading_guide(plan, "architect", round_num=2))
        assert "original proposal / requirements: lines 5-6" in guide
        assert "Current Final Decision Plan: lines 15-16" in guide
        assert "Latest author iteration: lines 13-14" in guide
        assert "previous review (round 1): lines 11-12" in guide
        assert "lines 2-" not in guide

        qa_guide = "\n".join(agy_cross_review.reading_guide(plan, "qa", round_num=1))
        assert "previous review" not in qa_guide

    def test_reading_guide_falls_back_to_active_plan(self):
        guide = agy_cross_review.reading_guide("# 📋 Implementation Plan: X\nfree text\n", "architect", round_num=1)
        assert guide == ["- Active plan: lines 1-2"]

    def test_archive_plans_keeps_only_active_plan(self, tmp_path):
        plan_path = tmp_path / "implementation-plan.md"
        plan_path.write_text(
            "# 📋 Implementation Plan & Refinement Lifecycle: Config Reload\nold one\n"
            "```\n# 📋 Implementation Plan inside a fence\n```\n"
            "# 📋 Implementation Plan: TUI Streaming\nold two\n"
            "# 📋 Implementation Plan: Active Feature\ncurrent\n",
            encoding="utf-8",
        )
        written = agy_cross_review.archive_plans(plan_path, keep_active=True, now=datetime(2026, 9, 23, 10, 0, 0))

        assert [p.name for p in written] == [
            "20260923-100000-01-config-reload.md",
            "20260923-100000-02-tui-streaming.md",
        ]
        assert all(p.parent == tmp_path / "archive" for p in written)
        assert "inside a fence" in written[0].read_text(encoding="utf-8")
        assert "old two" in written[1].read_text(encoding="utf-8")
        assert plan_path.read_text(encoding="utf-8") == "# 📋 Implementation Plan: Active Feature\ncurrent\n"

        # A single active plan is never archived implicitly
        assert agy_cross_review.archive_plans(plan_path, keep_active=True) == []
        assert "current" in plan_path.read_text(encoding="utf-8")

    def test_archive_plans_all(self, tmp_path):
        plan_path = tmp_path / "implementation-plan.md"
        plan_path.write_text("# 📋 Implementation Plan: Done Feature\nbody\n", encoding="utf-8")

        written = agy_cross_review.archive_plans(plan_path, keep_active=False)

        assert len(written) == 1
        assert "body" in written[0].read_text(encoding="utf-8")
        assert plan_path.read_text(encoding="utf-8") == ""

    def test_parse_architect_verdict(self):
        agreed_plan = """
## 🏛️ Architect Review Iteration 1
- [NON-BLOCKING] Consider an index.
VERDICT: AGREED
"""
        assert agy_cross_review.parse_architect_verdict(agreed_plan, "", 1) == "AGREED"

        legacy_disagreed_plan = """
## 🏛️ Gemini Architect Review Iteration 1
Critical race condition found.
VERDICT: DISAGREED
"""
        assert agy_cross_review.parse_architect_verdict(legacy_disagreed_plan, "", 1) == "DISAGREED"
        assert agy_cross_review.parse_architect_verdict("no review section", "", 1) == "DISAGREED"

    def test_parse_qa_verdict(self):
        agreed_plan = """
## 🧪 Claude QA Review Iteration 1 (Requirements & UX/UI Guardian)
Requirements strictly respected, UX ergonomics approved.
VERDICT: AGREED
"""
        assert agy_cross_review.parse_qa_verdict(agreed_plan, "", 1) == "AGREED"

        disagreed_plan = """
## 🧪 Claude QA Review Iteration 1 (Requirements & UX/UI Guardian)
Plan dropped the operator's invariant constraint on quiescence.
VERDICT: DISAGREED
"""
        assert agy_cross_review.parse_qa_verdict(disagreed_plan, "", 1) == "DISAGREED"

    def test_extract_disagreement_points(self):
        plan = """
## 🏛️ Architect Review Iteration 1
### ⚖️ Critical Architecture & Drawbacks Critique
- Subprocess timeout retains index lock on Windows.
- SQLite column missing foreign key constraint.
### 🏁 Verdict
VERDICT: DISAGREED

## 🧪 Claude QA Review Iteration 1 (Requirements & UX/UI Guardian)
### 🎯 Requirements Fidelity Audit
- Operator constraint 'never start in middle of dev' not guaranteed in failure modes.
- CLI table missing active project indicator column.
### 🏁 Verdict
VERDICT: DISAGREED
"""
        architect_points = agy_cross_review.extract_disagreement_points(plan, 1, header_prefix="🏛️")
        assert len(architect_points) == 2
        assert "Subprocess timeout" in architect_points[0]

        qa_points = agy_cross_review.extract_disagreement_points(plan, 1, header_prefix="🧪")
        assert len(qa_points) == 2
        assert "Operator constraint" in qa_points[0]

    def test_extract_disagreement_points_prefers_blocking(self):
        plan = """
## 🏛️ Architect Review Iteration 2
- [NON-BLOCKING] Rename the helper for clarity.
- [BLOCKING] Lock is never released on timeout.
VERDICT: DISAGREED
"""
        points = agy_cross_review.extract_disagreement_points(plan, 2, header_prefix="🏛️")
        assert points == ["[BLOCKING] Lock is never released on timeout."]

    @patch("subprocess.run")
    def test_invoke_claude_arguments(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=_claude_json("OK"), stderr="")

        agy_cross_review.invoke_claude(prompt="Architect Prompt", model="claude-opus-5-5", effort="medium")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
        assert cmd[cmd.index("--effort") + 1] == "medium"
        assert "--no-session-persistence" in cmd
        assert "--dangerously-skip-permissions" in cmd
        # Every round is a fresh session: no resume and no fixed session id
        assert "-r" not in cmd
        assert "-c" not in cmd
        assert "--session-id" not in cmd
        # Read-only code tools only (no Bash/Edit/Write/Agent), no MCP servers, JSON metrics
        assert cmd[cmd.index("--tools") + 1] == "Read,Grep,Glob"
        assert "--strict-mcp-config" in cmd
        assert "--mcp-config" not in cmd
        assert cmd[cmd.index("--output-format") + 1] == "json"
        # Prompt goes through stdin, never argv
        assert "Architect Prompt" not in cmd
        assert mock_run.call_args[1]["input"] == "Architect Prompt"
        assert mock_run.call_args[1]["timeout"] == agy_cross_review.DEFAULT_CLAUDE_TIMEOUT_S

    @patch("subprocess.run")
    def test_invoke_claude_long_prompt_stays_out_of_command_line(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=_claude_json("OK"), stderr="")
        huge_prompt = "x" * 100_000  # far above the Windows 32,767-char command-line limit

        agy_cross_review.invoke_claude(prompt=huge_prompt, model="claude-opus-5-5", effort="high")
        cmd = mock_run.call_args[0][0]
        assert len(" ".join(cmd)) < 1000
        assert mock_run.call_args[1]["input"] == huge_prompt

    @patch("subprocess.run")
    def test_invoke_claude_parses_reply_and_metrics(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=_claude_json("## Review\nVERDICT: APPROVED", duration_ms=4200, num_turns=3, spawned=0),
            stderr="",
        )

        code, reply, _, metrics = agy_cross_review.invoke_claude("p", model="claude-opus-5-5", effort="high", tools="Read")
        assert code == 0
        assert reply == "## Review\nVERDICT: APPROVED"
        assert metrics["duration_ms"] == 4200
        assert metrics["num_turns"] == 3
        assert metrics["input_tokens"] == 10
        assert metrics["cache_read_input_tokens"] == 500
        assert metrics["cache_creation_input_tokens"] == 2000
        assert metrics["output_tokens"] == 300
        assert metrics["subagents_spawned"] == 0
        assert metrics["is_error"] is False
        cmd = mock_run.call_args[0][0]
        assert cmd[cmd.index("--tools") + 1] == "Read"

    @patch("subprocess.run")
    def test_invoke_claude_error_result_is_nonzero(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=_claude_json("boom", is_error=True), stderr="")

        code, _, _, metrics = agy_cross_review.invoke_claude("p", model="claude-opus-5-5", effort="high")
        assert code == 1
        assert metrics["is_error"] is True

    @patch("subprocess.run")
    def test_invoke_claude_non_json_output_is_flagged_not_dropped(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="plain text reply", stderr="")

        code, reply, _, metrics = agy_cross_review.invoke_claude("p", model="claude-opus-5-5", effort="high")
        assert code == 0
        assert reply == "plain text reply"
        assert "output_format_error" in metrics

    @patch("subprocess.run")
    def test_invoke_claude_timeout(self, mock_run):
        mock_run.side_effect = agy_cross_review.subprocess.TimeoutExpired(cmd="claude", timeout=5)

        code, reply, stderr, metrics = agy_cross_review.invoke_claude("p", model="claude-opus-5-5", effort="high", timeout_s=5)
        assert code == 124
        assert reply == ""
        assert "timed out after 5s" in stderr
        assert metrics == {"timed_out": True, "timeout_s": 5}

    def test_find_plan_file_custom_path(self, tmp_path, monkeypatch):
        custom = tmp_path / "specs" / "checkout-redesign.md"
        custom.parent.mkdir()
        custom.write_text("# 📋 Implementation Plan: Checkout\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        assert agy_cross_review.find_plan_file("specs/checkout-redesign.md") == custom.resolve()
        assert agy_cross_review.find_plan_file(str(custom)) == custom.resolve()
        with pytest.raises(FileNotFoundError, match="Specified plan file not found"):
            agy_cross_review.find_plan_file("specs/missing.md")

    def test_find_plan_file_default_searches_upward(self, tmp_path, monkeypatch):
        default = tmp_path / "docs" / "draft-requisites" / "implementation-plan.md"
        default.parent.mkdir(parents=True)
        default.write_text("# 📋 Implementation Plan: X\n", encoding="utf-8")
        nested = tmp_path / "orchestrator" / "nodes"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)

        assert agy_cross_review.find_plan_file(None) == default.resolve()

    def _run_main(self, monkeypatch, capsys, *argv):
        monkeypatch.setattr(sys, "argv", ["agy_cross_review.py", *argv])
        with pytest.raises(SystemExit) as exc:
            agy_cross_review.main()
        return exc.value.code, json.loads(capsys.readouterr().out)

    def test_main_check_status_uses_custom_plan(self, tmp_path, monkeypatch, capsys):
        # A default plan exists too; --plan must win over it.
        default = tmp_path / "docs" / "draft-requisites" / "implementation-plan.md"
        default.parent.mkdir(parents=True)
        default.write_text("# 📋 Implementation Plan: Default\n## 🏛️ Architect Review Iteration 1\n", encoding="utf-8")
        custom = tmp_path / "specs" / "feature.md"
        custom.parent.mkdir()
        custom.write_text(
            "# 📋 Implementation Plan: Feature\n## 🔍 Review Iteration 1 (Author)\n"
            "## 🏛️ Architect Review Iteration 1\n## 🏛️ Architect Review Iteration 2\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        code, out = self._run_main(monkeypatch, capsys, "--plan", "specs/feature.md", "--check-status")

        assert code == 0
        assert Path(out["plan_path"]) == custom.resolve()
        assert out["architect_rounds"] == 2
        assert out["author_rounds"] == 1

    @pytest.mark.parametrize("flag", ["--plan", "--path", "--file"])
    def test_main_accepts_plan_flag_aliases(self, flag, tmp_path, monkeypatch, capsys):
        # A default plan exists too; every alias must select the custom file, never the default.
        default = tmp_path / "docs" / "draft-requisites" / "implementation-plan.md"
        default.parent.mkdir(parents=True)
        default.write_text("# 📋 Implementation Plan: Default\n", encoding="utf-8")
        custom = tmp_path / "docs" / "draft-requisites" / "archive" / "impl_mobile_removal.md"
        custom.parent.mkdir()
        custom.write_text("I want to remove everything related to the mobile app.\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        code, out = self._run_main(monkeypatch, capsys, flag, str(custom), "--check-status")

        assert code == 0
        assert Path(out["plan_path"]) == custom.resolve()

    def test_main_reports_plan_source(self, tmp_path, monkeypatch, capsys):
        default = tmp_path / "docs" / "draft-requisites" / "implementation-plan.md"
        default.parent.mkdir(parents=True)
        default.write_text("# 📋 Implementation Plan: Default\n", encoding="utf-8")
        custom = tmp_path / "specs" / "feature.md"
        custom.parent.mkdir()
        custom.write_text("# 📋 Implementation Plan: Feature\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        code, out = self._run_main(monkeypatch, capsys, "--plan", str(custom), "--check-status")
        assert code == 0
        assert out["plan_source"] == "argument"
        assert Path(out["plan_path"]) == custom.resolve()

        monkeypatch.setattr(sys, "argv", ["agy_cross_review.py", "--check-status"])
        with pytest.raises(SystemExit):
            agy_cross_review.main()
        captured = capsys.readouterr()
        out_default = json.loads(captured.out)
        assert out_default["plan_source"] == "default"
        assert Path(out_default["plan_path"]) == default.resolve()
        assert "WARNING: no --plan given" in captured.err

    def test_main_archive_raw_requirement_is_noop(self, tmp_path, monkeypatch, capsys):
        """A raw requirement file (no plan heading) must never be moved or emptied by --archive."""
        custom = tmp_path / "impl_explorer_filters.md"
        custom.write_text("I want to create filters in asset explorer.\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        code, out = self._run_main(monkeypatch, capsys, "--path", str(custom), "--archive")

        assert code == 0
        assert out["archived_files"] == []
        assert custom.read_text(encoding="utf-8") == "I want to create filters in asset explorer.\n"
        assert not (tmp_path / "archive").exists()

    def test_main_archive_writes_next_to_custom_plan(self, tmp_path, monkeypatch, capsys):
        custom = tmp_path / "specs" / "feature.md"
        custom.parent.mkdir()
        custom.write_text("# 📋 Implementation Plan: Done Feature\nbody\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        code, out = self._run_main(monkeypatch, capsys, "--plan", str(custom), "--archive")

        assert code == 0
        archived = [Path(p) for p in out["archived_files"]]
        assert len(archived) == 1
        assert archived[0].parent == custom.parent / "archive"
        assert "body" in archived[0].read_text(encoding="utf-8")
        assert custom.read_text(encoding="utf-8") == ""
        assert not (tmp_path / "docs").exists()

    def test_main_missing_custom_plan_fails_fast(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)

        code, out = self._run_main(monkeypatch, capsys, "--plan", "specs/nope.md", "--check-status")

        assert code == 1
        assert out["status"] == "error"
        assert "nope.md" in out["message"]

    def test_count_and_parse_new_qa_heading(self):
        plan = """
# 📋 Implementation Plan: Feature Z
## 🔍 Review Iteration 1 (Author Perspective)
## 🏛️ Architect Review Iteration 1
VERDICT: AGREED
## 🧪 QA Review Iteration 1 (Requirements & UX/UI Guardian)
- [BLOCKING] Missing empty-state scenario.
VERDICT: DISAGREED
"""
        assert agy_cross_review.count_iterations(plan) == (1, 1, 1, 0)
        assert agy_cross_review.parse_qa_verdict(plan, "", 1) == "DISAGREED"
        assert agy_cross_review.extract_disagreement_points(plan, 1, header_prefix="🧪") == [
            "[BLOCKING] Missing empty-state scenario."
        ]

    @patch("subprocess.run")
    def test_invoke_agy_arguments(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        agy_cross_review.invoke_agy(prompt="QA Prompt", model="gemini-3.8-flash-medium")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "agy"
        assert cmd[cmd.index("-p") + 1] == "QA Prompt"
        assert cmd[cmd.index("--model") + 1] == "gemini-3.8-flash-medium"
        assert "--dangerously-skip-permissions" in cmd
        assert "--effort" not in cmd
        # Fresh session every round: never continue the most recent conversation
        assert "-c" not in cmd
        assert "--continue" not in cmd

        agy_cross_review.invoke_agy(prompt="QA Prompt", model="gemini-3.8-flash-high", effort="low")
        cmd = mock_run.call_args[0][0]
        assert cmd[cmd.index("--effort") + 1] == "low"

    def test_default_models(self):
        assert agy_cross_review.DEFAULT_ARCHITECT_MODEL == "gemini-3.8-flash-high"
        assert agy_cross_review.DEFAULT_ARCHITECT_EFFORT is None
        assert agy_cross_review.DEFAULT_QA_MODEL == "gemini-3.8-flash-medium"
        assert agy_cross_review.DEFAULT_QA_EFFORT is None
        assert agy_cross_review.DEFAULT_CHIEF_FA_MODEL == "claude-opus-5-5"
        # Interactive-parity effort; max only on the final executive iteration
        assert agy_cross_review.DEFAULT_CHIEF_FA_EFFORT == "high"
        assert agy_cross_review.DEFAULT_CHIEF_FA_FINAL_EFFORT == "max"
        assert agy_cross_review.CLAUDE_REVIEW_TOOLS == "Read,Grep,Glob"
        assert agy_cross_review.CLAUDE_EXECUTIVE_TOOLS == "Read,Grep,Glob,Edit"

    def test_count_iterations_with_chief_fa(self):
        plan = """
# 📋 Implementation Plan: Feature Two-Tier
## 🔍 Review Iteration 1 (Author Perspective)
## 🏛️ Architect Review Iteration 1
## 🧪 QA Review Iteration 1 (Requirements & UX/UI Guardian)
## 🏛️ Chief Functional Architect Review Iteration 1: Problem-Solution & Standards Audit
## 🔍 Review Iteration 2 (Author Response to Chief Functional Architect)
## 🏛️ Architect Review Iteration 2
## 🧪 QA Review Iteration 2 (Requirements & UX/UI Guardian)
## 🏛️ Chief Functional Architect Review Iteration 2: Problem-Solution & Standards Audit
"""
        author, architect, qa, chief_fa = agy_cross_review.count_iterations(plan)
        assert author == 2
        assert architect == 2
        assert qa == 2
        assert chief_fa == 2

    def test_count_council_rounds_in_cycle(self):
        # Cycle 1: 2 council rounds
        plan_c1 = """
# 📋 Implementation Plan: Feature Cycle
## 🏛️ Architect Review Iteration 1
## 🧪 QA Review Iteration 1
## 🏛️ Architect Review Iteration 2
## 🧪 QA Review Iteration 2
"""
        assert agy_cross_review.count_council_rounds_in_cycle(plan_c1) == 2

        # Cycle 2: Chief FA ran once, then 1 new council round
        plan_c2 = plan_c1 + """
## 🏛️ Chief Functional Architect Review Iteration 1: Problem-Solution & Standards Audit
VERDICT: CHANGES REQUIRED
## 🔍 Review Iteration 2 (Author Response)
## 🏛️ Architect Review Iteration 3
## 🧪 QA Review Iteration 3
"""
        assert agy_cross_review.count_council_rounds_in_cycle(plan_c2) == 1

    def test_build_chief_fa_prompt(self, tmp_path):
        dummy_plan = tmp_path / "implementation-plan.md"
        dummy_plan.write_text("# Plan", encoding="utf-8")

        prompt = agy_cross_review.build_chief_fa_prompt(
            round_num=1,
            plan_path=dummy_plan,
            guide=["- Active plan: lines 1-1"],
            is_final_round=False,
        )

        assert "Chief Functional Architect" in prompt
        assert "Iteration 1" in prompt
        assert "MOST CRITICAL WAY POSSIBLE" in prompt
        assert "PROBLEM-SOLUTION FIT & ROOT CAUSE" in prompt
        assert "INDUSTRY STANDARDS & BEST PRACTICES" in prompt
        assert "## 🏛️ Chief Functional Architect Review Iteration 1: Problem-Solution & Standards Audit" in prompt
        assert "VERDICT: APPROVED" in prompt
        assert "VERDICT: CHANGES REQUIRED" in prompt
        assert "CRITICAL EXECUTIVE AUTHORITY" not in prompt

    def test_build_chief_fa_prompt_final_round(self, tmp_path):
        dummy_plan = tmp_path / "implementation-plan.md"
        dummy_plan.write_text("# Plan", encoding="utf-8")

        prompt = agy_cross_review.build_chief_fa_prompt(
            round_num=3,
            plan_path=dummy_plan,
            guide=["- Active plan: lines 1-1"],
            is_final_round=True,
        )

        assert "Iteration 3 (Hard Cap)" in prompt
        assert "CRITICAL EXECUTIVE AUTHORITY (FINAL ITERATION)" in prompt
        assert "### 🎯 Definitive Executive Resolution" in prompt
        assert "[Executive Resolution: Dictated by Chief Functional Architect]" in prompt
        assert "VERDICT: EXECUTIVE RESOLUTION DICTATED" in prompt

    def test_parse_chief_fa_verdict(self):
        approved_plan = """
## 🏛️ Chief Functional Architect Review Iteration 1: Problem-Solution & Standards Audit
- [NON-BLOCKING] Minor telemetry naming.
VERDICT: APPROVED
"""
        assert agy_cross_review.parse_chief_fa_verdict(approved_plan, "", 1) == "APPROVED"

        changes_plan = """
## 🏛️ Chief Functional Architect Review Iteration 1: Problem-Solution & Standards Audit
- [BLOCKING] Does not solve root cause of subprocess pipe hangs on Windows.
VERDICT: CHANGES REQUIRED
"""
        assert agy_cross_review.parse_chief_fa_verdict(changes_plan, "", 1) == "CHANGES REQUIRED"

        exec_plan = """
## 🏛️ Chief Functional Architect Review Iteration 3: Problem-Solution & Standards Audit
### 🎯 Definitive Executive Resolution
VERDICT: EXECUTIVE RESOLUTION DICTATED
"""
        assert agy_cross_review.parse_chief_fa_verdict(exec_plan, "", 3) == "EXECUTIVE_RESOLUTION"

    def test_extract_chief_fa_points(self):
        plan = """
## 🏛️ Chief Functional Architect Review Iteration 1: Problem-Solution & Standards Audit
### 🚨 Deficiencies & Functional Amendments
- [BLOCKING] Fails to adhere to POSIX file locking standards on shared storage.
- [NON-BLOCKING] Suggest adding Prometheus metrics.
VERDICT: CHANGES REQUIRED
"""
        points = agy_cross_review.extract_chief_fa_points(plan, 1)
        assert points == ["[BLOCKING] Fails to adhere to POSIX file locking standards on shared storage."]

    def test_reading_guide_chief_fa(self):
        plan = "\n".join([
            "# 📋 Implementation Plan: Feature Two-Tier",   # 1
            "## 📝 Initial Draft Proposal",                  # 2
            "operator initial request",                      # 3
            "## 🔍 Review Iteration 1 (Author)",             # 4
            "author notes",                                  # 5
            "## 🏛️ Architect Review Iteration 1",            # 6
            "architect notes",                               # 7
            "## 🧪 QA Review Iteration 1 (Requirements)",    # 8
            "qa notes",                                      # 9
            "## 🎯 Final Decision Plan",                     # 10
            "final spec",                                    # 11
        ])
        guide = "\n".join(agy_cross_review.reading_guide(plan, "chief_fa", round_num=1))
        assert "original proposal / requirements: lines 2-3" in guide
        assert "Current Final Decision Plan: lines 10-11" in guide
        assert "Latest author iteration: lines 4-5" in guide
        assert "Latest Architect review: lines 6-7" in guide
        assert "Latest QA review: lines 8-9" in guide

    _EXCERPT_PLAN = "\n".join([
        "# 📋 Implementation Plan: Feature Two-Tier",   # 1
        "## 📝 Initial Draft Proposal",                  # 2
        "operator initial request",                      # 3
        "## 🔍 Review Iteration 1 (Author)",             # 4
        "stale author notes",                            # 5
        "## 🏛️ Architect Review Iteration 1",            # 6
        "architect notes",                               # 7
        "## 🔍 Review Iteration 2 (Author)",             # 8
        "latest author notes",                           # 9
        "```gherkin",                                    # 10
        "## not a heading inside a fence",               # 11
        "```",                                           # 12
        "## 🧪 QA Review Iteration 1 (Requirements)",    # 13
        "qa notes",                                      # 14
        "## 🎯 Final Decision Plan",                     # 15
        "final spec",                                    # 16
    ])

    def test_plan_excerpts_inline_the_reading_guide_sections_verbatim(self):
        excerpts = agy_cross_review.plan_excerpts(self._EXCERPT_PLAN, "chief_fa", round_num=1)

        assert '<plan_excerpt label="Operator\'s original proposal / requirements" lines="2-3">' in excerpts
        assert "operator initial request" in excerpts
        assert '<plan_excerpt label="Latest author iteration" lines="8-12">' in excerpts
        assert "latest author notes" in excerpts
        assert "## not a heading inside a fence" in excerpts  # fenced content stays inside its section
        assert "architect notes" in excerpts
        assert "qa notes" in excerpts
        assert "final spec" in excerpts
        # Superseded iterations are not sent
        assert "stale author notes" not in excerpts

    def test_plan_excerpts_falls_back_to_active_plan(self):
        excerpts = agy_cross_review.plan_excerpts("# 📋 Implementation Plan: Raw\nfree text only\n", "chief_fa", round_num=1)
        assert '<plan_excerpt label="Active plan" lines="1-2">' in excerpts
        assert "free text only" in excerpts

    def test_reading_guide_format_unchanged_for_agy_reviewers(self):
        guide = agy_cross_review.reading_guide(self._EXCERPT_PLAN, "qa", round_num=1)
        assert "- Latest author iteration: lines 8-12 (`## 🔍 Review Iteration 2 (Author)`)" in guide
        assert agy_cross_review.reading_guide("no headings here", "qa", 1) == ["- Active plan: lines 1-1"]

    def test_build_chief_fa_prompt_inline_mode(self, tmp_path):
        plan_path = tmp_path / "plan.md"
        guide = agy_cross_review.reading_guide(self._EXCERPT_PLAN, "chief_fa", 1)
        excerpts = agy_cross_review.plan_excerpts(self._EXCERPT_PLAN, "chief_fa", 1)

        prompt = agy_cross_review.build_chief_fa_prompt(1, plan_path, guide, is_final_round=False, excerpts=excerpts)

        assert prompt.startswith("PLAN EXCERPTS")
        assert prompt.index("final spec") < prompt.index("You are the Chief Functional Architect")
        assert "Do NOT open the plan file" in prompt
        assert "Read ONLY these line ranges" not in prompt
        assert "RETURN YOUR REVIEW as your final reply" in prompt
        assert "APPEND YOUR REVIEW" not in prompt
        assert "## 🏛️ Chief Functional Architect Review Iteration 1: Problem-Solution & Standards Audit" in prompt
        assert "Edit tool" not in prompt

        final = agy_cross_review.build_chief_fa_prompt(3, plan_path, guide, is_final_round=True, excerpts=excerpts)
        assert "Iteration 3 (Hard Cap)" in final
        assert "use the Edit tool; that section is the only part of the file you may change" in final

    def test_build_architect_prompt_keeps_file_mode_for_agy(self, tmp_path):
        guide = agy_cross_review.reading_guide(self._EXCERPT_PLAN, "architect", 1)
        prompt = agy_cross_review.build_architect_prompt(1, tmp_path / "plan.md", guide)
        assert "Read ONLY these line ranges" in prompt
        assert "APPEND YOUR REVIEW at the very end of" in prompt
        assert "<plan_excerpt" not in prompt

    @patch("agy_cross_review.invoke_claude")
    def test_execute_chief_fa_regular_round_is_read_only_high_effort(self, mock_claude, tmp_path):
        plan_path = tmp_path / "plan.md"
        plan_path.write_text(self._EXCERPT_PLAN + "\n", encoding="utf-8")
        reply = "## 🏛️ Chief Functional Architect Review Iteration 1: Problem-Solution & Standards Audit\nVERDICT: APPROVED"
        mock_claude.return_value = (0, reply, "", {"num_turns": 2})

        code, _, content, verdict, unresolved, metrics = agy_cross_review.execute_chief_fa(
            plan_path, 1, "claude-opus-5-5", "high", 3
        )

        kwargs = mock_claude.call_args[1]
        assert kwargs["effort"] == "high"
        assert kwargs["tools"] == "Read,Grep,Glob"
        assert kwargs["timeout_s"] == agy_cross_review.DEFAULT_CLAUDE_TIMEOUT_S
        assert "final spec" in mock_claude.call_args[0][0]  # excerpts inlined
        assert code == 0 and verdict == "APPROVED" and unresolved == []
        assert metrics == {"num_turns": 2}
        # The script appends the returned section to the plan
        assert content.rstrip().endswith("VERDICT: APPROVED")
        assert plan_path.read_text(encoding="utf-8").count("Chief Functional Architect Review Iteration 1") == 1

    @patch("agy_cross_review.invoke_claude")
    def test_execute_chief_fa_final_round_uses_final_effort_and_edit(self, mock_claude, tmp_path):
        plan_path = tmp_path / "plan.md"
        plan_path.write_text(self._EXCERPT_PLAN + "\n", encoding="utf-8")
        mock_claude.return_value = (
            0,
            "## 🏛️ Chief Functional Architect Review Iteration 3: Problem-Solution & Standards Audit\nVERDICT: EXECUTIVE RESOLUTION DICTATED",
            "",
            {},
        )

        *_, verdict, _, _ = agy_cross_review.execute_chief_fa(
            plan_path, 3, "claude-opus-5-5", "high", 3, final_effort="max", timeout_s=60
        )

        kwargs = mock_claude.call_args[1]
        assert kwargs["effort"] == "max"
        assert kwargs["tools"] == "Read,Grep,Glob,Edit"
        assert kwargs["timeout_s"] == 60
        assert verdict == "EXECUTIVE_RESOLUTION"

    @patch("agy_cross_review.invoke_claude")
    @patch("agy_cross_review.invoke_agy")
    def test_main_chief_fa_triggered_on_council_agreed(self, mock_agy, mock_claude, tmp_path, monkeypatch, capsys):
        custom = tmp_path / "specs" / "feature.md"
        custom.parent.mkdir()
        custom.write_text("# 📋 Implementation Plan: Test\n## 📝 Initial Draft Proposal\nReqs\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        # System Architect agrees (via agy), QA agrees (via agy), then Chief FA approves (via claude)
        def mock_agy_call(prompt, model, effort=None):
            if "System Architect" in prompt:
                assert model == "gemini-3.8-flash-high"
                return 0, "## 🏛️ Architect Review Iteration 1\nVERDICT: AGREED\n", ""
            elif "QA Lead" in prompt:
                assert model == "gemini-3.8-flash-medium"
                return 0, "## 🧪 QA Review Iteration 1 (Requirements & UX/UI Guardian)\nVERDICT: AGREED\n", ""
            return 0, "", ""

        def mock_claude_call(prompt, model, effort, **kwargs):
            if "Chief Functional Architect" in prompt:
                assert effort == "high"
                assert model == "claude-opus-5-5"
                assert kwargs["tools"] == "Read,Grep,Glob"
                assert "<plan_excerpt" in prompt
                return 0, "## 🏛️ Chief Functional Architect Review Iteration 1: Problem-Solution & Standards Audit\nVERDICT: APPROVED\n", "", {"num_turns": 2}
            return 0, "", "", {}

        mock_agy.side_effect = mock_agy_call
        mock_claude.side_effect = mock_claude_call

        code, out = self._run_main(monkeypatch, capsys, "--plan", str(custom))

        assert code == 0
        assert out["status"] == "completed"
        assert out["council_verdict"] == "AGREED"
        assert out["chief_fa_verdict"] == "APPROVED"
        assert out["overall_verdict"] == "APPROVED"
        assert out["chief_fa_round"] == 1
        assert out["chief_fa_metrics"] == {"num_turns": 2}
        assert "architect_metrics" not in out  # the Architect ran on agy

    @patch("agy_cross_review.invoke_claude")
    @patch("agy_cross_review.invoke_agy")
    def test_main_chief_fa_changes_required_returns_to_council(self, mock_agy, mock_claude, tmp_path, monkeypatch, capsys):
        custom = tmp_path / "specs" / "feature.md"
        custom.parent.mkdir()
        custom.write_text("# 📋 Implementation Plan: Test\n## 📝 Initial Draft Proposal\nReqs\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        def mock_agy_call(prompt, model, effort=None):
            if "System Architect" in prompt:
                assert model == "gemini-3.8-flash-high"
                return 0, "## 🏛️ Architect Review Iteration 1\nVERDICT: AGREED\n", ""
            elif "QA Lead" in prompt:
                assert model == "gemini-3.8-flash-medium"
                return 0, "## 🧪 QA Review Iteration 1 (Requirements & UX/UI Guardian)\nVERDICT: AGREED\n", ""
            return 0, "", ""

        def mock_claude_call(prompt, model, effort, **kwargs):
            if "Chief Functional Architect" in prompt:
                assert effort == "high"
                return 0, "## 🏛️ Chief Functional Architect Review Iteration 1: Problem-Solution & Standards Audit\n- [BLOCKING] Missing rollback semantics.\nVERDICT: CHANGES REQUIRED\n", "", {}
            return 0, "", "", {}

        mock_agy.side_effect = mock_agy_call
        mock_claude.side_effect = mock_claude_call

        code, out = self._run_main(monkeypatch, capsys, "--plan", str(custom))

        assert code == 0
        assert out["status"] == "chief_fa_changes_required"
        assert out["chief_fa_verdict"] == "CHANGES REQUIRED"
        assert "Returning to Council for a new 3-round cycle" in out["message"]

    @patch("agy_cross_review.invoke_claude")
    def test_main_run_chief_fa_flag(self, mock_claude, tmp_path, monkeypatch, capsys):
        custom = tmp_path / "specs" / "feature.md"
        custom.parent.mkdir()
        custom.write_text(
            "# 📋 Implementation Plan: Test\n"
            "## 🏛️ Architect Review Iteration 1\nVERDICT: AGREED\n"
            "## 🧪 QA Review Iteration 1 (Requirements & UX/UI Guardian)\nVERDICT: AGREED\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        mock_claude.return_value = (
            0,
            "## 🏛️ Chief Functional Architect Review Iteration 1: Problem-Solution & Standards Audit\nVERDICT: APPROVED\n",
            "",
            {"duration_ms": 1234},
        )

        code, out = self._run_main(
            monkeypatch, capsys, "--plan", str(custom), "--run-chief-fa",
            "--chief-fa-effort", "medium", "--claude-timeout", "90",
        )

        assert code == 0
        assert out["role"] == "chief_fa"
        assert out["chief_fa_verdict"] == "APPROVED"
        assert out["chief_fa_round"] == 1
        assert out["chief_fa_metrics"] == {"duration_ms": 1234}
        kwargs = mock_claude.call_args[1]
        assert kwargs["effort"] == "medium"
        assert kwargs["timeout_s"] == 90

    @patch("agy_cross_review.invoke_claude")
    def test_main_run_chief_fa_timeout_fails_run(self, mock_claude, tmp_path, monkeypatch, capsys):
        custom = tmp_path / "specs" / "feature.md"
        custom.parent.mkdir()
        custom.write_text("# 📋 Implementation Plan: Test\n## 📝 Initial Draft Proposal\nReqs\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        mock_claude.return_value = (124, "", "claude timed out after 90s", {"timed_out": True, "timeout_s": 90})

        code, out = self._run_main(monkeypatch, capsys, "--plan", str(custom), "--run-chief-fa")

        assert code == 1
        assert out["chief_fa_returncode"] == 124
        assert out["chief_fa_metrics"]["timed_out"] is True

    def test_main_check_status_includes_chief_fa(self, tmp_path, monkeypatch, capsys):
        custom = tmp_path / "specs" / "feature.md"
        custom.parent.mkdir()
        custom.write_text(
            "# 📋 Implementation Plan: Status\n"
            "## 🏛️ Architect Review Iteration 1\n"
            "## 🧪 QA Review Iteration 1\n"
            "## 🏛️ Chief Functional Architect Review Iteration 1\n"
            "## 🏛️ Architect Review Iteration 2\n"
            "## 🧪 QA Review Iteration 2\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        code, out = self._run_main(monkeypatch, capsys, "--plan", str(custom), "--check-status")

        assert code == 0
        assert out["architect_rounds"] == 2
        assert out["qa_rounds"] == 2
        assert out["chief_fa_rounds"] == 1
        assert out["council_rounds_in_cycle"] == 1
        assert out["max_chief_fa_rounds"] == 3

    @patch("agy_cross_review.invoke_claude")
    def test_main_chief_fa_final_resolution_on_round_3(self, mock_claude, tmp_path, monkeypatch, capsys):
        custom = tmp_path / "specs" / "feature.md"
        custom.parent.mkdir()
        custom.write_text(
            "# 📋 Implementation Plan: Final Resolution\n"
            "## 🏛️ Chief Functional Architect Review Iteration 1\n"
            "## 🏛️ Chief Functional Architect Review Iteration 2\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        def mock_claude_call(prompt, model, effort, **kwargs):
            assert effort == "max"
            assert kwargs["tools"] == "Read,Grep,Glob,Edit"
            assert "Iteration 3 (Hard Cap)" in prompt
            return 0, "## 🏛️ Chief Functional Architect Review Iteration 3: Problem-Solution & Standards Audit\n### 🎯 Definitive Executive Resolution\nFinal binding design.\nVERDICT: EXECUTIVE RESOLUTION DICTATED\n", "", {}

        mock_claude.side_effect = mock_claude_call

        code, out = self._run_main(monkeypatch, capsys, "--plan", str(custom), "--run-chief-fa")

        assert code == 0
        assert out["status"] == "executive_resolution_dictated"
        assert out["chief_fa_verdict"] == "EXECUTIVE_RESOLUTION"
        assert out["chief_fa_round"] == 3

    @patch("agy_cross_review.invoke_claude")
    @patch("agy_cross_review.invoke_agy")
    def test_main_architect_dispatch_claude_override(self, mock_agy, mock_claude, tmp_path, monkeypatch, capsys):
        custom = tmp_path / "specs" / "feature.md"
        custom.parent.mkdir()
        custom.write_text("# 📋 Implementation Plan: Override\n## 📝 Initial Draft Proposal\nReqs\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        mock_claude.return_value = (0, "## 🏛️ Architect Review Iteration 1\nVERDICT: AGREED\n", "", {"num_turns": 1})
        mock_agy.return_value = (0, "## 🧪 QA Review Iteration 1 (Requirements & UX/UI Guardian)\nVERDICT: AGREED\n", "")

        code, out = self._run_main(monkeypatch, capsys, "--plan", str(custom), "--architect-model", "claude-opus-5-5", "--skip-chief-fa")

        assert code == 0
        assert mock_claude.called
        call_args = mock_claude.call_args
        assert call_args[1]["model"] == "claude-opus-5-5"
        assert call_args[1]["effort"] == "medium"
        # A claude Architect gets the inline mode; the agy QA keeps line ranges
        assert "<plan_excerpt" in call_args[0][0]
        assert "RETURN YOUR REVIEW" in call_args[0][0]
        assert "<plan_excerpt" not in mock_agy.call_args[0][0]
        assert out["architect_metrics"] == {"num_turns": 1}
        assert custom.read_text(encoding="utf-8").count("## 🏛️ Architect Review Iteration 1") == 1



