"""Cookbook memory UI — combined operator dashboard + memory-store inspector.

Two views under one origin (``python -m ui``):

* **Monitor** — live operator dashboard for in-flight benchmark runs. Auto-
  discovers ``results/<run>/_memory/.cookbook-memory`` basedirs, polls every
  3s, surfaces KPI cards + charts + recent kept memories + top reject reasons.
  Reads ``memory.db`` as the source of truth for memory counts and reconciles
  against the diary event surface so silent-emit regressions are visible
  rather than mistaken for zero.

* **Inspector** — read-only browse of a single substrate (a ``.../_memory``
  directory). Three internal tabs: Browse (de-duped memories with backend
  chips and graph edges), Routing-effectiveness (predicted vs actual landing
  + low-margin flags), and Query Probe (a query's routing decision with each
  backend's RAW results and the routed engine's answer side by side).
  Mirrors ``cookbook_memory/core/contract.py::build_store`` through
  ``memeval``'s PUBLIC APIs; never modifies the stores, the router, or
  any existing file. Both inspector views can capture an eval case
  (``POST /api/capture``).

Inherited from the prior ``router_ui`` package. Owners: Brent (@bgibson1618)
for the inspector surface; Scott (@NerdAlert58) for the monitor surface.
"""

from __future__ import annotations
