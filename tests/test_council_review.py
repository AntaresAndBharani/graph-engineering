"""
Unit tests for the Architect Council Review helper (skills/architect-council-review/scripts/council_review.py)
and the `orchestrator council` pass-through command.

Covers: section parsing (fence-aware), council state derivation, prompt scoping (line ranges vs inline),
verdict parsing (fail-closed), harness command flags, the Round 1 / Round 2 / deadlock flow,
operator --continue / --proceed gates, plan tamper protection, and CLI argument forwarding.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

_repo_root = Path(__file__).resolve().parent.parent
SKILL_SCRIPT_DIR = _repo_root / "skills" / "architect-council-review" / "scripts"
if str(SKILL_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPT_DIR))

import council_review as cr  # noqa: E402

PLAN_TEXT = """# 📋 Implementation Plan: Widget export

## 📝 Initial Draft Proposal
Add a CSV export button.

```markdown
# not a heading inside a fence
## also not a heading
```

## Acceptance Criteria
Given a table When I click export Then a CSV downloads.
"""


def _reply(verdict="AGREED", blocking=None, agreed=("CSV export is in scope",), compromise="None", position="Looks good."):
    objections = [f"- [BLOCKING] {b}" for b in (blocking or [])] or ["- None"]
    return "\n".join([
        "### Agreed Points",
        *[f"- {a}" for a in agreed],
        "### Objections",
        *objections,
        "### Position",
        position,
        "### Proposed Compromise",
        compromise,
        "",
        f"VERDICT: {verdict}",
    ])


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path_factory, monkeypatch):
    """Ignore the operator's real global config and env override: tests use the shipped default unless they opt in."""
    monkeypatch.setattr(cr, "GLOBAL_CONFIG_PATH", tmp_path_factory.mktemp("noglobal") / "absent.json")
    monkeypatch.delenv(cr.CONFIG_ENV_VAR, raising=False)


