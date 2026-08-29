from __future__ import annotations

import asyncio
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock

from orchestrator.config import GlobalConfig, HarnessConfig, NodeConfig, ProjectConfig, SettingsConfig
from orchestrator.db import StateManager
from orchestrator.harness import AsyncHarnessAdapter
from orchestrator.nodes.supervisor import (
    POEvaluationResult,
    compute_issue_hash,
    evaluate_supervisor_issue,
    parse_po_evaluation_response,
    run_supervisor_node,
)
from orchestrator import poller


@pytest.fixture
def mock_config(tmp_path: Path) -> GlobalConfig:
    return GlobalConfig(
        settings=SettingsConfig(
            db_path=str(tmp_path / "state.db"),
            log_dir=str(tmp_path / "logs"),
        ),
        harnesses={
            "antigravity": HarnessConfig(
                binary="agy",
                flags=["--dangerously-skip-permissions"],
                timeout_minutes=10,
            ),
            "claude": HarnessConfig(
                binary="claude",
                flags=["-p"],
                timeout_minutes=10,
            ),
        },
    )


@pytest.fixture
def test_project(tmp_path: Path) -> ProjectConfig:
    return ProjectConfig(
        name="test-app",
        repo="AntaresAndBharani/test-app",
        local_path=str(tmp_path),
        nodes={
            "supervisor": NodeConfig(
                enabled=True,
                harness="antigravity",
                model="gemini-3.7-flash-low",
            )
        },
    )


@pytest.mark.asyncio
async def test_complete_requirements_approved_and_promoted(tmp_path: Path, mock_config: GlobalConfig, test_project: ProjectConfig, monkeypatch):
    """
    Scenario: Complete requirements are approved and promoted
      Given an issue labeled `needs-po-review` with a new or modified body hash
      When the Supervisor evaluates the issue via the PO evaluation prompt (`gemini-3.7-flash-low`)
      And the harness response confirms complete functional requirements
      Then the issue body is updated to include Acceptance Criteria formatted as Given/When/Then
      And the `needs-po-review` label is removed
      And the `needs-triage` label is applied
      And `upsert_po_tracking(repo, issue, body_hash, status="PO_APPROVED", gherkin_ac=<generated AC>)` is called
    """
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    issue_data = {
        "number": 105,
        "title": "feat(auth): add OAuth2 refresh token rotation",
        "body": "Implement secure refresh token rotation adhering to RFC 6749 Section 6.",
        "labels": [{"name": "needs-po-review"}],
    }

    mock_llm_response = """
VERDICT: PO_APPROVED
GAPS:
None

GHERKIN_AC:
```gherkin
Feature: OAuth2 Token Rotation
  Scenario: Refresh expired access token
    Given a valid refresh token
    When the client requests a new access token
    Then a new access token and rotated refresh token are issued
    And the old refresh token is invalidated
```
"""

    executed_harness_calls = []

    async def mock_execute(self, prompt, cwd, log_file, model=None, effort=None, extra_env=None, console_prefix=None):
        executed_harness_calls.append({
            "prompt": prompt,
            "cwd": cwd,
            "log_file": log_file,
            "model": model,
            "effort": effort,
        })
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        Path(log_file).write_text(mock_llm_response, encoding="utf-8")
        return 0

    monkeypatch.setattr("orchestrator.nodes.supervisor.AsyncHarnessAdapter.is_available", lambda self: True)
    monkeypatch.setattr("orchestrator.nodes.supervisor.AsyncHarnessAdapter.execute", mock_execute)

    # Track gh subprocess executions
    subprocess_cmds = []

    async def mock_create_subprocess_exec(*cmd, **kwargs):
        subprocess_cmds.append(list(cmd))
        mock_proc = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        return mock_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_create_subprocess_exec)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/gh" if cmd == "gh" else None)

    res = await evaluate_supervisor_issue(
        project=test_project,
        issue=issue_data,
        config=mock_config,
        state_manager=state_manager,
        dry_run=False,
    )

    # 1. Assert evaluation verdict
    assert res.verdict == "PO_APPROVED"
    assert res.status == "PO_APPROVED"
    assert res.skipped is False
    assert res.gherkin_ac is not None
    assert "Feature: OAuth2 Token Rotation" in res.gherkin_ac

    # 2. Assert harness invocation used configured model
    assert len(executed_harness_calls) == 1
    assert executed_harness_calls[0]["model"] == "gemini-3.7-flash-low"
    assert "OAuth2 refresh token rotation" in executed_harness_calls[0]["prompt"]

    # 3. Assert GitHub CLI mutations: edit (label swap + body) and comment
    edit_cmd = next((c for c in subprocess_cmds if "edit" in c), None)
    assert edit_cmd is not None
    assert edit_cmd[0] == "gh"
    assert edit_cmd[1] == "issue"
    assert edit_cmd[2] == "edit"
    assert edit_cmd[3] == "105"
    assert "--remove-label" in edit_cmd
    assert edit_cmd[edit_cmd.index("--remove-label") + 1] == "needs-po-review"
    assert "--add-label" in edit_cmd
    assert edit_cmd[edit_cmd.index("--add-label") + 1] == "needs-triage"

    comment_cmd = next((c for c in subprocess_cmds if "comment" in c), None)
    assert comment_cmd is not None
    assert "PO-Proxy Approval" in comment_cmd[comment_cmd.index("--body") + 1]

    # 4. Assert Blackboard persistence (po_tracking)
    po_record = await state_manager.get_po_tracking(test_project.repo, 105)
    assert po_record is not None
    assert po_record["status"] == "PO_APPROVED"
    assert "Feature: OAuth2 Token Rotation" in po_record["gherkin_ac"]
    assert po_record["body_hash"] == compute_issue_hash(issue_data["title"], issue_data["body"])


