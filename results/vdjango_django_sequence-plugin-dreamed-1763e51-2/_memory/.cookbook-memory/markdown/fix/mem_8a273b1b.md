---
type: Fix
title: mem_8a273b1b
description: 'When Engine.render_to_string() creates a Context in Django, pass autoescape=self.autoescape to honor the engine''s setting; otherwise autoescape=False is silently ignored.'
resource: 'memeval://memory/mem_8a273b1b'
tags:
- django
- template
- autoescape
- bug-fix
timestamp: '2026-06-27T15:44:18.289238+00:00'
x_item_id: mem_8a273b1b
x_relevancy: 0.9
x_version: 1
x_session_id: 2ac7f147-d0f4-46c6-9e22-a14704b4fc0f
x_source: daydream
x_tokens: 42
---

When Engine.render_to_string() creates a Context in Django, pass autoescape=self.autoescape to honor the engine's setting; otherwise autoescape=False is silently ignored.
