"""
Evidence Ledger — the deterministic, pre-LLM substrate the renderers
cite from, and the deterministic, post-LLM check that says they did.

THE LOAD-BEARING PRINCIPLE (§6 generated-vs-observed, from
docs/cross-layer-disciplines.md): every quantitative claim in a
rendered report must trace back to a real ledger entry built from the
intelligence object. The renderer EMITS markers next to the numbers it
cites; the validator scans the resulting prose and asserts every
number-shaped token carries a resolving marker. A bare number with no
marker is a VIOLATION, recorded as such.

Why this is honest and not theater: the binding is WRITTEN at the same
moment the number is rendered (the renderer owns both the value and
the marker). The validator independently checks the marker resolves,
which is a deterministic property of the (text, ledger) pair. Nothing
is regex-matched after the fact pretending to be a source.

§3 baseline-vs-attributed: every ledger entry carries a
`baseline_vs_attributed` flag so the renderer + UI can label backdrop
numbers as backdrop, never as marketing-driven.

The ledger READS existing intelligence fields by name only. It does NOT
refactor the engine. Field paths the ledger knows about are the same
shapes the renderers already consult: period_summary.attributed,
period_summary.backdrop, period_summary.deltas, memory_highlights[i].
metric_basis, watching[i].metric_basis, campaigns[i].attributed,
campaigns[i].plan_items + generated_assets, top_content[i].attributed,
production.*, notable_changes[i].delta_pct.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# --------------------------------------------------------------------------
# Marker shape — unicode tortoise-shell brackets, chosen because they
# don't collide with content prose and don't show up in any existing
# block text in the deterministic renderers or the LLM templates.
# --------------------------------------------------------------------------
_MARKER_OPEN = "⟦"   # ⟦
_MARKER_CLOSE = "⟧"  # ⟧
_MARKER_PREFIX = f"{_MARKER_OPEN}ev:"
_MARKER_RE = re.compile(rf"{re.escape(_MARKER_OPEN)}ev:([A-Za-z0-9_\-]+)"
                        rf"{re.escape(_MARKER_CLOSE)}")

# Number-shaped tokens that count as quantitative CLAIMS in prose:
#   * currency  $1,234.56
#   * percentages 5.0%, -10%, +3%
#   * decimals  5.0, 0.05
#   * comma-separated counts 1,200
#   * plain integers 0, 42, 1200
# Order matters — we run each pattern, then mask matches so later
# patterns don't re-match the same span.
_PATTERNS = [
    (re.compile(r"\$\d+(?:,\d{3})*(?:\.\d+)?"),                 "currency"),
    (re.compile(r"[+-]?\d+(?:\.\d+)?%"),                         "percent"),
    (re.compile(r"(?<!\d)[+-]?\d+\.\d+(?!\d)"),                  "decimal"),
    (re.compile(r"(?<!\d)\d{1,3}(?:,\d{3})+(?!\d)"),             "comma_int"),
    (re.compile(r"(?<![\d\-])[+-]?\d+(?!\d)"),                   "integer"),
]

# Strings we should NEVER count as quantitative claims — dates, times,
# version-style suffixes, and the marker bodies themselves. We mask
# these out of the text before the number scanner runs.
_MONTHS = (
    r"(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|"
    r"Oct|Nov|Dec)"
)
_SKIP_PATTERNS = [
    re.compile(r"\d{4}-\d{2}-\d{2}"),               # ISO dates 2026-05-29
    re.compile(r"\d{2}:\d{2}(:\d{2})?"),            # times 14:30
    re.compile(r"\bv\d+\b"),                         # v2, v10 (versions)
    # Human-format dates the LLM writes in titles + headers:
    # "April 29, 2026", "May 29", "29 April 2026", etc. The model
    # naturally writes these without markers (they're not claims —
    # they're the period's name), so the validator must skip them.
    re.compile(rf"{_MONTHS}\s+\d{{1,2}}(?:\s*,\s*\d{{4}})?", re.IGNORECASE),
    re.compile(rf"\d{{1,2}}\s+{_MONTHS}(?:\s+\d{{4}})?", re.IGNORECASE),
    re.compile(r"\b(?:19|20)\d{2}\b"),               # standalone 4-digit year
    _MARKER_RE,                                       # the markers themselves
]


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------
def _canonicalize_value(v) -> str:
    """Stable canonical form for value equality + hashing. Floats are
    rounded to 6 decimals to absorb display rounding."""
    if isinstance(v, bool):
        return f"bool:{int(v)}"
    if isinstance(v, int):
        return f"int:{v}"
    if isinstance(v, float):
        return f"float:{round(v, 6)}"
    if v is None:
        return "none"
    return f"str:{v}"


class Ledger:
    """A flat, deterministic list of evidence entries the renderers
    cite from. Idempotent on (source, value): adding the same pair
    twice yields the same id.

    The ledger has no opinion about how renderers select or phrase
    things — it just holds the values. The renderer decides which
    entries to cite and where the markers go.
    """

    def __init__(self) -> None:
        self._entries: list[dict] = []
        self._by_source: dict[tuple[str, str], str] = {}  # (source, val) -> id

    def add(self, *, source: str, value: Any, label: str,
            confidence: str = "n_a",
            baseline_vs_attributed: str = "n_a") -> str:
        """Add or return id of an existing entry for this (source, value).

        Skips None values entirely — we don't cite the absence of a
        number, we just don't render it.
        """
        if value is None:
            return ""
        key = (source, _canonicalize_value(value))
        if key in self._by_source:
            return self._by_source[key]
        # Ledger ids are just the index string — "1", "2", "3", ... The
        # marker format ⟦ev:<id>⟧ already carries the "ev:" namespace.
        # Putting a redundant "ev" inside the id caused the LLM to
        # collapse the prefix and write ⟦ev:1⟧ for entry id "ev1" — an
        # unresolvable marker. Numeric id strings sidestep that.
        ev_id = str(len(self._entries) + 1)
        self._entries.append({
            "id": ev_id, "source": source, "value": value,
            "label": label, "confidence": confidence,
            "baseline_vs_attributed": baseline_vs_attributed,
        })
        self._by_source[key] = ev_id
        return ev_id

    def cite(self, source: str, value: Any = None) -> str:
        """Return the inline marker for an entry by source (optionally
        also pinned to value). Empty string when no entry exists —
        callers compose the string with f"{number}{ledger.cite(...)}"
        so missing entries don't leave broken syntax in prose.
        """
        if value is None:
            # Look up by source only — pick the first matching entry.
            for e in self._entries:
                if e["source"] == source:
                    return f"{_MARKER_PREFIX}{e['id']}{_MARKER_CLOSE}"
            return ""
        key = (source, _canonicalize_value(value))
        ev_id = self._by_source.get(key)
        if not ev_id:
            return ""
        return f"{_MARKER_PREFIX}{ev_id}{_MARKER_CLOSE}"

    def has(self, ev_id: str) -> bool:
        return any(e["id"] == ev_id for e in self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterable[dict]:
        return iter(self._entries)

    def to_list(self) -> list[dict]:
        # Copy so callers can serialize without mutating the ledger.
        return [dict(e) for e in self._entries]

    def prompt_payload(self) -> list[dict]:
        """Compact JSON-shaped representation for inclusion in an LLM
        user message — only the fields the model needs to cite
        correctly. We deliberately keep confidence + flag in the
        payload so the renderer can frame the citation honestly."""
        return [
            {"id": e["id"], "value": e["value"], "label": e["label"],
             "confidence": e["confidence"],
             "baseline_vs_attributed": e["baseline_vs_attributed"]}
            for e in self._entries
        ]


# --------------------------------------------------------------------------
# Builder
# --------------------------------------------------------------------------
def build_ledger_from_intelligence(intelligence: dict) -> Ledger:
    """Walk the intelligence object and emit a ledger entry for every
    quantitative field the renderers might cite.

    Field paths use the EXISTING shape — no engine refactor. Same
    keys the renderers already read off (period_summary.attributed.*,
    memory_highlights[i].metric_basis.*, campaigns[i].attributed.*,
    etc.). The renderer side cites by source string.
    """
    L = Ledger()
    if not isinstance(intelligence, dict):
        return L

    # ---- period_summary --------------------------------------------------
    ps = intelligence.get("period_summary") or {}
    if isinstance(ps, dict):
        attr = ps.get("attributed") or {}
        if isinstance(attr, dict):
            for key, label in (
                    ("clicks", "Attributed clicks"),
                    ("conversions", "Attributed conversions"),
                    ("conversion_rate", "Attributed conversion rate"),
                    ("data_points", "Attributed data points"),
                    ("other", "Attributed other metrics")):
                L.add(source=f"period_summary.attributed.{key}",
                      value=attr.get(key), label=label,
                      confidence="n_a",
                      baseline_vs_attributed="attributed")
        back = ps.get("backdrop") or {}
        if isinstance(back, dict):
            for key, label in (
                    ("clicks", "Backdrop clicks (untagged, not marketing-driven)"),
                    ("conversions", "Backdrop conversions (untagged, not marketing-driven)"),
                    ("data_points", "Backdrop data points (untagged)"),
                    ("other", "Backdrop other metrics")):
                L.add(source=f"period_summary.backdrop.{key}",
                      value=back.get(key), label=label,
                      confidence="n_a",
                      baseline_vs_attributed="backdrop")
        deltas = ps.get("deltas") or {}
        if isinstance(deltas, dict):
            for key in ("clicks", "conversions", "conversion_rate"):
                d = deltas.get(key) or {}
                if not isinstance(d, dict):
                    continue
                pct = d.get("pct")
                if pct is None:
                    continue
                L.add(source=f"period_summary.deltas.{key}.pct",
                      value=pct,
                      label=f"Delta {key} vs prior period (pct)",
                      confidence="n_a",
                      baseline_vs_attributed="attributed")

    # ---- memory_highlights ----------------------------------------------
    for i, h in enumerate(intelligence.get("memory_highlights") or []):
        if not isinstance(h, dict):
            continue
        mb = h.get("metric_basis") or {}
        if not isinstance(mb, dict):
            continue
        key_display = h.get("key_display") or h.get("key") or f"highlight {i}"
        conf = h.get("confidence") or "n_a"
        for fld, fld_label in (
                ("clicks", "Memory clicks"),
                ("conversions", "Memory conversions"),
                ("conversion_rate", "Memory conversion rate"),
                ("data_points", "Memory data points")):
            v = mb.get(fld)
            L.add(source=f"memory_highlights[{i}].metric_basis.{fld}",
                  value=v,
                  label=f"{fld_label} ({key_display})",
                  confidence=conf,
                  baseline_vs_attributed="attributed")

    # ---- watching --------------------------------------------------------
    for i, w in enumerate(intelligence.get("watching") or []):
        if not isinstance(w, dict):
            continue
        mb = w.get("metric_basis") or {}
        if not isinstance(mb, dict):
            continue
        key_display = w.get("key_display") or w.get("key") or f"watch {i}"
        conf = w.get("confidence") or "n_a"
        for fld in ("clicks", "conversions", "data_points",
                    "conversion_rate"):
            v = mb.get(fld)
            L.add(source=f"watching[{i}].metric_basis.{fld}",
                  value=v,
                  label=f"Watching {fld} ({key_display})",
                  confidence=conf,
                  baseline_vs_attributed="attributed")

    # ---- campaigns -------------------------------------------------------
    for i, c in enumerate(intelligence.get("campaigns") or []):
        if not isinstance(c, dict):
            continue
        name = c.get("name") or f"campaign {i}"
        attr = c.get("attributed") or {}
        if isinstance(attr, dict):
            for fld, fld_label in (
                    ("clicks", "Campaign clicks"),
                    ("conversions", "Campaign conversions"),
                    ("conversion_rate", "Campaign conversion rate"),
                    ("data_points", "Campaign data points")):
                L.add(source=f"campaigns[{i}].attributed.{fld}",
                      value=attr.get(fld),
                      label=f"{fld_label}: {name}",
                      confidence="n_a",
                      baseline_vs_attributed="attributed")
        for fld in ("plan_items", "generated_assets"):
            L.add(source=f"campaigns[{i}].{fld}",
                  value=c.get(fld),
                  label=f"Campaign {fld}: {name}",
                  confidence="n_a",
                  baseline_vs_attributed="n_a")

    # ---- top_content -----------------------------------------------------
    for i, t in enumerate(intelligence.get("top_content") or []):
        if not isinstance(t, dict):
            continue
        title = (t.get("title") or "")[:60] or f"top_content {i}"
        attr = t.get("attributed") or {}
        if isinstance(attr, dict):
            for fld, fld_label in (
                    ("clicks", "Top content clicks"),
                    ("conversions", "Top content conversions"),
                    ("conversion_rate", "Top content conversion rate")):
                L.add(source=f"top_content[{i}].attributed.{fld}",
                      value=attr.get(fld),
                      label=f"{fld_label}: {title}",
                      confidence="n_a",
                      baseline_vs_attributed="attributed")

    # ---- production ------------------------------------------------------
    prod = intelligence.get("production") or {}
    if isinstance(prod, dict):
        for fld in ("runs_total", "runs_succeeded", "runs_failed",
                    "runs_pending", "artifacts_total", "artifacts_ready",
                    "artifacts_pending_review", "cost_usd_total"):
            L.add(source=f"production.{fld}",
                  value=prod.get(fld),
                  label=f"Production {fld.replace('_', ' ')}",
                  confidence="n_a",
                  baseline_vs_attributed="n_a")

    # ---- notable_changes (only the delta_pct field) ---------------------
    for i, n in enumerate(intelligence.get("notable_changes") or []):
        if not isinstance(n, dict):
            continue
        dp = n.get("delta_pct")
        if dp is None:
            continue
        stage = n.get("stage") or n.get("kind") or f"change {i}"
        L.add(source=f"notable_changes[{i}].delta_pct",
              value=dp,
              label=f"Notable change: {stage} delta",
              confidence="n_a",
              baseline_vs_attributed="attributed")

    return L


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def _mask_spans(text: str) -> str:
    """Replace text inside SKIP patterns with spaces so the number
    scanner can't re-match within dates / times / markers / versions.
    Preserves character positions so we can still report meaningful
    offsets in trust_checks."""
    out = list(text)
    for pat in _SKIP_PATTERNS:
        for m in pat.finditer(text):
            for i in range(m.start(), m.end()):
                out[i] = " "
    return "".join(out)


def _find_numbers(text: str) -> list[dict]:
    """Return every number-shaped token in text, with positions. Skips
    dates / times / version suffixes / marker bodies via _mask_spans."""
    cleaned = _mask_spans(text)
    found: list[dict] = []
    # We process the patterns in order, masking each match so later,
    # broader patterns don't re-find the same span.
    work = list(cleaned)
    for pat, kind in _PATTERNS:
        joined = "".join(work)
        for m in pat.finditer(joined):
            found.append({
                "text": m.group(0),
                "start": m.start(),
                "end": m.end(),
                "kind": kind,
            })
            for i in range(m.start(), m.end()):
                work[i] = " "
    found.sort(key=lambda f: f["start"])
    return found


def _find_markers(text: str) -> list[dict]:
    return [{
        "id": m.group(1),
        "start": m.start(),
        "end": m.end(),
        "text": m.group(0),
    } for m in _MARKER_RE.finditer(text)]


def _block_text(content: dict) -> str:
    """Concatenate all block text into a single string with newline
    separators between blocks. Single-string scanning is OK because
    markers point to ids that resolve globally — block boundaries
    don't matter for resolution, only proximity does."""
    blocks = (content or {}).get("blocks") or []
    return "\n".join((b.get("text") or "") for b in blocks)


