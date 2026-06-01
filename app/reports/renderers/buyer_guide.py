"""
Buyer's Guide — anchor #3. COMPARATIVE / decision-oriented.

A buyer's guide helps a reader compare options against decision
criteria. Selection here re-cuts the SAME intelligence object to
read as "criteria × options × evidence," NOT as a period readout
(reports) or a thesis (whitepaper). Same renderer input contract,
same ledger, same validator, same severity layer, same future-stub.
Per docs/content-architecture.md Level 3: anchors are render
strategies, not new pipelines.

Selection mapping for this anchor:
  * memory_highlights → criteria-evidence pairs (each highlight is
    "criterion X performed Y in the data")
  * campaigns → options whose attributed outcome shows how they
    perform against criteria
  * watching → criteria the evidence has not yet settled
  * open_questions → the decision the reader is being asked to make
  * honesty_notes → what the data cannot tell us
"""
from __future__ import annotations

from app.reports.evidence import build_ledger_from_intelligence
from app.reports.renderers._common import (
    cite_num, compose_style_lines, fmt_num, fmt_pct, future_stub_blocks,
    is_future_scope, llm_render, period_header, schema_example,
)


_CONTENT_TYPE = "buyer_guide"
_AUDIENCE_LABEL = "Buyer's guide"


def _select(intelligence: dict) -> dict:
    highlights = intelligence.get("memory_highlights") or []
    return {
        "scope": intelligence.get("scope") or {},
        "period_summary": intelligence.get("period_summary") or {},
        # Criteria are the things the buyer is deciding on. Each
        # criterion is grounded in a memory_highlight (so it has
        # ledger-bound evidence).
        "criteria": highlights[:5],
        # Options being compared = campaigns. Each carries attributed
        # outcome we can cite.
        "options": intelligence.get("campaigns") or [],
        # Criteria the data hasn't settled yet — surfaced as "still
        # uncertain" rather than dropped.
        "uncertain_criteria": (intelligence.get("watching") or [])[:4],
        # The decision being made.
        "decision_asks": intelligence.get("open_questions") or [],
        "honesty_notes": intelligence.get("honesty_notes") or [],
    }


def _criterion_line(h: dict, i: int, ledger) -> str:
    """Each criterion line is the criterion + the measured evidence
    that supports it, with per-number markers. Same shape rule as
    every other anchor: numbers carry their ledger marker."""
    mb = h.get("metric_basis") or {}
    label = h.get("key_display") or h.get("key") or f"criterion {i}"
    rate = mb.get("conversion_rate")
    convs = mb.get("conversions")
    clicks = mb.get("clicks")
    dp = mb.get("data_points")
    parts = [f"- {label}: "]
    if rate is not None:
        parts.append("measured at " + cite_num(
            ledger, f"memory_highlights[{i}].metric_basis.conversion_rate",
            rate, formatter=fmt_pct) + " conversion rate")
    elif convs:
        parts.append(cite_num(
            ledger, f"memory_highlights[{i}].metric_basis.conversions",
            convs, formatter=fmt_num) + " conversions observed")
    if clicks:
        parts.append("on " + cite_num(
            ledger, f"memory_highlights[{i}].metric_basis.clicks",
            clicks, formatter=fmt_num) + " clicks")
    if dp:
        parts.append("across " + cite_num(
            ledger, f"memory_highlights[{i}].metric_basis.data_points",
            dp, formatter=fmt_num) + " data points")
    return "".join(parts) + "."


def _option_line(c: dict, i: int, ledger) -> str:
    c_attr = c.get("attributed") or {}
    return ("- " + (c.get("name") or "(unnamed option)")
            + f" ({c.get('status','')}): "
            + cite_num(ledger,
                       f"campaigns[{i}].attributed.conversions",
                       c_attr.get("conversions"), formatter=fmt_num)
            + " attributed conversion(s) on "
            + cite_num(ledger,
                       f"campaigns[{i}].attributed.clicks",
                       c_attr.get("clicks"), formatter=fmt_num)
            + " click(s). This is the observed outcome — the buyer "
              "weighs it against the criteria above.")


