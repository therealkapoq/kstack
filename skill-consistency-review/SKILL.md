---
name: skill-consistency-review
description: Review a packaged or unzipped skill for run-to-run output consistency by running it multiple times with identical frozen inputs and diffing the artifacts, then write a developer-facing findings doc. Use when asked to review a skill for consistency, check whether a skill produces consistent/deterministic output, test a skill's repeatability, or evaluate a packaged .zip/.skill for run-to-run drift. Also covers verifying a skill's packaging and the portability of the artifacts it generates.
---

# Skill Consistency Review

Review a packaged (or unzipped) skill to find out whether it produces *consistent*
output when run repeatedly with the same inputs. The method: run the skill-under-review
several times in parallel with an identical frozen input-set, diff the resulting
artifacts, and write a developer-facing findings document. The review also checks the
skill's packaging and the portability of the artifacts it generates.

This skill reviews OTHER skills. It does not author or fix them.

## When to use

- "Review <skill> for consistency", "is <skill>'s output deterministic", "does this
  skill produce the same thing every run", "test this skill's repeatability".
- Evaluating a packaged `.zip` / `.skill` for run-to-run drift before shipping it.
- Checking a skill's packaging (structure, missing assets) and whether the artifacts it
  emits are portable or locked to one environment.

Do NOT use for authoring or editing a skill (that is `writing-skills`), or for
one-off "run this skill once" requests.

## What "consistent" means

The deliverable of most skills is an artifact (an HTML file, a deck, a populated
template, generated SQL). Consistency is whether two runs from the same inputs produce
the same artifact: same structure, same stat presentation, same labels, same embedded
queries/constants, same counts. It is NOT about whether the underlying data is correct.
Judge the artifacts, not the runs' chat summaries (see Step 4).

## Workflow

### Step 0 — Inspect and install

Before running anything, inspect the package:

- If it is an archive, confirm it is a `.zip` (a renamed `.skill`/other extension is a
  packaging defect — the installer expects `.zip`).
- It should extract to a folder named for the skill (no extension), with `SKILL.md` at
  the folder root, not files dumped at the archive root.
- Every file `SKILL.md` references (`references/...`, `assets/...`, `scripts/...`,
  `config/...`) must be present. Missing bundled assets is the most common packaging
  defect and on its own makes consistent output impossible.

Record any packaging defects as findings. Then install to `~/.claude/skills/<name>/`
(or read it in place if the reviewer does not want it installed). If you install over
an existing version, look at what you are replacing first.

### Step 1 — Understand the skill (and capture target-environment intent)

Read `SKILL.md` and its referenced assets. Establish:

- Which inputs are genuine user-facing decisions (these get frozen in Step 2) versus
  which are auto-discovered or purely mechanical (these are left to each run — they are
  what you are testing).
- The output artifact(s), and the environment the skill targets.
- Whether the artifact depends on environment-specific APIs/paths/tools — e.g.
  `window.cowork.callMcpTool`, `mcp__cowork__create_artifact`, claude.ai/Cowork-mode
  features, or container-only paths (`/mnt/skills/...`, `/mnt/user-data/...`,
  `/home/claude/`, `present_files`).
- What adaptation is needed to run the skill in the current environment (e.g. write to a
  local path instead of a container path; skip a tool that does not exist here). Keep
  adaptations to the output mechanism only — do not change the skill's logic.

Portability intent (default = portable). Unless the reviewer explicitly says an
environment-specific artifact is the intended deliverable, treat portability as a review
axis: an artifact that only works inside one environment (e.g. a Cowork-only artifact
that fetches data via `window.cowork` and cannot run as a standalone file) is a finding,
and Step 5 recommends a portable mode. If the reviewer declares the lock-in intentional,
record it as an accepted constraint instead of a defect.

### Step 2 — Pre-flight once

Resolve the user-facing decisions a single time (interactively if needed) into a frozen
answer-set. Probe the data/environment as needed to freeze values that are
ambiguous-but-deterministic (an exact account name, the latest-data date, a resolved
week) so they are not a source of divergence. The point of freezing is to remove
legitimate variance so any remaining divergence is attributable to the skill itself.

### Step 3 — Dispatch N parallel runs (default 3, scalable)

Dispatch N subagents (default 3) with the identical frozen answer-set. Each subagent:

- runs the skill-under-review end to end, fully autonomously — no questions;
- writes a distinct artifact (`<name>-run1`, `-run2`, `-run3`, ...);
- adapts ONLY the output mechanism for the current environment, nothing else;
- must NOT edit the skill's source files, and must report it as a finding if it believes
  a patch is required (a skill that forces per-run patches is itself a consistency
  problem — different runs patch differently).

Scale N up (5+) for high-stakes reviews, or when three agreeing runs could plausibly be
luck. Prefer background dispatch for long runs so one failed run does not lose the batch.
Give every run the same prompt; vary only the run id / output path.

### Step 4 — Compare for consistency

Use `scripts/compare_artifacts.sh` (compare mode) plus targeted inspection. Diff on the
axes that matter: structure, stat presentation, labels/terminology, embedded queries and
constants, element/section counts, and file size.

Two hard rules, both learned the hard way:

- Judge the ARTIFACTS, not the agents' prose summaries. A run can confidently misstate
  its own output (e.g. report "0.7 weeks of cover" while the file shows "0.9"). Always
  verify claims against the generated files.
- Verify skill-source integrity AFTER the runs (`compare_artifacts.sh` integrity mode):
  confirm the installed/source files are byte-identical to the original package, so no
  run contaminated the skill or a sibling run.

### Step 5 — Write the findings document

Write a developer-facing `.md` per `references/findings-template.md`. Make it
self-contained for that author — do not cross-reference other authors' skills. No emojis
in `.md` files.

Sections: methodology + frozen-inputs table; what WAS consistent; what was NOT consistent
(numbered, each with concrete evidence from the files); root cause; recommended fixes;
portability; verdict; artifacts. The Portability section is present by default: if the
artifact is environment-specific and the reviewer did not declare that intentional,
recommend a portable/self-contained mode; if the reviewer declared it intentional, record
the lock-in as an accepted constraint rather than a defect.

### Step 6 — Advisory bundled-code recommendation

Where it would genuinely help, add the recommendation in `references/bundled-code-guidance.md`:
suggest pinning deterministic transforms (math, formatting, column/section sets, query
shapes, empty-state strings) in bundled code/templates/SQL so they cannot drift. This is
a suggestion, not a mandate, and carries two caveats: bundled code must be robust on the
real input space (buggy bundled code that forces per-run patches amplifies divergence),
and genuine judgment (scope, narrative, which items matter) should stay with the model.
Tailor it to where the skill actually is: no builder -> suggest one; has a builder ->
extend its boundary upstream; has a query pack -> pin the variants.

## Reference files

- `references/findings-template.md` — the developer-facing review document structure.
- `references/bundled-code-guidance.md` — the advisory "bundle code for consistency"
  framing, with its caveats.
- `references/kapoq-appendix.md` — Kapoq-specific conventions (MCP connectors, ClickHouse
  pitfalls, Seller vs Vendor, claude.ai-container-to-CLI adaptations). Consult when the
  skill-under-review is a Kapoq skill.

## Helper script

`scripts/compare_artifacts.sh` provides `compare` (sizes, pairwise tag-split diff,
structural extraction) and `integrity` (installed-vs-source check) modes. It is portable
bash; if Windows/Git-Bash teammates hit friction, the documented fallback is to rewrite
it in Python3 (cross-platform, cleaner structured extraction).
