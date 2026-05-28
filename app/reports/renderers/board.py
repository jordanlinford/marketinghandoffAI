"""
Board update — defensive, accountable. ~600-900 words.

Emphasizes period summary with delta, memory_highlights at moderate+,
campaigns by attributed pipeline contribution, open_questions framed
as strategic asks/risks. Omits tactical content lists, watching list,
individual run cost detail.
"""
from __future__ import annotations

from app.reports.renderers._common import (
    compose_style_lines, fmt_num, fmt_pct, future_stub_blocks,
    is_future_scope, llm_render, memory_lines, period_header,
    schema_example,
)


_CONTENT_TYPE = "report_board"
_AUDIENCE_LABEL = "Board update"


def _select(intelligence: dict) -> dict:
    """Board-specific cut. Emphasizes accountability + trajectory."""
    return {
        "audience": "board",
        "scope": intelligence.get("scope") or {},
        "period_summary": intelligence.get("period_summary") or {},
        "notable_changes": [
            c for c in (intelligence.get("notable_changes") or [])
            # Board cares about stage-level deltas + campaign outcomes.
            # Memory-highlight notables are excluded here — the dedicated
            # highlights block carries them instead, with full evidence.
            if c.get("kind") != "memory_top_highlight"
        ],
        "memory_highlights": intelligence.get("memory_highlights") or [],
        "campaigns": intelligence.get("campaigns") or [],
        # Strategic asks — convert open questions into accountable framing.
        "asks": intelligence.get("open_questions") or [],
        "honesty_notes": intelligence.get("honesty_notes") or [],
        "production_summary": {
            "artifacts_total": (intelligence.get("production") or {}).get("artifacts_total", 0),
            "artifacts_ready": (intelligence.get("production") or {}).get("artifacts_ready", 0),
            "cost_usd_total": (intelligence.get("production") or {}).get("cost_usd_total", 0.0),
        },
    }


def _deterministic_blocks(sel: dict) -> list[dict]:
    blocks: list[dict] = []
    sc = sel["scope"]
    blocks.append({
        "kind": "title",
        "text": f"Board update — {sc.get('start')} to {sc.get('end')}"})
    blocks.append({
        "kind": "section_heading",
        "text": "Period summary"})
    attr = (sel["period_summary"].get("attributed") or {})
    deltas = sel["period_summary"].get("deltas")
    lines = [
        f"Attributed clicks: {fmt_num(attr.get('clicks'))}",
        f"Attributed conversions: {fmt_num(attr.get('conversions'))}",
        f"Attributed conversion rate: {fmt_pct(attr.get('conversion_rate'))}",
    ]
    if deltas:
        bits = []
        if (deltas.get("clicks") or {}).get("pct") is not None:
            bits.append(f"clicks {deltas['clicks']['pct'] * 100:+.0f}%")
        if (deltas.get("conversions") or {}).get("pct") is not None:
            bits.append(
                f"conversions {deltas['conversions']['pct'] * 100:+.0f}%")
        if (deltas.get("conversion_rate") or {}).get("pct") is not None:
            bits.append(
                f"conversion rate {deltas['conversion_rate']['pct'] * 100:+.0f}%")
        if bits:
            lines.append("Trajectory vs prior period: " + "; ".join(bits) + ".")
    else:
        lines.append("Trajectory: no prior-period data available; this is "
                     "the first measured period.")
    blocks.append({"kind": "body", "text": "\n".join(lines)})

    blocks.append({"kind": "section_heading",
                   "text": "What the data is saying (memory highlights)"})
    if sel["memory_highlights"]:
        evidence_lines = []
        for h in sel["memory_highlights"][:4]:
            evidence_lines.append(f"- {h.get('observation', '')}")
        blocks.append({"kind": "body", "text": "\n".join(evidence_lines)})
    else:
        blocks.append({
            "kind": "body",
            "text": "Memory has not yet identified a clearly-performing "
                    "channel or content type at moderate-or-higher confidence. "
                    "This is the honest read; more data will sharpen it."})

    blocks.append({"kind": "section_heading",
                   "text": "Campaign performance"})
    if sel["campaigns"]:
        campaign_lines = []
        for c in sel["campaigns"][:4]:
            attr = c["attributed"]
            campaign_lines.append(
                f"- {c['name']} ({c['status']}): "
                f"{fmt_num(attr.get('conversions'))} attributed conversion(s) "
                f"on {fmt_num(attr.get('clicks'))} click(s); "
                f"{c.get('plan_items')} planned item(s), "
                f"{c.get('generated_assets')} generated.")
        blocks.append({"kind": "body", "text": "\n".join(campaign_lines)})
    else:
        blocks.append({
            "kind": "body",
            "text": "No campaigns ran in this period. The next period's "
                    "production should be organized into a campaign so "
                    "attribution can compound."})

    blocks.append({"kind": "section_heading",
                   "text": "Production accountability"})
    p = sel["production_summary"]
    blocks.append({
        "kind": "body",
        "text": (f"{p.get('artifacts_total', 0)} asset(s) produced "
                 f"this period at an LLM cost of ${p.get('cost_usd_total', 0):.2f}; "
                 f"{p.get('artifacts_ready', 0)} have been approved + are "
                 f"shipped or shippable.")})

    blocks.append({"kind": "section_heading",
                   "text": "Strategic asks"})
    if sel["asks"]:
        asks_lines = [f"- {q}" for q in sel["asks"][:4]]
        blocks.append({"kind": "body", "text": "\n".join(asks_lines)})
    else:
        blocks.append({"kind": "body",
                       "text": "No strategic asks surfaced from the data this period."})

    if sel["honesty_notes"]:
        blocks.append({"kind": "section_heading",
                       "text": "Notes on what this report is and isn't"})
        notes_lines = [f"- {n}" for n in sel["honesty_notes"][:4]]
        blocks.append({"kind": "body", "text": "\n".join(notes_lines)})
    return blocks


