# Graph Engineering - Pipeline Health Dashboard

**Last Audit Run:** `2026-08-27 16:27:57`  
**Overall System Health (Active 4h Window):** **CRITICAL / ACTION REQUIRED**

---

## Executive Overview

| Project | Target Repository | Health (4h) | Active Tasks | Log Errors (4h) | Log Errors (24h) | Recent CI Failures |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **crosstrainingapp** | [AntaresAndBharani/crosstrainingapp](https://github.com/AntaresAndBharani/crosstrainingapp) | CRITICAL | 4 | **4** | 40 | 3 |
| **darwin-trader** | [AntaresAndBharani/darwin-trader](https://github.com/AntaresAndBharani/darwin-trader) | CRITICAL | 4 | **55** | 210 | 0 |
| **gh-development-dashboard** | [AntaresAndBharani/gh-development-dashboard](https://github.com/AntaresAndBharani/gh-development-dashboard) | CRITICAL | 4 | **39** | 43 |  |

---

## Active Issues & Remediation Action Items (Last 4 Hours)

The supervisor detected the following active issues requiring attention within the last 4 hours:

| Severity | Project / Component | Issue Description (Last 4h) | Recommended Action |
| :---: | :--- | :--- | :--- |
| CRITICAL | `CTA-ThreeAmigosDevTest` | Scheduled task CTA-ThreeAmigosDevTest exited with error code 1 on 08/27/2026 16:13:28 | `Inspect C:\Users\rogal\workspaces\ws-gym\crosstrainingapp\logs\local-pipeline\ logs for exact unhandled exception` |
| CRITICAL | `crosstrainingapp/three-amigos-and-dev-test-2026-08-27.log` | Log error in three-amigos-and-dev-test-2026-08-27.log: [2026-08-27 12:45:28] [ERROR] Unhandled error in three-amigos-and-dev-test run: git fetch origin failed with exit code 128 | `Check full trace in C:\Users\rogal\workspaces\ws-gym\crosstrainingapp\logs\local-pipeline\three-amigos-and-dev-test-2026-08-27.log` |
| CRITICAL | `crosstrainingapp/three-amigos-and-dev-test-2026-08-27.log` | Log error in three-amigos-and-dev-test-2026-08-27.log: [2026-08-27 15:58:33] [ERROR] Unhandled error in three-amigos-and-dev-test run: No se puede llamar a un método en una expresión con valor NULL. | `Check full trace in C:\Users\rogal\workspaces\ws-gym\crosstrainingapp\logs\local-pipeline\three-amigos-and-dev-test-2026-08-27.log` |
| CRITICAL | `crosstrainingapp/three-amigos-and-dev-test-2026-08-27.log` | Log error in three-amigos-and-dev-test-2026-08-27.log: [2026-08-27 16:13:32] [ERROR] Unhandled error in three-amigos-and-dev-test run: No se puede llamar a un método en una expresión con valor NULL. | `Check full trace in C:\Users\rogal\workspaces\ws-gym\crosstrainingapp\logs\local-pipeline\three-amigos-and-dev-test-2026-08-27.log` |
| CRITICAL | `DT-BacklogTriage` | Scheduled task DT-BacklogTriage exited with error code 1 on 08/27/2026 14:42:17 | `Inspect C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\ logs for exact unhandled exception` |
| CRITICAL | `DT-ThreeAmigosDevTest` | Scheduled task DT-ThreeAmigosDevTest exited with error code 1 on 08/27/2026 16:27:17 | `Inspect C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\ logs for exact unhandled exception` |
| CRITICAL | `darwin-trader/three-amigos-and-dev-test-2026-08-27.log` | Log error in three-amigos-and-dev-test-2026-08-27.log: [2026-08-27 15:57:30] [ERROR] Unhandled error in three-amigos-and-dev-test run: No se puede llamar a un método en una expresión con valor NULL. | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\three-amigos-and-dev-test-2026-08-27.log` |
| CRITICAL | `darwin-trader/three-amigos-and-dev-test-2026-08-27.log` | Log error in three-amigos-and-dev-test-2026-08-27.log: [2026-08-27 16:12:29] [ERROR] Unhandled error in three-amigos-and-dev-test run: No se puede llamar a un método en una expresión con valor NULL. | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\three-amigos-and-dev-test-2026-08-27.log` |
| CRITICAL | `darwin-trader/three-amigos-and-dev-test-2026-08-27.log` | Log error in three-amigos-and-dev-test-2026-08-27.log: [2026-08-27 16:27:30] [ERROR] Unhandled error in three-amigos-and-dev-test run: No se puede llamar a un método en una expresión con valor NULL. | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\three-amigos-and-dev-test-2026-08-27.log` |
| CRITICAL | `darwin-trader/pr-review-2026-08-27.log` | Log error in pr-review-2026-08-27.log: [2026-08-27 16:17:22] [ERROR] claude.exe invocation threw for PR #30: Excepción al llamar a "Start" con los argumentos "1": "El nombre del archivo o la extensión es demasiado largo" | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\pr-review-2026-08-27.log` |
| CRITICAL | `darwin-trader/pr-review-2026-08-27.log` | Log error in pr-review-2026-08-27.log: [2026-08-27 16:22:22] [ERROR] claude.exe invocation threw for PR #30: Excepción al llamar a "Start" con los argumentos "1": "El nombre del archivo o la extensión es demasiado largo" | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\pr-review-2026-08-27.log` |
| CRITICAL | `darwin-trader/pr-review-2026-08-27.log` | Log error in pr-review-2026-08-27.log: [2026-08-27 16:27:24] [ERROR] claude.exe invocation threw for PR #30: Excepción al llamar a "Start" con los argumentos "1": "El nombre del archivo o la extensión es demasiado largo" | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\pr-review-2026-08-27.log` |
| CRITICAL | `gh-development-dashboard/architect-2026-08-27.log` | Log error in architect-2026-08-27.log: [2026-08-27 14:17:46] [ERROR] Failed to create subtask 'Compute Dwell-Time Analytics & Expose AnalyticsUiState from DashboardViewModel' for issue #1: gh : could not add label: 'status:pending-review' not found | `Check full trace in C:\Users\rogal\workspaces\ws-setups\gh-development-dashboard\logs\local-pipeline\architect-2026-08-27.log` |
| CRITICAL | `gh-development-dashboard/architect-2026-08-27.log` | Log error in architect-2026-08-27.log: [2026-08-27 14:17:47] [ERROR] Failed to create subtask 'Redesign Main Screen with Scope Bar, Story Evolution Chart, and Stage Dwell-Time Cards' for issue #1: gh : could not add label: 'status:pending-review' not found | `Check full trace in C:\Users\rogal\workspaces\ws-setups\gh-development-dashboard\logs\local-pipeline\architect-2026-08-27.log` |
| CRITICAL | `gh-development-dashboard/architect-2026-08-27.log` | Log error in architect-2026-08-27.log: [2026-08-27 14:22:57] [ERROR] Failed to parse decision JSON from judge response for issue #1: Se ha pasado una matriz no válida. Se esperaba ','. (2180): { | `Check full trace in C:\Users\rogal\workspaces\ws-setups\gh-development-dashboard\logs\local-pipeline\architect-2026-08-27.log` |
| CRITICAL | `gh-development-dashboard/pr-review-2026-08-27.log` | Log error in pr-review-2026-08-27.log: [2026-08-27 15:56:55] [ERROR] claude.exe invocation threw for PR #11: Excepción al llamar a "Start" con los argumentos "1": "El nombre del archivo o la extensión es demasiado largo" | `Check full trace in C:\Users\rogal\workspaces\ws-setups\gh-development-dashboard\logs\local-pipeline\pr-review-2026-08-27.log` |
| CRITICAL | `gh-development-dashboard/pr-review-2026-08-27.log` | Log error in pr-review-2026-08-27.log: [2026-08-27 15:56:57] [ERROR] claude.exe invocation threw for PR #10: Excepción al llamar a "Start" con los argumentos "1": "El nombre del archivo o la extensión es demasiado largo" | `Check full trace in C:\Users\rogal\workspaces\ws-setups\gh-development-dashboard\logs\local-pipeline\pr-review-2026-08-27.log` |
| CRITICAL | `gh-development-dashboard/pr-review-2026-08-27.log` | Log error in pr-review-2026-08-27.log: [2026-08-27 15:56:58] [ERROR] claude.exe invocation threw for PR #9: Excepción al llamar a "Start" con los argumentos "1": "El nombre del archivo o la extensión es demasiado largo" | `Check full trace in C:\Users\rogal\workspaces\ws-setups\gh-development-dashboard\logs\local-pipeline\pr-review-2026-08-27.log` |
| CRITICAL | `gh-development-dashboard/three-amigos-and-dev-test-2026-08-27.log` | Log error in three-amigos-and-dev-test-2026-08-27.log: [2026-08-27 16:01:33] [ERROR] Failed to fetch comments for story #: accepts 1 arg(s), received 0 | `Check full trace in C:\Users\rogal\workspaces\ws-setups\gh-development-dashboard\logs\local-pipeline\three-amigos-and-dev-test-2026-08-27.log` |
| CRITICAL | `gh-development-dashboard/three-amigos-and-dev-test-2026-08-27.log` | Log error in three-amigos-and-dev-test-2026-08-27.log: [2026-08-27 16:01:36] [ERROR] Failed to fetch comments for story #: accepts 1 arg(s), received 0 | `Check full trace in C:\Users\rogal\workspaces\ws-setups\gh-development-dashboard\logs\local-pipeline\three-amigos-and-dev-test-2026-08-27.log` |
| CRITICAL | `gh-development-dashboard/three-amigos-and-dev-test-2026-08-27.log` | Log error in three-amigos-and-dev-test-2026-08-27.log: [2026-08-27 16:16:32] [ERROR] Failed to fetch comments for story #: accepts 1 arg(s), received 0 | `Check full trace in C:\Users\rogal\workspaces\ws-setups\gh-development-dashboard\logs\local-pipeline\three-amigos-and-dev-test-2026-08-27.log` |
| WARNING | `darwin-trader/pr-review-2026-08-27.log` | Warning in pr-review-2026-08-27.log: [2026-08-27 16:22:22] [WARN] Judge failed to produce a usable verdict for PR #30; leaving it unreviewed for manual triage. | Review logs for degradation |
| WARNING | `darwin-trader/pr-review-2026-08-27.log` | Warning in pr-review-2026-08-27.log: [2026-08-27 16:27:24] [WARN] Judge failed to produce a usable verdict for PR #30; leaving it unreviewed for manual triage. | Review logs for degradation |
| WARNING | `darwin-trader/architect-2026-08-27.log` | Warning in architect-2026-08-27.log: [2026-08-27 14:47:21] [WARN] gh api sub_issues threw for story #37: function not defined: open/0 | Review logs for degradation |
| WARNING | `gh-development-dashboard/architect-2026-08-27.log` | Warning in architect-2026-08-27.log: [2026-08-27 14:22:57] [WARN] Judge failed to produce a usable decision for issue #1; leaving it unprocessed for manual triage. | Review logs for degradation |
| WARNING | `gh-development-dashboard/pr-review-2026-08-27.log` | Warning in pr-review-2026-08-27.log: [2026-08-27 15:56:57] [WARN] Judge failed to produce a usable verdict for PR #10; leaving it unreviewed for manual triage. | Review logs for degradation |
| WARNING | `gh-development-dashboard/pr-review-2026-08-27.log` | Warning in pr-review-2026-08-27.log: [2026-08-27 15:56:58] [WARN] Judge failed to produce a usable verdict for PR #9; leaving it unreviewed for manual triage. | Review logs for degradation |

---

## Project: crosstrainingapp (AntaresAndBharani/crosstrainingapp)

### Windows Task Scheduler Execution Matrix
| Task Name | State | Last Run Time | Next Run Time | Last Exit Result | Status |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `CTA-Architect` | Ready | 2026-08-27 16:25:20 | 2026-08-27 16:30:19 | `0` | HEALTHY (0) |
| `CTA-BacklogTriage` | Ready | 2026-08-27 15:47:17 | 2026-08-27 21:47:16 | `0` | HEALTHY (0) |
| `CTA-PRReview` | Ready | 2026-08-27 16:23:28 | 2026-08-27 16:28:27 | `0` | HEALTHY (0) |
| `CTA-ThreeAmigosDevTest` | Ready | 2026-08-27 16:13:28 | 2026-08-27 16:28:27 | `1` | FAILED (1) |

### Local Pipeline Daily Logs (Errors: 4h vs 24h)
| Log File | Last Modified | Size | Errors (4h) | Errors (24h) | Warnings (4h) | Latest 4h Error Snippet |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `architect-2026-08-27.log` | 2026-08-27 16:25:24 | 162.9 KB | **0** | 0 | 0 | None |
| `pr-review-2026-08-27.log` | 2026-08-27 16:23:31 | 130.2 KB | **0** | 9 | 0 | None |
| `three-amigos-and-dev-test-2026-08-27.log` | 2026-08-27 16:13:32 | 42.7 KB | **4** | 5 | 0 | `[2026-08-27 16:13:32] [ERROR] Unhandled error in three-amigos-and-dev-test run: `... |
| `backlog-triage-2026-08-27.log` | 2026-08-27 15:47:34 | 15 KB | **0** | 2 | 0 | None |
| `three-amigos-and-dev-test-2026-08-26.log` | 2026-08-26 23:58:49 | 51.1 KB | **0** | 16 | 0 | None |
| `pr-review-2026-08-26.log` | 2026-08-26 23:58:31 | 145.7 KB | **0** | 5 | 0 | None |
| `architect-2026-08-26.log` | 2026-08-26 23:55:23 | 191.1 KB | **0** | 3 | 0 | None |
| `backlog-triage-2026-08-26.log` | 2026-08-26 21:47:59 | 32.3 KB | **0** | 0 | 0 | None |

### Recent GitHub Actions CI Runs
| Run ID | Workflow Name | Head Branch | Status | Updated At |
| :--- | :--- | :--- | :---: | :---: |
| [33057615711](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33057615711) | Release on Merge to Main | `main` | SUCCESS | 2026-08-27T09:15:19Z |
| [33057603457](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33057603457) | Merge & Backlog | `feat/issue-391` | SUCCESS | 2026-08-27T09:14:10Z |
| [33057308202](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33057308202) | PR CI & Test Verification | `feat/issue-391` | FAILURE | 2026-08-27T09:11:51Z |
| [33056847684](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33056847684) | Release on Merge to Main | `main` | SUCCESS | 2026-08-27T09:05:18Z |
| [33056835567](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33056835567) | Merge & Backlog | `feat/issue-390` | SUCCESS | 2026-08-27T09:04:05Z |
| [33056773923](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33056773923) | PR CI & Test Verification | `feat/issue-390` | FAILURE | 2026-08-27T09:05:00Z |
| [33056226976](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33056226976) | PR CI & Test Verification | `feat/issue-393` | SUCCESS | 2026-08-27T08:57:42Z |
| [33055994858](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33055994858) | PR CI & Test Verification | `feat/issue-393` | FAILURE | 2026-08-27T08:54:34Z |

---

## Project: darwin-trader (AntaresAndBharani/darwin-trader)

### Windows Task Scheduler Execution Matrix
| Task Name | State | Last Run Time | Next Run Time | Last Exit Result | Status |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `DT-Architect` | Ready | 2026-08-27 16:27:17 | 2026-08-27 16:32:16 | `0` | HEALTHY (0) |
| `DT-BacklogTriage` | Ready | 2026-08-27 14:42:17 | 2026-08-27 20:42:16 | `1` | FAILED (1) |
| `DT-PRReview` | Ready | 2026-08-27 16:27:17 | 2026-08-27 16:32:16 | `0` | HEALTHY (0) |
| `DT-ThreeAmigosDevTest` | Ready | 2026-08-27 16:27:17 | 2026-08-27 16:42:16 | `1` | FAILED (1) |

### Local Pipeline Daily Logs (Errors: 4h vs 24h)
| Log File | Last Modified | Size | Errors (4h) | Errors (24h) | Warnings (4h) | Latest 4h Error Snippet |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `three-amigos-and-dev-test-2026-08-27.log` | 2026-08-27 16:27:30 | 47.9 KB | **7** | 11 | 0 | `[2026-08-27 16:27:30] [ERROR] Unhandled error in three-amigos-and-dev-test run: `... |
| `pr-review-2026-08-27.log` | 2026-08-27 16:27:24 | 182.8 KB | **48** | 138 | 48 | `[2026-08-27 16:27:24] [ERROR] claude.exe invocation threw for PR #30: Excepción `... |
| `architect-2026-08-27.log` | 2026-08-27 16:27:22 | 99.9 KB | **0** | 5 | 1 | None |
| `backlog-triage-2026-08-27.log` | 2026-08-27 14:42:52 | 3.4 KB | **0** | 0 | 0 | None |
| `pr-review-2026-08-26.log` | 2026-08-26 23:57:27 | 59.2 KB | **0** | 48 | 0 | None |
| `architect-2026-08-26.log` | 2026-08-26 23:57:21 | 30 KB | **0** | 4 | 0 | None |
| `three-amigos-and-dev-test-2026-08-26.log` | 2026-08-26 23:57:19 | 16.5 KB | **0** | 4 | 0 | None |
| `backlog-triage-2026-08-26.log` | 2026-08-26 20:42:19 | 1.7 KB | **0** | 0 | 0 | None |

### Recent GitHub Actions CI Runs
| Run ID | Workflow Name | Head Branch | Status | Updated At |
| :--- | :--- | :--- | :---: | :---: |
| [33053782975](https://github.com/AntaresAndBharani/darwin-trader/actions/runs/33053782975) | Build APK & CI | `main` | SUCCESS | 2026-08-27T08:25:27Z |
| [33053769798](https://github.com/AntaresAndBharani/darwin-trader/actions/runs/33053769798) | Merge & Backlog | `feat/issue-21` | SUCCESS | 2026-08-27T08:23:04Z |
| [33053707845](https://github.com/AntaresAndBharani/darwin-trader/actions/runs/33053707845) | Build APK & CI | `feat/issue-21` | SUCCESS | 2026-08-27T08:24:07Z |
| [33053652118](https://github.com/AntaresAndBharani/darwin-trader/actions/runs/33053652118) | Build APK & CI | `feat/issue-21` | SUCCESS | 2026-08-27T08:23:16Z |
| [33053419372](https://github.com/AntaresAndBharani/darwin-trader/actions/runs/33053419372) | Build APK & CI | `main` | SUCCESS | 2026-08-27T08:20:22Z |
| [33053407937](https://github.com/AntaresAndBharani/darwin-trader/actions/runs/33053407937) | Merge & Backlog | `feat/issue-20` | SUCCESS | 2026-08-27T08:17:59Z |
| [33053237975](https://github.com/AntaresAndBharani/darwin-trader/actions/runs/33053237975) | Build APK & CI | `feat/issue-20` | SUCCESS | 2026-08-27T08:17:37Z |
| [33053217090](https://github.com/AntaresAndBharani/darwin-trader/actions/runs/33053217090) | Build APK & CI | `feat/issue-20` | SUCCESS | 2026-08-27T08:17:19Z |

---

## Project: gh-development-dashboard (AntaresAndBharani/gh-development-dashboard)

### Windows Task Scheduler Execution Matrix
| Task Name | State | Last Run Time | Next Run Time | Last Exit Result | Status |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `GDD-Architect` | Ready | 2026-08-27 16:26:29 | 2026-08-27 16:31:28 | `0` | HEALTHY (0) |
| `GDD-BacklogTriage` | Ready | 2026-08-27 10:46:27 | 2026-08-27 16:46:27 | `0` | HEALTHY (0) |
| `GDD-PRReview` | Ready | 2026-08-27 16:26:29 | 2026-08-27 16:31:28 | `0` | HEALTHY (0) |
| `GDD-ThreeAmigosDevTest` | Ready | 2026-08-27 16:16:29 | 2026-08-27 16:31:28 | `0` | HEALTHY (0) |

### Local Pipeline Daily Logs (Errors: 4h vs 24h)
| Log File | Last Modified | Size | Errors (4h) | Errors (24h) | Warnings (4h) | Latest 4h Error Snippet |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `architect-2026-08-27.log` | 2026-08-27 16:26:33 | 119.7 KB | **6** | 6 | 1 | `[2026-08-27 14:22:57] [ERROR] Failed to parse decision JSON from judge response `... |
| `pr-review-2026-08-27.log` | 2026-08-27 16:26:32 | 82.8 KB | **24** | 24 | 22 | `[2026-08-27 15:56:58] [ERROR] claude.exe invocation threw for PR #9: Excepción a`... |
| `three-amigos-and-dev-test-2026-08-27.log` | 2026-08-27 16:16:35 | 31 KB | **9** | 13 | 0 | `[2026-08-27 16:16:32] [ERROR] Failed to fetch comments for story #: accepts 1 ar`... |
| `backlog-triage-2026-08-27.log` | 2026-08-27 10:46:30 | 1.9 KB | **0** | 0 | 0 | None |

### Recent GitHub Actions CI Runs
| Run ID | Workflow Name | Head Branch | Status | Updated At |
| :--- | :--- | :--- | :---: | :---: |
| [33080390489](https://github.com/AntaresAndBharani/gh-development-dashboard/actions/runs/33080390489) | Release APK | `main` | SUCCESS | 2026-08-27T14:09:10Z |
| [33054266743](https://github.com/AntaresAndBharani/gh-development-dashboard/actions/runs/33054266743) | Release APK | `main` | SUCCESS | 2026-08-27T08:33:24Z |
| [33053863358](https://github.com/AntaresAndBharani/gh-development-dashboard/actions/runs/33053863358) | Release APK | `main` | FAILURE | 2026-08-27T08:27:34Z |

---

*Generated deterministically by Consistency Supervisor Node (scripts/run-consistency-supervisor.ps1).*

