.PHONY: help install test lint format format-check typecheck check transcripts-check build

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies
	uv sync

test: ## Run the tests (offline), transcript tests included
	uv run pytest -q

lint: ## Lint
	uv run ruff check .

format: ## Format
	uv run ruff format src tests scripts examples

format-check: ## Check formatting
	uv run ruff format --check src tests scripts examples

typecheck: ## Type check (the explicit -p avoids picking up a config from a parent directory)
	uv run pyright -p pyproject.toml

check: lint format-check typecheck test ## Everything

transcripts-check: ## Compare the library with the stored transcripts, without writing
	uv run python scripts/record_transcripts.py --check

build: ## Build the wheel and sdist
	uv build
