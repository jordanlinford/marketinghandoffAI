"""
build_report_intelligence — the SINGLE structured intelligence object
every report renderer draws from. Pure Python deterministic aggregation
over existing tables (metric_points, artifacts, campaigns, runs) via
scoped() + the shared `query_memory` service.

The discipline that keeps reports honest:

  * Baseline-vs-attributed: untagged metric_points stay in `funnel.backdrop`
    and are NEVER claimed in the attributed numbers. Same rule the
    dashboard and memory layers already enforce.
  * Memory propagation: insufficient/low patterns land in `watching`
    (framed honestly); only high/moderate land in `memory_highlights`.
  * Past-tense only: we describe what HAS happened. No predictions.
  * Honesty notes: when the data is thin or has gaps (no prior period,
    untagged volume substantial, no campaigns linked, etc.), say so
    explicitly so the renderer can surface the caveat.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.memory import query_memory
from app.models import Artifact, Campaign, MetricPoint, Run
from app.tenancy import scoped


# Same metric-name vocabulary memory uses — kept here as a small local
# duplicate so the engine doesn't reach into memory's private constants.
_NUM_TOKENS = ("conversion", "lead", "signup", "demo", "trial",
               "subscribe", "purchase", "request")
_DEN_TOKENS = ("click", "session", "tap", "visit")


def _kind_of(metric_name: str) -> str:
    n = (metric_name or "").lower()
    for t in _NUM_TOKENS:
        if t in n:
            return "numerator"
    for t in _DEN_TOKENS:
        if t in n:
            return "denominator"
    return "other"


def _utcnow_date() -> date:
    return datetime.now(timezone.utc).date()


def _to_dt(d: date, end_of_day: bool = False) -> datetime:
    # Naive (no tzinfo) — SQLite stores Run.created_at + Artifact.created_at
    # as naive datetimes, so comparisons must match shape. UTC is the
    # only zone the worker uses internally; treating naive as UTC here is
    # safe for in-DB comparisons.
    if end_of_day:
        return datetime.combine(d, datetime.max.time())
    return datetime.combine(d, datetime.min.time())


def _parse_date(s) -> date | None:
    if isinstance(s, date):
        return s
    if not s:
        return None
    try:
        return date.fromisoformat(str(s))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------
def _resolve_scope(db: Session, org_id: str, scope: dict,
                   lookback_days: int) -> dict:
    """Turn the caller's scope spec into a fully-resolved scope dict:
    {kind, start, end, product_id (passed through), campaign_id, campaign_name}.
    Defaults a missing time_window to the most-recent `lookback_days`."""
    kind = (scope or {}).get("kind") or "time_window"
    today = _utcnow_date()
    if kind == "campaign":
        camp_id = (scope or {}).get("campaign_id")
        if not camp_id:
            raise ValueError("scope.kind='campaign' requires campaign_id")
        camp = db.execute(
            scoped(Campaign, org_id).where(Campaign.id == camp_id)
        ).scalar_one_or_none()
        if camp is None:
            raise ValueError(f"campaign {camp_id!r} not found in this org")
        # Campaign window = creation through today (campaigns are usually
        # active or recently-completed when a report is requested).
        start = (camp.created_at.date() if camp.created_at else today)
        end = today
        return {
            "kind": "campaign",
            "start": start, "end": end,
            "campaign_id": camp.id,
            "campaign_name": camp.name,
            "campaign_utm": camp.utm_campaign,
        }
    # time_window
    start = _parse_date((scope or {}).get("start"))
    end = _parse_date((scope or {}).get("end"))
    if end is None:
        end = today
    if start is None:
        start = end - timedelta(days=max(1, lookback_days) - 1)
    if start > end:
        start, end = end, start
    return {
        "kind": "time_window",
        "start": start, "end": end,
        "campaign_id": None,
    }


# ---------------------------------------------------------------------------
# Funnel aggregation
# ---------------------------------------------------------------------------
def _aggregate_funnel(points: list[MetricPoint]) -> dict:
    """Split into attributed (tagged + non-baseline) vs backdrop. Untagged
    rows and is_baseline=True rows land in backdrop ONLY. Same rule
    memory enforces — surfaced here so the report can show 'X clicks in
    backdrop' without ever claiming them as marketing-driven."""
    bucket = {
        "attributed": {"clicks": 0.0, "conversions": 0.0, "other": 0.0,
                       "data_points": 0},
        "backdrop":   {"clicks": 0.0, "conversions": 0.0, "other": 0.0,
                       "data_points": 0},
    }
    by_channel = defaultdict(lambda: {"clicks": 0.0, "conversions": 0.0,
                                       "data_points": 0})
    for mp in points:
        is_attributed = (not mp.is_baseline
                         and bool((mp.utm_campaign or "").strip()))
        lane = "attributed" if is_attributed else "backdrop"
        kind = _kind_of(mp.metric_name)
        v = float(mp.value or 0.0)
        if kind == "numerator":
            bucket[lane]["conversions"] += v
        elif kind == "denominator":
            bucket[lane]["clicks"] += v
        else:
            bucket[lane]["other"] += v
        bucket[lane]["data_points"] += 1
        if is_attributed and mp.utm_source:
            ch = mp.utm_source.lower()
            if kind == "numerator":
                by_channel[ch]["conversions"] += v
            elif kind == "denominator":
                by_channel[ch]["clicks"] += v
            by_channel[ch]["data_points"] += 1

    def _rate(b):
        return (round(b["conversions"] / b["clicks"], 4)
                if b["clicks"] else None)
    bucket["attributed"]["conversion_rate"] = _rate(bucket["attributed"])
    bucket["backdrop"]["conversion_rate"] = _rate(bucket["backdrop"])
    by_channel_out = {}
    for ch, b in by_channel.items():
        by_channel_out[ch] = {
            "clicks": round(b["clicks"], 2),
            "conversions": round(b["conversions"], 2),
            "data_points": b["data_points"],
            "conversion_rate": _rate(b),
        }
    return {**bucket, "by_channel": by_channel_out}


