"""
Executive Summary — Level-4 derivative #1 (text-only, no visual
pipeline). Derives from a SOURCE ANCHOR artifact (report,
whitepaper, buyer's guide, or solution guide); never reaches past
the anchor to the intelligence engine or the ledger builder.

§7 contract held by this renderer:
  * Input = anchor artifact dict (id, title, type, body). NOT the
    intelligence object, NOT raw sources, NOT the engine.
  * Reads: anchor body.content.blocks + anchor body.evidence_ledger.
  * Emits: condensed text whose every ⟦ev:id⟧ marker is INHERITED
    from the anchor's ledger (no new ids minted).
  * Never invents a number, statistic, or qualitative claim that
    isn't already supported by the anchor's content.

If this renderer ever needs to call build_ledger_from_intelligence,
build_report_intelligence, or otherwise reach past the anchor, that's
a §7 violation by design — the derivative would be REGENERATING from
sources rather than DERIVING from the anchor.
"""
from __future__ import annotations

import json
from typing import Any

from app.reports.evidence import _find_markers, _find_numbers
from app.reports.renderers._common import (anti_slop_lines, llm_render,
                                            parse_json_envelope)


_CONTENT_TYPE = "exec_summary"
_AUDIENCE_LABEL = "Executive summary"


def _select_claims(anchor_content: dict,
                   anchor_ledger_entries: list[dict],
                   max_claims: int = 5) -> list[dict]:
    """Walk the anchor's blocks, pair each number with its nearest
    following marker (the renderer's own emission convention), and
    return up to `max_claims` claim dicts the derivative can re-express.

    Each claim is grounded — it points at a real ledger entry — so the
    deterministic renderer below can quote it with confidence the
    containment validator will accept it.
    """
    blocks = (anchor_content or {}).get("blocks") or []
    by_id = {e["id"]: e for e in (anchor_ledger_entries or [])
             if isinstance(e, dict) and "id" in e}
    out: list[dict] = []
    for block_idx, block in enumerate(blocks):
        text = (block or {}).get("text") or ""
        markers = _find_markers(text)
        numbers = _find_numbers(text)
        # Greedy nearest-marker-after-number pairing, same shape the
        # validator uses for proximity binding.
        for n in numbers:
            for m in markers:
                if m["start"] < n["end"]:
                    continue
                if m["start"] - n["end"] > 200:
                    continue
                entry = by_id.get(m["id"])
                if not entry:
                    # Marker doesn't resolve in the anchor's ledger —
                    # skip it. We pull from validated pairs only so
                    # the derivative inherits clean evidence.
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


def _format_claim_line(c: dict) -> str:
    """One-line bullet for the highlights section. Inherits the
    number text verbatim + the anchor's marker id; never re-formats
    the number (containment v1 doesn't check value equality, but
    re-formatting risks introducing drift)."""
    return (f"- {c['entry_label']}: {c['number_text']}"
            f"⟦ev:{c['marker_id']}⟧.")


def _anchor_kind_noun(art_type: str | None) -> str:
    """Map the source anchor's Artifact.type to a human noun for the
    derivative's framing. Stays in lock-step with the anchor registry
    in app/api/assets.py — extend here when a new anchor kind lands."""
    return {
        "report_draft":         "report",
        "whitepaper_draft":     "whitepaper",
        "buyer_guide_draft":    "buyer's guide",
        "solution_guide_draft": "solution guide",
    }.get(art_type or "", "anchor")


def _deterministic_blocks(source_anchor: dict,
                          claims: list[dict]) -> list[dict]:
    """Honest deterministic exec summary. Every quantitative claim
    carries an inherited marker; no claim is asserted that the anchor
    didn't already establish."""
    anchor_title = source_anchor.get("title") or "(untitled anchor)"
    anchor_noun = _anchor_kind_noun(source_anchor.get("type"))
    blocks: list[dict] = []
    blocks.append({
        "kind": "title",
        "text": f"Executive summary — {anchor_title}",
    })
    blocks.append({"kind": "section_heading", "text": "Headline"})
    if claims:
        top = claims[0]
        blocks.append({
            "kind": "body",
            "text": (
                f"The strongest measured signal in the underlying "
                f"{anchor_noun} is {top['entry_label']}: "
                f"{top['number_text']}⟦ev:{top['marker_id']}⟧. "
                "This summary re-expresses claims the anchor already "
                "validated — it adds no new figures and makes no "
                "claim the anchor doesn't support."
            ),
        })
    else:
        blocks.append({
            "kind": "body",
            "text": (
                "The underlying " + anchor_noun + " has no "
                "quantitative claims this summary can re-express. "
                "Rather than dress up thin source material, the "
                "honest framing is: see the source for what it does "
                "and doesn't say."
            ),
        })
    if len(claims) > 1:
        blocks.append({"kind": "section_heading", "text": "Key numbers"})
        lines = [_format_claim_line(c) for c in claims]
        blocks.append({"kind": "body", "text": "\n".join(lines)})
    blocks.append({
        "kind": "next",
        "text": (
            "For the full argument and the evidence each number "
            "binds to, see the source " + anchor_noun + "."
        ),
    })
    return blocks