@pytest.fixture
def plan(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text(PLAN_TEXT, encoding="utf-8")
    return p


def _outcome(reply, code=0, metrics=None):
    return cr.RoleOutcome(code, reply, "", metrics or {})


class FakeCouncil:
    """Replaces run_role with scripted replies per round: script[round][abbr] -> reply."""

    def __init__(self, script):
        self.script = script
        self.calls = []
        self.round = 0

    def __call__(self, abbr, prompt, config, args):
        self.calls.append((abbr, prompt, config))
        rnd = int(prompt.split("This is Round ")[1].split(" ")[0])
        return _outcome(self.script[rnd][abbr])


# ------------------------------------------------------------------ parsing & state

def test_parse_sections_ignores_fenced_headings():
    lines = PLAN_TEXT.split("\n")
    headings = [s.heading for s in cr.parse_sections(lines)]
    assert headings == [
        "# 📋 Implementation Plan: Widget export",
        "## 📝 Initial Draft Proposal",
        "## Acceptance Criteria",
    ]


def test_compute_state_idle_and_round_tracking(plan):
    _, _, state = cr.load_state(plan, 2)
    assert state.status == cr.STATUS_IDLE
    assert state.next_round() == 1

    cfg = cr.RoleConfig("claude", "m", "e")
    cr.append_blocks(plan, [cr.render_round_block(a, 1, cfg, _reply("DISAGREED", ["x"])) for a in cr.ROLE_ORDER])
    _, _, state = cr.load_state(plan, 2)
    assert state.status == cr.STATUS_IN_PROGRESS
    assert state.rounds_completed == 1
    assert state.next_round() == 2


def test_compute_state_consensus_then_new_session(plan):
    cfg = cr.RoleConfig("claude", "m", "e")
    cr.append_blocks(plan, [cr.render_round_block(a, 1, cfg, _reply()) for a in cr.ROLE_ORDER])
    reviews = {a: cr.parse_review(_reply()) for a in cr.ROLE_ORDER}
    cr.append_blocks(plan, [cr.render_consensus(1, reviews)])
    _, _, state = cr.load_state(plan, 2)
    assert state.status == cr.STATUS_CONSENSUS
    assert state.rounds_completed == 1
    assert state.next_round() == 1


def test_compute_state_deadlock_and_continuation(plan):
    cfg = cr.RoleConfig("claude", "m", "e")
    for rnd in (1, 2):
        cr.append_blocks(plan, [cr.render_round_block(a, rnd, cfg, _reply("DISAGREED", ["x"])) for a in cr.ROLE_ORDER])
    reviews = {a: cr.parse_review(_reply("DISAGREED", ["x"])) for a in cr.ROLE_ORDER}
    cr.append_blocks(plan, [cr.render_dossier(2, reviews, "")])
    _, _, state = cr.load_state(plan, 2)
    assert state.status == cr.STATUS_DEADLOCKED

    cr.append_blocks(plan, ["## ⏩ Council Continuation: +2 rounds\n_x_"])
    _, _, state = cr.load_state(plan, 2)
    assert state.status == cr.STATUS_IN_PROGRESS
    assert state.max_rounds == 4
    assert state.next_round() == 3


def test_append_preserves_crlf(tmp_path):
    p = tmp_path / "crlf.md"
    p.write_bytes(b"# Title\r\n\r\nBody\r\n")
    cr.append_blocks(p, ["## Added\nline"])
    raw = p.read_bytes()
    assert b"\r\n## Added\r\nline\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


# ------------------------------------------------------------------ review parsing

def test_parse_review_agreed():
    r = cr.parse_review(_reply(agreed=("A", "B")))
    assert r.verdict == "AGREED"
    assert r.agreed == ["A", "B"]
    assert r.blocking == []
    assert r.compromise == ""


def test_parse_review_missing_verdict_fails_closed():
    r = cr.parse_review("### Agreed Points\n- ok\n")
    assert r.verdict == "DISAGREED"
    assert "verdict_missing" in r.flags


def test_parse_review_agreed_with_blocking_is_downgraded():
    r = cr.parse_review(_reply("AGREED", ["Breaks the public API"]))
    assert r.verdict == "DISAGREED"
    assert r.blocking == ["Breaks the public API"]
    assert "agreed_with_blocking_objections" in r.flags


def test_parse_review_last_verdict_wins_and_non_blocking():
    reply = "### Objections\n- [NON-BLOCKING] rename var\n- **[BLOCKING]** none\nVERDICT: DISAGREED\n...\nVERDICT: AGREED"
    r = cr.parse_review(reply)
    assert r.verdict == "AGREED"
    assert r.non_blocking == ["rename var"]
    assert r.blocking == []


def test_sanitize_reply_demotes_headings_outside_fences():
    out = cr.sanitize_reply("## Big\n```\n## keep\n```\n# Huge")
    assert out.split("\n") == ["### Big", "```", "## keep", "```", "### Huge"]


# ------------------------------------------------------------------ prompts

def test_prompt_round1_agy_uses_line_ranges(plan):
    lines, sections, state = cr.load_state(plan, 2)
    prompt = cr.build_prompt(cr.ROLES["TL"], 1, plan, lines, sections, state, inline=False)
    assert "Technical Lead (TL)" in prompt
    assert "Round 1 (Independent Review)" in prompt
    assert "Lines 1-" in prompt
    assert "Do NOT create, edit, or delete any file" in prompt
    assert "### Rebuttals" not in prompt
    assert "Add a CSV export button." not in prompt


def test_prompt_round2_inlines_peers_for_claude(plan):
    cfg = cr.RoleConfig("claude", "m", "e")
    cr.append_blocks(plan, [
        cr.render_round_block(a, 1, cfg, _reply("DISAGREED", [f"{a}-objection"])) for a in cr.ROLE_ORDER
    ])
    lines, sections, state = cr.load_state(plan, 2)
    prompt = cr.build_prompt(cr.ROLES["FA"], 2, plan, lines, sections, state, inline=True)
    assert "Round 2 (Cross-Rebuttal)" in prompt
    assert "ACCEPT, REBUT" in prompt
    assert "Add a CSV export button." in prompt
    assert "TL-objection" in prompt and "QA-objection" in prompt
    assert "===== YOUR OWN REVIEW (ROUND 1) =====" in prompt
    # Council round sections are not part of the subject block.
    subject_block = prompt.split("===== SUBJECT UNDER REVIEW =====")[1].split("=====")[0]
    assert "Council Round" not in subject_block


# ------------------------------------------------------------------ harness flags

def test_invoke_claude_flags_and_metrics():
    stdout = json.dumps({"result": "review", "duration_ms": 5, "num_turns": 2, "usage": {"input_tokens": 1}})
    with patch.object(cr.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr="")) as run:
        out = cr.invoke_claude("PROMPT", "claude-opus-5-5", "high", timeout_s=9)
    cmd = run.call_args.args[0]
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    assert cmd[cmd.index("--effort") + 1] == "high"
    assert cmd[cmd.index("--tools") + 1] == "Read,Grep,Glob"
    for flag in ("--strict-mcp-config", "--no-session-persistence", "--dangerously-skip-permissions"):
        assert flag in cmd
    assert "-c" not in cmd and "PROMPT" not in cmd
    assert run.call_args.kwargs["input"] == "PROMPT"
    assert run.call_args.kwargs["timeout"] == 9
    assert out.reply == "review" and out.metrics["num_turns"] == 2


