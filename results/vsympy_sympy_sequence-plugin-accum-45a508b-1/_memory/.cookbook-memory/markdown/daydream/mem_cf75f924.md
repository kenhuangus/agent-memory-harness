---
type: daydream
title: mem_cf75f924
description: 'sympy has a bug in hyperbolic.py''s coth.eval method: variable ''cotm'' is used before assignment, causing NameError on subs(coth(log(tan(x)),…'
resource: 'memeval://memory/mem_cf75f924'
tags:
- sympy
- bug
- hyperbolic.py
timestamp: '2026-06-25T05:16:33.331517+00:00'
x_item_id: mem_cf75f924
x_relevancy: 0.9
x_version: 1
x_session_id: deaaebfd-929b-44a0-ace0-203ec8fba247
x_source: daydream
x_tokens: 41
x_metadata_json: '{"extracted_from": "deaaebfd-929b-44a0-ace0-203ec8fba247"}'
---

sympy has a bug in hyperbolic.py's coth.eval method: variable 'cotm' is used before assignment, causing NameError on subs(coth(log(tan(x)), N) for certain integers.
