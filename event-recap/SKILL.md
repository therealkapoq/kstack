---
name: event-recap
description: "Generate an interactive HTML year-over-year RECAP dashboard for any Amazon sales event — Prime Day, Prime Big Deal Days, Spring Sale, Black Friday/Cyber Monday — for any brand/account in any Kapoq tenant. Compares the current-year event vs the prior-year event day-by-day: sales, traffic, conversion, ad spend, CPCs, top/bottom keywords, product movers, new-to-brand, deals, and lift over baseline, with an auto-generated exec summary and next-time takeaways. Brand-agnostic, seller-or-vendor aware; works mid-event (Day-1 like-for-like basis) or after close (full-event basis), hiding sections when data is absent. Use whenever the user asks for a Prime Day recap/review/post-mortem/teardown, an event YoY comparison, 'how did Prime Day go vs last year', a Big Deal Days or Black Friday recap, or 'compare this year's event to last year' for any Amazon brand — even without the word 'dashboard'. Distinct from weekly-report/wbr and monthly-exec-summary/qbr: this is the event-anchored YoY retrospective."
---

# Event Recap Skill

Generate an **interactive HTML year-over-year recap** for one Amazon sales event (Prime Day, Prime Big Deal Days, Spring Sale, BFCM, or any event window you give it), for any brand in any Kapoq tenant. Output is a single self-contained HTML file with tabs: Overview (exec summary + KPIs + quality signals + event arc), Day by day, Lift & baseline, Keywords, Products, Deals (sellers), and Ad breakdown.

It is a **retrospective**, not a live monitor: it frames the read around the window that is *complete on both sides*. Mid-event, that's a Day-1 (or first-N-days) like-for-like; after the event closes, it's the full event.

## Workflow overview

This skill is a **hybrid**: this markdown guides discovery (which tenant, account, channel, event windows, what data exists), Claude runs the queries via the tenant's Kapoq MCP `run_query` tool and writes a single `context.json`, then a deterministic Python builder turns that into the dashboard. All the math and narrative live in the template's JS, so output is **byte-identical run-to-run** for the same `context.json`.

```
┌ Step 1 ┐ ┌ Step 2 ─┐ ┌ Step 3 ─┐ ┌ Step 4 ┐ ┌ Step 4.5 ┐ ┌ Step 5 ┐
│ Params │→│ Resolve │→│ Run all │→│ Build  │→│ Validate │→│Present │
│ event +│ │ account,│ │ queries,│ │ via    │ │(headless │ │to user │
│ brand  │ │ channel,│ │ write   │ │ script │ │ smoke)   │ │        │
│        │ │ windows │ │context  │ │        │ │          │ │        │
└────────┘ └─────────┘ └─────────┘ └────────┘ └──────────┘ └────────┘
```

---

## Environment and paths (read first)

This skill is written for the claude.ai web sandbox, so steps reference `/mnt/...` paths and the `present_files` tool. **Those are placeholders.** Resolve them once for the current environment and use the resolved values throughout. The Python builder is fully portable (every path is a CLI arg) — nothing in it needs editing.

| Placeholder in this doc | claude.ai web sandbox | Claude Code / local CLI |
|---|---|---|
| **Skill base dir** | `/mnt/skills/user/event-recap/` | the directory this `SKILL.md` lives in |
| **Work dir** (scratch for `context.json`) | `/home/claude/` | a fresh temp dir, e.g. `mktemp -d` |
| **Output dir** (final HTML) | `/mnt/user-data/outputs/` | a local dir you control (create it) |
| **Present the result** | `present_files` | state the absolute path and offer to open it |

---

## Step 1 — Gather parameters

If the user didn't provide them, ask for:

- **Brand and/or account** — the brand name and (if known) the exact account, e.g. `Brand X@Amazon US`.
- **Tenant** — which connected Kapoq MCP server the account lives in (e.g. Tenant A, Tenant B). The MCP tool naming differs per tenant — each has its own `{Tenant} Kapoq:run_query`. If a brand is named but not a tenant, **ask** (or check connected servers).
- **Event + year** — e.g. "Prime Day 2026 vs 2025". Look up the windows in `config/events.json`, but **always confirm dates against the data** (Amazon shifts them — Prime Day 2026 moved to late June). The prior-year event falls on different calendar dates, so resolve both windows explicitly.

The current-year event may be **in progress**. That's fine and expected — the skill handles it.

---

## Step 2 — Resolve account, channel, windows, freshness

