#!/usr/bin/env python3
"""
Weekly Report builder.

Reads the q_*.json files produced during Step 3, computes KPIs / callouts /
recommendations, injects the result into template.html, writes a single
self-contained HTML file, and runs a structural self-check before returning.

Usage:
  python3 build_report.py \
    --workdir /path/to/workdir \
    --output  /mnt/user-data/outputs/{brand-slug}-weekly-report-w{N}.html \
    --template ./template.html \
    --defaults ../config/defaults.json \
    [--config /path/to/brand_overrides.json] \
    [--no-validate]
"""
import argparse, json, statistics, re, sys
from datetime import date, timedelta


# ---------------------------------------------------------------- helpers
def pct(n, d):
    return (n / d - 1) * 100 if d else 0


def safe(n, d):
    # Null-safe on BOTH operands: a missing numerator (e.g. orders on a
    # no-LTV account) returns 0 rather than raising TypeError. This is the
    # canonical guard — every ratio in this builder routes through it.
    if n is None or not d:
        return 0
    return n / d


def deep_merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load(workdir, name, required=True, default=None):
    p = f"{workdir}/{name}"
    try:
        with open(p) as f:
            return json.load(f)
    except FileNotFoundError:
        if required:
            sys.exit(f"ERROR: required input {p} not found")
        return default


# ---------------------------------------------------------------- name + number canonicalization
def clean_name(raw, maxlen=40):
    """Deterministic ASIN label: cut at first comma or *separator* dash, trim, cap,
    trim again. A dash only separates when it's surrounded by whitespace (" - ",
    " \u2013 ", " \u2014 "); intra-word hyphens (e.g. "Acme-Pro", "Mercedes-Benz")
    are kept. Order is fixed (cut -> trim -> cap -> trim) so bytes never depend on
    cap-vs-trim sequencing."""
    if not raw:
        return raw
    s = str(raw)
    cut = len(s)
    i = s.find(",")                              # comma is always a separator
    if i != -1:
        cut = min(cut, i)
    m = re.search(r"\s[-\u2013\u2014]\s", s)     # dash only when space-surrounded
    if m:
        cut = min(cut, m.start())
    s = s[:cut].strip()        # cut, then trim
    s = s[:maxlen].strip()     # cap, then trim again
    return s


def shorten_asin_names(asins, maxlen=40):
    """Apply clean_name once at load; disambiguate any colliding labels by
    appending the ASIN's last 4 chars to every member of a colliding group.

    clean_name() is the single source of the rendered label, but it can only
    canonicalize a RAW title — it cannot reconstruct one a run already hand-shortened
    (SKILL.md Step 3 / Q4 makes passing the raw `Name` a hard contract). We can't prove
    a string was pre-shortened (a legitimately short title is indistinguishable), so this
    is a NON-FATAL nudge only: if a supplied name clean_name would not itself change
    (no comma / separator-dash, not over the cap) yet sits in the window just below the
    cap — exactly where a hand-trimmed long title lands — we note it on stderr and build
    on. A genuinely short title (well under the cap) and a correctly-passed raw long
    title (capped to exactly maxlen) both fall outside the window, so neither is flagged.
    """
    LOOKS_TRIMMED_FLOOR = max(0, maxlen - 6)  # window [maxlen-6, maxlen): truncation zone
    suspect = 0
    for a in asins:
        supplied = a.get("name")
        if supplied and clean_name(supplied, maxlen) == str(supplied) \
                and LOOKS_TRIMMED_FLOOR <= len(str(supplied)) < maxlen:
            suspect += 1
    if suspect:
        sys.stderr.write(
            f"NOTE: {suspect} ASIN name(s) look pre-shortened (length just under the "
            f"{maxlen}-char cap with no comma/dash cut). Pass raw titles verbatim for "
            "reproducible labels; clean_name() cannot recover a hand-shortened title.\n")
    for a in asins:
        a["name"] = clean_name(a.get("name"), maxlen)
    seen = {}
    for a in asins:
        seen[a["name"]] = seen.get(a["name"], 0) + 1
    for a in sorted(asins, key=lambda x: x.get("asin", "")):
        if seen.get(a["name"], 0) > 1:
            a["name"] = f"{a['name']} (\u00b7{str(a.get('asin',''))[-4:]})"
    return asins


def derive_marketplace(account, fallback="Amazon US"):
    """Deterministically derive the marketplace label from the account string
    (e.g. "Brand@Vendor Central US" -> "Amazon US", "Brand@Amazon CA" -> "Amazon CA")
    so it is no longer free-texted per run. Strips the channel words and keeps the
    trailing region token(s); falls back to the provided value if the account has no
    recognizable suffix."""
    if "@" not in (account or ""):
        return fallback
    suffix = account.split("@", 1)[1].strip()
    for w in ("Vendor Central", "Seller Central", "Amazon"):
        if suffix.startswith(w):
            suffix = suffix[len(w):].strip()
            break
    return ("Amazon " + suffix) if suffix else fallback


def normalize_tenant(tenant):
    """Deterministically normalize the tenant label so the footer is identical
    run-to-run. The connected MCP server is surfaced to different runs with or without
    a leading "claude.ai " prefix (e.g. "claude.ai Kapoq Demo" vs "Kapoq Demo"), which
    is the one free-texted, footer-visible field that drifted between otherwise
    byte-identical runs. Strip that prefix and collapse internal whitespace; the result
    is the bare connector display name, which is the form to write into context.json."""
    t = (tenant or "").strip()
    low = t.lower()
    if low.startswith("claude.ai "):
        t = t[len("claude.ai "):].strip()
    return " ".join(t.split())


