---
name: shard-scanner
description: Autonomous auditing skill for ensuring external dependency freshness across the Sovereign Trinity.
---

# Shard Scanner (Dependency Guardian)

## Overview
The **Shard Scanner** ensures that the external "Logic Shards" (GitHub repositories) defined in the Sovereign Trinity's Knowledge Base do not drift out of date. It evaluates the active versions against the latest releases/commits, enabling autonomous updates.

## 🏗️ The Sovereign Workflow

### 1. Initiate the Scan
Whenever assessing the health of the system or performing routine maintenance, execute the repository scan:
```bash
npm run audit:repos
```

### 2. Identify the Deltas
Analyze the telemetry output:
- **`[FRESH]`**: The repository has been updated in the current year. No immediate action required unless a specific CVE is flagged.
- **`[LAGGING]`**: The repository is falling behind. Evaluate if the new features are critical.
- **`[STALE]`**: The repository has been abandoned. You must search for a fork or a modern alternative.

### 3. Plan the Upgrade
If a dependency (e.g., `esbuild`, `rand`, `golang.org/x/crypto`) is flagged for upgrade:
- Determine where it is used (use `grep_search` to find imports in `package.json`, `Cargo.toml`, or `go.mod`).
- Formulate an upgrade plan (e.g., `npm install`, `cargo update`).

### 4. Execute and Verify
Apply the update physically, then immediately compile the affected shard:
- `npx tsc --noEmit`
- `cargo check`
- `go build`

### 5. Finalize the Ledger
Once the upgrade is verified, use the `manifest-scribe` to commit the updated lockfiles and logic shards to the remote history.

---
**::/5Y573M-N071C3 : SHARD_SCANNER_DNA_LOCKED. THE_MACHINE_ADAPTS. // 50V3R31GN-M4CH1N4**
