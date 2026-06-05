# Daily Brand Check — Query Pack

Per-tab SQL for the daily-brand-check artifact. Every query is parameterised by:

| Placeholder | Meaning |
|---|---|
| `{BRAND}` | Brand name, exactly as stored (`Brand` or `Partner` per the table) |
| `{ACCOUNT}` | Marketplace account, if scoping to one (omit the clause for single-account brands) |
| `{CUR_START}` `{CUR_END}` | Current 30-day **sales** window — `[LATEST-29, LATEST]` |
| `{PRI_START}` `{PRI_END}` | Prior 30-day **sales** window — `[LATEST-59, LATEST-30]` |
| `{YOY_START}` `{YOY_END}` | Same 30 **sales** days last year — `[LATEST-394, LATEST-365]` |
| `{AD_CUR_START}` `{AD_CUR_END}` | Current 30-day **ad** window — `[AD_LATEST-29, AD_LATEST]` |
| `{AD_PRI_START}` `{AD_PRI_END}` | Prior 30-day **ad** window — `[AD_LATEST-59, AD_LATEST-30]` |
| `{AD_YOY_START}` `{AD_YOY_END}` | Same 30 **ad** days last year — `[AD_LATEST-394, AD_LATEST-365]` |

The artifact's JS does straight string substitution before calling `runQuery(sql)`.
Sales/traffic/inventory windows anchor to `LATEST_DATE` (latest `TotalSales` date);
advertising-table windows anchor to `AD_LATEST` (latest `AdvertisingCampaignData` date),
which usually lags sales. Anchoring ad metrics to their own data edge keeps every ad
window a *complete* 30 days, so current ACoS / TACoS / ROAS stay comparable to prior and
YoY instead of being understated by the unfilled tail of the sales window. Neither anchor
is ever `today()`.

**Before editing any query, read `references/datalink-schema.md`.** The two
failure modes that dominate are (1) `Brand` vs `Partner` casing and (2) error
184 from wrapping an aggregate in `round()` inside a `GROUP BY`. Every query
below already follows the CTE-then-math pattern — keep it that way.

Seller vs vendor: the per-ASIN tabs below ship in **both** a seller and a vendor
variant — use the one matching your channel probe (Step 2). Do **not** hand-adapt
the seller query for a vendor brand; the column names differ in ways that fail
silently. Key differences (all verified against the live schema):

- `SellerSales`→`VendorSales`. Both use **`ReportingDate`** (vendor does *not* use
  `Date`). Sales `ProductSales`→`VendorTotalSales`, units
  `UnitsOrdered`→`VendorShippedUnits`, returns `ReturnQuantity`→`VendorReturnQuantity`.
- `SellerTraffic`→`VendorTraffic`. Vendor exposes only `GlanceViews` (use as the
  views metric); it has **no** conversion and **no** buy-box column — render those
  as `—` for vendor brands.
- `AsinFbaInventory`→`VendorInventory`. `AsinFbaInventory` has no date column, but it
  is **not** one row per ASIN — it carries one row per ASIN **per account/marketplace**
  (commonly 10-14 rows for a multi-marketplace brand), so any join on `ASIN` must
  aggregate the inventory side first (`sum(...) ... GROUP BY ASIN`) or it fans out.
  `VendorInventory` has a **`ReportingDate`** and keeps history, so you must pin to the
  latest date (and it too may have one row per ASIN per account — sum across them):
  `ReportingDate = (SELECT max(ReportingDate) FROM VendorInventory WHERE Partner='{BRAND}')`.
  Columns: on-hand `SellableOnHandInventoryUnits`, open POs `OpenPurchaseOrderUnits`,
  unsellable `UnsellableOnHandInventoryUnits`. Vendor has no FBA reserve buckets.

`TotalSales` and the advertising tables are identical for both channels.

---

## Tab 1 — Brand Overview

### 1A. Top-line KPIs — sales + units, three windows

