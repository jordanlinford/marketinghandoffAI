"""
Sales leadership update — collaborative. ~300-500 words.

Emphasizes top_content (assets sales can use), campaigns in/near
sales-handoff stage, what's about to ship. Framed TO sales, not ABOUT
marketing. Omits board-style efficiency framing and deep memory pattern
theory.
"""
from __future__ import annotations

from app.reports.renderers._common import (
    compose_style_lines, fmt_num, fmt_pct, llm_render, period_header,
    schema_example,
)


_CONTENT_TYPE = "report_sales_leadership"
_AUDIENCE_LABEL = "Sales leadership update"


def _select(intelligence: dict) -> dict:
    """Sales-specific cut. Emphasizes what's usable + what's coming."""
    campaigns = intelligence.get("campaigns") or []
    # Sales cares about campaigns in active/planned states (about to ship
    # or shipping); archived/completed go to the bottom.
    sales_active = [c for c in campaigns
                    if c.get("status") in ("active", "generating",
                                            "planned", "draft")]
    return {
        "audience": "sales_leadership",
        "scope": intelligence.get("scope") or {},
        "top_content": intelligence.get("top_content") or [],
        "active_campaigns": sales_active[:5],
        "production_what_shipped": {
            "ready_count": (intelligence.get("production") or {}).get("artifacts_ready", 0),
            "by_content_type": (intelligence.get("production") or {}).get(
                "by_content_type", {}),
        },
        "memory_one_liner": (intelligence.get("memory_highlights") or [{}])[0].get(
            "observation", ""),
        "watching_segments": [
            w for w in (intelligence.get("watching") or [])
            if w.get("dimension") in ("channel x audience",)
        ][:2],
        "honesty_notes": intelligence.get("honesty_notes") or [],
    }


def _deterministic_blocks(sel: dict) -> list[dict]:
    blocks: list[dict] = []
    blocks.append({
        "kind": "headline",
        "text": "Here's what marketing has for sales this period."})

    # 1. What sales can use right now — top content.
    blocks.append({"kind": "section_heading",
                   "text": "What sales can use this week"})
    if sel["top_content"]:
        lines = []
        for tc in sel["top_content"][:4]:
            attr = tc.get("attributed") or {}
            lines.append(
                f"- {tc['title']} ({tc['content_type']}): "
                f"{int(attr.get('conversions') or 0)} attributed conversion(s) "
                f"on {int(attr.get('clicks') or 0)} click(s). Share with "
                f"prospects who match the campaign audience.")
        blocks.append({"kind": "body", "text": "\n".join(lines)})
    elif sel["production_what_shipped"]["ready_count"] > 0:
        bt = sel["production_what_shipped"]["by_content_type"]
        bt_str = ", ".join(f"{n} {t.replace('_', ' ')}"
                            for t, n in bt.items() if n)
        blocks.append({
            "kind": "body",
            "text": f"{sel['production_what_shipped']['ready_count']} approved "
                    f"asset(s) ready to share — {bt_str or 'mixed content types'}. "
                    "Attribution data hasn't yet concentrated on a clear "
                    "winner, so use whichever fits the prospect's stage."})
    else:
        blocks.append({
            "kind": "body",
            "text": "No approved assets ready to share from this period. "
                    "The next handoff will come once content currently in "
                    "review is approved."})

    # 2. Campaigns sales should know about.
    blocks.append({"kind": "section_heading",
                   "text": "Campaigns in play"})
    if sel["active_campaigns"]:
        camp_lines = []
        for c in sel["active_campaigns"][:4]:
            attr = c["attributed"]
            camp_lines.append(
                f"- {c['name']} — {c['status']} ({c.get('campaign_type', 'other')}). "
                f"{c.get('generated_assets', 0)} asset(s) generated; "
                f"{int(attr.get('conversions') or 0)} attributed conversion(s) "
                f"so far. Watch for traffic from the campaign UTM "
                f"({c.get('utm_campaign', 'n/a')}).")
        blocks.append({"kind": "body", "text": "\n".join(camp_lines)})
    else:
        blocks.append({
            "kind": "body",
            "text": "No active campaigns to coordinate around right now. "
                    "Sales-relevant production is happening at the asset "
                    "level (see above)."})

    # 3. What's coming — production lane forward-looking.
    blocks.append({"kind": "section_heading",
                   "text": "Shipping next"})
    bt = sel["production_what_shipped"]["by_content_type"]
    if bt:
        upcoming = ", ".join(f"{n} {t.replace('_', ' ')}"
                              for t, n in bt.items() if n)
        blocks.append({
            "kind": "body",
            "text": f"Approved or in review this period: {upcoming}. "
                    "Next set lands as the team works through current draft "
                    "feedback."})
    else:
        blocks.append({
            "kind": "body",
            "text": "Production pipeline is empty for this scope. Reach "
                    "out if a prospect or deal calls for a specific asset; "
                    "we can prioritize it."})

    # 4. Audience signal sales should know about — only if material.
    if sel["memory_one_liner"]:
        blocks.append({"kind": "section_heading",
                       "text": "What's resonating"})
        blocks.append({"kind": "body", "text": sel["memory_one_liner"]})

    if sel["watching_segments"]:
        blocks.append({"kind": "section_heading",
                       "text": "Segments we're starting to watch"})
        watch_lines = [f"- {w.get('observation', '')}"
                       for w in sel["watching_segments"]]
        blocks.append({"kind": "body", "text": "\n".join(watch_lines)})

    blocks.append({
        "kind": "close",
        "text": "If a deal needs an asset we haven't made, flag it back "
                "and we'll get it into the next cycle."})
    return blocks


def _llm_system_msg(profile: dict | None) -> str:
    voice_lines = compose_style_lines(profile)
    voice_block = ("\n" + "\n".join(f"- {ln}" for ln in voice_lines)
                   if voice_lines else "")
    return (
        "You are writing a SALES LEADERSHIP UPDATE for a B2B marketing "
        "operation. Collaborative tone — 'here's what we've got for you' — "
        "framed TO sales leadership, not ABOUT marketing. ~300-500 words "
        "of finished prose. Sales wants: what they can share with "
        "prospects, what campaigns to coordinate around, what's about to "
        "land. NOT board-style efficiency framing, NOT memory pattern "
        "theory.\n\n"
        "You must follow these instructions internally — do not quote, "
        "paraphrase, or label them in the output." + voice_block + "\n\n"
        "HARD RULES (non-negotiable):\n"
        "  * Past-tense + present-tense for current state. Never predict.\n"
        "  * Lead with what's usable RIGHT NOW (top content sales can share).\n"
        "  * Then the campaigns in play, with concrete UTM strings.\n"
        "  * Then what's shipping next.\n"
        "  * Numbers come from the user message; never invent statistics.\n"
        "  * Untagged metric_points are NOT attributable to marketing — "
        "never claim them as wins.\n"
        "  * Close with a collaborative line that invites a back-channel.\n"
        "  * Produce ONLY the finished block content; no meta-commentary."
    )


def render_sales_leadership(intelligence: dict, *,
                            profile: dict | None = None,
                            settings=None) -> tuple[dict, float]:
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
        f"Audience: Sales leadership. {period_header(intelligence)}.\n\n"
        f"Intelligence to render (use these facts; do not invent):\n"
        f"{_json.dumps(sel, indent=2, default=str)}\n\n"
        "Return ONLY a JSON object matching this exact shape (no prose, "
        "no markdown fences). Each block's `text` is finished prose — "
        "replace the placeholder description with real content.\n\n"
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
