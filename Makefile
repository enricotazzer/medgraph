# Entry points. Everything runs through uv, so the locked environment is used.
# Extra arguments: make synthea-dev ARGS=--force

.PHONY: setup lint format typecheck test test-synthea check synthea-pilot synthea-dev profile \
	normalization review

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

# Re-run the review notebook in place (needs the T7 mounted).
review:
	uv run --with nbconvert jupyter nbconvert --to notebook --execute --inplace notebooks/review.ipynb