```sql
SELECT * FROM (
WITH
  cur AS (SELECT sum(TotalSales) sales, sum(TotalQuantity) units FROM TotalSales WHERE Brand='{BRAND}' AND Date BETWEEN '{CUR_START}' AND '{CUR_END}'),
  pri AS (SELECT sum(TotalSales) sales, sum(TotalQuantity) units FROM TotalSales WHERE Brand='{BRAND}' AND Date BETWEEN '{PRI_START}' AND '{PRI_END}'),
  yoy AS (SELECT sum(TotalSales) sales, sum(TotalQuantity) units FROM TotalSales WHERE Brand='{BRAND}' AND Date BETWEEN '{YOY_START}' AND '{YOY_END}')
SELECT 'cur' src, cur.sales, cur.units FROM cur
UNION ALL SELECT 'pri', pri.sales, pri.units FROM pri
UNION ALL SELECT 'yoy', yoy.sales, yoy.units FROM yoy
) ORDER BY src
```

### 1B. Ad KPIs — spend / sales / ACoS / TACoS / ROAS, three windows

Aggregate in CTEs, compute ratios in the outer SELECT (error-184 safe). TACoS
needs total sales, so each window joins the matching `TotalSales` window.

```sql
SELECT * FROM (
WITH
  ad AS (
    SELECT
      multiIf(Date BETWEEN '{AD_CUR_START}' AND '{AD_CUR_END}','cur',
              Date BETWEEN '{AD_PRI_START}' AND '{AD_PRI_END}','pri','yoy') src,
      toFloat64(sum(Cost)) spend, toFloat64(sum(AdSales)) ad_sales,
      toFloat64(sum(Impressions)) impr, toFloat64(sum(Clicks)) clicks
    FROM AdvertisingCampaignData
    WHERE Brand='{BRAND}' AND IsBrandActive=1 AND IsAccountActive=1
      AND ( Date BETWEEN '{AD_CUR_START}' AND '{AD_CUR_END}'
         OR Date BETWEEN '{AD_PRI_START}' AND '{AD_PRI_END}'
         OR Date BETWEEN '{AD_YOY_START}' AND '{AD_YOY_END}' )
    GROUP BY src
  ),
  ts AS (
    SELECT
      multiIf(Date BETWEEN '{AD_CUR_START}' AND '{AD_CUR_END}','cur',
              Date BETWEEN '{AD_PRI_START}' AND '{AD_PRI_END}','pri','yoy') src,
      toFloat64(sum(TotalSales)) total_sales
    FROM TotalSales
    WHERE Brand='{BRAND}'
      AND ( Date BETWEEN '{AD_CUR_START}' AND '{AD_CUR_END}'
         OR Date BETWEEN '{AD_PRI_START}' AND '{AD_PRI_END}'
         OR Date BETWEEN '{AD_YOY_START}' AND '{AD_YOY_END}' )
    GROUP BY src
  )
SELECT ad.src src, ad.spend, ad.ad_sales, ad.impr, ad.clicks, ts.total_sales,
  round(ad.ad_sales / nullif(ad.spend,0), 2) roas,
  round(ad.spend / nullif(ad.ad_sales,0) * 100, 2) acos,
  round(ad.spend / nullif(ts.total_sales,0) * 100, 2) tacos,
  round(ad.clicks / nullif(ad.impr,0) * 100, 3) ctr,
  round(ad.spend / nullif(ad.clicks,0), 2) cpc
FROM ad LEFT JOIN ts ON ad.src = ts.src
) ORDER BY src
```

### 1C. Daily sales + ad spend trend (for the overview chart)

```sql
SELECT * FROM (
WITH
  s AS (SELECT Date d, sum(TotalSales) sales FROM TotalSales WHERE Brand='{BRAND}' AND Date BETWEEN '{CUR_START}' AND '{CUR_END}' GROUP BY d),
  a AS (SELECT Date d, sum(Cost) spend FROM AdvertisingCampaignData WHERE Brand='{BRAND}' AND IsBrandActive=1 AND IsAccountActive=1 AND Date BETWEEN '{CUR_START}' AND '{CUR_END}' GROUP BY d)
SELECT toString(s.d) day, s.sales, a.spend
FROM s LEFT JOIN a ON s.d = a.d
) ORDER BY day
```

