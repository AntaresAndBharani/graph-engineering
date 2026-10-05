## 🎯 Final Decision Plan & User Story Specification

### 📖 User Story & Metadata
#### Title: feat(ui): TUI Dashboard Layout Rebalance & Project Filtering (Name & GitHub Organization)
* **Resolution Type:** Council Compromise Accepted (Round 3 Deliberation Consensus)
* **Sizing Pattern:** Pattern A (Standalone Task, $\le 4$ files, $\le 300$ LOC diff)
* **Target Files:**
  - `orchestrator/ui/dashboard.py`
  - `orchestrator/ui/widgets.py`
  - `tests/test_dashboard.py`
  - `tests/test_widgets.py`

**As an** operator managing multi-repository engineering pipelines across multiple GitHub organizations (`BasketIQ`, `AntaresAndBharani`, `Antares1980`),  
**I want** an inline project name filter, an organization cycling selector, and a 70/30 layout rebalance in the TUI dashboard,  
**So that** I can immediately locate target repositories, inspect active nodes, and observe logs without manual vertical scrolling, row truncation, or keybinding collisions.

---

### 🏛️ Architecture & Component Impact

```mermaid
flowchart TD
    subgraph DashboardApp ["DashboardApp (orchestrator/ui/dashboard.py)"]
        State["Reactive State: filter_text (str), selected_org (Optional[str])"]
        InputWidget["Filter Bar: Input#filter_name & Static#filter_chip"]
        KeyRouter["check_action() / Input Isolation: Suppress 'q', 'r', 'space' when Input focused"]
        PollLoop["update_projects_table() & _rebind_config()"]
        DataTable["#projects_table (DataTable) [height: 1fr; min-height: 12]"]
    end

    subgraph WidgetsModule ["orchestrator/ui/widgets.py"]
        OrgParser["extract_github_org(slug) -> str"]
        FilterHelper["filter_projects(projects, filter_text, selected_org) -> list[ProjectConfig]"]
    end

    subgraph BottomContainer ["#bottom_container [height: 30%; layout: horizontal]"]
        SDLC["SDLCProgressWidget (#sdlc_widget)"]
        Tabs["TabbedContent (#tabs) -> Logs, Quotas, Alerts"]
    end

    InputWidget -->|Input.Changed| State
    State --> PollLoop
    PollLoop --> FilterHelper
    FilterHelper --> OrgParser
    FilterHelper -->|Visible Projects| DataTable
    DataTable -->|on_project_row_highlighted| BottomContainer
    KeyRouter -.->|Shield| InputWidget
```

#### 1. Layout Rebalance (Textual CSS)
- In `orchestrator/ui/dashboard.py` (`DashboardApp.CSS`):
  - `#bottom_container { height: 30%; layout: horizontal; }` (rebalanced from `60%`).
  - `#projects_table { height: 1fr; min-height: 12; border: solid green; }`.
  - Add `#filter_bar { height: 3; layout: horizontal; padding: 0 1; }`.
  - Add `#filter_input { width: 40; }`.
  - Add `#filter_chip { width: 1fr; content-align: right middle; color: $text-muted; }`.

#### 2. Keybinding Isolation & Safety
- **Problem Solved:** `Binding("q", "quit", "Quit", priority=True)` previously intercepted `q` before child widgets, causing queries like `biq` to initiate daemon shutdown.
- **Contract:** Override `DashboardApp.check_action(action: str, parameters: tuple[object, ...]) -> bool | None`. If `action in ("quit", "refresh", "toggle_auto_scroll")` and `self.focused and isinstance(self.focused, Input)`: return `False`.
- **Keyboard Triggers:**
  - `/`: Focuses `#filter_input`.
  - `o`: Cycles `selected_org` through `[None, *sorted(unique_orgs, key=str.lower)]`. `None` renders as `[All Orgs]`.
  - `Esc`: If `#filter_input` is focused or filter active, clears `filter_text`, resets `selected_org = None`, blurs input, and restores focus to `#projects_table`.

#### 3. Pure Helpers in `orchestrator/ui/widgets.py`
- `extract_github_org(repository_slug: str) -> str`:
  - Splits `repository_slug.strip()` on `/` (limit 1). If valid prefix exists and no path separators like Windows drive letters, returns the trimmed organization name. Otherwise returns `"Unknown"`.
- `filter_projects(projects: list[ProjectConfig], filter_text: str = "", selected_org: Optional[str] = None) -> list[ProjectConfig]`:
  - Pure function, case-insensitive.
  - Matches `filter_text.strip().lower()` strictly against `p.name.lower()`.
  - Matches `selected_org` against `extract_github_org(p.repo).lower()`.
  - Composes both with logical `AND`.

