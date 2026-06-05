# Kapoq Datalink — Schema Cheat-Sheet (daily-brand-check)

A compact reference for the tables and columns the daily-brand-check artifact
touches. This is **not** the full Datalink schema — it's the subset the five
tabs need, plus the conventions that most often cause silent failures. When in
doubt, run `list_tables` / `describe_table` against the live tenant; schemas
drift slightly between tenants.

## Table of contents

1. [The golden rules](#the-golden-rules)
2. [Brand vs Partner column map](#brand-vs-partner-column-map)
3. [ASIN column casing](#asin-column-casing)
4. [Tables by tab](#tables-by-tab)
5. [Column reference](#column-reference)
6. [ClickHouse gotchas](#clickhouse-gotchas)

---

## The golden rules

1. **Brand column casing varies by table.** `Brand` on advertising + `TotalSales`;
   `Partner` on seller/vendor/traffic/inventory tables. Wrong column → zero rows,
   no error.
2. **ASIN column casing varies too.** `Asin` (mixed) on `AdvertisingAdData` /
   `AdvertisingProductData`; `ASIN` (caps) almost everywhere else.
3. **Always filter `IsBrandActive = 1 AND IsAccountActive = 1`** on advertising
   tables. Without it you pull spend from accounts the brand no longer belongs to.
4. **Never `round()`/`nullif()`/`if()` an aggregate inside a `GROUP BY` SELECT.**
   Aggregate in a CTE, do the math in the outer SELECT (ClickHouse error 184).
5. **Two date anchors, never `today()`.** Sales/traffic/inventory windows anchor to
   `LATEST_DATE` (latest `TotalSales` date). Advertising windows anchor to `AD_LATEST`
   (latest `AdvertisingCampaignData` date), which often lags sales by days to weeks.
   Anchoring ad metrics to `LATEST_DATE` leaves the tail of the current window with
   sales but no ad spend, which silently understates current ACoS / TACoS and inflates
   ROAS. Anchor each table family to its own data edge.
6. **Use `TotalSales` for unified sales** unless you specifically need seller-only
   or vendor-only columns. `TotalSales.Brand` + `TotalSales.Date` are the cleanest.

---

## Brand vs Partner column map

| Table | Brand column | Date column |
|---|---|---|
| `TotalSales` | `Brand` | `Date` |
| `AdvertisingCampaignData` | `Brand` | `Date` |
| `AdvertisingAdData` | `Brand` | `Date` |
| `AdvertisingSearchTermData` | `Brand` | `Date` |
| `AdvertisingProductData` | `Brand` | `Date` |
| `SellerSales` | `Partner` | `ReportingDate` |
| `VendorSales` | `Partner` | `ReportingDate` |
| `SellerTraffic` | `Partner` | `ReportingDate` |
| `VendorTraffic` | `Partner` | `ReportingDate` |
| `AsinFbaInventory` | `Partner` | (no date column; one row per ASIN **per account** — aggregate) |
| `VendorInventory` | `Partner` | `ReportingDate` (keeps history — pin to latest) |

> Mnemonic: **Ads + TotalSales speak "Brand"; everything operational speaks "Partner".**

---

## ASIN column casing

| Table | ASIN column |
|---|---|
| `AdvertisingAdData` | `Asin` |
| `AdvertisingProductData` | `Asin` |
| `SellerSales` / `VendorSales` | `ASIN` |
| `SellerTraffic` / `VendorTraffic` | `ASIN` |
| `AsinFbaInventory` / `VendorInventory` | `ASIN` |

The artifact's table-render code must tolerate both. A defensive accessor like
`row.ASIN ?? row.Asin ?? row.asin` is fine in the JS.

---

## Tables by tab

| Tab | Primary tables |
|---|---|
| Brand Overview | `TotalSales`, `AdvertisingCampaignData` |
| Advertising | `AdvertisingCampaignData` (campaign-type + per-campaign) |
| ASIN / Product | `SellerSales` or `VendorSales`, `AsinFbaInventory`/`VendorInventory`, `SellerTraffic` (for page views / conversion / BB) |
| Search Terms | `AdvertisingSearchTermData` |
| Inventory Health | `AsinFbaInventory` (seller) / `VendorInventory` (vendor) |

---

## Column reference

### `TotalSales`
- `TotalSales` (float, $) — unified ordered-product sales
- `TotalQuantity` (int) — units
- `Brand`, `Account`, `Date`

### `AdvertisingCampaignData`
- `Impressions`, `Clicks`, `Cost` ($ spend), `AdSales` ($), `Orders`
- `SalesNewToBrand` ($ NTB ad sales)
- `CampaignType` — `SP` / `SB` / `SBV` / `SD` (values may render as
  `Sponsored Products`, etc., depending on tenant — normalize in JS)
- `Campaign` (campaign name), `PortfolioName`
  (note: on this table the column is `Campaign`, **not** `CampaignName`.
  `AdvertisingSearchTermData` and `AdvertisingAdData` differ — see those entries.)
- `Brand`, `Account`, `Date`, `IsBrandActive`, `IsAccountActive`

### `AdvertisingSearchTermData`
- `SearchTerm` (the customer query)
- `Target` / `MatchType` (what it matched)
- `Impressions`, `Clicks`, `Cost`, `AdSales`, `Orders`
- `CampaignName` (on **this** table the campaign column is `CampaignName`, unlike
  `AdvertisingCampaignData` which uses `Campaign`)
- `Brand`, `Account`, `Date`, `IsBrandActive`, `IsAccountActive`

### `SellerSales`
- `ProductSales` ($), `UnitsOrdered`, `ReturnQuantity`
- `ASIN`, `Name` (product title), `CategoryName`
- `Partner`, `Account`, `ReportingDate`

### `VendorSales`
- `VendorTotalSales` ($, shipped sales), `VendorShippedCogs` ($), `VendorShippedUnits`
- `VendorReturnQuantity` (returns **are** on this table)
- `ASIN`, `Name`, `CategoryName`
- `Partner`, `Account`, `ReportingDate` (**not** `Date` — same date column as `SellerSales`)

### `SellerTraffic`
- `Sessions`, `PageViews`, `BuyBoxPercentage` (0–1 float)
- **No `UnitSessionPercentage` column.** Compute conversion as
  `UnitsOrdered / Sessions` (units from `SellerSales`, sessions from here), row-level
  in the outer SELECT after the CTEs aggregate.
- `ASIN`, `Partner`, `Account`, `ReportingDate`
- Vendor analogue `VendorTraffic` exposes only `GlanceViews` (no `Sessions`, no
  conversion, no `BuyBoxPercentage`); use `GlanceViews` as the views metric and
  render conversion / buy-box as `—` for vendor.

### `AsinFbaInventory` (seller)
- `AfnFulfillableQuantity` — on-hand sellable
- `ReservedCustomerOrders`, `ReservedFCTransfers`, `ReservedFCProcessing`
- `AfnInboundWorkingQuantity`, `AfnInboundShippedQuantity`, `AfnInboundReceivingQuantity`
- `ASIN`, `Partner`, `Account`. No date column, but **multiple rows per ASIN** (one per
  account/marketplace). Always aggregate to one row per ASIN before joining:
  `sum(AfnFulfillableQuantity) ... GROUP BY ASIN`. Joining the raw table fans out the
  other side (duplicate ASIN rows, per-marketplace on-hand and weeks-of-cover).

### `VendorInventory` (vendor)
- `SellableOnHandInventoryUnits` — on-hand sellable
- `OpenPurchaseOrderUnits` — inbound on open POs (render label **"Open POs"**)
- `UnsellableOnHandInventoryUnits` — unsellable on-hand
- `ASIN`, `Name`, `Partner`, `Account`, `ReportingDate`
- **Has a `ReportingDate` and keeps history** (not a single snapshot). Pin to the
  latest date:
  `ReportingDate = (SELECT max(ReportingDate) FROM VendorInventory WHERE Partner='{BRAND}')`.

---

## ClickHouse gotchas

| Symptom | Cause | Fix |
|---|---|---|
| Query returns 0 rows, no error | Used `Brand` where table wants `Partner` (or vice-versa) | Check the column map above |
| Error 184 | `round()`/`nullif()` wrapping an aggregate in a `GROUP BY` SELECT | Aggregate in a CTE, divide in the outer SELECT |
| Error 158 (rows limit) | Unbounded scan | Always date-bound; never `count(*)` a full table |
| `UNION ALL` + `ORDER BY` fails | ClickHouse needs the union wrapped | `SELECT * FROM ( ...UNION ALL... ) ORDER BY col` |
| Ad spend higher than expected | Missing active filters | Add `IsBrandActive=1 AND IsAccountActive=1` |
| Duplicate ASIN rows; weeks-of-cover too low | Joined `AsinFbaInventory`/`VendorInventory` without aggregating (multiple rows per ASIN per account) | Aggregate inv to one row per ASIN (`sum(...) GROUP BY ASIN`) before the join |
| Current ACoS/TACoS too low, ROAS too high | Ad windows anchored to `LATEST_DATE`; ad data lags, leaving an empty tail | Anchor ad windows to `AD_LATEST` (latest `AdvertisingCampaignData` date) |
| BB% shows 98 instead of 0.98 | `BuyBoxPercentage` is a 0–1 float | Multiply by 100 only at render time |
| YoY shows 0% for a new brand | No rows a year ago | Render `—`, never `0%` |
