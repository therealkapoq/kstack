---
name: weekly-report
description: "Generate an interactive HTML 'Weekly Report' dashboard for any brand in any Kapoq tenant. Brand-agnostic, vendor-or-seller aware, handles missing optional tables (Traffic, LTV, Coupons) by hiding those sections. Use when a user asks for a weekly report, weekly dashboard, weekly review (as a dashboard not a deck), or interactive weekly HTML report for any Amazon brand. Distinct from the wbr skill — this skill produces an interactive HTML file with tabs and sortable tables; WBR is a static PPTX deck."
---

# Weekly Report Skill

Generate an **interactive HTML weekly report** for any brand in any Kapoq tenant. Output is a single self-contained HTML file with six tabs (Executive Summary, Sales & Traffic, Advertising, Products & Inventory, Customer & LTV, Recommended Actions).

This skill is the dashboard counterpart to the `wbr` skill. Use **Weekly Report** when the user wants an interactive HTML file. Use **WBR** when they want a PPTX deck. If unclear, ask.

## Workflow overview

The skill is a **hybrid**: this markdown guides Claude through discovery (which tenant, which account, which tables have data), then a Python script does the heavy lifting (run derived metrics, callout logic, recommendation engine, template injection).

```
┌ Step 1 ┐ ┌ Step 1.5 ┐ ┌ Step 2 ┐ ┌ Step 3 ─┐ ┌ Step 4 ┐ ┌ Step 4.5 ┐ ┌ Step 5 ┐
│Discover│→│ Branding │→│ Detect │→│Run all  │→│ Build  │→│ Validate │→│Present │
│brand & │ │(logo +   │ │tables &│ │queries, │ │ via    │ │(self-chk │ │to user │
│tenant  │ │ colors)  │ │account │ │write q_*│ │ script │ │ + render)│ │        │
└────────┘ └──────────┘ └────────┘ └─────────┘ └────────┘ └──────────┘ └────────┘
```

---

## Step 1 — Gather parameters

If the user didn't provide them, ask for:

- **Brand name** (as it appears in the relevant Kapoq table)
- **Tenant** (which Kapoq MCP server to query — the connected tenant the brand belongs to)
- **Week ending date** (Saturday) — default to most recent complete week if not specified

If the user names a brand but not a tenant, **ask** which tenant. The MCP tool naming differs across tenants (each has its own `{Tenant} Kapoq:run_query` tool), so Claude must know which one to call. List the tenants that are connected if helpful.

If the user says "current week" or doesn't specify, find the latest complete week:

```sql
SELECT max(Date) AS latest_date FROM TotalSales WHERE Brand = '{BRAND}'
```

Then use the most recent **Saturday** before or on `latest_date` as `week_end`, and `week_end - 6 days` as `week_start`. This is a *provisional* anchor from sales alone; Step 2D re-resolves it with `resolve_window.py` once ads freshness is known (ads usually lag, so the final week may be pulled back).

---

## Step 1.5 — Branding (always ask)

After parameters are confirmed but before running queries, **always ask** the user if they want to apply branding to the report. Use `ask_user_input_v0` so it's a single tap:

```
Question: "Apply branding to this report?"
Options:
  - "No branding (default look)"
  - "Upload a logo + I'll provide hex colors"
  - "Upload a screenshot of the website"
  - "Pull colors from a website URL"
  - "I'll just give you hex colors"
```

The default option (no branding) keeps the existing slate-gradient header and `#0e7490` primary so reports remain consistent for users who don't engage. Skipping is a normal, expected choice — don't push.

In this sandbox the URL-scrape path will fail for most domains (network allow-list), so the screenshot option is usually the most reliable branded path. Don't bury it — list it as shown above.

### Branch A: "No branding"

Set `branding = None` in the context. Header uses the default gradient and accent color. Proceed to Step 2.

### Branch B: "Upload a logo + hex colors"

1. Check `/mnt/user-data/uploads/` for an uploaded image. If nothing's there, ask the user to upload one (PNG, JPG, or SVG, ideally a square or horizontal lockup).
2. Ask for **primary hex** (used for charts, key headers, KPI accents) and optionally **accent hex** (used for secondary highlights). Both fields accept 6-digit hex like `#1a2b3c`.
3. Base64-encode the logo as a `data:` URI so it embeds in the HTML (the report needs to be a single self-contained file).

### Branch C: "Upload a screenshot of the website"

This is the most reliable branded path in a network-restricted sandbox — no fetch required, Claude just looks at the image. Workflow:

1. Check `/mnt/user-data/uploads/` for an uploaded image. If nothing's there, ask the user to upload one (a homepage hero shot is ideal — it'll have the logo, primary CTA color, and any accent colors all on one screen).
2. **View the image directly.** When the screenshot is in context, Claude can read pixel colors by inspecting the image — no OCR or color-extraction script needed. Identify:
   - **Primary color:** the brand's signature accent — the color of CTAs ("Add to Cart", "Shop Now", etc.), promo banners, link hovers, or the brand logo's main color. If the same color appears in multiple high-attention places (banner + button + tag), it's the primary.
   - **Accent / secondary color:** a complementary brand color — often used for hover states, secondary buttons, or accent strokes. For luxury/jewelry brands this is often a metallic (gold `#C5A572`-ish, silver, copper). For SaaS it might be a paler shade of the primary. If the screenshot only reveals one strong color, set `accent = primary` and move on.
   - **Logo treatment:** read the wordmark style (serif vs sans, weight, letter-spacing) and any tagline beneath it. Note this for the wordmark rendering step.
   - **Header style preference:** if the site has a clean white header, set `header_style = "light"`. If it's dark/inverse, set `header_style = "dark"`. Default is `dark`.
3. **State the colors you picked back to the user** before generating, in plain language: "I'm reading primary red ≈ `#A91D2A` (from the sale banner and primary CTA button) and a gold accent `#C5A572` to complement." This gives them a chance to correct before a full rebuild.
4. **Recreate the wordmark in CSS, don't try to lift the bitmap.** Pulling pixels out of a screenshot produces a low-res ghost of the logo. Cleaner: build a CSS wordmark that matches the typographic style (serif caps + small-caps tagline, letterspaced sans, etc.). Flag this to the user — "I recreated the wordmark in CSS; for pixel-perfect fidelity upload the actual logo file."
5. Optionally ask if they also want to upload the standalone logo asset for exact reproduction. If they do, embed it via the same base64 data-URI path as Branch B.

### Branch D: "Pull colors from a website URL"

Attempt to scrape, but treat it as best-effort — many sandboxes restrict outbound HTTPS to a small allow-list (npm, pypi, github, etc.). If the fetch fails, fall through to Branch E and ask for hex codes manually.

Use this helper pattern:

```python
import urllib.request, re, base64, ssl
from urllib.parse import urljoin, urlparse

def try_extract_branding(url, timeout=10):
    """Returns dict with optional 'logo_data_uri', 'primary', 'accent'. Returns None if fetch fails."""
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; KapoqReport/1.0)'})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            html = resp.read(500_000).decode('utf-8', errors='ignore')
    except Exception as e:
        return None  # Network blocked, DNS failure, timeout, etc.

    result = {}

    # Color extraction: pull all hex codes from inline styles + <style> blocks, frequency-rank them.
    hexes = re.findall(r'#([0-9a-fA-F]{6})\b', html)
    # Filter near-greyscale and near-white/black — they're usually structural, not brand
    def is_brand_color(h):
        r, g, b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
        if max(r,g,b) - min(r,g,b) < 20: return False    # grey-ish
        if r+g+b < 60 or r+g+b > 720: return False        # near-black or near-white
        return True
    candidates = [h.lower() for h in hexes if is_brand_color(h)]
    if candidates:
        from collections import Counter
        ranked = Counter(candidates).most_common(2)
        result['primary'] = '#' + ranked[0][0]
        if len(ranked) > 1:
            result['accent'] = '#' + ranked[1][0]

    # Logo extraction: try favicon first (most reliable), then look for <img> with 'logo' in src/alt
    parsed = urlparse(url)
    favicon_url = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
    try:
        with urllib.request.urlopen(favicon_url, timeout=5, context=ctx) as fr:
            ico = fr.read()
            if ico and len(ico) > 100:
                result['logo_data_uri'] = 'data:image/x-icon;base64,' + base64.b64encode(ico).decode()
    except Exception:
        pass

    # Look for a real logo image in the HTML
    logo_match = re.search(r'<img[^>]+(?:logo|brand)[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if logo_match:
        logo_url = urljoin(url, logo_match.group(1))
        try:
            with urllib.request.urlopen(logo_url, timeout=5, context=ctx) as lr:
                logo_bytes = lr.read(2_000_000)  # cap at 2MB
                mime = 'image/svg+xml' if logo_url.endswith('.svg') else 'image/png'
                if logo_url.endswith(('.jpg', '.jpeg')): mime = 'image/jpeg'
                result['logo_data_uri'] = f'data:{mime};base64,' + base64.b64encode(logo_bytes).decode()
        except Exception:
            pass

    return result if result else None
```

If `try_extract_branding` returns `None`, tell the user the fetch failed and fall through to Branch E. If it returns a dict but is missing either logo or colors, ask the user to fill in the gaps manually.

### Branch E: "I'll just give you hex colors"

Prompt for primary hex (required) and accent hex (optional). Skip the logo. Validate hex format with a quick regex (`^#[0-9a-fA-F]{6}$`) before accepting.

### Storing branding in context

Append to `context.json`:

```json
{
  ...other fields...,
  "branding": {
    "logo_data_uri": "data:image/png;base64,iVBOR...",   // or null
    "primary": "#1a2b3c",                                 // or null → fall back to default
    "accent": "#c5a572"                                   // or null
  }
}
```

`null` for any field means "use the default for this element" — the build script handles partial branding cleanly.

### Branding is applied automatically — just set `ctx["branding"]`

Branding is now handled by the builder + template from the `branding` block you write into
`context.json` (Step 2D) — there is no manual template editing. Set the three fields and the
report does the rest:

```json
"branding": {"logo_data_uri": "data:image/png;base64,..." or null,
             "primary": "#1a2b3c" or null,
             "accent": "#c5a572" or null}
```

What happens at build/render time:

- `primary` / `accent` drive the CSS custom properties `--primary` / `--accent` and the chart
  colors (13-week trend line, KPI sparklines, sessions bars, donut, NTB/LTV bars). Any `null`
  falls back to the default look — primary `#0e7490`, accent `#e07856` — so an all-`null`
  branding block is byte-identical to an unbranded report. (`accent` defaults to `primary` if
  only `primary` is given.)
- `logo_data_uri`, when present, replaces the brand-name wordmark in the masthead with the
  embedded image; otherwise the brand name renders as text.

Pass full 6-digit hex (`#rrggbb`). You do not edit `template.html` or `build_report.py` to
brand a report — writing the `branding` block is the whole interface. The default dark slate
header gradient is intentionally kept regardless of branding; the brand color shows through the
accent border, tabs, charts, and stats.

---

## Step 2 — Detect account type, account, and table availability

### 2A. Is the brand a Seller or a Vendor?

Run both queries (substitute `{BRAND}`):

```sql
SELECT 'seller' as kind, count(*) as rows FROM SellerSales WHERE Partner = '{BRAND}' AND ReportingDate >= today() - 90
UNION ALL
SELECT 'vendor', count(*) FROM VendorSales WHERE Partner = '{BRAND}' AND ReportingDate >= today() - 90
```

If `seller.rows > 0` and `vendor.rows = 0` → account_type = `"Seller"`, use `SellerSales` / `SellerTraffic`.
If `vendor.rows > 0` and `seller.rows = 0` → account_type = `"Vendor"`, use `VendorSales` / `VendorTraffic`.
If both, ask the user which channel to report on. (Some brands run both, but a weekly review usually focuses on one.)

### 2B. Which account?

Many brands sell in multiple marketplaces (e.g., a brand may have US/CA/UK/FR/DE accounts). List the candidates:

```sql
-- For sellers
SELECT DISTINCT Account, AccountId, count(*) as rows
FROM SellerSales
WHERE Partner = '{BRAND}' AND ReportingDate >= today() - 30
GROUP BY Account, AccountId ORDER BY rows DESC

-- For vendors, same query against VendorSales (it also keys on ReportingDate, not Date)
```

If exactly one account, use it. If multiple, **ask the user** which to use. Don't try to roll up — currency and timezone differences make rollups misleading.

