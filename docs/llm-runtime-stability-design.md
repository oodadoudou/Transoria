# LLM Runtime Stability Design

Status: Implemented baseline + future experiment notes
Last reviewed: 2026-06-20

This document records the staged runtime improvement plan for Translation,
Glossary Extraction, and Glossary Review. The implementation and tests remain
the source of truth; this page distinguishes shipped baseline behavior from
future experiments.

## Goal

Improve high-concurrency provider reliability and timeout behavior without
weakening translation quality safeguards.

The target outcome is:

- fewer provider 5xx / loading / timeout failures on OpenAI-compatible custom
  endpoints
- request usage remains visible when streaming is enabled
- slow but progressing work is not killed by an aggregate subtask watchdog
- translation quality checks, partial accept, low-confidence rescue, source-residue
  detection, duplicate-drift detection, and output alignment remain unchanged

## Current Baseline

The shipped runtime already includes:

- OpenAI-compatible streaming by default for Translation, Glossary Extraction,
  and Glossary Review.
- `stream_options.include_usage` for compatible streaming endpoints, with a
  conservative one-time fallback when a provider rejects that field.
- Reused `httpx.AsyncClient` instances and tuned connection pool limits.
- High-concurrency request-start pacing to avoid launching large bursts at the
  same instant.
- Request events in `request-events.jsonl`, exposed through the pop-out request
  log for model status, duration, token usage, cached input tokens, errors, and
  final response text.
- Bounded retained request-log size and tail reads for normal polling.
- Per-request transport timeouts, while LLM workflow subtasks are no longer
  killed by a simple aggregate timeout that ignores internal progress.
  Run-page "longest running subtask" ages are wall-clock ages for the whole
  workflow subtask; they can exceed one request timeout when the runner is
  making bounded internal requests such as format repair, low-confidence
  rescue, or transport retries.
- Translation and Glossary Extraction cap effective same-key transport retries
  at 3 even when the module setting is higher; lower settings still reduce the
  budget.
- High-concurrency Translation handles provider transport timeouts
  conservatively: it does not keep resending the same timed-out batch request,
  and instead relies on the bounded rescue, split, and continue paths already
  used by the runner.
- Glossary Extraction uses a shorter internal soft timeout for extraction
  calls and, on timeout, performs one bounded split-rescue pass instead of
  retrying the same oversized prompt.

Remaining experiments are quality or provider-specific tradeoffs, not required
for the current stable path.

## Non-Goals

- Do not replace the translation runner wholesale.
- Do not remove partial accept, positional decode, low-confidence rescue,
  source-residue safeguards, duplicate-drift checks, or line-alignment checks.
- Do not hide provider failures by silently accepting empty model results.
- Do not add broad user-facing settings until an experiment proves a stable
  default or a real need for user control.
- Do not lower chunk size globally without measuring quality impact.

## Design Principles

1. Transport improvements are safe first. Request shape, connection reuse, and
   usage parsing should not change model instructions or source/translation
   content.
2. Runtime watchdogs should detect lack of progress, not punish legal quality
   recovery work.
3. Token-aware chunking is a quality tradeoff. Smaller requests reduce timeout
   risk, but can reduce narrative context and terminology continuity.
4. Provider pacing should smooth bursts before adding more retry attempts.
5. Every behavior change needs a rollback path and focused tests before broader
   verification.

## Phase 1: Streaming And Usage Parity

Status: implemented.

Scope:

- For OpenAI-compatible and custom endpoints, include
  `stream_options: {"include_usage": true}` when `ChatRequest.stream` is true.
- Preserve the current non-streaming request shape.
- Parse streamed usage into the same `TokenUsage` fields as non-streaming.
- Keep provider fallback conservative: if a provider rejects
  `stream_options` with a request-shape error, retry once without
  `stream_options` and record the compatibility path in the request log.

Expected benefit:

- Streaming can be enabled without losing token accounting.
- Compatible providers that return usage only in the final stream event become
  visible in statistics and request logs.

Quality risk:

- Low, if assembled streamed content is byte-equivalent to the non-stream
  response body content.

Tests:

- Unit test OpenAI-compatible stream payload contains `stream_options`.
- Unit test unsupported-stream-options fallback removes only that field.
- Unit test streamed usage populates input, output, and cached input tokens.
- Live smoke with one compatible custom endpoint: compare stream vs non-stream
  on the same small fixture and verify output decodes, usage is non-zero, and
  no quality tags increase unexpectedly.

## Phase 2: HTTP Client Pooling

Status: implemented.

Scope:

- Reuse `httpx.AsyncClient` instances across requests by transport identity:
  proxy, timeout mode, provider base host, and custom header/auth shape.
- Keep API keys out of cache keys that may be logged. Use an internal opaque
  identity if key-specific separation is needed.
- Provide an explicit close path for app shutdown and tests.
- Preserve injected fake transports for unit tests.

Expected benefit:

- Lower connection setup overhead.
- More stable high-concurrency behavior on providers sensitive to bursty new
  TLS connections.

Quality risk:

- Low. This changes network plumbing only.

Tests:

- Unit test two requests with the same transport identity reuse a client.
- Unit test different proxy/base endpoint identities do not share a client.
- Unit test close releases all cached clients.
- Existing LLM client retry, key rotation, and stream tests must stay green.

## Phase 3: Timeout Semantics

Status: partially implemented. The normal LLM workflows now disable aggregate
subtask timeouts; request-level timeouts bound individual provider calls, and
stop/pause remains bounded by the configured drain window. Stale live-task
reconciliation still relies on heartbeat/request-timeout evidence and must
remain conservative.

