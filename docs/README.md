# docs — design notes and history

Background reading, not instructions. The rules that actually bind the code are in
`CLAUDE.md`; the wiring reference is `DEPENDENCY_MAP.md`. Both live in the project root.

| File | What it is |
|------|------------|
| `CARD_UI_DECISIONS.md` | Why the dashboard board looks and behaves the way it does. Read before changing the card or table view. |
| `REFACTOR_PLAYBOOK.md` | The house method for safe refactors in this codebase. |
| `SPLIT_DATA_STEPS.md` | How the code/data split was carried out (why your data lives in a separate folder). |
| `STAGING.md` / `MERGE_STAGING_STEPS.md` | Running a staging copy alongside the live one, and merging it back. |
| `CLEANUP_CANDIDATES.md` | Older list of files considered for removal. Historical. |
| `DEPENDENCY_MAP.html` | Hand-built visual of the dependency map. Regenerated only on request — expect it to lag the code. |
