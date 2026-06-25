"""Memory inspector — a local web tool to browse plugin-saved memories and evaluate
router effectiveness. Owner: Brent (@bgibson1618).

Additive and read-only over a substrate: it opens the three backends + a Router /
RouterStore (mirroring ``cookbook_memory/core/contract.py::build_store``) through
``memeval``'s PUBLIC APIs and never modifies the stores, the router, or any existing
file. Run it with ``python -m router_ui`` (see :mod:`.__main__`).

Three views over the substrate:

* **Browse** — the de-duped union of every backend's ``all()``, with backend-membership
  chips and graph edges.
* **Routing-effectiveness** — predicted (``classify`` / ``explain`` / ``write_plan``) vs
  actual on-disk landing per memory, flagging write-plan-vs-actual asymmetry and
  ambiguous (low-margin) content.
* **Query Probe** — a query's routing decision, each backend's RAW results, and the
  routed engine's answer side by side.

Both views can capture an eval case (``POST /api/capture``).
"""

from __future__ import annotations
