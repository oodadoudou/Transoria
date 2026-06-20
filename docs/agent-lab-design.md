# Transoria Agent Lab Design

Status: Experimental fork proposal
Audience: Future implementation agent
Scope: Design only. This document does not describe current shipped behavior.

## Purpose

Transoria Agent Lab is an experimental workflow layer for novel translation. It
should let a user start and guide a complete translation project through
conversation instead of manually running glossary extraction, glossary review,
translation, proofreading, replacement, and EPUB rebuild steps one by one.

The core idea is not to replace Transoria's existing tools. The Agent Lab
should orchestrate them:

- Read a source folder.
- Inspect EPUB/TXT inputs.
- Build a project plan.
- Extract terminology candidates.
- Ask the user to confirm important terminology and style choices.
- Translate a sample.
- Ask the user to approve or adjust style.
- Run the full translation.
- Run QA passes.
- Produce final EPUB/TXT outputs, glossary artifacts, QA reports, and a
  resumable project state.

This belongs in a separate fork or experimental branch. The stable Transoria
project should remain focused on deterministic tools, explicit user controls,
and low-risk maintenance.

## Design Goals

- Reduce user workflow complexity while preserving user control.
- Reuse existing Transoria parsing, glossary, translation, review, model
  profile, prompt, and task infrastructure wherever possible.
- Make each major workflow stage resumable and auditable.
- Keep user-visible quality risks explicit, especially when a weak or cheap
  model is selected.
- Support human checkpoints before expensive or irreversible stages.
- Keep the workflow recoverable after crashes, model failures, or user stops.
- Treat prompt presets and model presets as first-class workflow inputs.
- Preserve existing translation quality safeguards unless a stage explicitly
  adds stronger checks.

## Non-Goals

- Do not merge the Agent Lab into the stable application before it has been
  validated on real projects.
- Do not silently change the existing Translation, Glossary Extraction, or
  Glossary Review behavior.
- Do not require users to learn a new project file format before starting.
- Do not make the workflow fully autonomous by default.
- Do not claim that the agent can compensate for a weak model's language
  ability.
- Do not store temporary planning state in long-term settings schemas.
- Do not put LLM behavior inside `transoria/formats/` or deterministic
  `transoria/tools/` modules.

## User Experience

The intended interaction should feel like:

```text
User:
Process this folder:
/Users/me/Novels/MyBook
Korean to Simplified Chinese. Natural modern romance style.

Agent:
I found 6 EPUB files. I will extract terms first, then ask you to confirm the
glossary before translating. Output will go to:
/Users/me/Novels/MyBook/Transoria Output

Agent:
Here are the top terms and names. Please confirm the highlighted uncertain
items.

User:
Use "玄秀" for 현수. Keep Korean honorifics only when they matter emotionally.

Agent:
Updated. I will translate a 1,500 character sample for style approval.

User:
Continue.
```

The user should not need to manually export a glossary, import it elsewhere,
run proofreading, regenerate outputs, or remember ordering rules. Those remain
internal workflow steps.

## Workflow Overview

```text
Conversation Request
  -> Project Scan
  -> Plan Proposal
  -> Term Extraction
  -> User Term Checkpoint
  -> Style Preset Selection
  -> Sample Translation
  -> User Style Checkpoint
  -> Full Translation
  -> QA Review
  -> Optional Repair Passes
  -> Output Build
  -> Final Report
```

Each stage should write a compact state snapshot to disk. The agent should be
able to resume from the last completed checkpoint without redoing expensive
work.

## Checkpoint Policy

The default workflow should require confirmation at these checkpoints:

- Project plan confirmation before model calls.
- Glossary confirmation before full translation.
- Sample translation confirmation before full translation.
- Final overwrite confirmation when output paths already exist.

The user may opt into a faster mode, but the default should stay conservative.

## Model Capability Strategy

