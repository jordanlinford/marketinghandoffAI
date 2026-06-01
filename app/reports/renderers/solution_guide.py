"""
Solution Guide — anchor #4. PROBLEM → SOLUTION structure.

A solution guide names a problem from the observed evidence, shows
what the data says about it, lays out the recommended solution, and
backs that solution with evidence. Same renderer input contract,
same ledger, same validator, same severity layer, same future-stub
as every other anchor. Per docs/content-architecture.md Level 3:
this is a new render strategy, not a new pipeline.

Selection mapping for this anchor:
  * open_questions → the problem(s) the guide addresses
  * period_summary → the scale of the problem
  * memory_highlights → what the evidence says about the problem
    domain; doubles as the evidence backing the recommended path
  * campaigns → past attempts that inform the recommended path
  * watching → considerations the guide flags but cannot resolve
  * honesty_notes → what the data cannot tell us about the problem
"""
from __future__ import annotations

from app.reports.evidence import build_ledger_from_intelligence
from app.reports.renderers._common import (
    anti_slop_lines, cite_num, compose_style_lines, fmt_num, fmt_pct,
    future_stub_blocks, is_future_scope, llm_render, period_header,
    schema_example,
)


_CONTENT_TYPE = "solution_guide"
_AUDIENCE_LABEL = "Solution guide"


def _select(intelligence: dict) -> dict:
    highlights = intelligence.get("memory_highlights") or []
    return {
        "scope": intelligence.get("scope") or {},
        "period_summary": intelligence.get("period_summary") or {},
        # Problems the guide addresses.
        "problems": intelligence.get("open_questions") or [],
        # Evidence about the problem domain — same memory the other
        # anchors lean on, here framed as "what the data says about
        # solving this."
        "evidence": highlights[:5],
        # Past attempts (campaigns) inform the recommended path.
        "past_attempts": intelligence.get("campaigns") or [],
        # Implementation considerations — surfaced as "watch fors,"
        # framed as caveats not as predictions.
        "implementation_watch": (intelligence.get("watching") or [])[:4],
        "honesty_notes": intelligence.get("honesty_notes") or [],
    }


def _evidence_line(h: dict, i: int, ledger) -> str:
    mb = h.get("metric_basis") or {}
    label = h.get("key_display") or h.get("key") or f"signal {i}"
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
            convs, formatter=fmt_num) + " conversions")
    if clicks:
        parts.append(" on " + cite_num(
            ledger, f"memory_highlights[{i}].metric_basis.clicks",
            clicks, formatter=fmt_num) + " clicks")
    if dp:
        parts.append(" across " + cite_num(
            ledger, f"memory_highlights[{i}].metric_basis.data_points",
            dp, formatter=fmt_num) + " data points")
    return "".join(parts) + "."


def _past_attempt_line(c: dict, i: int, ledger) -> str:
    c_attr = c.get("attributed") or {}
    return ("- " + (c.get("name") or "(unnamed attempt)")
            + f" ({c.get('status','')}): "
            + cite_num(ledger,
                       f"campaigns[{i}].attributed.conversions",
                       c_attr.get("conversions"), formatter=fmt_num)
            + " attributed conversion(s) on "
            + cite_num(ledger,
                       f"campaigns[{i}].attributed.clicks",
                       c_attr.get("clicks"), formatter=fmt_num)
            + " click(s). Treat as a data point about what has been "
              "tried, not as a prediction about what will work.")


