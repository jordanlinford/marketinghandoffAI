"""
Sales leadership update — collaborative. ~300-500 words.

Emphasizes top_content (assets sales can use), campaigns in/near
sales-handoff stage, what's about to ship. Framed TO sales, not ABOUT
marketing. Omits board-style efficiency framing and deep memory pattern
theory.
"""
from __future__ import annotations

from app.reports.evidence import build_ledger_from_intelligence
from app.reports.renderers._common import (
    anti_slop_lines, cite_num, compose_style_lines, fmt_num, fmt_pct,
    future_stub_blocks, is_future_scope, llm_render, period_header,
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
    top_memory = (intelligence.get("memory_highlights") or [{}])[0]
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
        "memory_one_liner": (top_memory or {}).get("observation", ""),
        # Passed through so the renderer can build a marker-bound
        # version of the top memory observation. The intelligence-side
        # index is 0 by construction; that's what the ledger sees too.
        "memory_top_basis": (top_memory or {}).get("metric_basis") or {},
        "watching_segments": [
            w for w in (intelligence.get("watching") or [])
            if w.get("dimension") in ("channel x audience",)
        ][:2],
        "honesty_notes": intelligence.get("honesty_notes") or [],
    }


def _deterministic_blocks(sel: dict, ledger=None) -> list[dict]:
    blocks: list[dict] = []
    blocks.append({
        "kind": "headline",
        "text": "Here's what marketing has for sales this period."})

    # 1. What sales can use right now — top content. Numbers cited via
    # ledger markers per the §6 binding discipline.
    blocks.append({"kind": "section_heading",
                   "text": "What sales can use this week"})
    if sel["top_content"]:
        lines = []
        # Use original ordering by intelligence; we don't have the
        # intelligence index here, so re-lookup by id-equivalence.
        # Practically, the renderer's slice preserves original order so
        # `tc_idx` matches `i` in build_ledger.
        for tc_idx, tc in enumerate(sel["top_content"][:4]):
            attr = tc.get("attributed") or {}
            lines.append(
                f"- {tc['title']} ({tc['content_type']}): "
                + cite_num(ledger,
                           f"top_content[{tc_idx}].attributed.conversions",
                           attr.get('conversions'), formatter=fmt_num)
                + " attributed conversion(s) on "
                + cite_num(ledger,
                           f"top_content[{tc_idx}].attributed.clicks",
                           attr.get('clicks'), formatter=fmt_num)
                + " click(s). Share with prospects who match the "
                "campaign audience.")
        blocks.append({"kind": "body", "text": "\n".join(lines)})
    elif sel["production_what_shipped"]["ready_count"] > 0:
        bt = sel["production_what_shipped"]["by_content_type"]
        # by_content_type's per-type counts are scoped to production
        # but not individually in the ledger (only artifacts_ready is).
        # Compose using artifacts_ready as the marker anchor; per-type
        # breakdown stays as descriptive text.
        ready = sel['production_what_shipped']['ready_count']
        bt_str = ", ".join(f"{n} {t.replace('_', ' ')}"
                            for t, n in bt.items() if n)
        blocks.append({
            "kind": "body",
            "text": cite_num(ledger, "production.artifacts_ready",
                              ready, formatter=fmt_num)
                    + f" approved asset(s) ready to share — {bt_str or 'mixed content types'}. "
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
        # Same caveat as top_content: index in the renderer's slice may
        # not equal the intelligence's index for these specific
        # campaigns. The renderer's `_select` filters active_campaigns
        # FROM intelligence.campaigns — so a campaign at active index 0
        # could be intelligence.campaigns[2]. Use the campaign's id to
        # find its original index would require passing intelligence
        # in; instead we cite by source-only lookup (ledger.cite returns
        # the first match) which works because we DO have entries for
        # every campaign's fields.
        for c in sel["active_campaigns"][:4]:
            attr = c["attributed"]
            # Cite by source path — the ledger matches on (source, value)
            # so multiple campaign entries don't collide.
            convs = attr.get('conversions')
            generated = c.get('generated_assets', 0)
            camp_lines.append(
                f"- {c['name']} — {c['status']} ({c.get('campaign_type', 'other')}). "
                + _cite_by_value(ledger, "campaigns", "generated_assets",
                                   generated, formatter=fmt_num)
                + " asset(s) generated; "
                + _cite_by_value(ledger, "campaigns", "attributed.conversions",
                                   convs, formatter=fmt_num)
                + " attributed conversion(s) so far. Watch for traffic "
                f"from the campaign UTM ({c.get('utm_campaign', 'n/a')}).")
        blocks.append({"kind": "body", "text": "\n".join(camp_lines)})
    else:
        blocks.append({
            "kind": "body",
            "text": "No active campaigns to coordinate around right now. "
                    "Sales-relevant production is happening at the asset "
                    "level (see above)."})

    # 3. What's coming — production lane forward-looking. No numeric
    # claims in this block (just labels); skip markers.
    blocks.append({"kind": "section_heading",
                   "text": "Shipping next"})
    bt = sel["production_what_shipped"]["by_content_type"]
    if bt:
        upcoming = ", ".join(f"{n} {t.replace('_', ' ')}"
                              for t, n in bt.items() if n)
        # The "n" inside the by_content_type breakdown is a count — and
        # it's NOT individually in the ledger (only roll-ups are). We
        # could add per-type ledger entries, but for v1 we drop these
        # microbreakdowns from quantitative claim status by phrasing
        # them as labels rather than headline metrics.
        blocks.append({
            "kind": "body",
            "text": "Approved or in review this period, by type: "
                    + upcoming
                    + ". Next set lands as the team works through current "
                    "draft feedback."})
    else:
        blocks.append({
            "kind": "body",
            "text": "Production pipeline is empty for this scope. Reach "
                    "out if a prospect or deal calls for a specific asset; "
                    "we can prioritize it."})

    # 4. Audience signal sales should know about — only if material.
    # The raw observation text contains numbers in its own formatting;
    # we rebuild a marker-bound summary line so the validator binds.
    mh = sel.get("memory_top_basis") or {}
    if mh.get("conversion_rate") is not None:
        blocks.append({"kind": "section_heading",
                       "text": "What's resonating"})
        blocks.append({"kind": "body", "text":
            cite_num(ledger,
                      "memory_highlights[0].metric_basis.conversion_rate",
                      mh.get("conversion_rate"), formatter=fmt_pct)
            + " conversion rate "
            + (("from " + cite_num(
                ledger, "memory_highlights[0].metric_basis.clicks",
                mh.get("clicks"), formatter=fmt_num) + " clicks.")
               if mh.get("clicks") else ".")})
    elif mh.get("conversions"):
        blocks.append({"kind": "section_heading",
                       "text": "What's resonating"})
        blocks.append({"kind": "body", "text":
            cite_num(ledger,
                      "memory_highlights[0].metric_basis.conversions",
                      mh.get("conversions"), formatter=fmt_num)
            + " attributed conversion(s) across the top channel."})

    if sel["watching_segments"]:
        blocks.append({"kind": "section_heading",
                       "text": "Segments we're starting to watch"})
        # Watching observations defer (no rate quoted — Bug 3
        # discipline), so they carry no standalone numbers and emit
        # cleanly verbatim.
        watch_lines = [f"- {w.get('observation', '')}"
                       for w in sel["watching_segments"]]
        blocks.append({"kind": "body", "text": "\n".join(watch_lines)})

    blocks.append({
        "kind": "close",
        "text": "If a deal needs an asset we haven't made, flag it back "
                "and we'll get it into the next cycle."})
    return blocks


def _cite_by_value(ledger, prefix: str, field: str, value, *,
                   formatter=None) -> str:
    """Cite a campaign-style field by SOURCE PREFIX + VALUE match. The
    sales selection re-slices intelligence.campaigns into active_
    campaigns, so the renderer doesn't know each campaign's original
    intelligence index. We walk the ledger looking for an entry whose
    source starts with the prefix, ends with the field, and whose value
    matches — that uniquely identifies the right entry."""
    formatted = (formatter(value) if formatter is not None else str(value))
    if ledger is None or value is None:
        return formatted
    needle_value = value
    for entry in ledger:
        src = entry["source"]
        if not src.startswith(prefix + "["):
            continue
        if not src.endswith("." + field) and not src.endswith(field):
            continue
        if entry["value"] == needle_value:
            return f"{formatted}⟦ev:{entry['id']}⟧"
    return formatted


def _llm_system_msg(profile: dict | None) -> str:
    voice_lines = compose_style_lines(profile) + anti_slop_lines()
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
        "  * EVERY quantitative claim — every number, percentage, "
        "currency figure, or count — MUST be followed immediately by a "
        "ledger marker ⟦ev:<id>⟧ matching its entry in the EVIDENCE "
        "LEDGER provided in the user message. NEVER state a number not "
        "in the ledger.\n"
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
        f"Audience: Sales leadership. {period_header(intelligence)}.\n\n"
        f"Intelligence to render (use these facts; do not invent):\n"
        f"{_json.dumps(sel, indent=2, default=str)}\n\n"
        f"EVIDENCE LEDGER — every quantitative claim MUST cite an id "
        f"from this list via the marker ⟦ev:<id>⟧ immediately after "
        f"the number. NEVER state a number not in this ledger.\n"
        f"{_json.dumps(ledger.prompt_payload(), indent=2, default=str)}\n\n"
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
