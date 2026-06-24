"""Native-evaluator registry: ``Benchmark`` -> its :class:`NativeEvaluator`.

Mirrors :mod:`memeval.loaders` (the loader registry) but for the per-benchmark
NATIVE evaluators under :mod:`memeval.native.evaluators`. Each evaluator module
is imported **lazily** on lookup so:

* importing :mod:`memeval.native` stays cheap, and
* in a parallel build where sibling evaluator modules may not exist yet, one
  missing evaluator only fails ITS lookup — not the whole registry.

Two ways to register
--------------------
1. **Module/class mapping (lazy)** — the default. The mapping holds
   ``Benchmark -> (module_suffix, class_name)`` and the class is imported on
   first :func:`get_native_evaluator`. Implementers add their benchmark here (or
   call :func:`register_native_evaluator` with the module+class form).
2. **Direct instance/class override (eager)** — pass a constructed evaluator or
   a class to :func:`register_native_evaluator`; useful in tests to inject a
   stub without a module on disk.

The integrate phase fills in the four/five real evaluators; until then a lookup
for an unregistered benchmark raises a clear :class:`KeyError`.
"""

from __future__ import annotations

import importlib
from typing import Any, Optional, Union

from ..schema import Benchmark
from .spec import NativeEvaluator

#: Benchmark -> (module suffix under ``memeval.native.evaluators``, class name).
#: Resolved lazily so a missing sibling module only fails *its* lookup. The
#: per-benchmark implementers create ``evaluators/<suffix>.py`` with ``<class>``.
#:
#: NOTE: these entries are the AGREED file+class names — implementers MUST create
#: exactly these so the registry resolves without edits. (Adding a new entry, or
#: calling register_native_evaluator(), is also fine and is additive.)
_REGISTRY: dict[Benchmark, tuple[str, str]] = {
    Benchmark.LONGMEMEVAL: ("longmemeval", "LongMemEvalNativeEvaluator"),
    Benchmark.MEMORY_AGENT_BENCH: ("memoryagentbench", "MemoryAgentBenchNativeEvaluator"),
    Benchmark.CONTEXTBENCH: ("contextbench", "ContextBenchNativeEvaluator"),
    Benchmark.SWE_CONTEXTBENCH: ("swe_contextbench", "SWEContextBenchNativeEvaluator"),
    Benchmark.SWE_BENCH_CL: ("swe_bench_cl", "SWEBenchCLNativeEvaluator"),
    Benchmark.VISTA: ("vista", "VistaNativeEvaluator"),
}

#: Eagerly-registered overrides (constructed instances or classes), keyed by
#: Benchmark. Checked before the lazy module map. Filled by
#: :func:`register_native_evaluator` when given an instance/class.
_OVERRIDES: dict[Benchmark, Any] = {}


def get_native_evaluator(benchmark: "Benchmark | str") -> NativeEvaluator:
    """Return a fresh native evaluator for ``benchmark``.

    Accepts a :class:`~memeval.schema.Benchmark` or a loose string (parsed via
    :meth:`Benchmark.from_str`). Resolution order:

    1. an eager override registered via :func:`register_native_evaluator`
       (instance returned as-is; class is instantiated), else
    2. the lazy ``(module_suffix, class_name)`` mapping — imports
       ``memeval.native.evaluators.<module_suffix>`` and instantiates
       ``<class_name>()``.

    Raises :class:`KeyError` for a benchmark with no registered evaluator, and
    :class:`ImportError` / :class:`AttributeError` only if a mapped module/class
    is genuinely missing on disk.
    """
    bench = benchmark if isinstance(benchmark, Benchmark) else Benchmark.from_str(benchmark)

    if bench in _OVERRIDES:
        ev = _OVERRIDES[bench]
        return ev() if isinstance(ev, type) else ev

    if bench not in _REGISTRY:
        raise KeyError(
            f"No native evaluator registered for benchmark {bench!r}. "
            f"Registered: {sorted(b.value for b in available_native())}. "
            f"Create memeval/native/evaluators/<suffix>.py and register it via "
            f"register_native_evaluator()."
        )
    module_suffix, class_name = _REGISTRY[bench]
    module = importlib.import_module(f"{__name__.rsplit('.', 1)[0]}.evaluators.{module_suffix}")
    evaluator_cls = getattr(module, class_name)
    return evaluator_cls()


def register_native_evaluator(
    benchmark: "Benchmark | str",
    evaluator: Union[NativeEvaluator, type, str],
    class_name: Optional[str] = None,
) -> None:
    """Register (or override) the native evaluator for ``benchmark``.

    Three call forms:

    * ``register_native_evaluator(bench, module_suffix, class_name)`` — the lazy
      module-mapping form (both strings). The class is imported on first use.
      This is the form per-benchmark implementers use from their module's import
      side-effect or from an integrate step.
    * ``register_native_evaluator(bench, EvaluatorClass)`` — eager class override
      (instantiated fresh per :func:`get_native_evaluator`).
    * ``register_native_evaluator(bench, evaluator_instance)`` — eager instance
      override (returned as-is). Handy in tests.

    Always additive: it only mutates the in-process registry dicts; it never
    touches any other module.
    """
    bench = benchmark if isinstance(benchmark, Benchmark) else Benchmark.from_str(benchmark)
    if isinstance(evaluator, str):
        if not class_name:
            raise ValueError(
                "module-mapping form requires both module_suffix and class_name"
            )
        _REGISTRY[bench] = (evaluator, class_name)
        _OVERRIDES.pop(bench, None)
    else:
        # class or instance -> eager override
        _OVERRIDES[bench] = evaluator


def available_native() -> list[Benchmark]:
    """Every benchmark with a registered native evaluator (lazy or eager)."""
    return list({*_REGISTRY.keys(), *_OVERRIDES.keys()})


__all__ = [
    "get_native_evaluator",
    "register_native_evaluator",
    "available_native",
]
