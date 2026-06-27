---
type: Convention
title: mem_eb265ef8
description: When handling Idx indices in a printer, implement _print_Idx to return self._print(expr.label) rather than str(expr), consistent across all code printers (ccode, rcode, octave).
resource: 'memeval://memory/mem_eb265ef8'
tags:
- printing
- idx
- sympy
timestamp: '2026-06-26T22:40:48.402021+00:00'
x_item_id: mem_eb265ef8
x_relevancy: 0.85
x_version: 1
x_session_id: 5f04e22d-47c9-44b8-b245-ec652b843cfe
x_source: daydream
x_tokens: 44
---

When handling Idx indices in a printer, implement _print_Idx to return self._print(expr.label) rather than str(expr), consistent across all code printers (ccode, rcode, octave).