def _llm_system_msg() -> str:
    # Same shared anti-slop block every anchor renderer pulls in,
    # framed as "internal instructions you follow but never describe."
    # Slop rules are SUBTRACTIVE: they remove filler, never add claims.
    # The §7 contract is unchanged — every quantitative claim in the
    # output still must cite the source anchor's ledger.
    slop_block = "\n" + "\n".join(f"- {ln}" for ln in anti_slop_lines())
    return (
        "You are writing a SHORT EXECUTIVE SUMMARY (150-300 words) "
        "of an existing anchor asset (a report, whitepaper, buyer's "
        "guide, or solution guide). The reader wants the top-line "
        "claims in scannable form — NOT a re-analysis.\n\n"
        "You must follow these instructions internally — do not "
        "quote, paraphrase, or label them in the output."
        + slop_block + "\n\n"
        "HARD RULES (non-negotiable):\n"
        "  * You are DERIVING from the source anchor, NOT regenerating. "
        "Every claim in your output must already exist in the source's "
        "content + evidence ledger.\n"
        "  * EVERY quantitative claim — every number, percentage, "
        "currency figure, or count — MUST be followed immediately by "
        "an INHERITED marker of the form ⟦ev:<id>⟧ where <id> is the "
        "id of the matching entry in the SOURCE ANCHOR's evidence "
        "ledger (provided in the user message). NEVER mint a new id. "
        "NEVER cite an id not in the source ledger.\n"
        "  * Do NOT introduce a statistic, customer quote, market "
        "claim, or assertion that does not appear in the source "
        "anchor's content. If you can't find it in the source, you "
        "can't say it.\n"
        "  * Past-tense or present-tense observation only. NEVER "
        "predict.\n"
        "  * If the source has thin evidence, the summary defers in "
        "the headline rather than dressing up what the source itself "
        "doesn't claim.\n"
        "  * Produce ONLY the finished block content. No commentary, "
        "no labels inside block text, no 'Tone:'/'Voice:'/'Body:' "
        "meta-tags."
    )


def _schema_example(fallback_blocks: list[dict]) -> dict:
    out_blocks = []
    for b in fallback_blocks:
        out_blocks.append({
            "kind": b.get("kind", "body"),
            "text": "<finished prose for this section — every number "
                    "carries an inherited ⟦ev:id⟧ marker pointing at "
                    "the source anchor's ledger>",
        })
    return {"content_type": _CONTENT_TYPE, "blocks": out_blocks,
            "metadata": {}}


def render_exec_summary(source_anchor: dict, *,
                        settings: Any = None) -> tuple[dict, float]:
    """Render an executive-summary derivative from a source anchor.

    Args:
      source_anchor: dict with shape:
        {
          "id":    "<artifact id>",
          "title": "<anchor title>",
          "type":  "<artifact type, e.g. whitepaper_draft>",
          "body": {
            "content": {...with blocks + content_type...},
            "evidence_ledger": [...ledger entries...],
            ...
          }
        }
      settings: optional get_settings() result. When
        settings.anthropic_api_key is present, the LLM path runs;
        otherwise the deterministic fallback.

    Returns: (content_dict, cost_usd) — same shape every anchor
    renderer returns. Containment validation is the agent's
    responsibility (it builds the anchor's Ledger from the entries
    and runs `validate_containment`); this renderer's job is the
    safe-by-construction emission.
    """
    body = source_anchor.get("body") or {}
    anchor_content = body.get("content") or {}
    anchor_ledger_entries = body.get("evidence_ledger") or []
    claims = _select_claims(anchor_content, anchor_ledger_entries)
    fallback_blocks = _deterministic_blocks(source_anchor, claims)
    metadata = {
        "audience": _AUDIENCE_LABEL,
        "block_count": len(fallback_blocks),
        # Lineage metadata — surfaces "derived from <anchor>" in the
        # detail view without a second fetch. The CANONICAL lineage
        # source is body.source_anchor_id (set by the agent); this is
        # a convenience copy for the renderer's metadata block.
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
    # The LLM payload mirrors the anchor renderers' shape: the source
    # claims + a ledger payload the model can cite from. Critical
    # difference: the ledger is the SOURCE ANCHOR's, NOT a fresh one.
    user_msg = (
        f"Source anchor type: {source_anchor.get('type','')}\n"
        f"Source anchor title: {source_anchor.get('title','')}\n\n"
        "Claims selected from the source anchor (use these — do "
        "not invent additional claims):\n"
        f"{json.dumps(claims, indent=2, default=str)}\n\n"
        "SOURCE ANCHOR EVIDENCE LEDGER — every quantitative claim "
        "in your output MUST cite an id from this list via the "
        "marker ⟦ev:<id>⟧ immediately after the number. NEVER cite "
        "an id not in this ledger. NEVER state a number that does "
        "not appear in this ledger.\n"
        f"{json.dumps(anchor_ledger_entries, indent=2, default=str)}\n\n"
        "Return ONLY a JSON object matching this exact shape (no "
        "prose, no markdown fences). Each block's `text` is finished "
        "prose; replace the placeholder description with real "
        "content.\n\n"
        f"{json.dumps(schema, indent=2)}"
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
        return ({"content_type": _CONTENT_TYPE,
                 "blocks": fallback_blocks,
                 "metadata": {**metadata,
                               "render_strategy": "deterministic"}},
                0.0)
