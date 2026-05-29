"""
Evidence-attached recommendations.

Two kinds, intentionally separate:

  * "trend"    — DETERMINISTIC rules over the org's own funnel + production
                 data. Every suggestion carries the specific numbers that
                 motivate it (the "because"). Always available — no LLM
                 dependency. This is the real feature.

  * "industry" — the LLM's general read of the space. EXPLICITLY labeled
                 as perspective (not data). Carries confabulation risk, so
                 we never present it as verified. The deterministic fallback
                 returns a SINGLE labeled "no industry suggestions without
                 an LLM key" item so the section degrades gracefully and
                 stays honest.

Every suggestion has an `idea_*` slot the UI uses for the one-click route
into the content engine: clicking "Generate this" POSTs an /api/runs
content_engine task pre-filled with that idea, so dashboard → suggestion
→ generate (tagged, graded) closes the loop.

Same synthesis.py LLM-or-template fallback shape used everywhere else.
"""
from __future__ import annotations

import json

from app.config import get_settings


# UI labels. Source-label rendering MUST tell the truth: the trend kind is
# "grounded in your data"; the industry kind is "verify before acting".
_LABEL_TREND = "Trend-based — grounded in your data"
_LABEL_INDUSTRY = "General industry perspective — verify before acting"


# Thresholds for the deterministic trend rules. Kept here so they're tunable
# without chasing them through the code. The numbers are deliberately
# conservative: a "rose 40%" rule should NOT fire on noise.
_PCT_RISE = 0.30        # 30% bucket-over-bucket = "rose"
_PCT_FALL = -0.20       # -20% = "fell"
_FLAT_TOL = 0.05        # within ±5% = "flat"


def _pct_change(prev: float, cur: float) -> float | None:
    """Returns the fractional change (e.g. 0.4 for +40%). None when prev
    is zero (so we don't divide-by-zero or pretend a 0→1 is "+infinity%")."""
    if prev <= 0:
        return None
    return (cur - prev) / prev


def _sum_recent(series: list[float], n: int) -> float:
    """Sum the most recent n buckets."""
    return float(sum(series[-n:] or [0.0]))


def _trend_label(p: float | None) -> str:
    if p is None:
        return "no prior period"
    if p >= _PCT_RISE:
        return f"+{int(p * 100)}%"
    if p <= _PCT_FALL:
        return f"{int(p * 100)}%"
    if abs(p) <= _FLAT_TOL:
        return "flat"
    return f"{int(p * 100):+d}%"