**2A — Pick the tenant tool.** Confirm the right `{Tenant} Kapoq:run_query` is loaded. On Claude web/desktop these are deferred — if it's not visible, call `tool_search` with the tenant name (and "run query") before concluding it's missing.

**2B — Resolve the account and channel.** Find the exact account string and whether it's **Seller** or **Vendor** — this decides which tables to use.

```sql
SELECT Account, count(*) AS rows FROM TotalSales
WHERE Account ILIKE '%{BRAND}%' GROUP BY Account ORDER BY rows DESC
```

If `TotalSales` has rows for the account → **seller** (use `TotalSales`, `SellerTraffic`, `PromotionPerformance`). If not, check `VendorSales` → **vendor** (use `VendorSales`; **no** `SellerTraffic`, **no** `PromotionPerformance`/`CouponPerformance` — those are seller-only). When picking among accounts, prefer the one with ad history covering the prior-year event (below).

**2C — Confirm both event windows have data, and pick the account with prior-year ad history.** The realtime ad tables carry full history *and* recent provisional days, so use them for every ad pull:

```sql
SELECT min(Date) AS first_ad, max(Date) AS last_ad
FROM AdvertisingCampaignDataRealtime
WHERE Account = '{ACCOUNT}' AND Date > '2000-01-01'
```

The `AND Date > '2000-01-01'` is required: the realtime ad table carries an epoch-sentinel row dated `1970-01-01`, and without the guard `min(Date)` returns that junk value, which trivially (and falsely) satisfies the "first_ad on/before the prior-year event" coverage check.

`first_ad` must be on/before the prior-year event start, or there's nothing to compare against (this is exactly why some tenants/accounts won't work — their ad history starts after last year's event). `last_ad` tells you how fresh the current event data is.

**2D — Determine day completeness.** For the current-year window, a day is **complete** if it has fully elapsed and landed; the in-progress day is **partial**; future days have no data. Check the latest landed day:

```sql
SELECT max(Date) AS sales_through FROM TotalSales WHERE Account = '{ACCOUNT}';
SELECT max(Date) AS ads_through  FROM AdvertisingCampaignDataRealtime WHERE Account = '{ACCOUNT}'
```

**The completeness rule is purely calendar-based — do not use `IsProvisional`.** A CY event day is complete **iff its calendar date is strictly before today (the in-progress day)**, regardless of whether the ad rows are provisional Marketing-Stream rows. A provisional-but-elapsed day (e.g. yesterday, still `IsProvisional=1`) counts as **complete**; today is **partial**; future dates have no data. Reading "complete" as `IsProvisional=0` would wrongly drop the most recent elapsed day and shift the whole basis (K), so don't.

To remove this from judgment entirely, also pass the CY day **ISO dates** as `cy_dates` (e.g. `["2026-06-23",...]`) and a `generated` date (today). When `cy_dates` is present the builder computes `cy_complete` itself as `date < generated` — the single deterministic source of truth for K. Still set `cy_complete[]` yourself as a fallback for builders/contexts without `cy_dates`: true for days strictly before today, false for the in-progress/future days. (After the event closes, all are true → full-event basis.)

**2E — Baseline windows.** Take the `baseline_days` (default 3) ending `baseline_gap_days` (default 1) before each event start. Example for Prime Day: 2026 baseline = Jun 20–22, 2025 baseline = Jul 5–7. These establish the pre-event daily run-rate for the lift analysis.

Record everything for the `context.json` (see the schema at the end).

---

## Step 3 — Run the queries, write `context.json`

Run these against the tenant's `run_query`. Use **`AdvertisingCampaignDataRealtime`** and **`AdvertisingTargetDataRealtime`** for ads (full history + provisional recent days) — **sum all rows, do not filter on a provisional flag**. Replace `{ACCOUNT}`, `{CY_START}`, `{CY_END}`, `{PY_START}`, `{PY_END}` etc. Always `describe_table` first to confirm column casing (`ASIN` in `TotalSales`, `Asin` in ad tables).

**Per-day ad metrics** (run for both the CY and PY windows; map each day into `daily.{cy,py}.ad[]` as `{impr,clk,cost,adsales,orders,ntbO,ntbS}`):

```sql
SELECT Date,
  sum(Impressions) AS impr, sum(Clicks) AS clk, sum(Cost) AS cost,
  sum(AdSales) AS adsales, sum(Orders) AS orders,
  sum(OrdersNewToBrand) AS ntbO, sum(SalesNewToBrand) AS ntbS
FROM AdvertisingCampaignDataRealtime
WHERE Account = '{ACCOUNT}' AND Date BETWEEN '{CY_START}' AND '{CY_END}'
GROUP BY Date ORDER BY Date
```

