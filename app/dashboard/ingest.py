"""
Forgiving CSV → metric_points normalization.

Reuses the same forgiving header-mapping approach as the existing account
CSV parser (`app/api/uploads.py` + `app/data_sources/csv.norm_header`): case-,
underscore-, hyphen-, and dot-insensitive matching, common-name aliases,
and a never-crash-on-a-missing-column rule.

Supports both shapes seen in real exports:

  WIDE  date,impressions,clicks,sessions,demo_requests,campaign,utm_source,...
        → one MetricPoint per (row × known-metric column).

  LONG  date,metric,value,segment,campaign,utm_source,...
        → one MetricPoint per row (auto-detected when a 'metric' or
        'metric_name' header and a 'value' header are present).

Unknown metric names are STILL stored (the schema is the generic point —
new tools shouldn't require a migration); they just won't slot into the
canonical funnel buckets until classified. The funnel view treats unknown
metrics as ungrouped extras.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Iterable

from app.data_sources.csv import norm_header


# Canonical funnel stages — used by both the ingest classifier and the
# funnel view. Keep this list small and named the way a marketer reads.
STAGE_TOP = "top"        # Awareness — impressions/clicks/reach/views
STAGE_MIDDLE = "middle"  # Engagement — sessions/visits/downloads/time
STAGE_BOTTOM = "bottom"  # Opportunities — demo_requests/MQLs/SQLs/pipeline


# Metric-name aliases → (canonical_name, stage). Both the canonical name
# and the stage drop into MetricPoint.metric_name and into the funnel
# grouping. A new alias is one line; new STAGE buckets are a code change.
_METRIC_ALIASES: dict[str, tuple[str, str]] = {
    # ---- Top (awareness/visibility) -----------------------------------
    "impressions": ("impressions", STAGE_TOP),
    "imps": ("impressions", STAGE_TOP),
    "ad impressions": ("impressions", STAGE_TOP),
    "reach": ("reach", STAGE_TOP),
    "clicks": ("clicks", STAGE_TOP),
    "ad clicks": ("clicks", STAGE_TOP),
    "ctr": ("ctr", STAGE_TOP),
    "views": ("views", STAGE_TOP),
    "video views": ("views", STAGE_TOP),
    # ---- Middle (engagement) ------------------------------------------
    "sessions": ("sessions", STAGE_MIDDLE),
    "visits": ("sessions", STAGE_MIDDLE),
    "users": ("users", STAGE_MIDDLE),
    "pageviews": ("pageviews", STAGE_MIDDLE),
    "page views": ("pageviews", STAGE_MIDDLE),
    "downloads": ("downloads", STAGE_MIDDLE),
    "time on site": ("time_on_site", STAGE_MIDDLE),
    "avg session duration": ("time_on_site", STAGE_MIDDLE),
    "scroll depth": ("scroll_depth", STAGE_MIDDLE),
    "engagements": ("engagements", STAGE_MIDDLE),
    # ---- Bottom (opportunities/pipeline) ------------------------------
    "demo requests": ("demo_requests", STAGE_BOTTOM),
    "demos": ("demo_requests", STAGE_BOTTOM),
    "mql": ("mqls", STAGE_BOTTOM),
    "mqls": ("mqls", STAGE_BOTTOM),
    "sql": ("sqls", STAGE_BOTTOM),
    "sqls": ("sqls", STAGE_BOTTOM),
    "signups": ("signups", STAGE_BOTTOM),
    "signup": ("signups", STAGE_BOTTOM),
    "conversions": ("conversions", STAGE_BOTTOM),
    "leads": ("leads", STAGE_BOTTOM),
    "pipeline": ("pipeline", STAGE_BOTTOM),
    "pipeline value": ("pipeline", STAGE_BOTTOM),
    "opportunities": ("opportunities", STAGE_BOTTOM),
    "revenue": ("revenue", STAGE_BOTTOM),
}

# Aliases for the non-metric "role" columns (date / segment / campaign /
# utm_*). The metric-CSV ingest reads these the same forgiving way the
# account CSV reads "name" / "employees" / "revenue".
_ROLE_ALIASES: dict[str, str] = {
    # date
    "date": "date", "day": "date", "datetime": "date", "timestamp": "date",
    "week": "date", "month": "date",
    # generic long-format columns
    "metric": "metric_name", "metric name": "metric_name", "event": "metric_name",
    "value": "value", "count": "value", "amount": "value", "total": "value",
    # segment (dimension)
    "segment": "segment", "channel": "segment", "page": "segment",
    "keyword": "segment", "landing page": "segment", "source page": "segment",
    "campaign": "utm_campaign",   # GA exports often call it just "campaign"
    "utm campaign": "utm_campaign",
    "utm source": "utm_source",   "source": "utm_source",
    "utm medium": "utm_medium",   "medium": "utm_medium",
    "utm content": "utm_content", "content": "utm_content",
}


def _classify_metric(header: str) -> tuple[str, str] | None:
    """Map an uploaded column header to (canonical metric_name, stage), or
    None if the header isn't a recognized metric. Returning None is the
    "forgiving" answer — the column is dropped silently rather than
    raising, so a noisy export doesn't break ingest."""
    return _METRIC_ALIASES.get(norm_header(header))


def _classify_role(header: str) -> str | None:
    return _ROLE_ALIASES.get(norm_header(header))


def known_metrics_summary() -> list[dict]:
    """Tiny introspection helper for the UI / debug — what metric names
    map to which stage. Useful for "header didn't map?" error messages."""
    out: list[dict] = []
    seen: set[str] = set()
    for canonical, stage in _METRIC_ALIASES.values():
        if canonical in seen:
            continue
        seen.add(canonical)
        out.append({"metric": canonical, "stage": stage})
    return out


