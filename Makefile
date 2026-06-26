.PHONY: help setup install-claude-plugin pipeline viewer monitor typecheck test test-daydream test-redaction \
        vista-off vista-builtin vista-plugin-real vista-smoke

# Everything runs through `uv` against a project-local .venv on Python 3.13 — no
# bare pip/python, no manual activation. `uv run` auto-uses ./.venv.
VENV := .venv
UVRUN := uv run --no-project
CLAUDE ?= claude
VENV_BIN := $(abspath $(VENV))/bin
LOCAL_CLAUDE_BUNDLE := $(abspath build/claude-code/cookbook-memory)

help:
	@echo "Targets:"
	@echo "  setup          - create .venv (Python 3.13) + install eval + plugin (uv)"
	@echo "  install-claude-plugin - install plugin into the user's real Claude Code config"
	@echo "  pipeline       - run ONE stage over ONE sequence (ARGS='--yes ...' to override)"
	@echo "  viewer         - launch the router memory-inspector web UI (ARGS='--seed --open ...')"
	@echo "  monitor        - launch the live results monitor for in-flight benchmark runs (ARGS='--open ...')"
	@echo "  test           - run the full pytest suite"
	@echo "  typecheck      - mypy --strict on memeval/dreaming/ (ADR-dreaming-010)"
	@echo "  test-daydream  - run only the dreaming-domain tests"
	@echo "  test-redaction - run only the redaction tests"
	@echo "  vista-off          - VISTA no-memory baseline (SPLIT/LIMIT vars)"
	@echo "  vista-builtin      - VISTA Claude Code native memory arm"
	@echo "  vista-plugin-real  - VISTA cookbook_memory plugin arm (WORKERS var)"
	@echo "  vista-smoke        - quick plugin-real dev/limit-8 smoke run"

# One-command setup: idempotent. Creates ./.venv on Python 3.13 if missing, then
# installs the harness (with dreaming + dev extras + the swebench grader, the pipeline's
# DEFAULT --grader) and the plugin (for plugin-real).
setup:
	@test -d $(VENV) || uv venv --python 3.13 $(VENV)
	uv pip install -e 'eval[claudecode,daydream,hf,dev,swebench]'
	# Plugin installed --no-deps so its `agent-memory-eval @ git+…` dep doesn't clobber
	# the LOCAL editable eval above; install its `[mcp]` runtime (memory-cli's MCP server)
	# EXPLICITLY so plugin-real turns actually work — without it every plugin turn dies on
	# "memory-cli MCP runtime not available".
	uv pip install --no-deps -e plugin
	uv pip install 'mcp>=1.0'
	@echo "✓ setup complete — run: make pipeline"

# Install into the user's real Claude Code config, not the eval sandbox.
# Deliberately unsets CLAUDE_CONFIG_DIR for every claude command so this targets the
# local Claude Code installation a user normally runs.
install-claude-plugin:
	@test -d $(VENV) || uv venv --python 3.13 $(VENV)
	uv pip install -e 'eval[claudecode,daydream,hf,dev,swebench]'
	uv pip install --no-deps -e plugin
	uv pip install 'mcp>=1.0'
	$(UVRUN) memory-cli install-claude-plugin --bundle-dir "$(LOCAL_CLAUDE_BUNDLE)" --runtime-bin-dir "$(VENV_BIN)" --claude "$(CLAUDE)"

# Run ONE pipeline stage over ONE sequence against the live cookbook-memory plugin.
# Each invocation runs a single stage (base | plugin-blank | plugin-accum |
# plugin-dreamed | plugin-primed; default plugin-accum) over one --sequence of one --benchmark
# (swe_bench_cl or vista) against the persistent per-version memory substrate. For
# SWE-Bench-CL sequences the grading venv is built once per sequence and reused.
# Defaults to a small interactive run; override with ARGS, e.g.:
#   make pipeline ARGS="--yes --sequence django_django_sequence --limit 20 --budget-usd 20"
#   make pipeline ARGS="--yes --benchmark vista --sequence coding --stage plugin-accum"
pipeline:
	$(UVRUN) memeval-pipeline $(ARGS)

# Launch the router memory-inspector web UI to browse the memories the plugin saved
# during a run and evaluate how the router routed them. Defaults to the newest pipeline
# substrate (results/v*/_memory); override/extend with ARGS, e.g.:
#   make viewer ARGS="--seed --open"           # synthetic demo corpus + open browser
#   make viewer ARGS="--store /path/to/_memory --port 9000"
# Delegates to router_ui/run.sh, which uses the repo .venv and sets PYTHONPATH.
viewer:
	./router_ui/run.sh $(ARGS)

# Launch the live operator dashboard for in-flight benchmark runs. Auto-discovers
# every results/<run>/_memory/.cookbook-memory basedir, lets you switch between
# them via dropdown, and polls every 3s for live KPIs + charts + recent memories.
# Defaults bind to 127.0.0.1:8770; override/extend with ARGS, e.g.:
#   make monitor ARGS="--open"                       # open browser on start
#   make monitor ARGS="--port 9001 --results-root /alt/results"
# Delegates to results_monitor/run.sh, which uses the repo .venv.
monitor:
	./results_monitor/run.sh $(ARGS)

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

# --- VISTA benchmark runners ------------------------------------------------
# Faithful reproduction of the VISTA 97-test-split run via tools/run_vista.sh.
# Override the defaults per invocation, e.g.:
#   make vista-plugin-real SPLIT=test LIMIT=0 WORKERS=4
#   make vista-builtin SPLIT=test LIMIT=0
#   make vista-off SPLIT=test LIMIT=0
# plugin-real requires OPENROUTER_API_KEY (WSL claude-managed block) for the
# daydream write path and a logged-in MEMEVAL_SANDBOX_CONFIG_DIR for auth.
SPLIT   ?= test
LIMIT   ?= 0
WORKERS ?= 4

vista-off:
	bash tools/run_vista.sh off $(SPLIT) $(LIMIT)

vista-builtin:
	bash tools/run_vista.sh builtin $(SPLIT) $(LIMIT)

vista-plugin-real:
	bash tools/run_vista.sh plugin-real $(SPLIT) $(LIMIT) $(WORKERS)

# Quick end-to-end sanity check: plugin-real over the dev split, 8 tasks, 4 workers.
vista-smoke:
	bash tools/run_vista.sh plugin-real dev 8 4