@pytest.mark.asyncio
async def test_harness_dispatch_model_configuration(tmp_path: Path, mock_config: GlobalConfig, monkeypatch):
    """
    Scenario: Harness dispatch uses configured model
      Given the Supervisor dispatches a PO evaluation
      Then it invokes `AsyncHarnessAdapter` with the `gemini-3.7-flash-low` harness/model configuration for the PO evaluation prompt
    """
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    project = ProjectConfig(
        name="custom-app",
        repo="AntaresAndBharani/custom-app",
        local_path=str(tmp_path),
        nodes={
            "supervisor": NodeConfig(
                enabled=True,
                harness="antigravity",
                model="gemini-3.7-flash-low",
                effort="low",
            )
        },
    )

    issue_data = {
        "number": 12,
        "title": "feat: custom story",
        "body": "User story details",
        "labels": [{"name": "needs-po-review"}],
    }

    harness_calls = []

    async def mock_execute(self, prompt, cwd, log_file, model=None, effort=None, extra_env=None, console_prefix=None):
        harness_calls.append({
            "harness": self.name,
            "model": model,
            "effort": effort,
            "prompt": prompt,
        })
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        Path(log_file).write_text("VERDICT: PO_APPROVED\nGAPS:\nNone\nGHERKIN_AC:\n```gherkin\nFeature: Test\n  Scenario: S1\n    Given g\n    When w\n    Then t\n```", encoding="utf-8")
        return 0

    monkeypatch.setattr("orchestrator.nodes.supervisor.AsyncHarnessAdapter.is_available", lambda self: True)
    monkeypatch.setattr("orchestrator.nodes.supervisor.AsyncHarnessAdapter.execute", mock_execute)
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    res = await evaluate_supervisor_issue(
        project=project,
        issue=issue_data,
        config=mock_config,
        state_manager=state_manager,
        dry_run=True,
    )

    assert len(harness_calls) == 1
    assert harness_calls[0]["harness"] == "antigravity"
    assert harness_calls[0]["model"] == "gemini-3.7-flash-low"
    assert harness_calls[0]["effort"] == "low"
    assert "INVEST principles" in harness_calls[0]["prompt"]