### 1D. Top 10 ASINs by current-window sales — **seller**

```sql
WITH cur AS (
  SELECT ASIN, any(Name) name, sum(ProductSales) sales, sum(UnitsOrdered) units
  FROM SellerSales WHERE Partner='{BRAND}' AND ReportingDate BETWEEN '{CUR_START}' AND '{CUR_END}'
  GROUP BY ASIN
)
SELECT ASIN asin, name, sales, units FROM cur ORDER BY sales DESC LIMIT 10
```

### 1D-V. Top 10 ASINs by current-window sales — **vendor**

```sql
WITH cur AS (
  SELECT ASIN, any(Name) name, sum(VendorTotalSales) sales, sum(VendorShippedUnits) units
  FROM VendorSales WHERE Partner='{BRAND}' AND ReportingDate BETWEEN '{CUR_START}' AND '{CUR_END}'
  GROUP BY ASIN
)
SELECT ASIN asin, name, sales, units FROM cur ORDER BY sales DESC LIMIT 10
```

---

## Tab 2 — Advertising

### 2A. By campaign type, current window

```sql
WITH agg AS (
  SELECT CampaignType,
    toFloat64(sum(Impressions)) impr, toFloat64(sum(Clicks)) clicks,
    toFloat64(sum(Cost)) spend, toFloat64(sum(AdSales)) ad_sales, toFloat64(sum(Orders)) orders
  FROM AdvertisingCampaignData
  WHERE Brand='{BRAND}' AND IsBrandActive=1 AND IsAccountActive=1
    AND Date BETWEEN '{AD_CUR_START}' AND '{AD_CUR_END}'
  GROUP BY CampaignType
)
SELECT CampaignType type, impr, clicks, spend, ad_sales, orders,
  round(ad_sales / nullif(spend,0), 2) roas,
  round(spend / nullif(ad_sales,0) * 100, 2) acos,
  round(clicks / nullif(impr,0) * 100, 3) ctr,
  round(spend / nullif(clicks,0), 2) cpc
FROM agg ORDER BY spend DESC
```

### 2B. Per-campaign table, current window (top 25 by spend)

```sql
WITH agg AS (
  SELECT Campaign name, any(CampaignType) type,
    toFloat64(sum(Impressions)) impr, toFloat64(sum(Clicks)) clicks,
    toFloat64(sum(Cost)) spend, toFloat64(sum(AdSales)) ad_sales, toFloat64(sum(Orders)) orders
  FROM AdvertisingCampaignData
  WHERE Brand='{BRAND}' AND IsBrandActive=1 AND IsAccountActive=1
    AND Date BETWEEN '{AD_CUR_START}' AND '{AD_CUR_END}'
  GROUP BY name
)
SELECT name, type, impr, clicks, spend, ad_sales, orders,
  round(ad_sales / nullif(spend,0), 2) roas,
  round(spend / nullif(ad_sales,0) * 100, 2) acos,
  round(clicks / nullif(impr,0) * 100, 3) ctr,
  round(spend / nullif(clicks,0), 2) cpc
FROM agg ORDER BY spend DESC LIMIT 25
```

### 2C. Daily ad-spend trend

```sql
SELECT toString(Date) day, toFloat64(sum(Cost)) spend, toFloat64(sum(AdSales)) ad_sales
FROM AdvertisingCampaignData
WHERE Brand='{BRAND}' AND IsBrandActive=1 AND IsAccountActive=1
  AND Date BETWEEN '{AD_CUR_START}' AND '{AD_CUR_END}'
GROUP BY Date ORDER BY Date
```

---

## Tab 3 — ASIN / Product

Per-ASIN sales / units / page views / conversion / ad spend / ACoS, current vs
prior, with inventory and buy box. Sales + traffic + inventory in CTEs, ad spend
joined from `AdvertisingAdData` (note: `Asin` mixed-case on that table).

