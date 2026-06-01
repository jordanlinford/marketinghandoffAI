"""
White Paper — anchor #2 (after Report). A THESIS-DRIVEN long-form
document over the SAME intelligence object + ledger as reports.

This file is a RENDER STRATEGY, not a new generator. Per
docs/content-architecture.md (Level 3 — anchors are renderers): it
reads the same intelligence object the audience reports already read,
binds every quantitative claim to the SAME evidence ledger, and is
gated by the SAME validate_evidence_binding + severity layer reports
already use. Nothing in the trust path changes — that's the load-
bearing invariant smoke test #19b pins.

Shape differs from a report: thesis up front, evidence in support of
that thesis, recommendations grounded in the evidence, honest limits.
Not a period readout. Still ONLY asserts claims that bind to the
ledger (§6) — the renderer cannot exceed the intelligence object's
known facts. Qualitative claim binding is BACKLOG (see §6 Extension
in docs/cross-layer-disciplines.md) — for now, every figure carries a
⟦ev:id⟧ marker and the validator decides if it lands.

Renderer input contract is IDENTICAL to the audience renderers:
  render_whitepaper(intelligence, *, profile=None, settings=None,
                    ledger=None) -> (content_dict, cost_usd)
"""
from __future__ import annotations

from app.reports.evidence import build_ledger_from_intelligence
from app.reports.renderers._common import (
    cite_num, compose_style_lines, fmt_num, fmt_pct, future_stub_blocks,
    is_future_scope, llm_render, period_header, schema_example,
)


_CONTENT_TYPE = "whitepaper"
_AUDIENCE_LABEL = "White paper"


def _select(intelligence: dict) -> dict:
    """Whitepaper-specific cut. A whitepaper argues a thesis over
    bounded evidence; the strongest memory_highlight (the highest-
    confidence pattern the system actually knows) becomes the thesis
    spine. period_summary supplies the macro picture; campaigns
    supply the proof points; honesty_notes and watching make the
    limits explicit."""
    highlights = intelligence.get("memory_highlights") or []
    watching = intelligence.get("watching") or []
    return {
        "scope": intelligence.get("scope") or {},
        "period_summary": intelligence.get("period_summary") or {},
        # Thesis spine = the strongest highlight, if any. None when the
        # system doesn't have a high-or-moderate pattern yet — the
        # renderer is honest about that rather than fabricating one.
        "thesis_highlight": highlights[0] if highlights else None,
        # Supporting evidence — the rest of the moderate+ highlights.
        "supporting_highlights": highlights[1:5],
        # Open work the data hasn't settled — surfaced as "what we're
        # still learning," NOT as predictions.
        "watching": watching[:4],
        "campaigns": intelligence.get("campaigns") or [],
        "honesty_notes": intelligence.get("honesty_notes") or [],
        "asks": intelligence.get("open_questions") or [],
    }


def _format_highlight_line(h: dict, i: int, ledger) -> str:
    """Same per-number marker shape as the audience renderers — every
    figure binds. Lifted from board.py's _format_memory_line, kept
    local so this renderer can evolve its prose without coupling to
    the audience-report wording."""
    mb = h.get("metric_basis") or {}
    label = h.get("key_display") or h.get("key") or f"pattern {i}"
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