> **Ad column names are canonical, not placeholders.** Use `Impressions`, `Clicks`, `Cost`, `AdSales`, `Orders`, `OrdersNewToBrand`, `SalesNewToBrand`, and `CampaignType` (campaign-type split) — these are the real Kapoq Datalink columns, standardized across tenants. There is **no** `AdOrders`, `NewToBrandOrders`, `NewToBrandSales`, or `AdType` column; do not use those. Still run `describe_table` to confirm casing, but the names here are authoritative.

**Per-day total sales + units** (seller: `TotalSales`; vendor: `VendorSales` — confirm the revenue/units column names via `describe_table`, vendor uses ordered revenue):

```sql
SELECT Date, sum(TotalSales) AS sales, sum(TotalQuantity) AS units
FROM TotalSales
WHERE Account = '{ACCOUNT}' AND Date BETWEEN '{CY_START}' AND '{CY_END}'
GROUP BY Date ORDER BY Date
```

**Per-day traffic** (seller only — `SellerTraffic`; it lags ~2 days, so the current event often has no sessions yet → leave `sessions`/`pv` null and the section self-labels). Vendors: skip entirely. Always set `traffic_fresh_through` to `SELECT max(ReportingDate) FROM SellerTraffic WHERE Account = '{ACCOUNT}'` (a single deterministic value) — do not leave it null when CY sessions are absent and do not improvise the date, or the traffic-pending note drifts between runs.

```sql
SELECT ReportingDate AS Date, sum(Sessions) AS sessions, sum(PageViews) AS pv
FROM SellerTraffic
WHERE Account = '{ACCOUNT}' AND ReportingDate BETWEEN '{CY_START}' AND '{CY_END}'
GROUP BY ReportingDate ORDER BY ReportingDate
```

**Baseline** (run for both baseline windows → `baseline.{cy,py}` arrays of per-day `sales`, `units`, `adsales`). Pull daily sales/units from the sales table and daily `adsales` from the realtime ad table over the baseline window.

**Keywords** (keyword-targeted spend in each event window → `keywords.{cy,py}`, up to `keyword_top_n`). Use the realtime target table with `IsKeyword = 1`:

```sql
WITH agg AS (
  SELECT Target AS t, min(MatchType) AS mt,
    sum(Impressions) AS impr, sum(Clicks) AS clk, sum(Cost) AS cost,
    sum(AdSales) AS sales, sum(Orders) AS orders
  FROM AdvertisingTargetDataRealtime
  WHERE Account = '{ACCOUNT}' AND IsKeyword = 1
    AND Date BETWEEN '{CY_START}' AND '{CY_END}'
  GROUP BY Target
)
SELECT t, mt, impr, clk, cost, sales, orders FROM agg
ORDER BY cost DESC, t ASC LIMIT 50
```

Use `min(MatchType)`, never `any(MatchType)` — a keyword that ran under more than one match type makes `any()` return a different value each run (it is non-deterministic in ClickHouse), flipping the displayed match type between runs. The `, t ASC` tiebreaker in the ORDER BY pins the row order when two keywords have equal spend. Run the same query for the PY window.

