"""
Board update — defensive, accountable. ~600-900 words.

Emphasizes period summary with delta, memory_highlights at moderate+,
campaigns by attributed pipeline contribution, open_questions framed
as strategic asks/risks. Omits tactical content lists, watching list,
individual run cost detail.
"""
from __future__ import annotations

from app.reports.evidence import (build_ledger_from_intelligence,
                                   validate_evidence_binding)
from app.reports.renderers._common import (
    anti_slop_lines, cite_num, compose_style_lines, fmt_num, fmt_pct,
    future_stub_blocks, is_future_scope, llm_render, memory_lines,
    period_header, schema_example,
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
            "artifacts_pending_review": (intelligence.get("production") or {}).get(
                "artifacts_pending_review", 0),
            "cost_usd_total": (intelligence.get("production") or {}).get("cost_usd_total", 0.0),
        },
    }


def _deterministic_blocks(sel: dict, ledger=None) -> list[dict]:
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
        f"Attributed clicks: {cite_num(ledger, 'period_summary.attributed.clicks', attr.get('clicks'), formatter=fmt_num)}",
        f"Attributed conversions: {cite_num(ledger, 'period_summary.attributed.conversions', attr.get('conversions'), formatter=fmt_num)}",
        f"Attributed conversion rate: {cite_num(ledger, 'period_summary.attributed.conversion_rate', attr.get('conversion_rate'), formatter=fmt_pct)}",
    ]
    if deltas:
        bits = []
        if (deltas.get("clicks") or {}).get("pct") is not None:
            pct = deltas['clicks']['pct']
            bits.append("clicks " + cite_num(
                ledger, "period_summary.deltas.clicks.pct", pct,
                formatter=lambda v: f"{v * 100:+.0f}%"))
        if (deltas.get("conversions") or {}).get("pct") is not None:
            pct = deltas['conversions']['pct']
            bits.append("conversions " + cite_num(
                ledger, "period_summary.deltas.conversions.pct", pct,
                formatter=lambda v: f"{v * 100:+.0f}%"))
        if (deltas.get("conversion_rate") or {}).get("pct") is not None:
            pct = deltas['conversion_rate']['pct']
            bits.append("conversion rate " + cite_num(
                ledger, "period_summary.deltas.conversion_rate.pct", pct,
                formatter=lambda v: f"{v * 100:+.0f}%"))
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
        for i, h in enumerate(sel["memory_highlights"][:4]):
            evidence_lines.append(
                _format_memory_line(h, i, ledger))
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
        for i, c in enumerate(sel["campaigns"][:4]):
            c_attr = c["attributed"]
            campaign_lines.append(
                f"- {c['name']} ({c['status']}): "
                f"{cite_num(ledger, f'campaigns[{i}].attributed.conversions', c_attr.get('conversions'), formatter=fmt_num)} attributed conversion(s) "
                f"on {cite_num(ledger, f'campaigns[{i}].attributed.clicks', c_attr.get('clicks'), formatter=fmt_num)} click(s); "
                f"{cite_num(ledger, f'campaigns[{i}].plan_items', c.get('plan_items'), formatter=fmt_num)} planned item(s), "
                f"{cite_num(ledger, f'campaigns[{i}].generated_assets', c.get('generated_assets'), formatter=fmt_num)} generated.")
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
        "text": (
            cite_num(ledger, "production.artifacts_total",
                     p.get('artifacts_total', 0), formatter=fmt_num)
            + " asset(s) produced this period at an LLM cost of "
            + cite_num(ledger, "production.cost_usd_total",
                       p.get('cost_usd_total', 0),
                       formatter=lambda v: f"${float(v):.2f}")
            + "; "
            + cite_num(ledger, "production.artifacts_ready",
                       p.get('artifacts_ready', 0), formatter=fmt_num)
            + " have been approved + are shipped or shippable.")})

    blocks.append({"kind": "section_heading",
                   "text": "Strategic asks"})
    if sel["asks"]:
        # The engine's _compute_open_questions embeds numbers inline for
        # the "approval queue depth" question. We rebuild that specific
        # shape with a ledger marker so the validator binds. Other
        # questions are text-only and emit verbatim.
        prod_info = sel.get("production_summary") or {}
        pending_review = prod_info.get("artifacts_pending_review")
        asks_lines = []
        for q in sel["asks"][:4]:
            if q.startswith("There are ") and "draft(s) sitting" in q \
                    and pending_review is not None:
                asks_lines.append(
                    "- There are "
                    + cite_num(ledger,
                                "production.artifacts_pending_review",
                                pending_review, formatter=fmt_num)
                    + " draft(s) sitting in the approval queue. "
                    "Reviewing them is the cheapest way to convert "
                    "produced work into shipped work.")
            else:
                asks_lines.append(f"- {q}")
        blocks.append({"kind": "body", "text": "\n".join(asks_lines)})
    else:
        blocks.append({"kind": "body",
                       "text": "No strategic asks surfaced from the data this period."})

    if sel["honesty_notes"]:
        blocks.append({"kind": "section_heading",
                       "text": "Notes on what this report is and isn't"})
        notes_lines = []
        # The "untagged volume excluded" note from the engine's
        # _compute_honesty_notes embeds the backdrop counts inline. We
        # rebuild it with per-number ledger markers so the validator
        # binds (it would otherwise see three bare integers). Other
        # honesty-note shapes are text-only and emit verbatim.
        intel_ps = (sel.get("period_summary") or {})
        backdrop = intel_ps.get("backdrop") or {}
        for n in sel["honesty_notes"][:4]:
            if n.startswith("Untagged volume excluded"):
                notes_lines.append(
                    "- Untagged volume excluded from attributed numbers: "
                    + cite_num(ledger, "period_summary.backdrop.clicks",
                                backdrop.get("clicks"), formatter=fmt_num)
                    + " click(s), "
                    + cite_num(ledger, "period_summary.backdrop.conversions",
                                backdrop.get("conversions"), formatter=fmt_num)
                    + " conversion(s) across "
                    + cite_num(ledger, "period_summary.backdrop.data_points",
                                backdrop.get("data_points"), formatter=fmt_num)
                    + " backdrop data point(s). These are funnel context "
                    "only — never quoted in this report as something "
                    "marketing drove.")
            else:
                notes_lines.append(f"- {n}")
        blocks.append({"kind": "body", "text": "\n".join(notes_lines)})
    return blocks


