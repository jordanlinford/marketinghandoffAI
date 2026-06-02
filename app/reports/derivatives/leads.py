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


def _is_absence(entry: dict) -> bool:
    """Is this entry a zero-valued 'absence' claim rather than a
    finding? An attributed conversion count of 0 IS an honest claim
    the anchor makes — but it's the ABSENCE of data, not a
    finding worth foregrounding across an entire content set. The
    fan-out spine deserves a real signal; the zeros stay perfectly
    valid in sibling bodies, they just aren't lead-eligible.

    LOAD-BEARING: this is a CATEGORY exclusion (absence vs. presence
    of a measurement), NOT a magnitude ranking. The brief is
    explicit: do not 'prefer bigger numbers,' which would wrongly
    teach the selector that larger values are better stories. A
    non-zero small claim still outranks nothing inappropriately.
    """
    v = entry.get("value")
    if v is None:
        # None should already be filtered at ledger build time
        # (Ledger.add skips None), but defensive: a missing value is
        # an absence of measurement, same category as 0.
        return True
    if isinstance(v, bool):
        # Bool is a numeric subtype in Python; treat False as absence,
        # True as presence — defensive on the rare bool-valued
        # ledger entry.
        return v is False
    try:
        return float(v) == 0.0
    except (TypeError, ValueError):
        # Non-numeric values (strings, lists, dicts) aren't numeric
        # absences. They're presence-of-information by default.
        return False


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
      2. ABSENCE EXCLUSION — zero-valued claims (and missing-value
         claims) are removed from LEAD candidacy. They remain
         perfectly valid claims in sibling BODIES; they just aren't
         eligible to be the foregrounded spine. The exclusion is a
         CATEGORY rule (absence vs. measurement), not a magnitude
         ranking: a non-zero small claim still outranks nothing
         improperly. See _is_absence above.
      3. Prefer attributed entries over backdrop (backdrop is funnel
         context, never marketing-driven — §3 discipline).
      4. Within attributed, prefer the highest confidence tier per
         _CONFIDENCE_RANK.
      5. Tiebreaker: earliest cited in the anchor (signals the
         anchor itself foregrounded it).

    All-zero fallthrough: when EVERY cited claim is an absence, the
    function still selects the highest-priority cited claim (same
    sort, no exclusion) and flags the result with weak_lead=True.
    The fan-out coordinator surfaces that advisory; it does NOT
    block. This is honest: an anchor with no findings has no good
    spine — we say so rather than fabricate one.

    Returns:
        {
          "ev_id":                  str,    # selected pointer
          "label":                  str,    # from ledger entry
          "value":                  Any,    # from ledger entry
          "confidence":             str,    # from ledger entry
          "baseline_vs_attributed": str,    # from ledger entry
          "weak_lead":              bool,   # True only when every
                                            # cited claim is an
                                            # absence (no non-zero
                                            # findings to spine on)
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

    def _ranked(entries: list[tuple[int, dict]]) -> list[tuple[tuple, dict]]:
        # Build (sort_key, entry) tuples. sort_key sorts ASCENDING;
        # we want the LOWEST tuple first (best candidate). Order
        # within: attributed (0) before backdrop (1), then highest
        # confidence first (negate the rank), then earliest cited
        # first (raw order_idx).
        out: list[tuple[tuple, dict]] = []
        for order_idx, entry in entries:
            bvA = entry.get("baseline_vs_attributed") or "n_a"
            conf = entry.get("confidence") or "n_a"
            attr_key = 0 if bvA == "attributed" else 1
            conf_key = -(_CONFIDENCE_RANK.get(conf, 0))
            out.append(((attr_key, conf_key, order_idx), entry))
        out.sort(key=lambda t: t[0])
        return out

    # Resolve cited ids to their ledger entries (preserving the
    # cited order — used as the tiebreaker key). Drop ids that don't
    # resolve in the ledger; that's a §6 binding failure on the
    # source anchor itself, and we never lead with a broken marker.
    enumerated: list[tuple[int, dict]] = []
    for i, ev_id in enumerate(cited):
        entry = by_id.get(ev_id)
        if entry is not None:
            enumerated.append((i, entry))
    if not enumerated:
        return None

    # ABSENCE EXCLUSION — primary pass excludes zero-valued claims.
    # If at least one non-absence claim exists, the lead comes from
    # that filtered pool; the zeros never become leads.
    non_absence = [(i, e) for (i, e) in enumerated if not _is_absence(e)]
    if non_absence:
        chosen = _ranked(non_absence)[0][1]
        weak_lead = False
    else:
        # Every cited claim is an absence — the anchor has no
        # non-zero findings to spine on. Pick the highest-priority
        # claim from the FULL cited pool (so a lead still exists)
        # and flag it weak. Honest, not blocking.
        chosen = _ranked(enumerated)[0][1]
        weak_lead = True

    return {
        "ev_id":                  chosen["id"],
        "label":                  chosen.get("label") or "",
        "value":                  chosen.get("value"),
        "confidence":             chosen.get("confidence") or "n_a",
        "baseline_vs_attributed": chosen.get("baseline_vs_attributed") or "n_a",
        "weak_lead":              weak_lead,
    }


def validate_lead(anchor_body: dict, ev_id: str) -> dict | None:
    """Confirm an externally-supplied ev_id is a valid lead for this
    anchor: it must exist in the ledger AND be cited in anchor prose.
    Returns the lead payload (same shape as select_lead) on success,
    None on failure. Used by the fan-out API to validate user-
    provided lead overrides.

    Why both conditions: an ev:id in the ledger but uncited in prose
    is not a claim the anchor surfaced. Foregrounding it in a fan-out
    would be inventing a claim the anchor didn't make.

    The absence exclusion that select_lead applies is INTENTIONALLY
    NOT applied here: this is the user-override escape hatch. If a
    user explicitly picks a zero-valued cited claim as the lead, we
    honor it — that's their judgment, not the auto-selector's. The
    returned payload always carries weak_lead=False, since an
    override is by definition an explicit human choice, not a weak
    auto-pick."""
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
        "weak_lead":              False,
    }
