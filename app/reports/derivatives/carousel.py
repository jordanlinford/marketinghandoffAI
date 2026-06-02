"""
Carousel — Level-4 derivative #2. SAME §7-bound path as exec_summary;
the only difference is the OUTPUT SHAPE — slides instead of prose.

CONTENT-ONLY at this layer. The visual render (Pillow → PNGs) lives
in carousel_visual.py and runs from the agent AFTER containment passes.
Splitting the two so a failure can be localized: a Stage-1 bug shows up
in the content/trust path; a Stage-2 bug shows up in the pixel
pipeline. Same load-bearing principle as the brief: prove the §7
mechanism on the structured data BEFORE rendering it visually.

§7 contract (held identically to exec_summary):
  * Input = anchor artifact dict. The renderer reads body.content
    blocks + body.evidence_ledger. It NEVER reaches past the anchor
    to the engine, the ledger builder, or raw sources.
  * Output = content with content_type='carousel' and blocks where
    EACH BLOCK IS ONE SLIDE (kind='slide'). The block's text carries
    title + body separated by a newline; every quantitative claim
    carries an INHERITED ⟦ev:id⟧ marker pointing at an entry in the
    source anchor's ledger.
  * Validation = the existing validate_containment function. Same
    trust_checks shape, same severity classifier, same gate.

The slide arc maps onto the anchor's structure:
  - hook      → opening one-liner (no number; sets the question)
  - finding   → the strongest measured claim from the anchor
  - insight   → a supporting measured claim (rate, magnitude, etc.)
  - rec       → the recommendation the evidence supports
  - cta       → next-step call (no number; lives outside the claim
                set so it doesn't need to bind)

Hook + CTA carry no numeric claim, so they're trivially containment-
clean. Finding / insight / rec carry inherited markers — the same
discipline exec_summary's body uses.
"""
from __future__ import annotations

import json
from typing import Any

from app.reports.evidence import _find_markers, _find_numbers
from app.reports.renderers._common import (anti_slop_lines, llm_render,
                                            parse_json_envelope)


_CONTENT_TYPE = "carousel"
_AUDIENCE_LABEL = "Carousel"

# How many anchor claims to lift into slides. A carousel that runs
# 5-7 slides reads cleanly in any feed; more becomes filler. The hook +
# CTA add 2 slides outside the claim set, so 3-4 claim slides is the
# sweet spot.
_MAX_CLAIM_SLIDES = 4


def _select_claims(anchor_content: dict,
                   anchor_ledger_entries: list[dict],
                   max_claims: int = _MAX_CLAIM_SLIDES) -> list[dict]:
    """Walk the anchor's blocks, pair each number with its nearest
    following marker, return up to max_claims grounded claim dicts.
    Same selection shape exec_summary uses — both derivatives lift
    from the SAME validated layer."""
    blocks = (anchor_content or {}).get("blocks") or []
    by_id = {e["id"]: e for e in (anchor_ledger_entries or [])
             if isinstance(e, dict) and "id" in e}
    out: list[dict] = []
    for block_idx, block in enumerate(blocks):
        text = (block or {}).get("text") or ""
        markers = _find_markers(text)
        numbers = _find_numbers(text)
        for n in numbers:
            for m in markers:
                if m["start"] < n["end"]:
                    continue
                if m["start"] - n["end"] > 200:
                    continue
                entry = by_id.get(m["id"])
                if not entry:
                    break
                out.append({
                    "number_text": n["text"],
                    "marker_id": m["id"],
                    "entry_label": entry.get("label") or m["id"],
                    "confidence": entry.get("confidence") or "n_a",
                    "baseline_vs_attributed": entry.get(
                        "baseline_vs_attributed") or "n_a",
                })
                break
        if len(out) >= max_claims:
            break
    return out[:max_claims]


def _slide_block(slot: str, title: str, body: str) -> dict:
    """Build one slide block. The slide's text is title + newline +
    body so the EXISTING containment validator sees both lines without
    a new code path. The `slot` field is metadata the visual renderer
    reads to pick a template; it is NOT scanned for markers (so it
    doesn't accidentally pull validation onto a label string)."""
    return {
        "kind": "slide",
        "slot": slot,
        "text": f"{title}\n{body}" if body else title,
    }