**Conversion note:** `SellerTraffic` has no `UnitSessionPercentage` column — compute
conversion in the outer SELECT as `units / sessions` (row-level math, after the CTEs
have aggregated, so it stays error-184 safe). `BuyBoxPercentage` is a 0–1 float;
multiply by 100 at render time only.

### 3-S. Per-ASIN — **seller**

```sql
WITH cur AS (
  SELECT ASIN, any(Name) name, sum(ProductSales) cur_sales, sum(UnitsOrdered) cur_units
  FROM SellerSales WHERE Partner='{BRAND}' AND ReportingDate BETWEEN '{CUR_START}' AND '{CUR_END}' GROUP BY ASIN
),
pri AS (
  SELECT ASIN, sum(UnitsOrdered) pri_units
  FROM SellerSales WHERE Partner='{BRAND}' AND ReportingDate BETWEEN '{PRI_START}' AND '{PRI_END}' GROUP BY ASIN
),
traf AS (
  SELECT ASIN, sum(Sessions) sessions
  FROM SellerTraffic WHERE Partner='{BRAND}' AND ReportingDate BETWEEN '{CUR_START}' AND '{CUR_END}' GROUP BY ASIN
),
ad AS (
  SELECT Asin ASIN, toFloat64(sum(Cost)) spend, toFloat64(sum(AdSales)) ad_sales
  FROM AdvertisingAdData WHERE Brand='{BRAND}' AND IsBrandActive=1 AND IsAccountActive=1
    AND Date BETWEEN '{AD_CUR_START}' AND '{AD_CUR_END}' GROUP BY Asin
),
inv AS (
  -- one row per ASIN per account/marketplace; aggregate to one row per ASIN.
  SELECT ASIN, sum(AfnFulfillableQuantity) oh FROM AsinFbaInventory WHERE Partner='{BRAND}' GROUP BY ASIN
)
SELECT cur.ASIN asin, substring(cur.name,1,30) name, cur.cur_sales, cur.cur_units,
  pri.pri_units,
  round(cur.cur_units / nullif(traf.sessions,0) * 100, 1) conv,
  ad.spend,
  round(ad.spend / nullif(ad.ad_sales,0) * 100, 2) acos,
  inv.oh
FROM cur
LEFT JOIN pri ON cur.ASIN = pri.ASIN
LEFT JOIN traf ON cur.ASIN = traf.ASIN
LEFT JOIN ad ON cur.ASIN = ad.ASIN
LEFT JOIN inv ON cur.ASIN = inv.ASIN
ORDER BY cur.cur_sales DESC LIMIT 25
```

### 3-V. Per-ASIN — **vendor**

Same column aliases as the seller variant so the artifact's render code is identical.
Vendor has no sessions/conversion and no buy box → `conv` and `bb` come back `NULL`
(render `—`). `reserved` is `NULL` (vendor has no FBA reserve buckets); `inbound`
maps to open POs.

```sql
WITH cur AS (
  SELECT ASIN, any(Name) name, sum(VendorTotalSales) cur_sales, sum(VendorShippedUnits) cur_units
  FROM VendorSales WHERE Partner='{BRAND}' AND ReportingDate BETWEEN '{CUR_START}' AND '{CUR_END}' GROUP BY ASIN
),
pri AS (
  SELECT ASIN, sum(VendorShippedUnits) pri_units
  FROM VendorSales WHERE Partner='{BRAND}' AND ReportingDate BETWEEN '{PRI_START}' AND '{PRI_END}' GROUP BY ASIN
),
ad AS (
  SELECT Asin ASIN, toFloat64(sum(Cost)) spend, toFloat64(sum(AdSales)) ad_sales
  FROM AdvertisingAdData WHERE Brand='{BRAND}' AND IsBrandActive=1 AND IsAccountActive=1
    AND Date BETWEEN '{AD_CUR_START}' AND '{AD_CUR_END}' GROUP BY Asin
),
inv AS (
  SELECT ASIN, sum(SellableOnHandInventoryUnits) oh
  FROM VendorInventory WHERE Partner='{BRAND}'
    AND ReportingDate = (SELECT max(ReportingDate) FROM VendorInventory WHERE Partner='{BRAND}')
  GROUP BY ASIN
)
SELECT cur.ASIN asin, substring(cur.name,1,30) name, cur.cur_sales, cur.cur_units,
  pri.pri_units,
  CAST(NULL AS Nullable(Float64)) conv,
  ad.spend,
  round(ad.spend / nullif(ad.ad_sales,0) * 100, 2) acos,
  inv.oh
FROM cur
LEFT JOIN pri ON cur.ASIN = pri.ASIN
LEFT JOIN ad ON cur.ASIN = ad.ASIN
LEFT JOIN inv ON cur.ASIN = inv.ASIN
ORDER BY cur.cur_sales DESC LIMIT 25
```

