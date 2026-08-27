# Changelog

All notable changes to the Graph Engineering Agentic SDLC architecture will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Created `docs/consistency-supervisor-node.md` specifying the Consistency Supervisor Node for cross-project health verification, anomaly detection, log analysis, and incident alerting across all Graph Engineering pipelines.
- Implemented `scripts/run-consistency-supervisor.ps1` providing deterministic multi-project audits across Windows Task Scheduler (`CTA-*`, `DT-*`), local logs (`logs/local-pipeline/*.log`), `.git/index.lock` collisions, and remote GitHub Actions workflow executions.
- Added automated Markdown health dashboard generation (`docs/pipeline-health-dashboard.md`) synthesizing executive overviews, per-project task matrices, 4h vs 24h error comparison tables, and actionable remediation checklists focused exclusively on active anomalies from the last 4 hours.
- Added Telegram Bot push alerting with anti-spam state caching (`logs/supervisor-state.json`) and recovery notifications.
- Created `scripts/register-supervisor-task.ps1` to register `GE-ConsistencySupervisor` as a recurring Windows Task Scheduler task.
- Created `docs/e2e-testing-recommendations.md` detailing the 5 pillars of End-to-End (E2E) testing in Graph Engineering (declarative flows, delta execution mapping via `flow-mapping.json`, deterministic runner scripts, external QA repository archival, and sticky PR evidence reporting).
- Added cross-stack E2E implementation blueprints for Android/Compose (Maestro), Web applications (Playwright), and Python CLI suites (Pester/pytest).
- Added cross-node contracts integrating E2E testing into Three Amigos (BDD scenarios & tag mapping), Dev & Test (delta test execution, visual artifact capture), and PR Review (evidence verification).
- Added `scripts/run-hidden.vbs` trampoline to execute Windows scheduled tasks silently in the background with `SW_HIDE (0)` without opening console windows or stealing pointer focus.
- Updated `scripts/register-supervisor-task.ps1` to register `GE-ConsistencySupervisor` using `wscript.exe` and `run-hidden.vbs`.

### Changed
- Updated `docs/local-cli-pipeline.md` documenting the console popup and focus-stealing bug class under Windows Task Scheduler and its resolution via GUI-subsystem launchers.
- Updated `README.md` to reference `docs/e2e-testing-recommendations.md` in the architectural specifications and record E2E testing lessons learned.
- Updated `docs/dev-test-node.md` to include delta E2E verification, visual artifact synchronization, and sticky PR evidence posting in Dev & Test node responsibilities.
- Updated `docs/three-amigos-node.md` to include E2E flow tag identification and BDD scenario mapping in QA evaluation.
- Updated `docs/pr-review-node.md` to include `<!-- e2e-evidence -->` PR comment and screenshot verification in PR Review guidelines.
