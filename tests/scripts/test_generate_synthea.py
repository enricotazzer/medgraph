import datetime as dt
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import generate_synthea as gen

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS = sorted((REPO_ROOT / "configs" / "synthea").glob("*.yaml"))


def make_config(**overrides: Any) -> gen.CohortConfig:
    data: dict[str, Any] = {
        "name": "test-cohort",
        "description": "unit test",
        "synthea": {
            "version": "v4.0.0",
            "jar_url": "https://example.org/synthea-with-dependencies.jar",
            "jar_sha256": "0" * 64,
        },
        "population": 5,
        "seed": 42,
        "clinician_seed": 7,
        "reference_date": dt.date(2026, 1, 1),
        "properties": {"exporter.fhir.export": True, "exporter.years_of_history": 10},
    }
    data.update(overrides)
    return gen.CohortConfig.model_validate(data)


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.name)
def test_committed_configs_are_valid(path: Path) -> None:
    cfg = gen.load_config(path)
    assert cfg.name == path.stem


def test_committed_configs_share_release_seeds_and_settings() -> None:
    configs = [gen.load_config(p) for p in CONFIGS]
    assert len(configs) >= 2
    shared = {
        (
            c.synthea,
            c.seed,
            c.clinician_seed,
            c.reference_date,
            c.state,
            tuple(sorted(c.properties.items())),
        )
        for c in configs
    }
    assert len(shared) == 1, "pilot and dev cohorts must differ only in name and population"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"population": 0}, "greater than 0"),
        ({"name": "Bad Name"}, "should match pattern"),
        ({"surprise": 1}, "Extra inputs are not permitted"),
        ({"reference_date": "2026-13-01"}, "month"),
        ({"properties": {"exporter.baseDirectory": "/tmp"}}, "set by the script"),
        ({"properties": {"not a key": True}}, "not a Synthea property name"),
    ],
)
def test_config_rejects_invalid_values(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        make_config(**overrides)


def test_config_rejects_malformed_checksum() -> None:
    with pytest.raises(ValidationError, match="should match pattern"):
        make_config(
            synthea={
                "version": "v4.0.0",
                "jar_url": "https://example.org/x.jar",
                "jar_sha256": "abc",
            }
        )


def test_build_command_pins_seeds_dates_and_jvm_environment() -> None:
    cfg = make_config()
    cmd = gen.build_command(cfg, Path("/tools/synthea.jar"), Path("/out/cohort"))

    assert cmd == gen.build_command(cfg, Path("/tools/synthea.jar"), Path("/out/cohort"))
    assert cmd[: 1 + len(gen.JVM_OPTIONS)] == ["java", *gen.JVM_OPTIONS]
    assert "-Duser.timezone=UTC" in cmd

    def value_after(flag: str) -> str:
        return cmd[cmd.index(flag) + 1]

    assert value_after("-s") == "42"
    assert value_after("-cs") == "7"
    assert value_after("-r") == value_after("-e") == "20260101"
    assert value_after("-p") == "5"
    assert "--exporter.baseDirectory=/out/cohort" in cmd
    overrides = [arg for arg in cmd if arg.startswith("--") and "baseDirectory" not in arg]
    assert overrides == ["--exporter.fhir.export=true", "--exporter.years_of_history=10"]
    assert cmd[-1] == "Massachusetts"


@pytest.mark.parametrize(
    ("line", "major"),
    [
        ('openjdk version "21.0.5" 2024-10-15 LTS', 21),
        ('openjdk version "17" 2021-09-14', 17),
        ('java version "1.8.0_311"', 8),
    ],
)
def test_parse_java_major(line: str, major: int) -> None:
    assert gen.parse_java_major(line) == major


def test_parse_java_major_rejects_unknown_output() -> None:
    with pytest.raises(ValueError, match="unrecognised"):
        gen.parse_java_major("something else")


def _release_for(content: bytes) -> gen.SyntheaRelease:
    return gen.SyntheaRelease(
        version="v4.0.0",
        jar_url="https://example.org/synthea-with-dependencies.jar",
        jar_sha256=hashlib.sha256(content).hexdigest(),
    )


def _no_network(*args: object, **kwargs: object) -> None:
    raise AssertionError("must not download when a verified JAR is cached")


def test_ensure_jar_uses_verified_cached_jar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _release_for(b"fake jar")
    jar = gen.jar_path(release, tmp_path)
    jar.parent.mkdir(parents=True)
    jar.write_bytes(b"fake jar")
    monkeypatch.setattr(gen.urllib.request, "urlopen", _no_network)
    assert gen.ensure_jar(release, tmp_path) == jar


def test_ensure_jar_rejects_tampered_cached_jar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _release_for(b"fake jar")
    jar = gen.jar_path(release, tmp_path)
    jar.parent.mkdir(parents=True)
    jar.write_bytes(b"tampered")
    monkeypatch.setattr(gen.urllib.request, "urlopen", _no_network)
    with pytest.raises(gen.ChecksumError, match="does not match pinned"):
        gen.ensure_jar(release, tmp_path)


def test_ensure_jar_discards_download_with_wrong_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _release_for(b"expected bytes")
    served = tmp_path / "served.jar"
    served.write_bytes(b"different bytes")
    monkeypatch.setattr(gen.urllib.request, "urlopen", lambda url, timeout: served.open("rb"))
    with pytest.raises(gen.ChecksumError):
        gen.ensure_jar(release, tmp_path)
    jar = gen.jar_path(release, tmp_path)
    assert not jar.exists()
    assert not jar.with_name(jar.name + ".part").exists()


def test_prepare_output_dir_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    out = tmp_path / "cohort"
    out.mkdir()
    (out / "MANIFEST.json").write_text("{}")
    with pytest.raises(FileExistsError, match="--force"):
        gen.prepare_output_dir(out, force=False)
    gen.prepare_output_dir(out, force=True)
    assert out.is_dir()
    assert not any(out.iterdir())


def test_summarize_fhir_output_counts_patients_and_skips_appledouble(
    tmp_path: Path, fhir_fixture_dir: Path
) -> None:
    fhir = tmp_path / "fhir"
    shutil.copytree(fhir_fixture_dir, fhir)
    clean = gen.summarize_fhir_output(fhir)
    (fhir / "._patient-a.json").write_bytes(b"\x00\x05\x16\x07binary")

    summary = gen.summarize_fhir_output(fhir)

    assert summary == clean
    assert summary["n_files"] == 3
    assert summary["n_patients"] == 2
    assert summary["n_deceased"] == 1
    assert summary["n_other_bundles"] == 1
    assert set(summary["files"]) == {"hospitalInformation.json", "patient-a.json", "patient-b.json"}


def test_summarize_fhir_output_digest_changes_with_content(
    tmp_path: Path, fhir_fixture_dir: Path
) -> None:
    fhir = tmp_path / "fhir"
    shutil.copytree(fhir_fixture_dir, fhir)
    before = gen.summarize_fhir_output(fhir)["content_digest"]
    bundle = json.loads((fhir / "patient-a.json").read_text())
    bundle["entry"][0]["resource"]["gender"] = "male"
    (fhir / "patient-a.json").write_text(json.dumps(bundle))
    assert gen.summarize_fhir_output(fhir)["content_digest"] != before


def test_dry_run_prints_command_without_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MEDGRAPH_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert gen.main([str(CONFIGS[0]), "--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert "-Duser.timezone=UTC" in printed
    assert " -e 20260101 " in printed
    assert list(tmp_path.iterdir()) == []
