"""
Unit test suite for Antigravity Tri-Party Architect & QA Cross-Review Script.
Tests:
  - Iteration counting (Author, Gemini Architect, Claude QA)
  - Prompt construction with QA mandates (Anti-drift, UX/UI, functional correctness, BDD)
  - Dual verdict parsing (Gemini & Claude QA)
  - Disagreement extraction
  - Subprocess invocation flags for agy and claude
"""

import os
import sys
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

## 🏛️ Gemini Architect Review Iteration 2
Second architect critique.

## 🧪 Claude QA Review Iteration 2 (Requirements & UX/UI Guardian)
Second QA critique.
"""
        author, gemini, qa = agy_cross_review.count_iterations(sample_plan)
        assert author == 2
        assert gemini == 2
        assert qa == 2

    def test_count_iterations_partial_rounds(self):
        sample_plan = """
# 📋 Implementation Plan: Feature Y

## 🔍 Review Iteration 1 (Author Perspective)
Initial.

## 🏛️ Gemini Architect Review Iteration 1
Gemini reviewed first.
"""
        author, gemini, qa = agy_cross_review.count_iterations(sample_plan)
        assert author == 1
        assert gemini == 1
        assert qa == 0

    def test_build_qa_prompt_mandates(self, tmp_path):
        dummy_plan = tmp_path / "implementation-plan.md"
        dummy_plan.write_text("# Plan", encoding="utf-8")

        prompt = agy_cross_review.build_qa_prompt(round_num=2, plan_path=dummy_plan)

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

    def test_build_gemini_prompt_structure(self, tmp_path):
        dummy_plan = tmp_path / "implementation-plan.md"
        dummy_plan.write_text("# Plan", encoding="utf-8")

        prompt = agy_cross_review.build_gemini_prompt(round_num=1, plan_path=dummy_plan)

        assert "Principal Architect" in prompt
        assert "Round 1" in prompt
        assert "## 🏛️ Gemini Architect Review Iteration 1" in prompt
        assert "VERDICT: AGREED" in prompt

    def test_parse_gemini_verdict(self):
        agreed_plan = """
## 🏛️ Gemini Architect Review Iteration 1
Everything is sound.
VERDICT: AGREED
"""
        assert agy_cross_review.parse_gemini_verdict(agreed_plan, "", 1) == "AGREED"

        disagreed_plan = """
## 🏛️ Gemini Architect Review Iteration 1
Critical race condition found.
VERDICT: DISAGREED
"""
        assert agy_cross_review.parse_gemini_verdict(disagreed_plan, "", 1) == "DISAGREED"

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
## 🏛️ Gemini Architect Review Iteration 1
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
        gemini_points = agy_cross_review.extract_disagreement_points(plan, 1, header_prefix="🏛️")
        assert len(gemini_points) == 2
        assert "Subprocess timeout" in gemini_points[0]

        qa_points = agy_cross_review.extract_disagreement_points(plan, 1, header_prefix="🧪")
        assert len(qa_points) == 2
        assert "Operator constraint" in qa_points[0]

    @patch("subprocess.run")
    def test_invoke_claude_arguments(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        # Test initial round
        agy_cross_review.invoke_claude(
            prompt="QA Prompt",
            model="sonnet",
            effort="low",
            session_id="qa-1234",
            is_resume=False,
        )
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "--session-id" in cmd
        assert "qa-1234" in cmd
        assert "--model" in cmd
        assert "sonnet" in cmd
        assert "--effort" in cmd
        assert "low" in cmd
        assert "--dangerously-skip-permissions" in cmd

        # Test resume round
        agy_cross_review.invoke_claude(
            prompt="QA Prompt Round 2",
            session_id="qa-1234",
            is_resume=True,
        )
        cmd_resume = mock_run.call_args[0][0]
        assert cmd_resume[0] == "claude"
        assert "-r" in cmd_resume
        assert "qa-1234" in cmd_resume
        assert "--dangerously-skip-permissions" in cmd_resume

    @patch("subprocess.run")
    def test_invoke_agy_arguments(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        # Round 1 (no -c)
        agy_cross_review.invoke_agy("Gemini Prompt", model="gemini-3.8-flash-high", is_resume=False)
        cmd1 = mock_run.call_args[0][0]
        assert cmd1[0] == "agy"
        assert "-c" not in cmd1
        assert "--model" in cmd1
        assert "gemini-3.8-flash-high" in cmd1

        # Round 2 (with -c)
        agy_cross_review.invoke_agy("Gemini Prompt 2", model="gemini-3.8-flash-high", is_resume=True)
        cmd2 = mock_run.call_args[0][0]
        assert cmd2[0] == "agy"
        assert "-c" in cmd2
