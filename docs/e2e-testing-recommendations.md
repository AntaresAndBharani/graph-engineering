# E2E Testing Recommendations & Architecture

Comprehensive guide and architectural standards for End-to-End (E2E) testing
within the Graph Engineering Agentic SDLC pipeline.

**Live reference implementation:**
[`AntaresAndBharani/crosstrainingapp`](https://github.com/AntaresAndBharani/crosstrainingapp)
(Kotlin / Jetpack Compose Android app with Maestro, delta execution mapping,
sticky PR test evidence, and dedicated QA repository storage).

---

## 1. Why E2E Testing Matters in Agentic SDLC

In an autonomous multi-agent pipeline (Architect → Three Amigos → Dev & Test →
PR Review → Merge & Backlog), unit tests alone are necessary but insufficient:

- **Unit tests verify logic in isolation**, but autonomous code generation can
  silently break UI layouts, user interaction flows, database migrations, or
  navigation routing without failing a single unit test.
- **Visual & functional regressions** caught late in human review stall the
  pipeline and break autonomous continuous delivery.
- **LLM PR Reviewers (Claude Sonnet/Opus)** cannot render UI components or run
  interactive apps directly in CI — they require structured, visual, and
  deterministic test evidence surfaced on the PR to evaluate code quality
  authoritatively.

The Graph Engineering E2E architecture bridges this gap through a fast,
deterministic, and modular testing framework.

---

## 2. The 5 Core Pillars of Graph Engineering E2E

```
+-----------------------------------------------------------------------------+
|                          5 Pillars of Agentic E2E                           |
+-----------------------------------------------------------------------------+
| 1. Declarative Flows        -> Human & LLM-readable test YAML/specs         |
| 2. Delta Execution Mapping  -> Targeted runs via flow-mapping.json          |
| 3. Deterministic Runner     -> Single runner script (local/CI parity)       |
| 4. QA Artifact Archival     -> Dedicated QA repo (<project>-qa) for assets  |
| 5. Sticky PR Evidence       -> In-place <!-- e2e-evidence --> PR reporting  |
+-----------------------------------------------------------------------------+
```

### Pillar 1: Declarative Test Flows

E2E tests must be written in declarative, human-readable formats that both
developers and LLM agents (Three Amigos, Dev & Test) can easily generate,
inspect, and update without fragile imperative boilerplate.

- **Location:** `e2e/flows/*.yaml` (or framework equivalent, e.g. `e2e/specs/`).
- **Structure:** Modular, single-purpose user journeys tagged by domain area:
  - `01_auth_flow.yaml` (tags: `["auth", "smoke"]`)
  - `02_log_session_flow.yaml` (tags: `["logging", "core"]`)
  - `03_history_and_search_flow.yaml` (tags: `["history"]`)
  - `04_library_categories_flow.yaml` (tags: `["library"]`)
  - `05_theme_mode_flow.yaml` (tags: `["theme", "settings"]`)
  - `06_coach_mode_flow.yaml` (tags: `["coach"]`)
- **Framework Recommendation:**
  - **Mobile (Android/iOS):** [Maestro](https://maestro.mobile.dev/) (`.yaml` flows).
  - **Web Apps:** [Playwright](https://playwright.dev/) or Cypress declarative specs.
  - **CLI / Backend:** Pester (`.Tests.ps1`) or pytest integration suites.

### Pillar 2: Delta Execution & Flow Mapping (`flow-mapping.json`)

Running an entire E2E suite on every minor subtask slows down agent loops and
burns unnecessary emulator/runner time. Graph Engineering uses a deterministic
mapping file (`e2e/flow-mapping.json`) to map modified source files to specific
E2E tags.

#### Schema (`e2e/flow-mapping.json`):

```json
{
  "version": "1.0",
  "description": "Deterministic mapping from changed file path patterns to E2E flow tags.",
  "rules": [
    {
      "pattern": "app/src/main/java/**/ui/screens/Login*.kt",
      "tags": ["auth"]
    },
    {
      "pattern": "app/src/main/java/**/ui/screens/Session*.kt",
      "tags": ["logging"]
    },
    {
      "pattern": "app/src/main/java/**/ui/theme/**",
      "tags": ["theme", "settings"]
    },
    {
      "pattern": "app/src/main/java/**/MainActivity.kt",
      "tags": ["core"]
    }
  ]
}
```

#### Resolution Strategy:
1. **Explicit Tag Flag:** If `-Tags <tag>` is supplied, run those specific tags.
2. **Delta Mode (`-Delta`):**
   - Query changed files via `git diff --name-only origin/$BaseBranch...HEAD`.
   - Match changed files against `flow-mapping.json` glob patterns.
   - Aggregate matched tags and execute only the affected flows.
   - If no patterns match or mapping is absent, fallback safely to full baseline.
3. **Full Run:** If neither is specified, run all flows as a comprehensive baseline.

### Pillar 3: Deterministic Test Runner Script

Each target repository provides a standardized orchestration script
(`scripts/run-e2e-tests.ps1` or `scripts/run-e2e-tests.sh`) providing identical
behavior across local developer machines, Antigravity scheduled tasks, and CI
runners.

#### Key Capabilities:
- **Environment Bootstrap:** Automatically detects connected devices/browsers;
  boots an Android Virtual Device (AVD) or headless browser if none is active.
- **Build & Sideload:** Compiles the snapshot/debug artifact and installs it
  cleanly on the target environment.
- **Targeted Execution:** Executes flows based on resolved tags or delta diff.
- **Artifact Export:** Generates execution summary JSON (`summary.json`), HTML
  reports, and failure screenshots.

```powershell
# Usage Examples:
.\scripts\run-e2e-tests.ps1 -Delta
.\scripts\run-e2e-tests.ps1 -Tags "auth","logging"
.\scripts\run-e2e-tests.ps1 -CaptureArtifacts -Version "latest" -PushArtifacts
```

### Pillar 4: Visual Artifact Capture & External QA Storage

To prevent git repository bloat caused by committing binary screenshot runs and
video recordings, Graph Engineering decouples active code repositories from test
evidence storage:

1. **Local Visual Baseline:** A curated set of key UI screens is maintained in
   `docs/screenshots/` inside the repo and updated only when UI changes are
   intentional.
2. **Dedicated QA Evidence Repository (`<project>-qa`):**
   - e.g. `AntaresAndBharani/virgymia-qa` for `crosstrainingapp`.
   - Execution runs push HTML test reports and failure screenshots as GitHub
     Release assets tagged by branch or PR number (`e2e-pr-<pr_number>`).
   - Keeps the main code repository fast, clean, and lightweight while providing
     permanent, publicly accessible asset URLs.

### Pillar 5: Sticky PR Evidence Publishing (`post-e2e-evidence.ps1`)

Surfacing test evidence directly on the Pull Request enables the PO and PR
Review node to inspect results immediately without digging into raw CI logs.

#### Sticky In-Place Comment Pattern:
- The publishing script (`scripts/post-e2e-evidence.ps1`) queries existing PR
  comments for a unique HTML marker: `<!-- e2e-evidence -->`.
- **Update in place:** If a comment with the marker exists from the current user/bot,
  it updates the comment via `PATCH /repos/{owner}/{repo}/issues/comments/{id}`.
- **Create once:** If no marker is found, it posts a fresh comment via
  `POST /repos/{owner}/{repo}/issues/{id}/comments`.
- **Zero Noise:** Prevents spamming PR threads with duplicate comments across
  multiple iterative fix-up commits.

#### Markdown Evidence Table Format:

```markdown
<!-- e2e-evidence -->
### 🧪 E2E Test Evidence

| Flow | Status |
|---|---|
| 01_auth_flow.yaml | ✅ |
| 02_log_session_flow.yaml | ❌ [📷 Failure Screenshot](https://github.com/AntaresAndBharani/virgymia-qa/releases/download/e2e-pr-125/failure-02_log_session_flow.png) |
| 03_history_and_search_flow.yaml | ✅ |

**Status:** ❌ Some flows failed.

[🔗 View Full HTML Report & Screenshots](https://github.com/AntaresAndBharani/virgymia-qa/releases/download/e2e-pr-125/report.html)
```

---

## 3. Integration Across the Graph Engineering SDLC

```
[PO: User Story]
       |
       v
1. Architect Node
       | - Defines subtasks with E2E entry points & user journey expectations
       v
2. Three Amigos Node
       | - Evaluates QA testability; generates Given/When/Then BDD scenarios
       | - Identifies matching E2E flow tags (e.g. tags: ["logging"])
       v
3. Dev & Test Node
       | - Implements code changes
       | - Runs local unit tests (.\gradlew.bat testDebugUnitTest / pytest)
       | - Executes targeted delta E2E tests (.\scripts\run-e2e-tests.ps1 -Delta)
       | - Captures visual artifacts & syncs docs/screenshots/
       | - Posts sticky PR evidence (.\scripts\post-e2e-evidence.ps1)
       v
4. PR Review Node (Claude)
       | - Validates code diff AND inspects <!-- e2e-evidence --> PR comment
       | - Examines failure screenshots if regressions occur -> review:changes-requested
       | - Approves once both code quality & visual/functional evidence pass
       v
5. Merge & Backlog Node
```

### Node Specific Contracts:

1. **Three Amigos (Node 2):**
   - The QA analysis section must verify that acceptance criteria map to
     concrete Given/When/Then BDD scenarios.
   - When reviewing UI or user-facing subtasks, Three Amigos explicitly specifies
     which E2E flows/tags must be executed for validation.

2. **Dev & Test (Node 3):**
   - **Definition of Done Gate:** If UI or navigation files are modified, Dev &
     Test must execute delta E2E tests and update `docs/screenshots/` before
     opening or updating a PR.
   - Automatically publishes the sticky E2E evidence table to the PR.

3. **PR Review (Node 4):**
   - Inspects the PR comment thread for the `<!-- e2e-evidence -->` marker.
   - A failing E2E flow or missing evidence on UI changes is treated as a
     blocking review issue (`review:changes-requested`).

---

## 4. Cross-Stack Implementation Blueprints

| Stack | Declarative Framework | Mapping File | Runner Script | QA Storage |
|---|---|---|---|---|
| **Android (Kotlin/Compose)** | Maestro (`.yaml`) | `e2e/flow-mapping.json` | `scripts/run-e2e-tests.ps1` | `virgymia-qa` (GitHub Releases) |
| **Web (React/Next.js/HTML)** | Playwright (`.spec.ts`) | `e2e/flow-mapping.json` | `scripts/run-e2e-tests.ps1` | `<project>-qa` (HTML Traces & Reports) |
| **Python CLI / Backend** | Pester / Pytest CLI | `tests/flow-mapping.json` | `scripts/run-e2e-tests.ps1` | GitHub Actions Artifacts / QA Releases |

---

## 5. Implementation Lessons & Hardening (from `crosstrainingapp`)

Real-world production testing surfaced critical gotchas when automating E2E
testing with autonomous agents:

1. **Temp-Safe Body Files:** Writing PR comment bodies directly to the working tree
   (e.g. `body.txt`) pollutes git state and trips dirty working tree checks.
   **Solution:** Write comment markdown to the OS temporary directory
   (`[System.IO.Path]::GetTempPath()`) and ensure cleanup in a `finally` block.
2. **Non-Fatal Evidence Publishing:** Transient GitHub API rate limits or network
   hiccups while publishing comments must never fail the underlying build job or
   abort the agent turn.
   **Solution:** Wrap comment publishing in non-fatal try/catch handlers that log
   warnings instead of throwing fatal errors.
3. **Pre-Validate Remote Release Targets:** Linking to screenshot URLs on a QA repo
   before the release asset is actually uploaded creates broken 404 links on PRs.
   **Solution:** Verify release availability via `gh release view <version> --repo <qa-repo>`
   prior to rendering markdown image links.
4. **Resilient Delta Base Branch Detection:** `origin/$BaseBranch` might not be
   fetched on shallow checkouts.
   **Solution:** Check `git rev-parse --verify origin/$BaseBranch` with fallback to
   `$BaseBranch` or `HEAD~1`.
5. **AVD / Emulator Startup Timeout Handling:** Running headless emulators in
   automated loops can hang if the emulator process deadlocks.
   **Solution:** Use explicit wait timeouts (`adb wait-for-device`) with retry limits.
