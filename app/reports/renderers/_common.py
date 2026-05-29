"""
Shared LLM / fallback helpers for the three audience renderers. Each
audience-specific renderer owns its SELECTION + PROMPT_INSTRUCTIONS;
this module owns:
  * The Anthropic call shape (no-echo discipline: intelligence-as-guidance
    in the system message, NEVER as labeled fields).
  * The deterministic fallback assembly — strict facts from the
    intelligence object, no narrative leaps.
  * JSON envelope tolerance (same parser the campaign propose path uses).
  * Inline citation emission via cite_num — the renderer emits a number
    AND its evidence-ledger marker in one call, so the §6 generated-vs-
    observed discipline is enforced at the writing site, not regex-
    matched afterward.

These helpers do NOT introduce a new artifact shape — renderers return
a content_dict in the same {content_type, blocks, metadata} structure
the content engine uses, so reports inherit Library + grading + UTM
machinery for free.
"""
from __future__ import annotations

import json
from typing import Any, Callable


# --------------------------------------------------------------------------
# JSON envelope tolerance — same shape as planner._parse_json_envelope
# (fence-stripping, balanced-{...} substring fallback).
# --------------------------------------------------------------------------
def parse_json_envelope(raw: str) -> Any:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        pass
    if s.startswith("```"):
        inner = s.strip("`").strip()
        if "\n" in inner and inner.split("\n", 1)[0].strip().lower() in (
                "json", "jsonc"):
            inner = inner.split("\n", 1)[1]
        try:
            return json.loads(inner.strip())
        except Exception:
            pass
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(s[start:end + 1])
        except Exception:
            pass
    return None


# --------------------------------------------------------------------------
# Style-brief weaving — voice is INSTRUCTION, never a labeled field.
# The model embodies the guidance; it does not narrate it.
# --------------------------------------------------------------------------
def compose_style_lines(profile: dict | None) -> list[str]:
    profile = profile or {}
    out: list[str] = []
    summary = (profile.get("product_summary") or "").strip()
    if summary:
        out.append(f"Org context: {summary}")
    voice = (profile.get("brand_voice") or "").strip()
    if voice:
        out.append("Voice you write in — embody this, NEVER describe or "
                   f"label it in your output: {voice}")
    banned = [str(b).strip() for b in (profile.get("banned_claims") or [])
              if str(b).strip()]
    if banned:
        quoted = "; ".join(f'"{b}"' for b in banned)
        out.append(f"Phrases that would reject this draft — never use any "
                   f"of them: {quoted}")
    return out


# --------------------------------------------------------------------------
# Memory / evidence rendering — past-tense observations only. No
# predictions, no projected percentages.
# --------------------------------------------------------------------------
def memory_lines(highlights: list[dict], *, max_lines: int = 4) -> list[str]:
    lines: list[str] = []
    for h in highlights[:max_lines]:
        obs = (h.get("observation") or "").strip()
        if obs:
            lines.append(f"- {obs}")
    return lines


def watching_lines(watching: list[dict], *, max_lines: int = 3) -> list[str]:
    out: list[str] = []
    for w in watching[:max_lines]:
        obs = (w.get("observation") or "").strip()
        if obs:
            out.append(f"- {obs}")
    return out


# --------------------------------------------------------------------------
# Number formatting — terse for prose, never inventing precision.
# --------------------------------------------------------------------------
def fmt_num(v) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f.is_integer():
        return f"{int(f):,}"
    return f"{f:,.2f}"


def fmt_pct(p) -> str:
    if p is None:
        return "—"
    try:
        return f"{float(p) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(p)


# --------------------------------------------------------------------------
# Inline citation helper — the §6 enforcement site.
# --------------------------------------------------------------------------
def cite_num(ledger, source: str, value: Any, *,
             formatter: Callable[[Any], str] | None = None) -> str:
    """Return f"{formatted}{marker}" — number plus its evidence marker.

    Empty-string marker when the value isn't in the ledger (None
    values, or sources the ledger doesn't know about) — caller still
    gets the formatted string without a broken marker. This keeps
    cite_num drop-in for "fmt_num(x)" or "fmt_pct(x)" at every emission
    site so the renderer can always cite.

    When the ledger is None (caller didn't pass one), we just format —
    no marker. Used by tests and legacy paths that skip binding.
    """
    formatted = (formatter(value) if formatter is not None else str(value))
    if ledger is None or value is None:
        return formatted
    marker = ledger.cite(source, value)
    if not marker:
        # Try source-only lookup as a fallback for cases where the
        # exact value canonicalization missed (rounding edge cases).
        marker = ledger.cite(source)
    return f"{formatted}{marker}" if marker else formatted


def cite_inline(ledger, source: str, value: Any) -> str:
    """Return the marker token alone (no number). Used when the prose
    is already written and we want to attach a marker after a phrase
    rather than embed it next to a number — e.g. a memory observation
    that already contains the numbers in its own format."""
    if ledger is None or value is None:
        return ""
    return ledger.cite(source, value) or ledger.cite(source)


