# Findings document template

Fill this in for the skill under review. Keep it self-contained for that one author —
do NOT cross-reference other authors' skills. No emojis in `.md` files. Quote concrete
evidence from the generated artifacts, not the runs' chat summaries.

Copy everything below the line into a new file
`skill_outputs/<skill-name>-consistency-review.md` and replace the bracketed parts.

---

# <skill-name> — Consistency Review

Reviewer: <name> (with Claude Code)
Date: <YYYY-MM-DD>
Skill under test: `<skill-name>` (packaged `<file>` dated `<date>`)
Inputs tested: <brand / account / connector / etc.>

## Purpose

One paragraph: what was tested and why (run-to-run consistency for identical inputs),
plus a re-check of packaging.

## Packaging

Pass, or list defects: wrong archive extension (expect `.zip`), folder not named for the
skill, `SKILL.md` not at the folder root, or any referenced asset (`references/`,
`assets/`, `scripts/`, `config/`) missing. Missing assets alone make consistency
impossible.

## Environment caveat (if any)

If the skill targets an environment that does not exist here (e.g. Cowork / a container)
and you had to adapt to run it, say so, and say what could and could NOT be tested as a
result.

## Methodology

N parallel runs (default 3) with identical frozen inputs. State the frozen inputs in a
table. Note what was left to each run (the thing being tested). Note any environment
adaptation applied to all runs equally.

| Item | Value |
|------|-------|
| ... | ... |

Per-run topline (sizes, key counts) — fill from `compare_artifacts.sh compare`:

| Run | File size | <key axis> | <key axis> |
|-----|-----------|------------|------------|
| run 1 | ... | ... | ... |

## What WAS consistent (working as intended)

Bullet the things identical across all runs (constants, KPIs, structure, labels, etc.),
with the evidence.

## What was NOT consistent (action items)

Numbered. Each item: what differed, across which runs, with concrete evidence from the
files (values, line counts, strings). Distinguish artifact divergences from chat-summary
divergences.

1. ...
2. ...

## Root cause

Trace the divergences to their source(s) — usually a step left to per-run model
improvisation, a latent bug in a bundled artifact, or an unhandled edge case.

## Recommended fixes

Numbered, mapped to the findings above. Concrete and actionable.

## Portability

Present by default. If the artifact only works in one environment and the reviewer did
NOT declare that intentional: recommend a portable/self-contained mode (bake data in at
build time, or abstract the environment dependency behind an adapter), and explain the
lock-in. If the reviewer DID declare it intentional: record the environment constraint as
accepted, not a defect.

## Improving consistency through bundled code (advisory)

Apply `references/bundled-code-guidance.md` where it makes sense. Advisory, not a mandate;
tailor to where this skill already is. Include the two caveats (bundled code must be
robust; do not codify genuine judgment).

## Verdict

One short paragraph: overall consistency level, the dominant cause, and whether the fixes
are narrow or broad.

## Artifacts

- `skill_outputs/<skill-name>-run1...` (size)
- ...