Agent Lab should support model tiering. A single model can run the whole
workflow, but the recommended setup is split by task difficulty.

### Economy Model Tasks

Suitable for cheaper or weaker models:

- Folder scan summary.
- File classification.
- Mechanical report summarization.
- Simple chunk metadata.
- Non-final low-stakes drafts.

### Standard Model Tasks

Suitable for mid-tier models:

- Terminology candidate extraction.
- Chapter summaries.
- Initial translation drafts.
- Basic consistency checks.

### Quality Model Tasks

Recommended for stronger models:

- Final terminology review.
- Difficult translation segments.
- Style rewrite.
- Ambiguous pronoun and relationship resolution.
- Final QA that judges fluency and semantic correctness.

### User Warning Policy

If the selected model profile is known or marked as weak, the agent should
warn:

```text
This model is suitable for low-cost drafts, but it may reduce final
translation quality. Use a stronger model for terminology review and final QA
if this project needs polished output.
```

The warning should not block execution.

## Presets

Agent Lab needs two preset families.

### Prompt Presets

Prompt presets define stage behavior. They should be versioned and treated as
workflow inputs, not hidden implementation details.

Recommended preset kinds:

- `agent.project_scan`
- `agent.term_extract`
- `agent.term_review`
- `agent.style_bible`
- `agent.sample_translate`
- `agent.translate`
- `agent.qa_terms`
- `agent.qa_style`
- `agent.qa_mechanical`
- `agent.repair`
- `agent.final_report`

Each preset should define:

- Stage purpose.
- Required input fields.
- Expected output schema.
- Failure/retry rules.
- Whether the stage may modify project state automatically.

### Model Presets

Model presets map workflow stages to existing Transoria model profiles.

Example:

```json
{
  "name": "Balanced Novel Translation",
  "stages": {
    "project_scan": "cheap-fast",
    "term_extract": "standard-long-context",
    "term_review": "quality-model",
    "sample_translate": "quality-model",
    "translate": "standard-long-context",
    "qa_terms": "quality-model",
    "qa_style": "quality-model",
    "qa_mechanical": "cheap-fast"
  }
}
```

The implementation should reuse existing model profile storage for provider
credentials. Agent Lab should store only the stage-to-profile mapping and any
workflow-specific runtime choices.

## Project State

Agent state should live in task cache or a dedicated project cache, not in app
settings.

Suggested path:

```text
<cache_root>/agent_projects/<project_id>/
  project.json
  plan.json
  checkpoints.json
  glossary/
    candidates.json
    confirmed.json
    references.txt
  style/
    style_bible.json
    sample_source.txt
    sample_translation.txt
  translation/
    chapter_summaries.json
    segment_status.json
    outputs/
  qa/
    term_issues.json
    style_issues.json
    mechanical_issues.json
    final_report.md
  logs/
    stage_events.jsonl
    model_calls/
```

### `project.json`

Minimum fields:

- `project_id`
- `created_at`
- `updated_at`
- `source_root`
- `output_root`
- `source_language`
- `target_language`
- `chinese_output_form`
- `status`
- `active_stage`
- `selected_files`
- `model_preset_id`
- `prompt_preset_versions`

### `plan.json`

Minimum fields:

- Ordered source files.
- Detected source types.
- Estimated segment counts.
- Estimated model-call stages.
- Output paths.
- Required checkpoints.
- Risk warnings.

### `checkpoints.json`

Minimum fields:

- Checkpoint id.
- Stage.
- Status: `pending`, `approved`, `rejected`, `skipped`.
- User notes.
- State hash or version reference.
- Timestamp.

## Stage Details

### 1. Project Scan

Inputs:

- Source folder path.
- Optional output folder.
- Optional source/target languages.
- Optional user style instruction.

Behavior:

- Scan recursively for supported `.epub` and `.txt` files.
- Exclude generated outputs and obvious cache folders.
- Detect file order using existing sort heuristics where possible.
- Read lightweight metadata only.
- Estimate file count, segment count, and output names.
- Produce a plan before running expensive model calls.

