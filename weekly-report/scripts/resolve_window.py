#!/usr/bin/env python3
"""resolve_window — pin the report's week anchor and ALL date boundaries in code.

Why this exists
---------------
The week-anchor and date-boundary math was deterministic but lived only as prose
in SKILL.md Step 2D, so the model re-derived it from instructions on every run.
That left the last model-judgment step in the pipeline unable to be proved stable
across independent runs: a single off-by-one in T13_START or LY_CW_END silently
shifts the date range a query scans, which the builder's downstream guards cannot
catch because by then the data has already been gathered over the wrong window.

This helper moves that derivation into bundled code. The run computes the two
freshness dates with the exact queries in Step 2D, hands them to this script, and
pastes the emitted values verbatim into context.json and the Q1-Q6 SQL. The math
no longer depends on the model reproducing the prose correctly.

What stays with the model (genuine judgment — NOT codified here)
----------------------------------------------------------------
Which brand, which account when several are plausible, whether to brand the
report, and the narrative framing — those are real decisions. So is *which week*
when the user names one: this helper accepts an explicit --week-end and simply
snaps it to its Saturday, it does not override a deliberately chosen week with the
freshness-based anchor. The helper only owns the arithmetic, never the choices.

Robustness (so it never forces a per-run patch)
-----------------------------------------------
- ads_fresh may be omitted (brands with no ad stream) -> sales alone constrains.
- ads_fresh later than sales_fresh -> min() still picks the true constraint.
- explicit --week-end on any weekday -> snapped back to the enclosing Saturday.
Inputs are validated and bad dates fail loudly rather than producing a plausible
wrong window.

Usage
-----
    python3 scripts/resolve_window.py --sales-fresh 2026-05-29 --ads-fresh 2026-05-12
    python3 scripts/resolve_window.py --sales-fresh 2026-05-29              # no ads
    python3 scripts/resolve_window.py --sales-fresh 2026-05-29 --week-end 2026-04-25

Output: JSON on stdout with three blocks:
    "context"    -> the week_start / week_end / week_number fields for context.json
    "sql_params" -> every {PLACEHOLDER} the Q1-Q6 / 2C queries substitute, ready to paste
    "anchor"     -> which stream constrained the week and whether ads lag sales
"""

import argparse
import json
import sys
from datetime import date, timedelta

SATURDAY = 5  # date.weekday(): Mon=0 .. Sat=5, Sun=6


def _parse(s, field):
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        raise SystemExit(f"resolve_window: --{field} must be an ISO date "
                         f"(YYYY-MM-DD), got {s!r}")


def _prev_saturday(d):
    """The most recent Saturday on or before d. Idempotent when d is a Saturday."""
    return d - timedelta(days=(d.weekday() - SATURDAY) % 7)


def _anchor_note(we, constrained_by, ads_lags, sf, af):
    when = f"{we.isoformat()} (Sat)"
    if constrained_by == "explicit":
        base = f"Week fixed to {when} per the explicitly requested week-ending date"
    else:
        base = f"Week anchored to {when}, constrained by {constrained_by} freshness"
    if ads_lags:
        return (base + f". Ads lag sales by {(sf - af).days} day(s); the report "
                f"surfaces this so every tab stays apples-to-apples.")
    return base + "."


def resolve_window(sales_fresh, ads_fresh=None, week_end=None):
    """Resolve the full set of report date boundaries.

    sales_fresh : ISO str — latest COMPLETE sales date (max(Date) WHERE Date<today()).
    ads_fresh   : ISO str or None — latest COMPLETE ads date; None if no ad stream.
    week_end    : ISO str or None — explicit Saturday the user asked for; overrides
                  the freshness-based anchor (still snapped to its Saturday).

    Anchor rule (SKILL.md Step 2D): the week ends on the most recent Saturday for
    which ALL core streams are complete — min(sales_fresh, ads_fresh) rounded back
    to its Saturday. With no ads stream, sales alone constrains it.
    """
    sf = _parse(sales_fresh, "sales-fresh")
    af = _parse(ads_fresh, "ads-fresh") if ads_fresh else None

    if week_end is not None:
        constraint = _parse(week_end, "week-end")
        constrained_by = "explicit"
    elif af is not None:
        constraint = min(sf, af)
        constrained_by = "ads" if af <= sf else "sales"
    else:
        constraint = sf
        constrained_by = "sales"

    we = _prev_saturday(constraint)          # week_end (Saturday)
    cw_start = we - timedelta(days=6)        # week_start (the Sunday)
    lw_end = cw_start - timedelta(days=1)
    lw_start = lw_end - timedelta(days=6)
    t4_start = we - timedelta(days=27)
    t13_start = we - timedelta(days=90)
    ly_cw_end = we - timedelta(days=364)     # same weekday one year prior (NOT 365)
    ly_cw_start = ly_cw_end - timedelta(days=6)
    ytd_start = date(we.year, 1, 1)
    ly_ytd_start = date(we.year - 1, 1, 1)

    iso = lambda d: d.isoformat()
    ads_lags = af is not None and af < sf

    return {
        "context": {
            "week_start": iso(cw_start),
            "week_end": iso(we),
            "week_number": we.isocalendar()[1],
            "sales_fresh_through": iso(sf),
            "ads_fresh_through": iso(af) if af is not None else None,
        },
        # Keys are the exact {PLACEHOLDER} tokens the Step 2C/3 queries substitute,
        # so the run pastes them in mechanically with no further arithmetic.
        "sql_params": {
            "CW_START": iso(cw_start),
            "CW_END": iso(we),
            "CW_END_PLUS1": iso(we + timedelta(days=1)),
            "LW_START": iso(lw_start),
            "LW_END": iso(lw_end),
            "T4_START": iso(t4_start),
            "T13_START": iso(t13_start),
            "LY_CW_START": iso(ly_cw_start),
            "LY_CW_END": iso(ly_cw_end),
            "LY_CW_END_PLUS1": iso(ly_cw_end + timedelta(days=1)),
            "YTD_START": iso(ytd_start),
            "LY_YTD_START": iso(ly_ytd_start),
            # aliases used by the 2C table-availability probe
            "WK_END": iso(we),
            "WK13_START": iso(t13_start),
        },
        "anchor": {
            "constrained_by": constrained_by,
            "ads_lags_sales": ads_lags,
            "note": _anchor_note(we, constrained_by, ads_lags, sf, af),
        },
    }


def main():
    ap = argparse.ArgumentParser(
        description="Resolve the weekly-report week anchor and date boundaries.")
    ap.add_argument("--sales-fresh", required=True,
                    help="Latest COMPLETE sales date, ISO (max(Date) WHERE Date<today()).")
    ap.add_argument("--ads-fresh", default=None,
                    help="Latest COMPLETE ads date, ISO. Omit for brands with no ads.")
    ap.add_argument("--week-end", default=None,
                    help="Explicit Saturday the user asked for; overrides the "
                         "freshness anchor (snapped to its Saturday).")
    a = ap.parse_args()
    out = resolve_window(a.sales_fresh, a.ads_fresh, a.week_end)
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
