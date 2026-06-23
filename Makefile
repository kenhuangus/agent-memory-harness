.PHONY: help typecheck test test-daydream test-redaction install-dev

help:
	@echo "Targets:"
	@echo "  install-dev    - pip install eval with daydream + dev extras"
	@echo "  typecheck      - mypy --strict on memeval/dreaming/ (ADR-dreaming-010)"
	@echo "  test           - run the full pytest suite"
	@echo "  test-daydream  - run only the dreaming-domain tests"
	@echo "  test-redaction - run only the redaction tests"
	@echo ""
	@echo "Pipeline (not a make target — it takes flags, which make handles poorly):"
	@echo "  memeval-pipeline --sequence pytest-dev_pytest_sequence --limit 3   # after install-dev"
	@echo "  (interactive by default; add --yes for non-interactive)"

install-dev:
	pip install -e 'eval[daydream,dev]'

typecheck:
	# strict-typecheck scope: production dreaming code only — the redaction
	# module (ADR-dreaming-010), llm.py (ADR-dreaming-006), events.py
	# (ADR-dreaming-009), plus the PR4 engine modules (engine.py + _state.py
	# + _extract.py + prompts.py per plan-v2 §3). Tests + worker.py are
	# intentionally outside --strict scope (test code is less type-strict
	# by convention).
	cd eval && python -m mypy --strict \
	    memeval/dreaming/redaction/ \
	    memeval/dreaming/llm.py \
	    memeval/dreaming/events.py \
	    memeval/dreaming/engine.py \
	    memeval/dreaming/_state.py \
	    memeval/dreaming/_extract.py \
	    memeval/dreaming/prompts.py

test:
	cd eval && python -m pytest

test-daydream:
	cd eval && python -m pytest memeval/dreaming/tests/ -v

test-redaction:
	cd eval && python -m pytest memeval/dreaming/tests/test_redaction*.py -v