_PROXIMITY_SCOPE = 200  # chars after a number to look for its marker


def validate_evidence_binding(content: dict, ledger: Ledger) -> dict:
    """Deterministic check that every quantitative claim in `content`
    is bound to a ledger entry.

    Returns a trust_checks dict:
      {
        ledger_size, markers_found, markers_resolved,
        markers_unresolved: [list of unresolved ids],
        numbers_found, numbers_bound,
        numbers_unbound: [list of {text, start} for violations],
        passed: bool,
      }

    `passed` is True iff every marker resolves AND every number-shaped
    token has a resolving marker within _PROXIMITY_SCOPE chars after
    it. This build RECORDS the result on the artifact; the next build
    enforces it at the gate.
    """
    text = _block_text(content)
    markers = _find_markers(text)
    numbers = _find_numbers(text)

    resolved_marker_positions: list[tuple[int, str]] = []
    markers_unresolved: list[str] = []
    for m in markers:
        if ledger.has(m["id"]):
            resolved_marker_positions.append((m["start"], m["id"]))
        else:
            markers_unresolved.append(m["id"])

    numbers_unbound: list[dict] = []
    for n in numbers:
        # A number is BOUND if a resolving marker appears within
        # _PROXIMITY_SCOPE chars after the number's end. Forward-only
        # because that's how the renderer emits them ("1,200 ⟦ev:1⟧")
        # and how the LLM is instructed to.
        bound = False
        for marker_start, _id in resolved_marker_positions:
            if marker_start < n["end"]:
                continue
            if marker_start - n["end"] <= _PROXIMITY_SCOPE:
                bound = True
                break
        if not bound:
            numbers_unbound.append({"text": n["text"], "start": n["start"],
                                     "kind": n["kind"]})

    passed = (not markers_unresolved) and (not numbers_unbound)
    return {
        "ledger_size": len(ledger),
        "markers_found": len(markers),
        "markers_resolved": len(markers) - len(markers_unresolved),
        "markers_unresolved": markers_unresolved,
        "numbers_found": len(numbers),
        "numbers_bound": len(numbers) - len(numbers_unbound),
        "numbers_unbound": numbers_unbound,
        "passed": passed,
        # Surfaced so the UI build can later show "5/5 claims bound"
        # next to the trust pill without doing arithmetic.
        "summary": (
            f"{len(numbers) - len(numbers_unbound)} of {len(numbers)} "
            f"claim(s) bound; {len(markers) - len(markers_unresolved)} of "
            f"{len(markers)} marker(s) resolved."),
    }
