#!/usr/bin/env python3
"""
build_recap.py — Build the event recap dashboard from a context.json.

The heavy lifting (deltas, lift, quality signals, narrative) lives in the template's
JS so output is deterministic given fixed input. This builder only:
  1. validates the context,
  2. assembles the `meta` / `flags` strings and labels,
  3. injects the data object into the template,
  4. prints a one-line summary.

Usage:
  python build_recap.py --context context.json \
      --template assets/template.html --output out.html
"""
import argparse, json, sys, os

# ---- ad-day key normalization -------------------------------------------------
AD_KEYS = ["impr", "clk", "cost", "adsales", "orders", "ntbO", "ntbS"]

def norm_ad(a):
    if a is None:
        return None
    return {k: (a.get(k, 0) or 0) for k in AD_KEYS}

def norm_side(side):
    """Pad/normalize one year's daily block to a clean shape."""
    return {
        "ad":       [norm_ad(x) for x in side.get("ad", [])],
        "sales":    side.get("sales", []),
        "units":    side.get("units", []),
        "sessions": side.get("sessions", []),
        "pv":       side.get("pv", []),
        "dates":    side.get("dates", []),
        "complete": side.get("complete", []),
    }

def win_label(day_labels):
    if not day_labels:
        return ""
    return day_labels[0] if len(day_labels) == 1 else f"{day_labels[0]}–{day_labels[-1]}"

# ---- deterministic product-name display clip ----------------------------------
# Product names come straight from AsinReference.ProductName (full marketing
# titles can run 150+ chars). Clip to a fixed length at a word boundary here so
# the rendered tables are identical run-to-run regardless of how long the raw
# title is — the model is instructed to pass ProductName verbatim, never to
# paraphrase or shorten it (that was a run-to-run divergence source).
NAME_MAXLEN = 64

def clip_name(s):
    s = (s or "").strip()
    if len(s) <= NAME_MAXLEN:
        return s
    cut = s[:NAME_MAXLEN]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" -–—,/&") + "…"

def clip_names(rows):
    out = []
    for r in (rows or []):
        r = dict(r)
        if "name" in r:
            r["name"] = clip_name(r.get("name"))
        out.append(r)
    return out

def build_name_map(raw):
    """Normalize the optional `names` context field (dict asin->name OR list of
    {asin,name}) into one ASIN->name map, ignoring empties and ASIN-as-name."""
    m = {}
    if isinstance(raw, dict):
        items = raw.items()
    else:
        items = ((r.get("asin"), r.get("name")) for r in (raw or []))
    for a, n in items:
        if a and n and n != a:
            m[a] = n
    return m

def resolve_and_clip(rows, name_map):
    """Single source of truth for display names: override each row's name from the
    canonical name map (so a row can never show a title in one run and the bare
    ASIN in another), fall back to the row's own name, then the ASIN; clip for
    display. Replaces per-query name resolution as the pinned, deterministic path."""
    out = []
    for r in (rows or []):
        r = dict(r)
        a = r.get("asin")
        nm = name_map.get(a) if a else None
        r["name"] = clip_name(nm or r.get("name") or (a or ""))
        out.append(r)
    return out

# ---- deterministic campaign-type normalization --------------------------------
# Raw CampaignType values arrive in varied casing/number ("Sponsored Product" vs
# "Sponsored Products"); without pinning, runs label and order them differently.
# Map to canonical plural labels and sort by a fixed ad-type order.
CTYPE_ORDER = ["Sponsored Products", "Sponsored Brands",
               "Sponsored Brands Video", "Sponsored Display"]

def canon_ctype_label(t):
    s = (t or "").lower()
    if "product" in s:
        return "Sponsored Products"
    if "brand" in s and "video" in s:
        return "Sponsored Brands Video"
    if "brand" in s:
        return "Sponsored Brands"
    if "display" in s:
        return "Sponsored Display"
    return (t or "").strip()

def canon_deal_label(t):
    """Canonicalize deal-type labels so they don't drift between runs
    ("Best Deal" vs "Best Deals", raw "BEST_DEAL", etc.)."""
    s = (t or "").lower().replace("_", " ")
    if "lightning" in s:
        return "Lightning Deals"
    if "best deal" in s:
        return "Best Deals"
    if "deal of the day" in s or "dotd" in s:
        return "Deal of the Day"
    if "coupon" in s:
        return "Coupons"
    return (t or "").strip() or "Deals"

