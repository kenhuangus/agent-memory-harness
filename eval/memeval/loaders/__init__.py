"""Loader registry: ``Benchmark`` -> the loader that produces its tasks.

One :class:`~memeval.loaders.base.BaseLoader` subclass per benchmark, each
registered here so the harness can resolve a loader from a (possibly loose)
benchmark string in one call::

    from memeval.loaders import get_loader
    loader = get_loader("longmemeval")
    tasks = loader.load("tests/fixtures/longmemeval.json", limit=10)

Loader modules are imported **lazily** inside :func:`get_loader` rather than at
package import time. That keeps importing :mod:`memeval.loaders` cheap and -- in
a parallel build where sibling loaders may not be on disk yet -- prevents one
unfinished loader module from breaking the whole registry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..schema import Benchmark

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .base import BaseLoader
    from ..protocols import Loader


#: Benchmark -> (module suffix under ``memeval.loaders``, class name).
#: Resolved lazily so a missing sibling module only fails *its* lookup.
_REGISTRY: dict[Benchmark, tuple[str, str]] = {
    Benchmark.MEMORY_AGENT_BENCH: ("memoryagentbench", "MemoryAgentBenchLoader"),
    Benchmark.LONGMEMEVAL: ("longmemeval", "LongMemEvalLoader"),
    Benchmark.SWE_CONTEXTBENCH: ("swe_contextbench", "SWEContextBenchLoader"),
    Benchmark.SWE_BENCH_CL: ("swe_bench_cl", "SWEBenchCLLoader"),
    Benchmark.CONTEXTBENCH: ("contextbench", "ContextBenchLoader"),
    Benchmark.VISTA: ("vista", "VistaLoader"),
}

#: Friendly alias for the registry (same object) -- the harness/CLI may import
#: ``BENCHMARKS`` to enumerate or reference registered loaders.
BENCHMARKS = _REGISTRY


def get_loader(benchmark: "Benchmark | str") -> "Loader":
    """Return a fresh loader instance for ``benchmark``.

    Accepts a :class:`~memeval.schema.Benchmark` or a loose string (parsed via
    :meth:`Benchmark.from_str`, so ``"LongMemEval"`` / ``"swe-bench-cl"`` work).
    Imports the loader module lazily; raises :class:`KeyError` for an unknown
    benchmark and :class:`ImportError`/:class:`AttributeError` only if the
    mapped loader module/class is genuinely missing.
    """
    bench = benchmark if isinstance(benchmark, Benchmark) else Benchmark.from_str(benchmark)
    if bench not in _REGISTRY:
        raise KeyError(f"No loader registered for benchmark {bench!r}")
    module_suffix, class_name = _REGISTRY[bench]

    import importlib

    module = importlib.import_module(f"{__name__}.{module_suffix}")
    loader_cls = getattr(module, class_name)
    return loader_cls()


def available() -> list[Benchmark]:
    """Return every benchmark that has a registered loader (importable or not)."""
    return list(_REGISTRY.keys())


def register_loader(
    benchmark: Benchmark, module_suffix: str, class_name: str
) -> None:
    """Register (or override) the loader for ``benchmark``.

    ``module_suffix`` is the module name under ``memeval.loaders`` and
    ``class_name`` the loader class within it (resolved lazily on first use).
    Lets new backends plug into the registry without editing this module.
    """
    _REGISTRY[benchmark] = (module_suffix, class_name)


__all__ = ["get_loader", "available", "register_loader", "BENCHMARKS", "_REGISTRY"]
