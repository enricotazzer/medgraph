# 0001: Tooling, data layout and reproducible Synthea cohorts

- Status: accepted
- Date: 2026-09-30

## Context

Phase 0 needs a project skeleton, a place for data that must never be committed, and a synthetic cohort that anyone can regenerate exactly. The development machine is an Apple M3 with 16 GB RAM. Its internal disk has about 18 GiB free; an external SSD (exFAT, about 600 GiB free) holds the datasets.

## Decision

- **Tooling:** Python 3.12 (broadest wheel support for the torch/PyG work in Phase 6), uv with a `src/` layout, ruff, `mypy --strict` with the pydantic plugin, pytest, and pre-commit. ruff and mypy run through uv, so hooks use the same versions as `uv.lock`.
- **Code and data are split.** The repository lives on the internal APFS disk. Data lives on the external SSD at `/Volumes/T7/datasets/medgraph`, set through `MEDGRAPH_DATA_DIR`. exFAT has no journaling and is slow with many small files, so it is a poor home for `.git` and `.venv`, but it is fine for data files.
- **exFAT handling:** macOS writes a `._*` AppleDouble companion next to every file there. Deleting a file removes its companion, and names with non-ASCII characters are listed in NFD but can only be deleted by their NFC spelling. All scans go through `iter_data_files`, which skips hidden files, and all deletions go through `remove_tree`.
- **Synthea v4.0.0**, run as a local JAR on JDK 21 (no Docker needed). The JAR is verified against the SHA-256 digest GitHub publishes for the release asset. These inputs are pinned:
  - seed and clinician seed;
  - reference date **and** end date (`-r` and `-e` together): in v4.0.0 the simulation end time is initialized from the system clock and `-r` does not move it;
  - JVM timezone (`UTC`) and locale (`en-US`): Synthea formats timestamps in the JVM's default timezone.
- **Guarding against committed data:** `.gitignore` covers the default data location, and a pre-commit hook blocks data and model file types outside `tests/fixtures/` and `docs/`.

## Evidence

- Regenerating the 10-patient pilot once with the machine timezone (Europe/Rome) and once with `TZ=America/New_York` gave the same content digest (`0eaba7ec…`), with every timestamp at offset `+00:00`. Before the JVM timezone was pinned, timestamps carried `+01:00`/`+02:00` offsets.
- With `-e` set, clinical events stop before the end date; the latest encounter in dev-1000 is 2025-12-31. Across all 1,148 patients, the only later dates are claim billing periods, device expiry dates, and `Provenance.recorded`, which overshoots by at most 6 days (under one simulation time step).

## Consequences

- Data paths are always taken from settings. Tests use fixtures and never need the external drive.
- A different Synthea release means a new config (new version and digest) and a new cohort name. Existing cohorts stay reproducible.
- Later decisions each get their own ADR: persistence (Phase 2), drug–drug interaction source (Phase 4), frontend stack (Phase 5), and local LLM model (Phase 1).