def norm_ctype(rows):
    out = []
    for r in (rows or []):
        r = dict(r)
        r["type"] = canon_ctype_label(r.get("type"))
        out.append(r)
    order = {lab: i for i, lab in enumerate(CTYPE_ORDER)}
    out.sort(key=lambda r: (order.get(r["type"], len(CTYPE_ORDER)), r["type"]))
    return out

_MON = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

def fmt_window(start, end):
    """Deterministic deal-window label from ISO date strings (YYYY-MM-DD).
    Promotion start/end are datetimes that often cross midnight, so a model
    formatting them by hand produces 'Jul 9' on one run and 'Jul 9-10' on the
    next. Formatting here from the date parts pins it."""
    def parse(s):
        s = (s or "").strip()[:10]
        try:
            y, m, d = s.split("-")
            return int(m), int(d)
        except Exception:
            return None
    a = parse(start); b = parse(end)
    if a is None:
        return None
    if b is None or a == b:
        return f"{_MON[a[0]]} {a[1]}"
    if a[0] == b[0]:
        return f"{_MON[a[0]]} {a[1]}–{b[1]}"
    return f"{_MON[a[0]]} {a[1]} – {_MON[b[0]]} {b[1]}"

def format_deals(rows, name_map=None):
    """Resolve the display name from the canonical map, clip it, and recompute the
    window from start/end dates so deal rows render identically run-to-run. Falls
    back to a model-supplied `window` only if start/end are absent."""
    out = []
    for r in (rows or []):
        r = dict(r)
        a = r.get("asin")
        nm = name_map.get(a) if (name_map and a) else None
        r["name"] = clip_name(nm or r.get("name") or (a or ""))
        w = fmt_window(r.get("start"), r.get("end"))
        if w is not None:
            r["window"] = w
        out.append(r)
    return out

def sort_deals(rows, cap=None):
    """Deterministic deal ordering, decided by the data not by year: if any row
    carries metrics, order by revenue then units desc (finalized view); otherwise
    by asin (genuinely-pending view). This is the 'has metrics -> finalized' rule —
    so an in-progress deal that already reports units/revenue is treated the same
    in every run. Cap to the top `cap` rows when given; ties broken by asin."""
    rows = list(rows or [])
    has_metrics = any((d.get("revenue") or d.get("units")) for d in rows)
    if has_metrics:
        rows.sort(key=lambda d: (-(d.get("revenue") or 0), -(d.get("units") or 0), d.get("asin", "")))
    else:
        rows.sort(key=lambda d: (d.get("asin", ""), d.get("window", "")))
    if cap is not None:
        rows = rows[:cap]
    return rows

def round_money(obj):
    """Recursively round float values to cents so sub-cent float-summation
    noise (which varies with row order) cannot make two runs differ."""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return round(obj, 2)
    if isinstance(obj, list):
        return [round_money(x) for x in obj]
    if isinstance(obj, dict):
        return {k: round_money(v) for k, v in obj.items()}
    return obj

# ---- inventory layer ----------------------------------------------------------
# The inventory tables (AsinFbaInventory / VendorInventory) are a CURRENT snapshot
# with no history, so this layer reads present availability — it flags products out
# of stock now and estimates days of cover. It is NOT a record of stock during the
# event. Status is computed deterministically here so it never drifts run-to-run.

