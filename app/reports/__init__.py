"""
Report Composer — the storytelling layer.

This package is the THIRD consumer of the substrate (after the content
engine and the campaign planner). It exists to turn the platform from a
production engine into a production + insight engine — the difference
between "we generate marketing content" and "we tell the story of how
marketing is doing."

THE LOAD-BEARING PRINCIPLE: one intelligence engine, three render
strategies. `build_report_intelligence()` produces a SINGLE structured
intelligence object; each audience renderer (`board`, `ceo_weekly`,
`sales_leadership`) selects a DIFFERENT cut of it and frames it for
that audience. Renderers MUST NOT reimplement aggregation. There is a
smoke test that the three audiences produce materially different
drafts; that test is load-bearing for the architecture.

Everything is deterministic at the structural level (aggregation,
ranking, pattern selection). LLM calls are confined to the renderers'
narrative copy. With no LLM key, the renderers fall back to a basic-
but-truthful deterministic render so the system is fully usable
without paid generation.
"""
from app.reports.intelligence import build_report_intelligence  # noqa: F401
