"""File helpers shared by ingestion and data scripts.

In the reference setup data lives on an exFAT volume, where macOS writes AppleDouble
companions (``._name``) that look like data files but hold binary metadata. Directory scans go
through :func:`iter_data_files`, which skips them along with every other hidden path.
"""

import hashlib
import os
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path


def iter_data_files(root: Path, pattern: str = "*") -> Iterator[Path]:
    """Yield regular files under ``root`` matching ``pattern``, in sorted order.

    Hidden files and anything inside hidden directories (names starting with ``.``, which
    includes ``._*`` AppleDouble files and ``.DS_Store``) are skipped.
    """
    for path in sorted(root.rglob(pattern)):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            yield path


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file's bytes."""
    with path.open("rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def remove_tree(root: Path) -> None:
    """Delete a directory tree, coping with two quirks of macOS exFAT volumes.

    Deleting a file also deletes its ``._`` companion, so a later delete finds nothing; and
    names with non-ASCII characters are listed in NFD form but can only be deleted by their
    NFC spelling. ``shutil.rmtree`` fails on both.
    """
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        base = Path(dirpath)
        for name in filenames:
            _remove(base / name, Path.unlink)
        for name in dirnames:
            _remove(base / name, Path.rmdir)
    _remove(root, Path.rmdir)


def _remove(path: Path, remove: Callable[[Path], None]) -> None:
    nfc = Path(unicodedata.normalize("NFC", str(path)))
    for candidate in dict.fromkeys((path, nfc)):
        try:
            remove(candidate)
        except FileNotFoundError:
            continue
        return
    # Neither spelling exists: already deleted, e.g. an AppleDouble companion. If the entry
    # does still exist, removing its parent directory fails loudly.


def content_digest(file_hashes: Mapping[str, str]) -> str:
    """Order-independent digest of a set of files, given ``relative POSIX path -> SHA-256``.

    Two directories have the same digest exactly when they hold the same paths with the same
    bytes, so it is a one-line check that a regenerated dataset is identical.
    """
    digest = hashlib.sha256()
    for rel_path in sorted(file_hashes):
        digest.update(f"{rel_path}\0{file_hashes[rel_path]}\n".encode())
    return digest.hexdigest()
