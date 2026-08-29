# Consistency Supervisor Node (`node-supervisor`)

**Module**: [`orchestrator/nodes/supervisor.py`](file:///c:/Users/rogal/workspaces/ws-setups/graph-engineering/orchestrator/nodes/supervisor.py)

The **Consistency Supervisor** acts as Node 0 in the pipeline—an autonomous watchdog and proactive AI Product Owner (PO) Proxy responsible for repository health verification, taxonomy validation, 12-hour SLA enforcement, and automated requirement readiness evaluation.

---

## 🏛️ Operational Flow

```mermaid
flowchart TD
    Start["Supervisor Execution Cycle"] --> PO["PO-Proxy Review on 'needs-po-review' Issues"]
    
    subgraph PO_Evaluation ["PO-Proxy Issue Evaluation & Hash Gating"]
        PO --> HASH{"SHA-256 Hash Changed?"}
        HASH -->|No & status=NEEDS_HUMAN_CLARIFICATION| SKIP["Zero-Token Skip Gate (0 Tokens)"]
        HASH -->|Yes or Force| DISPATCH["Dispatch AI PO Evaluation (gemini-3.7-flash-low)"]
        
        DISPATCH --> VERDICT{"Requirements Complete?"}
        VERDICT -->|Complete (PO_APPROVED)| PROMOTE["Enrich with Gherkin AC + Swap label to 'needs-triage' + Blackboard PO_APPROVED"]
        VERDICT -->|Ambiguous / Gaps| ESCALATE["Post Clarifying Comment + Retain 'needs-po-review' + Blackboard NEEDS_HUMAN_CLARIFICATION"]
    end

    PO_Evaluation --> Q["Zero-Token Anomaly Audit (GitHub CLI)"]
    
    Q --> C1{"Open PR Merge Conflicts?"}
    C1 -->|Yes| A1["Flag PR with 'needs-po-review' + Post Warning Comment"]
    
    Q --> C2{"Failed Jobs in state.db?"}
    C2 -->|Yes| A2["Log Failed Job Anomaly"]
    
    Q --> C3{"Unclassified Issues (Missing Managed Label)?"}
    C3 -->|Yes| A3["Assign 'needs-triage' for Architect review + Notify via Comment"]
    
    Q --> C4{"Active Issues Open > 12h (Excl. tech-debt / enhancement)?"}
    C4 -->|Yes| A4["Escalate with 'needs-po-review' + Post 12h SLA Alert"]
    
    C1 & C2 & C3 & C4 -->|0 Anomalies Found| Idle["Exit with 0 Tokens Consumed (Consistent State)"]
```

---

## 🔑 Core Capabilities

### 1. Proactive AI Product Owner (PO) Proxy & Zero-Token Hash Gating
- Evaluates user stories and issues labeled **`needs-po-review`** for functional completeness, acceptance criteria, and INVEST adherence.
- **SHA-256 Body Hash Gating**: Computes `hash(title + "\n" + body)`. If an issue was previously evaluated and marked `NEEDS_HUMAN_CLARIFICATION`, and its hash has not changed, the Supervisor skips evaluation with **0 LLM tokens consumed**.
- **Automated Gherkin Enrichment & Promotion**: When requirements are complete, generates Given/When/Then Acceptance Criteria, updates the issue body, transitions the label from `needs-po-review` to `needs-triage`, and records `PO_APPROVED` on the Blackboard.
- **Human Clarification Loop Guarding**: When requirements are incomplete or ambiguous, posts a structured clarifying comment, retains `needs-po-review`, and records `NEEDS_HUMAN_CLARIFICATION` on the Blackboard to prevent re-evaluation loops until edited by a human.

### 2. Deterministic Zero-Token Audits
- Queries GitHub metadata for open PRs and issues via `gh` CLI JSON format with 0 LLM tokens.

### 3. Issue Status & Taxonomy Validation
- Validates that every open issue carries a valid managed label (`needs-triage`, `ready-for-dev`, `dev-implemented`, `architect-processed`, `needs-po-review`, `orchestration-failed`, `tech-debt`, `enhancement`).
- Unclassified issues are automatically assigned **`needs-triage`** so the **Architect Node** can triage and classify them.

### 4. 12-Hour Stale Issue SLA Monitoring
- Evaluates issue age based on `createdAt`.
- **Excludes** `tech-debt` and `enhancement` issues (which are safely queued for the daily BAU Node).
- If an active workflow item remains open for **longer than 12 hours**, flags it with **`needs-po-review`** and posts a diagnostic warning comment.

### 5. PR Merge Conflict Detection
- Identifies `CONFLICTING` pull requests and attaches `needs-po-review` to prevent review node deadlock.

---

## 💾 Decoupled Blackboard Schema (`po_tracking`)

The PO-proxy state is persisted in SQLite WAL:

```sql
CREATE TABLE IF NOT EXISTS po_tracking (
    repo TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    body_hash TEXT NOT NULL,
    status TEXT NOT NULL,         -- 'PO_APPROVED' | 'NEEDS_HUMAN_CLARIFICATION'
    gherkin_ac TEXT,              -- Generated Gherkin acceptance criteria
    blockers TEXT,                -- Detected gaps / clarifying questions
    updated_at REAL NOT NULL,
    PRIMARY KEY (repo, issue_number)
);
```

---

## 🖥️ CLI Commands

### 1. Supervisor Evaluate (`--dry-run` and Live)
Evaluates an issue's functional completeness and renders a Rich-formatted diagnostic panel with the verdict, detected gaps, and generated Gherkin AC.

```bash
# Dry-run inspection without mutating GitHub:
orchestrator supervisor evaluate <issue_id> -p <project> --dry-run

# Live evaluation and promotion:
orchestrator supervisor evaluate <issue_id> -p <project>
```

### 2. Supervisor Status
Displays a formatted Rich table of all tracked issues in the `po_tracking` Blackboard table.

```bash
orchestrator supervisor status [-p <project>]
```

---

## ⚙️ Configuration

Configured under `nodes.supervisor` in `~/.config/orchestrator/config.yaml`:

```yaml
projects:
  - name: "crosstrainingapp"
    repo: "AntaresAndBharani/crosstrainingapp"
    nodes:
      supervisor:
        enabled: true
        harness: "antigravity"
        model: "gemini-3.7-flash-low"
```

