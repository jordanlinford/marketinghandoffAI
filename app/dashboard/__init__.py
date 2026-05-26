"""
Analytics dashboard package.

The dashboard is a RECEPTACLE: users upload the reports their tools already
export (GA / SEMrush / LinkedIn Ads / ...) and we normalize each row into the
generic `metric_points` table. The curated funnel + production lane views and
the evidence-attached suggestions compose on top.

We deliberately do not connect to live GA/SEMrush/LinkedIn APIs (out of
scope for v1) and we never claim baseline/untagged rows as something the
system drove — the dashboard story is "trend before us → what we produced
→ trend after," with attribution ONLY where the UTM join connects.
"""