def inv_status(avail, inbound, units, elapsed_days, remaining_days):
    avail = avail or 0
    inbound = inbound or 0
    rate = (units or 0) / elapsed_days if elapsed_days else 0
    cover = int(avail // rate) if rate > 0 else None
    if avail == 0 and inbound == 0:
        status = "out"               # out of stock, no replenishment inbound
    elif avail == 0:
        status = "inbound"           # out now, restock on the way
    elif cover is not None and remaining_days > 0 and cover < remaining_days:
        status = "low"               # in stock but won't last the rest of the event
    else:
        status = "ok"
    return {"avail": avail, "inbound": inbound, "cover": cover, "status": status}

def attach_inventory(rows, inv_map, elapsed_days, remaining_days, day1=False):
    out = []
    for r in rows:
        r = dict(r)
        inv = inv_map.get(r.get("asin"))
        if inv is not None:
            edays = 1 if day1 else max(1, elapsed_days)
            r["inv"] = inv_status(inv.get("avail"), inv.get("inbound"),
                                   r.get("units26", 0), edays, remaining_days)
        out.append(r)
    return out

def build_stock_watch(rows, event, py_year, cur_sym):
    """Exec 'Stock watch' callout: out-of-stock products that mattered last year.
    Deterministic ordering (prior-year sales desc, then asin)."""
    flagged = [r for r in rows
               if r.get("inv") and r["inv"]["status"] in ("out", "inbound")
               and (r.get("sales25") or 0) > 0]
    if not flagged:
        return ""
    flagged.sort(key=lambda r: (-(r.get("sales25") or 0), r.get("asin", "")))
    money = lambda x: f"{cur_sym}{(x or 0):,.0f}"
    n = len(flagged)
    top = flagged[0]
    head = (f"Stock watch — {n} product{'s' if n != 1 else ''} in this recap "
            f"{'are' if n != 1 else 'is'} out of stock right now")
    lead = (f"The biggest is <b>{top['name']}</b>, which did {money(top.get('sales25'))} during "
            f"{py_year} {event} but only {money(top.get('sales26'))} so far this year — a likely "
            f"stock-driven miss.")
    li = "".join(
        f"<li style='margin:3px 0'><b>{r['name']}</b> "
        f"<span class='mt'>{r['asin']}</span> — {money(r.get('sales25'))} in {py_year} "
        f"&rarr; {money(r.get('sales26'))} so far"
        f"{' &middot; restock inbound' if r['inv']['status'] == 'inbound' else ' &middot; nothing inbound'}</li>"
        for r in flagged[:6])
    note = ("Inventory is a live snapshot of fulfillable units now, not a record of stock during "
            "the event — treat it as the current read, not proof of a mid-event outage.")
    return (f"<h3>{head}</h3><p>{lead}</p>"
            f"<ul style='margin:8px 0 0;padding-left:18px;color:var(--ink);font-size:13px'>{li}</ul>"
            f"<p class='pending' style='margin-top:11px'>{note}</p>")

# ---- DSP (optional display-ad layer; absent for most brands) ------------------
def dsp_rollup(days, k):
    """Sum a per-day DSP array over the first k days (the comparable basis window)
    and compute rate metrics. Returns None if there was no spend."""
    acc = {"cost": 0.0, "impr": 0, "clk": 0, "sales": 0.0, "orders": 0, "ntbO": 0}
    for d in (days or [])[:k]:
        if not d:
            continue
        for key in ("cost", "impr", "clk", "sales", "orders", "ntbO"):
            acc[key] += d.get(key, 0) or 0
    if acc["cost"] == 0 and acc["sales"] == 0:
        return None
    acc["acos"] = (acc["cost"] / acc["sales"] * 100) if acc["sales"] else None
    acc["roas"] = (acc["sales"] / acc["cost"]) if acc["cost"] else None
    acc["cpm"] = (acc["cost"] / acc["impr"] * 1000) if acc["impr"] else None
    acc["ntbShare"] = (acc["ntbO"] / acc["orders"] * 100) if acc["orders"] else None
    return acc

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", required=True)
    ap.add_argument("--template", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    with open(args.context) as f:
        c = json.load(f)
    with open(args.template) as f:
        tpl = f.read()

    # ---- required keys ---------------------------------------------------------
    required = ["brand", "account", "tenant", "channel", "event_name",
                "cy_year", "py_year", "cy_day_labels", "py_day_labels", "daily"]
    missing = [k for k in required if k not in c]
    if missing:
        sys.exit(f"context.json missing required keys: {missing}")

    cy_labels = c["cy_day_labels"]
    py_labels = c["py_day_labels"]
    evlen = len(cy_labels)
    if len(py_labels) != evlen:
        sys.exit(f"cy_day_labels ({evlen}) and py_day_labels ({len(py_labels)}) must be the same length")

    # daily blocks (attach the per-side day labels + completeness)
    cy = norm_side(c["daily"]["cy"]); cy["dates"] = cy_labels
    py = norm_side(c["daily"]["py"]); py["dates"] = py_labels

    # Completeness: if CY ISO dates are supplied, compute it here as pure calendar
    # arithmetic (date < today) so K is mechanical, not a per-run judgment call.
    # A day is complete iff strictly before the generated date; IsProvisional is
    # irrelevant. Falls back to the model-supplied cy_complete when no ISO dates.
    cy_iso = c.get("cy_dates")
    gen = c.get("generated", "")
    if cy_iso and len(cy_iso) == evlen and gen:
        cy["complete"] = [str(d)[:10] < str(gen)[:10] for d in cy_iso]
    else:
        cy["complete"] = c.get("cy_complete", [True] * evlen)
    py["complete"] = c.get("py_complete", [True] * evlen)

    K = max(1, sum(1 for x in cy["complete"] if x))   # complete current-year days
    partial = K < evlen
    basis_label = "full event" if K >= evlen else ("Day 1" if K == 1 else f"first {K} days")

    # ---- section flags ---------------------------------------------------------
    def any_pos(arr):
        return any(v not in (None, 0) for v in arr)
    show_traffic = any_pos(py["sessions"]) or any_pos(cy["sessions"])
    deals = c.get("deals") or {}
    show_deals = bool(deals.get("py")) or bool(deals.get("cy"))

    cy_year, py_year = c["cy_year"], c["py_year"]
    event = c["event_name"]
    channel = c["channel"]
    mkt = c.get("marketplace", "")
    cur_sym = c.get("currency_symbol", "$")
    currency = c.get("currency", "USD")
    tenant = c["tenant"]
    generated = c.get("generated", "")

    chan_label = "Amazon Seller Central" if channel == "seller" else "Amazon Vendor Central"
    cy_win, py_win = win_label(cy_labels), win_label(py_labels)

    # ---- basis note (recap framing) -------------------------------------------
    if partial:
        basis_note = (
            f"<b>How to read this recap.</b> A retrospective read, not a live monitor. "
            f"{event} {py_year} ran <b>{py_win}</b> ({evlen} full days). For {cy_year}, the captured data covers "
            f"<b>{K} complete day{'s' if K!=1 else ''}</b> plus a partial day; later days fall outside this dataset. "
            f"{basis_label} is the only window complete on both sides, so it is the analytical basis — headline KPIs, "
            f"quality signals, lift, and product movers all use <b>{basis_label} vs {basis_label}</b>. "
            f"Partial-window figures (event-to-date rollup, later days) are included for reference only and labeled as such."
        )
    else:
        basis_note = (
            f"<b>How to read this recap.</b> {event} {py_year} ran <b>{py_win}</b> and {cy_year} ran <b>{cy_win}</b> — "
            f"both full {evlen}-day events. All comparisons are full-event, like-for-like."
        )

    # ---- traffic / deals pending notes ----------------------------------------
    traffic_pending = ""
    if show_traffic and not any_pos(cy["sessions"]):
        tft = c.get("traffic_fresh_through")
        traffic_pending = (
            f"Organic sessions are not available for the {cy_year} event in this dataset — "
            f"Amazon's Seller traffic report lagged the data capture"
            + (f" (latest landed {tft})." if tft else ".")
        )

    deals_pending = ""
    if show_deals and deals.get("cy") and not any((d.get("revenue") or d.get("units")) for d in deals["cy"]):
        ctl = deals.get("cy_type_label", "Deals")
        deals_pending = (
            f"{cy_year} {ctl} performance is not in this dataset — these deals report units/revenue only after "
            f"the window closes, so they sit outside this recap."
        )

    # ---- product scopes --------------------------------------------------------
    movers_scope = f"Day 1 — {cy_labels[0]} {cy_year} vs {py_labels[0]} {py_year}, apples-to-apples"
    movers_note = ("Day-1 comparison avoids the partial-window distortion — the cleanest YoY view of which "
                   "products gained or lost ground.")
    if partial:
        todate_scope = f"{cy_year} event-to-date vs {py_year} full event"
        todate_note = ("Different window lengths — read levels, not the gap. Use the Movers view for a fair YoY change.")
    else:
        todate_scope = f"{cy_year} vs {py_year}, full event"
        todate_note = "Full-event totals for both years."

    ct_meta = f"By campaign type · {cy_year} {'event-to-date' if partial else 'full event'} vs {py_year} full event"
    lift_meta = "Event day vs the pre-event daily run-rate"
    if c.get("cy_baseline_window") and c.get("py_baseline_window"):
        lift_meta += f" (current: {c['cy_baseline_window']} · prior: {c['py_baseline_window']})"

    sales_tbl = "VendorSales" if channel == "vendor" else "TotalSales"
    sess_src = "—" if channel == "vendor" else "SellerTraffic"
    footer = (
        f"Source: Kapoq Datalink ({tenant} tenant) · Account: {c['account']} · Recap generated {generated}. "
        f"Recent-day ad metrics from Amazon Marketing Stream (provisional, subject to Amazon restatement). "
        f"Sales/units from {sales_tbl}; sessions from {sess_src}; deals from PromotionPerformance. Currency: {currency}."
    )

    meta = {
        "title": f"{event} Recap — {c['brand']}",
        "crumb": f"{event} · Recap (YoY)",
        "brand": c["brand"],
        "subtitle": f"{event} performance, {cy_year} vs {py_year}",
        "pill": f"Recap · {basis_label} basis",
        "acct": [{"l": "Account", "v": c["account"]},
                 {"l": "Channel", "v": chan_label + (f" ({mkt})" if mkt else "")}],
        "cyLabel": str(cy_year), "pyLabel": str(py_year),
        "cyYear": cy_year, "pyYear": py_year,
        "eventName": event,
        "cyDates": cy_labels, "pyDates": py_labels,
        "currencySymbol": cur_sym, "currency": currency,
        "basisNote": basis_note,
        "primaryMeta": f"{cy_labels[0]}, {cy_year} vs {py_labels[0]}, {py_year} — the comparable complete window on both sides",
        "liftMeta": lift_meta,
        "moversScope": movers_scope, "moversNote": movers_note,
        "todateScope": todate_scope, "todateNote": todate_note,
        "ctMeta": ct_meta,
        "trafficPending": traffic_pending,
        "dealsPending": deals_pending,
        "footer": footer,
    }

    flags = {"completeCyDays": K, "partial": partial,
             "showTraffic": show_traffic, "showDeals": show_deals}

    # ---- inventory: attach per-product stock status + exec stock-watch ---------
    # Event-to-date products cover the K complete days (the in-progress partial
    # day is excluded), so the days-of-cover run-rate denominator is K, and the
    # event has (evlen - K) days still to run.
    elapsed_days = K
    remaining_days = max(0, evlen - K)
    cap = int(c.get("product_top_n", 14))   # safety-net cap (movers has no query LIMIT historically)
    # One canonical ASIN->name map applied to every list, so a product can never
    # render as a title in one run and the bare ASIN in another (a residual
    # name-resolution drift). Falls back to the row's own name when the map is absent.
    name_map = build_name_map(c.get("names"))
    inv_map = {r["asin"]: r for r in (c.get("inventory") or []) if r.get("asin")}
    movers_out = attach_inventory(resolve_and_clip(c.get("movers", []), name_map)[:cap], inv_map,
                                  elapsed_days, remaining_days, day1=True)
    todate_out = attach_inventory(resolve_and_clip(c.get("products", []), name_map)[:cap], inv_map,
                                  elapsed_days, remaining_days, day1=False)
    # Stock-watch candidates: event-to-date products plus any day-1 movers not
    # already present (deduped by ASIN), so an out-of-stock item in either list
    # is caught — using the row with the larger prior-year sales when both exist.
    sw_rows = {}
    for r in todate_out + movers_out:
        a = r.get("asin")
        if a is None:
            continue
        prev = sw_rows.get(a)
        if prev is None or (r.get("sales25") or 0) > (prev.get("sales25") or 0):
            sw_rows[a] = r
    meta["stockWatch"] = build_stock_watch(list(sw_rows.values()), event, py_year, cur_sym) if inv_map else ""
    flags["showInventory"] = bool(inv_map)

    # ---- DSP (optional): roll up to the comparable first-K-days window both sides
    # so it is like-for-like with the sponsored KPIs. Reported as its own card and
    # NOT folded into the sponsored ACoS (different, 14-day attribution). Hidden
    # entirely when the brand ran no DSP — which is most of them.
    dsp_in = c.get("dsp") or {}
    dsp_cy_days = dsp_in.get("cy", []) or []
    dsp_py_days = dsp_in.get("py", []) or []
    dsp_cy = dsp_rollup(dsp_cy_days, K)
    dsp_py = dsp_rollup(dsp_py_days, K)
    show_dsp = bool(dsp_cy or dsp_py)
    dsp_out = None
    if show_dsp:
        dsp_out = {
            "show": True,
            "meta": f"{basis_label} {cy_year} vs {py_year} · 14-day attributed",
            "cy": dsp_cy or {}, "py": dsp_py or {},
            "note": "DSP is reported separately and uses 14-day attribution, so it is "
                    "not added into the Sponsored ACoS above.",
        }
    flags["showDsp"] = show_dsp

    # deal type labels onto the deals object the template reads.
    # When there are no deals (showDeals false), emit an EMPTY object with no
    # model prose — the Deals tab is hidden, so any insight/takeaway/coupons text
    # the model supplied would otherwise be written (silently) into hidden markup
    # and diverge run-to-run. Prose is only carried when deals actually exist.
    if show_deals:
        deal_cap = int(c.get("deal_top_n", c.get("product_top_n", 14)))
        deals_out = {
            "py": sort_deals(format_deals(deals.get("py", []), name_map), cap=deal_cap),
            "cy": sort_deals(format_deals(deals.get("cy", []), name_map), cap=deal_cap),
            # Type labels are derived/canonicalized by the builder (Best Deal -> Best
            # Deals, BEST_DEAL -> Best Deals, etc.) so plural/singular never drifts.
            "pyTypeLabel": canon_deal_label(deals.get("py_type_label") or deals.get("py_type")),
            "cyTypeLabel": canon_deal_label(deals.get("cy_type_label") or deals.get("cy_type")),
            "couponsNote": deals.get("coupons_note", ""),
            # Pinned label — fixed so it does not drift between the builder default
            # and a model-authored head. The narrative body (insight / takeaway)
            # stays model-authored on purpose.
            "insightHead": "Deal contribution",
            "insight": deals.get("insight", ""),
            "takeaway": deals.get("takeaway", ""),
        }
    else:
        deals_out = {"py": [], "cy": [], "pyTypeLabel": "Deals", "cyTypeLabel": "Deals",
                     "couponsNote": "", "insightHead": "Deal contribution",
                     "insight": "", "takeaway": ""}

    D = {
        "meta": meta, "flags": flags,
        "daily": {"cy": cy, "py": py},
        "baseline": c.get("baseline", {"cy": {"sales": [], "units": [], "adsales": []},
                                       "py": {"sales": [], "units": [], "adsales": []}}),
        "keywords": c.get("keywords", {}),
        "products": {"movers": movers_out, "todate": todate_out},
        "ctype": norm_ctype(c.get("ctype", [])),
        "deals": deals_out,
        "dsp": dsp_out,
    }

    payload = json.dumps(round_money(D), ensure_ascii=False)
    if "/*__DATA__*/ null" not in tpl:
        sys.exit("template is missing the /*__DATA__*/ null injection point")
    html = tpl.replace("/*__DATA__*/ null", payload).replace("__TITLE__", meta["title"])

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w") as f:
        f.write(html)

    kw_cy = len(c.get("keywords", {}).get("cy", []))
    kw_py = len(c.get("keywords", {}).get("py", []))
    print(f"built: {args.output}")
    print(f"basis: {basis_label} (K={K}/{evlen})  partial={partial}")
    print(f"flags: showTraffic={show_traffic} showDeals={show_deals} showInventory={flags['showInventory']} showDsp={show_dsp}")
    print(f"keywords: cy={kw_cy} py={kw_py}  movers={len(movers_out)}  products={len(todate_out)} (cap {cap})")
    n_oos = sum(1 for r in todate_out if r.get('inv') and r['inv']['status'] in ('out', 'inbound'))
    print(f"inventory: rows={len(inv_map)}  out-of-stock-in-recap={n_oos}  stockWatch={'yes' if meta['stockWatch'] else 'no'}")

if __name__ == "__main__":
    main()
