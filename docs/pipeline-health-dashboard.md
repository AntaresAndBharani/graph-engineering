# Graph Engineering - Pipeline Health Dashboard

**Last Audit Run:** `2026-08-27 08:16:30`  
**Overall System Health (Active 4h Window):** **CRITICAL / ACTION REQUIRED**

---

## Executive Overview

| Project | Target Repository | Health (4h) | Active Tasks | Log Errors (4h) | Log Errors (24h) | Recent CI Failures |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **crosstrainingapp** | [AntaresAndBharani/crosstrainingapp](https://github.com/AntaresAndBharani/crosstrainingapp) | CRITICAL | 4 | **2** | 46 |  |
| **darwin-trader** | [AntaresAndBharani/darwin-trader](https://github.com/AntaresAndBharani/darwin-trader) | CRITICAL | 4 | **14** | 90 |  |

---

## Active Issues & Remediation Action Items (Last 4 Hours)

The supervisor detected the following active issues requiring attention within the last 4 hours:

| Severity | Project / Component | Issue Description (Last 4h) | Recommended Action |
| :---: | :--- | :--- | :--- |
| CRITICAL | `CTA-BacklogTriage` | Scheduled task CTA-BacklogTriage exited with error code 1 on 08/27/2026 07:52:14 | `Inspect C:\Users\rogal\workspaces\ws-gym\crosstrainingapp\logs\local-pipeline\ logs for exact unhandled exception` |
| CRITICAL | `crosstrainingapp/backlog-triage-2026-08-27.log` | Log error in backlog-triage-2026-08-27.log: [2026-08-27 07:52:17] [INFO] git: fatal: Unable to create 'C:/Users/rogal/workspaces/ws-gym/crosstrainingapp/.git/index.lock': File exists. | `Check full trace in C:\Users\rogal\workspaces\ws-gym\crosstrainingapp\logs\local-pipeline\backlog-triage-2026-08-27.log` |
| CRITICAL | `crosstrainingapp/backlog-triage-2026-08-27.log` | Log error in backlog-triage-2026-08-27.log: [2026-08-27 07:52:17] [ERROR] Unhandled error in backlog triage run: git checkout main failed with exit code 128 | `Check full trace in C:\Users\rogal\workspaces\ws-gym\crosstrainingapp\logs\local-pipeline\backlog-triage-2026-08-27.log` |
| CRITICAL | `DT-BacklogTriage` | Scheduled task DT-BacklogTriage exited with error code 1 on 08/27/2026 07:52:14 | `Inspect C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\ logs for exact unhandled exception` |
| CRITICAL | `darwin-trader/pr-review-2026-08-27.log` | Log error in pr-review-2026-08-27.log: [2026-08-27 08:07:29] [ERROR] claude.exe invocation threw for PR #14: Excepción al llamar a "Start" con los argumentos "1": "El nombre del archivo o la extensión es demasiado largo" | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\pr-review-2026-08-27.log` |
| CRITICAL | `darwin-trader/pr-review-2026-08-27.log` | Log error in pr-review-2026-08-27.log: [2026-08-27 08:12:25] [ERROR] claude.exe invocation threw for PR #15: Excepción al llamar a "Start" con los argumentos "1": "El nombre del archivo o la extensión es demasiado largo" | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\pr-review-2026-08-27.log` |
| CRITICAL | `darwin-trader/pr-review-2026-08-27.log` | Log error in pr-review-2026-08-27.log: [2026-08-27 08:12:28] [ERROR] claude.exe invocation threw for PR #14: Excepción al llamar a "Start" con los argumentos "1": "El nombre del archivo o la extensión es demasiado largo" | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\pr-review-2026-08-27.log` |
| CRITICAL | `darwin-trader/three-amigos-and-dev-test-2026-08-27.log` | Log error in three-amigos-and-dev-test-2026-08-27.log: [2026-08-27 07:57:19] [INFO] git: fatal: Unable to create 'C:/Users/rogal/workspaces/ws-trading/darwin-trader/.git/index.lock': File exists. | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\three-amigos-and-dev-test-2026-08-27.log` |
| CRITICAL | `darwin-trader/three-amigos-and-dev-test-2026-08-27.log` | Log error in three-amigos-and-dev-test-2026-08-27.log: [2026-08-27 07:57:19] [ERROR] Unhandled error in three-amigos-and-dev-test run: git reset --hard origin/main failed with exit code 128 | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\three-amigos-and-dev-test-2026-08-27.log` |
| WARNING | `darwin-trader/pr-review-2026-08-27.log` | Warning in pr-review-2026-08-27.log: [2026-08-27 08:12:25] [WARN] Judge failed to produce a usable verdict for PR #15; leaving it unreviewed for manual triage. | Review logs for degradation |
| WARNING | `darwin-trader/pr-review-2026-08-27.log` | Warning in pr-review-2026-08-27.log: [2026-08-27 08:12:28] [WARN] Judge failed to produce a usable verdict for PR #14; leaving it unreviewed for manual triage. | Review logs for degradation |
| WARNING | `darwin-trader/architect-2026-08-27.log` | Warning in architect-2026-08-27.log: [2026-08-27 07:57:22] [WARN] gh api sub_issues threw for story #16: function not defined: open/0 | Review logs for degradation |

---

## Project: crosstrainingapp (AntaresAndBharani/crosstrainingapp)

### Windows Task Scheduler Execution Matrix
| Task Name | State | Last Run Time | Next Run Time | Last Exit Result | Status |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `CTA-Architect` | Ready | 2026-08-27 08:15:20 | 2026-08-27 08:20:19 | `0` | HEALTHY (0) |
| `CTA-BacklogTriage` | Ready | 2026-08-27 07:52:14 | 2026-08-27 09:47:16 | `1` | FAILED (1) |
| `CTA-PRReview` | Ready | 2026-08-27 08:13:28 | 2026-08-27 08:18:27 | `0` | HEALTHY (0) |
| `CTA-ThreeAmigosDevTest` | Ready | 2026-08-27 08:13:28 | 2026-08-27 08:28:27 | `0` | HEALTHY (0) |

### Local Pipeline Daily Logs (Errors: 4h vs 24h)
| Log File | Last Modified | Size | Errors (4h) | Errors (24h) | Warnings (4h) | Latest 4h Error Snippet |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `architect-2026-08-27.log` | 2026-08-27 08:15:24 | 22.7 KB | **0** | 0 | 0 | None |
| `three-amigos-and-dev-test-2026-08-27.log` | 2026-08-27 08:13:35 | 7.7 KB | **0** | 0 | 0 | None |
| `pr-review-2026-08-27.log` | 2026-08-27 08:13:31 | 25.5 KB | **0** | 0 | 0 | None |
| `backlog-triage-2026-08-27.log` | 2026-08-27 07:52:17 | 0.9 KB | **2** | 2 | 0 | `[2026-08-27 07:52:17] [ERROR] Unhandled error in backlog triage run: git checkou`... |
| `three-amigos-and-dev-test-2026-08-26.log` | 2026-08-26 23:58:49 | 51.1 KB | **0** | 26 | 0 | None |
| `pr-review-2026-08-26.log` | 2026-08-26 23:58:31 | 145.7 KB | **0** | 5 | 0 | None |
| `architect-2026-08-26.log` | 2026-08-26 23:55:23 | 191.1 KB | **0** | 4 | 0 | None |
| `backlog-triage-2026-08-26.log` | 2026-08-26 21:47:59 | 32.3 KB | **0** | 9 | 0 | None |

### Recent GitHub Actions CI Runs
| Run ID | Workflow Name | Head Branch | Status | Updated At |
| :--- | :--- | :--- | :---: | :---: |
| [33043926619](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33043926619) | Release on Merge to Main | `main` | SUCCESS | 2026-08-27T05:54:16Z |
| [33043916158](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33043916158) | Merge & Backlog | `feat/issue-367` | SUCCESS | 2026-08-27T05:53:08Z |
| [33043610015](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33043610015) | PR Snapshot Build & Pre-Release | `feat/issue-367` | SUCCESS | 2026-08-27T05:49:50Z |
| [33019857132](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33019857132) | Merge & Backlog | `feat/issue-327` | FAILURE | 2026-08-26T22:29:23Z |
| [33019847793](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33019847793) | PR Snapshot Build & Pre-Release | `feat/issue-327` | SUCCESS | 2026-08-26T22:31:31Z |
| [33019787047](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33019787047) | PR Snapshot Build & Pre-Release | `feat/issue-327` | SUCCESS | 2026-08-26T22:30:59Z |
| [33019258153](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33019258153) | Release on Merge to Main | `main` | SUCCESS | 2026-08-26T22:20:44Z |
| [33019247089](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33019247089) | Merge & Backlog | `feat/issue-326` | SUCCESS | 2026-08-26T22:19:38Z |

---

## Project: darwin-trader (AntaresAndBharani/darwin-trader)

### Windows Task Scheduler Execution Matrix
| Task Name | State | Last Run Time | Next Run Time | Last Exit Result | Status |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `DT-Architect` | Ready | 2026-08-27 08:12:17 | 2026-08-27 08:17:16 | `0` | HEALTHY (0) |
| `DT-BacklogTriage` | Ready | 2026-08-27 07:52:14 | 2026-08-27 08:42:16 | `1` | FAILED (1) |
| `DT-PRReview` | Ready | 2026-08-27 08:12:17 | 2026-08-27 08:17:16 | `0` | HEALTHY (0) |
| `DT-ThreeAmigosDevTest` | Ready | 2026-08-27 08:12:17 | 2026-08-27 08:27:16 | `0` | HEALTHY (0) |

### Local Pipeline Daily Logs (Errors: 4h vs 24h)
| Log File | Last Modified | Size | Errors (4h) | Errors (24h) | Warnings (4h) | Latest 4h Error Snippet |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `pr-review-2026-08-27.log` | 2026-08-27 08:12:28 | 28.1 KB | **12** | 32 | 10 | `[2026-08-27 08:12:28] [ERROR] claude.exe invocation threw for PR #14: Excepción `... |
| `three-amigos-and-dev-test-2026-08-27.log` | 2026-08-27 08:12:22 | 8.1 KB | **2** | 2 | 0 | `[2026-08-27 07:57:19] [ERROR] Unhandled error in three-amigos-and-dev-test run: `... |
| `architect-2026-08-27.log` | 2026-08-27 08:12:21 | 13.1 KB | **0** | 0 | 1 | None |
| `backlog-triage-2026-08-27.log` | 2026-08-27 07:52:43 | 1.1 KB | **0** | 0 | 0 | None |
| `pr-review-2026-08-26.log` | 2026-08-26 23:57:27 | 59.2 KB | **0** | 48 | 0 | None |
| `architect-2026-08-26.log` | 2026-08-26 23:57:21 | 30 KB | **0** | 4 | 0 | None |
| `three-amigos-and-dev-test-2026-08-26.log` | 2026-08-26 23:57:19 | 16.5 KB | **0** | 4 | 0 | None |
| `backlog-triage-2026-08-26.log` | 2026-08-26 20:42:19 | 1.7 KB | **0** | 0 | 0 | None |

### Recent GitHub Actions CI Runs
| Run ID | Workflow Name | Head Branch | Status | Updated At |
| :--- | :--- | :--- | :---: | :---: |
| [33009254458](https://github.com/AntaresAndBharani/darwin-trader/actions/runs/33009254458) | Build APK & CI | `feat/issue-7` | SUCCESS | 2026-08-26T20:15:01Z |
| [33009244769](https://github.com/AntaresAndBharani/darwin-trader/actions/runs/33009244769) | Build APK & CI | `feat/issue-7` | SUCCESS | 2026-08-26T20:15:05Z |
| [33007534233](https://github.com/AntaresAndBharani/darwin-trader/actions/runs/33007534233) | Build APK & CI | `feat/issue-6` | SUCCESS | 2026-08-26T19:55:19Z |
| [33007519951](https://github.com/AntaresAndBharani/darwin-trader/actions/runs/33007519951) | Build APK & CI | `feat/issue-6` | SUCCESS | 2026-08-26T19:55:10Z |
| [33007109029](https://github.com/AntaresAndBharani/darwin-trader/actions/runs/33007109029) | Merge & Backlog | `feat/issue-5` | FAILURE | 2026-08-26T19:48:41Z |
| [33006861929](https://github.com/AntaresAndBharani/darwin-trader/actions/runs/33006861929) | Build APK & CI | `feat/issue-5` | SUCCESS | 2026-08-26T19:47:57Z |
| [33006843234](https://github.com/AntaresAndBharani/darwin-trader/actions/runs/33006843234) | Build APK & CI | `feat/issue-5` | SUCCESS | 2026-08-26T19:47:34Z |
| [33006401448](https://github.com/AntaresAndBharani/darwin-trader/actions/runs/33006401448) | Build APK & CI | `main` | SUCCESS | 2026-08-26T19:42:34Z |

---

*Generated deterministically by Consistency Supervisor Node (scripts/run-consistency-supervisor.ps1).*