def test_invoke_agy_flags_tl_effort_in_model_id():
    with patch.object(cr.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="ok", stderr="")) as run:
        out = cr.invoke_agy("P", "claude-opus-5-5-medium", timeout_s=1200)
    cmd = run.call_args.args[0]
    assert cmd[:3] == ["agy", "-p", "P"]
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5-medium"
    assert "--effort" not in cmd
    assert cmd[cmd.index("--print-timeout") + 1] == "20m"
    assert "-c" not in cmd
    assert out.reply == "ok"


def test_invoke_agy_missing_cli():
    with patch.object(cr.subprocess, "run", side_effect=FileNotFoundError):
        out = cr.invoke_agy("P", "gemini-3.8-flash-high")
    assert out.returncode == 127


def test_shipped_default_config_matches_spec():
    args = cr.build_parser().parse_args([])
    cfg, path, source = cr.resolve_role_configs(args)
    assert source == "default" and path == cr.DEFAULT_CONFIG_PATH
    assert cfg["FA"] == cr.RoleConfig("claude", "claude-opus-5-5", "high")
    assert cfg["TL"] == cr.RoleConfig("agy", "claude-opus-5-5-medium", None)
    assert cfg["QA"] == cr.RoleConfig("agy", "gemini-3.8-flash-high", None)
    assert args.max_rounds == 2


def test_run_role_dispatches_by_configured_harness():
    args = cr.build_parser().parse_args([])
    with patch.object(cr, "invoke_claude", return_value=_outcome("c")) as ic, patch.object(cr, "invoke_agy", return_value=_outcome("a")) as ia:
        cr.run_role("FA", "p", cr.RoleConfig("claude", "claude-opus-5-5", "high"), args)
        cr.run_role("QA", "p", cr.RoleConfig("agy", "gemini-3.8-flash-high"), args)
        cr.run_role("TL", "p", cr.RoleConfig("claude", "claude-sonnet-5-5", "low"), args)
    assert ic.call_args_list[0].args == ("p", "claude-opus-5-5", "high", args.claude_timeout)
    assert ic.call_args_list[1].args == ("p", "claude-sonnet-5-5", "low", args.claude_timeout)
    ia.assert_called_once_with("p", "gemini-3.8-flash-high", args.agy_timeout)


# ------------------------------------------------------------------ config file

def _write_config(path, roles):
    path.write_text(json.dumps({"roles": roles}), encoding="utf-8")
    return path


GOOD_ROLES = {
    "FA": {"harness": "claude", "model": "claude-opus-5-5", "effort": "medium"},
    "TL": {"harness": "agy", "model": "claude-sonnet-5-5-high"},
    "QA": {"harness": "agy", "model": "gemini-3.7-flash-low"},
}