@pytest.mark.asyncio
async def test_ambiguous_requirements_clarification_escalation(tmp_path: Path, mock_config: GlobalConfig, test_project: ProjectConfig, monkeypatch):
    """
    Scenario: Incomplete or ambiguous requirements trigger clarification escalation
      Given an issue with missing requirements
      When the harness returns NEEDS_HUMAN_CLARIFICATION with gaps
      Then a clarifying comment is posted
      And needs-po-review is retained
      And po_tracking status is set to NEEDS_HUMAN_CLARIFICATION
    """
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    issue_data = {
        "number": 106,
        "title": "feat(billing): support crypto payments",
        "body": "We should accept bitcoin.",
        "labels": [{"name": "needs-po-review"}],
    }

    mock_llm_response = """
VERDICT: NEEDS_HUMAN_CLARIFICATION
GAPS:
1. Which crypto networks and tokens are supported (e.g. Bitcoin Mainnet, Lightning)?
2. What exchange rate oracle or payment processor (BitPay, Coinbase Commerce) should be integrated?
3. What is the confirmation threshold before order fulfillment?
GHERKIN_AC:
None
"""

    async def mock_execute(self, prompt, cwd, log_file, model=None, effort=None, extra_env=None, console_prefix=None):
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        Path(log_file).write_text(mock_llm_response, encoding="utf-8")
        return 0

    monkeypatch.setattr("orchestrator.nodes.supervisor.AsyncHarnessAdapter.is_available", lambda self: True)
    monkeypatch.setattr("orchestrator.nodes.supervisor.AsyncHarnessAdapter.execute", mock_execute)

    subprocess_cmds = []

    async def mock_create_subprocess_exec(*cmd, **kwargs):
        subprocess_cmds.append(list(cmd))
        mock_proc = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        return mock_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_create_subprocess_exec)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/gh" if cmd == "gh" else None)

    res = await evaluate_supervisor_issue(
        project=test_project,
        issue=issue_data,
        config=mock_config,
        state_manager=state_manager,
        dry_run=False,
    )

    assert res.verdict == "NEEDS_HUMAN_CLARIFICATION"
    assert res.status == "NEEDS_HUMAN_CLARIFICATION"
    assert "Which crypto networks" in (res.gaps or "")

    # No label edit command should be run (needs-po-review is retained)
    edit_cmd = next((c for c in subprocess_cmds if "edit" in c), None)
    assert edit_cmd is None

    # Clarifying comment must be posted
    comment_cmd = next((c for c in subprocess_cmds if "comment" in c), None)
    assert comment_cmd is not None
    assert "PO-Proxy Human Escalation" in comment_cmd[comment_cmd.index("--body") + 1]

    # Blackboard must record NEEDS_HUMAN_CLARIFICATION
    po_record = await state_manager.get_po_tracking(test_project.repo, 106)
    assert po_record is not None
    assert po_record["status"] == "NEEDS_HUMAN_CLARIFICATION"
    assert "Which crypto networks" in po_record["blockers"]


@pytest.mark.asyncio
async def test_zero_token_hash_skip_and_re_evaluation_on_edit(tmp_path: Path, mock_config: GlobalConfig, test_project: ProjectConfig, monkeypatch):
    """
    Scenario: Zero-token hash skip gate and re-evaluation upon body modification
      1. Issue initially evaluated as NEEDS_HUMAN_CLARIFICATION.
      2. Subsequent cycle with same body skips evaluation (0 tokens).
      3. User modifies issue body -> hash changes -> re-evaluation runs and approves.
    """
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    title = "feat: analytics webhook"
    body_v1 = "Send analytics events somewhere."
    body_hash_v1 = compute_issue_hash(title, body_v1)

    # Pre-seed NEEDS_HUMAN_CLARIFICATION
    await state_manager.upsert_po_tracking(
        repo=test_project.repo,
        issue_number=107,
        body_hash=body_hash_v1,
        status="NEEDS_HUMAN_CLARIFICATION",
        blockers="Missing endpoint URL and schema format.",
    )

    harness_calls = []

    async def mock_execute(self, prompt, cwd, log_file, model=None, effort=None, extra_env=None, console_prefix=None):
        harness_calls.append(prompt)
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        Path(log_file).write_text("""
VERDICT: PO_APPROVED
GAPS:
None
GHERKIN_AC:
```gherkin
Feature: Analytics Webhook
  Scenario: Deliver event payload
    Given a configured webhook URL
    When an analytics event triggers
    Then the JSON payload is dispatched via HTTP POST
```
""", encoding="utf-8")
        return 0

    monkeypatch.setattr("orchestrator.nodes.supervisor.AsyncHarnessAdapter.is_available", lambda self: True)
    monkeypatch.setattr("orchestrator.nodes.supervisor.AsyncHarnessAdapter.execute", mock_execute)
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    # 1. First run with unmodified body: Zero-Token Skip Gate
    issue_v1 = {"number": 107, "title": title, "body": body_v1}
    res_skip = await evaluate_supervisor_issue(
        project=test_project,
        issue=issue_v1,
        config=mock_config,
        state_manager=state_manager,
        dry_run=False,
    )
    assert res_skip.skipped is True
    assert len(harness_calls) == 0

    # 2. User edits body with detailed specs: New hash -> Re-evaluation runs
    body_v2 = (
        "Send analytics events via HTTP POST to configured webhook URL in JSON format.\n"
        "Retry 3 times with exponential backoff on 5xx responses."
    )
    issue_v2 = {"number": 107, "title": title, "body": body_v2}
    res_reval = await evaluate_supervisor_issue(
        project=test_project,
        issue=issue_v2,
        config=mock_config,
        state_manager=state_manager,
        dry_run=False,
    )
    assert res_reval.skipped is False
    assert res_reval.verdict == "PO_APPROVED"
    assert len(harness_calls) == 1

    # Blackboard updated to PO_APPROVED
    po_record = await state_manager.get_po_tracking(test_project.repo, 107)
    assert po_record["status"] == "PO_APPROVED"
    assert po_record["body_hash"] == compute_issue_hash(title, body_v2)


