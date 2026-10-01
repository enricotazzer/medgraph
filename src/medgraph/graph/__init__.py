"""Per-patient knowledge graph and timeline.

Nodes are conditions, medications, observations, procedures and encounters; edges are typed
(treated_by, monitored_by, measured_at, occurred_during, temporal order). The graph is built
deterministically from normalized records.
"""