def _llm_system_msg(profile: dict | None) -> str:
    voice_lines = compose_style_lines(profile)
    voice_block = ("\n" + "\n".join(f"- {ln}" for ln in voice_lines)
                   if voice_lines else "")
    return (
        "You are writing a BOARD UPDATE for a B2B marketing operation. "
        "Defensive posture. Measured, accountable tone. ~600-900 words "
        "of finished prose. The reader is a board member who wants "
        "trajectory + accountability — NOT tactical detail.\n\n"
        "You must follow these instructions internally — do not quote, "
        "paraphrase, or label them in the output." + voice_block + "\n\n"
        "HARD RULES (non-negotiable):\n"
        "  * Past-tense only. Describe what HAS happened. NEVER predict.\n"
        "  * Numbers come from the user message's intelligence object. "
        "Do NOT invent statistics, customer quotes, or proof points.\n"
        "  * Untagged metric_points are funnel backdrop ONLY — never "
        "attribute them to a campaign or to marketing action. The "
        "honesty_notes call this out; respect it.\n"
        "  * If memory has no high/moderate patterns, say so honestly "
        "rather than dressing up thin data.\n"
        "  * Produce ONLY the finished block content. No commentary, no "
        "labels inside block text, no 'Tone:'/'Voice:'/'Body:' meta-tags."
    )


def render_board(intelligence: dict, *,
                 profile: dict | None = None,
                 settings=None) -> tuple[dict, float]:
    # Future-date guard: when the engine flags the scope as future,
    # no renderer may synthesize a "what happened" narrative. Emit the
    # shared honest stub instead — current memory survives as a clearly-
    # labeled reference baseline.
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
        f"Audience: Board update. {period_header(intelligence)}.\n\n"
        f"Intelligence to render (use these facts; do not invent):\n"
        f"{_json.dumps(sel, indent=2, default=str)}\n\n"
        "Return ONLY a JSON object matching this exact shape (no prose, "
        "no markdown fences). Each block's `text` is finished prose — "
        "replace the placeholder description with real content. Keep the "
        "block order; you may add up to two extra `body` blocks if a "
        "section needs more narrative.\n\n"
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
        # Fall back silently — never crash a report on a flaky LLM call.
        return ({"content_type": _CONTENT_TYPE, "blocks": fallback_blocks,
                 "metadata": {**metadata, "render_strategy": "deterministic"}},
                0.0)
