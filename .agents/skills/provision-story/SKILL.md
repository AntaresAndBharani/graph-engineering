---
name: provision-story
description: Deterministically provisions approved requirements from docs/draft-requisites/implementation-plan.md into GitHub issues and synchronizes SQLite state.db with zero runtime token consumption. Strictly adheres to Pattern A (Standalone Task) and Pattern B (Decomposed Feature) and enforces the Single Active Feature Invariant.
---

# Deterministic Story Provisioning Protocol (`/provision-story`)

Use this skill whenever user stories, requirements, or implementation plans have achieved consensus approval (via `/user-story-refining` or `/agy-architect-review`) and are ready to be provisioned directly into GitHub and SQLite `state.db`.

## 🏛️ Core Principles

1. **Deterministic Execution (0 LLM Tokens):**
   - Issue generation and state synchronization are driven by the deterministic Python command `orchestrator story provision`.
   - Bypasses runtime LLM triage and manual GitHub issue creation.
2. **Strict Invariant 1 (Single Active Feature Invariant):**
   - Only **one active feature parent story** (`architect-processed`) may be provisioned per project at a time.
   - If an active parent story is already locked in `sdlc_items` for the target project, `orchestrator story provision` fails closed with exit code 1 to protect queue determinism, unless `--force` is specified.
3. **Pattern Selection:**
   - **Pattern A (Standalone Task, $\le 300$ LOC, $\le 4$ files):** Single issue labeled `ready-for-dev` directly (no parent, no child). DevTest executes directly via Fallback 1.
   - **Pattern B (Decomposed Feature Story, $> 300$ LOC):**
     - Parent feature issue labeled `architect-processed` with markdown checklist `- [ ] #<child_id>` in the body.
     - Triple-redundant child linkage comment (`Child issues: #101, #102`) on the parent to defend against GitHub API search-lag.
     - Child Slice 1 labeled `ready-for-dev` with `Parent: #<parent_id>` in body.
     - Child Slices 2..N labeled `queued` with `Parent: #<parent_id>` in body.

---

## 🚀 Execution Workflow

### Step 1: Pre-Flight Dry Run
Run the provisioning command in dry-run mode to inspect the parsed user story, detected pattern, and planned issue structure:

```powershell
python -m orchestrator.cli story provision <project_name> --dry-run
```

If the plan file is in a custom path, supply `--file <path>`:
```powershell
python -m orchestrator.cli story provision <project_name> --file docs/draft-requisites/my-plan.md --dry-run
```

### Step 2: Authenticate GitHub CLI
Before executing live provisioning, ensure GitHub CLI authentication is loaded:

```powershell
C:\Users\rogal\workspaces\Set-GhToken-Antares.ps1
```

### Step 3: Execute Live Provisioning
Run the live command:

```powershell
python -m orchestrator.cli story provision <project_name>
```

Optional flags:
- `--pattern A` or `--pattern B`: Force a specific pattern override regardless of slice count.
- `--force`: Bypass the active story lock check (use only if explicitly instructed).
- `--config <path>`: Custom configuration file path.

### Step 4: Verify Blackboard Synchronization
Upon successful completion:
1. Review the output summary table showing created issue IDs, titles, and labels.
2. Inspect the SQLite blackboard to verify zero-latency synchronization:
```powershell
python -c "
import asyncio
from orchestrator.config import load_config
from orchestrator.db import StateManager

async def check():
    cfg = load_config()
    sm = StateManager(cfg.settings.resolved_db_path)
    items = await sm.get_sdlc_items('<project_name>')
    for it in items:
        print(f'#{it[\"issue_number\"]} | {it[\"item_type\"]} | {it[\"state\"]} | {it[\"labels\"]} | {it[\"title\"][:50]}')

asyncio.run(check())
"
```
3. The DevTest node daemon will autonomously pick up Slice 1 (`ready-for-dev`) on its next cycle pass.