@pytest.mark.asyncio
async def test_dry_run_evaluation_does_not_mutate(tmp_path: Path, mock_config: GlobalConfig, test_project: ProjectConfig, monkeypatch):
    """
    Scenario: --dry-run evaluation performs inspection without mutating GitHub or DB
    """
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    issue_data = {
        "number": 108,
        "title": "feat: rate limiting",
        "body": "Apply leaky bucket rate limiting per IP address.",
    }

    mock_llm_response = """
VERDICT: PO_APPROVED
GAPS:
None
GHERKIN_AC:
```gherkin
Feature: Rate Limiting
  Scenario: Exceed request quota
    Given a client sending > 100 req/min
    When the 101st request arrives
    Then HTTP 429 Too Many Requests is returned
```
"""

    async def mock_execute(self, prompt, cwd, log_file, model=None, effort=None, extra_env=None, console_prefix=None):
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        Path(log_file).write_text(mock_llm_response, encoding="utf-8")
        return 0

    monkeypatch.setattr("orchestrator.nodes.supervisor.AsyncHarnessAdapter.is_available", lambda self: True)
    monkeypatch.setattr("orchestrator.nodes.supervisor.AsyncHarnessAdapter.execute", mock_execute)

    subprocess_called = []

    async def mock_create_subprocess_exec(*cmd, **kwargs):
        subprocess_called.append(cmd)
        mock_proc = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        return mock_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_create_subprocess_exec)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/gh" if cmd == "gh" else None)

    res = await evaluate_supervisor_issue(
        project=test_project,
        issue=issue_data,
        config=mock_config,
        state_manager=state_manager,
        dry_run=True,
    )

    assert res.verdict == "PO_APPROVED"
    assert "Dry-run evaluation complete" in res.details
    assert len(subprocess_called) == 0

    # No record in DB
    po_record = await state_manager.get_po_tracking(test_project.repo, 108)
    assert po_record is None


@pytest.mark.asyncio
async def test_run_supervisor_node_processes_needs_po_review_issues(tmp_path: Path, mock_config: GlobalConfig, test_project: ProjectConfig, monkeypatch):
    """
    Scenario: run_supervisor_node polls 'needs-po-review' issues and promotes approved ones
    """
    state_manager = StateManager(tmp_path / "state.db")
    await state_manager.init_db()

    mock_issues = [
        {
            "number": 201,
            "title": "feat(cache): Redis caching layer",
            "body": "Cache database query results in Redis with 5m TTL.",
            "labels": [{"name": "needs-po-review"}],
            "createdAt": "2026-08-29T10:00:00Z",
        }
    ]

    async def mock_fetch_issues(repo, label, limit=5):
        if label == "needs-po-review":
            return mock_issues
        return []

    monkeypatch.setattr(poller, "fetch_issues_with_label", mock_fetch_issues)
    monkeypatch.setattr(poller, "fetch_open_prs", AsyncMock(return_value=[]))
    monkeypatch.setattr(poller, "fetch_all_open_issues", AsyncMock(return_value=mock_issues))

    mock_response = """
VERDICT: PO_APPROVED
GAPS:
None
GHERKIN_AC:
```gherkin
Feature: Redis Caching
  Scenario: Cache hit
    Given key in cache
    When queried
    Then return cached value
```
"""

    async def mock_execute(self, prompt, cwd, log_file, model=None, effort=None, extra_env=None, console_prefix=None):
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        Path(log_file).write_text(mock_response, encoding="utf-8")
        return 0

    monkeypatch.setattr("orchestrator.nodes.supervisor.AsyncHarnessAdapter.is_available", lambda self: True)
    monkeypatch.setattr("orchestrator.nodes.supervisor.AsyncHarnessAdapter.execute", mock_execute)
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    ran, msg = await run_supervisor_node(
        project=test_project,
        config=mock_config,
        state_manager=state_manager,
        force=True,
    )

    assert ran is True
    assert "Approved Issue #201" in msg
    assert "needs-triage" in msg

    po_record = await state_manager.get_po_tracking(test_project.repo, 201)
    assert po_record is not None
    assert po_record["status"] == "PO_APPROVED"