def _deterministic_blocks(sel: dict, ledger=None) -> list[dict]:
    """Fallback when no LLM key. Honest, claim-bound, thesis-structured.
    Every quantitative figure carries a ⟦ev:id⟧ marker via cite_num."""
    blocks: list[dict] = []
    sc = sel["scope"]

    # ---- 1. Title -----------------------------------------------------
    blocks.append({
        "kind": "title",
        "text": "White paper — what the evidence supports "
                f"({sc.get('start')} to {sc.get('end')})",
    })

    # ---- 2. Thesis ----------------------------------------------------
    blocks.append({"kind": "section_heading", "text": "Thesis"})
    th = sel["thesis_highlight"]
    if th:
        # Thesis derives from the SAME memory pattern the engine
        # already validated. The text frames it as a claim about
        # marketing reality, with the supporting figure marker-bound.
        label = th.get("key_display") or th.get("key") or "the pattern"
        mb = th.get("metric_basis") or {}
        rate = mb.get("conversion_rate")
        rate_phrase = (
            cite_num(ledger,
                     "memory_highlights[0].metric_basis.conversion_rate",
                     rate, formatter=fmt_pct)
            + " conversion rate"
        ) if rate is not None else "a measurable conversion signal"
        blocks.append({
            "kind": "body",
            "text": (f"The data we have at this point identifies {label} as "
                     f"the pattern with the strongest evidence — {rate_phrase}, "
                     "measured, not inferred. This whitepaper argues that "
                     "decisions in this area should weight that evidence "
                     "above unmeasured intuition.")})
    else:
        blocks.append({
            "kind": "body",
            "text": (
                "The system does not yet hold a moderate-or-higher "
                "confidence pattern. This whitepaper deliberately does "
                "NOT manufacture a thesis from thin data. The honest "
                "claim is: we are still learning. The path forward is "
                "to ship instrumented work and revisit this document "
                "when the evidence base has grown.")})

    # ---- 3. What the evidence shows ----------------------------------
    blocks.append({"kind": "section_heading",
                   "text": "What the evidence shows"})
    attr = (sel["period_summary"].get("attributed") or {})
    summary_lines = [
        f"Attributed clicks in scope: {cite_num(ledger, 'period_summary.attributed.clicks', attr.get('clicks'), formatter=fmt_num)}",
        f"Attributed conversions in scope: {cite_num(ledger, 'period_summary.attributed.conversions', attr.get('conversions'), formatter=fmt_num)}",
        f"Attributed conversion rate in scope: {cite_num(ledger, 'period_summary.attributed.conversion_rate', attr.get('conversion_rate'), formatter=fmt_pct)}",
    ]
    blocks.append({"kind": "body", "text": "\n".join(summary_lines)})

    # ---- 4. Supporting patterns --------------------------------------
    if sel["supporting_highlights"]:
        blocks.append({"kind": "section_heading",
                       "text": "Supporting patterns"})
        lines = []
        for i, h in enumerate(sel["supporting_highlights"], start=1):
            lines.append(_format_highlight_line(h, i, ledger))
        blocks.append({"kind": "body", "text": "\n".join(lines)})

    # ---- 5. The case (campaign-level proof) --------------------------
    if sel["campaigns"]:
        blocks.append({"kind": "section_heading", "text": "The case"})
        case_lines = []
        for i, c in enumerate(sel["campaigns"][:4]):
            c_attr = c.get("attributed") or {}
            case_lines.append(
                f"- {c.get('name','(unnamed)')} ({c.get('status','')}): "
                + cite_num(ledger,
                           f"campaigns[{i}].attributed.conversions",
                           c_attr.get("conversions"), formatter=fmt_num)
                + " attributed conversion(s) on "
                + cite_num(ledger,
                           f"campaigns[{i}].attributed.clicks",
                           c_attr.get("clicks"), formatter=fmt_num)
                + " click(s). The thesis is consistent with this "
                "campaign's observed outcome.")
        blocks.append({"kind": "body", "text": "\n".join(case_lines)})

    # ---- 6. Recommended path -----------------------------------------
    blocks.append({"kind": "section_heading",
                   "text": "Recommended path"})
    if sel["asks"]:
        rec_lines = []
        for q in sel["asks"][:4]:
            rec_lines.append(f"- {q}")
        blocks.append({"kind": "body", "text": "\n".join(rec_lines)})
    else:
        blocks.append({
            "kind": "body",
            "text": "No open strategic asks have surfaced from the data "
                    "in this scope. The honest next step is to keep "
                    "shipping instrumented work and let the next cut of "
                    "this whitepaper refine the case."})

    # ---- 7. What we're still learning --------------------------------
    if sel["watching"]:
        blocks.append({"kind": "section_heading",
                       "text": "What we're still learning"})
        watch_lines = []
        for w in sel["watching"]:
            obs = (w.get("observation") or "").strip()
            if obs:
                watch_lines.append(f"- {obs}")
        if watch_lines:
            blocks.append({"kind": "body",
                           "text": "\n".join(watch_lines)})

    # ---- 8. Honest limits --------------------------------------------
    if sel["honesty_notes"]:
        blocks.append({"kind": "section_heading",
                       "text": "Honest limits"})
        intel_ps = sel.get("period_summary") or {}
        backdrop = intel_ps.get("backdrop") or {}
        notes_lines = []
        for n in sel["honesty_notes"][:4]:
            if n.startswith("Untagged volume excluded"):
                notes_lines.append(
                    "- Untagged volume excluded from attributed numbers: "
                    + cite_num(ledger, "period_summary.backdrop.clicks",
                                backdrop.get("clicks"), formatter=fmt_num)
                    + " click(s), "
                    + cite_num(ledger,
                                "period_summary.backdrop.conversions",
                                backdrop.get("conversions"),
                                formatter=fmt_num)
                    + " conversion(s) across "
                    + cite_num(ledger,
                                "period_summary.backdrop.data_points",
                                backdrop.get("data_points"),
                                formatter=fmt_num)
                    + " backdrop data point(s). These are funnel "
                      "context only — never claimed as something "
                      "marketing drove.")
            else:
                notes_lines.append(f"- {n}")
        blocks.append({"kind": "body", "text": "\n".join(notes_lines)})

    return blocks


