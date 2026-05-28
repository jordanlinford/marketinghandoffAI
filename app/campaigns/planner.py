"""
Campaign planner — Step-2 proposal (one cheap LLM call) + channel mix.

DESIGN GUARDRAILS (same shape as the rest of the system):
  * Reuses synthesis.py's LLM-or-fallback pattern. With no key / parse
    failure / API error, fall back to a deterministic rule-based plan
    derived from `campaign_type`, the chosen channels, and the CTA.
  * Returns ONE plan per call. No content is generated here — this is a
    strategy step. The /generate endpoint (which calls the content
    engine per plan item) is the only place LLM money is spent generating
    actual copy.
  * `recommend_channels(..., performance_context=None)` exposes a seam
    for a FUTURE substrate-grounded build. v1 ignores it; the smoke test
    verifies the seam exists and that v1 passes None.

The plan shape (intentionally small + JSON-shaped so it round-trips
cleanly to/from the DB column `campaigns.plan`):

  {
    "derivative_assets": [
      {"id": "<short slug>", "content_type": "email|ad|social_post|blog_outline|carousel",
       "channel": "linkedin|google|email|meta|reddit|webinar|organic",
       "angle": "<one-line topic / angle>",
       "rationale": "<why this asset in this slot>",
       "cadence_hint": "<week 1 / day 3 / nurture follow-up, etc.>"},
      ...
    ],
    "channel_mix": [
      {"channel": "linkedin", "weight": "primary|secondary|support",
       "rationale": "<why this channel for this campaign>"},
      ...
    ],
    "cadence_guidance": "<2–4 sentences of high-level sequencing>"
  }
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.config import get_settings


# Best-practice asset mixes per campaign type, used by the deterministic
# fallback. The brief says these can be generic; the LLM path can iterate
# and tailor; the deterministic path is the always-works floor.
_TYPE_TEMPLATES: dict[str, list[dict]] = {
    "awareness": [
        {"content_type": "social_post", "channel": "linkedin",
         "angle": "Problem framing — name the pattern your audience already feels.",
         "cadence_hint": "Week 1, day 1"},
        {"content_type": "social_post", "channel": "linkedin",
         "angle": "Contrast post — old way vs. new way.",
         "cadence_hint": "Week 1, day 3"},
        {"content_type": "ad", "channel": "linkedin",
         "angle": "Single-message awareness ad pointing at the parent asset.",
         "cadence_hint": "Week 1–3, continuous"},
        {"content_type": "blog_outline", "channel": "organic",
         "angle": "Long-form supporting article — SEO anchor.",
         "cadence_hint": "Week 2"},
    ],
    "demand_gen": [
        {"content_type": "email", "channel": "email",
         "angle": "Pain-led outbound: lead with the cost of doing nothing.",
         "cadence_hint": "Sequence step 1"},
        {"content_type": "email", "channel": "email",
         "angle": "Proof-led follow-up: customer outcome from the parent asset.",
         "cadence_hint": "Sequence step 2 (+3 days)"},
        {"content_type": "ad", "channel": "linkedin",
         "angle": "Retargeting ad — drive engaged visitors back to the parent asset.",
         "cadence_hint": "Weeks 1–4"},
        {"content_type": "social_post", "channel": "linkedin",
         "angle": "Quote-card from the parent asset — distil one specific claim.",
         "cadence_hint": "Week 1"},
    ],
    "launch": [
        {"content_type": "email", "channel": "email",
         "angle": "Announcement — what's new + why it matters.",
         "cadence_hint": "Day 0"},
        {"content_type": "social_post", "channel": "linkedin",
         "angle": "Founder/PMM voice post — what we built and why.",
         "cadence_hint": "Day 0"},
        {"content_type": "social_post", "channel": "linkedin",
         "angle": "Carousel structure: top 3 use cases (structure only — visual layer is future).",
         "cadence_hint": "Day 2"},
        {"content_type": "blog_outline", "channel": "organic",
         "angle": "Deep-dive blog supporting the launch claims.",
         "cadence_hint": "Day 0"},
        {"content_type": "email", "channel": "email",
         "angle": "Sales-team-ready follow-up email — for AE outbound.",
         "cadence_hint": "Day 3"},
    ],
    "nurture": [
        {"content_type": "email", "channel": "email",
         "angle": "Value-add email — practical framework from the parent asset.",
         "cadence_hint": "Drip step 1"},
        {"content_type": "email", "channel": "email",
         "angle": "Customer-story email — outcome from the parent asset.",
         "cadence_hint": "Drip step 2 (+5 days)"},
        {"content_type": "social_post", "channel": "linkedin",
         "angle": "Repurpose key idea as a short LinkedIn post.",
         "cadence_hint": "Mid-cadence"},
    ],
    "competitive": [
        {"content_type": "blog_outline", "channel": "organic",
         "angle": "Honest comparison vs. the named competitors — what each is for.",
         "cadence_hint": "Week 1"},
        {"content_type": "ad", "channel": "google",
         "angle": "Search ad targeting competitor comparison queries.",
         "cadence_hint": "Continuous"},
        {"content_type": "email", "channel": "email",
         "angle": "Sales-handoff email when a prospect names a competitor.",
         "cadence_hint": "Triggered"},
        {"content_type": "social_post", "channel": "linkedin",
         "angle": "Customer migration story — switched from X to us, here's what changed.",
         "cadence_hint": "Week 2"},
    ],
    "other": [
        {"content_type": "email", "channel": "email",
         "angle": "Pain-led outbound around the campaign objective.",
         "cadence_hint": "Step 1"},
        {"content_type": "social_post", "channel": "linkedin",
         "angle": "Hook post pointing at the parent asset.",
         "cadence_hint": "Step 1"},
        {"content_type": "ad", "channel": "linkedin",
         "angle": "Retargeting ad supporting the parent asset.",
         "cadence_hint": "Continuous"},
    ],
}

# Channel weight defaults per type — used by recommend_channels.
_TYPE_CHANNEL_WEIGHTS: dict[str, dict[str, str]] = {
    "awareness":   {"linkedin": "primary", "organic": "primary",
                    "google": "secondary"},
    "demand_gen":  {"linkedin": "primary", "email": "primary",
                    "google": "secondary"},
    "launch":      {"email": "primary", "linkedin": "primary",
                    "organic": "support"},
    "nurture":     {"email": "primary", "linkedin": "support"},
    "competitive": {"google": "primary", "organic": "primary",
                    "email": "support"},
    "other":       {"linkedin": "primary", "email": "secondary"},
}


def _slug(text: str, max_len: int = 60) -> str:
    s = (text or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")[:max_len] or "item"


# ---------------------------------------------------------------------------
# Channel recommendations
# ---------------------------------------------------------------------------
def recommend_channels(campaign_type: str,
                       selected_channels: list[str] | None,
                       primary_cta: str,
                       *,
                       performance_context: dict | None = None) -> dict:
    """Generic best-practice channel mix. v1 is deterministic.

    `performance_context` is RESERVED for a future substrate-grounded
    build (Build B's dimensions-column discipline applied to the planner):
    when wired, it will carry metric_points-derived channel conversion
    data, attribution outcomes, and engagement patterns the planner can
    weigh. v1 IGNORES it; the smoke test verifies the seam exists and
    that v1 passes None.

    Returns:
      {"recommended_channels": [...],
       "channel_mix": [{channel, weight, rationale}, ...],
       "rationale": "<one-line summary>"}
    """
    ctype = (campaign_type or "other").lower()
    weights = dict(_TYPE_CHANNEL_WEIGHTS.get(ctype, _TYPE_CHANNEL_WEIGHTS["other"]))
    # If the user explicitly picked channels, prefer those — bump anything
    # they picked to at least "secondary" so the recommendation respects
    # the user's intent without overriding the type's primaries.
    selected = [c.lower() for c in (selected_channels or []) if c]
    for c in selected:
        weights.setdefault(c, "secondary")
    mix = [{"channel": c, "weight": w,
            "rationale": _channel_rationale(c, ctype, primary_cta)}
           for c, w in weights.items()]
    return {
        "recommended_channels": [m["channel"] for m in mix],
        "channel_mix": mix,
        "rationale": (
            f"Best-practice mix for a {ctype.replace('_', ' ')} campaign "
            f"driving toward {(primary_cta or 'the CTA').strip()}. "
            "Tailored to your channel selection; no performance data "
            "consulted in v1 (the substrate-grounded path is reserved "
            "via the performance_context seam)."),
    }


def _channel_rationale(channel: str, campaign_type: str, primary_cta: str) -> str:
    cta = (primary_cta or "the CTA").strip()
    blurbs = {
        "linkedin": f"In-feed credibility for B2B buyers; supports the "
                    f"{campaign_type} narrative with both organic + paid.",
        "email":    f"Highest-control channel for a {campaign_type} sequence "
                    f"pushing toward '{cta}'.",
        "google":   f"Intent capture for buyers actively searching the "
                    f"{campaign_type} problem space.",
        "organic":  f"Long-tail SEO anchor that the {campaign_type} narrative "
                    "compounds against over time.",
        "meta":     "B2C / SMB consumer-grade audiences; weight low for B2B.",
        "reddit":   "Niche community trust; use for genuine practitioner threads.",
        "webinar":  f"High-intent capture moment to convert on '{cta}'.",
    }
    return blurbs.get(channel, f"Supports the {campaign_type} motion.")


# ---------------------------------------------------------------------------
# Plan proposal (Step 2)
# ---------------------------------------------------------------------------
def propose_plan(campaign_row, profile: dict | None,
                 parent_asset_summary: str = "",
                 *, settings=None) -> tuple[dict, float]:
    """Returns (plan, cost). LLM-or-fallback per synthesis.py."""
    settings = settings or get_settings()
    fallback = _rule_based_plan(campaign_row, profile or {})
    if not settings.anthropic_api_key:
        return fallback, 0.0
    try:
        return _llm_propose(campaign_row, profile or {},
                            parent_asset_summary, settings, fallback)
    except Exception:
        return fallback, 0.0


def _rule_based_plan(campaign_row, profile: dict) -> dict:
    """Deterministic plan from campaign_type + chosen channels + product
    profile. Sensible non-empty default; no LLM required."""
    ctype = (campaign_row.campaign_type or "other").lower()
    template = list(_TYPE_TEMPLATES.get(ctype, _TYPE_TEMPLATES["other"]))
    chosen = [c.lower() for c in (campaign_row.selected_channels or []) if c]
    # If the user picked channels and the template has items on channels
    # they didn't pick, swap to a preferred channel. Best-effort; the
    # template is the floor.
    if chosen:
        first = chosen[0]
        for item in template:
            if item["channel"] not in chosen:
                item["channel"] = first
    cta = (campaign_row.primary_cta or "book a demo").strip()
    audience = _audience_phrase(campaign_row, profile)
    derivatives: list[dict] = []
    for idx, item in enumerate(template, start=1):
        item = dict(item)
        topic = _angle_to_topic(item["angle"], cta, profile)
        item["topic"] = topic
        item["id"] = _slug(f"{idx}-{item['channel']}-{topic}")
        item["audience"] = audience
        item["rationale"] = item.get("rationale", "") or _default_rationale(item, ctype, cta)
        derivatives.append(item)
    channel_recs = recommend_channels(
        ctype, campaign_row.selected_channels, cta)
    cadence = (
        f"Plan covers ~2 weeks. Lead with the {derivatives[0]['channel']} "
        f"items in week 1, follow with email cadence + paid support. "
        f"Re-evaluate after week 2 against the funnel view in Insights."
    )
    return {
        "derivative_assets": derivatives,
        "channel_mix": channel_recs["channel_mix"],
        "cadence_guidance": cadence,
        "source": "deterministic-rule-based",
    }


def _audience_phrase(campaign_row, profile: dict) -> str:
    if campaign_row.target_personas:
        return ", ".join(campaign_row.target_personas[:3])
    icp = (profile or {}).get("icp") or {}
    titles = icp.get("titles") or []
    if titles:
        return ", ".join(titles[:3])
    return "your target audience"


def _angle_to_topic(angle: str, cta: str, profile: dict) -> str:
    # Keep the angle as-is for the topic; the content_engine's generate
    # step takes it as the topic field.
    return (angle or "").strip()


def _default_rationale(item: dict, ctype: str, cta: str) -> str:
    return (f"Supports the {ctype} motion by leveraging "
            f"{item['channel']} for a {item['content_type']} that drives "
            f"toward '{cta}'.")


def _llm_propose(campaign_row, profile: dict, parent_asset_summary: str,
                 settings, fallback: dict) -> tuple[dict, float]:
    """Single cheap LLM call. On any parse/structure issue → return the
    fallback (we don't want a failed plan call to block the user)."""
    import anthropic

    grounding = {
        "campaign": {
            "name": campaign_row.name, "type": campaign_row.campaign_type,
            "objective": campaign_row.objective,
            "primary_cta": campaign_row.primary_cta,
            "selected_channels": campaign_row.selected_channels or [],
            "personas": campaign_row.target_personas or [],
            "segments": campaign_row.target_segments or [],
            "industries": campaign_row.target_industries or [],
        },
        "product": {
            "product_summary": profile.get("product_summary", ""),
            "value_prop": profile.get("value_prop", ""),
            "icp": profile.get("icp") or {},
            "competitors": [
                c.get("name") for c in (profile.get("competitors") or [])
                if isinstance(c, dict) and c.get("name")],
        },
        "parent_asset_summary": parent_asset_summary[:2000],
    }
    fallback_schema = {
        "derivative_assets": [
            {"id": "<short slug>", "content_type": "<email|ad|social_post|"
             "blog_outline|carousel>",
             "channel": "<linkedin|google|email|meta|reddit|webinar|organic>",
             "topic": "<one-line topic/angle>",
             "angle": "<one-line angle the asset takes>",
             "audience": "<who this lands with>",
             "rationale": "<why this asset in this slot>",
             "cadence_hint": "<e.g. Week 1, day 3>"}
        ],
        "channel_mix": [
            {"channel": "<channel>", "weight": "<primary|secondary|support>",
             "rationale": "<why this channel for this campaign>"}
        ],
        "cadence_guidance": "<2–4 sentences of high-level sequencing>",
    }
    system_msg = (
        "You are a senior B2B campaign strategist. Produce a coordinated "
        "campaign plan — a small, opinionated list of derivative assets "
        "to create, the right channel mix, and lightweight cadence "
        "guidance. You are NOT writing the assets themselves; another "
        "system does that. Be specific. Reference the parent asset by "
        "what it does, not by repeating it.\n\n"
        "HARD RULES:\n"
        "  * Return ONLY a JSON object matching the schema below. No "
        "prose, no markdown fences.\n"
        "  * Keep the derivative list small + coordinated (5–8 items is "
        "the sweet spot). Each item must be specific enough that a "
        "writer could draft it next.\n"
        "  * Each item must include id, content_type, channel, topic, "
        "angle, audience, rationale, cadence_hint.\n"
        "  * If a carousel is proposed, mark angle as 'structure only — "
        "visual rendering is a future layer.'\n"
        "  * cadence_guidance is high-level (2–4 sentences); never a "
        "scheduling engine.\n"
    )
    user_msg = (
        f"Schema (replace placeholder strings with real values):\n"
        f"{json.dumps(fallback_schema, indent=2)}\n\n"
        f"Campaign + product grounding:\n"
        f"{json.dumps(grounding, indent=2)}"
    )
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=settings.anthropic_model, max_tokens=2500,
        system=system_msg,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    plan = _parse_json_envelope(raw)
    # Defensive: malformed shape → fallback, with the raw text saved on
    # the plan so a developer can debug without re-running the call.
    if (not isinstance(plan, dict) or not isinstance(plan.get("derivative_assets"), list)
            or not plan["derivative_assets"]):
        f = dict(fallback)
        f["llm_raw_excerpt"] = (raw or "")[:300]
        return f, _cost_from_usage(msg)
    # Stamp ids if the model forgot.
    for idx, item in enumerate(plan["derivative_assets"], start=1):
        if not isinstance(item, dict):
            continue
        item.setdefault("id", _slug(f"{idx}-{item.get('channel','x')}-"
                                    f"{item.get('topic') or item.get('angle','item')}"))
    plan.setdefault("source", "llm")
    return plan, _cost_from_usage(msg)


def _parse_json_envelope(raw: str) -> Any:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        pass
    if s.startswith("```"):
        inner = s.strip("`").strip()
        if "\n" in inner and inner.split("\n", 1)[0].strip().lower() in (
                "json", "jsonc"):
            inner = inner.split("\n", 1)[1]
        try:
            return json.loads(inner.strip())
        except Exception:
            pass
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(s[start:end + 1])
        except Exception:
            pass
    return None


def _cost_from_usage(msg) -> float:
    usage = getattr(msg, "usage", None)
    if not usage:
        return 0.0
    return round(
        (usage.input_tokens * 3 + usage.output_tokens * 15) / 1_000_000, 6)