Once chosen, **copy the `Account` string verbatim** from this `DISTINCT Account` result into `context.json` — do not retype, relabel, or reformat it (e.g. don't turn `Seller Central US` into `Amazon US`). `tenant` and `marketplace` are normalized/derived in the builder, but `account` is embedded as-passed, so it is the one field whose run-to-run stability rests on copying it exactly rather than on a code normalizer.

### 2C. Which optional tables have data?

The dashboard has three optional sections: Traffic, Customer/LTV, and Coupons. Each is hidden if no data exists. Probe each (bound queries by date to avoid scanning all rows):

```sql
SELECT 'traffic' as tbl, count(*) FROM SellerTraffic
  WHERE Partner = '{BRAND}' AND Account = '{ACCOUNT}' AND ReportingDate BETWEEN '{WK13_START}' AND '{WK_END}'
UNION ALL SELECT 'ltv', count(*) FROM LtvRawCustomer
  WHERE Brand = '{BRAND}' AND Account = '{ACCOUNT}'
UNION ALL SELECT 'coupons', count(*) FROM CouponPerformance
  WHERE Account = '{ACCOUNT}' AND EndDateTime >= today() - 120
```

(For vendors: probe `VendorTraffic` instead of `SellerTraffic` for the `traffic` row (same `ReportingDate` filter). CouponPerformance and LtvRawCustomer exist for both.)

Also try a **`list_tables` call** to check if `LtvRawCustomer` and `LtvRawOrders` exist in the tenant at all — some older tenants don't have them. If a table isn't listed, skip it without trying to query.

### 2D. Resolve the window (week anchor + all date boundaries)

This math is deterministic, so **don't free-hand it from the formulas below** — run the bundled
helper and paste its output. The flow is: compute the two freshness dates (the canonical rule and
SQL are a few paragraphs down), then hand them to `resolve_window.py`:

```bash
python3 scripts/resolve_window.py \
  --sales-fresh {sales_fresh_through} \
  --ads-fresh   {ads_fresh_through}      # omit entirely if the brand has no ad stream
  # optional: --week-end {YYYY-MM-DD}    # only if the USER named a specific week ending date;
  #                                      # it overrides the anchor and is snapped to its Saturday
```

It prints three blocks:

- **`context`** → copy `week_start`, `week_end`, `week_number` (and the freshness fields it echoes
  back) straight into `context.json`.
- **`sql_params`** → every `{PLACEHOLDER}` the Step 2C and Q1–Q6 queries substitute
  (`CW_START`, `CW_END`, `CW_END_PLUS1`, `T13_START`, `LY_CW_START`, `LY_CW_END`, `LY_CW_END_PLUS1`,
  `YTD_START`, `LY_YTD_START`, `LW_START`, `LW_END`, `T4_START`, plus the `WK_END`/`WK13_START`
  aliases). Substitute these verbatim — no further arithmetic.
- **`anchor`** → which stream constrained the week and whether ads lag sales (informational).

The helper owns only the arithmetic; the genuine decisions — which brand, which account, whether to
brand, the narrative framing — stay yours. For reference, the boundaries it computes from `week_end`
(a Saturday) are:

```
cw_start  = week_end - 6 days
lw_end    = cw_start - 1 day
lw_start  = lw_end - 6 days
t4_start  = week_end - 27 days  (covers 4 weeks ending CW)
t13_start = week_end - 90 days  (13 weeks)
ly_cw_end   = week_end - 364 days   (same weekday, 1 year prior — NOT 365)
ly_cw_start = ly_cw_end - 6 days
ytd_start   = first day of week_end's year
ly_ytd_start = first day of the year before week_end's year (start of last year's YTD span;
               Q1's ytd_ly CTE runs from ly_ytd_start to ly_cw_end, aligned with current YTD)
```

The builder also runs a fail-closed `_check_week_consistency` guard at build time: it aborts if
`week_end` isn't a Saturday, `week_start` isn't `week_end - 6`, or `week_number` isn't the ISO week
of `week_end` — so a transposed field can't slip through to a rendered report.

Write all parameters into a working `context.json`:

```json
{
  "brand": "{Brand Name}",
  "account": "{Account}@Amazon US",
  "account_type": "Seller",
  "marketplace": "Amazon US",
  "tenant": "{Connector display name, e.g. Kapoq Demo}",
  "week_start": "2026-05-03",
  "week_end": "2026-05-09",
  "week_number": 19,
  "has_traffic": true,
  "has_ltv": true,
  "has_coupons": true,
  "sales_fresh_through": "2026-05-29",
  "ads_fresh_through": "2026-05-12",
  "branding": {"logo_data_uri": null, "primary": null, "accent": null}
}
```

`week_number` is the ISO week of `week_end`. The `has_*` flags are set in Step 2 and tell the
builder which sections to render. `branding` comes from Step 1.5 (all-null = default look).

`marketplace` is now **derived in the builder** from the `account` string (it strips the channel
words and keeps the region, e.g. `...@Vendor Central US` → `Amazon US`), so the masthead label is
identical run-to-run. The value you write here is only a fallback for accounts with no `@`-suffix;
don't agonize over its exact wording.

`tenant` is the bare connector display name (the name of the connected `{Tenant} Kapoq:run_query`
tool **minus** any leading `claude.ai ` prefix) — e.g. write `Kapoq Demo`, not `claude.ai Kapoq Demo`
and not `{Tenant} Kapoq`. The builder normalizes it anyway (strips a leading `claude.ai ` prefix and
collapses whitespace) so the footer label is identical run-to-run, but write the bare name to begin with.

**`sales_fresh_through` / `ads_fresh_through` (data-freshness fields).** Optional but recommended.
Each is the latest date for which that data stream is *complete*. These render in the footer and the
Step 5 chat line, so they **must** be computed identically every run — do not free-hand them.

**Canonical rule (pin this exactly):** a stream is complete only through the last date that is not
the current calendar day, because today's rows are still ingesting and partial. So define each field
as `max(Date)` **excluding today** — i.e. add `WHERE Date < today()`. Never set a freshness date to
`today()`, and never "back off" by your own judgment; the `< today()` filter is the whole rule. Use
exactly these queries (do not drop the `Date < today()` clause):

```sql
SELECT max(Date) FROM TotalSales            WHERE Brand = '{BRAND}'   AND Date < today()  -- sales_fresh_through
SELECT max(Date) FROM AdvertisingCampaignData WHERE Account = '{ACCT}' AND Date < today()  -- ads_fresh_through
```

The builder uses both fields two ways: (1) the footer + data-note render real completeness dates, and
(2) when ads lag sales, it auto-adds a "data lags" recommendation explaining the anchor.

> **Note (server clock):** `today()` is evaluated on the ClickHouse *server* clock, not your local
> date, so `sales_fresh_through`/`ads_fresh_through` reflect the server clock at run time. Two runs on
> opposite sides of server midnight can resolve a *different* week and produce a different report —
> inherent to any "latest complete data" anchor, not a defect. If you need a run to be reproducible
> (e.g. a consistency check), freeze the resolved window once and reuse it rather than re-resolving.

**Anchor the week to the least-fresh core stream, not the calendar.** Sales, traffic, and ads often
ingest on different lags — ads commonly trail sales by a week or more. If you anchor to the
calendar-latest complete *sales* week, the Advertising tab can come back empty or half-populated for
that week. `resolve_window.py` handles this for you: it takes `min(sales_fresh_through,
ads_fresh_through)` and rounds back to the enclosing Saturday, so the week is the most recent one for
which **all** core streams are complete. You only feed it the two freshness dates (above) — it does
the rounding. The lag is surfaced in the report automatically when both freshness fields are set.
This keeps every tab apples-to-apples.

---

## Step 3 — Run the queries

Run these against the appropriate tenant's MCP `run_query` tool. Each query writes results to a JSON file in the working directory. **Use exactly the file names and shapes below** — the build script reads them by name.

The Q1 sales summary reads the unified `TotalSales` table (which keys on `Date`) for **both** channels — don't swap it. The per-channel tables are only swapped for traffic (Q2) and the per-ASIN query (Q4): sellers use `SellerSales` / `SellerTraffic`, vendors use `VendorSales` / `VendorTraffic`. **Both the Seller and Vendor per-channel sales/traffic tables key on `ReportingDate`** (only `TotalSales` uses `Date`), so do **not** rename `ReportingDate` to `Date` when swapping to the vendor tables — that was a long-standing error. Always include `Account = '{ACCOUNT}'` to scope to the chosen marketplace.

Many queries combine multiple periods (CW, LW, LY, T4, YTD, weekly trend) into a single ClickHouse statement via `UNION ALL` with a `src` tag. ClickHouse requires UNION-ALL queries with `ORDER BY` to be wrapped in a subquery — see existing wbr skill if you hit error 184 or similar.

**Pass query values at full precision — never pre-round.** Write the raw numbers ClickHouse returns into the `q_*.json` files (e.g. `119006.3696`, not `119006.37`, and don't cast integral counts to floats). The builder's `canon()` step canonicalizes every embedded number at load (ints stay ints, integral floats become ints, other floats round to 4 dp), so any rounding you do upstream only introduces run-to-run byte drift without changing what renders. This is enforced for money the same way it is for Buy Box: the builder runs an aggregate `_check_coarse_money` guard and **hard-aborts** with a deterministic `BUILD FAILED: money rollups appear pre-rounded` message if nearly all of the money rollups (OPS, spend, ad_sales, NTB/LTV revenue, per-ASIN sales) come back at ≤1 decimal place — the fingerprint of a systematic pre-round (a blanket `round()` in the SQL or a tidied reshape). It is aggregate, not per-value, precisely so a single legitimate whole-dollar total never trips it; only a systematic pre-round does.

### Q1 — Sales summary + 13-week trend

```sql
SELECT * FROM (
WITH
  cw AS (SELECT sum(TotalSales) ops, sum(TotalQuantity) units FROM TotalSales WHERE Brand='{BRAND}' AND Account='{ACCOUNT}' AND Date BETWEEN '{CW_START}' AND '{CW_END}'),
  lw AS (SELECT sum(TotalSales) ops, sum(TotalQuantity) units FROM TotalSales WHERE Brand='{BRAND}' AND Account='{ACCOUNT}' AND Date BETWEEN '{LW_START}' AND '{LW_END}'),
  ly AS (SELECT sum(TotalSales) ops, sum(TotalQuantity) units FROM TotalSales WHERE Brand='{BRAND}' AND Account='{ACCOUNT}' AND Date BETWEEN '{LY_CW_START}' AND '{LY_CW_END}'),
  t4 AS (SELECT sum(TotalSales) ops, sum(TotalQuantity) units FROM TotalSales WHERE Brand='{BRAND}' AND Account='{ACCOUNT}' AND Date BETWEEN '{T4_START}' AND '{CW_END}'),
  ytd_cy AS (SELECT sum(TotalSales) ops, sum(TotalQuantity) units FROM TotalSales WHERE Brand='{BRAND}' AND Account='{ACCOUNT}' AND Date BETWEEN '{YTD_START}' AND '{CW_END}'),
  ytd_ly AS (SELECT sum(TotalSales) ops, sum(TotalQuantity) units FROM TotalSales WHERE Brand='{BRAND}' AND Account='{ACCOUNT}' AND Date BETWEEN '{LY_YTD_START}' AND '{LY_CW_END}'),
  weekly AS (SELECT toStartOfWeek(Date,6) wk, sum(TotalSales) ops, sum(TotalQuantity) units FROM TotalSales WHERE Brand='{BRAND}' AND Account='{ACCOUNT}' AND Date BETWEEN '{T13_START}' AND '{CW_END}' GROUP BY wk)
SELECT 'cw' src, '' k, cw.ops opv, cw.units unv FROM cw
UNION ALL SELECT 'lw','',lw.ops,lw.units FROM lw
UNION ALL SELECT 'ly','',ly.ops,ly.units FROM ly
UNION ALL SELECT 't4','',t4.ops,t4.units FROM t4
UNION ALL SELECT 'ytd_cy','',ytd_cy.ops,ytd_cy.units FROM ytd_cy
UNION ALL SELECT 'ytd_ly','',ytd_ly.ops,ytd_ly.units FROM ytd_ly
UNION ALL SELECT 'wk',toString(wk),ops,units FROM weekly
) ORDER BY src, k
```

Reshape into `q_sales.json`:

```json
{
  "summary": {
    "cw": {"ops": 200000, "units": 4000},
    "lw": {"ops": 210000, "units": 4200},
    "ly": {"ops": 120000, "units": 2600},
    "t4": {"ops": 800000, "units": 16000},
    "ytd_cy": {"ops": 3600000, "units": 72000},
    "ytd_ly": {"ops": 1800000, "units": 40000}
  },
  "weekly_13w": [
    {"wk": "2026-02-08", "ops": 180000, "units": 3700},
    ... 13 rows ...
  ]
}
```

If the brand has no prior-year data (recent launch, or a vendor account with no LY rows),
**omit** the `ly` and `ytd_ly` keys from the `q_sales.json` summary — do **not** write zeros or a
null object. The same rule covers any single empty comparison period (an LY week, YTD-LY, or the LY
traffic block in Q2): if the query returns no rows for that period, drop its key entirely. The
template honors this: a missing `ly`/`ytd_ly` skips that period-summary row and the chart's LY
benchmark line, and any zero-unit row renders its ASP as `—` rather than `$NaN`. Pick **omit**
consistently (never mix omit and zero-fill) so two runs of the same brand serialize identically.

**`weekly_13w` order is builder-pinned.** The 13-week arrays in `q_sales.json`, `q_traffic.json`,
and `q_ads.json` are consumed positionally by the builder (last row = current week, `[-4:]` = the
trailing-4 average, sparklines render left-to-right), so at load the builder sorts each `weekly_13w`
ascending by the `wk` ISO-date string — the same order `toStartOfWeek(Date,6)` + the SQL `ORDER BY`
already produce. Emit the rows in any convenient order; the render is identical either way. (This is
the weekly-trend counterpart of the ASIN re-pin in Q4 and the ad-table re-pin in Q3.)

### Q2 — Traffic (skip entirely if `has_traffic = false`)

```sql
SELECT * FROM (
WITH
  weekly AS (SELECT toStartOfWeek(ReportingDate,6) wk, sum(Sessions) sess, sum(PageViews) pv, avg(BuyBoxPercentage) bb FROM SellerTraffic WHERE Partner='{BRAND}' AND Account='{ACCOUNT}' AND ReportingDate BETWEEN '{T13_START}' AND '{CW_END}' GROUP BY wk),
  ly AS (SELECT sum(Sessions) sess, sum(PageViews) pv, avg(BuyBoxPercentage) bb FROM SellerTraffic WHERE Partner='{BRAND}' AND Account='{ACCOUNT}' AND ReportingDate BETWEEN '{LY_CW_START}' AND '{LY_CW_END}')
SELECT 'wk' src, toString(wk) k, sess, pv, bb FROM weekly
UNION ALL SELECT 'ly','',ly.sess, ly.pv, ly.bb FROM ly
) ORDER BY src, k
```

Reshape into `q_traffic.json`: `{"ly": {sess, pv, bb}, "weekly_13w": [{wk, sess, pv, bb}, ...]}`.

**Pass `bb` (`avg(BuyBoxPercentage)`) verbatim — do NOT pre-round it.** The builder snaps
Buy Box to 2dp, and that snap is idempotent for raw and 2dp inputs but NOT for a 3dp
pre-round: `round(round(0.9554019, 3), 2)` is `0.95`, while `round(0.9554019, 2)` is `0.96`,
so a run that tidies Buy Box to 3 decimals drifts the rendered Buy Box % vs a run that
passed raw precision. The 3dp pre-round destroys the precision the builder needs, so this
cannot be fixed downstream — write the raw float (or, at most, a value tidied to 2
decimals, which the 2dp snap leaves unchanged). The builder hard-aborts the build with a
deterministic `BUILD FAILED: Buy Box appears pre-rounded to 3 decimals` message if it
detects a value pre-rounded to exactly 3 decimals; raw and 2dp inputs pass and converge.

For vendors, query `VendorTraffic`. Vendor traffic schema typically has `GlanceViews` instead of `Sessions` — adapt if needed and map to `sess` in the output JSON.

**Vendor missing-metric sentinel (pin this):** vendor traffic has no PageViews or Buy Box. Set
`pv` and `bb` to **`null`** in every weekly row and in `ly` — **never `0`**. (`0` and `null` render
the same today only because the dual-axis chart coerces `null` to `0`; writing `0` is one template
tweak away from showing a real, wrong "0% Buy Box" series, so write `null` and keep the distinction
honest.) The template already pairs with this: `dualChart` detects an all-`null`/all-`0` Buy Box
series and **skips** the Buy Box line and its right-hand axis, so a vendor report shows the sessions
bars alone — no meaningless flat 0% line. Writing `null` is the data-side half; the guard is the
template-side half, and both now ship.

### Q3 — Ads 13-week + LY + Campaign Type + Portfolio + MTD

Three sub-queries: weekly trend, campaign type mix for CW, portfolio breakdown for CW. Plus MTD.

```sql
-- Weekly trend (13 weeks + LY)
SELECT * FROM (
WITH
  weekly AS (
    SELECT toStartOfWeek(Date,6) wk,
      toFloat64(sum(Impressions)) impr, toFloat64(sum(Clicks)) clicks,
      toFloat64(sum(Cost)) spend, toFloat64(sum(AdSales)) ad_sales,
      toFloat64(sum(Orders)) orders, toFloat64(sum(SalesNewToBrand)) ntb_sales
    FROM AdvertisingCampaignData
    WHERE Brand='{BRAND}' AND Account='{ACCOUNT}' AND IsAccountActive=1 AND IsBrandActive=1
      AND Date BETWEEN '{T13_START}' AND '{CW_END}'
    GROUP BY wk
  ),
  ly AS (
    SELECT toFloat64(sum(Impressions)) impr, toFloat64(sum(Clicks)) clicks,
      toFloat64(sum(Cost)) spend, toFloat64(sum(AdSales)) ad_sales,
      toFloat64(0) orders, toFloat64(0) ntb_sales
    FROM AdvertisingCampaignData
    WHERE Brand='{BRAND}' AND Account='{ACCOUNT}' AND IsAccountActive=1 AND IsBrandActive=1
      AND Date BETWEEN '{LY_CW_START}' AND '{LY_CW_END}'
  )
SELECT 'wk' src, toString(wk) k, impr, clicks, spend, ad_sales, orders, ntb_sales FROM weekly
UNION ALL SELECT 'ly','',ly.impr,ly.clicks,ly.spend,ly.ad_sales,ly.orders,ly.ntb_sales FROM ly
) ORDER BY src, k
```

Then campaign type for CW:

```sql
WITH agg AS (
  SELECT CampaignType,
    toFloat64(sum(Impressions)) impr, toFloat64(sum(Clicks)) clicks,
    toFloat64(sum(Cost)) spend, toFloat64(sum(AdSales)) ad_sales, toFloat64(sum(Orders)) orders
  FROM AdvertisingCampaignData
  WHERE Brand='{BRAND}' AND Account='{ACCOUNT}' AND IsAccountActive=1 AND IsBrandActive=1
    AND Date BETWEEN '{CW_START}' AND '{CW_END}'
  GROUP BY CampaignType
)
SELECT CampaignType type, impr, clicks, spend, ad_sales, orders,
  round(ad_sales/nullif(spend,0),2) roas,
  round(spend/nullif(ad_sales,0)*100,2) acos,
  round(clicks/nullif(impr,0)*100,3) ctr
FROM agg ORDER BY spend DESC, type
```

Then portfolio for CW (same pattern, GROUP BY PortfolioName, `ORDER BY spend DESC, PortfolioName`, LIMIT 12). The secondary sort key (`type` / `PortfolioName` / `ASIN` on Q4) is a deterministic tie-break — without it, ClickHouse may return rows with equal spend/sales in a different order between runs, reordering the table. As with the ASIN table (Q4), the final `campaign_types_cw` / `portfolios_cw` order is also **re-pinned in the builder** at load — `campaign_types_cw` by `(-spend, type)` and `portfolios_cw` by `(-spend, name)`, applied after the blank→`Unassigned` collapse — so the rendered order is identical regardless of how a run reshaped these arrays, not only when the SQL `ORDER BY` is preserved.

MTD: aggregate `Cost`, `AdSales`, `Impressions`, `Clicks` from `AdvertisingCampaignData` AND `TotalSales` for the month-to-date window (1st of month to CW_END).

Combine into `q_ads.json`:

```json
{
  "weekly_13w": [{wk, impr, clicks, spend, ad_sales, orders, ntb_sales}, ...],
  "ly": {impr, clicks, spend, ad_sales},
  "campaign_types_cw": [{type, impr, clicks, spend, ad_sales, orders, roas, acos, ctr}, ...],
  "portfolios_cw": [{name, spend, ad_sales, orders, impr, clicks, roas, acos}, ...],
  "mtd": {ad_spend, ad_sales, total_sales, impr, clicks}
}
```

**Note on PortfolioName field**: `PortfolioName` is a real column on `AdvertisingCampaignData` — `GROUP BY PortfolioName` directly (as the query above does); do not try to derive portfolios from anything else. The `Partner`/`Brand` + `Account` fields are only the brand/account filter (the same ones used in every other query here), not the portfolio identity. (`AdvertisingPortfolios` is a separate table keyed on `PartnerName` + `AccountName` and is not needed for this report.)

### Q4 — Top ASINs

Pull the top 15 ASINs ranked by current-week sales, with WoW/T4/LY/YTD comparisons, inventory, and buy box:

```sql
WITH cw AS (
  SELECT ASIN, any(Name) name, any(CategoryName) cat,
    sum(ProductSales) cw_ops, sum(UnitsOrdered) cw_units, sum(ReturnQuantity) cw_returns
  FROM SellerSales WHERE Partner='{BRAND}' AND Account='{ACCOUNT}'
    AND ReportingDate BETWEEN '{CW_START}' AND '{CW_END}'
  GROUP BY ASIN
),
lw AS (
  SELECT ASIN, sum(ProductSales) lw_ops, sum(UnitsOrdered) lw_units
  FROM SellerSales WHERE Partner='{BRAND}' AND Account='{ACCOUNT}'
    AND ReportingDate BETWEEN '{LW_START}' AND '{LW_END}'
  GROUP BY ASIN
),
t4 AS (
  SELECT ASIN, sum(UnitsOrdered) t4_units, sum(ProductSales) t4_ops
  FROM SellerSales WHERE Partner='{BRAND}' AND Account='{ACCOUNT}'
    AND ReportingDate BETWEEN '{T4_START}' AND '{CW_END}'
  GROUP BY ASIN
),
ytd AS (
  SELECT ASIN, sum(ProductSales) ytd_ops, sum(UnitsOrdered) ytd_units, sum(ReturnQuantity) ytd_returns
  FROM SellerSales WHERE Partner='{BRAND}' AND Account='{ACCOUNT}'
    AND ReportingDate BETWEEN '{YTD_START}' AND '{CW_END}'
  GROUP BY ASIN
),
ly AS (
  SELECT ASIN, sum(ProductSales) ly_ops, sum(UnitsOrdered) ly_units
  FROM SellerSales WHERE Partner='{BRAND}' AND Account='{ACCOUNT}'
    AND ReportingDate BETWEEN '{LY_CW_START}' AND '{LY_CW_END}'
  GROUP BY ASIN
),
inv AS (
  SELECT ASIN, AfnFulfillableQuantity oh, ReservedCustomerOrders+ReservedFCTransfers+ReservedFCProcessing reserved,
    AfnInboundWorkingQuantity+AfnInboundShippedQuantity+AfnInboundReceivingQuantity inbound
  FROM AsinFbaInventory WHERE Partner='{BRAND}' AND Account='{ACCOUNT}'
),
bb AS (
  SELECT ASIN, avg(BuyBoxPercentage) bb_cw
  FROM SellerTraffic WHERE Partner='{BRAND}' AND Account='{ACCOUNT}'
    AND ReportingDate BETWEEN '{CW_START}' AND '{CW_END}'
  GROUP BY ASIN
)
SELECT cw.ASIN as asin, cw.name, cw.cat,
  cw.cw_ops, cw.cw_units, cw.cw_returns,
  lw.lw_ops, lw.lw_units,
  t4.t4_units, t4.t4_ops,
  ytd.ytd_ops, ytd.ytd_units, ytd.ytd_returns,
  ly.ly_ops, ly.ly_units,
  inv.oh, inv.reserved, inv.inbound,
  bb.bb_cw
FROM cw
LEFT JOIN lw ON cw.ASIN=lw.ASIN
LEFT JOIN t4 ON cw.ASIN=t4.ASIN
LEFT JOIN ytd ON cw.ASIN=ytd.ASIN
LEFT JOIN ly ON cw.ASIN=ly.ASIN
LEFT JOIN inv ON cw.ASIN=inv.ASIN
LEFT JOIN bb ON cw.ASIN=bb.ASIN
ORDER BY cw.cw_ops DESC, cw.ASIN
LIMIT 15
```

The `ORDER BY cw_ops DESC, ASIN` is kept for the `LIMIT 15` cut, but the final Products-table order is **re-pinned in the builder** (`asins.sort(key=lambda a: (-(a.get("cw_ops") or 0), a.get("asin") or ""))`) before embedding, so the rendered order is identical regardless of the SQL row order, a tie-break gap, or how a run reshaped `q_asins.json`.

For vendors (keep `ReportingDate` as the date column — do **not** rename it to `Date`):
- Replace `SellerSales` → `VendorSales`. The per-ASIN measure columns differ, so map them explicitly:
  `ProductSales → VendorTotalSales` (→ `cw_ops`/`lw_ops`/`t4_ops`/`ytd_ops`/`ly_ops`),
  `UnitsOrdered → VendorShippedUnits` (→ `*_units`), and `ReturnQuantity → VendorReturnQuantity`
  (→ `cw_returns`/`ytd_returns`). `Name`, `CategoryName`, and `ASIN` are the same. So a vendor CW CTE reads
  `sum(VendorTotalSales) cw_ops, sum(VendorShippedUnits) cw_units, sum(VendorReturnQuantity) cw_returns`.
- Replace `SellerTraffic` → `VendorTraffic` and **drop the BB join entirely** (vendor has no Buy Box); set
  `bb_cw` to `null` per the missing-metric sentinel in Q2.
- Vendor uses `VendorInventory` not `AsinFbaInventory`. Map the fields **exactly** as follows (do not
  re-derive per run — this previously diverged): `oh = SellableOnHandInventoryUnits`,
  `inbound = OpenPurchaseOrderUnits`, `reserved = 0` (vendor has no customer-reserved concept; always
  zero, never mapped to UnsellableOnHandInventoryUnits or any other column). Take the **latest snapshot
  per ASIN** via `argMax(col, ReportingDate)` so a single row per ASIN is selected deterministically.
  The vendor `inv` CTE therefore reads:

  ```sql
  inv AS (
    SELECT ASIN,
      argMax(SellableOnHandInventoryUnits, ReportingDate) oh,
      0 reserved,
      argMax(OpenPurchaseOrderUnits, ReportingDate) inbound
    FROM VendorInventory WHERE Partner='{BRAND}' AND Account='{ACCOUNT}'
    GROUP BY ASIN
  )
  ```

Shape into `q_asins.json`:

```json
{
  "asins": [
    {"asin": "B0EXAMPLE1", "name": "Acme Garden Trowel", "cat": "Patio, Lawn & Garden", "cw_ops": 38000, "cw_units": 1000, "cw_returns": 0,
     "lw_ops": 34000, "lw_units": 900, "t4_units": 3800, "t4_ops": 140000,
     "ytd_ops": 530000, "ytd_units": 14000, "ytd_returns": 0, "ly_ops": 24000, "ly_units": 650,
     "oh": 3500, "reserved": 5300, "inbound": 2100, "bb_cw": 0.98}
  ]
}
```

`cw_returns`/`ytd_returns` come straight from `sum(ReturnQuantity)`, which is **`null`** for an
ASIN with no returns. Pass whatever the query returns (`null` or a number) — the builder drops
both fields at load (they are not rendered or used), so the choice of `null` vs `0` vs omitting
the key can no longer drift the output. They are shown here only to document the shape.

As in Q2, pass `bb_cw` (`avg(BuyBoxPercentage)`) **verbatim** — never pre-rounded. The same 2dp
snap (and the same 3dp double-round caveat) applies to the per-ASIN Buy Box.

Use **shortened** product names (e.g., a raw title like `"Acme Garden Trowel, 3-Pack — Green"` becomes the clean label `"Acme Garden Trowel"`, instead of carrying the full 150-char Amazon title). Apply this **single deterministic rule** so the same product yields the same label on every run — do NOT free-hand or "ask Claude to summarize," since that makes recommendation titles vary run-to-run. Apply the steps **in this exact order** (the order is the rule — out-of-order application changes the bytes):

1. Take the raw `Name` value.
2. **Cut** at the first comma, or the first **separator dash** — a hyphen/en-dash/em-dash *surrounded by spaces* (`" - "`, `" \u2013 "`, `" \u2014 "`) — whichever comes first; keep the part before it. Intra-word hyphens (e.g. `Acme-Pro`, `Mercedes-Benz`, `Semi-Metallic`) are **not** separators and must be kept.
3. **Trim** leading/trailing whitespace.
4. **Cap** at 40 characters (keep the first 40).
5. **Trim again** — capping can leave a trailing space, so trim once more after the cap.
6. Do **not** prepend brand codes/abbreviations (e.g. `"ACM "`) that aren't in the cut result — the label is exactly the output of steps 1–5.

The cap-then-trim ordering (steps 4→5) is mandatory: trimming only before the cap leaves a trailing
space on names whose 40th character is a space (this drifted run-to-run before it was pinned).

**Collision disambiguator.** Catalogs with long, similar titles collapse many ASINs onto the same
40-char label (e.g. several `"Acme Garden Multipack — Stainless Steel V"`). When two or more ASINs would
produce the **same** label after steps 1–5, append ` (·{last4})` where `{last4}` is the last 4
characters of the ASIN — applied deterministically to **every** member of a colliding group (sort the
group by ASIN so the suffixing is order-independent). This keeps lead/rec titles unambiguous; the
table's ASIN sub-label still disambiguates on its own.

This keeps rec titles like "Protect Acme Garden Trowel supply" byte-identical across runs.

> **Contract:** `build_report.py` implements steps 1–5 plus the collision suffix as a `clean_name()`
> helper applied once at load (cap is configurable via `display.asin_name_maxlen`, default 40).
> **Pass the raw `Name` verbatim in `q_asins.json` — never hand-shorten, truncate, or summarize an
> ASIN title yourself.** `clean_name()` is the single source of the rendered label, and it can only
> canonicalize a *raw* title; it cannot reconstruct one a run already shortened, so a hand-trimmed
> title is the one reshape the builder cannot absorb and is the known way to make two runs of the same
> data diverge. The rule is documented above so the output is *predictable*, not so you apply it by
> hand. As a backstop the builder emits a non-fatal stderr note when a supplied name looks
> pre-shortened (sits just under the cap with no comma/dash cut); treat that note as a signal you
> passed a trimmed title instead of the raw one.

### Q5 — Customer / LTV (skip if `has_ltv = false`)

Two queries combined:

```sql
-- NTB breakdown across periods
SELECT * FROM (
WITH
  cw AS (SELECT NewToBrandExistingUnknownBuyer ntb, count(DISTINCT AmazonOrderId) orders, sum(OrderPrice) rev FROM LtvRawOrders WHERE Brand='{BRAND}' AND Account='{ACCOUNT}' AND OrderDateLocal >= '{CW_START}' AND OrderDateLocal < '{CW_END_PLUS1}' GROUP BY ntb WITH ROLLUP),
  lw AS (SELECT NewToBrandExistingUnknownBuyer ntb, count(DISTINCT AmazonOrderId) orders, sum(OrderPrice) rev FROM LtvRawOrders WHERE Brand='{BRAND}' AND Account='{ACCOUNT}' AND OrderDateLocal >= '{LW_START}' AND OrderDateLocal < '{CW_START}' GROUP BY ntb WITH ROLLUP),
  ly AS (SELECT NewToBrandExistingUnknownBuyer ntb, count(DISTINCT AmazonOrderId) orders, sum(OrderPrice) rev FROM LtvRawOrders WHERE Brand='{BRAND}' AND Account='{ACCOUNT}' AND OrderDateLocal >= '{LY_CW_START}' AND OrderDateLocal < '{LY_CW_END_PLUS1}' GROUP BY ntb WITH ROLLUP),
  ytd AS (SELECT NewToBrandExistingUnknownBuyer ntb, count(DISTINCT AmazonOrderId) orders, sum(OrderPrice) rev FROM LtvRawOrders WHERE Brand='{BRAND}' AND Account='{ACCOUNT}' AND OrderDateLocal >= '{YTD_START}' AND OrderDateLocal < '{CW_END_PLUS1}' GROUP BY ntb WITH ROLLUP)
SELECT 'cw' src, ntb, orders, rev FROM cw
UNION ALL SELECT 'lw', ntb, orders, rev FROM lw
UNION ALL SELECT 'ly', ntb, orders, rev FROM ly
UNION ALL SELECT 'ytd', ntb, orders, rev FROM ytd
) ORDER BY src, ntb
```

`WITH ROLLUP` adds one extra row per period whose `ntb` is the empty string `''`: that
row is the authoritative period total — `count(DISTINCT AmazonOrderId)` computed over the
whole period, independent of the buyer-type split. Map it to an `orders` key on the period
(see below). The Conversion KPI is computed from this total, so it stays stable even if a
buyer-type row is dropped or relabelled in the reshape. The three named buyer rows still
feed NTB share. (Buyer types partition orders, so the total equals the sum when all rows are
present; the rollup just makes the total independent of that.) The builder also **pins** this
`orders` key in every period — preferring the supplied rollup, else the buyer-row sum, and always
keeping it — so a reshape that supplied `orders` and one that omitted it embed a byte-identical NTB
block (it is normalized in the same pass that zero-fills the six buyer keys).

```sql
-- LTV summary
SELECT OneTimeRepeat, count(*) buyers, sum(TotalOrderProductSales) total_rev, avg(TotalOrderProductSales) avg_ltv
FROM LtvRawCustomer
WHERE Brand='{BRAND}' AND Account='{ACCOUNT}'
GROUP BY OneTimeRepeat
```

Shape into `q_customer.json`:

```json
{
  "ntb": {
    "cw": {"orders": 3600, "existing_orders": 1900, "existing_rev": 100000, "ntb_orders": 1200, "ntb_rev": 60000, "unknown_orders": 500, "unknown_rev": 28000},
    "lw": {...},
    "ly": {...},
    "ytd": {...}
  },
  "ltv": {
    "repeat_buyers": 80000, "repeat_rev": 20000000, "repeat_avg_ltv": 250.00,
    "onetime_buyers": 130000, "onetime_rev": 5800000, "onetime_avg_ltv": 44.00
  }
}
```

### Q6 — Coupons (skip if `has_coupons = false`)

```sql
SELECT CouponName name,
  toString(StartDateTime) start, toString(EndDateTime) end,
  Clips clips, Redemptions redemptions, Sales sales, TotalDiscount discount, BudgetPercentageUsed budget_pct
FROM CouponPerformance
WHERE Account='{ACCOUNT}' AND EndDateTime >= '{T13_START}'
ORDER BY EndDateTime DESC, CouponId LIMIT 10
```

The `, CouponId` secondary key is a required determinism tie-break, not optional: many
accounts have well over 10 coupons sharing the same latest `EndDateTime`, and without a
secondary key `LIMIT 10` returns an engine-arbitrary subset that can change run-to-run or
after a data re-partition. `CouponId` is unique, so it fully pins both which 10 rows are
selected and their order. (Same convention as Q3's `, type` / `, PortfolioName` and Q4's
`, cw.ASIN`.)

Shape into `q_coupons.json`: `{"coupons": [...]}`.

---

## Step 4 — Build the dashboard

Copy the skill scripts locally (skill directory is read-only):

```bash
cp /mnt/skills/user/weekly-report/scripts/build_report.py /home/claude/
cp /mnt/skills/user/weekly-report/scripts/template.html /home/claude/
```

Run the builder:

```bash
python3 /home/claude/build_report.py \
  --workdir /path/to/workdir \
  --output /mnt/user-data/outputs/{brand-slug}-weekly-report-w{N}.html \
  --template /home/claude/template.html \
  --defaults /mnt/skills/user/weekly-report/config/defaults.json
```

Optional: `--config /path/to/brand_overrides.json` to override thresholds for high-volume brands. Keys are deep-merged over `defaults.json`, so include only what you change:

```json
{
  "buy_box": {"low_bb_threshold": 0.90},
  "advertising": {"roas_warning_threshold": 1.5},
  "statistical": {"z_threshold": 2.0},
  "display": {"top_asins_count": 10}
}
```

Output filename convention: `{brand-slug}-weekly-report-w{week_number}.html`. Slugify the brand name (lowercase, dashes for spaces and `&`/special chars).

`build_report.py` runs a structural self-check before writing the file and **aborts on failure**
(see Step 4.5). Pass `--no-validate` only to bypass it for debugging — never for a deliverable.

---

## Step 4.5 — Validate before presenting (required)

A weekly report is JS-rendered: a single uncaught error blanks the whole dashboard while the file
still *looks* fine on disk. **Never present a report you have not seen render.** Two layers:

**1. Structural self-check (automatic).** `build_report.py` calls `validate()` after injection and
exits non-zero if any of these fail, so a broken file is never written:
- the `__DATA__` placeholder was actually replaced, and the embedded `DATA` parses as JSON;
- every `getElementById('x')` has a matching `id="x"` in the markup;
- no element id is referenced as a bare global (the classic trap: markup has `id="m-week"` but the
  script uses `m_week.textContent=...`. Browsers do **not** reliably expose hyphenated ids as
  globals, and even valid-identifier ids break under strict mode — always grab elements with
  `document.getElementById('m-week')` and assign to a `const` first).

If you hand-edit `template.html`, keep this contract: declare every element ref up front, e.g.
`const m_week = document.getElementById('m-week');`.

**2. Headless render smoke test (do this before `present_files`).** The self-check is static; it
won't catch a runtime `TypeError` from a missing data key. Load the built file in a headless DOM and
assert zero console/window errors plus non-empty render:

**Click through every tab — do NOT check only the default tab.** Tab panels build *lazily on
first click*, not all at load (only Executive Summary builds at load). A chart that breaks on the
Sales & Traffic, Advertising, Customer & LTV, etc. tab will look perfectly clean if you only inspect
the landing tab — this is exactly how a flat-`$NaN` chart shipped undetected before this check was
hardened. The loop below clicks each tab and, per tab, runs the visible-text leak check **and** scans
every SVG `<path>` for `NaN` coordinates in its `d` attribute (the signature of a collapsed/flat chart
whose axis scale resolved to `NaN` — a failure that often leaves no console error and no visible-text
leak, so the path scan is the only thing that catches it).

```bash
cd /home/claude && npm install jsdom --silent
node -e '
const fs=require("fs"),{JSDOM}=require("jsdom");
const html=fs.readFileSync(process.argv[2]||process.argv[1],"utf8"),errs=[];
const dom=new JSDOM(html,{runScripts:"dangerously",pretendToBeVisual:true,
  beforeParse(w){w.addEventListener("error",e=>errs.push(e.error?e.error.stack:e.message));
                 const o=w.console.error;w.console.error=(...a)=>{errs.push(a.join(" "));o(...a);};}});
setTimeout(()=>{const d=dom.window.document;
  const tabs=[...d.querySelectorAll("#tabbar .tab")];
  const rep=[]; let bad=false;
  tabs.forEach(t=>{
    t.click();                       // force the lazy panel to build
    const name=t.textContent.trim();
    // Visible-text leak check: clone body, strip <script>/<style> so the JS source
    // (which legitimately contains the tokens NaN/undefined) is not counted — only rendered DOM text.
    const body=d.body.cloneNode(true);body.querySelectorAll("script,style").forEach(n=>n.remove());
    const leaks=(body.textContent.match(/\$?NaN|\bundefined\b|\[object Object\]/g)||[]).length;
    // Flat-chart check: any path whose d= has NaN means the axis scale broke for THIS tab.
    const badPaths=[...d.querySelectorAll("path")].filter(p=>/NaN/.test(p.getAttribute("d")||"")).length;
    if(leaks||badPaths)bad=true;
    rep.push("  "+name+": leaks="+leaks+" badPaths="+badPaths);
  });
  console.log("errors:",errs.length,"tabs:",tabs.length);
  rep.forEach(r=>console.log(r));
  errs.slice(0,8).forEach(e=>console.log("  ERR -",String(e).slice(0,180)));
  process.exit(errs.length||tabs.length<6||bad?1:0);
},700);
' /mnt/user-data/outputs/{brand-slug}-weekly-report-w{N}.html
```

Expect `errors: 0`, all 6 tabs present, and every tab line reading `leaks=0 badPaths=0`. If any
error, leak, or bad path appears, fix and rebuild — do not present. The leak check strips
`<script>`/`<style>` before counting, so the JS keyword `undefined`/`NaN` in source is never flagged —
only `undefined`/`NaN`/`[object Object]` that escaped into *visible* DOM text (e.g. an unguarded
`ops/units` with zero units). The `badPaths` count catches the orthogonal failure where the chart
renders no visible text but its line collapses to `NaN` coordinates. (`scrollTo`-not-implemented lines
on stderr are a benign jsdom limitation, not a failure.)

---

## Step 5 — Present to the user

Use the `present_files` tool with the output path. Don't try to summarize the data extensively — the dashboard does that. A short framing line is enough:

> "Here's the weekly report for {brand}, week ending {date}. {X} action items and {Y} statistical outliers in the callouts; data through {data_freshness_date}."

**Map the placeholders to the builder's printed output (do not eyeball them):**

- `{X}` = the recommendation count the builder prints as `recs:` (i.e. `len(recs)` in the injected
  data) — the number of items on the Recommended Actions tab.
- `{Y}` = the statistical-outlier callout count the builder prints as `callouts:` (`len(callouts)`).
- `{data_freshness_date}` = `sales_fresh_through`; if ads lag sales, state both
  (e.g. "sales through {sales_fresh_through}, ads through {ads_fresh_through}").

The builder prints `recs:` and `callouts:` to stdout on a successful build; read `{X}`/`{Y}` straight
from those lines so the chat summary always matches the artifact. This is a contract, not a
convention — if the printed labels ever change, update this mapping in lockstep.

**Quote figures from the rendered artifact, not from intermediate query results.** Any number you state in chat (weeks-of-cover, conversion, rec count, NTB share, etc.) must come from the values the builder actually computed and rendered — i.e. the `derived`/`kpis`/`recs` in the data you injected, or the final HTML — never from a raw query output you eyeballed earlier in the run. Intermediate query numbers can differ from what the dashboard shows (e.g. a velocity computed over a different window), and a chat summary that contradicts the dashboard is a defect. When in doubt, re-read the value out of the built data structure before stating it.

---

## Reading the dashboard

The dashboard has 6 tabs:

1. **Executive Summary** — Lead story (largest operational warning if any), 8 KPI cards with sparklines + WoW/T4/YoY comparisons, "What changed this week" callouts (hybrid: operational rules + statistical outliers), 13-week trend chart with metric toggle.
2. **Sales & Traffic** — 13-week OPS chart with LY benchmark, sessions+BB dual-axis chart, period summary table, conversion & ASP trends.
3. **Advertising** — 8 ad KPIs, 13-week ROAS/ACoS/TACoS chart, campaign type donut + table, sortable portfolio table.
4. **Products & Inventory** — Sortable top-N ASIN table with auto-tags (Low BB, OOS Risk, Surge).
5. **Customer & LTV** — NTB share, NTB chart, LTV by buyer type, coupon history.
6. **Recommended Actions** — Auto-generated recommendations prioritized High/Medium/Low.

---

## Configuration: callout thresholds

Defaults are in `/mnt/skills/user/weekly-report/config/defaults.json`. Each section has tunable thresholds with inline comments. Common reasons to override:

| Scenario | Setting to bump |
|---|---|
| Brand has structurally low ROAS (e.g. heavily promotional) | `advertising.roas_warning_threshold` down to 1.5 |
| Brand with thin product line (small ASIN catalog) | `display.top_asins_count` down to 8–10 |
| Brand on cyclical growth (seasonality dominant) | `statistical.z_threshold` up to 2.0 to reduce noise |

---

## Troubleshooting

| Issue | Resolution |
|---|---|
| Brand not found | `SELECT DISTINCT Brand FROM TotalSales WHERE Brand ILIKE '%{partial}%'` — match exact casing |
| Vendor with no traffic data | Some vendor accounts don't report traffic. Skip `q_traffic.json` entirely; dashboard hides those sections |
| ClickHouse error 158 (rows limit) | Add date bounds to all queries. Avoid `count(*)` on full tables — use `WHERE Date >= today() - 120` |
| ClickHouse error 184 (aggregate in WHERE/HAVING) | Wrap aggregates in a CTE, compute ratios in outer SELECT. Never alias an aggregate as the same name as the underlying column |
| ClickHouse UNION ALL ORDER BY fails | Wrap in subquery: `SELECT * FROM (...UNION ALL...) ORDER BY col` |
| Brand has no LY data (recent launch / no vendor LY rows) | Omit `ly` and `ytd_ly` from `q_sales.json` (never zero-fill). The template skips the LY row + benchmark and renders zero-unit ASP as `—` |
| BB column missing for vendor | Skip BB lookup entirely — don't try to map vendor traffic to BB |
| ASIN names too long | Apply the canonical name rule in Step 3 / Q4 (cut → trim → cap at 40 → trim again). Don't invent a different cap here — long names break table layout, but the cap value must match the one rule |
| Output filename has `&` | The build script doesn't slugify automatically — pass an already-slugified path to `--output` |
| Wrong tenant tools loaded | Check the user's connected MCP servers. Each tenant has a separate `{Tenant} Kapoq:run_query` tool. Call `tool_search` with the tenant name if the right one isn't visible |
| Different account active states | Some tables have `IsAccountActive` (advertising) but `SellerSales`/`TotalSales` don't. Only filter on it for advertising tables |
| Report renders blank / only the header shows | A JS error halted rendering. Run the Step 4.5 headless smoke test — it prints the stack. Most common cause is the id/global mismatch below or a data key the template reads but a `q_*.json` didn't supply |
| `ReferenceError: x is not defined` at load | The script references an element as a bare global (`m_week`) instead of fetching it (`document.getElementById('m-week')`). Hyphenated ids are never exposed as globals, and even valid ids fail under strict mode. The builder's `validate()` now flags this before writing; if you hand-edit the template, declare every element ref via `getElementById` up front |
| Branding URL scrape fails | The sandbox's outbound network is restricted to a small allow-list (npm, pypi, github, anthropic.com, etc.) and does NOT include arbitrary brand websites. A scrape attempt will return `None` with a connection error — that's expected, not a bug. Fall back to asking for hex codes (Branch E), or better yet, ask the user to upload a screenshot of the homepage (Branch C). If you need URL scraping to actually work, the user's org owner must add the brand domain to the network allow-list |
| Logo too large for embedding | Cap the base64 logo at ~2MB before embedding. SVG is preferred (small, scales cleanly). For PNG/JPG, the build script can downscale via PIL if needed, but the easier path is to ask the user for a smaller version |
| Logo color clashes with header gradient | The default header is a dark slate gradient (`#0f172a` → `#1e293b`). If the user's logo is also dark, wrap the `<img>` in a white-background div (already done in the snippet above with `background:#fff;padding:6px 10px;border-radius:6px`). For dark-mode brand logos, allow override via a `branding.logo_bg = "transparent"` field |

---

## Differences from the WBR skill

| | WBR | Weekly Report |
|---|---|---|
| Output format | PPTX (11 slides) | Single HTML file (6 tabs) |
| Interactivity | None — static | Sortable tables, tab navigation, chart toggles |
| Tables used | ~5 (sales, traffic, ads, inventory, search terms) | adds LTV, Coupons (plus traffic, ads, inventory) |
| Brand-specific assumptions | Tenant-template oriented | Brand-agnostic, vendor-or-seller, graceful section hiding |
| Tenant | Single-tenant historically | Any Kapoq tenant |
| Auto-narrative | Manual callouts in Recommendations slide | Hybrid: operational rules + statistical outliers |
| Best for | Sending decks to clients/leadership | Self-serve interactive review for any audience |

If a user says "weekly review" without specifying format, ask which they want. Default to Weekly Report if they say "interactive" or "dashboard"; default to WBR if they say "deck", "presentation", or "PPT".