Implemented scope:

- Keep per-request HTTP timeouts.
- Disable aggregate subtask hard timeouts for normal LLM workflows that can
  perform multiple internal requests.
- Stop and pause must remain bounded. Stop can still cancel in-flight work
  after the configured drain window.

Remaining experiment:

- If real request logs show a runner can wedge without any request-level
  timeout, prefer an inactivity watchdog: fail a subtask only when no request
  progress, log append, retry transition, or runner heartbeat has occurred for
  a bounded window above the model request timeout.

Expected benefit:

- A subtask that is actively doing partial accept or low-confidence rescue no longer
  fails only because total elapsed time crossed the former aggregate
  `model.timeout + 10` style cutoff.
- Low-confidence rescue remains useful for normal suspicious segments but is
  bounded by `low_confidence_max_retries * 4` calls per chunk, derived from the
  user's quality-retry setting. Larger pending sets use compact micro-batch
  retries before isolated solo retries. Later runtime subtask attempts receive
  only one bounded rescue call instead of refreshing the full budget.
- Provider calls still terminate at the request-timeout layer. A runner that
  wedges outside an active provider request is still covered only by the
  conservative stale live-task reconciliation path until a future inactivity
  watchdog is implemented.
- In high-concurrency Translation, provider transport timeout failures are not
  blindly retried as the same batch request; the runner keeps those failures
  bounded and moves recovery to rescue, split, or continue behavior.

Quality risk:

- Medium if stale-running detection is too lax or too aggressive. This touches
  task lifecycle semantics.

Tests to keep or extend:

- Existing tests should prove multiple successful internal waits longer than one request
  timeout budget should complete when it emits progress.
- A future inactivity watchdog should add a fake-runner test where no progress
  beyond the inactivity window fails.
- Stop and pause tests must prove queued subtasks do not start after the user
  requests stop/pause.
- Continue after timeout must leave failed/pending subtasks recoverable.

## Phase 4: Token-Aware Chunk Experiment

Scope:

- Use the existing `chunk_token_limit` path, but do not enable a global small
  default immediately.
- Add an internal experiment flag or development-only override to compare:
  current line-count chunks, moderate token-capped chunks, and aggressive
  small chunks.
- Preserve context-line assembly and glossary matching for every chunk.
- Do not change output writers or segment ids.

Expected benefit:

- Shorter requests should reduce long-tail response latency and provider
  timeout risk.

Quality risk:

- Medium to high if chunks become too small. Novel translation can lose
  speaker continuity, terminology context, and nearby narrative cues.

Tests:

- Unit test token-capped chunking preserves segment order and never drops a
  segment.
- Unit test context and glossary sections remain attached to token-capped
  chunks.
- Compare the same fixture across chunk strategies:
  - completion rate
  - total wall time
  - request P50/P95 duration
  - input/output tokens
  - source-residue tags
  - low-confidence tags
  - line mismatch and fallback tags
  - proofreading term-audit risk count

Go/no-go rule:

- Do not enable by default unless timeout/5xx reduction is significant and
  quality-risk tags do not increase on representative Korean and Japanese
  samples.

## Phase 5: Provider Burst Smoothing

Status: implemented as a lightweight pacing baseline.

Scope:

- Translation, Glossary Extraction, and Glossary Review pass a shared launch
  spacing into the task executor.
- When configured concurrency is 8 or higher, new LLM subtasks are launched at
  least 0.05 seconds apart so dozens of requests do not start at the same
  instant.
- Configured concurrency and RPM limits still apply; the pacing only smooths
  request starts and does not add retries or change prompts.

Expected benefit:

- Fewer provider overload responses without lowering translation quality.

Quality risk:

- Low to medium. Pacing can make tasks slower if tuned too conservatively.

Tests:

- Unit test start times are paced under high concurrency.
- Unit test RPM and concurrency limits still cap correctly.
- Live smoke on a custom endpoint with a known high-concurrency profile.

## Measurement Plan

Automated tests must not use real network calls. Live provider validation should
be manual or smoke-only and must use disposable fixture inputs.

For every phase that touches runtime behavior, record:

- task id
- provider format
- stream enabled/disabled
- chunk strategy
- configured concurrency and RPM
- request count
- failed request count by status/error class
- request duration P50/P95/max
- input/output/cached input tokens
- final task status
- failed/skipped/completed subtask counts
- quality-risk tag counts

Minimum live matrix before enabling a default:

- one OpenAI-compatible custom endpoint
- one DeepSeek-compatible endpoint when available
- one OpenRouter-style compatible endpoint when available
- one small fixture and one long-book excerpt
- stream on/off where the provider supports both

## Rollback Plan

Each phase should be independently revertible:

- streaming usage can fall back to current stream payload
- client pooling can fall back to one client per request
- timeout semantics can fall back by re-enabling a conservative aggregate
  watchdog if request-level bounds prove insufficient
- token-aware chunking can remain disabled by default
- provider burst smoothing can be disabled by removing the dispatch limiter

No phase should require a settings schema migration for rollback.

## Current Follow-up Priority

1. Use request logs from real problematic providers to compare streaming on/off,
   duration percentiles, provider 5xx classes, and timeout behavior.
2. Keep token-aware chunking experimental until quality comparisons prove it
   does not increase source residue, glossary risk, format rescue, or
   low-confidence counts.
3. Tune provider pacing only when logs show burst-related failures after the
   current pooling/streaming baseline.
