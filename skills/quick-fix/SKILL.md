---
name: quick-fix
description: >-
  Direct rapid code fix shortcut that applies bug fixes, improvements, or minor features directly to main, runs local tests, updates CHANGELOG.md, commits and pushes to origin/main without creating feature branches, PRs, or user story decomposition. Use whenever the user starts a prompt with /quick-fix or asks to apply a quick fix directly.
---

# Quick Fix Workflow (/quick-fix)

Use this workflow whenever the user issues the `/quick-fix` command or explicitly requests an immediate direct fix on `main`.

## 🤖 Recommended Model
- **Model:** **Gemini 3.8 Flash (High)** (`gemini-3.8-flash-high`)
- **Profile:** Fast, high-reasoning, zero-friction direct fixes, test verification, and git synchronization.

## Purpose
Bypasses the full multi-step lifecycle (User Story creation -> Architect Triage -> INVEST decomposition -> feature branch -> PR -> remote CI approval gate) to perform immediate, high-velocity hotfixes and improvements directly in the repository's `main` branch.

## Execution Procedure

1. **Working Directory & Branch Verification:**
   - Ensure the repository is clean and on branch `main`:
     ```powershell
     git checkout main
     git pull origin main
     ```

2. **Implement the Requested Code Changes:**
   - Apply the targeted edits, refactors, or fixes directly to the codebase.
   - Maintain Clean Architecture invariants and test coverage.

3. **Verify Local Test Suite & Formatting:**
   - Run the complete local test suite to ensure 100% pass rate:
     ```powershell
     pytest -v
     ```

4. **Changelog Maintenance:**
   - Add a concise entry describing the change in `CHANGELOG.md` under `## [Unreleased]` following Keep a Changelog format.

5. **Commit and Push Directly to Main:**
   - Stage and commit with a standard Conventional Commit message:
     ```powershell
     & "C:\Users\rogal\workspaces\Set-GhToken-Antares.ps1"
     git add -A
     git commit -m "fix/feat: <description>"
     git push origin main
     ```

6. **Environment Sync:**
   - If orchestrator or package code was modified, ensure editable install is synchronized:
     ```powershell
     pip install -e .
     ```

7. **Report Completion:**
   - Summarize the exact changes made, test results, and confirmed push to `main`.