def _coerce_float(v) -> float | None:
    """Best-effort numeric — tolerates "$1,234.56", "12%", and blanks.
    Returns None when nothing useful is in the cell so the caller can
    skip the row rather than persist a zero that pretends to be data."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    s = s.replace(",", "").replace("$", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _coerce_date(v) -> date | None:
    """Accept the formats real tools export: ISO date, ISO datetime,
    M/D/YYYY, YYYY-MM, and a bare YYYY. Returns None on anything
    unparseable — the caller skips the row."""
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    s = str(v).strip()
    if not s:
        return None
    # Try the common ones in order; first that parses wins.
    fmts = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
            "%m/%d/%Y", "%m/%d/%y", "%Y-%m", "%Y/%m/%d", "%Y")
    for fmt in fmts:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_report_csv(raw: str) -> dict:
    """Parse a metric-report CSV. Returns:

       {"points": [ {metric_name, stage, value, date, segment, utm_*, raw_ref}, ...],
        "column_mapping": {original_header: role_or_metric_name},
        "skipped_rows": int,
        "format": "wide" | "long" | "empty"}

    Forgiving rules:
      - Missing headers / a missing date column → returns an empty point
        list and "empty" format, NEVER raises.
      - Unmapped columns are silently dropped (recorded in column_mapping
        as None so the UI can show "we didn't use these").
      - Numeric cells that don't parse (or are blank) cause the (row,col)
        observation to be skipped, never the entire row.
    """
    reader = csv.DictReader(io.StringIO(raw))
    fieldnames = list(reader.fieldnames or [])
    if not fieldnames:
        return {"points": [], "column_mapping": {}, "skipped_rows": 0,
                "format": "empty"}

    # Map each original header to a role OR a metric tuple. Roles take
    # precedence (a "campaign" column is the campaign, not a metric).
    column_mapping: dict[str, dict] = {}
    date_col: str | None = None
    metric_col: str | None = None   # long-format name column
    value_col: str | None = None    # long-format value column
    segment_col: str | None = None
    utm_cols: dict[str, str] = {}   # role → original header
    metric_cols: dict[str, tuple[str, str]] = {}  # original header → (canonical, stage)

    for h in fieldnames:
        role = _classify_role(h)
        if role == "date" and date_col is None:
            date_col = h
            column_mapping[h] = {"role": "date"}
            continue
        if role == "metric_name" and metric_col is None:
            metric_col = h
            column_mapping[h] = {"role": "metric_name"}
            continue
        if role == "value" and value_col is None:
            value_col = h
            column_mapping[h] = {"role": "value"}
            continue
        if role == "segment" and segment_col is None:
            segment_col = h
            column_mapping[h] = {"role": "segment"}
            continue
        if role in ("utm_campaign", "utm_source", "utm_medium", "utm_content"):
            utm_cols.setdefault(role, h)
            column_mapping[h] = {"role": role}
            continue
        classified = _classify_metric(h)
        if classified:
            metric_cols[h] = classified
            column_mapping[h] = {"role": "metric", "metric_name": classified[0],
                                 "stage": classified[1]}
            continue
        column_mapping[h] = {"role": None}   # unmapped, dropped silently

    is_long = metric_col is not None and value_col is not None
    if date_col is None:
        # Forgiving: a CSV with no recognized date column still gets a
        # column-mapping report, but we can't make points without a date.
        return {"points": [], "column_mapping": column_mapping,
                "skipped_rows": 0, "format": "empty"}
    if not is_long and not metric_cols:
        # No metric columns AND not long-format → still forgiving: zero
        # points, mapping returned so the user can see why.
        return {"points": [], "column_mapping": column_mapping,
                "skipped_rows": 0, "format": "empty"}

    points: list[dict] = []
    skipped = 0
    for row_index, raw_row in enumerate(reader, start=1):
        d = _coerce_date(raw_row.get(date_col))
        if d is None:
            skipped += 1
            continue
        segment = (raw_row.get(segment_col) or "").strip() if segment_col else ""
        utm: dict = {}
        for role, src in utm_cols.items():
            val = (raw_row.get(src) or "").strip()
            utm[role] = val or None

        if is_long:
            name_raw = (raw_row.get(metric_col) or "").strip()
            val_raw = raw_row.get(value_col)
            classified = _classify_metric(name_raw)
            if classified:
                canonical, stage = classified
            elif name_raw:
                # Unknown metric — keep it (generic receptacle) but mark
                # stage as None so the funnel view excludes it from
                # canonical buckets.
                canonical, stage = norm_header(name_raw).replace(" ", "_"), None
            else:
                skipped += 1
                continue
            value = _coerce_float(val_raw)
            if value is None:
                skipped += 1
                continue
            points.append({
                "metric_name": canonical, "stage": stage, "value": value,
                "date": d, "segment": segment, **utm,
                "raw_ref": {"row": row_index, "long_name": name_raw},
            })
        else:
            for src, (canonical, stage) in metric_cols.items():
                value = _coerce_float(raw_row.get(src))
                if value is None:
                    continue   # blank cells aren't observations
                points.append({
                    "metric_name": canonical, "stage": stage, "value": value,
                    "date": d, "segment": segment, **utm,
                    "raw_ref": {"row": row_index, "column": src},
                })
    return {
        "points": points,
        "column_mapping": column_mapping,
        "skipped_rows": skipped,
        "format": "long" if is_long else "wide",
    }


def iter_points(parsed: dict) -> Iterable[dict]:
    """Convenience iterator over points in a parse_report_csv() result."""
    return iter(parsed.get("points") or [])
