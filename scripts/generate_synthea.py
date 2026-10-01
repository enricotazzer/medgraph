"""Generate a reproducible Synthea cohort (FHIR R4) into ``MEDGRAPH_DATA_DIR``.

    uv run python scripts/generate_synthea.py configs/synthea/dev-1000.yaml [--out DIR] [--force]

Reproducibility rests on four pinned inputs: the Synthea release (checked by SHA-256), the
seed, the clinician seed and the reference date. The date is passed as both ``-r`` and ``-e``:
in Synthea v4.0.0 the simulation end time is initialised from the system clock and ``-r``
does not move it, so pinning ``-r`` alone still makes output depend on the day it is run.
The JVM timezone and locale are pinned too: Synthea formats timestamps in the JVM's default
timezone, so the same run on a machine set to Europe/Rome and on one set to UTC would differ.
``MANIFEST.json`` records these inputs with per-file hashes and a content digest, so a
regeneration can be checked for identity.

Synthea patients follow hand-written disease modules. Cohorts are for building and testing
the pipeline, never for medical claims.
"""

import argparse
import datetime as dt
import hashlib
import json
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from medgraph.ingest.files import content_digest, iter_data_files, remove_tree, sha256_file
from medgraph.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
MIN_JAVA_MAJOR = 17
MANIFEST_VERSION = 1
# Machine-independent output: timestamps in UTC, en-US number and date formatting.
JVM_OPTIONS = (
    "-Duser.timezone=UTC",
    "-Duser.language=en",
    "-Duser.country=US",
    "-Dfile.encoding=UTF-8",
)
# The script owns the output location; configs must not override it.
RESERVED_PROPERTIES = frozenset({"exporter.baseDirectory"})
PROPERTY_KEY = re.compile(r"[a-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+")

PropertyValue = bool | int | str


class SyntheaRelease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(pattern=r"^v\d+\.\d+\.\d+$")
    jar_url: HttpUrl
    jar_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CohortConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    description: str
    synthea: SyntheaRelease
    population: int = Field(gt=0)
    seed: int
    clinician_seed: int
    reference_date: dt.date
    state: str = "Massachusetts"
    properties: dict[str, PropertyValue] = Field(default_factory=dict)

    @field_validator("properties")
    @classmethod
    def _check_properties(cls, value: dict[str, PropertyValue]) -> dict[str, PropertyValue]:
        for key in value:
            if key in RESERVED_PROPERTIES:
                raise ValueError(f"{key} is set by the script, not by the config")
            if not PROPERTY_KEY.fullmatch(key):
                raise ValueError(f"not a Synthea property name: {key!r}")
        return value


class ChecksumError(RuntimeError):
    """A downloaded or cached file does not match its pinned SHA-256."""


def load_config(path: Path) -> CohortConfig:
    with path.open(encoding="utf-8") as fh:
        return CohortConfig.model_validate(yaml.safe_load(fh))


