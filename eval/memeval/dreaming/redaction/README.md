# Daydream v1 redaction module

Inline secret-redaction layer applied to every string Daydream passes to the LLM. See the [v1 inline-redaction ADR](../../../../docs/adrs/ADR-dreaming-005-v1-inline-redaction.md) and the [expanded scope ADR](../../../../docs/adrs/ADR-dreaming-011-expanded-redaction-scope.md).

## Public surface

```python
from memeval.dreaming.redaction import RedactedText, redact

cleaned: RedactedText = redact("user pasted sk-ant-api03-…")
# cleaned == "user pasted [REDACTED:Anthropic API Key]"
```

`RedactedText` is the structural enforcement of the trust boundary (see [ADR-010](../../../../docs/adrs/ADR-dreaming-010-redactedtext-newtype.md)). `LLMClient.complete()` (PR2) will accept only `RedactedText`, never raw `str` — mypy `--strict` catches bypasses.

## What's redacted

**11 structured detect-secrets plugins** (AWS, Azure, GitHub, GitLab, Slack, Stripe, OpenAI, JWT, PrivateKey, BasicAuth, Artifactory) plus **6 Daydream-local custom plugins** under `plugins/`:

| Plugin | Catches |
|---|---|
| `AnthropicKeyDetector` | `sk-ant-api03-…`, `sk-ant-sid01-…` |
| `OpenRouterKeyDetector` | `sk-or-v1-…` |
| `GoogleCloudKeyDetector` | `AIza[…35 chars…]` |
| `BearerTokenDetector` | tokens after `Authorization: Bearer ` |
| `DatabaseURLDetector` | `postgres://user:pw@host/db`, mysql/mongodb/redis/amqp variants |
| `URLCredentialDetector` | `?access_token=…`, `?api_key=…`, `?auth=…`, `?token=…`, `?secret=…`, `?password=…` |

## Out of scope (v1)

This redaction layer does **not** catch:

- **Free-form English credentials** ("my password is hunter2", "the API key is X"). No pattern detector — would require LLM-based detection, which contradicts "redact before LLM call."
- **Novel/custom token formats** (one-off MCP server tokens, experimental provider keys). Surface these via the FP/FN audit file (`write_audit_record`) and add detectors in successor ADRs when patterns repeat.
- **PII** (personal names, emails, addresses). Separate concern; deferred. See `docs/honcho-comparison.md` (locally on `honcho-research` branch) for the Presidio path if/when PII becomes load-bearing.

This list is the contract with downstream users: if your sessions contain these, layer your own controls — Daydream redaction will not catch them.

## Audit file

`_audit.write_audit_record(path, chunk_id=..., pre=..., post=..., detected=...)` appends one JSONL line per chunk to a local-only file (gitignored). The eval driver reads these to compute FP/FN rates. See [ADR-011 §3](../../../../docs/adrs/ADR-dreaming-011-expanded-redaction-scope.md) for the record shape.
