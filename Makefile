.PHONY: help setup pipeline typecheck test test-daydream test-redaction

# Everything runs through `uv` against a project-local .venv on Python 3.13 — no
# bare pip/python, no manual activation. `uv run` auto-uses ./.venv.
VENV := .venv
UVRUN := uv run --no-project

help:
	@echo "Targets:"
	@echo "  setup          - create .venv (Python 3.13) + install eval + plugin (uv)"
	@echo "  pipeline       - run the 5-stage SWE-Bench-CL pipeline (ARGS='--yes ...' to override)"
	@echo "  test           - run the full pytest suite"
	@echo "  typecheck      - mypy --strict on memeval/dreaming/ (ADR-dreaming-010)"
	@echo "  test-daydream  - run only the dreaming-domain tests"
	@echo "  test-redaction - run only the redaction tests"

# One-command setup: idempotent. Creates ./.venv on Python 3.13 if missing, then
# installs the harness (with dreaming + dev extras) and the plugin (for plugin-real).
setup:
	@test -d $(VENV) || uv venv --python 3.13 $(VENV)
	uv pip install -e 'eval[claudecode,daydream,hf,dev]'
	uv pip install --no-deps -e plugin
	@echo "✓ setup complete — run: make pipeline"

# Run the 5-stage SWE-Bench-CL pipeline against the live cookbook-memory plugin.
# Defaults to a small interactive run; override with ARGS, e.g.:
#   make pipeline ARGS="--yes --sequence django_django_sequence --limit 20 --budget-usd 20"
pipeline:
	$(UVRUN) memeval-pipeline $(ARGS)

typecheck:
	# strict-typecheck scope: production dreaming code only — the redaction
	# module (ADR-dreaming-010), llm.py (ADR-dreaming-006), events.py
	# (ADR-dreaming-009), plus the PR4 engine modules (engine.py + _state.py
	# + _extract.py + prompts.py per plan-v2 §3). Tests + worker.py are
	# intentionally outside --strict scope (test code is less type-strict
	# by convention).
	cd eval && $(UVRUN) python -m mypy --strict \
	    memeval/dreaming/redaction/ \
	    memeval/dreaming/llm.py \
	    memeval/dreaming/events.py \
	    memeval/dreaming/engine.py \
	    memeval/dreaming/_state.py \
	    memeval/dreaming/_extract.py \
	    memeval/dreaming/prompts.py

test:
	cd eval && $(UVRUN) python -m pytest

test-daydream:
	cd eval && $(UVRUN) python -m pytest memeval/dreaming/tests/ -v

test-redaction:
	cd eval && $(UVRUN) python -m pytest memeval/dreaming/tests/test_redaction*.py -v