def _delta(curr: float, prev: float) -> dict | None:
    if prev == 0 and curr == 0:
        return None
    if prev == 0:
        return {"abs": round(curr - prev, 4), "pct": None,
                "direction": "up" if curr > 0 else "flat"}
    pct = (curr - prev) / prev
    return {
        "abs": round(curr - prev, 4),
        "pct": round(pct, 4),
        "direction": ("up" if pct > 0.001 else
                      "down" if pct < -0.001 else "flat"),
    }


def _compute_deltas(curr_attr: dict, prev_attr: dict) -> dict:
    return {
        "clicks": _delta(curr_attr.get("clicks", 0), prev_attr.get("clicks", 0)),
        "conversions": _delta(curr_attr.get("conversions", 0),
                              prev_attr.get("conversions", 0)),
        "conversion_rate": _delta(curr_attr.get("conversion_rate") or 0,
                                   prev_attr.get("conversion_rate") or 0),
    }


# ---------------------------------------------------------------------------
# Notable changes — deterministic rules over the aggregated numbers
# ---------------------------------------------------------------------------
def _compute_notable_changes(period_summary: dict,
                             deltas: dict | None,
                             campaigns: list[dict],
                             memory_highlights: list[dict]) -> list[dict]:
    """Plain-language past-tense observations the renderers can pull from."""
    out: list[dict] = []
    attr = period_summary["attributed"]
    # Stage-level deltas (when we have a prior period to compare to)
    if deltas:
        for stage in ("clicks", "conversions"):
            d = deltas.get(stage)
            if not d or d.get("pct") is None:
                continue
            pct = d["pct"]
            if abs(pct) >= 0.10:  # >10% move is "notable"
                out.append({
                    "kind": f"{stage}_{d['direction']}",
                    "stage": stage,
                    "delta_pct": pct,
                    "observation": (
                        f"Attributed {stage} {'up' if d['direction'] == 'up' else 'down'} "
                        f"{abs(pct) * 100:.0f}% vs the prior period "
                        f"({d['abs']:+g} {stage})."),
                })
        cr = deltas.get("conversion_rate")
        if cr and cr.get("pct") is not None and abs(cr["pct"]) >= 0.15:
            out.append({
                "kind": f"conversion_rate_{cr['direction']}",
                "stage": "conversion_rate",
                "delta_pct": cr["pct"],
                "observation": (
                    f"Conversion rate {'up' if cr['direction'] == 'up' else 'down'} "
                    f"{abs(cr['pct']) * 100:.0f}% vs the prior period."),
            })
    # Campaign-level: any campaign with attributed conversions > 0.
    for c in campaigns:
        attributed = c.get("attributed") or {}
        if (attributed.get("conversions") or 0) > 0:
            out.append({
                "kind": "campaign_attributed_conversions",
                "campaign_id": c["id"],
                "campaign_name": c["name"],
                "observation": (
                    f"Campaign {c['name']!r} drove "
                    f"{int(attributed['conversions'])} attributed "
                    f"conversion{'s' if attributed['conversions'] != 1 else ''} "
                    f"in this period."),
            })
    # Memory-derived: top moderate/high pattern surfaced as a "notable"
    # observation when the data tier earned it.
    if memory_highlights:
        top = memory_highlights[0]
        out.append({
            "kind": "memory_top_highlight",
            "dimension": top.get("dimension"),
            "key": top.get("key"),
            "observation": top.get("observation", ""),
            "confidence": top.get("confidence"),
        })
    return out