**Names — resolve once, centrally (→ `names`).** Build a single ASIN→ProductName map and pass it as `names` (a `{asin: ProductName}` object) in `context.json`. Run this **after** you have the movers, products, and deals ASIN sets, and scope it to **exactly that union** (movers ∪ products ∪ both windows' deal ASINs) — `{EVENT_ASINS}` below is that explicit list:

```sql
SELECT ASIN AS asin, min(ProductName) AS name
FROM AsinReference WHERE Account = '{ACCOUNT}' AND ASIN IN ({EVENT_ASINS})
GROUP BY ASIN
```

**Scope it — do not run it account-wide.** A `WHERE Account = '{ACCOUNT}'` query with no `ASIN IN (...)` returns the full catalog and on a large account **overflows the MCP response token cap**, forcing each run to down-scope the map by hand — and runs scope it differently (one review had a run drop the deal-only ASINs, so its Deals tab rendered bare ASINs while other runs rendered titles). The `ASIN IN ({EVENT_ASINS})` form both prevents the overflow and guarantees every row the builder looks up — including **deal-only ASINs that are not in movers/products** — resolves to a title. The deal ASIN set must be included here even though the inventory query's `{EVENT_ASINS}` stays movers+products (the builder never looks up deal ASINs in inventory, but it does for names).

The builder applies this map as the **single source of display names** across movers, products, and deals — overriding each row's `name` from the map. This guarantees the same ASIN never renders as a title in one run and a bare ASIN in another. You may still set `name` on each row as a fallback, but do **not** rely on per-query name joins as the source of truth, and never substitute the bare ASIN for a missing name yourself — leave that to the builder's `coalesce`.

**Product movers — Day 1** (CY day-1 vs PY day-1, by ASIN → `movers[]`). Pull each side's day-1 sales+units by ASIN from the sales table, join `AsinReference` for `ProductName`, merge on ASIN into `{asin,name,sales26,units26,sales25,units25}` (keep the `25`/`26` key names — they're the template's prior/current slots regardless of actual years), ordered by current-year sales descending, then by `asin` ascending to break ties deterministically.

> **`name` must be the raw `ProductName` verbatim.** Pass the exact string `AsinReference.ProductName` returns — do **not** paraphrase, shorten, truncate, or "clean" the title. The builder clips it to a fixed length for display, so a verbatim title renders identically every run; a hand-shortened one does not (this was a run-to-run divergence source).

Use **this exact query shape** for movers — do not hand-roll a `FULL OUTER JOIN`. A `FULL OUTER JOIN ... USING(ASIN)` that selects one side's key turns rows present on only one year into blank-ASIN rows, which then get dropped — silently losing real products (typically the YoY *losers*, which is exactly what this view should surface). The `UNION ALL` + `GROUP BY` pattern below keeps every ASIN from either side. Set `{D1_CY}` / `{D1_PY}` to each side's day-1 date:

```sql
WITH cy AS (
  SELECT ASIN AS asin, sum(TotalSales) AS sales26, sum(TotalQuantity) AS units26
  FROM TotalSales WHERE Account = '{ACCOUNT}' AND Date = '{D1_CY}' AND ASIN != '' GROUP BY ASIN
),
py AS (
  SELECT ASIN AS asin, sum(TotalSales) AS sales25, sum(TotalQuantity) AS units25
  FROM TotalSales WHERE Account = '{ACCOUNT}' AND Date = '{D1_PY}' AND ASIN != '' GROUP BY ASIN
),
u AS (
  SELECT asin, sum(sales26) AS sales26, sum(units26) AS units26, sum(sales25) AS sales25, sum(units25) AS units25
  FROM ( SELECT asin, sales26, units26, 0 AS sales25, 0 AS units25 FROM cy
         UNION ALL
         SELECT asin, 0, 0, sales25, units25 FROM py ) GROUP BY asin
),
nm AS ( SELECT ASIN AS asin, min(ProductName) AS name FROM AsinReference WHERE Account = '{ACCOUNT}' GROUP BY ASIN )
SELECT u.asin AS asin, coalesce(nm.name, u.asin) AS name, u.sales26, u.units26, u.sales25, u.units25
FROM u LEFT JOIN nm USING (asin)
ORDER BY u.sales26 DESC, u.asin ASC
LIMIT {product_top_n}
```

Cap movers at `product_top_n` (the same default as products — there is no separate movers cap). The builder also enforces this cap as a safety net, so the count is pinned even if the `LIMIT` is omitted.

(`ASIN != ''` drops junk keys; `coalesce(nm.name, u.asin)` gives a deterministic fallback when a name is missing — never improvise a cross-account `any()` lookup, which is itself non-deterministic. `min(ProductName)` keeps the name pick deterministic.)

**Products — event-to-date** (CY elapsed window vs PY full event, by ASIN → `products[]`, up to `product_top_n`). **Identical query to movers**, but each side aggregates over its full window instead of a single day — change `Date = '{D1_CY}'` to `Date BETWEEN '{CY_START}' AND '{CY_THROUGH}'` and `Date = '{D1_PY}'` to `Date BETWEEN '{PY_START}' AND '{PY_END}'`. **`CY_THROUGH` is the last COMPLETE CY day — the in-progress partial day is excluded** (so the event-to-date window matches the K-day basis exactly and never silently includes a half-landed day). For example with `cy_complete=[true,true,false,false]`, `CY_THROUGH` is the 2nd day, not the 3rd. Keep the same `ASIN != ''` filter, `coalesce` name fallback, and `min(ProductName)`, but order by **`ORDER BY greatest(sales26, sales25) DESC, asin ASC`** (not `sales26` alone). Ordering by the larger of the two years keeps a product that was big *last* year but collapsed this year in the list — otherwise it drops out of the top-N and the inventory layer never sees it, which is exactly the out-of-stock hero case the Stock-watch exists to catch. Take the top `product_top_n` rows. Same rule: `name` is the verbatim `ProductName`.

**Campaign-type split** (→ `ctype[]`, one row per CampaignType for both windows):

```sql
WITH cy AS (
  SELECT CampaignType AS type, sum(Cost) AS cost26, sum(AdSales) AS sales26, sum(Clicks) AS clk26
  FROM AdvertisingCampaignDataRealtime
  WHERE Account = '{ACCOUNT}' AND Date BETWEEN '{CY_START}' AND '{CY_END}' GROUP BY CampaignType
),
py AS (
  SELECT CampaignType AS type, sum(Cost) AS cost25, sum(AdSales) AS sales25, sum(Clicks) AS clk25
  FROM AdvertisingCampaignDataRealtime
  WHERE Account = '{ACCOUNT}' AND Date BETWEEN '{PY_START}' AND '{PY_END}' GROUP BY CampaignType
)
SELECT type, cost25, sales25, clk25, cost26, sales26, clk26
FROM cy FULL OUTER JOIN py USING (type)
```

**DSP — optional (→ `dsp.{cy,py}`).** Most brands run no DSP, so this is conditional: include it only when there is spend. First probe both windows in one query:

```sql
SELECT sum(TotalCost) FROM AdvertisingDspStatisticsByReportDateAndLineItem
WHERE Account = '{ACCOUNT}' AND Date BETWEEN '{PY_START}' AND '{CY_END}'
```

If that is zero/empty, **omit `dsp` from `context.json` entirely** — the builder leaves `showDsp` false and the DSP card never renders (same hide-when-absent pattern as Deals and Traffic). If there is spend, pull per-day DSP for each window and map into `dsp.{cy,py}` aligned to the event days (same shape/length as `daily.*.ad`):

```sql
SELECT Date,
  sum(TotalCost) AS cost, sum(Impressions) AS impr, sum(Clicks) AS clk,
  sum(TotalSales14d) AS sales, sum(TotalPurchases14d) AS orders,
  sum(NewToBrandPurchases14d) AS ntbO
FROM AdvertisingDspStatisticsByReportDateAndLineItem
WHERE Account = '{ACCOUNT}' AND Date BETWEEN '{CY_START}' AND '{CY_END}'
GROUP BY Date ORDER BY Date
```

DSP is account/brand-level (not per-ASIN) and uses **14-day attribution** (`TotalSales14d` includes brand-halo across all products), a different basis than Sponsored `AdSales`. The builder rolls `dsp` up to the comparable first-K-days window on both sides and renders a standalone DSP card (spend, 14d sales, ACoS, ROAS, CPM, new-to-brand share) — it is deliberately **not** blended into the Sponsored ACoS. Works for sellers and vendors alike (DSP is account-scoped). Note: in some tenants the DSP table lags the sponsored tables, so confirm its `max(Date)` covers the event before relying on it.

**Deals — sellers only** (`PromotionPerformance` → `deals.py[]` / `deals.cy[]`). Use **this exact per-ASIN query for each window** — do not hand-roll the scope or cap (a prior review found three runs each inventing a different cap and a different shape: top-15-by-revenue vs top-14 vs a single summary row). One row per ASIN, scoped to the event window, ordered, capped:

```sql
SELECT ASIN AS asin,
  min(PromotionType) AS type,
  toDate(min(PromotionStartDate)) AS start, toDate(max(PromotionEndDate)) AS end,
  sum(ProductUnitsSold) AS units, sum(ProductRevenue) AS revenue, sum(ProductGlanceViews) AS glance
FROM PromotionPerformance
WHERE Account = '{ACCOUNT}'
  AND toDate(PromotionStartDate) <= '{WIN_END}' AND toDate(PromotionEndDate) >= '{WIN_START}'
GROUP BY ASIN
ORDER BY revenue DESC, units DESC, asin ASC
LIMIT {deal_top_n}
```

Run it once with `{WIN_START}`/`{WIN_END}` = the PY window and once = the CY window. Pass `start`/`end` as the ISO date strings the query returns (do **not** pre-format a `window` label — the builder derives it). For each deal's `name`, leave it to the **`names` map** (the builder resolves names centrally; you may also set `name` = verbatim `AsinReference.ProductName` as a fallback). Pass the raw `type` (e.g. `BEST_DEAL`) or a `py_type_label`/`cy_type_label` — **the builder canonicalizes it** ("Best Deals" / "Lightning Deals"), so do not worry about plural/singular. Write `insight` and `takeaway` prose (the card heading is fixed by the builder; do not author `insight_head`).

**Finalized vs pending is decided by the data, not by the year — never emit a synthetic summary row.** Always pass the real per-ASIN rows the query returns. If the rows carry units/revenue (even mid-event — Best Deals increasingly report live), the builder renders them as a finalized per-ASIN table ordered by revenue. If **no** row has metrics yet, the builder renders them as pending. Do **not** collapse a multi-ASIN deal into one `asin:""` "Best Deal — N ASINs" summary row — that was a per-run divergence; the per-ASIN rows + the `deal_top_n` cap handle it deterministically.

**Omit `deals` entirely when there were no promotions.** Vendors always omit it. A **seller that ran zero deals in both windows** must also omit the whole `deals` object — do **not** include an empty `deals` with authored `insight`/`takeaway`/`coupons_note` prose. The Deals tab is hidden either way, but that prose would otherwise be written into the hidden markup and differ run-to-run. (The builder now also drops any deals prose when there are no deal rows, as a safety net — but don't author it in the first place.)

**Coupons probe (pin it).** A prior review caught two runs disagreeing on whether coupons ran (one read rows, another read zero) because they queried `CouponPerformance` differently. Use this exact probe, and `describe_table` to confirm the datetime column names for the tenant (it is `StartDateTime`/`EndDateTime`, **not** `StartDate`):

```sql
SELECT count(*) AS coupons
FROM CouponPerformance
WHERE Account = '{ACCOUNT}'
  AND toDate(StartDateTime) <= '{CY_END}' AND toDate(EndDateTime) >= '{PY_START}'
```

Only write a `coupons_note` when `deals` is present (i.e. promotions ran); for a no-promotions account there is no deals object and no coupon claim to make.

**Inventory — current stock (→ `inventory[]`).** A one-row-per-ASIN snapshot of present availability, used to flag products out of stock and estimate days of cover. **It has no history** — it reflects stock *now*, not stock during the event — so the dashboard frames it as the current read, not proof of a mid-event outage. Sellers use `AsinFbaInventory`:

```sql
SELECT ASIN AS asin,
  sum(coalesce(AfnFulfillableQuantity, 0)) AS avail,
  sum(coalesce(AfnInboundWorkingQuantity,0) + coalesce(AfnInboundShippedQuantity,0)
      + coalesce(AfnInboundReceivingQuantity,0)) AS inbound,
  sum(coalesce(ReservedCustomerOrders, 0)) AS reserved
FROM AsinFbaInventory
WHERE Account = '{ACCOUNT}' AND ASIN != '' AND ASIN IN ({EVENT_ASINS})
GROUP BY ASIN
ORDER BY asin
```

Three things pin this query (a prior review caught all three drifting): **(1)** `GROUP BY ASIN` with `sum(...)` collapses any duplicate ASIN rows into exactly one deterministic row — do **not** dedup by hand or synthesize a `_dup` row; the aggregate handles it. **(2)** `ASIN IN ({EVENT_ASINS})` scopes the pull to exactly the ASINs that appear in `movers`/`products` (the only ASINs the builder ever looks up), so the payload is the same size every run instead of swinging between the relevant subset and the full catalog snapshot. **(3)** `ORDER BY asin` fixes the row order so `context.json` is reproducible (ClickHouse does not guarantee `GROUP BY` return order). Pass the rows through **verbatim** — never invent or alter a row.

Vendors use `VendorInventory` instead (no FBA buckets): map `avail` from the on-hand sellable column (e.g. `SellableOnHandInventoryUnits`) and `inbound` from the open-PO column (e.g. `OpenPurchaseOrderUnits`) — confirm exact names with `describe_table`; keep the same `GROUP BY ASIN` / `ASIN IN ({EVENT_ASINS})` / `ORDER BY asin` shape. Map every row into `inventory[]` as `{asin, avail, inbound, reserved}`. The builder joins this onto movers/products by ASIN, derives a per-product status (out / out·inbound / low / in-stock) and days-of-cover, and writes the exec "Stock watch" callout that names out-of-stock products which sold well last year. `RestockStatus` is a listing-active flag, **not** a stock signal — `avail = 0` is the out-of-stock signal. If the inventory table is empty/unavailable, omit `inventory` and the stock column + callout simply don't render.

After all queries, assemble the **one** `context.json` (schema below) in the work dir. Re-read values out of the JSON when stating figures later — never eyeball intermediate query output.

---

## Step 4 — Build

```bash
python /mnt/skills/user/event-recap/scripts/build_recap.py \
  --context /home/claude/context.json \
  --template /mnt/skills/user/event-recap/assets/template.html \
  --output /mnt/user-data/outputs/{brand-slug}-{event-slug}-recap.html
```

The builder prints the basis (`Day 1`/`first N days`/`full event`), `partial`, the section flags, and the keyword/product counts. It computes completeness, section visibility, labels, and the recap framing; the template does deltas, lift, quality signals, and the auto-narrative.

---

## Step 4.5 — Validate (headless smoke test)

Render the output headless before presenting (jsdom — `npm install jsdom --no-save` if needed). Confirm `errors: 0`, every section id populated, the right tabs present, and no `undefined`/`NaN`/`[object Object]`/`/*__DATA__*/` leaking into visible text. If anything fails, fix `context.json` (or the template) and rebuild — do not present a broken dashboard. A ready-made smoke script lives at the path used during development; reuse the same id list as the section ids in the template.

---

## Step 5 — Present

Use `present_files` with the output path and a short framing line — the dashboard speaks for itself:

> "Here's the {event} {cy_year}-vs-{py_year} recap for {brand}. Basis: {basis} ({why}). {one headline figure}."

Pull the basis and headline figure from the builder's printed summary / the rendered artifact, not from raw query output.

---

## Reading the dashboard

- **Overview** — Bottom-line verdict, KPI cards (rate metrics as pp, dollar/volume as %), quality signals (organic share, NTB rate, ASP, CTR), "what changed" + next-time takeaway, a "Stock watch" callout (shown only when products are out of stock now — names the ones that sold well last year), event-arc chart, event-to-date rollup.
- **Day by day** — Aligned by event day, current over prior; traffic/conversion block (hidden for vendors / when sessions absent).
- **Lift & baseline** — Event-day vs pre-event run-rate, the "bigger starting line vs bigger spike" read.
- **Keywords** — Highest performers and the worst spend-for-return list (bid-cut/negation candidates), per year.
- **Products** — Day-1 movers (clean YoY) and event-to-date levels, each with a current-stock badge (out of stock / out·inbound / low + days-of-cover / in-stock) when inventory data is present.
- **Deals** (sellers) — Prior-year finalized deals, current-year pending, contribution insight.
- **Ad breakdown** — Spend/sales/ACoS/CPC by campaign type, plus an optional DSP card (spend, 14d sales, ACoS, ROAS, CPM, NTB share, CY vs PY) shown only when the brand ran DSP.

---

## Event date reference

`config/events.json` has known US windows for Prime Day, Prime Big Deal Days, Spring Sale, and BFCM (2023–2026) plus defaults (`baseline_days`, `keyword_top_n`, etc.). **Confirm dates against the data** — Amazon announces them yearly and they move. Non-US marketplaces differ.

---

## Troubleshooting

| Issue | Resolution |
|---|---|
| Account's ad history starts after last year's event | No fair comparison exists — pick a different account, or tell the user this account can't be compared YoY for this event (Step 2C) |
| Wrong tenant tools loaded | Each tenant has its own `{Tenant} Kapoq:run_query`. Call `tool_search` with the tenant name if it's not visible — absence from one search isn't proof it's missing |
| ClickHouse error 184 (aggregate in WHERE/HAVING) | Wrap aggregates in a CTE, compute ratios in the outer SELECT; never alias an aggregate the same as the underlying column |
| UNION/ORDER BY fails | Wrap in a subquery: `SELECT * FROM (... UNION ALL ...) ORDER BY col` |
| `Unknown expression identifier` on ASIN | Casing differs by table — `ASIN` in `TotalSales`/`SellerSales`, `Asin` in ad tables. `describe_table` is the source of truth |
| Vendor account | Use `VendorSales`; skip `SellerTraffic`, `PromotionPerformance`, `CouponPerformance`. The builder hides the traffic block and Deals tab automatically |
| Current event has no sessions yet | `SellerTraffic` lags ~2 days — leave `sessions`/`pv` null; the dashboard self-labels traffic as not-yet-landed |
| Current-year deals show no metrics | Best Deals report after the window closes — include them with a `status`; the template renders them as pending |
| Ad totals look low for recent days | Use the **Realtime** ad tables and sum all rows (they include provisional Marketing Stream days); don't filter on a provisional flag |
| Output renders blank | A JS error halted rendering — run the Step 4.5 smoke test; it prints the stack. Usually a `context.json` key the template reads is missing |
| Narrative prose reads slightly off for an unusual brand | The exec summary / takeaways are rule-based from computed signals. They're correct but generic; you may hand-tune the prose in the built HTML before presenting (a deliberate edit, not run-to-run drift) |

---

## context.json schema (what the builder consumes)

```jsonc
{
  "brand": "Brand X", "account": "Brand X@Amazon US", "tenant": "Demo Tenant",
  "channel": "seller",                         // "seller" | "vendor"
  "marketplace": "Amazon US", "currency": "USD", "currency_symbol": "$",
  "event_name": "Prime Day", "cy_year": 2026, "py_year": 2025,
  "cy_day_labels": ["Jun 23","Jun 24","Jun 25","Jun 26"],   // length = event days
  "py_day_labels": ["Jul 8","Jul 9","Jul 10","Jul 11"],     // same length
  "cy_dates": ["2026-06-23","2026-06-24","2026-06-25","2026-06-26"],  // ISO; if present the builder computes cy_complete = date < generated (pins K)
  "cy_complete": [true,false,false,false],     // fallback when cy_dates absent — which CY days are final
  "py_complete": [true,true,true,true],
  "generated": "2026-06-24", "sales_fresh_through": "...", "ad_fresh_through": "...",
  "traffic_fresh_through": "2026-06-22",       // null for vendor
  "cy_baseline_window": "Jun 20–22", "py_baseline_window": "Jul 5–7",   // display strings
  "daily": {
    "cy": { "ad": [ {"impr":..,"clk":..,"cost":..,"adsales":..,"orders":..,"ntbO":..,"ntbS":..}, null, ... ],
            "sales": [..,null,..], "units": [..], "sessions": [null,..], "pv": [null,..] },
    "py": { ... all days filled ... }
  },
  "baseline": { "cy": {"sales":[3],"units":[3],"adsales":[3]}, "py": {"sales":[3],"units":[3],"adsales":[3]} },
  "keywords": { "cy": [ {"t":..,"mt":..,"impr":..,"clk":..,"cost":..,"sales":..,"orders":..} ], "py": [...] },
  "names": { "B0XXXXXXXX": "Full ProductName…", "..": ".." },  // one ASIN->name map; builder applies it to movers/products/deals as the source of display names
  "movers":   [ {"asin":..,"name":..,"sales26":..,"units26":..,"sales25":..,"units25":..} ],   // day-1; name is a fallback — `names` is authoritative; builder clips for display
  "products": [ {"asin":..,"name":..,"sales25":..,"units25":..,"sales26":..,"units26":..} ],   // event-to-date; name = verbatim ProductName
  "ctype":    [ {"type":"Sponsored Products","cost25":..,"sales25":..,"clk25":..,"cost26":..,"sales26":..,"clk26":..} ],
  "inventory":[ {"asin":..,"avail":..,"inbound":..,"reserved":..} ],   // current FBA/vendor snapshot; builder derives stock status + cover + Stock-watch (omit if no inventory data)
  "dsp": { "cy": [ {"cost":..,"impr":..,"clk":..,"sales":..,"orders":..,"ntbO":..}, null, ... ], "py": [ ... ] },  // optional — omit entirely if no DSP spend; per-day aligned to event days
  "deals": {                                   // omit entirely for vendors AND for sellers that ran no promotions
    "py_type_label":"Best Deals", "cy_type_label":"Best Deals",   // or raw type (BEST_DEAL) — builder canonicalizes
    "py": [ {"asin":..,"type":"BEST_DEAL","start":"2025-07-09","end":"2025-07-10","units":..,"revenue":..,"glance":..} ],  // per-ASIN rows from the canonical query; capped at deal_top_n
    "cy": [ {"asin":..,"type":"BEST_DEAL","start":"2026-06-23","end":"2026-06-27","units":..,"revenue":..,"glance":..} ],  // same shape; builder shows finalized if metrics present, pending if none — no synthetic summary row
    "coupons_note":"...", "insight":"...", "takeaway":"..."   // no insight_head — heading is fixed by the builder
  }
}
```

Note: the `25`/`26` suffixes in `movers`/`products`/`ctype` are the template's **prior/current** slots, not literal years — `25` = prior year, `26` = current year, whatever the actual years are.
