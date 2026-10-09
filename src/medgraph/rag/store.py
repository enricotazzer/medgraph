"""Where the knowledge store keeps its files, and the manifest that says where each came from.

``$MEDGRAPH_DATA_DIR/knowledge/``:
- ``documents/<id>.<pdf|html>``: guidelines and reference pages (``documents.yaml``);
- ``labels/<set id>/v<version>.xml``: DailyMed labels, one file per version, never replaced;
- ``manifest.json``: for every stored file, its URL, SHA-256, size, licence and the time it was
  first retrieved.

Nothing here is committed: guideline licences forbid or restrict redistribution, and NLM does
not vouch for the copyright of drug labels.
"""

import datetime as dt
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from medgraph.rag.fetch import write_atomic


class ManifestEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str  # "document" or "label"
    url: str
    sha256: str
    bytes: int
    licence: str
    retrieved: dt.datetime


class KnowledgeStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.documents_dir = root / "documents"
        self.labels_dir = root / "labels"
        self.manifest_path = root / "manifest.json"

    def document_path(self, filename: str) -> Path:
        return self.documents_dir / filename

    def label_path(self, relative: str) -> Path:
        return self.labels_dir / relative

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def load_manifest(self) -> dict[str, ManifestEntry]:
        if not self.manifest_path.exists():
            return {}
        raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return {path: ManifestEntry.model_validate(entry) for path, entry in raw.items()}

    def save_manifest(self, manifest: dict[str, ManifestEntry]) -> None:
        data = {path: manifest[path].model_dump(mode="json") for path in sorted(manifest)}
        write_atomic(self.manifest_path, (json.dumps(data, indent=1) + "\n").encode())

    @staticmethod
    def record(manifest: dict[str, ManifestEntry], relative: str, entry: ManifestEntry) -> None:
        """Add or update an entry, keeping the first retrieval time of an unchanged file."""
        old = manifest.get(relative)
        if old is not None and old.sha256 == entry.sha256:
            entry = entry.model_copy(update={"retrieved": old.retrieved})
        manifest[relative] = entry
