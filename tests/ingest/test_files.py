import unicodedata
from pathlib import Path

import pytest

from medgraph.ingest import files
from medgraph.ingest.files import content_digest, iter_data_files, remove_tree, sha256_file

APPLEDOUBLE = b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        "


def test_iter_data_files_skips_hidden_and_appledouble_files(tmp_path: Path) -> None:
    (tmp_path / "b.json").write_text("{}")
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / "._a.json").write_bytes(APPLEDOUBLE)
    (tmp_path / ".DS_Store").write_bytes(b"\x00")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "c.json").write_text("{}")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "d.json").write_text("{}")

    found = [p.relative_to(tmp_path).as_posix() for p in iter_data_files(tmp_path, "*.json")]

    assert found == ["a.json", "b.json", "sub/d.json"]


def test_sha256_file_matches_known_digest(tmp_path: Path) -> None:
    path = tmp_path / "abc.txt"
    path.write_bytes(b"abc")
    assert sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_content_digest_ignores_order_but_not_content() -> None:
    forward = {"a.json": "11", "b.json": "22"}
    backward = {"b.json": "22", "a.json": "11"}
    assert content_digest(forward) == content_digest(backward)
    assert content_digest(forward) != content_digest({"a.json": "11", "b.json": "23"})
    assert content_digest(forward) != content_digest({"a.json": "11", "c.json": "22"})


def test_remove_tree_deletes_nested_tree(tmp_path: Path) -> None:
    root = tmp_path / "cohort"
    (root / "fhir").mkdir(parents=True)
    (root / "fhir" / "Jesús825.json").write_text("{}")
    (root / "MANIFEST.json").write_text("{}")
    remove_tree(root)
    assert not root.exists()


def test_remove_tree_copes_with_exfat_quirks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates macOS exFAT: companions vanish with their file; NFD names only unlink as NFC."""
    root = tmp_path / "cohort"
    root.mkdir()
    nfd_name = unicodedata.normalize("NFD", "Jesús825.json")
    (root / "plain.json").write_text("{}")
    (root / "._plain.json").write_bytes(APPLEDOUBLE)
    (root / nfd_name).write_text("{}")
    real_unlink = Path.unlink

    def exfat_unlink(path: Path, missing_ok: bool = False) -> None:
        if not unicodedata.is_normalized("NFC", str(path)):
            raise FileNotFoundError(str(path))
        companion = path.with_name("._" + path.name)
        real_unlink(path)
        if companion.exists():
            real_unlink(companion)

    monkeypatch.setattr(files.Path, "unlink", exfat_unlink)
    remove_tree(root)
    assert not root.exists()
