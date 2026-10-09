# Entry points. Everything runs through uv, so the locked environment is used.
# Extra arguments: make synthea-dev ARGS=--force

.PHONY: setup lint format typecheck test test-synthea check synthea-pilot synthea-dev profile \
	normalization extract-rules extract-llm graphs flags knowledge index knowledge-report view report review

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

# Follow-up rules for every stored patient (needs make graphs) -> docs/data/synthea-<cohort>-flags.md
flags:
	uv run python scripts/evaluate_flags.py $(COHORT) $(ARGS)

# Knowledge store (Phase 4): pinned guideline documents and the cohort's DailyMed labels in
# $MEDGRAPH_DATA_DIR/knowledge. ARGS=--lock chooses the labels again; ARGS=--offline verifies.
knowledge:
	uv run python scripts/fetch_knowledge.py $(COHORT) $(ARGS)

# Retrieval index: passages + BM25 in seconds. Embeddings are a local-model run, so they go
# through llm_run.sh: make index ARGS=--embed OLLAMA_MODELS=/Volumes/T7/ollama-models
index:
ifneq (,$(findstring --embed,$(ARGS)))
	scripts/dev/llm_run.sh index-$(COHORT) uv run python -u scripts/build_index.py $(COHORT) $(ARGS)
else
	uv run python scripts/build_index.py $(COHORT) $(ARGS)
endif

# Knowledge report (quotes, labels, index, retrieval checks) -> docs/data/knowledge-<cohort>.md.
# The dense retrieval checks need the local embedding model, so it runs through llm_run.sh.
knowledge-report:
	scripts/dev/llm_run.sh knowledge-report-$(COHORT) uv run python -u scripts/knowledge_report.py $(COHORT) $(ARGS)

# One patient's page, for example: make view PATIENT=b475e58b (no PATIENT: suggestions)
view:
	uv run python scripts/view_patient.py $(COHORT) $(PATIENT) $(ARGS)

# Technical reports: figures from the saved metrics, then the PDFs (docs/reports/build/).
report:
	uv run python scripts/report_figures.py
	cd docs/reports && latexmk -lualatex -interaction=nonstopmode -halt-on-error -outdir=build \
		medgraph-phase0-1-report.tex medgraph-phase1e-2-addendum.tex

# Re-run the review notebook in place (needs the T7 mounted). nbconvert is in the notebook
# group, so this runs in exactly the environment of the notebook's .venv kernel.
review:
	uv run jupyter nbconvert --to notebook --execute --inplace notebooks/review.ipynb