def _check_coarse_bb(traffic, asins):
    """Deterministic, false-positive-safe guard against a Buy Box value pre-rounded
    upstream to 3 decimals (the one input precision the 2dp snap cannot reconcile with a
    raw run). A real avg(BuyBoxPercentage) over a week is a long float; a value that is
    already its own 3dp rounding (round(x, 3) == x) BUT is not also its own 2dp rounding
    (round(x, 2) != x), and is strictly inside (0, 1), almost certainly came from a hand
    pre-round to 3 decimals. The `round(x, 2) != x` clause is essential: a value tidied to
    2 decimals (e.g. 0.96) satisfies round(x, 3) == x too, but a 2dp input is LEGAL — the
    2dp snap is idempotent on it, so it converges with a raw run and must NOT be flagged
    (see SKILL.md Step 3 Q2). The exact endpoints 0.0 and 1.0 are excluded because they are
    legitimate raw values (e.g. a 100%-Buy-Box ASIN), so this never fires on clean real data.

    Unlike the prior warning, this ABORTS the build. A 3dp pre-round silently shifts the
    rendered Buy Box % for any ASIN in the crossover band (round(round(x, 3), 2) !=
    round(x, 2)), producing a deliverable that is non-reproducible against a run that passed
    raw precision. Failing closed enforces the contract (pass avg(BuyBoxPercentage) verbatim
    — see SKILL.md Step 3 Q2/Q4) instead of relying on someone noticing a warning. The check
    runs before any output is written, so a violating run produces no file. The abort message
    is identical on every run that hits the same input."""
    def coarse(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool) \
            and 0.0 < x < 1.0 and round(x, 3) == x and round(x, 2) != x
    hits = []
    for r in traffic.get("weekly_13w", []):
        if coarse(r.get("bb")):
            hits.append(f"traffic week {r.get('wk')} bb={r.get('bb')}")
    if isinstance(traffic.get("ly"), dict) and coarse(traffic["ly"].get("bb")):
        hits.append(f"traffic ly bb={traffic['ly'].get('bb')}")
    for a in asins:
        if coarse(a.get("bb_cw")):
            hits.append(f"ASIN {a.get('asin')} bb_cw={a.get('bb_cw')}")
    if hits:
        sys.exit("BUILD FAILED: Buy Box appears pre-rounded to 3 decimals for "
                 f"{len(hits)} value(s); the 2dp snap is not idempotent on a 3dp input, so "
                 "this would drift the rendered Buy Box % vs a run that passed raw precision. "
                 "Pass avg(BuyBoxPercentage) verbatim, or tidied to at most 2 decimals, and "
                 "rebuild. Affected: " + "; ".join(hits[:6]) + (" ..." if len(hits) > 6 else ""))


_MONEY_KEYS = frozenset({
    "ops", "spend", "ad_sales",
    "existing_rev", "ntb_rev", "unknown_rev",
    "repeat_rev", "onetime_rev",
    "cw_ops", "lw_ops", "ly_ops", "t4_ops", "ytd_ops",
})


def _check_coarse_money(**groups):
    """Deterministic, low-false-positive guard against money ROLLUPS that arrived
    pre-rounded to whole dollars or one decimal instead of passed as the raw 2dp sum
    (the contract is the same as Buy Box: pass query values verbatim — see SKILL.md).

    Why this is NOT a per-value check like _check_coarse_bb. Buy Box has a safe band: a
    raw avg() is a long float, so `round(x,3)==x and round(x,2)!=x` is essentially never
    true on real data, and a single hit is decisive. Money has NO such band — a real
    summed total (a sum of 2dp order values) lands on a whole dollar ~1% of the time and
    on a single decimal ~10% of the time purely by chance, so flagging an INDIVIDUAL
    coarse money value WOULD false-positive on a legitimate whole-dollar total. That is
    exactly the false-positive the recommendation warns about.

    The robust signal is therefore AGGREGATE. A model that pre-rounds money does it
    systematically (a blanket round() in the SQL, or a tidied reshape), so almost EVERY
    money value comes back at <=1dp at once. Chance explains one or two coincidences in a
    report; it cannot explain nearly all of them. We collect the non-trivial money values
    (abs >= MONEY_MIN, which drops small legit whole values like a $50 spend), require a
    meaningful sample, and abort only when almost the entire sample is coarse — a
    combination that is astronomically unlikely on raw 2dp data but is the fingerprint of
    a systematic pre-round.

    Two detection bands, both fail closed:
      (a) GLOBAL — the original test. If almost the entire pooled sample is coarse the
          run pre-rounded everything; abort. This is the systematic-pre-round target.
      (b) PER-GROUP — catches a PARTIAL pre-round that stays under the global threshold,
          e.g. a run that tidies q_sales money but leaves ads/asin/customer raw. Each
          group (sales, ads, asin, customer) is judged on its OWN sample only when that
          sample is large enough to be decisive (>= MIN_SAMPLE). A group is "coarse" on
          the same near-100% band as the global test (>=90% AND all-but-one), and "raw"
          only when clearly fine-grained (<= RAW_MAX coarse). We abort only when at least
          one well-sampled group is coarse AND at least one OTHER well-sampled group is
          clearly raw — the fingerprint of a partial pre-round. A lone legitimate
          whole-dollar total cannot make its group ~100% coarse, and a small group (under
          the sample floor) is never judged in isolation, so neither false-positives.

    Fails closed like _check_coarse_bb (runs before any file is written; no output on a
    violating run) and the abort message is identical on every run that hits the same
    input. _MONEY_KEYS lists only the rollup fields accepted pre-summed from the model;
    derived ratios (roas/acos/ctr) and builder-computed averages (repeat/onetime LTV) are
    excluded on purpose — they are recomputed downstream and are not an upstream
    pre-round risk."""
    MONEY_MIN = 100.0
    MIN_SAMPLE = 6     # pooled floor for the global test, and per-group floor for (b)
    RAW_MAX = 0.5      # a group is "clearly raw" when at most half its values are coarse

    def collect(o, out):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    if k in _MONEY_KEYS and abs(v) >= MONEY_MIN:
                        out.append(v)
                else:
                    collect(v, out)
        elif isinstance(o, list):
            for v in o:
                collect(v, out)

    def is_coarse_value(v):
        return round(v, 1) == v   # <=1 decimal place (0dp or 1dp)

    # Per-group samples, in a fixed key order so the abort message is deterministic.
    per_group = {}
    for name in sorted(groups):
        g = []
        collect(groups[name], g)
        per_group[name] = g

    pooled = [v for g in per_group.values() for v in g]
    if len(pooled) < MIN_SAMPLE:
        return  # too small a sample to separate any pre-round from chance

    # (a) GLOBAL: blanket pre-round. Allow at most one chance coincidence AND require
    # >=90% coarse, so a lone legitimate whole-dollar total never trips it.
    coarse_pooled = [v for v in pooled if is_coarse_value(v)]
    if len(coarse_pooled) >= len(pooled) - 1 and len(coarse_pooled) / len(pooled) >= 0.9:
        sys.exit("BUILD FAILED: money rollups appear pre-rounded to <=1 decimal place "
                 f"({len(coarse_pooled)} of {len(pooled)} non-trivial money values are "
                 "<=1dp; a raw 2dp sum almost never is). A systematic pre-round drifts "
                 "rendered totals vs a run that passed raw 2dp sums. Pass money rollups "
                 "verbatim — the raw sums ClickHouse returns, not rounded to whole dollars "
                 "or one decimal — and rebuild.")

    # (b) PER-GROUP: partial pre-round. Only well-sampled groups are judged in isolation.
    coarse_groups, raw_groups = [], []
    for name, g in per_group.items():
        if len(g) < MIN_SAMPLE:
            continue
        c = sum(1 for v in g if is_coarse_value(v))
        if c >= len(g) - 1 and c / len(g) >= 0.9:
            coarse_groups.append((name, c, len(g)))
        elif c / len(g) <= RAW_MAX:
            raw_groups.append(name)
    if coarse_groups and raw_groups:
        cg = ", ".join(f"{n} ({c}/{t} <=1dp)" for n, c, t in coarse_groups)
        sys.exit("BUILD FAILED: some money groups appear pre-rounded to <=1 decimal place "
                 f"while others are raw 2dp ({cg} coarse vs {', '.join(raw_groups)} raw). "
                 "A partial pre-round drifts rendered totals vs a run that passed every "
                 "group's raw 2dp sums. Pass all money rollups verbatim — the raw sums "
                 "ClickHouse returns, not rounded to whole dollars or one decimal — and rebuild.")