Existing code to reuse:

- Format parsers under `transoria/formats/`.
- Existing scan/order logic from translation and glossary workflows.
- Existing task cache utilities.

Checkpoint:

- Ask user to confirm selected files, output folder, and language direction.

### 2. Term Extraction

Inputs:

- Selected source files.
- Language direction.
- User-provided hints.
- Term extraction prompt preset.

Behavior:

- Reuse current Glossary Extraction workflow where possible.
- Collect candidates with source text references.
- Prefer structured rows over free text.
- Preserve existing normalization and filtering logic.
- Record decode issues instead of hiding them.

Candidate fields:

- `src`
- `dst`
- `type`
- `info`
- `aliases`
- `frequency`
- `references`
- `confidence`
- `needs_user_review`

Compatibility:

- Existing Translation glossary imports still need `src`, `dst`, `info`,
  `case_sensitive`, `enabled`, and `frequency`.
- Extended fields can live in Agent Lab project state until the stable app has
  a reason to support them.

Checkpoint:

- Show uncertain names, high-frequency terms, identical source/destination
  rows, and terms with conflicting suggestions.

### 3. Term Review

Inputs:

- Candidate glossary.
- User corrections.
- Existing glossary if present.

Behavior:

- Merge user corrections.
- Keep original model suggestions for audit.
- Mark confirmed terms separately from rejected terms.
- Generate an import-compatible glossary for current Translation workflow.

Quality rule:

- Confirmed glossary entries should override later model suggestions.
- If a weak model is used for review, flag the glossary as lower confidence.

### 4. Style Bible

Inputs:

- Source samples.
- User style instructions.
- Genre preset.
- Confirmed glossary.

Behavior:

- Produce a concise project style bible.
- Avoid overfitting to one chapter.
- Include concrete translation rules, not vague taste statements.

Suggested fields:

- `genre`
- `narration_style`
- `dialogue_style`
- `honorific_policy`
- `name_policy`
- `romance_or_intimacy_policy`
- `profanity_policy`
- `punctuation_policy`
- `do_not_do`

Checkpoint:

- Ask the user to approve or edit major style rules.

### 5. Sample Translation

Inputs:

- One or more representative source slices.
- Confirmed glossary.
- Style bible.
- Translation preset.

Behavior:

- Translate a small sample before full translation.
- Prefer a section with dialogue, names, and narration.
- Show source and translation side by side.

Checkpoint:

- User can approve, revise style instruction, or request another sample.

### 6. Full Translation

Inputs:

- Existing Translation workflow settings.
- Confirmed glossary.
- Style bible.
- Chapter summaries.
- Model preset.

Behavior:

- Reuse current Translation workflow as much as possible.
- Inject generated glossary and style bible into prompt context.
- Keep existing text preserve and replacement order.
- Keep existing low-confidence checks and retry behavior.
- Keep pause/stop/continue semantics.

Important:

- The Agent Lab orchestrator should not bypass the current Translation
  safeguards.
- If it needs new prompt context, pass it through workflow-level prompt
  assembly, not by editing output post hoc.

### 7. QA Review

QA should be split into layers.

Mechanical QA:

- Missing output files.
- EPUB structure validation.
- Segment count mismatch.
- Source residue.
- Japanese kana in Chinese output.
- Repeated output drift.
- Length-ratio anomaly as a low-quality reason, not a hard filter.

Term QA:

- Confirmed terms missing.
- Conflicting translations for the same source term.
- Different source terms collapsed into one translation.
- Name/title inconsistencies.

Style QA:

- Dialogue stiffness.
- Tone drift.
- Over-literal Korean syntax.
- Inconsistent honorific handling.
- Emotionally important ambiguity.

The QA result should distinguish:

