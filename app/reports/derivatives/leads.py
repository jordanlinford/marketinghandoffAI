"""
Lead selection for fan-out derivatives.

THE GOVERNING DISCIPLINE (load-bearing — restated here because this
is the file where the §6/§7 frontier could most easily slip):

  The LEAD is a SELECTION, never a SYNTHESIS.

A lead is an ev:id — a pointer to one entry in the SOURCE ANCHOR's
existing ledger. It is NOT a string this module composes, NOT a
new headline, NOT a unifying message, NOT a generated label. Every
function in this module that returns lead information returns
fields LIFTED FROM THE ANCHOR's ledger entries (label, value,
confidence, baseline_vs_attributed) — not rewordings.

Why this is load-bearing: a generated unifying message would have
NO ledger entry of its own. It would be a net-new claim
propagated into every sibling via the fan-out, passing §7
trivially (siblings contain it because they all repeat it) while
being exactly the §6 fabrication §6 exists to stop. The
synthesis-vs-selection distinction at THIS module is the
architectural seam that prevents it. If a future edit introduces
a function that COMPOSES new prose for the lead, that's the
violation; revert it.

Two surfaces:
  * select_lead(anchor_body)
      Heuristic-driven pick of one existing claim to foreground.
      Deterministic given the same anchor body.
  * validate_lead(anchor_body, ev_id)
      Confirms an externally-supplied ev:id is BOTH in the
      anchor's ledger AND cited in the anchor's prose. (A ledger
      entry that no anchor block cites isn't really a claim the
      anchor MADE — it's just a number the ledger has. Foregrounding
      it would be inventing a claim the anchor didn't surface.)
"""
from __future__ import annotations

from app.reports.evidence import _find_markers


# Higher = better. The default heuristic prefers the highest-
# confidence cited claim. n_a entries (period_summary attributed
# metrics — directly observed) get a middle weight so they can be
# leads when nothing higher is available, but high/moderate memory
# patterns outrank them when present.
_CONFIDENCE_RANK = {
    "high":         5,
    "moderate":     4,
    "n_a":          3,
    "low":          2,
    "insufficient": 1,
}


def _cited_ev_ids_in_order(anchor_content: dict) -> list[str]:
    """Walk anchor blocks in document order, return the ev:id of
    every cited claim in the order they first appear. Used both as
    the candidate pool for select_lead AND as the membership check
    for validate_lead. A ledger entry NOT in this list isn't an
    existing anchor claim — it's just an unused ledger number."""
    out: list[str] = []
    seen: set[str] = set()
    blocks = (anchor_content or {}).get("blocks") or []
    for block in blocks:
        text = (block or {}).get("text") or ""
        for m in _find_markers(text):
            mid = m["id"]
            if mid in seen:
                continue
            seen.add(mid)
            out.append(mid)
    return out


def _ledger_by_id(anchor_ledger_entries: list[dict]) -> dict[str, dict]:
    return {e["id"]: e for e in (anchor_ledger_entries or [])
            if isinstance(e, dict) and "id" in e}


def select_lead(anchor_body: dict) -> dict | None:
    """Return the LEAD ev:id + the anchor's existing ledger payload
    for it. Pure SELECTION — every field is lifted verbatim from the
    anchor; no string is composed.

    Heuristic (deterministic given the same anchor):
      1. Candidate pool = ev:ids cited in anchor prose (ledger
         entries not surfaced by the anchor don't count — see
         module docstring).
      2. Prefer attributed entries over backdrop (backdrop is funnel
         context, never marketing-driven — §3 discipline).
      3. Within attributed, prefer the highest confidence tier per
         _CONFIDENCE_RANK.
      4. Tiebreaker: earliest cited in the anchor (signals the
         anchor itself foregrounded it).

    Returns:
        {
          "ev_id":                  str,    # selected pointer
          "label":                  str,    # from ledger entry
          "value":                  Any,    # from ledger entry
          "confidence":             str,    # from ledger entry
          "baseline_vs_attributed": str,    # from ledger entry
        }
        or None if the anchor has no cited claims (a §6 honest
        outcome — never fabricate a lead).
    """
    content = (anchor_body or {}).get("content") or {}
    ledger = (anchor_body or {}).get("evidence_ledger") or []
    by_id = _ledger_by_id(ledger)
    cited = _cited_ev_ids_in_order(content)
    if not cited:
        return None
    # Build (sort_key, entry) tuples. sort_key sorts ASCENDING; we
    # want the LOWEST tuple first (best candidate).
    candidates: list[tuple[tuple, dict]] = []
    for order_idx, ev_id in enumerate(cited):
        entry = by_id.get(ev_id)
        if entry is None:
            # Marker cited in prose but missing from ledger — a §6
            # binding failure on the source anchor itself. Skip;
            # leads must point at REAL ledger entries.
            continue
        bvA = entry.get("baseline_vs_attributed") or "n_a"
        conf = entry.get("confidence") or "n_a"
        # Sort: attributed (0) before backdrop (1), highest
        # confidence first (negate the rank), earliest cited first.
        attr_key = 0 if bvA == "attributed" else 1
        conf_key = -(_CONFIDENCE_RANK.get(conf, 0))
        candidates.append(((attr_key, conf_key, order_idx), entry))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    chosen = candidates[0][1]
    return {
        "ev_id":                  chosen["id"],
        "label":                  chosen.get("label") or "",
        "value":                  chosen.get("value"),
        "confidence":             chosen.get("confidence") or "n_a",
        "baseline_vs_attributed": chosen.get("baseline_vs_attributed") or "n_a",
    }


def validate_lead(anchor_body: dict, ev_id: str) -> dict | None:
    """Confirm an externally-supplied ev_id is a valid lead for this
    anchor: it must exist in the ledger AND be cited in anchor prose.
    Returns the lead payload (same shape as select_lead) on success,
    None on failure. Used by the fan-out API to validate user-
    provided lead overrides.

    Why both conditions: an ev:id in the ledger but uncited in prose
    is not a claim the anchor surfaced. Foregrounding it in a fan-out
    would be inventing a claim the anchor didn't make."""
    if not ev_id:
        return None
    content = (anchor_body or {}).get("content") or {}
    ledger = (anchor_body or {}).get("evidence_ledger") or []
    by_id = _ledger_by_id(ledger)
    cited = set(_cited_ev_ids_in_order(content))
    if ev_id not in cited:
        return None
    entry = by_id.get(ev_id)
    if entry is None:
        return None
    return {
        "ev_id":                  entry["id"],
        "label":                  entry.get("label") or "",
        "value":                  entry.get("value"),
        "confidence":             entry.get("confidence") or "n_a",
        "baseline_vs_attributed": entry.get("baseline_vs_attributed") or "n_a",
    }
