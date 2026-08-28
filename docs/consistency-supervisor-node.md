# Consistency Supervisor Node (`run_supervisor_node`)

The **Consistency Supervisor Node** is the autonomous watchdog of the Graph Engineering pipeline. It continuously audits repository health, detects methodology anomalies, and monitors issue SLA compliance.

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
    C3 -->|Yes| A3["Assign 'needs-triage' + Notify Architect via Comment"]
    
    Q --> C4{"Active Issues Open > 12h (Excl. tech-debt / enhancement)?"}
    C4 -->|Yes| A4["Escalate with 'needs-po-review' + Post 12h SLA Alert"]
    
    C1 & C2 & C3 & C4 -->|0 Anomalies Found| Idle["Exit with 0 Tokens Consumed (Consistent State)"]
```

---

## 🔑 Core Responsibilities

1. **Deterministic Zero-Token Audits**:
   - Queries GitHub metadata for open PRs and issues using `gh` CLI JSON format.
   - Evaluates rules purely deterministically in Python without invoking any LLM unless complex synthesis is requested.

2. **Issue Status & Taxonomy Validation**:
   - Ensures all open issues carry a valid managed workflow label (`needs-triage`, `ready-for-dev`, `dev-implemented`, `architect-processed`, `needs-po-review`, `orchestration-failed`, `tech-debt`, `enhancement`).
   - If an unclassified issue is opened without labels, the Supervisor automatically assigns **`needs-triage`** so the **Architect Node** can triage and classify it.

3. **12-Hour Stale Issue SLA Monitoring**:
   - Calculates the age of all active issues from `createdAt`.
   - **Excludes** `tech-debt` and `enhancement` issues (which are safely queued for the daily BAU Node).
   - If an active workflow item remains open for **longer than 12 hours**, the Supervisor flags it with **`needs-po-review`** and posts a diagnostic warning comment.

4. **PR Merge Conflict Detection**:
   - Checks the `mergeable` status of all open pull requests.
   - If a PR encounters git merge conflicts (`CONFLICTING`), it attaches `needs-po-review` to prevent review node deadlock.

---

## ⚙️ Configuration Example

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
