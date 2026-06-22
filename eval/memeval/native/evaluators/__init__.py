"""Per-benchmark native evaluators (one module per benchmark; additive).

Each module here defines ONE class satisfying
:class:`memeval.native.spec.NativeEvaluator` and is resolved lazily by
:func:`memeval.native.registry.get_native_evaluator`. The expected module/class
names (the registry's agreed contract) are:

* ``longmemeval.py``       -> ``LongMemEvalNativeEvaluator``
* ``memoryagentbench.py``  -> ``MemoryAgentBenchNativeEvaluator``
* ``contextbench.py``      -> ``ContextBenchNativeEvaluator``
* ``swe_contextbench.py``  -> ``SWEContextBenchNativeEvaluator``
* ``swe_bench_cl.py``      -> ``SWEBenchCLNativeEvaluator``

This package is an intentionally-empty namespace; importing it has no side
effects (no eager evaluator imports), so a half-finished sibling never breaks the
others.
"""

from __future__ import annotations

__all__: list[str] = []
