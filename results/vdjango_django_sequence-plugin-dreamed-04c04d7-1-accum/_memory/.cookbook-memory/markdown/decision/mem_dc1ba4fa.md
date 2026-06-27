---
type: Decision
title: mem_dc1ba4fa
description: Any translator/printer subclass in this codebase (e.g.
resource: 'memeval://memory/mem_dc1ba4fa'
tags:
- django
- ForeignKey
- managers
- validation
timestamp: '2026-06-27T17:05:44.768343+00:00'
x_item_id: mem_dc1ba4fa
x_relevancy: 0.85
x_version: 1
x_session_id: d1e6ca97-a23b-42eb-9fd1-1794d524d184
x_source: daydream
x_tokens: 49
---

Any translator/printer subclass in this codebase (e.g. ModuleLevelPrinter) should explicitly call super().__init__(self) before using self, or define __init__ with **kwargs that forward to parent.