Flag logic for the artifact (compute in JS, not SQL):
- **Slow mover** — `cur_units` well below `pri_units` (e.g. ≤50%) on an ASIN that
  had meaningful prior sales.
- **At-risk hero** — top-decile `cur_sales` ASIN with `oh` covering < ~3 weeks at
  the current daily run-rate.

Truncate `name` to ~30 chars before rendering or the table layout breaks.

---

## Tab 4 — Search Terms

### 4A. Top terms by ad sales

```sql
WITH agg AS (
  SELECT SearchTerm term,
    toFloat64(sum(Impressions)) impr, toFloat64(sum(Clicks)) clicks,
    toFloat64(sum(Cost)) spend, toFloat64(sum(AdSales)) ad_sales, toFloat64(sum(Orders)) orders
  FROM AdvertisingSearchTermData
  WHERE Brand='{BRAND}' AND IsBrandActive=1 AND IsAccountActive=1
    AND Date BETWEEN '{AD_CUR_START}' AND '{AD_CUR_END}'
  GROUP BY term
)
SELECT term, impr, clicks, spend, ad_sales, orders,
  round(ad_sales / nullif(spend,0), 2) roas,
  round(spend / nullif(ad_sales,0) * 100, 2) acos
FROM agg ORDER BY ad_sales DESC LIMIT 25
```

### 4B. Top terms by wasted spend (clicks, no orders)

```sql
WITH agg AS (
  SELECT SearchTerm term,
    toFloat64(sum(Clicks)) clicks, toFloat64(sum(Cost)) spend,
    toFloat64(sum(Orders)) orders, toFloat64(sum(AdSales)) ad_sales
  FROM AdvertisingSearchTermData
  WHERE Brand='{BRAND}' AND IsBrandActive=1 AND IsAccountActive=1
    AND Date BETWEEN '{AD_CUR_START}' AND '{AD_CUR_END}'
  GROUP BY term
)
SELECT term, clicks, spend, orders, ad_sales
FROM agg WHERE orders = 0 AND spend > 0
ORDER BY spend DESC LIMIT 25
```

### 4C. Harvesting potential (converting terms not yet isolated)

```sql
WITH agg AS (
  SELECT SearchTerm term, any(MatchType) match,
    toFloat64(sum(Clicks)) clicks, toFloat64(sum(Cost)) spend,
    toFloat64(sum(Orders)) orders, toFloat64(sum(AdSales)) ad_sales
  FROM AdvertisingSearchTermData
  WHERE Brand='{BRAND}' AND IsBrandActive=1 AND IsAccountActive=1
    AND Date BETWEEN '{AD_CUR_START}' AND '{AD_CUR_END}'
  GROUP BY term
)
SELECT term, match, clicks, spend, orders, ad_sales,
  round(spend / nullif(ad_sales,0) * 100, 2) acos
FROM agg
WHERE orders >= 2 AND spend / nullif(ad_sales,0) * 100 < 30
ORDER BY ad_sales DESC LIMIT 25
```

(The harvest threshold — orders ≥ 2 and ACoS < 30% — is a reasonable default;
the `keyword-harvesting-rules` skill has the brand-tuned version.)

---

## Tab 5 — Inventory Health

### 5A. Per-ASIN inventory snapshot with weeks-of-cover — **seller**

Cover needs a daily run-rate, so join the current-window units from `SellerSales`.