def _anchor_kind_noun(art_type: str | None) -> str:
    return {
        "report_draft":         "report",
        "whitepaper_draft":     "whitepaper",
        "buyer_guide_draft":    "buyer's guide",
        "solution_guide_draft": "solution guide",
    }.get(art_type or "", "anchor")


def _deterministic_blocks(source_anchor: dict,
                          claims: list[dict]) -> list[dict]:
    """Deterministic slide deck. Every quantitative claim carries an
    inherited marker; the hook + CTA carry no numbers (so they need
    no markers). Containment-clean by construction."""
    anchor_title = source_anchor.get("title") or "(untitled anchor)"
    anchor_noun = _anchor_kind_noun(source_anchor.get("type"))
    blocks: list[dict] = []

    # ---- Slide 1: Hook (no numbers) ---------------------------------
    blocks.append(_slide_block(
        slot="hook",
        title="What the data is telling us",
        body=f"A short read from our {anchor_noun} on what the "
             "evidence supports right now."))

    # ---- Slide 2: Headline finding (lifts claims[0]) ---------------
    if claims:
        top = claims[0]
        blocks.append(_slide_block(
            slot="finding",
            title=f"{top['entry_label']}",
            body=f"Measured at {top['number_text']}⟦ev:{top['marker_id']}⟧."))
    else:
        # No claims to lift — the carousel says so honestly rather
        # than dressing up empty space. Same §6 Principle: never
        # manufacture a finding to fill the slot.
        blocks.append(_slide_block(
            slot="finding",
            title="What we measured",
            body=f"The underlying {anchor_noun} has no measured "
                 "claims at this confidence yet. We'll revisit when "
                 "the evidence grows."))

    # ---- Slides 3-5: Supporting claims (claims[1:4]) ---------------
    for c in claims[1:_MAX_CLAIM_SLIDES]:
        blocks.append(_slide_block(
            slot="insight",
            title=c["entry_label"],
            body=f"At {c['number_text']}⟦ev:{c['marker_id']}⟧ in scope."))

    # ---- Recommendation (no number — implication of the data) -----
    if claims:
        top_label = claims[0]["entry_label"]
        blocks.append(_slide_block(
            slot="rec",
            title="Where to lean",
            body=f"Weight the next move toward {top_label} — the "
                 "signal the evidence supports most strongly."))
    else:
        blocks.append(_slide_block(
            slot="rec",
            title="Where to lean",
            body="Ship instrumented work and let the next pass of "
                 "this anchor refine the recommendation."))

    # ---- CTA (no number) -------------------------------------------
    blocks.append(_slide_block(
        slot="cta",
        title="Want the full picture?",
        body=f"See the source {anchor_noun} for the underlying "
             "evidence and the per-claim citations."))

    return blocks


def _llm_system_msg() -> str:
    slop_block = "\n" + "\n".join(f"- {ln}" for ln in anti_slop_lines())
    return (
        "You are writing a SHORT SOCIAL CAROUSEL (5-7 slides) "
        "derived from an existing anchor asset (a report, whitepaper, "
        "buyer's guide, or solution guide). Each slide is "
        "scannable — one idea per slide, no walls of text.\n\n"
        "You must follow these instructions internally — do not "
        "quote, paraphrase, or label them in the output."
        + slop_block + "\n\n"
        "HARD RULES (non-negotiable):\n"
        "  * You are DERIVING from the source anchor, NOT regenerating. "
        "Every claim in your output must already exist in the source's "
        "content + evidence ledger.\n"
        "  * EVERY quantitative claim (number, percentage, currency, "
        "count) MUST be followed immediately by an INHERITED marker "
        "of the form ⟦ev:<id>⟧ where <id> is the id of the matching "
        "entry in the SOURCE ANCHOR's evidence ledger. NEVER mint a "
        "new id. NEVER cite an id not in the source ledger.\n"
        "  * Do NOT introduce a statistic, customer quote, market "
        "claim, or assertion that does not appear in the source "
        "anchor. If you can't find it in the source, the slide can't "
        "say it.\n"
        "  * Past-tense or present-tense observation only. NEVER "
        "predict.\n"
        "  * Hook and CTA slides carry NO numbers — they frame and "
        "invite. The numeric claims live in the finding / insight "
        "slides where each cites a ledger entry.\n"
        "  * Each slide is short — title (≤8 words) plus one line of "
        "body (≤25 words). If you can't say it in that space, the "
        "slide is the wrong granularity, not the wrong length.\n"
        "  * Produce ONLY the finished slide content. No commentary, "
        "no labels inside slide text, no meta-tags."
    )


