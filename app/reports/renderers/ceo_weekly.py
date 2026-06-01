"""
CEO weekly — signal-dense. ~150-250 words.

Emphasizes 2-3 notable_changes with one-line memory citations where
relevant, one "watch" item if material, one "what's next." Omits raw
funnel tables, full campaign list, comprehensive production accounting.
"""
from __future__ import annotations

from app.reports.evidence import build_ledger_from_intelligence
from app.reports.renderers._common import (
    anti_slop_lines, cite_num, compose_style_lines, fmt_num, fmt_pct,
    future_stub_blocks, is_future_scope, llm_render, period_header,
    schema_example,
)


_CONTENT_TYPE = "report_ceo_weekly"
_AUDIENCE_LABEL = "CEO weekly digest"


def _pick_lead(intelligence: dict, ledger=None) -> str:
    deltas = (intelligence.get("period_summary") or {}).get("deltas") or {}
    convs = deltas.get("conversions")
    if convs and convs.get("pct") is not None and abs(convs["pct"]) >= 0.15:
        pct = convs["pct"]
        return ("Conversions " + cite_num(
            ledger, "period_summary.deltas.conversions.pct", pct,
            formatter=lambda v: f"{'up' if v > 0 else 'down'} {abs(v) * 100:.0f}%")
            + " vs last week.")
    if intelligence.get("memory_highlights"):
        top = intelligence["memory_highlights"][0]
        # Rebuild the observation with markers so the validator can bind
        # every number to a ledger entry. Same facts; marker-aware
        # phrasing.
        mb = top.get("metric_basis") or {}
        label = top.get("key_display") or top.get("key") or "top channel"
        rate = mb.get("conversion_rate")
        if rate is not None:
            return (f"{label}: " + cite_num(
                ledger, "memory_highlights[0].metric_basis.conversion_rate",
                rate, formatter=fmt_pct) + " conversion rate "
                + ("from " + cite_num(
                    ledger, "memory_highlights[0].metric_basis.clicks",
                    mb.get("clicks"), formatter=fmt_num) + " clicks"
                   if mb.get("clicks") else ""))
        return top.get("observation") or "Memory surfaced a top pattern."
    if intelligence.get("campaigns"):
        c = intelligence["campaigns"][0]
        return (f"Campaign {c['name']!r} is {c['status']} — "
                + cite_num(ledger, "campaigns[0].attributed.conversions",
                            c['attributed'].get('conversions'),
                            formatter=fmt_num)
                + " attributed conversion(s) to date.")
    return ("No statistically meaningful movement to call out this "
            "period. The system is watching.")


def _select(intelligence: dict, ledger=None) -> dict:
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
        "lead": _pick_lead(intelligence, ledger=ledger),
        "notable_changes": notable_for_ceo[:3],
        "watching": intelligence.get("watching") or [],
        "open_questions": intelligence.get("open_questions") or [],
        "memory_highlights": intelligence.get("memory_highlights") or [],
        "scope": intelligence.get("scope") or {},
        "honesty_notes": intelligence.get("honesty_notes") or [],
    }


def _deterministic_blocks(sel: dict, ledger=None) -> list[dict]:
    blocks: list[dict] = []
    blocks.append({"kind": "headline", "text": sel["lead"]})
    # Two to three bullets of what changed. notable_changes carry their
    # own delta_pct field which the ledger knows about — we attach a
    # marker at the end of each bullet for binding.
    bullet_lines: list[str] = []
    for i_change, c in enumerate(sel["notable_changes"][:3]):
        obs = c.get("observation", "")
        dp = c.get("delta_pct")
        # Build a marker-aware bullet. If the change has a delta_pct,
        # we embed the marker; otherwise the bullet is a non-numeric
        # observation (e.g. memory_top_highlight which we cite via the
        # memory section instead).
        if dp is not None:
            stage = c.get("stage", "metric")
            # Original observation already mentions a percentage —
            # rewrite to a marker-bound phrasing so the validator sees
            # the binding.
            direction = "up" if dp > 0 else ("down" if dp < 0 else "flat")
            bullet_lines.append(
                f"- {stage.title()} {direction} "
                + cite_num(ledger,
                            f"notable_changes[{i_change}].delta_pct", dp,
                            formatter=lambda v: f"{abs(v) * 100:.0f}%")
                + " vs prior period.")
        elif obs:
            # Non-numeric notable (e.g. campaign attribution rollup) —
            # safe to emit verbatim; it doesn't carry standalone
            # numbers the validator would flag.
            bullet_lines.append(f"- {obs}")
    if not bullet_lines and sel["memory_highlights"]:
        # Fall back to top memory observations as the "what changed."
        # Same marker rewrite as the lead.
        for i, h in enumerate(sel["memory_highlights"][:2]):
            mb = h.get("metric_basis") or {}
            label = h.get("key_display") or h.get("key") or "channel"
            rate = mb.get("conversion_rate")
            if rate is not None:
                bullet_lines.append(
                    f"- {label}: " + cite_num(
                        ledger, f"memory_highlights[{i}].metric_basis.conversion_rate",
                        rate, formatter=fmt_pct) + " conversion rate.")
            elif mb.get("conversions"):
                bullet_lines.append(
                    f"- {label}: " + cite_num(
                        ledger, f"memory_highlights[{i}].metric_basis.conversions",
                        mb.get("conversions"), formatter=fmt_num)
                    + " attributed conversion(s).")
    if not bullet_lines:
        bullet_lines.append(
            "- Nothing crossed the 'notable' threshold this week. "
            "Production continued; attribution data has not yet "
            "concentrated on a specific winner.")
    blocks.append({"kind": "body", "text": "\n".join(bullet_lines)})

    # One watch line — only if there IS something worth watching.
    # The watching observation defers (no rate quoted per Bug 3
    # discipline), so it carries no standalone numbers the validator
    # would flag. Emit verbatim.
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
    voice_lines = compose_style_lines(profile) + anti_slop_lines()
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
        "  * EVERY quantitative claim — every number, percentage, "
        "currency figure, or count — MUST be followed immediately by a "
        "ledger marker ⟦ev:<id>⟧ matching its entry in the EVIDENCE "
        "LEDGER provided in the user message. NEVER state a number not "
        "in the ledger.\n"
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
    sel = _select(intelligence, ledger=ledger)
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
        f"Audience: CEO weekly. {period_header(intelligence)}.\n\n"
        f"Intelligence to render (use these facts; do not invent):\n"
        f"{_json.dumps(sel, indent=2, default=str)}\n\n"
        f"EVIDENCE LEDGER — every quantitative claim MUST cite an id "
        f"from this list via the marker ⟦ev:<id>⟧ immediately after "
        f"the number. NEVER state a number not in this ledger.\n"
        f"{_json.dumps(ledger.prompt_payload(), indent=2, default=str)}\n\n"
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
