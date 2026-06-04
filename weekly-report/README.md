# Weekly Report skill

Generates an **interactive HTML weekly report** for any brand in any Kapoq tenant — a single,
self-contained `.html` file with six tabs (Executive Summary, Sales & Traffic, Advertising,
Products & Inventory, Customer & LTV, Recommended Actions). Brand-agnostic, seller-or-vendor aware,
and it hides any section whose data isn't available.

This is the dashboard counterpart to the `wbr` skill. Use **Weekly Report** when you want an
interactive HTML file; use **WBR** when you want a PPTX deck.

## Layout

```
weekly-report/
├── SKILL.md                 # the skill instructions (workflow, queries, branding, troubleshooting)
├── README.md                # this file
├── .gitignore
├── scripts/
│   ├── build_report.py      # CLI builder: KPIs + callouts + recs, template injection, self-validation
│   ├── resolve_window.py    # CLI helper: pins the week anchor + all date boundaries from freshness dates
│   └── template.html        # self-contained dashboard template (vanilla JS, inline SVG charts, no CDN deps)
└── config/
    └── defaults.json        # tunable callout / recommendation thresholds
```

## Install

Drop the `weekly-report/` directory into your skills folder so the paths match those referenced in
`SKILL.md`. In the Claude environment that is typically:

```
/mnt/skills/user/weekly-report/
```

`SKILL.md` is the entry point — it walks through discovery, branding, table detection, window
resolution (via `resolve_window.py`, which pins the week anchor and all date boundaries from the
freshness dates), the SQL queries (Q1–Q7, written out as `q_*.json`), the build, and validation.

## Build (Step 4)

The data-gathering steps write `context.json` and `q_*.json` into a working directory. Then:

```bash
python3 scripts/build_report.py \
  --workdir  /path/to/workdir \
  --output   /mnt/user-data/outputs/{brand-slug}-weekly-report-w{N}.html \
  --template scripts/template.html \
  --defaults config/defaults.json
  # optional: --config /path/to/brand_overrides.json   (deep-merged over defaults.json)
  # optional: --no-validate                              (debugging only — never for a deliverable)
```

The builder runs a structural self-check after injection and **aborts on failure**, so a broken
file is never written. It verifies the `__DATA__` placeholder was replaced, the embedded `DATA`
parses as JSON, every `getElementById('x')` has a matching `id="x"`, and that no element id is
referenced as a bare global (the trap that previously rendered the dashboard blank).

## Validate (Step 4.5)

The self-check is static. Because the report is JS-rendered, also run the headless smoke test in
`SKILL.md` before presenting — it loads the built file in a headless DOM and asserts zero runtime
errors plus a non-empty render (all 6 tabs). Requires Node + `jsdom`:

```bash
npm install jsdom --silent
```

## Requirements

- **Python 3** (standard library only — no pip packages needed for the build).
- **Node + jsdom** — only for the optional Step 4.5 render smoke test.
- Access to the relevant Kapoq tenant's MCP `run_query` tool for the data-gathering steps.

## Configuration

`config/defaults.json` holds the callout/recommendation thresholds (Buy Box floor, weeks-of-cover
warning, statistical z-threshold, ROAS warning, ASP-YoY trigger, top-ASIN count). Override any
subset per brand with `--config`; keys are deep-merged over the defaults.

## Notes

- Generated reports contain real business data and are git-ignored by default — keep them out of
  the repo.
- `SKILL.md` includes illustrative tenant/brand names and example figures. If this repo is public,
  review and scrub those first.