#### 4. Selection & 0-Match State Machine
- Integrated inside `DashboardApp.update_projects_table()`:
  - Calls `visible_projects = filter_projects(self.config.projects, self.filter_text, self.selected_org)`.
  - Counter chip displays: `[Filter: '<filter_text>' | Org: <OrgName>] (Matched: <visible_count> of <total_count>)`.
  - Child pipeline rows (`proj::devtest` / `└─`) follow parent project visibility and are excluded from distinct match counts.
  - If `visible_count > 0`:
    - If `self.selected_project` not in visible set: re-index selection to `visible_projects[0].name` and trigger `hydrate_project_logs(...)`.
  - If `visible_count == 0`:
    - Set `self.selected_project = None`.
    - Clear table cursor repositioning without raising `IndexError`.
    - Bottom detail panes render empty placeholder: `[dim]No projects match active filter[/dim]`.

---

### 🧪 Given-When-Then BDD Acceptance Criteria

#### Deterministic 11-Project Test Fixture
All scenarios evaluate against an 11-project test fixture:
- 5 `BasketIQ` projects: `biq-home`, `biq-knowledge`, `biq-onboard`, `biq-training`, `biq-playbook`.
- 5 `AntaresAndBharani` projects: `crosstrainingapp`, `darwin-trader`, `graph-engineering`, `kerberito`, `retro-fighter-classic`.
- 1 `Antares1980` project: `retro-fighter`.

#### Scenario 1: Vertical Layout Proportions
- **Given** `DashboardApp` mounted in a test terminal of size $(120, 35)$ with 11 projects loaded,
- **When** the initial composition and render pass completes,
- **Then** `#projects_table` allocates at least 15 visible rows without row truncation,
- **And** `#bottom_container` occupies exactly 30% of vertical height ($\le 35\%$).

#### Scenario 2: GitHub Organization Filter Cycling
- **Given** the 11-project dashboard with `selected_org = None` (displaying `[All Orgs]`),
- **When** the operator presses `o` once,
- **Then** `selected_org` transitions to `Antares1980`, displaying 1 project (`retro-fighter`),
- **When** the operator presses `o` again,
- **Then** `selected_org` transitions to `AntaresAndBharani`, displaying 5 projects,
- **When** the operator presses `o` again,
- **Then** `selected_org` transitions to `BasketIQ`, displaying 5 projects (`biq-*`),
- **And** the filter chip displays `(Matched: 5 of 11)`.

#### Scenario 3: Name Substring Search & Priority Key Isolation
- **Given** the dashboard displaying all 11 projects,
- **When** the operator presses `/` to focus `#filter_input`,
- **And** types `"biq rq"`,
- **Then** `#filter_input.value` equals `"biq rq"`,
- **And** the application does NOT quit, drain, reload, or toggle auto-scroll,
- **And** only projects matching substring `"biq rq"` in `project.name` remain visible.

#### Scenario 4: Compound Filtering & Escape Reset
- **Given** `selected_org` is set to `AntaresAndBharani` and `filter_text` is `"cross"`,
- **When** the filter evaluates,
- **Then** only `crosstrainingapp` is visible and highlighted,
- **When** the operator presses `Esc`,
- **Then** `filter_text` is cleared to `""`, `selected_org` is reset to `None`,
- **And** all 11 projects are immediately restored to `#projects_table`,
- **And** focus returns to `#projects_table`.

#### Scenario 5: Zero-Match Safety & Placeholder Hydration
- **Given** the active dashboard,
- **When** `#filter_input` receives a search query with 0 matches (e.g., `"nonexistent-xyz"`),
- **Then** `#projects_table` displays 0 rows without raising `IndexError`,
- **And** `self.selected_project` is set to `None`,
- **And** `#sdlc_widget` and `#log_view` display empty-state placeholders `[dim]No projects match active filter[/dim]`.

#### Scenario 6: State Persistence Across 2-Second Polling & Config Hot-Reload
- **Given** an active filter (`filter_text="biq"`, `selected_org="BasketIQ"`),
- **When** the 2-second background timer executes `update_projects_table()`,
- **Or** `_rebind_config()` executes during a hot-reload,
- **Then** `filter_text` and `selected_org` persist unchanged,
- **And** the filtered view and cursor selection remain intact without DOM flickering.

---

### 📦 Task Breakdown (Pattern A: Standalone Task)

* **[x] Task 1 (Pure Helpers):** Implement `extract_github_org` and `filter_projects` in `orchestrator/ui/widgets.py` with comprehensive unit tests in `tests/test_widgets.py`.
* **[x] Task 2 (Layout & Controls):** Add `#filter_bar`, `#filter_input`, and `#filter_chip` to `DashboardApp.compose()`, update CSS to 70/30 height distribution (`#bottom_container { height: 30%; }`).
* **[x] Task 3 (Key Handling & Focus):** Implement `check_action()` App-level binding suppression for `q`, `r`, `space` while `#filter_input` is focused. Wire `/`, `o`, and `Esc` bindings.
* **[x] Task 4 (Reactive Polling Integration):** Integrate `filter_projects` into `update_projects_table()` and `_rebind_config()`, handle 0-match empty states, and hydrate detail panes safely.
* **[x] Task 5 (Test Suite Verification):** Add asynchronous Textual `App.run_test()` scenarios in `tests/test_dashboard.py` validating all 6 BDD scenarios and regression safety.