# ---------------------------------------------------------------------------
# Open questions + honesty notes — surfaced honestly, never invented
# ---------------------------------------------------------------------------
def _compute_open_questions(production: dict, campaigns: list[dict],
                            top_content: list[dict],
                            memory_highlights: list[dict]) -> list[str]:
    """Rule-based: surface things the data can't answer. Reports never
    fabricate findings, but they can ASK questions the human can chase."""
    qs: list[str] = []
    if not top_content:
        qs.append("Which produced asset is actually driving the pipeline? "
                  "We don't have enough attributed clicks/conversions on "
                  "individual pieces yet to single one out.")
    elif len(top_content) == 1:
        qs.append(f"Only one piece has attributed conversion data so "
                  f"far ({top_content[0]['title']!r}). Is the rest of the "
                  f"production reaching the right audience?")
    if not campaigns:
        qs.append("No campaigns were active in this scope. Should the "
                  "next period's production be organized into a campaign "
                  "so attribution can compound?")
    if production.get("artifacts_pending_review", 0) > 0:
        qs.append(f"There are {production['artifacts_pending_review']} "
                  f"draft(s) sitting in the approval queue. Reviewing "
                  f"them is the cheapest way to convert produced work "
                  f"into shipped work.")
    if not memory_highlights:
        qs.append("Memory has not yet identified a clearly-performing "
                  "channel or content_type for this org. Are reports "
                  "being uploaded with the UTM columns that would let "
                  "memory learn?")
    return qs


def _compute_honesty_notes(points: list[MetricPoint],
                           prior_points: list[MetricPoint],
                           deltas: dict | None,
                           period_summary: dict,
                           campaigns_data: list[dict]) -> list[str]:
    notes: list[str] = []
    backdrop = period_summary["backdrop"]
    attr = period_summary["attributed"]
    if backdrop["data_points"] > 0:
        # Quote the magnitude so the renderer can decide whether to
        # surface it; the discipline is "never claim it as attribution."
        notes.append(
            f"Untagged volume excluded from attributed numbers: "
            f"{int(backdrop['clicks']):g} click(s), "
            f"{int(backdrop['conversions']):g} conversion(s) across "
            f"{backdrop['data_points']} backdrop data point(s). These are "
            f"funnel context only — never quoted in this report as "
            f"something marketing drove.")
    if prior_points == [] or deltas is None:
        notes.append("No prior-period data available — deltas were not "
                     "computed. The first period of measurement always "
                     "looks like 'all new'; that's not a finding, it's a "
                     "baseline.")
    if not points:
        notes.append("No metric_points in scope. The report is structurally "
                     "thin and any narrative will reflect that honestly.")
    if attr["data_points"] == 0 and backdrop["data_points"] > 0:
        notes.append("All metric_points in scope were untagged or baseline. "
                     "Nothing in this period can honestly be attributed to "
                     "marketing-produced work.")
    return notes