def _check_week_consistency(ctx):
    """Fail-closed guard that the week fields are internally consistent — the
    builder cannot see the per-query date bounds (T13_START, LY_CW_END, ...) the
    run substitutes upstream, so it cannot catch a window scanned over the wrong
    dates; the new resolve_window.py helper pins that math in code. What the builder
    CAN do cheaply is verify the three week fields it does receive form a valid
    Sun-Sat pair: week_end is a Saturday, week_start is exactly six days earlier,
    and week_number is the ISO week of week_end (SKILL.md Step 2D / line ~294).

    This is independent of HOW the week was chosen (auto-anchor or an explicit
    user-requested week both produce a real Sun-Sat pair), so a legitimate run never
    trips it — only a transposed or fat-fingered field does. Aborts before any file
    is written, like the other guards, with a deterministic message."""
    we_s, ws_s = ctx.get("week_end"), ctx.get("week_start")
    if not we_s or not ws_s:
        return  # freshness/week fields are optional inputs; nothing to check
    try:
        we = date.fromisoformat(we_s)
        ws = date.fromisoformat(ws_s)
    except (ValueError, TypeError):
        sys.exit(f"BUILD FAILED: week_start/week_end are not ISO dates "
                 f"(week_start={ws_s!r}, week_end={we_s!r}).")
    problems = []
    if we.weekday() != 5:  # Mon=0 .. Sat=5
        problems.append(f"week_end {we_s} is not a Saturday")
    if ws != we - timedelta(days=6):
        problems.append(f"week_start {ws_s} is not week_end - 6 days ({we_s})")
    wn = ctx.get("week_number")
    iso_wn = we.isocalendar()[1]
    if wn is not None and wn != iso_wn:
        problems.append(f"week_number {wn} is not the ISO week of {we_s} ({iso_wn})")
    if problems:
        sys.exit("BUILD FAILED: week fields are inconsistent — "
                 + "; ".join(problems)
                 + ". Re-derive the window with scripts/resolve_window.py and rebuild.")


def canon(x):
    """Canonicalize numbers so upstream/computed float precision can't drift the
    embedded DATA blob: ints stay ints, integral floats become ints, other floats
    round to 4 dp. Strings/None/bools pass through."""
    if isinstance(x, bool):
        return x
    if isinstance(x, float):
        r = round(x, 4)
        return int(r) if r == int(r) else r
    if isinstance(x, dict):
        return {k: canon(v) for k, v in x.items()}
    if isinstance(x, list):
        return [canon(v) for v in x]
    return x


def strip_private(x):
    """Drop any dict key that starts with an underscore, recursively, before the data is
    embedded. None of the documented q-object / output shapes use underscore-prefixed
    keys, so this is a no-op on conformant data; it only removes stray fields a
    non-conformant reshape might carry in (e.g. a debug "_note"), so they cannot leak into
    the artifact or drift its bytes. This is the cheap form of fix #4 — the stronger
    alternative (projecting each object onto its exact documented key set) is higher-effort
    and only matters for a reshape that already violates the documented shapes, so it is
    not done here."""
    if isinstance(x, dict):
        return {k: strip_private(v) for k, v in x.items()
                if not (isinstance(k, str) and k.startswith("_"))}
    if isinstance(x, list):
        return [strip_private(v) for v in x]
    return x


