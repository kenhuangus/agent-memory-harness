.PHONY: help typecheck test test-daydream test-redaction install-dev

help:
	@echo "Targets:"
	@echo "  install-dev    - pip install eval with daydream + dev extras"
	@echo "  typecheck      - mypy --strict on memeval/dreaming/ (ADR-dreaming-010)"
	@echo "  test           - run the full pytest suite"
	@echo "  test-daydream  - run only the dreaming-domain tests"
	@echo "  test-redaction - run only the redaction tests"

install-dev:
	pip install -e 'eval[daydream,dev]'

typecheck:
	# strict-typecheck scope: production dreaming code only — the redaction
	# module (ADR-dreaming-010), llm.py (ADR-dreaming-006), events.py
	# (ADR-dreaming-009). Tests + worker.py are intentionally outside
	# --strict scope (test code is less type-strict by convention).
	cd eval && python -m mypy --strict \
	    memeval/dreaming/redaction/ \
	    memeval/dreaming/llm.py \
	    memeval/dreaming/events.py

test:
	cd eval && python -m pytest

test-daydream:
	cd eval && python -m pytest memeval/dreaming/tests/ -v

test-redaction:
	cd eval && python -m pytest memeval/dreaming/tests/test_redaction*.py -v