def _deterministic_blocks(sel: dict, ledger=None) -> list[dict]:
    blocks: list[dict] = []
    sc = sel["scope"]
    blocks.append({
        "kind": "title",
        "text": f"Buyer's guide — evaluating the options "
                f"({sc.get('start')} to {sc.get('end')})"})

    # ---- 1. Framing the decision ------------------------------------
    blocks.append({"kind": "section_heading",
                   "text": "The decision in front of you"})
    if sel["decision_asks"]:
        ask_lines = [f"- {q}" for q in sel["decision_asks"][:4]]
        blocks.append({
            "kind": "body",
            "text": ("This guide compares the options available "
                     "against criteria the evidence already has "
                     "something to say about. The open decisions:\n"
                     + "\n".join(ask_lines))})
    else:
        blocks.append({
            "kind": "body",
            "text": "This guide compares the options the data has "
                    "observed against criteria the evidence already "
                    "has something to say about."})

    # ---- 2. Decision criteria ---------------------------------------
    blocks.append({"kind": "section_heading",
                   "text": "Decision criteria (and what the evidence says)"})
    if sel["criteria"]:
        lines = []
        for i, h in enumerate(sel["criteria"]):
            lines.append(_criterion_line(h, i, ledger))
        blocks.append({"kind": "body", "text": "\n".join(lines)})
    else:
        blocks.append({
            "kind": "body",
            "text": "The system has not yet identified moderate-or-"
                    "higher-confidence criteria from the data. The "
                    "honest answer is: pick criteria the buyer cares "
                    "about, instrument them, and revisit this guide "
                    "once the evidence has accumulated."})

    # ---- 3. Period context ------------------------------------------
    attr = (sel["period_summary"].get("attributed") or {})
    blocks.append({"kind": "section_heading",
                   "text": "Scale of the decision (period context)"})
    blocks.append({"kind": "body",
                   "text": (
        f"Attributed clicks in scope: {cite_num(ledger, 'period_summary.attributed.clicks', attr.get('clicks'), formatter=fmt_num)}. "
        f"Attributed conversions in scope: {cite_num(ledger, 'period_summary.attributed.conversions', attr.get('conversions'), formatter=fmt_num)}. "
        f"Attributed conversion rate: {cite_num(ledger, 'period_summary.attributed.conversion_rate', attr.get('conversion_rate'), formatter=fmt_pct)}. "
        "These numbers bound the scale at which the decision matters.")})

    # ---- 4. Options compared ----------------------------------------
    if sel["options"]:
        blocks.append({"kind": "section_heading",
                       "text": "Options compared against the criteria"})
        lines = []
        for i, c in enumerate(sel["options"][:4]):
            lines.append(_option_line(c, i, ledger))
        blocks.append({"kind": "body", "text": "\n".join(lines)})

    # ---- 5. Criteria still uncertain --------------------------------
    if sel["uncertain_criteria"]:
        blocks.append({"kind": "section_heading",
                       "text": "Criteria the evidence hasn't settled yet"})
        ulines = []
        for w in sel["uncertain_criteria"]:
            obs = (w.get("observation") or "").strip()
            if obs:
                ulines.append(f"- {obs}")
        if ulines:
            blocks.append({"kind": "body", "text": "\n".join(ulines)})

    # ---- 6. Recommended choice + tradeoffs --------------------------
    blocks.append({"kind": "section_heading",
                   "text": "Recommended path"})
    if sel["criteria"] and sel["options"]:
        # Recommend the option that aligns with the strongest measured
        # criterion. This is honest because the criterion IS the
        # strongest evidence the system has.
        top = sel["criteria"][0]
        top_label = top.get("key_display") or top.get("key") or "the strongest pattern"
        blocks.append({
            "kind": "body",
            "text": (f"Weight options by how well they exercise {top_label} — "
                     "the criterion the evidence supports most strongly. "
                     "Options whose attributed outcome lines up with that "
                     "criterion are the lower-risk choice; options that "
                     "ignore it are the speculative choice.")})
    else:
        blocks.append({
            "kind": "body",
            "text": "There is not enough evidence yet to recommend an "
                    "option. The honest next step is to instrument the "
                    "candidate options and revisit this guide."})

    # ---- 7. Honest limits -------------------------------------------
    if sel["honesty_notes"]:
        blocks.append({"kind": "section_heading",
                       "text": "What this guide cannot tell you"})
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
                                backdrop.get("conversions"), formatter=fmt_num)
                    + " conversion(s) across "
                    + cite_num(ledger,
                                "period_summary.backdrop.data_points",
                                backdrop.get("data_points"), formatter=fmt_num)
                    + " backdrop data point(s). These are funnel "
                      "context only — never weighed as evidence for "
                      "an option.")
            else:
                notes_lines.append(f"- {n}")
        blocks.append({"kind": "body", "text": "\n".join(notes_lines)})
    return blocks


