"""Pre-commit hook: refuse to commit data, model artifacts or environment files.

Patient data, synthetic or not, belongs in ``MEDGRAPH_DATA_DIR`` outside the repository.
Small hand-written fixtures under ``tests/fixtures/`` and documents under ``docs/`` are allowed.
Standard library only: pre-commit runs it in its own environment.
"""

import sys
from pathlib import PurePosixPath

BLOCKED_DIRS = ("data/", "runs/")
ALLOWED_PREFIXES = ("tests/fixtures/", "docs/")
BLOCKED_SUFFIXES = (
    ".ndjson",
    ".csv",
    ".tsv",
    ".gz",
    ".zip",
    ".parquet",
    ".feather",
    ".h5",
    ".hdf5",
    ".npy",
    ".npz",
    ".pt",
    ".pth",
    ".ckpt",
    ".safetensors",
    ".pkl",
    ".pickle",
    ".joblib",
    ".jar",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pdf",
)


def violation(path: str) -> str | None:
    """Why ``path`` (relative to the repository root) must not be committed, or ``None``."""
    posix = path.replace("\\", "/")
    name = PurePosixPath(posix).name
    if posix.startswith(BLOCKED_DIRS):
        return "inside a data directory"
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return "environment file (may hold secrets or local paths)"
    if posix.startswith(ALLOWED_PREFIXES):
        return None
    if name.lower().endswith(BLOCKED_SUFFIXES):
        return "data or model file type"
    return None


def main(paths: list[str]) -> int:
    found = [(path, reason) for path in paths if (reason := violation(path))]
    for path, reason in found:
        print(f"forbid-data-files: {path}: {reason}", file=sys.stderr)
    if found:
        print(
            "Keep data in MEDGRAPH_DATA_DIR, outside the repository. Small synthetic fixtures "
            "go under tests/fixtures/.",
            file=sys.stderr,
        )
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
