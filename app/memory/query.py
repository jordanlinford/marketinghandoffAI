"""
query_memory — the single shared entry point for the persistent
marketing memory read-path.

What it does:
    Reads metric_points (the org's own performance telemetry) joined to
    campaigns (for audience) and aggregates them by dimension into
    EVIDENCE-BACKED PATTERNS with HONEST CONFIDENCE. Every pattern can
    show the numbers behind it.

What it does NOT do:
    - No ML, no learned model, no embedding, no opaque scoring.
    - No predictions ("this WILL convert at X"). Past-tense only.
    - No autonomous action — patterns are advisory, human-overridable.
    - No cross-org pooling. Memory is the ORG's own data only.

Weighting (one sentence, inspectable):
    Each metric_point contributes its raw value plus a recency weight
    that decays linearly from 1.0 today to 0.1 at `lookback_days` ago,
    and confidence is a deterministic function of sample size and the
    age of the freshest supporting data point.

Thin-data discipline:
    Combinations with 1-2 data points are RETURNED so the system is
    honest that it's watching the dimension, but their `observation`
    string is framed as "not enough yet" — never as a confident finding
    dressed up with a small number. `insufficient`-confidence patterns
    are NEVER allowed to reorder defaults downstream (see the planner
    and content_engine integrations).
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import Campaign, MetricPoint
from app.tenancy import scoped

# --------------------------------------------------------------------------
# Confidence tiers — sample-size thresholds + a recency-staleness cutoff.
# Tiers, not false precision: high | moderate | low | insufficient.
# --------------------------------------------------------------------------
_INSUFFICIENT_MAX_N = 2          # 0-2 data points → insufficient, framed honestly
_LOW_MIN_N = 3                   # 3-9
_MODERATE_MIN_N = 10             # 10-49
_HIGH_MIN_N = 50                 # 50+ (and recent — see _STALE_DAYS)
_STALE_DAYS = 90                 # freshest point older than this caps confidence

# Conversion-rate computation: metric_name match is case-insensitive
# substring so a CSV row labeled "Conversions" or "demo_requests" or
# "Total Clicks" all sort into the right bucket. The dashboard already
# treats these as the funnel-stage signals; we use the same vocabulary.
_NUMERATOR_TOKENS = (
    "conversion", "lead", "signup", "demo", "trial",
    "subscribe", "purchase", "request",
)
_DENOMINATOR_TOKENS = ("click", "session", "tap", "visit")


def _utcnow_date() -> date:
    return datetime.now(timezone.utc).date()


def _recency_weight(point_date: date, today: date, lookback_days: int) -> float:
    # Linear decay from 1.0 (today) to 0.1 (lookback_days ago). Older
    # points are kept around for sample-size signal but don't move the
    # mean. The shape of the curve is intentionally simple — anyone
    # should be able to explain it in one breath.
    age = max(0, (today - point_date).days)
    if age >= lookback_days:
        return 0.1
    return max(0.1, 1.0 - 0.9 * (age / lookback_days))


def _kind_of_metric(metric_name: str) -> str:
    name = (metric_name or "").lower()
    for tok in _NUMERATOR_TOKENS:
        if tok in name:
            return "numerator"
    for tok in _DENOMINATOR_TOKENS:
        if tok in name:
            return "denominator"
    return "other"


def _confidence_for(n: int, days_since_freshest: int | None) -> tuple[str, str]:
    # Honest confidence: function of sample size + recency only. No
    # consistency / variance term in v1 — keeping the rule simple beats a
    # cleverer rule the user can't reproduce in their head.
    if n <= _INSUFFICIENT_MAX_N:
        return ("insufficient",
                f"only {n} data point{'s' if n != 1 else ''} so far")
    stale = (days_since_freshest is not None
             and days_since_freshest > _STALE_DAYS)
    if n >= _HIGH_MIN_N and not stale:
        return ("high",
                f"{n} data points; freshest within {_STALE_DAYS} days")
    if n >= _MODERATE_MIN_N:
        if stale:
            return ("low",
                    f"{n} data points but freshest is {days_since_freshest} "
                    f"days old (stale)")
        return ("moderate",
                f"{n} data points; freshest "
                f"{days_since_freshest if days_since_freshest is not None else '?'}"
                f" days ago")
    if stale:
        return ("low",
                f"only {n} data points and freshest is "
                f"{days_since_freshest} days old")
    return ("low", f"only {n} data points")


def _humanize_channel(channel: str) -> str:
    # "linkedin" -> "LinkedIn"; "google" -> "Google"; keep multi-word
    # channels readable. Display-only; matching stays case-insensitive.
    if not channel:
        return ""
    base = re.sub(r"[_-]+", " ", channel).strip()
    fixups = {"linkedin": "LinkedIn", "youtube": "YouTube",
              "tiktok": "TikTok", "instagram": "Instagram"}
    return fixups.get(base.lower(), base.title())


def _observation_thick(dimension: str, key_display: str, agg: dict) -> str:
    # Evidence-first phrasing. Past tense. NEVER predictive — we describe
    # what HAS happened, not what WILL happen.
    n = agg["data_points"]
    fresh = agg.get("days_since_freshest")
    fresh_str = (f"most recent {fresh} day{'s' if fresh != 1 else ''} ago"
                 if fresh is not None else "")
    if agg["clicks"] > 0 and agg["conversions"] >= 0:
        rate = agg["conversions"] / agg["clicks"] if agg["clicks"] else 0
        return (
            f"{key_display} drove {rate * 100:.1f} conversion"
            f"{'s' if rate * 100 != 1 else ''} per 100 clicks "
            f"({agg['clicks']:g} clicks, {agg['conversions']:g} conversions "
            f"across {n} data points"
            f"{'; ' + fresh_str if fresh_str else ''})."
        )
    if agg["conversions"] > 0:
        return (
            f"{key_display} produced {agg['conversions']:g} conversion"
            f"{'s' if agg['conversions'] != 1 else ''} across {n} data points"
            f"{'; ' + fresh_str if fresh_str else ''} (no click denominator "
            f"to compute a rate)."
        )
    if agg["clicks"] > 0:
        return (
            f"{key_display} carried {agg['clicks']:g} click"
            f"{'s' if agg['clicks'] != 1 else ''} across {n} data points"
            f"{'; ' + fresh_str if fresh_str else ''} but no conversions have "
            f"been attributed to it yet."
        )
    return (
        f"{key_display} has {n} data points recorded across "
        f"{', '.join(sorted(agg['metric_names'])) or 'other metrics'}"
        f"{'; ' + fresh_str if fresh_str else ''}."
    )


def _observation_thin(key_display: str, n: int) -> str:
    # Honest thin-data framing — used for BOTH `insufficient` and `low`
    # confidence patterns. Reads as "watching," never as a finding.
    # The forbidden shape (per brief) is e.g. "LinkedIn: 0.7 effectiveness
    # (low confidence)" or "Reddit drove 1.4 conv/100 clicks" with a LOW
    # badge — both read as findings with small numbers attached. The
    # numbers stay in metric_basis for the inspectable detail; the
    # user-facing one-liner defers rather than quantifies.
    return (
        f"Only {n} data point{'s' if n != 1 else ''} so far for "
        f"{key_display} — not enough to call a pattern yet. Watching "
        f"as data grows."
    )


def _bucket_init() -> dict:
    return {"clicks": 0.0, "conversions": 0.0, "other": 0.0,
            "data_points": 0, "metric_names": set(),
            "freshest_date": None, "values_weighted": 0.0,
            "weight_total": 0.0}


def _bucket_record(b: dict, mp: MetricPoint, weight: float) -> None:
    kind = _kind_of_metric(mp.metric_name)
    v = float(mp.value or 0.0)
    if kind == "numerator":
        b["conversions"] += v
    elif kind == "denominator":
        b["clicks"] += v
    else:
        b["other"] += v
    b["data_points"] += 1
    b["metric_names"].add(mp.metric_name)
    if b["freshest_date"] is None or mp.date > b["freshest_date"]:
        b["freshest_date"] = mp.date
    b["values_weighted"] += v * weight
    b["weight_total"] += weight


def _bucket_finalize(b: dict, today: date) -> dict:
    fresh = b["freshest_date"]
    days_since_freshest = (today - fresh).days if fresh else None
    out = dict(b)
    out["days_since_freshest"] = days_since_freshest
    out["metric_names"] = sorted(b["metric_names"])
    out["weighted_mean"] = (b["values_weighted"] / b["weight_total"]
                            if b["weight_total"] else 0.0)
    if b["clicks"] > 0:
        out["conversion_rate"] = b["conversions"] / b["clicks"]
    else:
        out["conversion_rate"] = None
    return out


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def query_memory(db: Session, org_id: str, *,
                 product_id: str | None = None,
                 audience: str | None = None,
                 channel: str | None = None,
                 content_type: str | None = None,
                 campaign_type: str | None = None,
                 lookback_days: int = 180) -> list[dict]:
    """Return EVIDENCE-BACKED patterns for the requested dimensions.

    Patterns are aggregated, weighted by recency, and tagged with honest
    confidence. Caller passes the SQLAlchemy session — memory does not
    open a session of its own. All reads go through scoped() so cross-
    org data can never appear.

    Filters narrow the data BEFORE aggregation; they do not silence
    other dimensions. If no filter matches anything, returns [].

    Returns a list of Pattern dicts:
        {dimension, key, key_display, observation, metric_basis,
         sample_size, recency, confidence, confidence_reason}
    """
    today = _utcnow_date()
    cutoff = today - timedelta(days=lookback_days)

    q = scoped(MetricPoint, org_id).where(MetricPoint.date >= cutoff)
    if product_id:
        q = q.where(MetricPoint.product_id == product_id)
    if channel:
        q = q.where(MetricPoint.utm_source == channel.lower())
    if content_type:
        q = q.where(MetricPoint.utm_medium == content_type.lower())
    points: list[MetricPoint] = list(db.execute(q).scalars().all())

    # Index campaigns by utm_campaign for the audience + campaign_type
    # join. One scan, no per-point query — keeps the read cheap on a
    # cold cache. scoped() guarantees Acme's campaigns never appear here
    # even if a future bug lets an Onit metric_point carry a foreign
    # utm_campaign value.
    camp_by_utm: dict[str, Campaign] = {}
    for c in db.execute(scoped(Campaign, org_id)).scalars().all():
        if c.utm_campaign:
            camp_by_utm[c.utm_campaign] = c

    # Dimension buckets. We always compute "channel" + "content_type"
    # buckets; audience and campaign_type buckets need a campaign join.
    by_channel: dict[str, dict] = defaultdict(_bucket_init)
    by_channel_audience: dict[tuple[str, str], dict] = defaultdict(_bucket_init)
    by_content_type: dict[str, dict] = defaultdict(_bucket_init)
    by_campaign_type: dict[str, dict] = defaultdict(_bucket_init)

    for mp in points:
        if mp.is_baseline:
            # Baseline rows are backdrop only — they exist to show the
            # before-picture in the funnel view, not to attribute or
            # judge channel performance. Including them would inflate
            # the denominator and wash out the actual signal.
            continue
        if not (mp.utm_campaign or "").strip():
            # Untagged rows are the same kind of backdrop as baselines:
            # they have NO campaign attribution, so they cannot honestly
            # claim "this channel worked for this audience for this CTA."
            # The dashboard already keeps them out of the attributed
            # view (baseline-vs-attributed discipline); memory extends
            # the same rule. They remain visible in the funnel; they do
            # NOT generate confidence scores or actionable patterns.
            continue
        w = _recency_weight(mp.date, today, lookback_days)
        chan = (mp.utm_source or "").lower()
        ctype = (mp.utm_medium or "").lower()
        camp = camp_by_utm.get(mp.utm_campaign or "")

        # Channel bucket
        if chan:
            _bucket_record(by_channel[chan], mp, w)

        # Content-type bucket (utm_medium)
        if ctype:
            _bucket_record(by_content_type[ctype], mp, w)

        # Channel × audience: every persona on the linked campaign gets
        # a row; with no personas (or no campaign), the point doesn't
        # contribute to audience-level patterns.
        if chan and camp and camp.target_personas:
            for persona in camp.target_personas:
                pkey = (chan, str(persona).strip())
                if not pkey[1]:
                    continue
                # Audience filter — when caller pinned an audience, only
                # buckets matching it are populated.
                if audience and pkey[1].lower() != audience.lower():
                    continue
                _bucket_record(by_channel_audience[pkey], mp, w)

        # Campaign-type bucket — only when the metric_point joins to a
        # campaign we recognize.
        if camp and camp.campaign_type:
            ctk = camp.campaign_type.lower()
            if campaign_type and ctk != campaign_type.lower():
                continue
            _bucket_record(by_campaign_type[ctk], mp, w)

    out: list[dict] = []

    # Materialize "channel" patterns
    for chan, raw in by_channel.items():
        agg = _bucket_finalize(raw, today)
        n = agg["data_points"]
        conf, reason = _confidence_for(n, agg["days_since_freshest"])
        display = _humanize_channel(chan)
        out.append({
            "dimension": "channel",
            "key": chan,
            "key_display": display,
            "observation": (_observation_thin(display, n)
                            if conf in ("insufficient", "low")
                            else _observation_thick("channel", display, agg)),
            "metric_basis": {
                "channel": chan,
                "clicks": round(agg["clicks"], 4),
                "conversions": round(agg["conversions"], 4),
                "conversion_rate": (round(agg["conversion_rate"], 4)
                                    if agg["conversion_rate"] is not None
                                    else None),
                "data_points": n,
                "metric_names": agg["metric_names"],
                "days_since_freshest": agg["days_since_freshest"],
            },
            "sample_size": n,
            "recency": (f"most recent: {agg['days_since_freshest']} days ago"
                        if agg["days_since_freshest"] is not None
                        else "no recency info"),
            "confidence": conf,
            "confidence_reason": reason,
        })

    # Materialize "channel x audience" patterns
    for (chan, pers), raw in by_channel_audience.items():
        agg = _bucket_finalize(raw, today)
        n = agg["data_points"]
        conf, reason = _confidence_for(n, agg["days_since_freshest"])
        chan_display = _humanize_channel(chan)
        key_display = f"{chan_display} × {pers}"
        out.append({
            "dimension": "channel x audience",
            "key": f"{chan}|{pers}",
            "key_display": key_display,
            "observation": (_observation_thin(key_display, n)
                            if conf in ("insufficient", "low")
                            else _observation_thick("channel x audience",
                                                    key_display, agg)),
            "metric_basis": {
                "channel": chan, "audience": pers,
                "clicks": round(agg["clicks"], 4),
                "conversions": round(agg["conversions"], 4),
                "conversion_rate": (round(agg["conversion_rate"], 4)
                                    if agg["conversion_rate"] is not None
                                    else None),
                "data_points": n,
                "metric_names": agg["metric_names"],
                "days_since_freshest": agg["days_since_freshest"],
            },
            "sample_size": n,
            "recency": (f"most recent: {agg['days_since_freshest']} days ago"
                        if agg["days_since_freshest"] is not None
                        else "no recency info"),
            "confidence": conf,
            "confidence_reason": reason,
        })

    # Materialize "content_type" patterns
    for ctype, raw in by_content_type.items():
        agg = _bucket_finalize(raw, today)
        n = agg["data_points"]
        conf, reason = _confidence_for(n, agg["days_since_freshest"])
        display = ctype.replace("_", " ").title()
        out.append({
            "dimension": "content_type",
            "key": ctype,
            "key_display": display,
            "observation": (_observation_thin(display, n)
                            if conf in ("insufficient", "low")
                            else _observation_thick("content_type", display, agg)),
            "metric_basis": {
                "content_type": ctype,
                "clicks": round(agg["clicks"], 4),
                "conversions": round(agg["conversions"], 4),
                "conversion_rate": (round(agg["conversion_rate"], 4)
                                    if agg["conversion_rate"] is not None
                                    else None),
                "data_points": n,
                "metric_names": agg["metric_names"],
                "days_since_freshest": agg["days_since_freshest"],
            },
            "sample_size": n,
            "recency": (f"most recent: {agg['days_since_freshest']} days ago"
                        if agg["days_since_freshest"] is not None
                        else "no recency info"),
            "confidence": conf,
            "confidence_reason": reason,
        })

    # Materialize "campaign_type" patterns
    for ctk, raw in by_campaign_type.items():
        agg = _bucket_finalize(raw, today)
        n = agg["data_points"]
        conf, reason = _confidence_for(n, agg["days_since_freshest"])
        display = ctk.replace("_", " ").title()
        out.append({
            "dimension": "campaign_type",
            "key": ctk,
            "key_display": display,
            "observation": (_observation_thin(display, n)
                            if conf in ("insufficient", "low")
                            else _observation_thick("campaign_type", display, agg)),
            "metric_basis": {
                "campaign_type": ctk,
                "clicks": round(agg["clicks"], 4),
                "conversions": round(agg["conversions"], 4),
                "conversion_rate": (round(agg["conversion_rate"], 4)
                                    if agg["conversion_rate"] is not None
                                    else None),
                "data_points": n,
                "metric_names": agg["metric_names"],
                "days_since_freshest": agg["days_since_freshest"],
            },
            "sample_size": n,
            "recency": (f"most recent: {agg['days_since_freshest']} days ago"
                        if agg["days_since_freshest"] is not None
                        else "no recency info"),
            "confidence": conf,
            "confidence_reason": reason,
        })

    # Stable ordering — confidence > sample_size > conversion_rate. Ties
    # break by key for determinism (so a test that asserts ordering can).
    _CONF_ORDER = {"high": 0, "moderate": 1, "low": 2, "insufficient": 3}
    out.sort(key=lambda p: (
        _CONF_ORDER[p["confidence"]],
        -p["sample_size"],
        -(p["metric_basis"].get("conversion_rate") or 0.0),
        p["key"],
    ))
    return out


def summarize_for_prompt(patterns: list[dict], *,
                         max_lines: int = 4) -> str:
    """Render the strongest patterns as a short text block for an LLM
    system message. NEVER includes raw column names, JSON, or labeled
    fields — that violates the no-echo discipline and the model tends to
    parrot the structure. Plain past-tense English, evidence-first.

    Returns '' when nothing actionable is available so the caller can
    cleanly skip the section in the prompt.
    """
    if not patterns:
        return ""
    actionable = [p for p in patterns if p["confidence"] != "insufficient"]
    if not actionable:
        return ""
    lines: list[str] = []
    for p in actionable[:max_lines]:
        lines.append(f"- {p['observation']}")
    return "\n".join(lines)
