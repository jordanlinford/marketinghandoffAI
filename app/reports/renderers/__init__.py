"""
Three render strategies over the ONE intelligence object. Each
strategy SELECTS a different cut of the intelligence and frames it
for a specific audience. Renderers MUST NOT reimplement aggregation —
they import from `app.reports.intelligence` only via the engine's
return value, never directly.

If a renderer ever degenerates into "the same content with a different
heading," the build has failed; smoke test #4 (load-bearing) asserts
the three audiences produce materially different drafts.
"""
from app.reports.renderers.board import render_board  # noqa: F401
from app.reports.renderers.buyer_guide import render_buyer_guide  # noqa: F401
from app.reports.renderers.ceo_weekly import render_ceo_weekly  # noqa: F401
from app.reports.renderers.sales_leadership import render_sales_leadership  # noqa: F401
from app.reports.renderers.solution_guide import render_solution_guide  # noqa: F401
from app.reports.renderers.whitepaper import render_whitepaper  # noqa: F401

# Registry the agent dispatches through. New audiences hook in here.
# Anchor-class renderers (whitepaper, buyer's guide, solution guide)
# co-register here per docs/content-architecture.md Level 3 — they are
# new render strategies over the SAME intelligence + ledger, NOT new
# generators or new content pipelines.
RENDERERS = {
    "board": render_board,
    "ceo_weekly": render_ceo_weekly,
    "sales_leadership": render_sales_leadership,
    "whitepaper": render_whitepaper,
    "buyer_guide": render_buyer_guide,
    "solution_guide": render_solution_guide,
}