def format_property(value: PropertyValue) -> str:
    """Render a property value the way Synthea's config parser expects (lowercase booleans)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def build_command(cfg: CohortConfig, jar: Path, out_dir: Path, java: str = "java") -> list[str]:
    """Synthea command line; identical inputs always give the identical list."""
    date = cfg.reference_date.strftime("%Y%m%d")
    cmd = [
        java,
        *JVM_OPTIONS,
        "-jar",
        str(jar),
        "-s",
        str(cfg.seed),
        "-cs",
        str(cfg.clinician_seed),
        "-r",
        date,
        "-e",
        date,
        "-p",
        str(cfg.population),
        f"--exporter.baseDirectory={out_dir}",
    ]
    cmd += [f"--{key}={format_property(cfg.properties[key])}" for key in sorted(cfg.properties)]
    cmd.append(cfg.state)
    return cmd


def parse_java_major(version_line: str) -> int:
    """Major version from the first line of ``java -version`` (handles legacy ``1.8``)."""
    match = re.search(r'version "(\d+)(?:\.(\d+))?', version_line)
    if match is None:
        raise ValueError(f"unrecognised java -version output: {version_line!r}")
    major = int(match.group(1))
    if major == 1 and match.group(2) is not None:
        major = int(match.group(2))
    return major


def java_version_line(java: str) -> str:
    proc = subprocess.run([java, "-version"], capture_output=True, text=True, check=True)
    return (proc.stderr or proc.stdout).splitlines()[0].strip()


def verify_sha256(path: Path, expected: str) -> None:
    actual = sha256_file(path)
    if actual != expected:
        raise ChecksumError(f"{path}: SHA-256 {actual} does not match pinned {expected}")


def jar_path(release: SyntheaRelease, tools_dir: Path) -> Path:
    return tools_dir / "synthea" / release.version / "synthea-with-dependencies.jar"


def ensure_jar(release: SyntheaRelease, tools_dir: Path) -> Path:
    """Return the pinned Synthea JAR, downloading it once. Any hash mismatch is fatal."""
    jar = jar_path(release, tools_dir)
    if jar.exists():
        verify_sha256(jar, release.jar_sha256)
        return jar
    jar.parent.mkdir(parents=True, exist_ok=True)
    partial = jar.with_name(jar.name + ".part")
    with (
        urllib.request.urlopen(str(release.jar_url), timeout=60) as resp,
        partial.open("wb") as out,
    ):
        shutil.copyfileobj(resp, out)
    try:
        verify_sha256(partial, release.jar_sha256)
    except ChecksumError:
        partial.unlink()
        raise
    partial.replace(jar)
    return jar


def prepare_output_dir(out_dir: Path, force: bool) -> None:
    """Create an empty output directory; replacing an existing cohort needs ``force``."""
    if out_dir.exists() and any(out_dir.iterdir()):
        if not force:
            raise FileExistsError(f"{out_dir} already holds a cohort; pass --force to replace it")
        remove_tree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)


def git_state(repo: Path) -> dict[str, Any]:
    """Commit and dirty flag of the medgraph checkout (``None`` when unknown)."""

    def git(*args: str) -> str | None:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
        )
        return proc.stdout.strip() if proc.returncode == 0 else None

    status = git("status", "--porcelain")
    return {"commit": git("rev-parse", "HEAD"), "dirty": None if status is None else bool(status)}


def summarize_fhir_output(fhir_dir: Path) -> dict[str, Any]:
    """Per-file hashes, content digest and patient counts for an exported FHIR directory."""
    files: dict[str, str] = {}
    total_bytes = 0
    n_patients = n_deceased = n_other_bundles = 0
    for path in iter_data_files(fhir_dir, "*.json"):
        raw = path.read_bytes()
        files[path.relative_to(fhir_dir).as_posix()] = hashlib.sha256(raw).hexdigest()
        total_bytes += len(raw)
        bundle = json.loads(raw)
        patients = [
            entry["resource"]
            for entry in bundle.get("entry", [])
            if entry.get("resource", {}).get("resourceType") == "Patient"
        ]
        if not patients:
            n_other_bundles += 1
        n_patients += len(patients)
        n_deceased += sum(
            1 for p in patients if "deceasedDateTime" in p or p.get("deceasedBoolean") is True
        )
    return {
        "n_files": len(files),
        "total_bytes": total_bytes,
        "n_patients": n_patients,
        "n_deceased": n_deceased,
        "n_other_bundles": n_other_bundles,
        "content_digest": content_digest(files),
        "files": files,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a reproducible Synthea cohort.")
    parser.add_argument("config", type=Path, help="cohort config (configs/synthea/*.yaml)")
    parser.add_argument(
        "--out", type=Path, help="output directory (default: $MEDGRAPH_DATA_DIR/synthea/<name>)"
    )
    parser.add_argument("--force", action="store_true", help="replace an existing output dir")
    parser.add_argument("--java", default="java", help=f"java executable (JDK >= {MIN_JAVA_MAJOR})")
    parser.add_argument("--dry-run", action="store_true", help="print the command and exit")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    settings = Settings()
    tools_dir = settings.tools_dir.resolve()
    out_dir = (args.out or settings.synthea_dir / cfg.name).expanduser().resolve()
    cmd = build_command(cfg, jar_path(cfg.synthea, tools_dir), out_dir, args.java)
    if args.dry_run:
        print(shlex.join(cmd))
        return 0

    java_line = java_version_line(args.java)
    if parse_java_major(java_line) < MIN_JAVA_MAJOR:
        print(f"Synthea needs JDK >= {MIN_JAVA_MAJOR}; found: {java_line}", file=sys.stderr)
        return 1
    ensure_jar(cfg.synthea, tools_dir)
    prepare_output_dir(out_dir, args.force)

    print(f"Generating {cfg.name} ({cfg.population} patients) into {out_dir}", flush=True)
    started = dt.datetime.now(dt.UTC)
    t0 = time.monotonic()
    log_path = out_dir / "synthea.log"
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=out_dir, check=False)
    duration_s = round(time.monotonic() - t0, 1)
    if result.returncode != 0:
        print(f"Synthea exited with {result.returncode}; see {log_path}", file=sys.stderr)
        return result.returncode

    outputs = summarize_fhir_output(out_dir / "fhir")
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "cohort": cfg.name,
        "config": cfg.model_dump(mode="json"),
        "config_path": args.config.resolve().relative_to(REPO_ROOT).as_posix(),
        "config_sha256": sha256_file(args.config),
        "command": cmd,
        "java": java_line,
        "platform": platform.platform(),
        "medgraph_git": git_state(REPO_ROOT),
        "started_at": started.isoformat(timespec="seconds"),
        "duration_s": duration_s,
        "outputs": outputs,
    }
    manifest_path = out_dir / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", "utf-8")

    size_mb = outputs["total_bytes"] / 1e6
    print(
        f"Done in {duration_s:.0f}s: {outputs['n_patients']} patients "
        f"({outputs['n_deceased']} deceased), {outputs['n_files']} files, {size_mb:.0f} MB\n"
        f"content digest {outputs['content_digest']}\nmanifest {manifest_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
