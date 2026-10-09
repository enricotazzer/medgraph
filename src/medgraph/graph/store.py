"""SQLite store for patient graphs.

One file per cohort, with tables ``graphs``, ``nodes`` and ``edges``. Attributes are stored as
JSON, and each graph is written in a single transaction that replaces the patient's previous
graph. Foreign keys make the database refuse an edge whose endpoint is not a node. A loaded
graph is identical to the saved one: same attributes, same node and edge order, same
:func:`graph_digest`.

A ``rules`` table holds rule results (``medgraph.rules``) per patient, ruleset and evaluation
date, as JSON. They are derived data, recomputed whenever the rules change.
"""

import hashlib
import json
import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from medgraph.graph.schema import SCHEMA_VERSION, PatientGraph

_TABLES = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS graphs (
    graph_id INTEGER PRIMARY KEY,
    patient_id TEXT NOT NULL UNIQUE,
    attrs TEXT NOT NULL,
    digest TEXT NOT NULL,
    n_nodes INTEGER NOT NULL,
    n_edges INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS nodes (
    graph_id INTEGER NOT NULL REFERENCES graphs (graph_id),
    node_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    attrs TEXT NOT NULL,
    PRIMARY KEY (graph_id, node_id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS edges (
    graph_id INTEGER NOT NULL,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    kind TEXT NOT NULL,
    seq INTEGER NOT NULL,
    attrs TEXT NOT NULL,
    PRIMARY KEY (graph_id, src, dst, kind),
    FOREIGN KEY (graph_id, src) REFERENCES nodes (graph_id, node_id),
    FOREIGN KEY (graph_id, dst) REFERENCES nodes (graph_id, node_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS nodes_by_kind ON nodes (kind);
CREATE TABLE IF NOT EXISTS rules (
    patient_id TEXT NOT NULL,
    ruleset TEXT NOT NULL,
    as_of TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (patient_id, ruleset, as_of)
);
"""


class StoreError(RuntimeError):
    """The store holds something this code can't read, or a graph that isn't there."""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def graph_digest(graph: PatientGraph) -> str:
    """SHA-256 over a graph's attributes, nodes and edges, in order."""
    digest = hashlib.sha256(_json(graph.graph).encode())
    for node, attrs in graph.nodes(data=True):
        digest.update(b"\nN" + _json([node, attrs]).encode())
    for u, v, kind, attrs in graph.edges(keys=True, data=True):
        digest.update(b"\nE" + _json([u, v, kind, attrs]).encode())
    return digest.hexdigest()


class GraphStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA foreign_keys = ON")
        with self.db:
            self.db.executescript(_TABLES)
            self.db.execute(
                "INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,)
            )
        stored = self.meta()["schema_version"]
        if stored != SCHEMA_VERSION:
            self.db.close()
            raise StoreError(f"{path} holds {stored} graphs; this code reads {SCHEMA_VERSION}")

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    def meta(self) -> dict[str, str]:
        return dict(self.db.execute("SELECT key, value FROM meta").fetchall())

    def set_meta(self, values: dict[str, str]) -> None:
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", values.items())

    def save(self, graph: PatientGraph) -> str:
        """Replace the patient's graph in one transaction; returns its digest."""
        patient_id = str(graph.graph["patient"]["id"])
        digest = graph_digest(graph)
        with self.db:
            old = self.db.execute(
                "SELECT graph_id FROM graphs WHERE patient_id = ?", (patient_id,)
            ).fetchone()
            if old is not None:
                for table in ("edges", "nodes", "graphs"):
                    self.db.execute(f"DELETE FROM {table} WHERE graph_id = ?", old)
            graph_id = self.db.execute(
                "INSERT INTO graphs (patient_id, attrs, digest, n_nodes, n_edges) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    patient_id,
                    _json(graph.graph),
                    digest,
                    graph.number_of_nodes(),
                    graph.number_of_edges(),
                ),
            ).lastrowid
            self.db.executemany(
                "INSERT INTO nodes VALUES (?, ?, ?, ?, ?)",
                (
                    (graph_id, node, seq, graph.nodes[node]["kind"], _json(graph.nodes[node]))
                    for seq, node in enumerate(graph.nodes)
                ),
            )
            self.db.executemany(
                "INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?)",
                (
                    (graph_id, u, v, kind, seq, _json(attrs))
                    for seq, (u, v, kind, attrs) in enumerate(graph.edges(keys=True, data=True))
                ),
            )
        return digest

    def load(self, patient_id: str) -> PatientGraph:
        row = self.db.execute(
            "SELECT graph_id, attrs, digest FROM graphs WHERE patient_id = ?", (patient_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"no graph for patient {patient_id} in {self.path}")
        graph_id, graph_attrs, digest = row
        graph = PatientGraph()
        graph.graph.update(json.loads(graph_attrs))
        for node, attrs in self.db.execute(
            "SELECT node_id, attrs FROM nodes WHERE graph_id = ? ORDER BY seq", (graph_id,)
        ):
            graph.add_node(node, **json.loads(attrs))
        for u, v, kind, attrs in self.db.execute(
            "SELECT src, dst, kind, attrs FROM edges WHERE graph_id = ? ORDER BY seq",
            (graph_id,),
        ):
            graph.add_edge(u, v, key=kind, **json.loads(attrs))
        if graph_digest(graph) != digest:
            raise StoreError(f"graph for patient {patient_id} does not match its digest")
        return graph

    def save_rules(self, patient_id: str, ruleset: str, as_of: str, payload: str) -> None:
        """Store one patient's rule results (JSON), replacing any for the same key."""
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO rules VALUES (?, ?, ?, ?)",
                (patient_id, ruleset, as_of, payload),
            )

    def load_rules(self, patient_id: str, ruleset: str, as_of: str) -> str | None:
        row = self.db.execute(
            "SELECT payload FROM rules WHERE patient_id = ? AND ruleset = ? AND as_of = ?",
            (patient_id, ruleset, as_of),
        ).fetchone()
        return str(row[0]) if row else None

    def clear_rules(self, ruleset: str, as_of: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM rules WHERE ruleset = ? AND as_of = ?", (ruleset, as_of))

    def digests(self) -> dict[str, str]:
        """Patient ID to graph digest, sorted by patient ID."""
        return dict(
            self.db.execute("SELECT patient_id, digest FROM graphs ORDER BY patient_id").fetchall()
        )

    def nodes_of_kind(self, kind: str) -> list[tuple[str, str, dict[str, Any]]]:
        """``(patient_id, node_id, attrs)`` of every stored node of one kind."""
        return [
            (patient, node, json.loads(attrs))
            for patient, node, attrs in self.db.execute(
                "SELECT g.patient_id, n.node_id, n.attrs FROM nodes AS n "
                "JOIN graphs AS g USING (graph_id) WHERE n.kind = ? ORDER BY g.patient_id, n.seq",
                (kind,),
            )
        ]


def cohort_digest(digests: dict[str, str]) -> str:
    """One digest for a whole store, from its per-patient digests."""
    return hashlib.sha256(
        "".join(f"{p}\0{d}\n" for p, d in sorted(digests.items())).encode()
    ).hexdigest()
