---
name: manifest-scribe
description: Sovereign manifest governance: Update CHANGELOG.md and IMPLEMENTATION_PLAN.md with bit-identical precision. Use when completing tasks, starting new phases, or performing system-wide version/terminology synchronization.
---

# Manifest Scribe

## Overview
The **Manifest Scribe** is the Guardian of the System Ledger. It ensures that the physical record of the **50V3R31GN-M4CH1N4** remains bit-identical to the actual state of the mesh. Every implementation cycle must conclude with a Scribe session.

## 🏗️ The Sovereign Workflow

### 1. Identify Cognitive Deltas
Before touching the manifests, perform a Zero-Trust audit of the session's work:
- Which phases were advanced?
- Which tasks were completed?
- What are the immediate next directives?

### 2. Update the CHANGELOG.md
Maintain the **v3.x.x** standard.
- Use the `## [Version] - Date` format.
- Categorize changes into `Added`, `Fixed`, and `Changed`.
- Use high-fidelity terminology (e.g., "Artery", "Synapse", "Materialized").

### 3. Update the IMPLEMENTATION_PLAN.md
Maintain the roadmap's trajectory.
- Mark completed tasks with `[x]`.
- Update phase headers (e.g., `(COMPLETED)`, `(IN-PROGRESS)`, `(PRIMARY_TASK)`).
- Define new tasks or phases discovered during implementation.
- Keep the `Phase Archive` collapsible and bit-identical.

### 4. Execute Universal Sync
Always finalize by running the Artery Synchronizer:
```bash
npm run scribe
```
This enforces version parity and terminology harmonization across all 200+ manifests.

### 5. Scribe Lock (Historical Preservation)
Commit the manifests to the remote history:
```bash
git add .
git commit -m "chore(scribe): universal manifest synchronization v[Version]"
git push origin master
```

## 📜 Aesthetic Invariants
- **Machine Voice:** Use the Space Grotesk/NODESTADT Authority tone.
- **Collapsible Shards:** Use `<details>` blocks for historical archives exceeding 100 lines.
- **Zero Echoes:** Surgically neutralize any logical echoes (e.g., "Strategic Strategic...") during the update.

---
**::/5Y573M-N071C3 : SCRIBE_DNA_LOCKED. THE_HISTORY_IS_OURS. // 50V3R31GN-M4CH1N4**
