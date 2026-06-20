# Translation Runtime Experiments Plan

Status: Design proposal, not implemented
Last reviewed: 2026-06-20

This document records future translation-runtime experiments that need real
measurements before they should become shipped behavior. The current
implementation remains the source of truth; this page is a planning document.

The current baseline already includes bounded transport retries, streamed
OpenAI-compatible requests, connection pooling, request logs, micro-batch
low-confidence rescue, source-residue safeguards, split-on-failure, and a
default low-confidence retry count of 3.

## Evidence Checked

The plan below is based on the current implementation shape:

- The translation runner builds a stable system prompt from preset and target
  language, while chunk-specific glossary, context, retry banners, and source
  rows live in the user prompt. Runtime memory must therefore stay in the user
  prompt to avoid breaking system-prompt cache stability.
- The low-confidence path already retries compact micro-batches before isolated
  solo rows, and its total rescue-call budget is derived from the configured
  low-confidence retry count.
- The orchestrator collects translations from completed subtask
  `response_content` payloads and writes outputs from that merged map.
- The shared task executor marks subtasks as whole units: pending, running,
  completed, failed, skipped, stopped, or paused. It does not currently have a
  partial-row success API.
- Proofreading and output regeneration reconstruct translations from cached
  subtask responses. They do not currently read a canonical per-segment result
  store.
- Chunking is line-count based with an optional token cap. Context and glossary
  matching are assembled per chunk, so globally smaller chunks would change the
  amount of nearby narrative context available to the model.

## Non-Goals And Invariants

- Do not weaken source-residue, mass-echo, line-count, duplicate-drift, or
  low-confidence safeguards.
- Do not globally shrink translation chunks by default.
- Do not hardcode any target language in prompts, retry hints, validation, or
  comments.
- Do not move runtime memory into the system prompt. The system prompt must
  stay byte-stable inside one task so provider prompt caching can keep working.
- Do not add user-facing settings until an experiment proves that a switch is
  useful and the default is safe.
- Keep segment order, segment ids, and output regeneration stable.
- Preserve old task caches and settings compatibility.

## Current Architecture Constraint

Today the canonical translated text is reconstructed from completed subtask
responses. Each completed subtask stores a `response_content` payload whose
`translations` map is merged by the orchestrator, proofreading bridge, and
output regeneration path.

That means sparse segment commit cannot be implemented only inside the
translation runner. It requires a segment-level result store first, otherwise a
partially successful chunk has nowhere authoritative to persist good rows while
bad rows are requeued.

## F1: Runtime Term And Context Memory

Status: future experiment.

Goal: give later chunks a compact memory of already confirmed translations
without replacing the reviewed glossary and without raising token cost
unboundedly.

Recommended first version:

- Keep the reviewed glossary as the hard source of terminology constraints.
- Add a compact runtime memory block only to the user prompt.
- Include only terms that are relevant to the current chunk.
- Treat each glossary source form as independent. Do not merge full names,
  nicknames, surnames, organizations, places, or aliases.
- Prefer strong evidence:
  - reviewed glossary rows
  - user-edited proofreading rows
  - clean completed rows where the source term and target form are both
    detected
- Treat local term-audit output as evidence for review, not as automatic
  glossary mutation.
- Use a strict entry count and byte cap.
- Sort entries stably so repeated prompts are as cache-friendly as possible.

The memory block should contain target-language-neutral instructions such as:

```text
Runtime terminology memory:
- src: <source form>
  preferred: <observed or reviewed target form>
  evidence: reviewed glossary | user edit | previous clean segment
```

This still increases user-prompt tokens, so it should be default-off or
development-only until measured. It should not be placed in the system prompt;
system-prompt caching is more valuable than caching a frequently changing
memory block.

Later context memory is higher risk. Speaker, gender, relationship, faction, or
scene facts can improve consistency, but bad inferred facts can damage a whole
book. If tested, context facts must include evidence segment ids, confidence,
and soft wording. They should never override explicit glossary rows or source
text.

Metrics:

- input token delta
- cached input tokens
- request count and wall time
- `glossary_not_applied` count
- `term_inconsistency` count
- `source_residue` and `low_confidence` counts
- human spot-check pass rate on repeated names and dialogue

## Sparse Segment Commit

Status: future experiment, medium to high risk.

