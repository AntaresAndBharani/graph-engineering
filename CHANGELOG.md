# Changelog

All notable changes to the Graph Engineering Agentic SDLC architecture will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Added **Automated Gherkin Acceptance Criteria Enrichment & Promotion (`orchestrator/nodes/supervisor.py`)**: Implemented automated functional requirement evaluation via `gemini-3.7-flash-low`, Gherkin Given/When/Then Acceptance Criteria generation and issue body enrichment, label promotion from `needs-po-review` to `needs-triage`, human clarification loop guarding with structured feedback comments, and `PO_APPROVED` status synchronization on the SQLite WAL Blackboard (`po_tracking`).
- Added comprehensive unit and integration test coverage (`tests/test_supervisor_po.py`) verifying Given/When/Then acceptance criteria, model configuration, label transitions, and zero-token hash skip gating.
- Added **Supervisor PO-Proxy CLI Inspection & Evaluation (`orchestrator supervisor evaluate` & `orchestrator supervisor status`)**: Exposes interactive and dry-run (`--dry-run`) evaluation commands for issues labeled `needs-po-review` with Gherkin acceptance criteria generation, SHA-256 hash gating, and persistent `po_tracking` Blackboard status reporting.
- Added **Decoupled Blackboard `po_tracking` Table (`orchestrator/db.py`)**: Persistent SQLite WAL table and helper methods (`upsert_po_tracking`, `get_po_tracking`, `delete_po_tracking`, `list_po_trackings`) for cross-node PO readiness context.
- Implemented the **Decoupled, Agnostic Multi-Agent Orchestrator CLI (`graph-orchestrator`)** in Python (`pyproject.toml`, `orchestrator/`), decoupling workflow execution and state from target application repositories.
- Added **Zero-Token Idle Polling**: GitHub issues/PRs are queried deterministically using batched GitHub CLI / GraphQL queries, spending zero AI tokens during idle periods.
- Added **Agnostic AI CLI Harness Adapter Pattern** (`orchestrator/harness.py`): Supports interchangeable execution across `claude` (Claude Code), `agy` (Antigravity CLI), `devin`, or custom runners via declarative configuration (`config.yaml`).
- Added **Model Level of Effort / Reasoning Configuration**: Supported via `effort_flag` in `HarnessConfig` (e.g. `--effort low|medium|high|max` for Claude), embedded model naming for Antigravity (`gemini-3.7-flash-thinking`), and `effort` specifications in `NodeConfig`.
- Implemented **Native OAuth Subscription Token Support**: Harnesses seamlessly inherit user profile paths and OAuth credentials, utilizing developers' active hired subscriptions rather than requiring raw API tokens.
- Implemented **Consistency Supervisor Node (Node 0)** (`orchestrator/nodes/supervisor.py`): Scheduled/startup self-healing node with zero-token anomaly filtering (detects merge conflicts, orphaned PRs, stuck locks) that auto-resolves state or escalates to `needs-po-review`.
- Implemented **Architect Node (Node 1)** (`orchestrator/nodes/architect.py`): Evaluates `needs-triage` issues, decomposes stories into INVEST/3-amigos subtasks with Gherkin criteria, and provisions child issues labeled `ready-for-dev`.
- Implemented **3AmigosDevTest Node (Node 2)** (`orchestrator/nodes/devtest.py`): Sanitizes workspace with strict remote-matching git safety checks, generates code and unit/integration tests, verifies test pass and non-empty git diff, opens PRs labeled `needs-architect-review`, and auto-merges when approved.
- Implemented **State Locking & Concurrency Engine** (`orchestrator/db.py`): Asynchronous SQLite database (`state.db`) configured with Write-Ahead Logging (WAL) and TTL lock expiration to prevent duplicate executions and recover from daemon restarts.
- Implemented **Structured Logging & ANSI Stripping** (`orchestrator/logging.py`): Dedicated per-project/per-node log hierarchies (`~/.config/orchestrator/logs/<project>/<node>/`) with active regex ANSI filtering and automatic rotation.
- Added **Automated Label Housekeeping** (`orchestrator/housekeeping.py`): Automatic idempotent provisioning of workflow taxonomy labels across all registered repositories using `gh label create --force`.
- Added **CLI Command Suite** (`orchestrator/cli.py`): Full Typer + Rich CLI supporting `run`, `watch` (live dashboard), `list`, `doctor` (with optional `--sync-labels`), `init`, `labels` (taxonomy sync/list), `ingest`, `clean`, and `logs`.
- Added full unit and integration test suite (`tests/`) achieving 100% pass rate across configuration, state locking, harness execution, zero-token gating, and CLI diagnostics.
- Added GitHub Actions CI workflow (`.github/workflows/ci.yml`) matrix-testing across Python 3.11 and 3.12 with automated build verification.
- Added configuration template `templates/config.example.yaml`.
- Created `docs/consistency-supervisor-node.md` specifying the Consistency Supervisor Node for cross-project health verification, anomaly detection, log analysis, and incident alerting across all Graph Engineering pipelines.
- Implemented `scripts/run-consistency-supervisor.ps1` providing deterministic multi-project audits across Windows Task Scheduler (`CTA-*`, `DT-*`), local logs (`logs/local-pipeline/*.log`), `.git/index.lock` collisions, and remote GitHub Actions workflow executions.
- Added automated Markdown health dashboard generation (`docs/pipeline-health-dashboard.md`) synthesizing executive overviews, per-project task matrices, 4h vs 24h error comparison tables, and actionable remediation checklists focused exclusively on active anomalies from the last 4 hours.
- Added Telegram Bot push alerting with anti-spam state caching (`logs/supervisor-state.json`) and recovery notifications.
- Added `docs/github-repositories-directory.html` and `scripts/export-github-directory.ps1` providing an interactive directory of all ecosystem repositories with direct filtered links to open User Stories (`is:open label:type:user-story`), Pull Requests, GitHub Actions, and live GitHub API status refresh buttons.
- Created `docs/e2e-testing-recommendations.md` detailing the 5 pillars of End-to-End (E2E) testing in Graph Engineering (declarative flows, delta execution mapping via `flow-mapping.json`, deterministic runner scripts, external QA repository archival, and sticky PR evidence reporting).
- Added cross-stack E2E implementation blueprints for Android/Compose (Maestro), Web applications (Playwright), and Python CLI suites (Pester/pytest).
- Added cross-node contracts integrating E2E testing into Three Amigos (BDD scenarios & tag mapping), Dev & Test (delta test execution, visual artifact capture), and PR Review (evidence verification).
- Added E2E implementation lessons and resilience hardening derived from live testing in `crosstrainingapp` (temp-safe body writing, non-fatal comment publishing, QA release pre-validation).
- Added local CLI pipeline resilience hardening to `docs/local-cli-pipeline.md`: Windows 32KB command-line length limits (`Process.Start`) mitigated via standard input streaming (`claude.exe -p`), robust JSON boundary extraction (`(?s)(\{.*\})`) with Python fallback parser, and strict PowerShell 5.1 array semantics in `ConvertFrom-JsonSafeArray` via `List[psobject]`.
### Changed
- Updated `README.md` to reference `docs/e2e-testing-recommendations.md` in the architectural specifications and record E2E testing lessons learned.
- Updated `docs/dev-test-node.md` to include delta E2E verification, visual artifact synchronization, and sticky PR evidence posting in Dev & Test node responsibilities.
- Updated `docs/three-amigos-node.md` to include E2E flow tag identification and BDD scenario mapping in QA evaluation.
- Updated `docs/pr-review-node.md` to include `<!-- e2e-evidence -->` PR comment and screenshot verification in PR Review guidelines.