def _schema_example(fallback_blocks: list[dict]) -> dict:
    out_blocks = []
    for b in fallback_blocks:
        out_blocks.append({
            "kind": "slide",
            "slot": b.get("slot", "insight"),
            "text": "<slide title (≤8 words)>\\n<one line of body "
                    "(≤25 words); every number carries ⟦ev:id⟧>",
        })
    return {"content_type": _CONTENT_TYPE, "blocks": out_blocks,
            "metadata": {}}


def render_carousel(source_anchor: dict, *,
                    settings: Any = None) -> tuple[dict, float]:
    """Render a carousel derivative from a source anchor.

    Same input contract as render_exec_summary; same return shape
    (content_dict, cost_usd). Containment validation is the agent's
    responsibility — this renderer's job is the safe-by-construction
    emission of slides whose every marker resolves in the source
    anchor's ledger.
    """
    body = source_anchor.get("body") or {}
    anchor_content = body.get("content") or {}
    anchor_ledger_entries = body.get("evidence_ledger") or []
    claims = _select_claims(anchor_content, anchor_ledger_entries)
    fallback_blocks = _deterministic_blocks(source_anchor, claims)
    metadata = {
        "audience": _AUDIENCE_LABEL,
        "block_count": len(fallback_blocks),
        "source_anchor_id": source_anchor.get("id"),
        "source_anchor_title": source_anchor.get("title"),
        "source_anchor_type": source_anchor.get("type"),
    }
    if not (settings and getattr(settings, "anthropic_api_key", "")):
        return ({"content_type": _CONTENT_TYPE,
                 "blocks": fallback_blocks,
                 "metadata": {**metadata,
                               "render_strategy": "deterministic"}},
                0.0)
    system_msg = _llm_system_msg()
    schema = _schema_example(fallback_blocks)
    user_msg = (
        f"Source anchor type: {source_anchor.get('type','')}\n"
        f"Source anchor title: {source_anchor.get('title','')}\n\n"
        "Claims selected from the source anchor (use these — do "
        "not invent additional claims):\n"
        f"{json.dumps(claims, indent=2, default=str)}\n\n"
        "SOURCE ANCHOR EVIDENCE LEDGER — every quantitative claim "
        "in your output MUST cite an id from this list via the "
        "marker ⟦ev:<id>⟧. NEVER cite an id not in this ledger.\n"
        f"{json.dumps(anchor_ledger_entries, indent=2, default=str)}\n\n"
        "Return ONLY a JSON object matching this exact shape (no "
        "prose, no markdown fences). Each block represents ONE "
        "SLIDE — kind='slide', a `slot` value of "
        "hook/finding/insight/rec/cta, and `text` carrying the "
        "slide content with title and body separated by a newline.\n\n"
        f"{json.dumps(schema, indent=2)}"
    )
    try:
        content, cost = llm_render(
            audience_label=_AUDIENCE_LABEL,
            system_msg=system_msg, user_msg=user_msg,
            settings=settings, fallback_blocks=fallback_blocks,
            content_type=_CONTENT_TYPE)
        # The shared llm_render strips the `slot` field if the model
        # omitted it; restore it from the fallback so the visual
        # renderer always sees a template signal. Defensive: if the
        # model returned more slides than the fallback, fall back
        # to "insight" for the extras (a sensible default template).
        out_blocks = content.get("blocks") or []
        for i, b in enumerate(out_blocks):
            if not b.get("slot"):
                b["slot"] = (
                    fallback_blocks[i].get("slot")
                    if i < len(fallback_blocks) else "insight"
                )
        content["metadata"] = {**content.get("metadata", {}), **metadata}
        return content, cost
    except Exception:
        return ({"content_type": _CONTENT_TYPE,
                 "blocks": fallback_blocks,
                 "metadata": {**metadata,
                               "render_strategy": "deterministic"}},
                0.0)
