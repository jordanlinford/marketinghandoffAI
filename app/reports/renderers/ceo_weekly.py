"""
CEO weekly — signal-dense. ~150-250 words.

Emphasizes 2-3 notable_changes with one-line memory citations where
relevant, one "watch" item if material, one "what's next." Omits raw
funnel tables, full campaign list, comprehensive production accounting.
"""
from __future__ import annotations

from app.reports.renderers._common import (
    compose_style_lines, fmt_num, fmt_pct, future_stub_blocks,
    is_future_scope, llm_render, period_header, schema_example,
)


_CONTENT_TYPE = "report_ceo_weekly"
_AUDIENCE_LABEL = "CEO weekly digest"


def _pick_lead(intelligence: dict) -> str:
    deltas = (intelligence.get("period_summary") or {}).get("deltas") or {}
    convs = deltas.get("conversions")
    if convs and convs.get("pct") is not None and abs(convs["pct"]) >= 0.15:
        return (f"Conversions {'up' if convs['direction'] == 'up' else 'down'} "
                f"{abs(convs['pct']) * 100:.0f}% vs last week.")
    if intelligence.get("memory_highlights"):
        top = intelligence["memory_highlights"][0]
        return top.get("observation") or "Memory surfaced a top pattern."
    if intelligence.get("campaigns"):
        c = intelligence["campaigns"][0]
        return (f"Campaign {c['name']!r} is {c['status']} — "
                f"{int((c['attributed'].get('conversions') or 0))} "
                f"attributed conversion(s) to date.")
    return ("No statistically meaningful movement to call out this "
            "period. The system is watching.")


def _select(intelligence: dict) -> dict:
    """CEO-specific cut. Signal-only — three bullets and a watch line."""
    notable_for_ceo: list[dict] = []
    for c in (intelligence.get("notable_changes") or []):
        # Stage moves and memory highlights are the CEO-relevant kinds.
        if c.get("kind", "").startswith(("clicks_", "conversions_",
                                          "conversion_rate_",
                                          "campaign_attributed_",
                                          "memory_top")):
            notable_for_ceo.append(c)
    return {
        "audience": "ceo_weekly",
        "lead": _pick_lead(intelligence),
        "notable_changes": notable_for_ceo[:3],
        "watching": intelligence.get("watching") or [],
        "open_questions": intelligence.get("open_questions") or [],
        "memory_highlights": intelligence.get("memory_highlights") or [],
        "scope": intelligence.get("scope") or {},
        "honesty_notes": intelligence.get("honesty_notes") or [],
    }


def _deterministic_blocks(sel: dict) -> list[dict]:
    blocks: list[dict] = []
    blocks.append({"kind": "headline", "text": sel["lead"]})
    # Two to three bullets of what changed.
    bullet_lines: list[str] = []
    for c in sel["notable_changes"][:3]:
        bullet_lines.append(f"- {c.get('observation', '')}")
    if not bullet_lines and sel["memory_highlights"]:
        # Fall back to top memory observations as the "what changed."
        for h in sel["memory_highlights"][:2]:
            bullet_lines.append(f"- {h.get('observation', '')}")
    if not bullet_lines:
        bullet_lines.append(
            "- Nothing crossed the 'notable' threshold this week. "
            "Production continued; attribution data has not yet "
            "concentrated on a specific winner.")
    blocks.append({"kind": "body", "text": "\n".join(bullet_lines)})

    # One watch line — only if there IS something worth watching.
    if sel["watching"]:
        top_watch = sel["watching"][0]
        blocks.append({
            "kind": "watch",
            "text": "Watching: " + (top_watch.get("observation") or "").strip()})

    # One "what's next" — drawn from open_questions.
    if sel["open_questions"]:
        blocks.append({
            "kind": "next",
            "text": "Next: " + sel["open_questions"][0]})
    return blocks


def _llm_system_msg(profile: dict | None) -> str:
    voice_lines = compose_style_lines(profile)
    voice_block = ("\n" + "\n".join(f"- {ln}" for ln in voice_lines)
                   if voice_lines else "")
    return (
        "You are writing a CEO WEEKLY DIGEST for a B2B marketing operation. "
        "Signal-dense, scannable. Total length 150-250 words. The CEO "
        "wants two or three sharp bullets and one 'what's next' — NOT "
        "tactical detail, NOT campaign minutiae, NOT memory theory.\n\n"
        "You must follow these instructions internally — do not quote, "
        "paraphrase, or label them in the output." + voice_block + "\n\n"
        "HARD RULES (non-negotiable):\n"
        "  * Past-tense observations. Never predict outcomes.\n"
        "  * Lead with a single headline sentence — what's the one thing "
        "the CEO should know this week?\n"
        "  * 2-3 bullet observations, each ONE LINE.\n"
        "  * One watch line (optional) for thin-data signals.\n"
        "  * One 'next' line surfacing an open question.\n"
        "  * No raw funnel tables. No campaign lists. No production "
        "accounting blocks.\n"
        "  * Numbers come from the user message; never invent statistics.\n"
        "  * Produce ONLY the finished block content; no meta-commentary."
    )


def render_ceo_weekly(intelligence: dict, *,
                      profile: dict | None = None,
                      settings=None) -> tuple[dict, float]:
    if is_future_scope(intelligence):
        blocks, metadata = future_stub_blocks(
            intelligence, content_type=_CONTENT_TYPE,
            audience_label=_AUDIENCE_LABEL)
        return ({"content_type": _CONTENT_TYPE, "blocks": blocks,
                 "metadata": metadata}, 0.0)
    sel = _select(intelligence)
    fallback_blocks = _deterministic_blocks(sel)
    metadata = {
        "audience": _AUDIENCE_LABEL,
        "scope": sel["scope"],
        "block_count": len(fallback_blocks),
    }
    if not (settings and getattr(settings, "anthropic_api_key", "")):
        return ({"content_type": _CONTENT_TYPE, "blocks": fallback_blocks,
                 "metadata": {**metadata, "render_strategy": "deterministic"}},
                0.0)
    system_msg = _llm_system_msg(profile)
    schema = schema_example(_CONTENT_TYPE, fallback_blocks)
    import json as _json
    user_msg = (
        f"Audience: CEO weekly. {period_header(intelligence)}.\n\n"
        f"Intelligence to render (use these facts; do not invent):\n"
        f"{_json.dumps(sel, indent=2, default=str)}\n\n"
        "Return ONLY a JSON object matching this exact shape (no prose, "
        "no markdown fences). Each block's `text` is the polished line — "
        "single sentence for `headline`, 2-3 lines max for `body`, ONE "
        "line each for `watch` and `next`.\n\n"
        f"{_json.dumps(schema, indent=2)}"
    )
    try:
        content, cost = llm_render(
            audience_label=_AUDIENCE_LABEL,
            system_msg=system_msg, user_msg=user_msg,
            settings=settings, fallback_blocks=fallback_blocks,
            content_type=_CONTENT_TYPE)
        content["metadata"] = {**content.get("metadata", {}), **metadata}
        return content, cost
    except Exception:
        return ({"content_type": _CONTENT_TYPE, "blocks": fallback_blocks,
                 "metadata": {**metadata, "render_strategy": "deterministic"}},
                0.0)