# ---------------------------------------------------------------------------
# Top content — rank artifacts by attributed conversions via utm_content
# ---------------------------------------------------------------------------
def _compute_top_content(points: list[MetricPoint],
                         artifacts: list[Artifact]) -> list[dict]:
    art_by_utm = {a.utm_content: a for a in artifacts if a.utm_content}
    if not art_by_utm:
        return []
    convs: dict[str, float] = defaultdict(float)
    clicks: dict[str, float] = defaultdict(float)
    for mp in points:
        if mp.is_baseline or not (mp.utm_campaign or "").strip():
            continue
        if not (mp.utm_content or "").strip():
            continue
        kind = _kind_of(mp.metric_name)
        v = float(mp.value or 0.0)
        if kind == "numerator":
            convs[mp.utm_content] += v
        elif kind == "denominator":
            clicks[mp.utm_content] += v
    ranked = []
    for uc, a in art_by_utm.items():
        c = convs.get(uc, 0.0)
        k = clicks.get(uc, 0.0)
        if c <= 0:
            continue
        ranked.append((c, k, uc, a))
    ranked.sort(reverse=True)
    out = []
    for c, k, uc, a in ranked[:5]:
        body = a.body or {}
        content = body.get("content") or {}
        out.append({
            "id": a.id, "title": a.title,
            "content_type": content.get("content_type") or "content",
            "utm_campaign": a.utm_campaign, "utm_content": uc,
            "attributed": {
                "clicks": round(k, 2), "conversions": round(c, 2),
                "conversion_rate": round(c / k, 4) if k else None,
            },
        })
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def build_report_intelligence(db: Session, org_id: str, *,
                              product_id: str | None = None,
                              scope: dict | None = None,
                              lookback_days: int = 30) -> dict:
    """Compute the single ReportIntelligence object the renderers consume.

    Empty / thin scope is honest: a populated honesty_notes array tells
    the renderer to keep the narrative short and truthful. Never
    confabulate findings the data doesn't support.
    """
    scope_resolved = _resolve_scope(db, org_id, scope or {}, lookback_days)
    today = _utcnow_date()

    # ---- Future-date honesty guard ------------------------------------
    # When the requested time_window is entirely or partially in the
    # future, the engine CANNOT honestly produce a period_summary,
    # notable_changes, production accounting, or top_content. Returning
    # a short-circuit intelligence object lets the renderers produce a
    # truthful "no data exists yet" stub with current memory as a
    # reference baseline (clearly labeled as-of-today, NOT a finding
    # about the requested period). This is the same anti-overclaim
    # discipline memory uses for thin data — when the system can't say
    # something true, it says what it can't say.
    if scope_resolved["kind"] == "time_window":
        if scope_resolved["start"] > today or scope_resolved["end"] > today:
            patterns = query_memory(db, org_id, product_id=product_id,
                                    lookback_days=180)
            reference_highlights = [
                {"observation": p.get("observation", ""),
                 "metric_basis": p.get("metric_basis") or {},
                 "confidence": p.get("confidence"),
                 "dimension": p.get("dimension"),
                 "key": p.get("key"),
                 "key_display": p.get("key_display") or p.get("key", ""),
                 "sample_size": p.get("sample_size")}
                for p in patterns
                if p.get("confidence") in ("moderate", "high")
            ][:5]
            return {
                "scope": {
                    "kind": "time_window",
                    "start": scope_resolved["start"].isoformat(),
                    "end": scope_resolved["end"].isoformat(),
                    "product_id": product_id,
                    "campaign_id": None,
                    "campaign_name": None,
                    "lookback_days": lookback_days,
                    "is_future": True,
                    "today": today.isoformat(),
                },
                # Period sections OMITTED — reports describe what HAS
                # happened; they don't forecast. The renderer must NOT
                # synthesize a period_summary, deltas, or "what changed"
                # narrative from data that doesn't exist.
                "period_summary": None,
                "notable_changes": [],
                "production": None,
                "top_content": [],
                "campaigns": [],
                "watching": [],
                # Memory survives as the REFERENCE baseline — explicitly
                # labeled. Renderers MUST frame these as "as of today,
                # not for the requested future period."
                "memory_highlights": reference_highlights,
                "memory_reference_label": (
                    f"Memory snapshot as of {today.isoformat()} — NOT a "
                    f"finding about the requested future period."),
                "open_questions": [
                    "What will marketing produce during the requested "
                    "future period? That cannot be answered from data "
                    "that does not yet exist.",
                ],
                "honesty_notes": [
                    f"No data exists for the requested period; it is in "
                    f"the future ({scope_resolved['start'].isoformat()} to "
                    f"{scope_resolved['end'].isoformat()}). Today is "
                    f"{today.isoformat()}.",
                    "Memory highlights are a reference baseline as of "
                    "today, NOT a finding about the requested period.",
                    "Re-generate this report once data exists for the "
                    "requested period.",
                ],
            }

    start = scope_resolved["start"]
    end = scope_resolved["end"]
    camp_utm_filter = scope_resolved.get("campaign_utm")

    # Metric_points in scope.
    mp_q = scoped(MetricPoint, org_id).where(
        MetricPoint.date >= start, MetricPoint.date <= end)
    if product_id:
        mp_q = mp_q.where(MetricPoint.product_id == product_id)
    if camp_utm_filter:
        mp_q = mp_q.where(MetricPoint.utm_campaign == camp_utm_filter)
    points = list(db.execute(mp_q).scalars().all())
    period_summary = _aggregate_funnel(points)

    # Prior-period funnel for deltas (when no campaign filter — the
    # "prior period" of a single campaign doesn't have a clean meaning).
    deltas: dict | None = None
    prior_points: list[MetricPoint] = []
    if camp_utm_filter is None:
        period_length = (end - start).days + 1
        prior_end = start - timedelta(days=1)
        prior_start = prior_end - timedelta(days=period_length - 1)
        prior_q = scoped(MetricPoint, org_id).where(
            MetricPoint.date >= prior_start,
            MetricPoint.date <= prior_end)
        if product_id:
            prior_q = prior_q.where(MetricPoint.product_id == product_id)
        prior_points = list(db.execute(prior_q).scalars().all())
        if prior_points:
            prior_attr = _aggregate_funnel(prior_points)["attributed"]
            deltas = _compute_deltas(period_summary["attributed"], prior_attr)

    # Memory patterns — high/moderate go to highlights; low/insufficient
    # go to "watching" framed honestly. Same discipline as everywhere
    # else in the codebase.
    patterns = query_memory(db, org_id, product_id=product_id,
                            lookback_days=180)
    memory_highlights = [
        {"observation": p.get("observation", ""),
         "metric_basis": p.get("metric_basis") or {},
         "confidence": p.get("confidence"),
         "dimension": p.get("dimension"),
         "key": p.get("key"),
         "key_display": p.get("key_display") or p.get("key", ""),
         "sample_size": p.get("sample_size"),
         "recency": p.get("recency"),
         "confidence_reason": p.get("confidence_reason")}
        for p in patterns if p.get("confidence") in ("moderate", "high")
    ][:5]
    watching = [
        {"observation": p.get("observation", ""),
         "metric_basis": p.get("metric_basis") or {},
         "confidence": p.get("confidence"),
         "dimension": p.get("dimension"),
         "key": p.get("key"),
         "key_display": p.get("key_display") or p.get("key", ""),
         "sample_size": p.get("sample_size")}
        for p in patterns if p.get("confidence") in ("low", "insufficient")
    ][:5]

    # Production lane: runs + artifacts created in the time window.
    start_dt, end_dt = _to_dt(start), _to_dt(end, end_of_day=True)
    run_q = scoped(Run, org_id).where(
        Run.created_at >= start_dt, Run.created_at <= end_dt)
    if product_id:
        run_q = run_q.where(Run.product_id == product_id)
    runs = list(db.execute(run_q).scalars().all())
    art_q = scoped(Artifact, org_id).where(
        Artifact.type == "content_draft",
        Artifact.created_at >= start_dt, Artifact.created_at <= end_dt)
    if product_id:
        art_q = art_q.where(Artifact.product_id == product_id)
    artifacts = list(db.execute(art_q).scalars().all())
    if camp_utm_filter:
        artifacts = [a for a in artifacts if a.utm_campaign == camp_utm_filter]

    by_type: dict[str, int] = defaultdict(int)
    for a in artifacts:
        body = a.body or {}
        ct = (body.get("content") or {}).get("content_type") or a.type
        by_type[ct] += 1
    production = {
        "runs_total": len(runs),
        "runs_succeeded": sum(1 for r in runs if r.status == "succeeded"),
        "runs_failed": sum(1 for r in runs if r.status == "failed"),
        "runs_pending": sum(1 for r in runs
                            if r.status in ("queued", "running")),
        "artifacts_total": len(artifacts),
        "artifacts_ready": sum(1 for a in artifacts if a.status == "ready"),
        "artifacts_pending_review": sum(
            1 for a in artifacts if a.status == "pending_review"),
        "cost_usd_total": round(
            sum(float(r.cost_usd or 0) for r in runs), 4),
        "by_content_type": dict(by_type),
    }

    # Campaigns in scope: explicit one if scope.kind=campaign; otherwise
    # any campaign whose UTM matches in-scope metric_points OR was
    # created in the scope window. Reports that drive toward a campaign
    # readout need the campaign even when no metric_points connect yet.
    campaigns_data: list[dict] = []
    if camp_utm_filter:
        camp = db.execute(
            scoped(Campaign, org_id).where(Campaign.id == scope_resolved["campaign_id"])
        ).scalar_one()
        camps = [camp]
    else:
        utms_in_scope = {mp.utm_campaign for mp in points if mp.utm_campaign}
        all_camps_q = scoped(Campaign, org_id)
        if product_id:
            all_camps_q = all_camps_q.where(Campaign.product_id == product_id)
        all_camps = list(db.execute(all_camps_q).scalars().all())
        camps = []
        for c in all_camps:
            in_window = (c.created_at and start_dt <= c.created_at <= end_dt)
            matches_metric = c.utm_campaign in utms_in_scope
            if in_window or matches_metric:
                camps.append(c)
    for c in camps:
        camp_points = [mp for mp in points
                       if mp.utm_campaign == c.utm_campaign
                       and not mp.is_baseline]
        camp_funnel = _aggregate_funnel(camp_points)["attributed"]
        campaigns_data.append({
            "id": c.id, "name": c.name, "status": c.status,
            "campaign_type": c.campaign_type,
            "plan_items": len(((c.plan or {}).get("derivative_assets")) or []),
            "generated_assets": len(c.generated_asset_ids or []),
            "utm_campaign": c.utm_campaign,
            "attributed": {
                "clicks": round(camp_funnel.get("clicks", 0), 2),
                "conversions": round(camp_funnel.get("conversions", 0), 2),
                "conversion_rate": camp_funnel.get("conversion_rate"),
                "data_points": camp_funnel.get("data_points", 0),
            },
        })
    # Order campaigns by attributed conversions desc.
    campaigns_data.sort(
        key=lambda c: -(c["attributed"].get("conversions") or 0))

    top_content = _compute_top_content(points, artifacts)

    notable_changes = _compute_notable_changes(
        period_summary, deltas, campaigns_data, memory_highlights)
    open_questions = _compute_open_questions(
        production, campaigns_data, top_content, memory_highlights)
    honesty_notes = _compute_honesty_notes(
        points, prior_points, deltas, period_summary, campaigns_data)

    return {
        "scope": {
            "kind": scope_resolved["kind"],
            "start": scope_resolved["start"].isoformat(),
            "end": scope_resolved["end"].isoformat(),
            "product_id": product_id,
            "campaign_id": scope_resolved.get("campaign_id"),
            "campaign_name": scope_resolved.get("campaign_name"),
            "lookback_days": lookback_days,
        },
        "period_summary": {
            "attributed": period_summary["attributed"],
            "backdrop": period_summary["backdrop"],
            "by_channel": period_summary["by_channel"],
            "deltas": deltas,
            "has_prior_period": bool(prior_points),
        },
        "notable_changes": notable_changes,
        "memory_highlights": memory_highlights,
        "watching": watching,
        "production": production,
        "campaigns": campaigns_data,
        "top_content": top_content,
        "open_questions": open_questions,
        "honesty_notes": honesty_notes,
    }
