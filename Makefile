.PHONY: install lint format test eval serve
install:  ## editable install with dev, postgres and api extras
	pip install -e ".[dev,postgres,api]"
lint:
	ruff check . && ruff format --check .
format:
	ruff check --fix . && ruff format .
test:
	pytest -q
eval:     ## offline eval with the deterministic heuristic backend
	PYTHONPATH=src python evals/run_eval.py
serve:
	uvicorn edi_triage.api:app --reload
