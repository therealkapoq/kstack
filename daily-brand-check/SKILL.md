---
name: daily-brand-check
description: "Build a persistent multi-page Cowork artifact a user can re-open every morning to check the health of one Amazon brand in Kapoq. The artifact has tabs for Brand Overview, Advertising, ASIN / Product, Search Terms, and Inventory Health, each showing 30 days vs prior 30 plus year-over-year. Brand is baked in at creation, so one artifact = one brand. Use this skill whenever the user asks for a daily check, morning check, live view, command center, cockpit, daily dashboard, persistent dashboard, or any phrase that implies a page they want to keep re-opening to track a brand's performance over time. Trigger even if the user does not say 'dashboard' explicitly — phrases like 'something I can look at each morning for [brand]', 'a page I keep open for [brand]', or 'check on [brand]' that mention a brand and recurring inspection are strong signals."
---

# Daily Brand Check

Build a live, multi-page Cowork artifact for one Amazon brand in Kapoq. The user opens it each morning; the artifact re-queries Kapoq Datalink at load time so data is always fresh.

## What this skill produces

A single Cowork artifact with five tabs:

1. **Brand Overview** — top-line KPIs (sales, units, ad spend, ACoS, TACoS, ROAS, traffic) for the trailing 30 days vs prior 30, plus YoY (same 30 days last year). Daily sales + ad spend trend chart. Top 10 ASINs by sales.
2. **Advertising** — ad spend, sales, ACoS, ROAS, CTR, CPC, by campaign type (SP / SB / SBV / SD) and per campaign. Daily ad-spend trend.
3. **ASIN / Product** — per-ASIN sales, units, page views, conversion, ad spend, ACoS; sortable; flags slow movers and at-risk hero ASINs.
4. **Search Terms** — top search terms by ad sales, by wasted spend, and by harvesting potential.
5. **Inventory Health** — on-hand, inbound, reserved, weeks-of-cover; flags low-cover hero ASINs and stranded inventory.

The artifact is persistent: it shows up in Cowork's sidebar, the user can re-open it, the built-in Reload button refreshes the data, and the brand is hard-coded into the artifact so there is no brand picker.

## Workflow

### Step 1 — Capture brand + tenant

Ask the user (if not already specified):
- **Brand** — exactly as it appears in Kapoq (`Brand` column in `AdvertisingCampaignData` and `TotalSales`, or `Partner` in `SellerSales` / `VendorSales`).
- **Tenant** — which Kapoq Datalink connector to use. If only one Kapoq `run_query` MCP tool is visible in the session, use it. If multiple tenants are connected, ask which one. If the tools are deferred (Claude.ai / Cowork pattern), call `ToolSearch` with queries like `"kapoq"`, `"run_query"`, or the tenant name to load the schema before invoking.

If you do not yet know the exact fully-qualified MCP tool name, this is the most important thing to nail down — the artifact's HTML embeds that exact name in its JavaScript so it can call `window.cowork.callMcpTool(<tool-name>, {sql: ...})` at load time. Get it wrong and the artifact will silently fail in the user's browser. Capture it verbatim, e.g. `mcp__<tenant-uuid>__run_query` (the live tool name from this session — do not hard-code a UUID from another tenant).

### Step 2 — Probe before you build

Before writing the artifact, run one or two small probe queries through the actual `run_query` tool to confirm:

1. The tool is reachable and returns rows for this brand
2. The latest `Date` in `TotalSales` (this becomes `LATEST_DATE`, the sales anchor) **and**
   the latest `Date` in `AdvertisingCampaignData` (this becomes `AD_LATEST`, the ad anchor).
   They differ: ad data commonly lags sales by days to weeks, so the two anchors are probed
   and baked separately. Never anchor ad windows to the sales date.
3. The brand uses `Brand` vs `Partner` consistently across the tables you plan to query
4. Whether the brand is Seller, Vendor, or both (probe both `SellerSales` and `VendorSales`)

```sql
-- Probe A: latest sales date + sales presence
SELECT max(Date) AS latest, count() AS rows
FROM TotalSales
WHERE Brand = '{BRAND}'
  AND Date >= today() - 7
```

```sql
-- Probe A2: latest AD date (anchors the ad windows; usually lags Probe A)
SELECT max(Date) AS ad_latest
FROM AdvertisingCampaignData
WHERE Brand = '{BRAND}' AND IsBrandActive = 1 AND IsAccountActive = 1
```