def test_config_precedence_argument_env_global(tmp_path, monkeypatch):
    glob = _write_config(tmp_path / "global.json", GOOD_ROLES)
    env = _write_config(tmp_path / "env.json", {**GOOD_ROLES, "QA": {"harness": "agy", "model": "gemini-3.1-pro-high"}})
    arg = _write_config(tmp_path / "arg.json", {**GOOD_ROLES, "QA": {"harness": "agy", "model": "gpt-oss-120b-medium"}})
    monkeypatch.setattr(cr, "GLOBAL_CONFIG_PATH", glob)
    assert cr.resolve_config_path() == (glob, "global")
    monkeypatch.setenv(cr.CONFIG_ENV_VAR, str(env))
    assert cr.resolve_config_path() == (env.resolve(), "env")
    assert cr.resolve_config_path(str(arg)) == (arg.resolve(), "argument")

    args = cr.build_parser().parse_args(["--config", str(arg)])
    cfg, _, source = cr.resolve_role_configs(args)
    assert source == "argument" and cfg["QA"].model == "gpt-oss-120b-medium"


def test_global_config_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "GLOBAL_CONFIG_PATH", _write_config(tmp_path / "g.json", GOOD_ROLES))
    cfg, _, source = cr.resolve_role_configs(cr.build_parser().parse_args([]))
    assert source == "global"
    assert cfg["FA"].effort == "medium"
    assert cfg["TL"].model == "claude-sonnet-5-5-high"


def test_missing_explicit_config_is_error(tmp_path):
    with pytest.raises(cr.ConfigError, match="not found"):
        cr.resolve_config_path(str(tmp_path / "missing.json"))


@pytest.mark.parametrize(
    "role_patch, message",
    [
        ({"TL": {"harness": "agy", "model": "claude-opus-5-5", "effort": "medium"}}, "claude-opus-5-5-medium"),
        ({"TL": {"harness": "agy", "model": "claude-opus-5-5"}}, "no effort suffix"),
        ({"FA": {"harness": "claude", "model": "claude-opus-5-5"}}, "effort is required"),
        ({"QA": {"harness": "codex", "model": "x-high"}}, "harness must be one of"),
        ({"QA": {"harness": "agy", "model": "gemini-3.8-flash-high", "temperature": 1}}, "unknown keys"),
    ],
)
def test_config_validation_errors(tmp_path, role_patch, message):
    path = _write_config(tmp_path / "c.json", {**GOOD_ROLES, **role_patch})
    with pytest.raises(cr.ConfigError, match=message):
        cr.resolve_role_configs(cr.build_parser().parse_args(["--config", str(path)]))


def test_config_missing_role_and_bad_json(tmp_path):
    path = _write_config(tmp_path / "c.json", {"FA": GOOD_ROLES["FA"], "TL": GOOD_ROLES["TL"]})
    with pytest.raises(cr.ConfigError, match="missing roles"):
        cr.load_council_config(path)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(cr.ConfigError, match="not valid JSON"):
        cr.load_council_config(bad)


def test_cli_overrides_apply_and_are_validated(tmp_path):
    path = _write_config(tmp_path / "c.json", GOOD_ROLES)
    args = cr.build_parser().parse_args(["--config", str(path), "--tl-model", "claude-opus-5-5-low", "--fa-effort", "max"])
    cfg, _, _ = cr.resolve_role_configs(args)
    assert cfg["TL"].model == "claude-opus-5-5-low"
    assert cfg["FA"].effort == "max"
    args = cr.build_parser().parse_args(["--config", str(path), "--qa-effort", "high"])
    with pytest.raises(cr.ConfigError, match="not allowed for the agy harness"):
        cr.resolve_role_configs(args)


def test_run_reports_config_error_without_invoking(plan, tmp_path):
    path = _write_config(tmp_path / "c.json", {**GOOD_ROLES, "TL": {"harness": "agy", "model": "claude-opus-5-5"}})
    with patch.object(cr, "run_role", side_effect=AssertionError("must not run")):
        code, result = cr.run(["--plan", str(plan), "--config", str(path)])
    assert code == cr.EXIT_ERROR and "no effort suffix" in result["error"]


def test_check_status_reports_roles_and_config(plan, tmp_path):
    path = _write_config(tmp_path / "c.json", GOOD_ROLES)
    code, result = cr.run(["--plan", str(plan), "--config", str(path), "--check-status"])
    assert code == cr.EXIT_OK
    assert result["config_source"] == "argument"
    assert result["roles"]["TL"] == {"harness": "agy", "model": "claude-sonnet-5-5-high", "effort": None}


