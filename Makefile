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
	# v1 strict-typecheck scope is the redaction module per ADR-dreaming-010.
	# Expand to all of memeval/dreaming/ once worker.py + tests/ are typed.
	cd eval && python -m mypy --strict memeval/dreaming/redaction/

test:
	cd eval && python -m pytest

test-daydream:
	cd eval && python -m pytest memeval/dreaming/tests/ -v

test-redaction:
	cd eval && python -m pytest memeval/dreaming/tests/test_redaction*.py -v
