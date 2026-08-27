# Graph Engineering - Pipeline Health Dashboard

**Last Audit Run:** `2026-08-27 10:45:21`  
**Overall System Health (Active 4h Window):** **CRITICAL / ACTION REQUIRED**

---

## Executive Overview

| Project | Target Repository | Health (4h) | Active Tasks | Log Errors (4h) | Log Errors (24h) | Recent CI Failures |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **crosstrainingapp** | [AntaresAndBharani/crosstrainingapp](https://github.com/AntaresAndBharani/crosstrainingapp) | CRITICAL | 4 | **6** | 41 | 2 |
| **darwin-trader** | [AntaresAndBharani/darwin-trader](https://github.com/AntaresAndBharani/darwin-trader) | CRITICAL | 4 | **58** | 134 | 0 |
| **gh-development-dashboard** | [AntaresAndBharani/gh-development-dashboard](https://github.com/AntaresAndBharani/gh-development-dashboard) | CRITICAL | 0 | **0** | 0 |  |

---

## Active Issues & Remediation Action Items (Last 4 Hours)

The supervisor detected the following active issues requiring attention within the last 4 hours:

| Severity | Project / Component | Issue Description (Last 4h) | Recommended Action |
| :---: | :--- | :--- | :--- |
| CRITICAL | `crosstrainingapp/three-amigos-and-dev-test-2026-08-27.log` | Log error in three-amigos-and-dev-test-2026-08-27.log: [2026-08-27 10:33:15] [ERROR] agy.exe exited 1 for subtask #393. StdErr:  - StdOut: {"conversation_id":"44cf1677-2185-4c91-9957-c03f998f3500","status":"ERROR","response":"","error":"timeout waiting for response","duration_seconds":1198.553593,"num_turns":1,"usage":{"input_tokens":1054419,"output_tokens":52575,"thinking_tokens":26587,"cache_read_tokens":30536005,"total_tokens":1106994}} | `Check full trace in C:\Users\rogal\workspaces\ws-gym\crosstrainingapp\logs\local-pipeline\three-amigos-and-dev-test-2026-08-27.log` |
| CRITICAL | `crosstrainingapp/pr-review-2026-08-27.log` | Log error in pr-review-2026-08-27.log: [2026-08-27 08:28:31] [ERROR] Unhandled error in PR review run: git fetch origin failed with exit code 1 | `Check full trace in C:\Users\rogal\workspaces\ws-gym\crosstrainingapp\logs\local-pipeline\pr-review-2026-08-27.log` |
| CRITICAL | `crosstrainingapp/pr-review-2026-08-27.log` | Log error in pr-review-2026-08-27.log: [2026-08-27 09:13:33] [INFO] git: fatal: Unable to create 'C:/Users/rogal/workspaces/ws-gym/crosstrainingapp/.git/index.lock': File exists. | `Check full trace in C:\Users\rogal\workspaces\ws-gym\crosstrainingapp\logs\local-pipeline\pr-review-2026-08-27.log` |
| CRITICAL | `crosstrainingapp/pr-review-2026-08-27.log` | Log error in pr-review-2026-08-27.log: [2026-08-27 09:13:33] [ERROR] Unhandled error in PR review run: git reset --hard origin/main failed with exit code 128 | `Check full trace in C:\Users\rogal\workspaces\ws-gym\crosstrainingapp\logs\local-pipeline\pr-review-2026-08-27.log` |
| CRITICAL | `crosstrainingapp/backlog-triage-2026-08-27.log` | Log error in backlog-triage-2026-08-27.log: [2026-08-27 07:52:17] [INFO] git: fatal: Unable to create 'C:/Users/rogal/workspaces/ws-gym/crosstrainingapp/.git/index.lock': File exists. | `Check full trace in C:\Users\rogal\workspaces\ws-gym\crosstrainingapp\logs\local-pipeline\backlog-triage-2026-08-27.log` |
| CRITICAL | `crosstrainingapp/backlog-triage-2026-08-27.log` | Log error in backlog-triage-2026-08-27.log: [2026-08-27 07:52:17] [ERROR] Unhandled error in backlog triage run: git checkout main failed with exit code 128 | `Check full trace in C:\Users\rogal\workspaces\ws-gym\crosstrainingapp\logs\local-pipeline\backlog-triage-2026-08-27.log` |
| CRITICAL | `crosstrainingapp/PR CI & Test Verification` | GitHub Workflow 'PR CI & Test Verification' (feat/issue-394) failed on run #33054907452 (https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33054907452) | `Review workflow run logs at https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33054907452` |
| CRITICAL | `crosstrainingapp/PR Snapshot Build & Pre-Release` | GitHub Workflow 'PR Snapshot Build & Pre-Release' (feat/issue-393) failed on run #33054165311 (https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33054165311) | `Review workflow run logs at https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33054165311` |
| CRITICAL | `DT-BacklogTriage` | Scheduled task DT-BacklogTriage exited with error code 1 on 08/27/2026 08:42:17 | `Inspect C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\ logs for exact unhandled exception` |
| CRITICAL | `darwin-trader/three-amigos-and-dev-test-2026-08-27.log` | Log error in three-amigos-and-dev-test-2026-08-27.log: [2026-08-27 07:57:19] [ERROR] Unhandled error in three-amigos-and-dev-test run: git reset --hard origin/main failed with exit code 128 | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\three-amigos-and-dev-test-2026-08-27.log` |
| CRITICAL | `darwin-trader/three-amigos-and-dev-test-2026-08-27.log` | Log error in three-amigos-and-dev-test-2026-08-27.log: [2026-08-27 09:30:30] [ERROR] agy.exe exited 1 for fix-up on PR #15. StdErr:  - StdOut: {"conversation_id":"6c9ec90f-5f9e-4960-a34a-9081492a132f","status":"ERROR","response":"","error":"timeout waiting for response","duration_seconds":1150.7705321,"num_turns":1,"usage":{"input_tokens":669090,"output_tokens":23551,"thinking_tokens":8924,"cache_read_tokens":10175400,"total_tokens":692641}} | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\three-amigos-and-dev-test-2026-08-27.log` |
| CRITICAL | `darwin-trader/three-amigos-and-dev-test-2026-08-27.log` | Log error in three-amigos-and-dev-test-2026-08-27.log: [2026-08-27 09:32:35] [ERROR] agy.exe exited 1 for fix-up on PR #15. StdErr:  - StdOut: {"conversation_id":"55bf230f-11a7-4da3-8098-c02855a02c70","status":"ERROR","response":"","error":"The stream was interrupted. Please continue the task you were working on.","duration_seconds":1198.2503427,"num_turns":1,"usage":{"input_tokens":431885,"output_tokens":13899,"thinking_tokens":5988,"cache_read_tokens":4842790,"total_tokens":445784}} | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\three-amigos-and-dev-test-2026-08-27.log` |
| CRITICAL | `darwin-trader/pr-review-2026-08-27.log` | Log error in pr-review-2026-08-27.log: [2026-08-27 10:32:22] [ERROR] claude.exe invocation threw for PR #30: Excepción al llamar a "Start" con los argumentos "1": "El nombre del archivo o la extensión es demasiado largo" | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\pr-review-2026-08-27.log` |
| CRITICAL | `darwin-trader/pr-review-2026-08-27.log` | Log error in pr-review-2026-08-27.log: [2026-08-27 10:37:22] [ERROR] claude.exe invocation threw for PR #30: Excepción al llamar a "Start" con los argumentos "1": "El nombre del archivo o la extensión es demasiado largo" | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\pr-review-2026-08-27.log` |
| CRITICAL | `darwin-trader/pr-review-2026-08-27.log` | Log error in pr-review-2026-08-27.log: [2026-08-27 10:42:23] [ERROR] claude.exe invocation threw for PR #30: Excepción al llamar a "Start" con los argumentos "1": "El nombre del archivo o la extensión es demasiado largo" | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\pr-review-2026-08-27.log` |
| CRITICAL | `darwin-trader/architect-2026-08-27.log` | Log error in architect-2026-08-27.log: [2026-08-27 09:47:20] [ERROR] Unhandled error in Architect run: git reset --hard origin/main failed with exit code 128 | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\architect-2026-08-27.log` |
| CRITICAL | `darwin-trader/architect-2026-08-27.log` | Log error in architect-2026-08-27.log: [2026-08-27 10:02:19] [ERROR] Unhandled error in Architect run: git fetch origin failed with exit code 1 | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\architect-2026-08-27.log` |
| CRITICAL | `darwin-trader/architect-2026-08-27.log` | Log error in architect-2026-08-27.log: [2026-08-27 10:27:20] [ERROR] Unhandled error in Architect run: git fetch origin failed with exit code 1 | `Check full trace in C:\Users\rogal\workspaces\ws-trading\darwin-trader\logs\local-pipeline\architect-2026-08-27.log` |
| CRITICAL | `gh-development-dashboard/Release APK` | GitHub Workflow 'Release APK' (main) failed on run #33053863358 (https://github.com/AntaresAndBharani/gh-development-dashboard/actions/runs/33053863358) | `Review workflow run logs at https://github.com/AntaresAndBharani/gh-development-dashboard/actions/runs/33053863358` |
| WARNING | `darwin-trader/pr-review-2026-08-27.log` | Warning in pr-review-2026-08-27.log: [2026-08-27 10:37:22] [WARN] Judge failed to produce a usable verdict for PR #30; leaving it unreviewed for manual triage. | Review logs for degradation |
| WARNING | `darwin-trader/pr-review-2026-08-27.log` | Warning in pr-review-2026-08-27.log: [2026-08-27 10:42:23] [WARN] Judge failed to produce a usable verdict for PR #30; leaving it unreviewed for manual triage. | Review logs for degradation |
| WARNING | `darwin-trader/architect-2026-08-27.log` | Warning in architect-2026-08-27.log: [2026-08-27 08:47:22] [WARN] gh api sub_issues threw for story #17: function not defined: open/0 | Review logs for degradation |
| WARNING | `darwin-trader/architect-2026-08-27.log` | Warning in architect-2026-08-27.log: [2026-08-27 08:48:16] [WARN] gh api sub_issues threw for story #16: function not defined: open/0 | Review logs for degradation |

---

## Project: crosstrainingapp (AntaresAndBharani/crosstrainingapp)

### Windows Task Scheduler Execution Matrix
| Task Name | State | Last Run Time | Next Run Time | Last Exit Result | Status |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `CTA-Architect` | Ready | 2026-08-27 10:40:20 | 2026-08-27 10:45:19 | `0` | HEALTHY (0) |
| `CTA-BacklogTriage` | Ready | 2026-08-27 09:47:17 | 2026-08-27 15:47:16 | `0` | HEALTHY (0) |
| `CTA-PRReview` | Ready | 2026-08-27 10:43:28 | 2026-08-27 10:48:27 | `0` | HEALTHY (0) |
| `CTA-ThreeAmigosDevTest` | Running | 2026-08-27 10:43:28 | 2026-08-27 10:58:27 | `267009` | RUNNING |

### Local Pipeline Daily Logs (Errors: 4h vs 24h)
| Log File | Last Modified | Size | Errors (4h) | Errors (24h) | Warnings (4h) | Latest 4h Error Snippet |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `three-amigos-and-dev-test-2026-08-27.log` | 2026-08-27 10:43:34 | 19.3 KB | **1** | 1 | 0 | `[2026-08-27 10:33:15] [ERROR] agy.exe exited 1 for subtask #393. StdErr:  - StdO`... |
| `pr-review-2026-08-27.log` | 2026-08-27 10:43:31 | 71.9 KB | **3** | 3 | 0 | `[2026-08-27 09:13:33] [ERROR] Unhandled error in PR review run: git reset --hard`... |
| `architect-2026-08-27.log` | 2026-08-27 10:40:24 | 76.3 KB | **0** | 0 | 0 | None |
| `backlog-triage-2026-08-27.log` | 2026-08-27 09:47:20 | 11.6 KB | **2** | 2 | 0 | `[2026-08-27 07:52:17] [ERROR] Unhandled error in backlog triage run: git checkou`... |
| `three-amigos-and-dev-test-2026-08-26.log` | 2026-08-26 23:58:49 | 51.1 KB | **0** | 26 | 0 | None |
| `pr-review-2026-08-26.log` | 2026-08-26 23:58:31 | 145.7 KB | **0** | 5 | 0 | None |
| `architect-2026-08-26.log` | 2026-08-26 23:55:23 | 191.1 KB | **0** | 4 | 0 | None |
| `backlog-triage-2026-08-26.log` | 2026-08-26 21:47:59 | 32.3 KB | **0** | 0 | 0 | None |

### Recent GitHub Actions CI Runs
| Run ID | Workflow Name | Head Branch | Status | Updated At |
| :--- | :--- | :--- | :---: | :---: |
| [33054968350](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33054968350) | Release on Merge to Main | `main` | SUCCESS | 2026-08-27T08:40:38Z |
| [33054953982](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33054953982) | Merge & Backlog | `feat/issue-394` | SUCCESS | 2026-08-27T08:39:06Z |
| [33054907452](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33054907452) | PR CI & Test Verification | `feat/issue-394` | FAILURE | 2026-08-27T08:39:36Z |
| [33054661592](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33054661592) | Release on Merge to Main | `main` | SUCCESS | 2026-08-27T08:36:14Z |
| [33054617220](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33054617220) | Merge & Backlog | `feat/issue-393` | completed | 2026-08-27T08:34:23Z |
| [33054517483](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33054517483) | PR Snapshot Build & Pre-Release | `feat/issue-393` | SUCCESS | 2026-08-27T08:35:38Z |
| [33054234288](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33054234288) | Merge & Backlog | `feat/issue-393` | completed | 2026-08-27T08:29:10Z |
| [33054165311](https://github.com/AntaresAndBharani/crosstrainingapp/actions/runs/33054165311) | PR Snapshot Build & Pre-Release | `feat/issue-393` | FAILURE | 2026-08-27T08:30:58Z |

---

## Project: darwin-trader (AntaresAndBharani/darwin-trader)

### Windows Task Scheduler Execution Matrix
| Task Name | State | Last Run Time | Next Run Time | Last Exit Result | Status |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `DT-Architect` | Ready | 2026-08-27 10:42:17 | 2026-08-27 10:47:16 | `0` | HEALTHY (0) |
| `DT-BacklogTriage` | Ready | 2026-08-27 08:42:17 | 2026-08-27 14:42:16 | `1` | FAILED (1) |
| `DT-PRReview` | Ready | 2026-08-27 10:42:17 | 2026-08-27 10:47:16 | `0` | HEALTHY (0) |
| `DT-ThreeAmigosDevTest` | Ready | 2026-08-27 10:42:17 | 2026-08-27 10:57:16 | `0` | HEALTHY (0) |

### Local Pipeline Daily Logs (Errors: 4h vs 24h)
| Log File | Last Modified | Size | Errors (4h) | Errors (24h) | Warnings (4h) | Latest 4h Error Snippet |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `three-amigos-and-dev-test-2026-08-27.log` | 2026-08-27 10:42:32 | 23.7 KB | **4** | 4 | 0 | `[2026-08-27 09:32:35] [ERROR] agy.exe exited 1 for fix-up on PR #15. StdErr:  - `... |
| `pr-review-2026-08-27.log` | 2026-08-27 10:42:23 | 102.4 KB | **49** | 69 | 42 | `[2026-08-27 10:42:23] [ERROR] claude.exe invocation threw for PR #30: Excepción `... |
| `architect-2026-08-27.log` | 2026-08-27 10:42:21 | 45.4 KB | **5** | 5 | 3 | `[2026-08-27 10:27:20] [ERROR] Unhandled error in Architect run: git fetch origin`... |
| `backlog-triage-2026-08-27.log` | 2026-08-27 08:42:36 | 2.2 KB | **0** | 0 | 0 | None |
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

### Local Pipeline Daily Logs (Errors: 4h vs 24h)
| Log File | Last Modified | Size | Errors (4h) | Errors (24h) | Warnings (4h) | Latest 4h Error Snippet |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |

### Recent GitHub Actions CI Runs
| Run ID | Workflow Name | Head Branch | Status | Updated At |
| :--- | :--- | :--- | :---: | :---: |
| [33054266743](https://github.com/AntaresAndBharani/gh-development-dashboard/actions/runs/33054266743) | Release APK | `main` | SUCCESS | 2026-08-27T08:33:24Z |
| [33053863358](https://github.com/AntaresAndBharani/gh-development-dashboard/actions/runs/33053863358) | Release APK | `main` | FAILURE | 2026-08-27T08:27:34Z |

---

*Generated deterministically by Consistency Supervisor Node (scripts/run-consistency-supervisor.ps1).*