def _llm_system_msg(profile: dict | None) -> str:
    voice_lines = compose_style_lines(profile)
    voice_block = ("\n" + "\n".join(f"- {ln}" for ln in voice_lines)
                   if voice_lines else "")
    return (
        "You are writing a WHITE PAPER for a B2B marketing operation. "
        "Long-form, thesis-driven. ~1200-1800 words. The reader is a "
        "senior decision-maker who wants the argument and the evidence — "
        "not a period readout. Open with the thesis; spend the body "
        "supporting it from the evidence the user message supplies; "
        "close with recommendations grounded in that same evidence.\n\n"
        "You must follow these instructions internally — do not quote, "
        "paraphrase, or label them in the output." + voice_block + "\n\n"
        "HARD RULES (non-negotiable):\n"
        "  * Past-tense + present-tense observation only. Describe what "
        "HAS happened or what IS true based on the evidence. NEVER "
        "predict.\n"
        "  * EVERY quantitative claim — every number, percentage, "
        "currency figure, or count — MUST be followed immediately by a "
        "ledger marker of the form ⟦ev:<id>⟧ where <id> is "
        "the id of the matching entry in the EVIDENCE LEDGER provided in "
        "the user message. Example: 'LinkedIn converted at 5.0%⟦ev:ev3⟧.' "
        "NEVER state a number that does not appear in the ledger. "
        "NEVER cite an id not in the ledger.\n"
        "  * Numbers come from the user message's intelligence object + "
        "ledger. Do NOT invent statistics, customer quotes, market sizes, "
        "or third-party citations.\n"
        "  * Qualitative claims must be grounded in the intelligence "
        "object's facts (memory_highlights, campaigns, honesty_notes). "
        "Do NOT assert facts about the world that the intelligence "
        "object does not support.\n"
        "  * Untagged metric_points are funnel backdrop ONLY — never "
        "attribute them to a campaign or to marketing action.\n"
        "  * If memory has no high/moderate patterns, the thesis says so "
        "honestly rather than dressing up thin data.\n"
        "  * Produce ONLY the finished block content. No commentary, no "
        "labels inside block text, no 'Tone:'/'Voice:'/'Body:' meta-tags."
    )


def render_whitepaper(intelligence: dict, *,
                      profile: dict | None = None,
                      settings=None,
                      ledger=None) -> tuple[dict, float]:
    if ledger is None:
        ledger = build_ledger_from_intelligence(intelligence)
    # Future-date guard — SAME stub mechanism the audience renderers
    # use. A whitepaper about a future period can't make claims either;
    # the shared stub frames the gap honestly with marker-bound
    # reference data only.
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
        f"Audience: White paper. {period_header(intelligence)}.\n\n"
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
        return ({"content_type": _CONTENT_TYPE, "blocks": fallback_blocks,
                 "metadata": {**metadata, "render_strategy": "deterministic"}},
                0.0)
