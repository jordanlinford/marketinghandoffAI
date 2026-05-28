"""
Shared LLM / fallback helpers for the three audience renderers. Each
audience-specific renderer owns its SELECTION + PROMPT_INSTRUCTIONS;
this module owns:
  * The Anthropic call shape (no-echo discipline: intelligence-as-guidance
    in the system message, NEVER as labeled fields).
  * The deterministic fallback assembly — strict facts from the
    intelligence object, no narrative leaps.
  * JSON envelope tolerance (same parser the campaign propose path uses).

These helpers do NOT introduce a new artifact shape — renderers return
a content_dict in the same {content_type, blocks, metadata} structure
the content engine uses, so reports inherit Library + grading + UTM
machinery for free.
"""
from __future__ import annotations

import json
from typing import Any


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
