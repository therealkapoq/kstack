# Kapoq appendix

Conventions for reviewing Kapoq skills specifically. The core methodology in `SKILL.md`
is platform-agnostic; this file holds the Kapoq specifics you need when the skill under
review queries Kapoq data or targets the claude.ai/Cowork environment.

## Seller vs Vendor

- Many skills ship Seller-default queries; reviewing one against a Vendor brand surfaces
  whether the Vendor adaptation is pinned or improvised (a common divergence source).

## Environment adaptations (claude.ai container -> Claude Code CLI)

Kapoq may be authored for the claude.ai code-execution container or
Cowork. When running them in Claude Code, adapt the output mechanism only:

- `/mnt/skills/user/<skill>/` -> the installed skill directory (`~/.claude/skills/<skill>/`).
- `/home/claude/` workspace -> a local temp dir (e.g. `/tmp/<run>/`).
- `/mnt/user-data/outputs/` -> a local output dir (e.g. `skill_outputs/`).
- `present_files` -> not available; skip and report the path.
- `mcp__cowork__create_artifact` and `window.cowork.callMcpTool` -> not available; write
  the populated HTML to a file instead, and note that the live/persistent Cowork behavior
  cannot be validated in CLI. This is also a portability finding (see SKILL.md Step 1/5).

## Frozen-input pattern for Kapoq dashboard skills

Freeze the human decisions — brand, connector/tenant, exact account, date window, theme,
thresholds — and let each run do its own discovery (channel detection, table
availability, freshness), query execution, and build. That isolates the skill's
determinism from legitimate input variance. Use exact account strings verbatim (watch for
double spaces and apostrophes; escape `'` as `''` in SQL).