```sql
-- Probe B: channel detection
SELECT 'seller' AS ch, count() FROM SellerSales
WHERE Partner = '{BRAND}' AND ReportingDate >= today() - 30
UNION ALL
SELECT 'vendor', count() FROM VendorSales
WHERE Partner = '{BRAND}' AND ReportingDate >= today() - 30
```

**Resolve exactly one channel.** If only `SellerSales` *or* `VendorSales` has rows, that is the channel. If **both** have rows (a hybrid brand), break the tie deterministically with Probe B2 below: choose the channel with the greater trailing-30-day sales; on a tie, **vendor wins**. Set `IS_SELLER` / `IS_VENDOR` so that **exactly one is true — never both**. This single decision drives the pasted query variant *and* the inventory render, so make it once, here.

```sql
-- Probe B2: hybrid-brand tiebreak — trailing-30 sales by channel
SELECT 'seller' AS ch, sum(ProductSales) AS sales FROM SellerSales
WHERE Partner = '{BRAND}' AND ReportingDate >= today() - 30
UNION ALL
SELECT 'vendor', sum(VendorTotalSales) FROM VendorSales
WHERE Partner = '{BRAND}' AND ReportingDate >= today() - 30
```

Record the response shape — the artifact's JS will need to parse the same shape `run_query` returns in this session. Different Kapoq MCP wrappers return slightly different envelopes (often `{ "rows": [...] }` or a raw array). Look at what you actually got back before writing the parser.

### Step 3 — Decide the date window

The artifact needs two families of three windows. Compute them in JS from the data's edge, not the user's wall clock. Sales/traffic/inventory queries use the sales anchor `LATEST_DATE`; advertising queries use the ad anchor `AD_LATEST`:

Sales family (anchor `LATEST_DATE`):
- **Current 30**: `[LATEST - 29, LATEST]`
- **Prior 30**: `[LATEST - 59, LATEST - 30]`
- **YoY 30**: `[LATEST - 365 - 29, LATEST - 365]`

Ad family (anchor `AD_LATEST`), same shape:
- **Current 30**: `[AD_LATEST - 29, AD_LATEST]`
- **Prior 30**: `[AD_LATEST - 59, AD_LATEST - 30]`
- **YoY 30**: `[AD_LATEST - 365 - 29, AD_LATEST - 365]`

Anchoring ad windows separately keeps every ad window a complete 30 days, so current ACoS / TACoS / ROAS stay comparable to prior and YoY instead of being understated by the unfilled tail. (TACoS for the ad KPIs uses total sales over the *ad* window so numerator and denominator align — see query 1B.)

Bake both `LATEST_DATE` and `AD_LATEST` into the artifact as JS constants. The Reload button re-probes both, so each window family slides forward independently as new data arrives.

### Step 4 — Write the queries

The skill ships a per-page query pack in `references/queries.md`. Read it. Each query is parameterised by `{BRAND}` and the date windows: sales queries use `{CUR_START}` `{CUR_END}` `{PRI_START}` `{PRI_END}` `{YOY_START}` `{YOY_END}`; advertising queries use the `{AD_CUR_START}` … `{AD_YOY_END}` variants. The artifact's JS does straight string substitution (the shell's `fill()` already substitutes both families) before calling `run_query`.

Critical conventions to internalize before you tweak any query:

- **ClickHouse error 184**: never wrap an aggregate (`sum`, `count`, `avg`) in `round()` / `nullif()` / `if()` inside a `SELECT` with `GROUP BY`. Always aggregate in a CTE, then do the math in the outer SELECT. This is the single most common query failure on Kapoq.
- **Brand column casing varies by table.** `Brand` in `AdvertisingCampaignData`, `AdvertisingAdData`, `AdvertisingSearchTermData`, `TotalSales`. `Partner` in `SellerSales`, `VendorSales`, `SellerTraffic`, `VendorTraffic`, `AsinFbaInventory`. Get this wrong and your query silently returns zero rows.
- **ASIN column casing varies too.** `Asin` (mixed) in `AdvertisingAdData` / `AdvertisingProductData`. `ASIN` (caps) in everything else. The artifact's table-render code needs to handle both.
- **Always filter `IsBrandActive = 1 AND IsAccountActive = 1`** on advertising tables. Without this, you pull spend from accounts the brand no longer belongs to.
- **Use `TotalSales` for unified sales** unless you specifically need seller-only or vendor-only columns. `TotalSales.Brand` and `TotalSales.Date` are the cleanest joins.
- **Inventory:** `AsinFbaInventory` for seller (`Partner` col, `AfnFulfillableQuantity` / `ReservedCustomerOrders` / `AfnInboundShippedQuantity`); `VendorInventory` for vendor. Probe both for hybrid brands. **Both have one row per ASIN per account/marketplace** — the shipped queries aggregate the inventory CTE to one row per ASIN (`sum(...) GROUP BY ASIN`). Keep that aggregation; removing it fans out the join into duplicate ASIN rows and wrong weeks-of-cover.
- **CampaignType is normalized in the shell, not the query.** Queries 2A/2B return the raw `CampaignType` string ("Sponsored Product", "Sponsored Brand", …); the shell's pinned `normType()` maps these to SP / SB / SBV / SD at render time. Don't add a second normalizer.
- **Conversion is already a percentage.** Query 3-S returns `conv` as `units / sessions * 100`; the shell renders it with `fmt.pct(c)` directly. Do not multiply by 100 again.
- **Seller vs vendor is pinned to one resolved channel, not improvised.** Use the single channel resolved in Step 2 (including the hybrid tiebreak). `IS_SELLER` and `IS_VENDOR` must be mutually exclusive — exactly one true. Paste the matching variant verbatim: **seller → 1D / 3-S / 5A**, **vendor → 1D-V / 3-V / 5B**. Do **not** hand-edit the seller query to target a vendor brand; the table and column names differ (e.g. `VendorSales.VendorTotalSales` on `ReportingDate`, `VendorInventory` pinned to its latest `ReportingDate`), and getting them wrong fails silently. The inventory render is driven **solely** by `IS_VENDOR`: pasting seller query 5A while `IS_VENDOR=true` makes the render read `r.open_po`/`r.unsellable` (which 5A does not return) and the Open POs / Unsellable columns silently blank — so the pasted variant and the channel booleans must come from the *same* Step-2 decision.

- **Table queries are capped at 25 rows.** The Cowork artifact bridge truncates large tool responses, and a truncated payload is unparseable — the tab then renders blank. The shipped per-ASIN (3-S/3-V) and inventory (5A/5B) queries therefore `LIMIT 25` and select only the columns the table renders (product names truncated with `substring(name,1,30)`). Keep them lean; do not raise the limit or re-add wide columns, or the response can exceed the bridge cap and the tab goes blank.

### Step 5 — Build the artifact

Read `assets/dashboard_shell.html` for the layout, CSS, and JS scaffolding. The shell already has:

- A tab strip with the five page names
- Light-mode CSS using the Cowork-friendly palette
- A `runQuery(sql)` helper that calls `window.cowork.callMcpTool(MCP_TOOL_NAME, {sql})` with caching. Its `normalizeRows()` already unwraps the Kapoq Datalink `{ "result": "<json string>" }` envelope (as well as raw arrays, `{rows}`, `{data}`, and `{content[]}`) — do not strip that branch, or every tab renders blank. The shell also surfaces load failures: if a query errors or its response is truncated, `runQuery` throws and the tab shows the message instead of a silent empty table.
- Pinned, integrity-checked CDN tags for Chart.js (4.5.0) and Grid.js (5.0.2) — the exact versions the Cowork artifact sandbox allowlists. Do not downgrade or unpin them, or the libraries are blocked and charts/tables fail to load.
- A `kpiCard(label, current, prior, yoy, format)` helper that renders the KPI cards
- A `renderChart(canvas, config)` helper around Chart.js
- A `renderTable(container, columns, rows)` helper around Grid.js
- A channel-aware Inventory render: seller shows Reserved / Inbound (query 5A); vendor shows Open POs / Unsellable (query 5B). Driven by `IS_VENDOR`; do not hard-code seller columns.

Your job is to:

1. Substitute these constants in the CONFIG block at the top of the `<script>` (each is a `__TOKEN__` placeholder — replace the token, not the surrounding code):
   - `BRAND` — the brand name, e.g. `"Example Brand"`
   - `MCP_TOOL_NAME` — the fully-qualified Datalink tool name, verbatim, e.g. `"mcp__<tenant-uuid>__run_query"`
   - `LATEST_DATE` — the latest `TotalSales` date as `YYYY-MM-DD` (sales anchor)
   - `AD_LATEST_DATE` — the latest `AdvertisingCampaignData` date as `YYYY-MM-DD` (ad anchor; usually lags sales)
   - `IS_SELLER` / `IS_VENDOR` — booleans from your channel probe, e.g. `true` / `false`

   These `__TOKEN__` placeholders live only in the CONFIG block. The shell's top-of-file comment is a neutral, ship-safe artifact header — it carries no placeholders and needs no editing; leave it as is.
