"""Per-patient knowledge graph, timeline, store and viewer.

``schema`` defines node and edge kinds; ``build`` turns a record (and its lab reports) into a
graph deterministically; ``check`` holds the invariants; ``timeline`` reads lab series, events
and data notes off a graph; ``store`` saves graphs to SQLite; ``view`` renders one patient as a
self-contained HTML page. See docs/decisions/0005-patient-graph.md.
"""