def _format_memory_line(highlight: dict, i: int, ledger) -> str:
    """Build a memory-highlight line with PER-NUMBER markers so each
    cited figure binds to its ledger entry. We rewrite the observation
    rather than emit the raw `observation` text because that raw text
    is a single string containing multiple values, none of which carry
    markers — the validator would correctly flag every number as
    unbound. Same facts, marker-bound phrasing."""
    mb = highlight.get("metric_basis") or {}
    label = highlight.get("key_display") or highlight.get("key") or f"highlight {i}"
    parts = [f"{label}:"]
    rate = mb.get("conversion_rate")
    clicks = mb.get("clicks")
    convs = mb.get("conversions")
    dp = mb.get("data_points")
    if rate is not None:
        parts.append(cite_num(
            ledger, f"memory_highlights[{i}].metric_basis.conversion_rate",
            rate, formatter=fmt_pct) + " conversion rate")
    elif convs:
        parts.append(cite_num(
            ledger, f"memory_highlights[{i}].metric_basis.conversions",
            convs, formatter=fmt_num) + " conversions")
    if clicks:
        parts.append("from " + cite_num(
            ledger, f"memory_highlights[{i}].metric_basis.clicks",
            clicks, formatter=fmt_num) + " clicks")
    if dp:
        parts.append("across " + cite_num(
            ledger, f"memory_highlights[{i}].metric_basis.data_points",
            dp, formatter=fmt_num) + " data points")
    return "- " + " ".join(parts) + "."


def _llm_system_msg(profile: dict | None) -> str:
    # voice + anti-slop are both "internal instructions you follow but
    # never describe in the output" — they live in the same bullet
    # block. anti_slop_lines() is universal; compose_style_lines() is
    # org-specific voice/banned-claims.
    voice_lines = compose_style_lines(profile) + anti_slop_lines()
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
        "  * EVERY quantitative claim — every number, percentage, "
        "currency figure, or count — MUST be followed immediately by a "
        "ledger marker of the form ⟦ev:<id>⟧ where <id> is "
        "the id of the matching entry in the EVIDENCE LEDGER provided in "
        "the user message. Example: 'Conversions rose 12%⟦ev:ev3⟧ "
        "vs prior period.' NEVER state a number that does not appear in "
        "the ledger. NEVER cite an id not in the ledger.\n"
        "  * Numbers come from the user message's intelligence object + "
        "ledger. Do NOT invent statistics, customer quotes, or proof "
        "points.\n"
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
                 settings=None,
                 ledger=None) -> tuple[dict, float]:
    # Build the evidence ledger up front so both the deterministic
    # blocks AND the LLM prompt can cite from the same source of truth.
    # Caller may pre-pass a ledger (the agent does — it needs the same
    # ledger for the post-render validation pass).
    if ledger is None:
        ledger = build_ledger_from_intelligence(intelligence)
    # Future-date guard: when the engine flags the scope as future,
    # no renderer may synthesize a "what happened" narrative. Emit the
    # shared honest stub instead — current memory survives as a clearly-
    # labeled reference baseline, with per-number markers so the
    # validator binds it correctly.
    if is_future_scope(intelligence):
        blocks, metadata = future_stub_blocks(
            intelligence, content_type=_CONTENT_TYPE,
            audience_label=_AUDIENCE_LABEL, ledger=ledger)
        return ({"content_type": _CONTENT_TYPE, "blocks": blocks,
                 "metadata": metadata}, 0.0)
    sel = _select(intelligence)
    fallback_blocks = _deterministic_blocks(sel, ledger=ledger)
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
        f"EVIDENCE LEDGER — every quantitative claim MUST cite an id "
        f"from this list via the marker ⟦ev:<id>⟧ immediately after "
        f"the number. NEVER state a number not in this ledger.\n"
        f"{_json.dumps(ledger.prompt_payload(), indent=2, default=str)}\n\n"
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