# --------------------------------------------------------------------------
# LLM call — only invoked when settings.anthropic_api_key is present.
# Caller falls back to deterministic on any failure.
# --------------------------------------------------------------------------
def llm_render(*, audience_label: str, system_msg: str, user_msg: str,
               settings, fallback_blocks: list[dict],
               content_type: str) -> tuple[dict, float]:
    """Returns (content_dict, cost_usd). Raises on transport errors so
    the caller can decide whether to fall back."""
    import anthropic
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=settings.anthropic_model, max_tokens=2200,
        system=system_msg,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = "".join(b.text for b in msg.content
                  if getattr(b, "type", "") == "text")
    parsed = parse_json_envelope(raw)
    if (not isinstance(parsed, dict)
            or not isinstance(parsed.get("blocks"), list)
            or not parsed["blocks"]):
        # LLM didn't honor the schema — use the deterministic body but
        # still record the cost we incurred.
        parsed = {"content_type": content_type, "blocks": fallback_blocks,
                  "metadata": {"audience": audience_label,
                               "render_strategy": "llm_fell_back_to_template"}}
    else:
        parsed["content_type"] = content_type
        parsed.setdefault("metadata", {})
        parsed["metadata"]["audience"] = audience_label
        parsed["metadata"]["render_strategy"] = "llm"
    usage = getattr(msg, "usage", None)
    cost = 0.0
    if usage:
        cost = (usage.input_tokens * 3 + usage.output_tokens * 15) / 1_000_000
    return parsed, round(cost, 6)


# --------------------------------------------------------------------------
# Schema example — same trick the content_templates path uses to nudge
# the model toward the block-shape without copying real text.
# --------------------------------------------------------------------------
def schema_example(content_type: str, fallback_blocks: list[dict]) -> dict:
    out_blocks = []
    for b in fallback_blocks:
        kind = b.get("kind", "body")
        out_blocks.append({
            "kind": kind,
            "text": "<polished prose for this section — finished sentences, "
                    "no labels, no meta-commentary>",
        })
    return {"content_type": content_type, "blocks": out_blocks,
            "metadata": {}}


# --------------------------------------------------------------------------
# Period header — same little summary every audience starts with, but
# each renderer is free to omit it (CEO weekly does).
# --------------------------------------------------------------------------
def period_header(intelligence: dict) -> str:
    scope = intelligence.get("scope") or {}
    if scope.get("kind") == "campaign":
        return (f"Campaign report for {scope.get('campaign_name') or 'campaign'} "
                f"({scope.get('start')} – {scope.get('end')})")
    return f"Period: {scope.get('start')} – {scope.get('end')}"


# --------------------------------------------------------------------------
# Future-date stub — shared across the three audiences. When the engine
# marks scope.is_future=True, every renderer produces a short, truthful
# "no data exists yet" body. Three sections only:
#   1. The fact: no data exists for the requested period.
#   2. Reference baseline: current memory snapshot AS OF TODAY, clearly
#      labeled — never as a claim about the requested period.
#   3. The path forward: regenerate once data exists.
#
# Same anti-overclaim discipline as the memory layer's thin-data
# deferral. When the system can't say something true, it says what it
# can't say and offers the nearest truthful thing.
# --------------------------------------------------------------------------
def is_future_scope(intelligence: dict) -> bool:
    return bool((intelligence.get("scope") or {}).get("is_future"))


def future_stub_blocks(intelligence: dict, *,
                        content_type: str,
                        audience_label: str,
                        ledger=None) -> tuple[list[dict], dict]:
    scope = intelligence.get("scope") or {}
    today = scope.get("today") or ""
    start, end = scope.get("start") or "?", scope.get("end") or "?"
    highlights = intelligence.get("memory_highlights") or []
    reference_label = (intelligence.get("memory_reference_label")
                        or f"Memory snapshot as of {today} — NOT a finding "
                           "about the requested future period.")
    blocks: list[dict] = []
    blocks.append({
        "kind": "headline",
        "text": (f"No data exists yet for {start} to {end}. This period is "
                 f"in the future (today is {today})."),
    })
    if highlights:
        # Build the reference baseline with per-number markers so the
        # validator sees every figure as bound to a ledger entry.
        # Numbers in stub mode are EXPLICITLY framed as "as of today,
        # not for the requested period" — they don't claim anything
        # about the future, but they're still real numbers the system
        # has to cite honestly.
        ref_lines = ["For reference — what memory currently knows "
                     "(AS OF TODAY, not for the requested period):"]
        for i, h in enumerate(highlights[:4]):
            mb = h.get("metric_basis") or {}
            label = h.get("key_display") or h.get("key") or f"highlight {i}"
            rate = mb.get("conversion_rate")
            clicks = mb.get("clicks")
            convs = mb.get("conversions")
            dp = mb.get("data_points")
            parts = [f"{label}:"]
            if rate is not None:
                parts.append(cite_num(
                    ledger, f"memory_highlights[{i}].metric_basis.conversion_rate",
                    rate, formatter=fmt_pct) + " conversion rate")
            elif convs is not None and convs:
                parts.append(cite_num(
                    ledger, f"memory_highlights[{i}].metric_basis.conversions",
                    convs, formatter=fmt_num) + " conversions")
            if clicks is not None and clicks:
                parts.append("from " + cite_num(
                    ledger, f"memory_highlights[{i}].metric_basis.clicks",
                    clicks, formatter=fmt_num) + " clicks")
            if dp is not None and dp:
                parts.append("across " + cite_num(
                    ledger, f"memory_highlights[{i}].metric_basis.data_points",
                    dp, formatter=fmt_num) + " data points")
            ref_lines.append("- " + " ".join(parts) + ".")
        blocks.append({"kind": "body", "text": "\n".join(ref_lines)})
        blocks.append({"kind": "body", "text": reference_label})
    else:
        blocks.append({
            "kind": "body",
            "text": "Memory has no high-or-moderate-confidence patterns to "
                    "report as a reference baseline yet. The system is "
                    "still learning.",
        })
    blocks.append({
        "kind": "next",
        "text": "Re-generate this report once data exists for the "
                "requested period.",
    })
    metadata = {
        "audience": audience_label,
        "scope": scope,
        "render_strategy": "future_stub",
        "block_count": len(blocks),
    }
    return blocks, metadata