```sql
WITH inv AS (
  -- AsinFbaInventory has one row per ASIN PER ACCOUNT/marketplace; aggregate to one row
  -- per ASIN or the join to vel fans out (duplicate ASIN rows, per-marketplace cover).
  SELECT ASIN,
    sum(AfnFulfillableQuantity) oh,
    sum(ReservedCustomerOrders + ReservedFCTransfers + ReservedFCProcessing) reserved,
    sum(AfnInboundWorkingQuantity + AfnInboundShippedQuantity + AfnInboundReceivingQuantity) inbound
  FROM AsinFbaInventory WHERE Partner='{BRAND}' GROUP BY ASIN
),
vel AS (
  SELECT ASIN, any(Name) name, sum(UnitsOrdered) cur_units
  FROM SellerSales WHERE Partner='{BRAND}' AND ReportingDate BETWEEN '{CUR_START}' AND '{CUR_END}'
  GROUP BY ASIN
)
SELECT inv.ASIN asin, substring(vel.name,1,30) name, inv.oh, inv.reserved, inv.inbound, vel.cur_units,
  round(inv.oh / nullif(vel.cur_units / 30.0 * 7.0, 0), 1) weeks_of_cover
FROM inv LEFT JOIN vel ON inv.ASIN = vel.ASIN
ORDER BY weeks_of_cover ASC LIMIT 25
```

Flag logic (in JS):
- **Low-cover hero** — `weeks_of_cover` < ~3 on an ASIN in the top sales decile.
- **Stranded** — `oh > 0` but `cur_units = 0` over the whole current window.

### 5B. Per-ASIN inventory snapshot with weeks-of-cover — **vendor**

`VendorInventory` keeps history, so pin to the latest `ReportingDate`. Columns:
on-hand `SellableOnHandInventoryUnits`, open POs `OpenPurchaseOrderUnits` (column
label **"Open POs"**), unsellable `UnsellableOnHandInventoryUnits`. Vendor has no
FBA reserve buckets; run-rate comes from `VendorShippedUnits`.

```sql
WITH inv AS (
  -- Pin to latest ReportingDate, then sum across accounts (one row per ASIN per account).
  SELECT ASIN, any(Name) name,
    sum(SellableOnHandInventoryUnits) oh,
    sum(OpenPurchaseOrderUnits) open_po,
    sum(UnsellableOnHandInventoryUnits) unsellable
  FROM VendorInventory WHERE Partner='{BRAND}'
    AND ReportingDate = (SELECT max(ReportingDate) FROM VendorInventory WHERE Partner='{BRAND}')
  GROUP BY ASIN
),
vel AS (
  SELECT ASIN, any(Name) name, sum(VendorShippedUnits) cur_units
  FROM VendorSales WHERE Partner='{BRAND}' AND ReportingDate BETWEEN '{CUR_START}' AND '{CUR_END}'
  GROUP BY ASIN
)
SELECT inv.ASIN asin, substring(coalesce(nullif(vel.name,''), inv.name),1,30) name,
  inv.oh, inv.open_po, inv.unsellable, vel.cur_units,
  round(inv.oh / nullif(vel.cur_units / 30.0 * 7.0, 0), 1) weeks_of_cover
FROM inv LEFT JOIN vel ON inv.ASIN = vel.ASIN
ORDER BY weeks_of_cover ASC LIMIT 25
```

Same flag logic as 5A. Render columns differ by channel: seller shows
**Reserved / Inbound**; vendor shows **Open POs / Unsellable**.

---

## Combining queries

The shell's tab loaders fire these queries **separately** (e.g. Overview runs 1A, 1B,
1C, 1D as four cached fetches). That is the pinned behavior — do not merge them per run,
or two runs will structure their fetches differently. The KPI queries 1A and 1B are the
only ones that internally union (their `src` discriminator + `UNION ALL` wrapped in
`SELECT * FROM ( ... ) ORDER BY src`; ClickHouse requires the wrap when a union carries
`ORDER BY`). If you ever want to reduce round-trips, change the shell loader and pin the
merged query here — never improvise the merge at build time.