def trend_suggestions(funnel: dict, profile: dict | None) -> list[dict]:
    """Run the deterministic rules over the funnel view. Returns a list of
    suggestion dicts: {kind, recommendation, evidence, confidence,
    source_label, idea_content_type, idea_topic, idea_target}.

    The rules look at the latest two halves of the bucket window — the most
    recent N buckets vs the N before that — so they compare like-for-like
    even when the timeline is sparse. Each rule produces at most ONE
    suggestion to avoid spamming the cockpit."""
    stages = funnel.get("stages") or {}
    buckets = funnel.get("buckets") or []
    if not buckets:
        return []
    half = max(1, len(buckets) // 2)
    out: list[dict] = []

    def _stage_totals(stage_key: str) -> tuple[float, float, float]:
        s = (stages.get(stage_key) or {})
        totals = s.get("total") or []
        prev = _sum_recent(totals[:half], half)
        cur = _sum_recent(totals[half:], len(buckets) - half)
        return prev, cur, sum(totals or [0.0])

    top_prev, top_cur, _ = _stage_totals("top")
    mid_prev, mid_cur, _ = _stage_totals("middle")
    bot_prev, bot_cur, _ = _stage_totals("bottom")
    top_chg = _pct_change(top_prev, top_cur)
    mid_chg = _pct_change(mid_prev, mid_cur)
    bot_chg = _pct_change(bot_prev, bot_cur)

    # Pick a target audience suggestion-side from the profile's ICP titles
    # / industries so "Generate this" lands with sensible defaults.
    icp = (profile or {}).get("icp") or {}
    target = ((icp.get("titles") or [None])[0]
              or (icp.get("industries") or [None])[0]
              or "your ICP")

    # Rule 1 — mid-funnel up but bottom flat/down: sales handoff leak.
    if (mid_chg is not None and bot_chg is not None
            and mid_chg >= _PCT_RISE
            and (bot_chg <= _FLAT_TOL or abs(bot_chg) <= _FLAT_TOL)):
        out.append({
            "kind": "trend",
            "recommendation": (
                "Mid-funnel engagement rose materially but opportunities "
                "stayed flat — the bottom of the funnel is leaking. Produce "
                "bottom-funnel assets (case studies, ROI calculators, "
                "comparison guides) to convert the engaged traffic."),
            "evidence": {
                "middle_change_pct": round(mid_chg * 100, 1),
                "bottom_change_pct": round((bot_chg or 0) * 100, 1),
                "middle_recent_total": round(mid_cur, 2),
                "middle_prior_total": round(mid_prev, 2),
                "bottom_recent_total": round(bot_cur, 2),
                "bottom_prior_total": round(bot_prev, 2),
                "buckets_compared": len(buckets),
            },
            "confidence": "high" if (mid_chg or 0) >= 0.5 else "medium",
            "source_label": _LABEL_TREND,
            "idea_content_type": "blog_outline",
            "idea_topic": "ROI of switching from spreadsheets to a unified platform",
            "idea_target": target,
        })

    # Rule 2 — top up but mid flat: traffic landing without engagement.
    if (top_chg is not None and mid_chg is not None
            and top_chg >= _PCT_RISE and abs(mid_chg) <= _FLAT_TOL):
        out.append({
            "kind": "trend",
            "recommendation": (
                "Awareness is up but engagement is flat — visitors are "
                "arriving and bouncing. Sharpen mid-funnel assets (use "
                "cases, demos, content that earns the second click) and "
                "review landing page → conversion paths."),
            "evidence": {
                "top_change_pct": round(top_chg * 100, 1),
                "middle_change_pct": round((mid_chg or 0) * 100, 1),
                "top_recent_total": round(top_cur, 2),
                "middle_recent_total": round(mid_cur, 2),
                "buckets_compared": len(buckets),
            },
            "confidence": "medium",
            "source_label": _LABEL_TREND,
            "idea_content_type": "ad",
            "idea_topic": "Show the 'aha' moment in 30 seconds",
            "idea_target": target,
        })

    # Rule 3 — bottom of the funnel is falling: act now.
    if bot_chg is not None and bot_chg <= _PCT_FALL:
        out.append({
            "kind": "trend",
            "recommendation": (
                "Bottom-funnel volume is declining — pipeline risk. "
                "Prioritize re-engagement of recent engaged accounts and "
                "ABM outreach to in-market segments before the trend "
                "compounds."),
            "evidence": {
                "bottom_change_pct": round(bot_chg * 100, 1),
                "bottom_recent_total": round(bot_cur, 2),
                "bottom_prior_total": round(bot_prev, 2),
                "buckets_compared": len(buckets),
            },
            "confidence": "high",
            "source_label": _LABEL_TREND,
            "idea_content_type": "email",
            "idea_topic": "We noticed you came back — should we run a tailored demo?",
            "idea_target": target,
        })

    # Rule 4 — production happening but no attribution arrives.
    prod = funnel.get("production") or {}
    drafts_total = sum(prod.get("drafts_produced") or [0])
    # Sum attributed across all stages. If we made content but nothing's
    # attributed, the tagged links probably aren't live yet.
    attributed_total = sum(
        sum(stages.get(s, {}).get("attributed") or [0.0]) for s in stages
    )
    if drafts_total > 0 and attributed_total == 0:
        out.append({
            "kind": "trend",
            "recommendation": (
                f"You produced {drafts_total} draft(s) but no incoming "
                "performance row carries a matching UTM. Verify your "
                "tagged links are live on the channels you published — "
                "the join key only flows in if the link does."),
            "evidence": {
                "drafts_produced_total": drafts_total,
                "attributed_value_total": 0.0,
            },
            "confidence": "high",
            "source_label": _LABEL_TREND,
            # No content idea — the action here is publishing, not making.
            "idea_content_type": None, "idea_topic": None, "idea_target": None,
        })

    return out


def _industry_fallback(profile: dict | None, funnel: dict) -> list[dict]:
    """No-LLM fallback for the industry kind. We do NOT fabricate industry
    facts AND we do not surface an alert card the user can't act on. The
    §6 Principle: if the feature can't run, it shows nothing — not an
    error and not invented data. The Insights / HQ surfaces filter
    empty lists as a quiet empty state rather than a "configure key"
    card cluttering the dashboard."""
    return []


def industry_suggestions(profile: dict | None, funnel: dict) -> list[dict]:
    """LLM-or-fallback industry perspective. ALWAYS labeled non-data so the
    UI renders it with the right "verify before acting" badge."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        return _industry_fallback(profile, funnel)
    try:
        return _llm_industry(profile, funnel, settings)
    except Exception:
        return _industry_fallback(profile, funnel)


def _llm_industry(profile: dict | None, funnel: dict, settings) -> list[dict]:
    """Ask Claude for at most three industry-perspective items in the SAME
    shape `trend_suggestions` produces, so downstream code is uniform.
    Defensive parse; on anything off-shape we return the fallback."""
    import anthropic

    grounding = {
        "product_summary": (profile or {}).get("product_summary", ""),
        "value_prop": (profile or {}).get("value_prop", ""),
        "icp": (profile or {}).get("icp") or {},
        "competitors": [c.get("name") for c in (profile or {}).get("competitors") or []
                        if isinstance(c, dict) and c.get("name")],
        "stages_summary": {
            s: {"recent_attributed": sum(
                    (funnel.get("stages") or {}).get(s, {}).get("attributed") or [0.0]),
                "metric_names": (funnel.get("stages") or {}).get(s, {}).get("metric_names") or []}
            for s in ("top", "middle", "bottom")
        },
    }
    prompt = (
        "You are an outside demand-gen advisor giving the org a "
        "GENERAL INDUSTRY PERSPECTIVE — not verified data about THIS "
        "company. Return ONLY a JSON array of at most 3 suggestions, "
        "each matching:\n"
        "  {\"recommendation\": <string>, \"evidence\": {\"note\": <string>}, "
        "\"confidence\": \"low\"|\"medium\"|\"high\", "
        "\"idea_content_type\": null | one of [email, ad, social_post, blog_outline], "
        "\"idea_topic\": <string|null>, \"idea_target\": <string|null>}\n\n"
        "Mark anything you're not certain about as confidence=low. Be "
        "honest if the org's saved profile doesn't give you enough to go on.\n\n"
        f"GROUNDING:\n{json.dumps(grounding, indent=2)}"
    )
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=settings.anthropic_model, max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    try:
        parsed = json.loads(raw)
    except Exception:
        return _industry_fallback(profile, funnel)
    if not isinstance(parsed, list):
        return _industry_fallback(profile, funnel)
    out: list[dict] = []
    for item in parsed[:3]:
        if not isinstance(item, dict) or not item.get("recommendation"):
            continue
        out.append({
            "kind": "industry",
            "recommendation": str(item["recommendation"]),
            "evidence": item.get("evidence") if isinstance(item.get("evidence"), dict)
                        else {"note": "industry perspective; not verified data"},
            "confidence": item.get("confidence") or "low",
            # The label is non-negotiable — never let the model relabel
            # itself as grounded.
            "source_label": _LABEL_INDUSTRY,
            "idea_content_type": item.get("idea_content_type"),
            "idea_topic": item.get("idea_topic"),
            "idea_target": item.get("idea_target"),
        })
    return out or _industry_fallback(profile, funnel)
