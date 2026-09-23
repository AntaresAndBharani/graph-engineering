"""
Unit test suite for Antigravity Tri-Party Architect & QA Cross-Review Script.
Tests:
  - Iteration counting (Author, Architect incl. legacy Gemini headings, Claude QA)
  - Plan archiving and scoped reading guides
  - Prompt construction with QA mandates and BLOCKING/NON-BLOCKING verdict rules
  - Dual verdict parsing (Architect & Claude QA)
  - Disagreement extraction
  - Subprocess invocation flags for claude (fresh, non-persisted sessions)
"""

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Add skill script directory to sys.path
SKILL_SCRIPT_DIR = Path(__file__).resolve().parent.parent / ".agents" / "skills" / "agy-architect-review" / "scripts"
if str(SKILL_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPT_DIR))

import agy_cross_review


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
        author, architect, qa = agy_cross_review.count_iterations(sample_plan)
        assert author == 2
        assert architect == 2
        assert qa == 2

    def test_count_iterations_partial_rounds(self):
        sample_plan = """
# 📋 Implementation Plan: Feature Y

## 🔍 Review Iteration 1 (Author Perspective)
Initial.

## 🏛️ Architect Review Iteration 1
Architect reviewed first.
"""
        author, architect, qa = agy_cross_review.count_iterations(sample_plan)
        assert author == 1
        assert architect == 1
        assert qa == 0

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
        assert agy_cross_review.count_iterations(plan) == (1, 1, 1)

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
        assert "## 🧪 Claude QA Review Iteration 2 (Requirements & UX/UI Guardian)" in prompt
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

        assert "Principal Architect" in prompt
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
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        agy_cross_review.invoke_claude(prompt="Architect Prompt", model="claude-opus-5-5", effort="medium")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert cmd[cmd.index("-p") + 1] == "Architect Prompt"
        assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
        assert cmd[cmd.index("--effort") + 1] == "medium"
        assert "--no-session-persistence" in cmd
        assert "--dangerously-skip-permissions" in cmd
        # Every round is a fresh session: no resume and no fixed session id
        assert "-r" not in cmd
        assert "-c" not in cmd
        assert "--session-id" not in cmd

    def test_default_models(self):
        assert agy_cross_review.DEFAULT_ARCHITECT_MODEL == "claude-opus-5-5"
        assert agy_cross_review.DEFAULT_ARCHITECT_EFFORT == "medium"
        assert agy_cross_review.DEFAULT_QA_MODEL == "sonnet"
        assert agy_cross_review.DEFAULT_QA_EFFORT == "low"
