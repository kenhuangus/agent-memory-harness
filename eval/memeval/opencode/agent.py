"""OpenCode agent — the multi-step loop. Owner: Keith (@kmazanec). Scaffold.

:class:`OpenCodeAgent` satisfies :class:`memeval.agent.AgentAdapter`: it runs
OpenCode's own retrieve -> generate -> tool -> remember loop inside ``solve``,
using the :class:`~memeval.agent.AgentContext` so cost, trajectory, and grading
stay centralized in the eval harness (Ken). Memory ops flow through the
:class:`~memeval.opencode.framework.MemoryFramework` (Keith), which is backed by
Brent's stores/router and consolidated by Scott's dreaming.

Wire-up for a real run::

    fw    = MemoryFramework(router=Router(backends), backends=backends, dreamer=DreamingWorker(...))
    agent = OpenCodeAgent(model=get_model("claude-haiku-4-5"))
    rr    = run_agent(Benchmark.SWE_BENCH_CL, agent, memory=True, store=fw)

TODO(keith): implement ``solve`` — drive the OpenCode loop, calling
``ctx.retrieve`` / ``ctx.generate`` / ``ctx.remember`` each step, and return an
``AgentResult`` (prediction + patch) for the coding benchmarks.
"""

from __future__ import annotations

from typing import Any, Optional

from ..agent import AgentContext, AgentResult, SolveReturn
from ..protocols import ModelAdapter
from ..schema import Task


class OpenCodeAgent:
    """OpenCode wrapped as an :class:`~memeval.agent.AgentAdapter`. (stub)

    ``name`` / ``price_in`` / ``price_out`` mirror ``ModelAdapter`` so the results
    grid can build a :class:`~memeval.schema.ModelConfig` for the run.
    """

    name = "opencode"
    price_in = 0.0
    price_out = 0.0

    def __init__(self, model: Optional[ModelAdapter] = None, *, name: str = "opencode") -> None:
        self.model = model
        self.name = name if model is None else f"{name}+{getattr(model, 'name', 'model')}"
        self.price_in = getattr(model, "price_in", 0.0)
        self.price_out = getattr(model, "price_out", 0.0)

    def solve(self, task: Task, ctx: AgentContext, **kwargs: Any) -> SolveReturn:
        """Run OpenCode's loop over ``task`` via ``ctx``; return prediction/patch. (stub)"""
        raise NotImplementedError(
            "OpenCodeAgent.solve — TODO(keith): retrieve -> generate -> tool -> remember loop"
        )


__all__ = ["OpenCodeAgent", "AgentResult"]
