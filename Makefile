# Entry points. Everything runs through uv, so the locked environment is used.
# Extra arguments: make synthea-dev ARGS=--force

.PHONY: setup lint format typecheck test test-synthea check synthea-pilot synthea-dev profile \
	normalization extract-rules extract-llm graphs view report review

setup:
	uv sync
	uv run pre-commit install

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy

test:
	uv run pytest

# Needs the dev cohort in MEDGRAPH_DATA_DIR; about a minute.
test-synthea:
	uv run pytest --run-synthea -m synthea

check: lint typecheck test

synthea-pilot:
	uv run python scripts/generate_synthea.py configs/synthea/pilot-10.yaml $(ARGS)

synthea-dev:
	uv run python scripts/generate_synthea.py configs/synthea/dev-1000.yaml $(ARGS)

profile:
	uv run python scripts/profile_cohort.py dev-1000 $(ARGS)

normalization:
	uv run python scripts/check_normalization.py dev-1000 $(ARGS)

# Lab-report extraction evaluation, SPLIT=dev or test; configs/extraction/<name>.yaml.
SPLIT ?= dev
RULES ?= rules
LLM ?= qwen35-9b
export OLLAMA_MODELS

extract-rules:
	uv run python scripts/evaluate_extraction.py configs/extraction/$(RULES).yaml --split $(SPLIT) $(ARGS)

# Takes hours, so it runs in the background (see scripts/dev/llm_run.sh). For example:
#   make extract-llm SPLIT=dev ARGS="--formats text" OLLAMA_MODELS=/Volumes/T7/ollama-models
extract-llm:
	scripts/dev/llm_run.sh $(LLM)-$(SPLIT) uv run python scripts/evaluate_extraction.py \
		configs/extraction/$(LLM).yaml --split $(SPLIT) $(ARGS)

# Patient graphs: build, check and store every patient (about 10 min, 1.3 GB for dev-1000).
COHORT ?= dev-1000
graphs:
	uv run python scripts/build_graphs.py configs/graphs/$(COHORT).yaml $(ARGS)

# One patient's page, for example: make view PATIENT=b475e58b (no PATIENT: suggestions)
view:
	uv run python scripts/view_patient.py $(COHORT) $(PATIENT) $(ARGS)

# Technical report: figures from the saved metrics, then the PDF (docs/reports/build/).
report:
	uv run python scripts/report_figures.py
	cd docs/reports && latexmk -lualatex -interaction=nonstopmode -halt-on-error -outdir=build \
		medgraph-phase0-1-report.tex

# Re-run the review notebook in place (needs the T7 mounted).
review:
	uv run --with nbconvert jupyter nbconvert --to notebook --execute --inplace notebooks/review.ipynb