# ---------------------------------------------------------------- build
def build_data(workdir, cfg):
    ctx = load(workdir, "context.json")
    # Fail closed if the week fields are not a valid Sun-Sat pair (catches a
    # transposed/fat-fingered week_start/week_end/week_number before anything renders).
    _check_week_consistency(ctx)
    # Marketplace is builder-derived (not free-texted) so the masthead label is
    # identical run-to-run for a given account.
    ctx["marketplace"] = derive_marketplace(ctx.get("account", ""),
                                             ctx.get("marketplace") or "Amazon US")
    # Tenant is builder-normalized (not free-texted) so the footer label is identical
    # run-to-run regardless of whether the connector name arrived with a "claude.ai " prefix.
    ctx["tenant"] = normalize_tenant(ctx.get("tenant", ""))
    sales = load(workdir, "q_sales.json")
    traffic = load(workdir, "q_traffic.json", required=ctx.get("has_traffic", True),
                   default={"weekly_13w": [], "ly": {}})
    ads = load(workdir, "q_ads.json")
    asins = shorten_asin_names(load(workdir, "q_asins.json")["asins"],
                               cfg.get("display", {}).get("asin_name_maxlen", 40))
    cust = load(workdir, "q_customer.json", required=ctx.get("has_ltv", True),
                default={"ntb": {}, "ltv": {}})
    coupons = load(workdir, "q_coupons.json", required=ctx.get("has_coupons", False),
                   default={"coupons": []}).get("coupons", [])

    # Pin the 13-week trend order in the builder, the same way the ASIN table is pinned
    # by asins.sort(...) below. The `weekly_13w` arrays are consumed positionally —
    # ads["weekly_13w"][-1]/[-2] are this/last week, [-4:] is the trailing-4 average, and
    # the *_spark arrays render left-to-right — so a reshape that re-sorted these (e.g.
    # newest-first) would silently flip the chart and mis-pick the current week. The SQL
    # emits them ascending by toStartOfWeek(Date,6); the `wk` values are ISO date strings,
    # so a lexical sort reproduces that order exactly and has a natural unique key. An
    # empty/optional table (e.g. traffic default) sorts to an empty list. Applied before
    # canon(): `wk` is a string, so canon does not touch it and order is preserved.
    for _blk in (sales, traffic, ads):
        if isinstance(_blk, dict) and isinstance(_blk.get("weekly_13w"), list):
            _blk["weekly_13w"].sort(key=lambda r: r.get("wk") or "")

    # Snap Buy Box to 2dp on the 0-1 fraction BEFORE canon. The live
    # avg(BuyBoxPercentage) is full float precision (e.g. 0.9554019455...), so a run
    # that passed it raw and a run that tidied it to 0.96 both land on 0.96 here and
    # converge.
    #
    # IMPORTANT — this snap is idempotent for RAW and 2dp inputs only, NOT for a value a
    # run pre-rounded to 3dp. round(round(x, 3), 2) can cross a 2dp boundary the raw value
    # would not: e.g. round(0.9554019, 2) == 0.96 but round(round(0.9554019, 3), 2)
    # == round(0.955, 2) == 0.95. The information needed to match the raw run is destroyed
    # by the 3dp pre-round, so no builder-side arithmetic can recover it. The contract is
    # therefore: BUY BOX MUST BE PASSED VERBATIM (raw avg(BuyBoxPercentage)) — never
    # pre-rounded — see SKILL.md Step 3 Q2/Q4. _check_coarse_bb() below turns a violation
    # into a hard build failure (before any file is written) instead of silent drift.
    _check_coarse_bb(traffic, asins)
    for _r in traffic.get("weekly_13w", []):
        if _r.get("bb") is not None:
            _r["bb"] = round(_r["bb"], 2)
    if isinstance(traffic.get("ly"), dict) and traffic["ly"].get("bb") is not None:
        traffic["ly"]["bb"] = round(traffic["ly"]["bb"], 2)
    for _a in asins:
        if _a.get("bb_cw") is not None:
            _a["bb_cw"] = round(_a["bb_cw"], 2)

    # Canonicalize numbers at load so neither the embedded inputs nor any value
    # computed from them depends on upstream float precision (fix: residual #4).
    sales, traffic, ads = canon(sales), canon(traffic), canon(ads)
    asins, cust, coupons = canon(asins), canon(cust), canon(coupons)

    # Guard against money rollups that arrived pre-rounded to <=1dp instead of as raw 2dp
    # sums. Like _check_coarse_bb this fails the build closed before any file is written;
    # unlike Buy Box it is an aggregate test (money has no safe single-value band), so a
    # lone legitimate whole-dollar total passes while a systematic pre-round aborts. Groups
    # are passed labeled so the guard can also catch a PARTIAL pre-round (one group rounded
    # while another is raw), not just a blanket one.
    _check_coarse_money(sales=sales, ads=ads, asin=asins, customer=cust)

    # Normalize the default-portfolio sentinel. Live AdvertisingCampaignData returns
    # the unassigned bucket as an empty string "" (not the literal "Unassigned"),
    # which (a) rendered as a blank-named row in the portfolio table and (b) defeated
    # the scale-rec guard below (`name != "Unassigned"`), so the first real portfolio
    # was never evaluated. Collapse blank/None to "Unassigned" once, here, so both the
    # table and the rec see a single, stable label regardless of how a run reshaped it.
    for _p in ads.get("portfolios_cw", []):
        if not (str(_p.get("name") or "").strip()):
            _p["name"] = "Unassigned"

    # Pin the ad-mix and portfolio table order in the builder, giving them the same
    # reshape-order-independence the ASIN table already has. The SQL emits both spend-DESC
    # with a name/type tie-break; re-applying that here means a reshape that reversed or
    # re-sorted these rows (values unchanged) still renders identically. Done AFTER the
    # blank -> "Unassigned" collapse above so the tie-break key is the final, stable label.
    # `spend` is 2dp money (canon runs after, but the key only orders rows) and name/type
    # are strings; missing values coerce to 0 / "" so an empty optional table sorts to []
    # and ties never fault.
    if isinstance(ads.get("campaign_types_cw"), list):
        ads["campaign_types_cw"].sort(key=lambda r: (-(r.get("spend") or 0), r.get("type") or ""))
    if isinstance(ads.get("portfolios_cw"), list):
        ads["portfolios_cw"].sort(key=lambda r: (-(r.get("spend") or 0), r.get("name") or ""))

    # Zero-fill the six NTB keys in every period so the embedded customer block is
    # identical whether a run zero-filled a missing buyer type or omitted it (the
    # live NTB query drops the "Unknown" row in periods with none). This makes the
    # ntb_orders() guard below redundant-but-safe and removes a reshape divergence.
    for _per in (cust.get("ntb") or {}).values():
        if isinstance(_per, dict):
            for _k in ("existing_orders", "existing_rev", "ntb_orders",
                       "ntb_rev", "unknown_orders", "unknown_rev"):
                _per.setdefault(_k, 0)
            # Pin the rollup `orders` key the same way the buyer keys are zero-filled
            # (and the way cw_returns/ytd_returns are pinned for the ASINs): prefer the
            # authoritative period rollup (the Q6 WITH ROLLUP row, count(DISTINCT
            # AmazonOrderId)); else fall back to the buyer-row sum. The key is then ALWAYS
            # present, so a reshape that supplied `orders` and one that omitted it embed a
            # byte-identical ntb block, and ntb_orders() below sees a single stable value
            # rather than recomputing the fallback at read time. The buyer keys are
            # zero-filled just above, so the sum is well-defined even when a buyer-type
            # row was dropped.
            _per["orders"] = _per.get("orders") if _per.get("orders") is not None else (
                _per.get("existing_orders", 0) + _per.get("ntb_orders", 0) + _per.get("unknown_orders", 0))

    # Recompute the ad ratios in the builder from the raw counts the model supplies,
    # instead of trusting a model-rounded roas/acos/ctr. Upstream rounding (the example
    # SQL rounds ctr to 3dp and roas/acos to 2dp) was a run-to-run divergence source;
    # deriving them here from impr/clicks/spend/ad_sales (which are integers and 2dp
    # money, stable across runs) pins them.
    def _recompute_ratios(rows):
        for r in (rows or []):
            sp = r.get("spend") or 0
            asl = r.get("ad_sales") or 0
            impr = r.get("impr") or 0
            clk = r.get("clicks") or 0
            r["roas"] = round(asl / sp, 4) if sp else None
            r["acos"] = round(sp / asl * 100, 4) if asl else None
            if impr or "ctr" in r:
                r["ctr"] = round(clk / impr * 100, 4) if impr else None
    _recompute_ratios(ads.get("campaign_types_cw"))
    _recompute_ratios(ads.get("portfolios_cw"))

    # Recompute the LTV averages from totals/buyers rather than trusting a model-supplied
    # avg() (full float precision upstream, e.g. 102.84062..., which a tidied run would
    # round to 102.84 -> divergence). repeat_rev/onetime_rev are 2dp money and the buyer
    # counts are integers, so deriving the averages here pins them.
    _ltv = cust.get("ltv")
    if isinstance(_ltv, dict):
        if _ltv.get("repeat_buyers"):
            _ltv["repeat_avg_ltv"] = round((_ltv.get("repeat_rev") or 0) / _ltv["repeat_buyers"], 4)
        if _ltv.get("onetime_buyers"):
            _ltv["onetime_avg_ltv"] = round((_ltv.get("onetime_rev") or 0) / _ltv["onetime_buyers"], 4)
        # Zero-fill the six LTV keys the same way the NTB keys are zero-filled above, so a
        # brand with no repeat cohort (e.g. an "One Time"-only LtvRawCustomer) does not leave
        # repeat_avg_ltv/repeat_buyers/repeat_rev unset. template.html reads these unguarded
        # (fmtUsd2(l.repeat_avg_ltv) etc.); an unset key rendered fmtUsd2(undefined) -> threw
        # and blanked the Customer tab, which the structural validate() did not catch.
        for _k in ("repeat_buyers", "repeat_rev", "repeat_avg_ltv",
                   "onetime_buyers", "onetime_rev", "onetime_avg_ltv"):
            _ltv.setdefault(_k, 0)

    # Pin the omit-vs-zero-fill decision for empty comparison periods. The SKILL says
    # to omit an LY/YTD-LY period that has no rows (never zero-fill), but that was prose
    # only: a zero-filling run wrote {"ops":0,...} while an omitting run dropped the key,
    # and the two produced different artifacts. Normalize here -- a period whose ops is
    # zero/missing is treated as absent (dropped) -- so both reshapes converge. The
    # template already skips an absent ly/ytd_ly. Same rule per-ASIN: a zero/missing LY
    # collapses to null so the row's YoY renders identically regardless of reshape.
    _summ = sales.get("summary", {})
    for _k in ("ly", "ytd_ly"):
        _p = _summ.get(_k)
        if isinstance(_p, dict) and not (_p.get("ops") or 0):
            _summ.pop(_k, None)
    for _a in asins:
        if not (_a.get("ly_ops") or 0):
            _a["ly_ops"] = None
            _a["ly_units"] = None
        # cw_returns / ytd_returns are carried in the embedded blob but read by neither
        # this builder nor the template. The Q4 SQL returns ReturnQuantity as null when
        # an ASIN has no returns, and different reshapes wrote null / 0 / omitted-key,
        # which drifted the file bytes (not the render) run-to-run. Drop them here so the
        # representation is fixed regardless of how a run reshaped them. (If a future
        # version surfaces returns, replace these pops with `= _a.get(k) or 0` instead.)
        _a.pop("cw_returns", None)
        _a.pop("ytd_returns", None)

    # Pin the Products-table order in code: descending current-week OPS, with ASIN as a
    # stable lexical tie-break. This mirrors the SQL `ORDER BY cw_ops DESC, ASIN`, but
    # pinning it here makes the embedded list deterministic regardless of the SQL row
    # order, a tie-break gap, or how a run reshaped q_asins.json — matching the
    # determinism-in-code philosophy already used for the portfolio and NTB normalizations
    # above. (Callout selection builds its own sorted copies, so this does not affect it.)
    asins.sort(key=lambda a: (-(a.get("cw_ops") or 0), a.get("asin") or ""))

    s = sales["summary"]

    # Derive last-week start from the current-week start (no hard-coded dates).
    cw_wk = ctx["week_start"]
    lw_wk = (date.fromisoformat(cw_wk) - timedelta(days=7)).isoformat()

    def wk_get(series, wk, key):
        for r in series:
            if r["wk"] == wk:
                return r.get(key)
        return None

    has_traffic = ctx.get("has_traffic", True) and traffic.get("weekly_13w")
    sess_cw = wk_get(traffic["weekly_13w"], cw_wk, "sess") if has_traffic else None
    sess_lw = wk_get(traffic["weekly_13w"], lw_wk, "sess") if has_traffic else None
    sess_ly = traffic.get("ly", {}).get("sess") if has_traffic else None
    bb_cw = wk_get(traffic["weekly_13w"], cw_wk, "bb") if has_traffic else None

    def ntb_orders(p):
        d = cust.get("ntb", {}).get(p)
        if not d:
            return None
        # Prefer the authoritative period total: the Q6 `WITH ROLLUP` row (mapped to
        # "orders") is count(DISTINCT AmazonOrderId) over the whole period, computed
        # independently of the buyer-type split. Using it for the Conversion KPI means a
        # reshape that drops or relabels a buyer-type row cannot move conversion. Fall back
        # to summing the buyer-type rows only when "orders" is absent (older reshape that
        # predates the rollup), defaulting each key to 0 because the live NTB query omits the
        # "Unknown" row in periods with no unknown buyers.
        if d.get("orders") is not None:
            return d["orders"]
        return (d.get("existing_orders", 0) or 0) + (d.get("ntb_orders", 0) or 0) + (d.get("unknown_orders", 0) or 0)

    ord_cw, ord_lw, ord_ly = ntb_orders("cw"), ntb_orders("lw"), ntb_orders("ly")

    ad_cw = ads["weekly_13w"][-1]
    ad_lw = ads["weekly_13w"][-2]
    ad_ly = ads.get("ly", {})
    t4_ad = ads["weekly_13w"][-4:]
    t4_ad_spend = sum(w["spend"] for w in t4_ad) / 4
    t4_ad_sales = sum(w["ad_sales"] for w in t4_ad) / 4

    # ----- KPI cards -----
    def kpi(label, fmt, cw, lw, ly, t4, spark, good_up=True, sub=""):
        return {"label": label, "fmt": fmt, "cw": cw, "lw": lw, "ly": ly,
                "t4": t4, "spark": spark, "good_up": good_up, "sub": sub}

    ops_spark = [r["ops"] for r in sales["weekly_13w"]]
    unit_spark = [r["units"] for r in sales["weekly_13w"]]
    asp_spark = [safe(r["ops"], r["units"]) for r in sales["weekly_13w"]]
    sess_spark = [r["sess"] for r in traffic["weekly_13w"]] if has_traffic else []
    conv_spark = ([safe(u, se) * 100 for u, se in zip(unit_spark, sess_spark)]
                  if has_traffic else [])
    spend_spark = [r["spend"] for r in ads["weekly_13w"]]
    roas_spark = [safe(r["ad_sales"], r["spend"]) for r in ads["weekly_13w"]]
    tacos_spark = [safe(sp, op) * 100 for sp, op in zip(spend_spark, ops_spark)]

    asp_cw = safe(s["cw"]["ops"], s["cw"]["units"])
    asp_lw = safe(s["lw"]["ops"], s["lw"]["units"])
    asp_ly = safe(s["ly"]["ops"], s["ly"]["units"]) if s.get("ly") else 0
    asp_t4 = safe(s["t4"]["ops"], s["t4"]["units"])

    roas_cw = safe(ad_cw["ad_sales"], ad_cw["spend"])
    roas_lw = safe(ad_lw["ad_sales"], ad_lw["spend"])
    roas_ly = safe(ad_ly.get("ad_sales", 0), ad_ly.get("spend", 0))
    roas_t4 = safe(t4_ad_sales, t4_ad_spend)

    tacos_cw = safe(ad_cw["spend"], s["cw"]["ops"]) * 100
    tacos_lw = safe(ad_lw["spend"], s["lw"]["ops"]) * 100
    tacos_ly = safe(ad_ly.get("spend", 0), s.get("ly", {}).get("ops", 0)) * 100
    tacos_t4 = safe(t4_ad_spend, safe(s["t4"]["ops"], 4)) * 100

    kpis = [
        kpi("Ordered Product Sales", "usd", s["cw"]["ops"], s["lw"]["ops"],
            s.get("ly", {}).get("ops", 0), s["t4"]["ops"] / 4, ops_spark),
        kpi("Units Ordered", "int", s["cw"]["units"], s["lw"]["units"],
            s.get("ly", {}).get("units", 0), s["t4"]["units"] / 4, unit_spark),
        kpi("Avg Selling Price", "usd2", asp_cw, asp_lw, asp_ly, asp_t4, asp_spark),
    ]
    if has_traffic:
        # Canonical conversion basis: orders/session when LTV/NTB orders are
        # available, else units/session — which is exactly what conv_spark
        # (above) already uses, so the headline and the sparkline agree.
        ltv_orders = ctx.get("has_ltv", False) and ord_cw is not None
        if ltv_orders:
            conv_cw = safe(ord_cw, sess_cw) * 100
            conv_lw = safe(ord_lw, sess_lw) * 100
            conv_ly = safe(ord_ly, sess_ly) * 100
            conv_sub = "orders / session"
        else:
            conv_cw = safe(s["cw"]["units"], sess_cw) * 100
            conv_lw = safe(s["lw"]["units"], sess_lw) * 100
            conv_ly = safe(s.get("ly", {}).get("units"), sess_ly) * 100
            conv_sub = "units / session"
        kpis += [
            kpi("Sessions", "int", sess_cw, sess_lw, sess_ly,
                sum(sess_spark[-4:]) / 4, sess_spark),
            kpi("Conversion", "pct", conv_cw, conv_lw, conv_ly,
                sum(conv_spark[-4:]) / 4 if conv_spark else 0, conv_spark,
                sub=conv_sub),
        ]
    else:
        conv_cw = 0
    kpis += [
        kpi("Ad Spend", "usd", ad_cw["spend"], ad_lw["spend"],
            ad_ly.get("spend", 0), t4_ad_spend, spend_spark, good_up=False),
        kpi("ROAS", "x", roas_cw, roas_lw, roas_ly, roas_t4, roas_spark),
        kpi("TACoS", "pct", tacos_cw, tacos_lw, tacos_ly, tacos_t4,
            tacos_spark, good_up=False),
    ]

    # ----- callouts -----
    callouts = []
    bb_t = cfg["buy_box"]["low_bb_threshold"]
    low_bb = sorted([a for a in asins if (a.get("bb_cw") or 1) < bb_t],
                    key=lambda a: a["bb_cw"])
    for a in low_bb[:cfg["buy_box"]["max_callouts"]]:
        wow = pct(a["cw_ops"], a["lw_ops"]) if a.get("lw_ops") else 0
        callouts.append({"type": "warn", "title": f"Buy Box loss — {a['name']}",
            "detail": f"Buy Box held only {a['bb_cw']*100:.0f}% this week (ASIN {a['asin']}). "
                      f"Sales {('down' if wow<0 else 'up')} {abs(wow):.1f}% WoW to ${a['cw_ops']:,.0f}."})

    woc_t = cfg["inventory"]["weeks_of_cover_warning"]
    min_ops = cfg["inventory"]["min_weekly_ops_for_oos_flag"]
    oos = []
    for a in asins:
        vel = (a["t4_units"] / 4) if a.get("t4_units") else 0
        woc = (a["oh"] / vel) if vel else 99
        if woc < woc_t and a["cw_ops"] > min_ops:
            oos.append((woc, a))
    oos.sort()
    for woc, a in oos[:cfg["inventory"]["max_callouts"]]:
        callouts.append({"type": "warn", "title": f"Inventory risk — {a['name']}",
            "detail": f"~{woc:.1f} weeks of cover at current velocity ({a['t4_units']/4:,.0f} units/wk). "
                      f"On hand {a['oh']:,}, reserved {a['reserved']:,}, inbound {a['inbound']:,} (ASIN {a['asin']})."})

    z_t = cfg["statistical"]["z_threshold"]
    if len(ops_spark) >= 3:
        mean = statistics.mean(ops_spark[:-1]); sd = statistics.pstdev(ops_spark[:-1])
        cur = ops_spark[-1]; z = (cur - mean) / sd if sd else 0
        if abs(z) >= z_t:
            up = z > 0
            vs_avg = pct(cur, mean)                    # % vs the 12-week average
            strength = "well " if abs(z) >= 2 else ""  # z stays internal; not shown to the reader
            callouts.append({"type": "good" if up else "warn",
                "title": f"Sales {strength}{'above' if up else 'below'} the recent average",
                "detail": f"This week's ${cur:,.0f} is about {abs(vs_avg):.0f}% {'above' if up else 'below'} the "
                          f"12-week average of ${mean:,.0f} — a bigger move than the usual week-to-week swing."})

    asp_yoy = pct(asp_cw, asp_ly) if asp_ly else 0
    if abs(asp_yoy) >= cfg["pricing"]["asp_yoy_callout_pct"]:
        callouts.append({"type": "info", "title": f"ASP {'up' if asp_yoy>0 else 'down'} {abs(asp_yoy):.0f}% YoY",
            "detail": f"Avg selling price ${asp_cw:.2f} vs ${asp_ly:.2f} LY; units {pct(s['cw']['units'], s.get('ly',{}).get('units',0)):+.0f}% YoY "
                      f"but OPS {pct(s['cw']['ops'], s.get('ly',{}).get('ops',0)):+.0f}% — price/mix carrying growth."})

    has_ltv_data = ctx.get("has_ltv", False) and ord_cw
    ntb_share_cw = safe(cust.get("ntb", {}).get("cw", {}).get("ntb_orders", 0), ord_cw) * 100 if has_ltv_data else 0
    roas_warn = cfg["advertising"]["roas_warning_threshold"]
    weak_ads = roas_cw < roas_warn
    callouts.append({
        "type": "warn" if weak_ads else "good",
        "title": (f"Ad efficiency below target — ROAS {roas_cw:.2f}\u00d7" if weak_ads
                  else f"Ad efficiency healthy — ROAS {roas_cw:.2f}\u00d7"),
        "detail": f"ACoS {safe(ad_cw['spend'],ad_cw['ad_sales'])*100:.1f}%, TACoS {tacos_cw:.1f}%."
                  + (f" ROAS is under the {roas_warn:.1f}\u00d7 guardrail — ad spend is outrunning ad sales; review bids and targeting."
                     if weak_ads else "")
                  + (f" NTB share {ntb_share_cw:.0f}% of weekly orders." if has_ltv_data else "")})

    warns = [c for c in callouts if c["type"] == "warn"]
    lead = warns[0] if warns else callouts[0]

    # ----- recommendations -----
    recs = []
    if low_bb:
        b = low_bb[0]
        recs.append({"pri": "High", "title": f"Recover Buy Box on {b['name']}",
            "body": f"Buy Box at {b['bb_cw']*100:.0f}% on ASIN {b['asin']} — check price competitiveness, a competing offer "
                    f"on the listing, or a suppression flag. Lost Buy Box is also lost Sponsored-Product eligibility."})
    if oos:
        woc, a = oos[0]
        recs.append({"pri": "High", "title": f"Protect {a['name']} supply",
            "body": f"~{woc:.1f} weeks of cover with {a['reserved']:,} units reserved. Expedite the {a['inbound']:,} inbound "
                    f"and consider throttling spend if a stockout looks likely before replenishment lands."})
    if asp_ly:
        recs.append({"pri": "Medium", "title": "Validate the price-led growth",
            "body": f"OPS is {pct(s['cw']['ops'], s.get('ly',{}).get('ops',0)):+.0f}% YoY on {pct(s['cw']['units'], s.get('ly',{}).get('units',0)):+.0f}% units — "
                    f"ASP {asp_cw:.2f} ({asp_yoy:+.0f}% YoY). Confirm this is intentional price/mix and test elasticity before further moves."})

    sp = next((c for c in ads.get("campaign_types_cw", []) if "Product" in c["type"]), None)
    sb = next((c for c in ads.get("campaign_types_cw", []) if "Brand" in c["type"]), None)
    if sp and sb and sb["roas"] < sp["roas"]:
        recs.append({"pri": "Medium", "title": "Close the Sponsored Brand efficiency gap",
            "body": f"Sponsored Brand ROAS {sb['roas']:.2f} trails Sponsored Product {sp['roas']:.2f}. Refresh SB creative/targeting "
                    f"or rebalance toward SP until SB recovers."})

    # Conditional ad-lag note — only when context says ads lag sales.
    sft, aft = ctx.get("sales_fresh_through"), ctx.get("ads_fresh_through")
    if sft and aft and aft < sft:
        lag_days = (date.fromisoformat(sft) - date.fromisoformat(aft)).days
        recs.append({"pri": "Medium", "title": f"Advertising data lags sales by ~{lag_days} days",
            "body": f"Ad metrics are complete only through {aft} while sales run through {sft}, so this report is anchored to "
                    f"the week ending {ctx['week_end']} for an apples-to-apples view. Check the ad ingestion pipeline if the lag persists."})

    scale_t = cfg["advertising"]["portfolio_scale_roas"]
    bg = next((p for p in ads.get("portfolios_cw", []) if p["name"] != "Unassigned"), None)
    if bg and bg["roas"] > scale_t:
        recs.append({"pri": "Low", "title": f"Scale the '{bg['name']}' portfolio",
            "body": f"ROAS {bg['roas']:.0f} on just ${bg['spend']:,.0f} spend — clear room to add budget and capture more of that demand."})

    ltv = cust.get("ltv", {})
    if ctx.get("has_ltv", False) and ltv:
        ntb_share_ytd = safe(cust.get("ntb", {}).get("ytd", {}).get("ntb_orders", 0), ntb_orders("ytd") or 0) * 100
        recs.append({"pri": "Low", "title": "Lift repeat rate with post-purchase flows",
            "body": f"Repeat buyers are worth ${ltv.get('repeat_avg_ltv',0):.2f} avg LTV vs ${ltv.get('onetime_avg_ltv',0):.2f} for one-timers, "
                    f"and {ntb_share_ytd:.0f}% of YTD orders are new-to-brand. Acquisition is strong; invest in retention "
                    f"(loyalty offers, brand follow, inserts)."})

    return {
        "ctx": ctx, "kpis": kpis,
        "sales": sales, "traffic": traffic, "ads": ads, "asins": asins,
        "cust": cust, "coupons": coupons,
        "callouts": callouts, "lead": lead, "recs": recs,
        "derived": {
            "ops_yoy": pct(s["cw"]["ops"], s.get("ly", {}).get("ops", 0)),
            "ytd_yoy": pct(s.get("ytd_cy", {}).get("ops", 0), s.get("ytd_ly", {}).get("ops", 0)),
            "conv_cw": conv_cw, "asp_cw": asp_cw,
            "roas_cw": roas_cw, "tacos_cw": tacos_cw,
            "ntb_share_cw": ntb_share_cw,
        },
    }