- Must fix.
- Suggested fix.
- Informational risk.
- False-positive candidate.

### 8. Repair Pass

Inputs:

- QA issue list.
- Original source segments.
- Current translations.
- Confirmed glossary.
- Style bible.

Behavior:

- Repair only affected segments.
- Never rewrite a whole book because a small QA issue exists.
- Keep before/after records.
- Re-run relevant QA after repair.

Checkpoint:

- For high-risk repair batches, ask the user before applying.

### 9. Output Build

Behavior:

- Reuse current output writers.
- Preserve EPUB structure.
- Write translated files.
- Optionally write bilingual files if user requested.
- Write final glossary and QA reports.
- Open output folder if the user enabled it.

Overwrite policy:

- Never overwrite user files silently.
- Confirm exact overwrite paths before replacing.

## Conversation Interface

The Agent Lab can be exposed as either:

- A chat panel in the app.
- A command palette style workflow runner.
- A CLI/chat hybrid for early experiments.

Minimum user commands:

- Start a project from a folder.
- Show current plan.
- Continue from checkpoint.
- Pause.
- Stop.
- Resume.
- Show glossary candidates.
- Apply term correction.
- Translate sample.
- Approve checkpoint.
- Run full translation.
- Run QA.
- Build output.

The agent should show short, concrete progress updates:

```text
Scanning 6 EPUB files.
Extracting terminology from 184 source chunks.
Waiting for glossary confirmation.
Translating chapter 4 of 17.
Running term QA on 1,246 translated segments.
```

## Backend Architecture

Suggested modules for the experimental fork:

```text
transoria/agent/
  orchestrator.py
  project_store.py
  planner.py
  checkpoints.py
  stage_runner.py
  presets.py
  schemas.py
  qa.py
  reports.py

transoria/workflows/agent/
  project_scan.py
  term_extract.py
  term_review.py
  style_bible.py
  sample_translation.py
  full_translation.py
  qa_review.py
  repair.py
  output_build.py

transoria/bridge/handlers/agent.py
frontend/src/pages/agent-lab/
```

Boundary rules:

- `transoria/agent/` owns orchestration state and stage transitions.
- `transoria/workflows/agent/` composes existing workflows and LLM calls.
- Existing `formats/` and `tools/` remain deterministic.
- Existing Translation and Glossary workflows should not import Agent Lab.
- Bridge handler exposes agent methods but does not contain stage logic.
- Frontend state displays project state and sends user checkpoint decisions.

## Bridge API Sketch

Suggested methods:

```text
agent.create_project
agent.read_project
agent.update_project_options
agent.start_stage
agent.pause_project
agent.stop_project
agent.resume_project
agent.approve_checkpoint
agent.reject_checkpoint
agent.apply_glossary_edits
agent.read_glossary_candidates
agent.read_sample_translation
agent.read_qa_report
agent.read_final_artifacts
agent.list_recent_projects
agent.delete_project_cache
```

Requests and responses should use lower-snake-case keys and the existing
bridge error envelope.

## Frontend Design

The first experimental UI should be operational, not decorative.

Recommended layout:

- Project header: source folder, output folder, status, active stage.
- Conversation panel: user instructions and agent updates.
- Stage timeline: scan, glossary, style, sample, translation, QA, output.
- Checkpoint panel: current decision needed.
- Artifact panel: glossary, sample, QA report, final outputs.
- Model/prompt panel: selected preset and per-stage overrides.

Important UX rules:

- Always show when the agent is waiting for user approval.
- Do not hide model quality warnings.
- Do not bury overwrite confirmations in logs.
- Keep long tables virtualized or paginated.
- Keep large project snapshots out of hot render loops.

## Quality Safeguards

Minimum safeguards:

- No silent overwrite.
- No full translation before glossary/style checkpoints in default mode.
- No hidden downgrade when a selected model lacks required context length.
- No silent loss of failed segments.
- No post-processing that changes glossary terms without recording it.
- No disabling existing low-confidence checks to make the agent look smoother.