def _deterministic_blocks(sel: dict, ledger=None) -> list[dict]:
    blocks: list[dict] = []
    sc = sel["scope"]
    blocks.append({
        "kind": "title",
        "text": f"Solution guide — the problem and the path "
                f"({sc.get('start')} to {sc.get('end')})"})

    # ---- 1. The problem ---------------------------------------------
    blocks.append({"kind": "section_heading",
                   "text": "The problem"})
    if sel["problems"]:
        problem_lines = [f"- {q}" for q in sel["problems"][:4]]
        blocks.append({"kind": "body",
                       "text": "Open from the evidence:\n"
                               + "\n".join(problem_lines)})
    else:
        blocks.append({
            "kind": "body",
            "text": "No problem has surfaced from the evidence in "
                    "this scope that the system can name with "
                    "confidence. The honest framing is: there isn't a "
                    "named problem yet — ship instrumented work and "
                    "revisit this guide when one emerges."})

    # ---- 2. Scale of the problem ------------------------------------
    attr = (sel["period_summary"].get("attributed") or {})
    blocks.append({"kind": "section_heading",
                   "text": "Scale of the problem"})
    blocks.append({"kind": "body",
                   "text": (
        f"Attributed clicks in scope: {cite_num(ledger, 'period_summary.attributed.clicks', attr.get('clicks'), formatter=fmt_num)}. "
        f"Attributed conversions in scope: {cite_num(ledger, 'period_summary.attributed.conversions', attr.get('conversions'), formatter=fmt_num)}. "
        f"Attributed conversion rate: {cite_num(ledger, 'period_summary.attributed.conversion_rate', attr.get('conversion_rate'), formatter=fmt_pct)}. "
        "These numbers bound how much the problem is worth solving.")})

    # ---- 3. What the evidence says about it -------------------------
    if sel["evidence"]:
        blocks.append({"kind": "section_heading",
                       "text": "What the evidence says about this problem"})
        lines = []
        for i, h in enumerate(sel["evidence"]):
            lines.append(_evidence_line(h, i, ledger))
        blocks.append({"kind": "body", "text": "\n".join(lines)})

    # ---- 4. Recommended path ----------------------------------------
    blocks.append({"kind": "section_heading",
                   "text": "Recommended path"})
    if sel["evidence"]:
        # Recommend the path most aligned with the strongest evidence.
        # Same shape of recommendation discipline as the other anchors:
        # the strongest measured pattern is what the guide leans on.
        top = sel["evidence"][0]
        top_label = top.get("key_display") or top.get("key") or "the strongest signal"
        blocks.append({
            "kind": "body",
            "text": ("Lean the solution into what the evidence "
                     f"already supports. The strongest measured "
                     f"signal is {top_label}; the path that "
                     "exercises that signal is the lower-risk "
                     "starting point. Iterate from there based on "
                     "instrumented outcomes, NOT on what feels "
                     "intuitively right.")})
    else:
        blocks.append({
            "kind": "body",
            "text": "There is not enough evidence yet to recommend a "
                    "specific path. The honest next step is to ship "
                    "instrumented small bets and revisit this guide "
                    "as the data accumulates."})

    # ---- 5. Past attempts (what informs the path) -------------------
    if sel["past_attempts"]:
        blocks.append({"kind": "section_heading",
                       "text": "Past attempts on the same problem"})
        attempt_lines = []
        for i, c in enumerate(sel["past_attempts"][:4]):
            attempt_lines.append(_past_attempt_line(c, i, ledger))
        blocks.append({"kind": "body", "text": "\n".join(attempt_lines)})

    # ---- 6. Implementation considerations ---------------------------
    if sel["implementation_watch"]:
        blocks.append({"kind": "section_heading",
                       "text": "Implementation considerations"})
        watch_lines = []
        for w in sel["implementation_watch"]:
            obs = (w.get("observation") or "").strip()
            if obs:
                watch_lines.append(f"- {obs}")
        if watch_lines:
            blocks.append({"kind": "body", "text": "\n".join(watch_lines)})

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
                      "context only — never claimed as part of the "
                      "solution's measured effect.")
            else:
                notes_lines.append(f"- {n}")
        blocks.append({"kind": "body", "text": "\n".join(notes_lines)})
    return blocks


def _llm_system_msg(profile: dict | None) -> str:
    voice_lines = compose_style_lines(profile) + anti_slop_lines()
    voice_block = ("\n" + "\n".join(f"- {ln}" for ln in voice_lines)
                   if voice_lines else "")
    return (
        "You are writing a SOLUTION GUIDE for a B2B marketing operation. "
        "Problem → solution structure. ~1000-1500 words. The reader is "
        "solving a specific problem; your job is to frame the problem "
        "from observed evidence, explain why it's the right problem to "
        "solve now, and lay out the recommended path with supporting "
        "evidence. Not a period readout. Not a thesis. Not a comparison.\n\n"
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
        "user message. Example: 'The funnel converts at 1.2%⟦ev:ev3⟧.' "
        "NEVER state a number that does not appear in the ledger. "
        "NEVER cite an id not in the ledger.\n"
        "  * The problem comes from the intelligence object's "
        "open_questions; do NOT invent additional problems.\n"
        "  * The recommended path leans on the strongest measured "
        "evidence; do NOT propose a path the evidence does not support.\n"
        "  * Untagged metric_points are funnel backdrop ONLY — never "
        "claimed as part of the solution's measured effect.\n"
        "  * If evidence is thin, the guide defers the recommendation "
        "rather than dressing up thin data.\n"
        "  * Produce ONLY the finished block content. No commentary, no "
        "labels inside block text, no 'Tone:'/'Voice:'/'Body:' meta-tags."
    )


def render_solution_guide(intelligence: dict, *,
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
        f"Audience: Solution guide. {period_header(intelligence)}.\n\n"
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
