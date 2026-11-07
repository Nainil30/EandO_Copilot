# Convenience targets (Linux/macOS/WSL/Git Bash). PowerShell equivalents are in the README.
PY ?= python

.PHONY: install db setup api ui mcp test lint fmt eval eval-api up down clean

install:      ## install runtime + dev deps
	$(PY) -m pip install -r requirements-dev.txt

db:           ## start postgres only
	docker compose up -d postgres

setup: db     ## schema + synthetic data + knowledge base
	$(PY) -m scripts.bootstrap

api:          ## run API with hot reload
	$(PY) -m uvicorn app.api.main:app --reload --port 8000

ui:           ## run Streamlit UI
	$(PY) -m streamlit run ui/streamlit_app.py

mcp:          ## run MCP server over stdio
	$(PY) -m app.mcp_server

test:         ## offline unit tests (fake LLM, no DB)
	$(PY) -m pytest

lint:
	$(PY) -m ruff check . && $(PY) -m ruff format --check app tests evals scripts

fmt:
	$(PY) -m ruff check . --fix && $(PY) -m ruff format app tests evals scripts

eval:         ## run golden-set evals in-process (needs DB + KB)
	$(PY) -m evals.run_evals --sleep-ms 300

eval-api:     ## run evals against a running API
	$(PY) -m evals.run_evals --api http://127.0.0.1:8000

up:           ## full stack in docker
	docker compose up -d --build

down:
	docker compose down

clean:
	rm -rf .chroma .cache .pytest_cache .ruff_cache reports
