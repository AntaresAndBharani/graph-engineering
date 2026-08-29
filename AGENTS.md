# Workspace Guidelines & Agent Protocols

## Direct Fix Shortcut (`/quick-fix`)
When the user prefixes their instruction with `/quick-fix` or explicitly requests a direct fix on `main`:
1. **Bypass the standard multi-step lifecycle** (no User Story decomposition, no feature branch, no remote PR gate).
2. **Implement directly on branch `main`**.
3. **Execute local test suite** (`pytest -v`) to confirm 100% passing tests.
4. **Update `CHANGELOG.md`** under `## [Unreleased]`.
5. **Commit and Push directly to `origin/main`** using `Set-GhToken-Antares.ps1`.
