---
name: demo-only-diagnosing-kapoq-data
description: Use when investigating any data question, anomaly, discrepancy, or "why is X" issue against data provided by the Kapoq MCP server. Triggers on requests like "look into X", "why is the spend off", "compare Y to Z", or any analytical run_query / sample_data work when using the Kapoq MCP server/connector.
---

# Diagnosing Kapoq Data

## Overview

The Kapoq MCP server is a read-only window into ClickHouse data for e-commerce platform data from venues like Amazon and Walmart (and eventually others like Target/Criteo). It exposes the six tools: `list_tables`, `describe_table`, `get_schema_docs`, `get_date_range`, `run_query`, `sample_data`. Jumping straight to `run_query` without a plan burns tokens, produces shallow answers, and frequently answers the wrong question.

**Core principle:** Diagnose in two enforced phases. Plan first. Execute second. Never blend them.

**Violating the letter of the two-phase split is violating the spirit.** Don't sneak exploratory queries into Phase 1 and don't plan-as-you-go in Phase 2.

## The Iron Law

```
NO run_query OR sample_data CALLS UNTIL
  (1) THE PLAN IS WRITTEN,
  (2) THE USER HAS APPROVED IT, AND
  (3) THE USER HAS PICKED AN EXECUTION MODE.
```

Schema-discovery tools (`list_tables`, `describe_table`, `get_schema_docs`, `get_date_range`) are allowed in Phase 1 — they answer "what's available", not "what's the data". Anything that returns row data is Phase 2 only.

## When to Use

Use whenever the user asks you to investigate, compare, explain, or quantify something in Kapoq MCP server data, including:
- "Why is [metric] [unexpected value]?"
- "Find rows / accounts / campaigns where..."
- "Compare X between [period A] and [period B]"
- "Is [hypothesis] true?"
- "Pull a list of..."
- "What does table X look like?"

**Do NOT use when:**
- The user is editing schema — that's authoring, not diagnosing.
- The user is asking about MCP server config / auth / deployment — that's infra.
- The question is fully answerable from `get_schema_docs` / `describe_table` alone (pure metadata) — answer it directly without the two-phase ceremony.

## Phase 1 — Plan and Clarify

**Before any `run_query` or `sample_data` call**, produce a written plan and get the user's sign-off.

### What Phase 1 produces

A short markdown plan with these fields:

| Field | Example |
|-------|---------|
| **MCP server** | `Kapoq MCP` |
| **Question (one sentence)** | "Why did Sponsored Products spend drop on 2026-04-25 vs 2026-04-18?" |
| **Tables involved** | `AdvertisingCampaignData`, `AdvertisingAdData` |
| **Time range** | `2026-04-18` and `2026-04-25` (confirmed via `get_date_range`) |
| **Slice / grouping** | by `CampaignId`, `AdGroupId` |
| **Filters** | marketplace = US, paused campaigns excluded |
| **Output shape** | "Two-row comparison + top 10 campaigns by spend delta" |
| **Open questions** | "Same DOW? Include archived campaigns?" |

### Tools allowed in Phase 1

Only metadata-shaped MCP calls:

| Tool | Purpose |
|------|---------|
| `list_tables` | Discover what's in the Kapoq MCP server's underlying database |
| `describe_table` | Column names, types, nullability |
| `get_schema_docs` | Semantic / business notes about tables and columns |
| `get_date_range` | Min/max date for a table — sanity-check time filters before they appear in `WHERE` |

These do NOT count as "starting the analysis". Use them to fill in the plan.

### Clarifying questions — ask them, don't guess

Always ask the user about anything ambiguous *before* the plan is final. Common gaps:

- **Time range**: absolute dates or relative ("last week")? Inclusive of today? UTC or tenant-local?
- **Marketplace / channel**: Amazon vs Walmart vs Criteo? Sponsored Products vs Sponsored Brands vs Sponsored Display vs DSP?
- **Aggregation level**: account, campaign, ad group, ASIN, keyword, search term?
- **"Why" questions**: what's the comparison baseline? (prior period, prior year, target)
- **Inclusion rules**: paused entities? archived? test campaigns? returns/refunds?
- **Definition**: "spend" = `cost`? `cost_local`? attributed only?

If the user gave you 80% of the spec, ask for the remaining 20% before planning. Do not guess and proceed.

### End of Phase 1

Output the plan as a markdown block, then hand control back explicitly:

> "Plan above. Pick an execution mode (A or B below), or tell me what to change."

## Phase 2 — Execute