def _llm_system_msg(profile: dict | None) -> str:
    voice_lines = compose_style_lines(profile)
    voice_block = ("\n" + "\n".join(f"- {ln}" for ln in voice_lines)
                   if voice_lines else "")
    return (
        "You are writing a BUYER'S GUIDE for a B2B marketing operation. "
        "Comparative, decision-oriented. ~1000-1500 words. The reader is "
        "evaluating options against decision criteria; your job is to "
        "lay out the criteria, evaluate each option against them, and "
        "recommend the choice the evidence supports. Not a period "
        "readout. Not a thesis.\n\n"
        "You must follow these instructions internally — do not quote, "
        "paraphrase, or label them in the output." + voice_block + "\n\n"
        "HARD RULES (non-negotiable):\n"
        "  * Past-tense + present-tense observation only. Describe what "
        "HAS happened or what IS true based on the evidence. NEVER "
        "predict.\n"
        "  * EVERY quantitative claim — every number, percentage, "
        "currency figure, or count — MUST be followed immediately by a "
        "ledger marker of the form ⟦ev:<id>⟧ where <id> is the id of "
        "the matching entry in the EVIDENCE LEDGER provided in the "
        "user message. Example: 'Option A converted at 5.0%⟦ev:ev3⟧.' "
        "NEVER state a number that does not appear in the ledger. "
        "NEVER cite an id not in the ledger.\n"
        "  * Compare options against criteria; the criteria themselves "
        "come from the intelligence object's memory_highlights. Do NOT "
        "invent additional criteria, options, or competitor names.\n"
        "  * Untagged metric_points are funnel backdrop ONLY — never "
        "weighed as evidence for an option.\n"
        "  * If criteria are thin, the guide says so honestly and "
        "defers the recommendation rather than dressing up thin data.\n"
        "  * Produce ONLY the finished block content. No commentary, no "
        "labels inside block text, no 'Tone:'/'Voice:'/'Body:' meta-tags."
    )


def render_buyer_guide(intelligence: dict, *,
                       profile: dict | None = None,
                       settings=None,
                       ledger=None) -> tuple[dict, float]:
    if ledger is None:
        ledger = build_ledger_from_intelligence(intelligence)
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
        f"Audience: Buyer's guide. {period_header(intelligence)}.\n\n"
        f"Intelligence to render (use these facts; do not invent):\n"
        f"{_json.dumps(sel, indent=2, default=str)}\n\n"
        f"EVIDENCE LEDGER — every quantitative claim MUST cite an id "
        f"from this list via the marker ⟦ev:<id>⟧ immediately after "
        f"the number. NEVER state a number not in this ledger.\n"
        f"{_json.dumps(ledger.prompt_payload(), indent=2, default=str)}\n\n"
        "Return ONLY a JSON object matching this exact shape (no prose, "
        "no markdown fences). Each block's `text` is finished prose — "
        "replace the placeholder description with real content. Keep "
        "the block order; you may add up to two extra `body` blocks "
        "if a section needs more narrative.\n\n"
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
