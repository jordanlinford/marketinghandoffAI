"""
Funnel aggregation + UTM attribution + production lane.

This is the READ side of the dashboard. The schema (metric_points) is a
generic time-series receptacle; this module computes the curated VIEW a
marketer reads — three stages over time, baseline backdrop vs attributed
signal, production lane on the same timeline.

Honesty rules baked in:
  * Baseline rows (is_baseline=True) flow into the trend BACKDROP only.
    They are NEVER claimed as something the system drove.
  * Attribution is reported ONLY where the UTM/campaign on a metric_point
    matches an artifact the system produced. The same campaign+source
    +medium+content tuple appearing on both sides is the join.
  * Unknown metric names are passed through into MetricPoint.metric_name
    but are excluded from the canonical funnel stages (stage was None at
    ingest time).

All reads tenant-scoped via scoped(). Pure compute over rows + dicts —
no agent contract here; the dashboard is a query layer, not an agent.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.dashboard.ingest import STAGE_BOTTOM, STAGE_MIDDLE, STAGE_TOP
from app.models import Artifact, MetricPoint, Run
from app.tenancy import scoped


_STAGES = (STAGE_TOP, STAGE_MIDDLE, STAGE_BOTTOM)
_STAGE_LABEL = {
    STAGE_TOP: "Leads created",
    STAGE_MIDDLE: "Engagement",
    STAGE_BOTTOM: "Opportunities",
}


def _stage_of(metric_name: str) -> str | None:
    """Look up a metric's stage by checking the canonical name. We don't
    persist `stage` on metric_points (it can be recomputed when the alias
    map changes) — instead we re-classify at read time."""
    from app.dashboard.ingest import _METRIC_ALIASES   # local: avoid cycles
    classified = _METRIC_ALIASES.get(metric_name.replace("_", " "))
    if classified:
        return classified[1]
    # Direct hit on the canonical name (alias map keyed on normalized
    # spaced form; some canonicals are themselves single tokens).
    for canonical, stage in _METRIC_ALIASES.values():
        if canonical == metric_name:
            return stage
    return None


def _bucket(d: date, bucket: str) -> str:
    """ISO key for the chosen aggregation bucket. We use a string key so
    the result is JSON-cleanly serializable for the API + UI."""
    if bucket == "day":
        return d.isoformat()
    if bucket == "month":
        return f"{d.year:04d}-{d.month:02d}"
    # Default: week — Monday-anchored ISO week start.
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


def _matching_artifact(point: MetricPoint,
                       artifacts_by_campaign: dict) -> Artifact | None:
    """Attribution join. `utm_campaign` is the PRIMARY join key — it's the
    dim every report tends to carry, and a campaign can legitimately span
    multiple channels (the same email asset gets republished on LinkedIn,
    say, so source on the report and on the artifact need not agree).

    For tiebreaking among multiple artifacts in the same campaign — e.g.
    v1 and v2 of "Give me something better" — we score on how many of the
    secondary dims (source / medium / content) AGREE. Disagreements don't
    disqualify; they just don't add to the score. Most recent wins ties.

    Returns the best matching artifact, or None when there's no campaign
    or no campaign-mate among produced content."""
    if not point.utm_campaign:
        return None
    candidates = artifacts_by_campaign.get(point.utm_campaign) or []
    if not candidates:
        return None
    best_score = -1
    best_art: Artifact | None = None
    for art in candidates:
        score = 0
        for p_val, a_val in ((point.utm_source, art.utm_source),
                             (point.utm_medium, art.utm_medium),
                             (point.utm_content, art.utm_content)):
            if p_val and a_val and p_val == a_val:
                score += 1
        # Equal scores → prefer the more recently produced artifact so the
        # latest version of a piece collects newer performance.
        if score > best_score or (score == best_score and best_art is not None
                                  and art.created_at > best_art.created_at):
            best_score = score
            best_art = art
    return best_art


def funnel_view(db: Session, org_id: str, *, bucket: str = "week",
                limit_buckets: int = 12,
                product_id: str | None = None) -> dict:
    """Compute the dashboard's main view for an org.

    `product_id` filters every series (metric_points + artifacts + runs)
    to that product. None (the historical default) returns the full
    org-level view. The filter is applied IN-SQL via scoped(): we never
    pull org-level rows and then filter in Python.

    Returns:
      {
        "buckets": [iso_key, ...],            # ordered, latest last
        "stages": {
          "top": {
            "label": "Leads created",
            "baseline": [n_per_bucket],       # backdrop trend
            "attributed": [n_per_bucket],     # tagged signal
            "total": [n_per_bucket],
            "metric_names": [str, ...],
            "top_contributors": [{...}, ...]  # artifacts that drove this stage
          }, ...
        },
        "production": {
          "buckets": [iso_key, ...],
          "drafts_produced": [n_per_bucket],
          "drafts_ready":    [n_per_bucket],
          "drafts_pending":  [n_per_bucket],
          "runs_total":      [n_per_bucket],
          "cost_usd":        [usd_per_bucket]
        },
        "ungrouped_metrics": [str, ...]   # metrics seen but not in any stage
      }
    """
    points_q = scoped(MetricPoint, org_id).order_by(MetricPoint.date.asc())
    artifacts_q = scoped(Artifact, org_id).where(Artifact.type == "content_draft")
    runs_q = scoped(Run, org_id)
    if product_id:
        points_q = points_q.where(MetricPoint.product_id == product_id)
        artifacts_q = artifacts_q.where(Artifact.product_id == product_id)
        runs_q = runs_q.where(Run.product_id == product_id)
    points = db.execute(points_q).scalars().all()
    artifacts = db.execute(artifacts_q).scalars().all()
    runs = db.execute(runs_q).scalars().all()

    # Index artifacts by utm_campaign — the primary join key. Several
    # versions of a piece share the same campaign (v1, v2, v3 of "Give me
    # something better"), so this is a list per campaign and the matcher
    # disambiguates by the other dims.
    artifacts_by_campaign: dict[str, list[Artifact]] = defaultdict(list)
    for a in artifacts:
        if a.utm_campaign:
            artifacts_by_campaign[a.utm_campaign].append(a)

    # Per-stage accumulators keyed by (bucket, baseline|attributed|total).
    # We track baseline & attributed separately so the UI can render
    # honest "backdrop vs signal" without re-querying.
    per_stage: dict[str, dict] = {s: {
        "metric_names": set(),
        "baseline": defaultdict(float),
        "attributed": defaultdict(float),
        "total": defaultdict(float),
        # Per-artifact contribution under this stage (for "top contributors").
        "by_artifact": defaultdict(float),
    } for s in _STAGES}
    ungrouped: set[str] = set()
    bucket_keys: set[str] = set()

    for p in points:
        stage = _stage_of(p.metric_name)
        if stage is None:
            ungrouped.add(p.metric_name)
            continue
        bkey = _bucket(p.date, bucket)
        bucket_keys.add(bkey)
        per_stage[stage]["metric_names"].add(p.metric_name)
        per_stage[stage]["total"][bkey] += p.value
        if p.is_baseline:
            per_stage[stage]["baseline"][bkey] += p.value
        else:
            # Only NON-baseline points contribute to "attributed". A row
            # without a matching artifact is signal-tagged-but-unmatched —
            # we still count it as non-baseline (it's what we changed),
            # just not credited to a specific artifact.
            per_stage[stage]["attributed"][bkey] += p.value
            art = _matching_artifact(p, artifacts_by_campaign)
            if art is not None:
                per_stage[stage]["by_artifact"][art.id] += p.value

    # Production lane — drafts produced / approved / pending + total cost,
    # bucketed on the same timeline. This is the "what did we feed the
    # engines?" axis no off-the-shelf tool has.
    production: dict[str, dict] = {
        "drafts_produced": defaultdict(int),
        "drafts_ready": defaultdict(int),
        "drafts_pending": defaultdict(int),
        "runs_total": defaultdict(int),
        "cost_usd": defaultdict(float),
    }
    for a in artifacts:
        bkey = _bucket(a.created_at.date(), bucket)
        bucket_keys.add(bkey)
        production["drafts_produced"][bkey] += 1
        if a.status == "ready":
            production["drafts_ready"][bkey] += 1
        elif a.status == "pending_review":
            production["drafts_pending"][bkey] += 1
    for r in runs:
        bkey = _bucket(r.created_at.date(), bucket)
        bucket_keys.add(bkey)
        production["runs_total"][bkey] += 1
        production["cost_usd"][bkey] += float(r.cost_usd or 0.0)

    # Ordered bucket list — most recent last so the UI can render a
    # left-to-right trend cleanly. Truncate to the most recent N if asked.
    buckets = sorted(bucket_keys)
    if limit_buckets and len(buckets) > limit_buckets:
        buckets = buckets[-limit_buckets:]

    # Render to dense arrays per bucket so the UI doesn't have to align
    # sparse dicts.
    artifacts_by_id = {a.id: a for a in artifacts}
    out_stages: dict[str, dict] = {}
    for s in _STAGES:
        agg = per_stage[s]
        top_contribs = sorted(agg["by_artifact"].items(),
                              key=lambda kv: kv[1], reverse=True)[:5]
        out_stages[s] = {
            "label": _STAGE_LABEL[s],
            "baseline":   [round(agg["baseline"].get(b, 0.0), 4) for b in buckets],
            "attributed": [round(agg["attributed"].get(b, 0.0), 4) for b in buckets],
            "total":      [round(agg["total"].get(b, 0.0), 4) for b in buckets],
            "metric_names": sorted(agg["metric_names"]),
            "top_contributors": [_serialize_contrib(artifacts_by_id.get(aid), val)
                                 for aid, val in top_contribs
                                 if artifacts_by_id.get(aid) is not None],
        }
    out_production = {
        "buckets": buckets,
        "drafts_produced": [production["drafts_produced"].get(b, 0) for b in buckets],
        "drafts_ready":    [production["drafts_ready"].get(b, 0) for b in buckets],
        "drafts_pending":  [production["drafts_pending"].get(b, 0) for b in buckets],
        "runs_total":      [production["runs_total"].get(b, 0) for b in buckets],
        "cost_usd":        [round(production["cost_usd"].get(b, 0.0), 4)
                            for b in buckets],
    }
    return {
        "buckets": buckets,
        "bucket": bucket,
        "stages": out_stages,
        "production": out_production,
        "ungrouped_metrics": sorted(ungrouped),
    }


def _serialize_contrib(art: Artifact | None, value: float) -> dict:
    """Compact contributor row for the UI: the artifact's title + the UTM
    tuple + the contribution. The UI links the title to the run detail."""
    if art is None:
        return {"artifact_id": None, "title": "(unknown)", "value": value}
    return {
        "artifact_id": art.id,
        "title": art.title,
        "utm_campaign": art.utm_campaign,
        "utm_source": art.utm_source,
        "utm_medium": art.utm_medium,
        "utm_content": art.utm_content,
        "value": round(value, 4),
    }