def test_round_header_shows_configured_harness_and_effort():
    block = cr.render_round_block("TL", 1, cr.RoleConfig("agy", "claude-opus-5-5-medium"), _reply())
    assert "_Harness: `agy` · Model: `claude-opus-5-5-medium` · Effort: `medium (in model ID)`" in block
    block = cr.render_round_block("FA", 2, cr.RoleConfig("claude", "claude-opus-5-5", "high"), _reply())
    assert "Effort: `high` · Mode: Cross-Rebuttal" in block


# ------------------------------------------------------------------ end-to-end flow

def test_round1_unanimous_consensus(plan):
    fake = FakeCouncil({1: {a: _reply() for a in cr.ROLE_ORDER}})
    with patch.object(cr, "run_role", side_effect=fake):
        code, result = cr.run(["--plan", str(plan)])
    assert code == cr.EXIT_OK
    assert result["status"] == cr.STATUS_CONSENSUS
    assert result["round"] == 1
    assert len(fake.calls) == 3
    text = plan.read_text(encoding="utf-8")
    assert text.index("Council Round 1: Functional Architect (FA)") < text.index("Council Round 1: Technical Lead (TL)") < text.index("Council Round 1: Quality Assurance (QA)")
    assert "## ✅ Council Consensus (Round 1)" in text
    assert "CSV export is in scope (FA, TL, QA)" in text


def test_round2_resolves_disagreement(plan):
    fake = FakeCouncil({
        1: {"FA": _reply(), "TL": _reply("DISAGREED", ["Missing migration"]), "QA": _reply()},
        2: {a: _reply() for a in cr.ROLE_ORDER},
    })
    with patch.object(cr, "run_role", side_effect=fake):
        code, result = cr.run(["--plan", str(plan)])
    assert code == cr.EXIT_OK
    assert result["status"] == cr.STATUS_CONSENSUS and result["round"] == 2
    assert len(fake.calls) == 6
    round2_prompts = [p for _, p, _ in fake.calls[3:]]
    assert all("Cross-Rebuttal" in p for p in round2_prompts)


def test_deadlock_writes_dossier_then_proceed(plan):
    deadlocked = {"FA": _reply(), "TL": _reply(), "QA": _reply("DISAGREED", ["No regression tests"], compromise="Add a CSV contract test.", position="Untested.")}
    fake = FakeCouncil({1: deadlocked, 2: deadlocked})
    with patch.object(cr, "run_role", side_effect=fake):
        code, result = cr.run(["--plan", str(plan)])
    assert code == cr.EXIT_DEADLOCK
    assert result["status"] == cr.STATUS_DEADLOCKED
    assert result["unresolved_points"] == ["[QA] No regression tests"]
    text = plan.read_text(encoding="utf-8")
    assert "## ⚠️ Council Deadlock Escalation Report (Round 2)" in text
    assert "### 1. Consensus Items (Agreed)" in text
    assert "**QA Position** (QA Lead, `VERDICT: DISAGREED`): Untested." in text
    assert "Majority (FA, TL) accepts the subject" in text
    assert "Add a CSV contract test." in text
    assert f'orchestrator council --plan "{plan}" --continue 2' in text
    assert f'orchestrator council --plan "{plan}" --proceed provision-story' in text

    # Re-running without a decision does not invoke any reviewer.
    with patch.object(cr, "run_role", side_effect=AssertionError("must not run")):
        code, result = cr.run(["--plan", str(plan)])
    assert code == cr.EXIT_DEADLOCK

    code, result = cr.run(["--plan", str(plan), "--proceed", "provision-story"])
    assert code == cr.EXIT_OK and result["status"] == cr.STATUS_ACCEPTED
    assert "## ✅ Council Compromise Accepted (Round 2)" in plan.read_text(encoding="utf-8")
    _, result = cr.run(["--plan", str(plan), "--check-status"])
    assert result["council_status"] == cr.STATUS_ACCEPTED


def test_continue_runs_extra_rounds(plan):
    split = {"FA": _reply(), "TL": _reply("DISAGREED", ["x"]), "QA": _reply("DISAGREED", ["y"])}
    fake = FakeCouncil({1: split, 2: split, 3: split, 4: {a: _reply() for a in cr.ROLE_ORDER}})
    with patch.object(cr, "run_role", side_effect=fake):
        code, _ = cr.run(["--plan", str(plan)])
        assert code == cr.EXIT_DEADLOCK
        code, result = cr.run(["--plan", str(plan), "--continue", "2"])
    assert code == cr.EXIT_OK
    assert result["status"] == cr.STATUS_CONSENSUS and result["round"] == 4 and result["max_rounds"] == 4
    text = plan.read_text(encoding="utf-8")
    assert "## ⏩ Council Continuation: +2 rounds" in text
    assert "No majority" in text  # the first dossier had only FA agreeing