Goal: commit good rows from a partially bad chunk into the task cache, then
requeue only the bad rows. One bad row should not block progress for the other
rows in the same chunk.

This is different from the current split-on-failure path. Today a subtask is
completed or failed as a whole. Sparse commit needs a segment-level truth store.

Recommended phases:

1. Add a shadow segment result index.
   - Store one record per segment under the task cache.
   - Suggested fields: `segment_id`, `source_hash`, `translation`, `status`,
     `quality_tags`, `quality_reasons`, `subtask_id`, `request_event_ids`,
     `attempt`, `run_id`, and `updated_at`.
   - Keep existing subtask responses as the active reader.
   - Add tests proving the shadow index matches the current merged subtask
     response map.
2. Switch readers to the segment index.
   - Proofreading, output regeneration, statistics, and final writers should
     read the segment index first.
   - Old caches should fall back to subtask responses.
3. Enable partial row commit.
   - The runner or executor must return good rows and bad rows separately.
   - Good rows are written atomically to the segment index.
   - Bad rows are requeued as smaller derived subtasks.
4. Make continue/resume segment-aware.
   - Completed rows with matching `source_hash` are skipped.
   - `needs_retry` and `failed` rows are requeued.
   - Progress is based on source segments, not parent and child subtasks.

Required guards:

- Every segment write must check `run_id` and `source_hash`.
- A late parent subtask must not overwrite a newer child result.
- A child may replace a parent result only for the same source hash and current
  run.
- Token usage remains request-based; do not double count usage per segment.

Tests:

- Crash after writing some segment rows but before the subtask reaches a
  terminal status.
- Continue reuses committed rows and requeues only incomplete rows.
- Split parent and child writes cannot overwrite each other incorrectly.
- Output regeneration preserves original segment order.
- Old task caches still open in proofreading and can regenerate outputs.

This should not ship until the shadow-index phase has run long enough to prove
that the new segment truth matches the existing output.

## Adaptive Failed-Block Shrink

Status: future experiment, default-off until measured.

Goal: reduce the blast radius of a bad chunk only after evidence shows the
current block is failing. This is not a global small-chunk strategy.

Do not lower the default chunk size for all translation. Smaller chunks reduce
single-request failure cost, but they increase request count, can reduce
narrative continuity, and can increase total input tokens.

Possible triggers:

- `mass_source_residue_after_batch`
- repeated low-confidence budget exhaustion
- repeated provider timeout or provider 5xx on the same profile
- localized line-count or format failure on one chunk

Strategy:

- First use the current quality rescue and split path.
- If the same failed block remains problematic, requeue only that failed block
  into smaller groups, such as 4 to 8 lines or a conservative token cap.
- Preserve nearby context lines and matched glossary entries for each retry
  group.
- Keep this adaptive shrink scoped to failed or requeued blocks, not future
  unrelated chunks.

Go/no-go metrics:

- final task completion rate
- final failed chunk count
- request count
- input, output, and cached input tokens
- request P50, P95, and max duration
- quality tag counts
- human spot-check quality on context-sensitive scenes

Enable broader behavior only if failure reduction is meaningful and token cost
plus quality risk stay bounded on representative Korean and Japanese samples.

## Experiment Order

1. Collect request-log and proofreading-risk baselines on real tasks.
2. Test compact runtime term memory behind a non-default experimental path.
3. Add the segment result shadow index and prove parity with current output.
4. Switch readers to the segment index with old-cache fallback.
5. Add sparse segment commit and requeue only failed rows.
6. Test adaptive failed-block shrink only for failed blocks.
7. Consider context-fact memory only if term memory and term audit do not solve
   enough real consistency problems.

## Verification Plan

Automated tests should use fake transports and deterministic fixtures. Live API
tests are manual smoke tests, not unit tests.

Required automated coverage before implementation ships:

- runtime memory prompt assembly keeps the system prompt unchanged
- memory entries are target-language-neutral and capped
- segment index write/read/fallback behavior
- sparse commit idempotency across crash and continue
- adaptive shrink preserves segment order, context, and glossary matching
- proofreading and output regeneration read the same translated text

Manual live-test report fields:

- model profile and provider format
- concurrency, retry settings, timeout, and stream mode
- task id and source fixture
- request count
- failure count by error class
- wall time, P50, P95, and max request duration
- input, output, and cached input tokens
- final proofreading risk counts
- human spot-check notes