## Observability

Each stage should produce:

- Input summary.
- Prompt preset version.
- Model profile id.
- Runtime parameters.
- Output summary.
- Issues and warnings.
- Retry count.
- Token/cost estimate when available.
- Paths to artifacts.

Model-call logs should follow existing debug-log patterns and must avoid
leaking API keys.

## Failure Handling

Stage failures should be recoverable.

Examples:

- If term extraction partially fails, keep successful candidate batches and
  offer retry for failed chunks.
- If sample translation fails, let the user switch model or prompt preset.
- If full translation fails, preserve completed subtasks and expose continue.
- If QA repair fails, keep the previous translation as authoritative.
- If output build fails, do not discard translated cache.

## Compatibility With Stable Transoria

The fork should reuse stable code through adapters, not invasive rewrites.

Safe reuse:

- EPUB/TXT parsing.
- Current glossary extraction as a stage.
- Current Translation workflow as a stage.
- Current Glossary Review workflow when useful.
- Prompt stores.
- Model profile stores.
- Task cache infrastructure.
- EPUB compressor, merger, metadata tools as optional utilities.

Avoid early changes to:

- Global settings schema.
- Translation glossary schema in stable UI.
- Format parser behavior.
- Model profile credential storage.
- Shared task lifecycle semantics.

## Implementation Phases

### Phase 0: Fork Setup

- Create experimental fork or branch.
- Add this design document and handover document.
- Confirm all existing tests pass before changes.
- Add an `agent` feature flag or hidden route.

### Phase 1: Project Store And Planner

- Implement project cache.
- Implement folder scan and plan generation.
- Add bridge methods for create/read/list.
- Add a minimal frontend project page.
- No LLM calls yet.

### Phase 2: Glossary Stage

- Reuse current glossary extraction.
- Store candidates and references in project cache.
- Add glossary confirmation UI.
- Export confirmed glossary in current Translation-compatible shape.

### Phase 3: Style And Sample Stage

- Add style bible prompt preset.
- Add sample translation stage.
- Add checkpoint approval and user notes.
- Keep output small and inspectable.

### Phase 4: Full Translation Stage

- Wire confirmed glossary and style bible into Translation workflow.
- Reuse pause/stop/continue.
- Store stage logs and artifact paths.

### Phase 5: QA And Repair

- Add mechanical QA first.
- Add term QA second.
- Add style QA only after prompt results are stable.
- Add repair pass only for selected segments.

### Phase 6: Polish And Evaluation

- Build golden sample projects.
- Compare model presets.
- Add cost/runtime reporting.
- Add final report export.

## Test Strategy

Unit tests:

- Project store read/write/upgrade.
- Checkpoint state transitions.
- Plan generation from synthetic folders.
- Stage preset validation.
- Glossary merge and correction application.
- QA issue classification.

Integration tests:

- Agent project from two tiny TXT files.
- Agent project from tiny EPUB fixtures.
- Resume after failed term extraction batch.
- Resume after stopped translation stage.
- Output build from cached translation.

Manual tests:

- Real Korean EPUB novel folder.
- Model switch after sample rejection.
- Weak model warning.
- User glossary correction before full translation.
- Output overwrite confirmation.

Regression samples:

- Dialogue-heavy chapter.
- Term-heavy chapter.
- Ambiguous pronoun chapter.
- Long sentence chapter.
- EPUB with cover/nav quirks.

## Success Criteria

The Agent Lab is worth keeping if it can:

- Start from a folder and finish with valid translated outputs.
- Preserve existing Translation safeguards.
- Make terminology confirmation easier than the current manual workflow.
- Resume after interruption without losing completed work.
- Show clear quality risks when model choice is weak.
- Produce reports that help the user decide whether output is final quality.

It is not successful if it only hides complexity while making failures harder
to inspect.
