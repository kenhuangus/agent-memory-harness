"""Semantic-retrieval eval (D019/D020) — does a real embedder beat the offline floor? Owner: Brent.

**Why this exists.** The D008 cascade fixture (``test_d008_evals``) is small and *lexically*
constructed, so the offline char-n-gram ``_HashingEmbedder`` already captures its signal — a live
Voyage run (DECISION_LOG **D019**) moved recall@5 by 0.000 there. That proved the *fixture*, not the
embedder, was the limit. This fixture supplies the missing instrument: **semantic-divergence**
retrieval cases where the query and its gold memory mean the same thing but share little-to-no
surface form (synonyms / paraphrase / conceptual "why" / cross-lingual / abstraction gaps), over a
shared 34-memory haystack. Surface overlap varies by lens (cross-lingual queries share essentially
nothing; some paraphrase cases retain partial word overlap) — what is *enforced* is not a subjective
overlap threshold but the empirical property below: the offline embedder misses the gold in top-k.

**What the OFFLINE (committed) path proves — headroom, not victory.** Every offline embedder here
(the stdlib hashing default, and ``MockEmbedder``) is char-n-gram *lexical*, so none can demonstrate a
*semantic* win offline. What this committed test proves is that the **headroom exists**:

* **divergence cases** — the offline embedder genuinely **fails** (recall@k == 0): gold is not in the
  top-k, lexically-closer wrong memories crowd it out. This is the room a real embedder must recover.
* **control cases** — the offline embedder genuinely **succeeds** (recall@k == 1): proves the harness
  *can* detect retrieval success, so "divergence fails" is a real semantic gap, not a broken test.

The actual hashing-vs-Voyage **measurement is captained (D020)** — it needs a live API key and is run
out-of-band (``work/`` scratch), never in CI, preserving the zero-dependency offline guarantee. Do not
add a network call here.

**Anti-theater.** "These cases are semantic" is not a comment — it is enforced. The committed tests
assert, against the real offline path, that divergence cases defeat it and controls do not. A future
"divergence" case the offline embedder can actually find FAILS the suite instead of silently inflating
the pool (the same self-confirming trap a verifier caught on the D008 PR1 fixture).

**Provenance (eval-first, blind).** Cases were authored by a blind multi-lens workflow (6 firewalled
generators × distinct lenses → synthesizer enforcing global gold-uniqueness → independent per-case
semantic verifiers), then passed through a **deterministic** offline-embedder calibration that dropped
every "divergence" case the hashing embedder could find (12 of 27 generated) and kept only those it
provably misses. The surviving distribution is itself a finding: cross-lingual and conceptual queries
defeat lexical matching reliably; synonyms barely do (they share morphology). See DECISION_LOG D019/D020.

Reproduce the report:    cd eval && python3 -m memeval.stores.tests.test_semantic_retrieval_evals
Run the regression guard: cd eval && python3 -m unittest memeval.stores.tests.test_semantic_retrieval_evals
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Optional

from memeval.schema import MemoryItem
from memeval.stores.sqlite_store import SqliteVectorStore

K = 5  # top-k for retrieval (matches the D008 fixture)

DIVERGENCE = "divergence"
CONTROL = "control"
_VALID_KINDS = {DIVERGENCE, CONTROL}
# the divergence lenses (provenance labels); control cases use "lexical_control".
_VALID_LENSES = {"synonym", "paraphrase", "conceptual", "crosslingual",
                 "hypernym_informal", "lexical_control"}


def _item(item_id: str, content: str, *, title: Optional[str] = None,
          links=None, timestamp: float = 0.0, relevancy: float = 1.0) -> MemoryItem:
    """A MemoryItem carrying OKF metadata (title/links) the way okf.py emits it."""
    meta: dict = {}
    if title is not None:
        meta["okf_title"] = title
    if links:
        meta["okf_links"] = list(links)
    return MemoryItem(item_id=item_id, content=content, timestamp=timestamp,
                      relevancy=relevancy, metadata=meta)


@dataclass(frozen=True)
class SemanticCase:
    """One semantic-retrieval case over the shared CORPUS.

    ``kind`` is DIVERGENCE (offline embedder must MISS gold — the headroom) or CONTROL (offline
    embedder must FIND gold — the apparatus check). ``lexical_distractor_ids`` name memories that
    share the query's surface words while being the wrong answer (a lexical embedder is tempted by
    them); ``note`` records why the match is genuine yet surface-divergent.
    """

    name: str
    lens: str
    kind: str
    query: str
    gold_item_ids: tuple
    lexical_distractor_ids: tuple = ()
    note: str = ""


CORPUS = (
    _item(
        'rl-token-bucket',
        'The public API gateway throttles each client to 100 requests per second using a token-bucket algorithm, with a burst allowance of 200 tokens. Tokens refill at a steady rate and a request is rejected with HTTP 429 when the bucket is empty.',
        title='Token-bucket throttling at the API gateway',
        links=('rl-quota-redis-counter',),
    ),
    _item(
        'rl-quota-redis-counter',
        "Each tenant's monthly call allotment is enforced with a Redis counter keyed by tenant-id and the current billing month; the counter increments per request and is compared against the plan's ceiling. When the allotment is exhausted the API returns 403 with a quota-exceeded body until the next billing cycle.",
        title='Monthly API quota tracked in Redis',
        links=('rl-token-bucket',),
    ),
    _item(
        'rl-backpressure-queue',
        'The ingestion service applies backpressure when the in-memory work queue exceeds 10k items: it stops acknowledging new Kafka messages so producers slow down rather than overwhelming downstream workers. This prevents unbounded memory growth and cascading timeouts under load spikes.',
        title='Backpressure on the ingestion queue',
        links=('obs-circuit-breaker', 'rl-concurrency-semaphore'),
    ),
    _item(
        'obs-circuit-breaker',
        'Calls to the third-party payments provider are wrapped in a circuit breaker that trips open after 5 consecutive failures (5xx responses), short-circuiting further calls for 30 seconds before a half-open probe request. This shields our worker threads from piling up against a slow or dead dependency and stops cascading timeouts when the upstream processor is unhealthy.',
        title='Circuit breaker for the payments dependency',
        links=('obs-backoff-jitter-retries', 'rl-backpressure-queue'),
    ),
    _item(
        'obs-backoff-jitter-retries',
        'Failed idempotent / outbound HTTP requests are retried with exponential backoff plus random (full) jitter, capped at a few seconds and a handful of attempts. The randomized delay spreads retry timing across a fleet of clients so a recovering dependency is not hit by a synchronized thundering-herd of retries all at once.',
        title='Exponential backoff with jitter on retries',
        links=('obs-circuit-breaker', 'rl-token-bucket'),
    ),
    _item(
        'rl-concurrency-semaphore',
        'Outbound calls to the search cluster are gated by a semaphore that permits at most 50 in-flight requests per node; callers block briefly when the limit is reached. This caps simultaneous load on the cluster regardless of how many requests arrive.',
        title='Bounded concurrency to the search cluster',
        links=('rl-backpressure-queue',),
    ),
    _item(
        'db-denormalized-read-models',
        'The analytics dashboard reads from precomputed flat tables that copy fields from orders, users, and products into one wide row, so the page never has to join across normalized tables at request time. We accept the storage overhead and duplicated columns in exchange for single-row lookups under heavy read traffic.',
        title='Denormalized read models for the dashboard',
        links=('db-cqrs-split', 'db-covering-index-orders'),
    ),
    _item(
        'db-cqrs-split',
        'We route mutations through a command path that writes to the transactional Postgres instance, while all queries hit a separate projection database kept eventually consistent via change events. This lets us scale and tune the two sides independently instead of forcing one schema to serve both insert-heavy and read-heavy workloads.',
        title='Separate write store from query store',
        links=('db-denormalized-read-models',),
    ),
    _item(
        'db-cache-stampede-lock',
        'When a hot key expires, hundreds of concurrent requests would otherwise all miss and hammer the database to recompute the same value. We use a per-key mutex so only the first requester rebuilds the entry while everyone else briefly waits or serves the stale value, collapsing the thundering herd into one backend hit.',
        title='Preventing cache stampede on expired keys',
        links=('db-write-through-cache',),
    ),
    _item(
        'db-write-through-cache',
        'Every update to a catalog item is written to Redis and the primary database in the same operation, so the cache and the source of truth never drift. Reads always hit Redis first and only fall back to the database on a true miss, keeping product detail pages fast without risking stale prices.',
        title='Write-through caching policy for product catalog',
        links=('db-cache-stampede-lock',),
    ),
    _item(
        'db-connection-pool-exhaustion',
        'During traffic spikes the API ran out of available connections because each request held one open while waiting on slow downstream calls, so new requests queued and timed out. We capped the pool, added a short acquire timeout, and moved the slow external call outside the transaction to release the connection sooner.',
        title='Database connection pool exhaustion under load',
        links=('db-covering-index-orders',),
    ),
    _item(
        'db-covering-index-orders',
        'The orders listing query was slow because the index only located rows and then fetched each one from the heap. We added a composite index that includes the selected columns directly, so the planner satisfies the query entirely from the index without touching the main table.',
        title='Covering index to avoid table heap reads',
        links=('db-denormalized-read-models', 'db-connection-pool-exhaustion'),
    ),
    _item(
        'auth-short-lived-access-tokens',
        'Access tokens are signed JWTs that expire 15 minutes after issuance; clients trade a long-lived refresh token for a new pair, and each refresh rotates the refresh token so a previously captured one is invalidated on reuse.',
        title='Access token lifetime and refresh rotation',
        links=('auth-refresh-token-reuse-detection',),
    ),
    _item(
        'auth-bcrypt-password-storage',
        'User passwords are never persisted in plaintext; we run bcrypt with a cost factor of 12 and a unique per-user salt, so two users with the same password produce different hashes and the work factor slows brute-force attempts.',
        title='Password storage uses bcrypt with per-user salt',
    ),
    _item(
        'auth-totp-second-factor',
        'After the password check, accounts flagged sensitive must present a six-digit TOTP code from an authenticator app; this means a leaked password alone cannot complete sign-in without the rotating code tied to the device.',
        title='TOTP enrollment as a second factor',
    ),
    _item(
        'auth-rbac-least-privilege',
        'Each API route declares the minimum role it requires, and new service accounts start with an empty scope set; engineers must explicitly grant capabilities, so a compromised account can only touch what it was deliberately given.',
        title='Role-based access scopes per endpoint',
    ),
    _item(
        'auth-refresh-token-reuse-detection',
        'If a refresh token that has already been rotated is presented again, we treat the whole token family as compromised and revoke every descendant, forcing re-authentication; this catches a thief replaying a token after the legitimate client already advanced.',
        title='Refresh token reuse triggers family revocation',
        links=('auth-short-lived-access-tokens',),
    ),
    _item(
        'auth-csrf-double-submit',
        "State-changing requests must echo a random value that is set both as a cookie and a request header; because a malicious cross-origin page cannot read the cookie to forge the header, forged requests riding on the user's session are rejected.",
        title='CSRF protection via double-submit cookie',
    ),
    _item(
        'dep-blue-green-cutover',
        'Blue-green deployment keeps the previous version serving live traffic while the new version warms up in parallel; the load balancer only switches the pool over once readiness and health checks pass, so users never see an outage. If the new pool misbehaves, the switch is reverted instantly back to the old pool.',
        title='Blue-green deployment cutover',
        links=('dep-canary-rollout', 'dep-rollback-on-error-budget'),
    ),
    _item(
        'dep-canary-rollout',
        'A canary release routes a small slice of users (1%, then 5%, then 25%) to the new build and watches latency and error rates before widening exposure. Automated analysis promotes or aborts each step, so a bad change is caught while it only affects a handful of requests.',
        title='Canary rollout with progressive traffic shift',
        links=('dep-blue-green-cutover', 'dep-rollback-on-error-budget'),
    ),
    _item(
        'dep-rollback-on-error-budget',
        'When the error-budget burn rate during a rollout exceeds the threshold, the pipeline halts promotion and reverts the service to the last known-good revision automatically, with no human paged. The revert reuses the artifact already cached on the nodes so recovery takes seconds.',
        title='Automatic rollback on error-budget burn',
        links=('dep-canary-rollout', 'dep-blue-green-cutover'),
    ),
    _item(
        'dep-db-expand-contract',
        'To change a database schema without downtime, first add the new column or table and have the application write to both shapes (expand), deploy code that reads the new shape, then drop the old column in a later release (contract). This decouples schema changes from code releases so neither blocks the other.',
        title='Expand-contract schema migration',
        links=('cfg-feature-flag-decouple',),
    ),
    _item(
        'cfg-feature-flag-decouple',
        'Shipping code behind a feature flag lets the binary go to production dark, then operators turn the capability on for users later via a config toggle without a redeploy. This separates the act of deploying from the act of releasing, and a flag flip is an instant kill switch if something breaks.',
        title='Feature flags decouple deploy from release',
        links=('dep-db-expand-contract', 'dep-canary-rollout'),
    ),
    _item(
        'dep-connection-draining',
        'Before an old instance is terminated during a rolling update, it is removed from the load balancer and allowed to finish in-flight requests for a drain timeout instead of dropping live connections. New traffic stops arriving immediately while existing sessions complete cleanly, avoiding 502s during the swap.',
        title='Graceful connection draining on shutdown',
        links=('dep-blue-green-cutover',),
    ),
    _item(
        'obs-trace-context-propagation',
        "Every inbound request extracts the W3C traceparent header and injects it into all downstream calls, so a single request's spans stitch together into one distributed trace in Tempo. Background jobs continue the trace via the context stored on the enqueued message.",
        title='W3C traceparent propagation across services',
        links=('obs-structured-json-logs',),
    ),
    _item(
        'obs-structured-json-logs',
        'All services emit structured JSON log lines carrying a request_id and trace_id field, shipped to Loki. The shared correlation ID lets an operator pivot from one log line to every other line emitted while handling the same request.',
        title='Structured JSON logs with correlation IDs',
        links=('obs-trace-context-propagation',),
    ),
    _item(
        'obs-red-dashboard-slo',
        'Each service has a RED dashboard (Rate, Errors, Duration) with p50/p95/p99 latency histograms. The p99 panel is tied to a 300ms latency SLO; sustained breach of the error budget pages the on-call engineer.',
        title='RED-method Grafana dashboards and latency SLO',
        links=('obs-dead-letter-queue',),
    ),
    _item(
        'obs-dead-letter-queue',
        'Messages that fail processing after 3 redelivery attempts are routed to a dead-letter queue instead of blocking the main consumer. An operator can inspect, fix, and replay them rather than losing the event or stalling the pipeline.',
        title='Dead-letter queue for poison messages',
        links=('obs-red-dashboard-slo',),
    ),
    _item(
        'cfg-cascade-off-by-default-flag',
        'The graph-vector cascade in router.py ships disabled: a bare RouterConfig() is byte-equivalent to pre-cascade behavior. The cascade flag only engages when a cascade-enabled profile is selected, classify==GRAPH, and both backends are present; otherwise routing falls through unchanged.',
        title='Cascade is off by default in RouterConfig',
        links=('cfg-speed-vs-accuracy-profiles',),
    ),
    _item(
        'cfg-speed-vs-accuracy-profiles',
        'router.py exposes speed_profile() and accuracy_profile() as named RouterConfig presets controlling the speed-versus-cascade tradeoff. speed_profile() keeps single-backend dispatch (recall@5 0.857, ~220 tokens); accuracy_profile() is a PR3 placeholder that only runs when a learned classifier and real embedder are injected.',
        title='speed_profile() and accuracy_profile() presets',
        links=('cfg-cascade-off-by-default-flag', 'cfg-embed-injection-seam'),
    ),
    _item(
        'cfg-embed-injection-seam',
        'SqliteVectorStore defaults to a deterministic stdlib char-n-gram hashing embedder, but a real model (Voyage or bge) plus ANN can be switched on by passing embed= at construction. This lazy-injection seam keeps the default path zero-dependency and offline.',
        title='Real embedder selected via embed= argument',
        links=('cfg-voyage-api-key-env', 'cfg-stdlib-offline-guarantee'),
    ),
    _item(
        'cfg-voyage-api-key-env',
        'The paid embedding and routing paths read VOYAGE_API_KEY and OPENROUTER_API_KEY from a local .env file. These secrets are never committed: .env and .env.* are listed in .gitignore alongside the work/ scratch directory.',
        title='VOYAGE_API_KEY and OPENROUTER_API_KEY in .env',
        links=('cfg-gitignore-env-and-work', 'cfg-embed-injection-seam'),
    ),
    _item(
        'cfg-gitignore-env-and-work',
        "The repo's .gitignore contains exactly three patterns: .env, .env.*, and work/. This keeps API-key secret files and the throwaway delegate-run scratch directory out of version control.",
        title='.gitignore excludes .env files and work/',
        links=('cfg-voyage-api-key-env',),
    ),
    _item(
        'cfg-stdlib-offline-guarantee',
        "Every backend's default code path is stdlib-only so the harness runs offline with no installed packages. Optional heavy dependencies (PyYAML, torch, semantic-router) are imported only behind a lazy boundary on the paid path, never at module top, preserving the zero-dependency guarantee.",
        title='Stdlib-only zero-dependency offline default',
        links=('cfg-embed-injection-seam',),
    ),
)


SEMANTIC_CASES = (
    SemanticCase(
        name='spread_out_reattempts', lens='synonym', kind='divergence',
        query="How do we avoid all clients re-sending at the exact same moment after an outage so the recovering box isn't swamped?",
        gold_item_ids=('obs-backoff-jitter-retries',),
        lexical_distractor_ids=('obs-circuit-breaker',),
        note='"avoid re-sending at the same moment / swamping the recovering box" is a synonym for jittered backoff preventing a thundering herd; the distractor shares "outage/failures/recovering" surface words but is the circuit breaker, not retry jitter.',
    ),
    SemanticCase(
        name='why_duplicate_fields_instead_of_joining', lens='paraphrase', kind='divergence',
        query='Why do we copy the same columns into one big table for the reporting page rather than stitching the records together when someone loads it?',
        gold_item_ids=('db-denormalized-read-models',),
        lexical_distractor_ids=('db-cqrs-split',),
        note="Both describe avoiding runtime joins by pre-flattening data; the query uses 'copy columns into one big table' and 'stitching records' instead of the gold's 'denormalized read models' and 'join across normalized tables'.",
    ),
    SemanticCase(
        name='split_path_for_inserts_vs_lookups', lens='paraphrase', kind='divergence',
        query='How come changes go to one place but lookups come from somewhere else, so each side can be sized on its own?',
        gold_item_ids=('db-cqrs-split',),
        lexical_distractor_ids=('db-write-through-cache',),
        note="This is the command/query separation idea reworded; 'changes go to one place but lookups come from somewhere else' and 'sized on its own' map to the gold's 'mutations through a command path', 'separate projection database', and 'scale independently' without sharing those terms.",
    ),
    SemanticCase(
        name='keep_fast_layer_and_truth_in_sync_on_update', lens='paraphrase', kind='divergence',
        query="How do we make sure the speed layer and the real record of prices don't disagree whenever an item changes?",
        gold_item_ids=('db-write-through-cache',),
        lexical_distractor_ids=('db-cqrs-split',),
        note="The query paraphrases write-through synchronization: 'speed layer and the real record don't disagree on change' equals the gold's 'cache and source of truth never drift' on every update, with no shared distinctive words like 'write-through' or 'Redis'.",
    ),
    SemanticCase(
        name='why_stolen_token_useless_quickly', lens='conceptual', kind='divergence',
        query="what keeps a leaked credential from being usable for long after it's grabbed?",
        gold_item_ids=('auth-short-lived-access-tokens',),
        lexical_distractor_ids=('auth-refresh-token-reuse-detection',),
        note="The gold's short expiry plus rotation is exactly the mechanism that limits a leaked credential's useful window, but it never says 'leaked', 'usable', or 'grabbed'; the distractor shares 'token'/'reuse'/'compromised' wording yet describes detection after theft, not limiting the window.",
    ),
    SemanticCase(
        name='purpose_of_per_user_salt', lens='conceptual', kind='divergence',
        query='why do two accounts with identical secrets end up stored differently?',
        gold_item_ids=('auth-bcrypt-password-storage',),
        lexical_distractor_ids=(),
        note="The gold explains that a unique per-user salt makes identical passwords hash differently, which is precisely the 'why', but the query avoids 'bcrypt', 'salt', 'hash', and 'password', using 'secrets' and 'stored differently' instead.",
    ),
    SemanticCase(
        name='why_blast_radius_limited', lens='conceptual', kind='divergence',
        query='why is the damage contained when one service identity gets taken over?',
        gold_item_ids=('auth-rbac-least-privilege',),
        lexical_distractor_ids=(),
        note="Least-privilege default-deny scopes are the reason a takeover's reach is bounded, matching 'a compromised account can only touch what it was deliberately given', yet the query uses 'damage contained' and 'service identity' instead of 'role', 'scope', or 'privilege'.",
    ),
    SemanticCase(
        name='intent_behind_echoed_random_value', lens='conceptual', kind='divergence',
        query="why can't a hostile webpage piggyback on a logged-in user to perform actions?",
        gold_item_ids=('auth-csrf-double-submit',),
        lexical_distractor_ids=(),
        note="The double-submit cookie defeats cross-origin forged requests riding on a session, which is the query's 'hostile webpage piggybacking on a logged-in user', but the query omits 'CSRF', 'cookie', 'header', and 'double-submit'.",
    ),
    SemanticCase(
        name='evitar_caida_al_publicar', lens='crosslingual', kind='divergence',
        query='¿Cómo lanzamos una versión nueva manteniendo la anterior atendiendo usuarios hasta que las comprobaciones de salud salgan bien, para que nadie note una interrupción?',
        gold_item_ids=('dep-blue-green-cutover',),
        lexical_distractor_ids=('dep-connection-draining',),
        note='Spanish query. Describes keeping the old version live until health checks pass with no outage — exactly blue-green cutover — but shares no English surface words with the gold.',
    ),
    SemanticCase(
        name='expo_gradual_du_trafic', lens='crosslingual', kind='divergence',
        query="On veut envoyer d'abord une petite fraction des utilisateurs vers la nouvelle build et surveiller les erreurs avant d'élargir; comment faire?",
        gold_item_ids=('dep-canary-rollout',),
        lexical_distractor_ids=('dep-blue-green-cutover',),
        note='French query. Progressively exposing a small fraction of users while watching errors is the canary rollout, yet the words are French with no overlap to the English gold.',
    ),
    SemanticCase(
        name='automatisches_zurückrollen', lens='crosslingual', kind='divergence',
        query='Wenn die Fehlerrate während der Auslieferung zu stark ansteigt, soll der Dienst von selbst auf die letzte funktionierende Fassung zurückgesetzt werden, ohne jemanden zu alarmieren.',
        gold_item_ids=('dep-rollback-on-error-budget',),
        lexical_distractor_ids=('dep-connection-draining',),
        note='German query. Reverting automatically to the last good revision when the error rate spikes, paging nobody, matches the error-budget rollback gold while sharing no English words.',
    ),
    SemanticCase(
        name='cambiar_esquema_sin_parar', lens='crosslingual', kind='divergence',
        query='¿De qué forma modifico la estructura de la base de datos sin detener el servicio, añadiendo primero lo nuevo y quitando lo viejo más tarde?',
        gold_item_ids=('dep-db-expand-contract',),
        lexical_distractor_ids=(),
        note='Spanish query. Adding the new shape first and dropping the old later to alter a DB without stopping the service is the expand-contract migration; surface words are Spanish.',
    ),
    SemanticCase(
        name='terminar_peticiones_antes_de_apagar', lens='crosslingual', kind='divergence',
        query='Antes de apagar una instancia vieja durante una actualización progresiva, ¿cómo dejo que termine las peticiones en curso para no cortar conexiones activas?',
        gold_item_ids=('dep-connection-draining',),
        lexical_distractor_ids=('dep-blue-green-cutover',),
        note='Spanish query. Letting an old instance finish in-flight requests before shutdown during a rolling update is graceful connection draining; the query is Spanish with no overlap to the English gold.',
    ),
    SemanticCase(
        name='stop_hammering_failing_dependency', lens='hypernym_informal', kind='divergence',
        query="how do we keep from pounding a service that's already falling over until it has a chance to recover?",
        gold_item_ids=('obs-circuit-breaker',),
        lexical_distractor_ids=('obs-dead-letter-queue',),
        note="The informal 'keep from pounding a falling-over service so it can recover' is exactly what a circuit breaker (trip open, cool down, half-open trial) does, but shares no words with 'circuit breaker / consecutive 5xx / short-circuiting'; the DLQ twin shares 'service/recover/message-failure' framing but is about poison-message routing, not protecting an upstream. Merged circuit-breaker memory keeps this gold globally unique.",
    ),
    SemanticCase(
        name='find_all_log_lines_for_one_request', lens='hypernym_informal', kind='divergence',
        query='when something breaks, how do I pull up every line we wrote out while handling that one specific request instead of grepping blind?',
        gold_item_ids=('obs-structured-json-logs',),
        lexical_distractor_ids=('obs-trace-context-propagation',),
        note="'Pull up every line written for one request' is the correlation-ID-in-structured-logs capability, but avoids 'structured JSON / correlation ID / request_id'; the tracing twin shares 'one request / handling' and even mentions context, but it stitches spans, not log lines.",
    ),
    SemanticCase(
        name='routerconfig_cascade_default_off', lens='lexical_control', kind='control',
        query='Is the cascade off by default in RouterConfig, or does it engage automatically?',
        gold_item_ids=('cfg-cascade-off-by-default-flag',),
        lexical_distractor_ids=('cfg-speed-vs-accuracy-profiles',),
        note="Query reuses the gold's exact identifiers 'cascade', 'off by default', and 'RouterConfig' so even a lexical retriever locks onto the right memory; the profiles memory shares 'cascade' and 'RouterConfig' as a near-twin.",
    ),
    SemanticCase(
        name='speed_profile_accuracy_profile_presets', lens='lexical_control', kind='control',
        query='What do the speed_profile() and accuracy_profile() presets control?',
        gold_item_ids=('cfg-speed-vs-accuracy-profiles',),
        lexical_distractor_ids=('cfg-cascade-off-by-default-flag',),
        note="Query quotes the gold's exact function names speed_profile() and accuracy_profile(); the distractor mentions 'profile' and 'cascade' but is about the default-off flag, not the presets.",
    ),
    SemanticCase(
        name='embed_argument_real_model', lens='lexical_control', kind='control',
        query='How do I pass embed= to switch SqliteVectorStore to a real embedder?',
        gold_item_ids=('cfg-embed-injection-seam',),
        lexical_distractor_ids=('cfg-voyage-api-key-env',),
        note="Query carries the gold's distinctive 'embed=' keyword and 'SqliteVectorStore' and 'real embedder'; the Voyage memory shares 'Voyage'/'real' surface words but is about secrets in .env, not the construction argument.",
    ),
    SemanticCase(
        name='voyage_api_key_env_file', lens='lexical_control', kind='control',
        query='Where is VOYAGE_API_KEY read from for the paid embedding path?',
        gold_item_ids=('cfg-voyage-api-key-env',),
        lexical_distractor_ids=('cfg-gitignore-env-and-work',),
        note="Query repeats the exact env var token VOYAGE_API_KEY; the .gitignore memory also mentions '.env' and is a tempting twin but answers what is excluded from git, not where the key is read.",
    ),
    SemanticCase(
        name='gitignore_env_patterns', lens='lexical_control', kind='control',
        query='What patterns does .gitignore use to exclude .env and work/?',
        gold_item_ids=('cfg-gitignore-env-and-work',),
        lexical_distractor_ids=('cfg-voyage-api-key-env',),
        note="Query uses the gold's literal tokens '.gitignore', '.env', and 'work/'; the Voyage memory shares '.env' but is about API-key reading, not the ignore patterns.",
    ),
)

_CORPUS_BY_ID = {m.item_id: m for m in CORPUS}
_CORPUS_IDS = set(_CORPUS_BY_ID)
_EXPECTED_CORPUS = 34   # hardcoded size lock — changing it is deliberate
_EXPECTED_CASES = 20   # hardcoded count lock — changing it is deliberate


# --------------------------------------------------------------------------- #
# Metrics + per-case evaluation
# --------------------------------------------------------------------------- #
def _recall_at_k(ranked_ids: list, gold: tuple) -> float:
    if not gold:
        return 0.0
    return sum(1 for g in gold if g in ranked_ids) / len(gold)


def _mrr(ranked_ids: list, gold: tuple) -> float:
    gold_set = set(gold)
    for idx, item_id in enumerate(ranked_ids):
        if item_id in gold_set:
            return 1.0 / (idx + 1)
    return 0.0


def _best_rank(ranked_ids: list, ids: tuple):
    """0-based rank of the best-placed id in ``ids`` within ranked_ids, else None."""
    wanted = set(ids)
    for idx, item_id in enumerate(ranked_ids):
        if item_id in wanted:
            return idx
    return None


def _build_store(embed=None) -> SqliteVectorStore:
    """Build one SqliteVectorStore over the WHOLE corpus (needle-in-haystack retrieval).

    ``embed=None`` -> the offline stdlib ``_HashingEmbedder`` (the committed path). The captained
    D020 run passes a real ``VoyageEmbedder`` here to measure recovery; that run lives in ``work/``.
    """
    store = SqliteVectorStore(embed=embed)
    for item in CORPUS:
        store.write(item)
    return store


def evaluate(case: SemanticCase, store: SqliteVectorStore, *, k: int = K) -> dict:
    """Run one case against ``store``; return recall@k / mrr / rank diagnostics."""
    hits = store.search(case.query, k=max(k, 20))  # extra depth for rank diagnostics
    ranked = [h.item_id for h in hits]
    gold = tuple(g for g in case.gold_item_ids if g in _CORPUS_IDS)
    topk = ranked[:k]
    return {
        "name": case.name, "kind": case.kind, "lens": case.lens,
        "recall_at_k": _recall_at_k(topk, gold),
        "mrr": _mrr(topk, gold),
        "gold_rank": _best_rank(ranked, gold),
        "distractor_rank": _best_rank(ranked, case.lexical_distractor_ids),
        "ranked": ranked,
    }


def score(embed=None) -> dict:
    """Aggregate retrieval metrics across all cases under ``embed`` (offline by default)."""
    store = _build_store(embed=embed)
    try:
        results = [evaluate(c, store) for c in SEMANTIC_CASES]
    finally:
        store.close()

    div = [r for r in results if r["kind"] == DIVERGENCE]
    ctl = [r for r in results if r["kind"] == CONTROL]

    def _mean(rows, key):
        return sum(r[key] for r in rows) / len(rows) if rows else 0.0

    by_lens: dict = {}
    for r in div:
        b = by_lens.setdefault(r["lens"], {"n": 0, "missed": 0})
        b["n"] += 1
        if r["recall_at_k"] == 0.0:
            b["missed"] += 1

    return {
        "results": results,
        "n_cases": len(results), "n_divergence": len(div), "n_control": len(ctl),
        "divergence_recall_at_k": _mean(div, "recall_at_k"),
        "control_recall_at_k": _mean(ctl, "recall_at_k"),
        "divergence_mrr": _mean(div, "mrr"),
        "control_mrr": _mean(ctl, "mrr"),
        "by_lens": by_lens,
    }


# --------------------------------------------------------------------------- #
# Tests — contract well-formedness + the headroom anti-theater guarantee
# --------------------------------------------------------------------------- #
class SemanticFixtureContractTests(unittest.TestCase):
    def test_corpus_is_well_formed(self) -> None:
        self.assertEqual(len(CORPUS), _EXPECTED_CORPUS,
                         "corpus size changed — update _EXPECTED_CORPUS deliberately")
        ids = [m.item_id for m in CORPUS]
        self.assertEqual(len(ids), len(set(ids)), "duplicate corpus item_id")
        for m in CORPUS:
            self.assertTrue(m.content.strip(), f"{m.item_id}: empty content")

    def test_cases_are_well_formed(self) -> None:
        self.assertEqual(len(SEMANTIC_CASES), _EXPECTED_CASES,
                         "case count changed — update _EXPECTED_CASES deliberately")
        seen: set = set()
        for i, c in enumerate(SEMANTIC_CASES):
            self.assertTrue(c.name, f"case[{i}] missing name")
            self.assertNotIn(c.name, seen, f"duplicate case name {c.name!r}")
            seen.add(c.name)
            self.assertIn(c.kind, _VALID_KINDS, f"{c.name}: bad kind {c.kind!r}")
            self.assertIn(c.lens, _VALID_LENSES, f"{c.name}: bad lens {c.lens!r}")
            self.assertTrue(c.query.strip(), f"{c.name}: empty query")
            self.assertTrue(c.note.strip(), f"{c.name}: every case must carry a note")
            self.assertTrue(c.gold_item_ids, f"{c.name}: needs >=1 gold id")
            for gid in c.gold_item_ids:
                self.assertIn(gid, _CORPUS_IDS, f"{c.name}: gold id {gid!r} not in corpus")
            for did in c.lexical_distractor_ids:
                self.assertIn(did, _CORPUS_IDS, f"{c.name}: distractor id {did!r} not in corpus")

    def test_control_lens_is_lexical(self) -> None:
        for c in SEMANTIC_CASES:
            if c.kind == CONTROL:
                self.assertEqual(c.lens, "lexical_control", f"{c.name}: control must use lexical_control lens")


class SemanticHeadroomTests(unittest.TestCase):
    """The anti-theater core: enforced against the REAL offline (hashing) path."""

    def test_divergence_cases_defeat_the_offline_embedder(self) -> None:
        # Every divergence case MUST miss gold in top-k offline (recall@k == 0). If the offline
        # char-n-gram embedder can find it, the case proves no semantic headroom and does not
        # belong in this pool — fail loudly rather than inflate the divergence count.
        s = score()
        offenders = [(r["name"], round(r["recall_at_k"], 2), r["gold_rank"])
                     for r in s["results"] if r["kind"] == DIVERGENCE and r["recall_at_k"] > 0.0]
        self.assertEqual(offenders, [],
                         "divergence cases the OFFLINE embedder can already retrieve (recall@k>0) — "
                         f"they demonstrate no semantic headroom; drop or reclassify: {offenders}")

    def test_control_cases_are_found_by_the_offline_embedder(self) -> None:
        # Every control case MUST be fully retrieved offline — otherwise "divergence fails" could
        # just mean the harness/embedder is broken. Controls are the apparatus check.
        s = score()
        offenders = [(r["name"], round(r["recall_at_k"], 2))
                     for r in s["results"] if r["kind"] == CONTROL and r["recall_at_k"] < 1.0]
        self.assertEqual(offenders, [],
                         "control cases the OFFLINE embedder failed to fully retrieve — the harness "
                         f"cannot be trusted to detect retrieval success: {offenders}")

    def test_headroom_gap_is_maximal_offline(self) -> None:
        # The whole point: offline divergence recall == 0 while control recall == 1. The size of
        # this gap is the headroom a real (semantic) embedder must recover in the captained D020 run.
        s = score()
        self.assertEqual(s["divergence_recall_at_k"], 0.0,
                         "offline divergence recall@k must be 0 (full headroom)")
        self.assertEqual(s["control_recall_at_k"], 1.0,
                         "offline control recall@k must be 1 (apparatus works)")

    def test_pool_is_not_vacuous(self) -> None:
        s = score()
        self.assertGreaterEqual(s["n_divergence"], 12,
                                "need a meaningful divergence pool to measure an embedder")
        self.assertGreaterEqual(s["n_control"], 4, "need positive controls")


# --------------------------------------------------------------------------- #
# Baseline report (offline floor + the headroom a real embedder must recover)
# --------------------------------------------------------------------------- #
def _pct(x: float) -> str:
    return f"{round(100 * x)}%"


def _report() -> None:
    s = score()
    print(f"Semantic-retrieval eval (D019/D020) — {s['n_cases']} cases "
          f"({s['n_divergence']} divergence, {s['n_control']} control) over a "
          f"{len(CORPUS)}-memory haystack. OFFLINE _HashingEmbedder (the floor).\n")

    print(f"{'case':40} {'kind':10} {'lens':16} {'rec@'+str(K):>6} {'mrr':>5} {'gRank':>6} {'dRank':>6}")
    for r in s["results"]:
        gr = "-" if r["gold_rank"] is None else r["gold_rank"]
        dr = "-" if r["distractor_rank"] is None else r["distractor_rank"]
        print(f"{r['name']:40.40} {r['kind']:10} {r['lens']:16} "
              f"{r['recall_at_k']:6.2f} {r['mrr']:5.2f} {str(gr):>6} {str(dr):>6}")

    print("\nOffline floor (what a real embedder must beat in the captained D020 run):")
    print(f"  divergence recall@{K} = {s['divergence_recall_at_k']:.3f}   MRR = {s['divergence_mrr']:.3f}"
          "   <- headroom (lower is more room)")
    print(f"  control    recall@{K} = {s['control_recall_at_k']:.3f}   MRR = {s['control_mrr']:.3f}"
          "   <- apparatus check (must be ~1.0)")
    print("\n  divergence cases defeated offline, by lens:")
    for lens, b in sorted(s["by_lens"].items()):
        print(f"    {lens:18} {b['missed']}/{b['n']}")
    print("\nNote: offline embedders are char-n-gram lexical, so this proves HEADROOM only. The real "
          "hashing-vs-Voyage measurement is captained (D020), run from work/ with a live key — never in CI.")


if __name__ == "__main__":
    _report()
