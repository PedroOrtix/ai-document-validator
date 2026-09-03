.PHONY: help sync test lint lint-fix eval eval-live run docker-build docker-up

help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

sync: ## Install and sync all project dependencies with uv
	uv sync --dev

test: ## Run the full pytest test suite (381 tests)
	uv run pytest

lint: ## Check code style and types with ruff
	uv run ruff check .

lint-fix: ## Auto-fix lint and import sorting issues
	uv run ruff check --fix .

eval: ## Run the evaluation harness on the credential-free OCR floor (78 fixtures, $0)
	uv run python -m eval.run --as-of 2026-09-03

eval-live: ## Run live multi-engine comparison (OCR vs SLM vs VLM, requires OPENROUTER_API_KEY)
	uv run python -m eval.run --lane ocr,slm,vlm --live --as-of 2026-09-03

run: ## Start the FastAPI service locally on port 8000 with auto-reload
	uv run uvicorn docvalidator.api.main:app --reload --port 8000

docker-build: ## Build the production Docker image with pre-downloaded ONNX weights
	docker compose build

docker-up: ## Run the service in Docker (credential-free local OCR mode by default)
	docker compose up