User has approved the plan. Now choose HOW to run it. Do not silently pick — present both options and recommend.

```
Execution mode:
  [A] Main session — I run the queries directly here; you see each result.
  [B] Subagent — I dispatch one or more subagents with the plan; they
      return a synthesized answer (rows stay out of this conversation).

Recommendation: [A or B] because [reason].
```

### When to recommend Main session (A)

- Plan needs ≤ 3 queries.
- Each query is small (KB-scale result, single time window).
- The user is likely to want to *iterate* on results ("now break that down by X").
- Schema is fuzzy enough that interactive `sample_data` will help.
- Final answer is one number, one chart, or one short table.

### When to recommend Subagent (B)

- Plan needs ≥ 4 queries or many slices.
- Multiple MCP servers blending different data sources in scope (one subagent per server, in parallel).
- Result set is large and only the *summary* matters in this conversation.
- Same query template across N entities (campaigns, brands, dates) — embarrassingly parallel.
- Long-running aggregation work where the main thread shouldn't hold the rows.

### Subagent prompt structure (mode B only)

Each subagent prompt MUST include:

1. The exact MCP tool prefix to use (e.g. `Kapoq__run_query`).
2. The full Phase 1 plan, verbatim.
3. The expected output shape ("return a markdown table with columns A, B, C — under 200 words").
4. A "do NOT explore beyond this plan" instruction — subagents drift.
5. Relevant schema context already gathered in Phase 1 (paste `describe_table` / `get_schema_docs` output) so the subagent does not re-discover.

### During execution

- Re-confirm date ranges with `get_date_range` before any query that filters on time.
- Prefer `LIMIT` and aggregate queries; avoid `SELECT *` from large tables.
- If a query returns nothing or implausibly little, do NOT silently re-run with looser filters — surface the zero result and revisit the plan with the user.
- For ClickHouse-specific syntax (`FINAL`, JOINs, correlated subqueries), follow the Pre-Flight Gate Checks in the Kapoq MCP server context, project conventions in CLAUDE.md, and the user's memory.

## Quick Reference

| Step | Tools allowed | Output |
|------|---------------|--------|
| Phase 1 — Plan | `list_tables`, `describe_table`, `get_schema_docs`, `get_date_range` | Markdown plan + clarifying questions |
| Phase 1 — Sign-off | (none) | User approves plan |
| Phase 2 — Mode pick | (none) | Recommend A (main) or B (subagent); user chooses |
| Phase 2 — Execute | `run_query`, `sample_data`, plus Phase 1 tools | Answer in the shape the plan specified |

## Kapoq MCP server tools (reference)

A Kapoq MCP server connection exposes these six tools:

| Tool | What it returns | Phase |
|------|------------------|-------|
| `list_tables` | Table names in the underlying Kapoq MCP DB | 1 |
| `describe_table` | Columns, types | 1 |
| `get_schema_docs` | Semantic / business notes | 1 |
| `get_date_range` | Min/max date for a table | 1 |
| `run_query` | Arbitrary read-only SQL | 2 |
| `sample_data` | A few rows from a table | 2 |

## Red Flags — Stop and Restart Phase 1

If you catch yourself doing any of these, abort and return to planning:

- Calling `run_query` before writing the plan.
- Calling `sample_data` "to get a feel" before the plan exists.
- "I'll just check one thing real quick" — that's Phase 2 leaking into Phase 1.
- Picking subagent vs main session without asking the user.
- Guessing the MCP connection when multilple MCP servers are connected (especially when non-Kapoq MCP connectors are involved).
- Assuming "last week" / "recently" without confirming exact dates with the user.
- Skipping clarifying questions because the request "seems clear".

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "The plan is obvious, just run it" | Obvious plans wrong-question all the time. 30 seconds to write it down catches half the mistakes. |
| "One quick exploratory query won't hurt" | It's never one. And the result anchors the plan you haven't written yet. |
| "User said 'check X' so they don't want a plan" | They want the answer. The plan is for *you* to converge on the right answer. Show it briefly, ask the gaps, move on. |
| "Subagents are overkill here" | Maybe — but say so out loud and recommend main session. Don't skip the choice. |
| "I already know the schema" | Confirm with `describe_table` / `get_date_range` before filtering. |
| "Auto mode means skip clarifying questions" | Auto mode means execute without asking permission to *act*. It does not mean guess at requirements. Ambiguous spec = ask. |
| "I'll plan in my head and just narrate the results" | The user can't redirect a plan they can't see. Write it down. |
