# Consistency Supervisor Node (`node-supervisor`)

**Module**: [`orchestrator/nodes/supervisor.py`](file:///c:/Users/rogal/workspaces/ws-setups/graph-engineering/orchestrator/nodes/supervisor.py)

The **Consistency Supervisor** acts as Node 0 in the pipeline—an autonomous watchdog responsible for repository health verification, taxonomy validation, and SLA enforcement.

---

## 🏛️ Operational Flow

```mermaid
flowchart TD
    Start["Periodic Run (Every 3600s / 1h)"] --> Q["Zero-Token Anomaly Audit (GitHub CLI)"]
    
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

1. **Deterministic Zero-Token Audits**:
   - Queries GitHub metadata for open PRs and issues via `gh` CLI JSON format.
   - Evaluates rules purely deterministically in Python with zero LLM token consumption.

2. **Issue Status & Taxonomy Validation**:
   - Validates that every open issue carries a valid managed label (`needs-triage`, `ready-for-dev`, `dev-implemented`, `architect-processed`, `needs-po-review`, `orchestration-failed`, `tech-debt`, `enhancement`).
   - If an unclassified issue is opened without labels, the Supervisor automatically assigns **`needs-triage`** so the **Architect Node** can triage and classify it.

3. **12-Hour Stale Issue SLA Monitoring**:
   - Evaluates issue age based on `createdAt`.
   - **Excludes** `tech-debt` and `enhancement` issues (which are safely queued for the daily BAU Node).
   - If an active workflow item remains open for **longer than 12 hours**, the Supervisor flags it with **`needs-po-review`** and posts a diagnostic warning comment.

4. **PR Merge Conflict Detection**:
   - Checks `mergeable` status of all open pull requests.
   - If a PR encounters git merge conflicts (`CONFLICTING`), it attaches `needs-po-review` to prevent review node deadlock.

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