# ---------------------------------------------------------------- inject + validate
PLACEHOLDER = "const DATA = __DATA__;"


def inject(template_text, data):
    if template_text.count(PLACEHOLDER) != 1:
        sys.exit(f"ERROR: template must contain exactly one `{PLACEHOLDER}` placeholder "
                 f"(found {template_text.count(PLACEHOLDER)}).")
    # sort_keys makes the embedded blob independent of upstream dict insertion
    # order, so two runs with identical values serialize byte-identically.
    return template_text.replace(PLACEHOLDER, f"const DATA = {json.dumps(data, sort_keys=True)};")


def validate(html_text):
    """
    Dependency-free structural self-check. Catches the most common build
    failures *before* the file reaches the user — in particular the
    id-vs-global-variable bug class (markup uses id="m-week" but the script
    references a bare global `m_week`, which is never defined in strict scope
    and throws ReferenceError, blanking the whole dashboard).
    """
    errors = []

    if "__DATA__" in html_text:
        errors.append("placeholder `__DATA__` still present — injection did not run")

    # Embedded data must be valid JSON.
    m = re.search(r"const DATA = (\{.*?\});", html_text, re.DOTALL)
    if not m:
        errors.append("could not locate embedded `const DATA = {...};`")
    else:
        try:
            json.loads(m.group(1))
        except Exception as e:
            errors.append(f"embedded DATA is not valid JSON: {e}")

    # Collect ids declared in markup and ids referenced in script.
    declared = set(re.findall(r"""id=["']([A-Za-z][\w-]*)["']""", html_text))
    getbyid = set(re.findall(r"""getElementById\(\s*["']([\w-]+)["']\s*\)""", html_text))

    # Every getElementById target should exist in the markup.
    missing = sorted(t for t in getbyid if t not in declared)
    if missing:
        errors.append("getElementById targets with no matching element id: " + ", ".join(missing))

    # The classic trap: an id like 'm-week' referenced as a bare global `m_week`.
    # Flag any underscore-form of a hyphenated id used as a standalone identifier
    # that is never assigned (no `var/let/const name` and not via getElementById).
    for did in declared:
        if "-" in did:
            underscored = did.replace("-", "_")
            used_as_global = re.search(rf"(?<![.\w]){re.escape(underscored)}\s*\.", html_text)
            ever_declared = re.search(rf"(?:var|let|const)\b[^;]*\b{re.escape(underscored)}\b", html_text)
            if used_as_global and not ever_declared:
                errors.append(
                    f"element id '{did}' appears to be used as undeclared global "
                    f"`{underscored}` — assign it via getElementById('{did}') first")

    return errors


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--template", required=True)
    ap.add_argument("--defaults", required=True)
    ap.add_argument("--config", default=None)
    ap.add_argument("--no-validate", action="store_true")
    a = ap.parse_args()

    with open(a.defaults) as f:
        cfg = json.load(f)
    if a.config:
        with open(a.config) as f:
            cfg = deep_merge(cfg, json.load(f))

    data = strip_private(canon(build_data(a.workdir, cfg)))

    with open(a.template) as f:
        tpl = f.read()
    html_text = inject(tpl, data)

    if not a.no_validate:
        errs = validate(html_text)
        if errs:
            print("VALIDATION FAILED:", file=sys.stderr)
            for e in errs:
                print("  -", e, file=sys.stderr)
            sys.exit(1)

    with open(a.output, "w") as f:
        f.write(html_text)

    print(f"Wrote {a.output} ({len(html_text):,} bytes)")
    print(f"  callouts: {len(data['callouts'])}  recs: {len(data['recs'])}")
    print(f"  lead: {data['lead']['title']}")
    if not a.no_validate:
        print("  structural validation: PASSED")


if __name__ == "__main__":
    main()