2. Paste the queries from `references/queries.md` into the five tab loaders. The shell's `fill()` substitutes both window families, so paste each query verbatim — do not hand-edit date placeholders.
3. Save the populated HTML to the session's outputs directory as `<brand-slug>-daily-check.html`, where `<brand-slug>` is the brand lowercased, with every run of non-alphanumeric characters replaced by a single hyphen and leading/trailing hyphens trimmed (e.g. `Coco Shores` -> `coco-shores`, `Palma Verde & Co.` -> `palma-verde-co`). Use the same slug for the artifact id so it is stable across rebuilds.
4. Call `mcp__cowork__create_artifact` with:
   - `id`: `<brand-slug>-daily-check`
   - `html_path`: the absolute file path
   - `mcp_tools`: a list containing only the Datalink `run_query` tool name (no others)
   - `description`: one sentence about what the artifact shows

You are creating a **single artifact per brand**. If the user later asks for the same dashboard for a different brand, that's a second artifact — don't try to make one artifact serve both.

### Step 6 — Confirm and brief

After `create_artifact` returns, tell the user:
- That the dashboard is open in the sidebar and will refresh whenever they click Reload
- The window is anchored to `LATEST_DATE` so YoY comparisons land on the same data shape
- One headline finding from the data you already saw — pick the biggest mover from Step 2 if you have it, otherwise a quick sentence on what they should look at first

Do not narrate the whole dashboard. The user can see it.

## Design principles for the artifact

The artifact lives in the user's sidebar and gets re-opened — that changes a few defaults:

- **Cache aggressively on load.** The `runQuery` helper caches by SQL string. Reload busts the cache. The user should never wait for the same query twice in one session.
- **One page = one fetch ideally, three fetches max.** Each tab's data should land in a handful of round-trips. Combine queries via `UNION ALL` with a `src` discriminator column (see the WBR skill's Query A pattern) rather than firing 10 small queries per page.
- **Render skeletons immediately.** When a tab is clicked, show the chart frames and table containers with a "loading…" state before the data lands. Don't make the user stare at a blank tab.
- **Number formatting matters more than chart prettiness.** `$1,234,567`, `2.84x` ROAS, `12.3%` ACoS, `+18.4%` deltas with green / red colour. Users glance for 10 seconds — get the numbers right and the typography legible.
- **Deltas matter more than absolute values.** Every KPI card and table row should show prior-period and YoY deltas alongside the current number. A user opening the dashboard already knows roughly what their sales should be — they want to know what changed.
- **YoY can be missing.** If the brand didn't exist a year ago, YoY queries return zero rows. Show `—` not `0%`. The artifact must handle this without crashing.

## Environment and portability

This skill produces a **Cowork-native artifact by design**. The whole point is a page the user re-opens each morning that re-queries Kapoq live, so the artifact fetches data client-side via `window.cowork.callMcpTool(MCP_TOOL_NAME, {sql})` and registers itself with `mcp__cowork__create_artifact`. Both dependencies are intentional and permanent — `window.cowork` is **not** a bug to "fix." The trade-off is that the file only runs inside claude.ai / Cowork; opened as a standalone file every tab shows the `runQuery` error box, because a live-refreshing dashboard inherently needs a runtime data bridge.

If a portable, point-in-time snapshot is ever needed (e.g. to email a static copy), that is a **separate output mode** — run the queries at build time and bake the results into the HTML (as `search-term-dashboard` / `weekly-report` do) — and is out of scope for this skill's live-artifact deliverable.

## Triggering — when to fire this skill vs. others

- Fires on phrases that imply *recurring* inspection: "daily check", "morning check", "live view", "command center", "cockpit", "page I keep open for [brand]", "something I can look at each morning".
- Does **not** fire for one-off audits or reports — those go to `amazon-account-audit` (deep dive, HTML report) or `wbr` (weekly PPTX deliverable).
- Does **not** fire for narrow questions like "what was Brand X's ACoS yesterday" — answer those directly in chat. Only fire when the user wants something persistent.

## Quick reference

- Query templates → `references/queries.md`
- Artifact shell → `assets/dashboard_shell.html`
- Datalink schema cheat-sheet → `references/datalink-schema.md`

## Trigger examples

- "Build me a live view of [Brand A] I can check every morning."
- "Set up a daily dashboard for [Brand B] — sales, ads, inventory, the whole picture."
- "Give me a command center for [Brand C], I want to re-open it each day."
- "I want a page I can keep open for Brand X so I can keep an eye on it."
- "Make me a morning check for [brand]."