def test_continue_and_proceed_rejected_without_deadlock(plan):
    code, result = cr.run(["--plan", str(plan), "--continue", "2"])
    assert code == cr.EXIT_ERROR and "deadlock" in result["error"]
    code, result = cr.run(["--plan", str(plan), "--proceed", "provision-story"])
    assert code == cr.EXIT_ERROR


def test_reviewer_failure_records_nothing(plan):
    before = plan.read_bytes()

    def failing(abbr, prompt, config, args):
        return _outcome("", code=124) if abbr == "QA" else _outcome(_reply())

    with patch.object(cr, "run_role", side_effect=failing):
        code, result = cr.run(["--plan", str(plan)])
    assert code == cr.EXIT_ERROR
    assert "QA" in result["failures"]
    assert plan.read_bytes() == before


def test_reviewer_edit_is_reverted(plan):
    def tampering(abbr, prompt, config, args):
        if abbr == "TL":
            plan.write_text("CLOBBERED", encoding="utf-8")
        return _outcome(_reply())

    with patch.object(cr, "run_role", side_effect=tampering):
        code, result = cr.run(["--plan", str(plan)])
    assert code == cr.EXIT_OK
    assert result["warnings"]
    text = plan.read_text(encoding="utf-8")
    assert text.startswith("# 📋 Implementation Plan") and "CLOBBERED" not in text


def test_diff_range_attached_as_subject(plan):
    diff = "diff --git a/x.py b/x.py\n+print('hi')\n"
    real_run = cr.subprocess.run

    def fake_run(cmd, *a, **kw):
        if cmd[:2] == ["git", "diff"]:
            return SimpleNamespace(returncode=0, stdout=diff, stderr="")
        return real_run(cmd, *a, **kw)

    fake = FakeCouncil({1: {a: _reply() for a in cr.ROLE_ORDER}})
    with patch.object(cr.subprocess, "run", side_effect=fake_run), patch.object(cr, "run_role", side_effect=fake):
        code, _ = cr.run(["--plan", str(plan), "--diff-range", "main...HEAD"])
    assert code == cr.EXIT_OK
    assert "## 🧾 Council Subject: git diff main...HEAD" in plan.read_text(encoding="utf-8")
    fa_prompt = next(p for a, p, _ in fake.calls if a == "FA")
    assert "print('hi')" in fa_prompt


def test_missing_plan_is_error(tmp_path):
    code, result = cr.run(["--plan", str(tmp_path / "nope.md")])
    assert code == cr.EXIT_ERROR and "not found" in result["error"]


# ------------------------------------------------------------------ orchestrator council CLI

def test_orchestrator_council_forwards_arguments(tmp_path):
    from orchestrator import cli

    runner = CliRunner()
    with patch("subprocess.run", return_value=SimpleNamespace(returncode=2)) as run:
        result = runner.invoke(cli.app, ["council", "--plan", "p.md", "--continue", "2", "--max-rounds", "3", "--config", "c.json"])
    assert result.exit_code == 2
    cmd = run.call_args.args[0]
    assert cmd[0] == sys.executable
    assert cmd[1].endswith("council_review.py")
    assert cmd[2:] == ["--plan", "p.md", "--continue", "2", "--max-rounds", "3", "--config", "c.json"]


def test_orchestrator_council_proceed_and_status():
    from orchestrator import cli

    runner = CliRunner()
    with patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
        result = runner.invoke(cli.app, ["council", "--proceed", "provision-story", "--check-status"])
    assert result.exit_code == 0
    assert run.call_args.args[0][2:] == ["--proceed", "provision-story", "--check-status"]


def test_resolve_council_script_points_at_repo_skill():
    from orchestrator import cli

    assert cli.resolve_council_script() == SKILL_SCRIPT_DIR / "council_review.py"
