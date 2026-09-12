# Data-analysis pipeline — what is done

Audited at `b60258c` (2026-08-24), working tree clean.
Every claim below was read from the code and re-verified by running it, not
taken from `backend/phase9plan.md` or `docs/phase9plan.html`.
Section numbers refer to `backend/phase9plan.md`; session numbers (S1–S14) refer
to `docs/phase9plan.html`.

Companion document: [remaining.md](remaining.md).

---

## Verification run at audit time

| Suite | Command | Result |
| --- | --- | --- |
| Backend, whole repo | `.venv/bin/python -m unittest discover -s tests -t . -q` | **873 passed**, 9 skipped, 0 failed (12.3 s) |
| Backend, Phase 9 only | same, `-p "test_data_analysis_phase9_*.py"` | **421 passed**, 0 failed (2.4 s) |
| Frontend types | `npm run typecheck` | clean |
| Frontend lint | `npm run lint` | 0 errors (2 pre-existing warnings in `components/SplitText.tsx`, unrelated) |
| Frontend parity/contract scripts | `npm run verify` | **all 6 green** — 12 cell-hash fixtures, 64 analysis-route checks, 19 execution-event checks, 39 result-preview checks, 24 revision-coordinator checks, Univer version pin |

The 9 skips are environmental, not gaps in logic: 8 need `MONGODB_TEST_URI`
(Atlas replica-set concurrency tests, `tests/test_phase8_mongo_replica_integration.py`)
and 1 needs `RUN_PYMUPDF_LAYOUT_BENCHMARK=1`.

### Live engine smoke test

Beyond the unit suite, the engine was driven directly through
`SubprocessNativeBackend` with a filter + sort recipe over a 5-row frame:

```
succeeded: True
engine: polars-1.43.2 | semantics: 2.0
rows: 4 | content hash: e76efff7534604d4…
  s1 (filter_rows): 5 -> 4 rows
  s2 (sort_rows):   4 -> 4 rows
replay hash identical: True
```

So the child process really runs, really computes, and replay is byte-identical.

---

## Capability gates — the real sequencing instrument

`backend/config/settings.py:208`

| Flag | Default | Meaning |
| --- | --- | --- |
| `ANALYSIS_NATIVE_EXECUTION_READY` | **`True`** | **Gate A is open.** Read-only plans are queued and executed by the Polars engine. |
| `ANALYSIS_WORKBOOK_PATCHES_READY` | `False` | **Gate B is closed.** A plan with a workbook write intent is admitted as `PLAN_ONLY` and stops at `plan_ready`. |

`runtime/bootstrap.py:109` builds one `ExecutorCapabilities` from these and hands
the same document to planning, the plan repository and execution admission, so
the three can never disagree. Cloudinary credentials are present in
`backend/.env`, so the blob store is constructed and results are genuinely
published rather than kept process-local.

---

## Phases 1–8 (inherited, all green)

| Area | State |
| --- | --- |
| PDF/table extraction, Docling fallback, table validation | shipped |
| Hybrid text + table retrieval, query generation, deterministic fusion | shipped |
| Requirements, evidence assessment, completion, repair | shipped |
| Profiling, preparation, normalization, versioning | shipped |
| Durable runs, state machine, MongoDB events, SSE replay, leases, pause/resume/cancel | shipped |
| Phase 8 typed planning, deterministic validation, HITL plan approval | shipped |
| Cloudinary artifacts, reconciler, privacy gateway, observability, index migrations | shipped |
| Spreadsheet import/export, workbook-per-workspace, menu bar, command layer | shipped |

---

## Phase 9, section by section

### 9.1 — Execution boundary and capability migration · **done**

- `runtime/models/capabilities.py` — versioned `native_spreadsheet_v1` profile,
  frozen, `extra="forbid"`. `python_execution`, `charts`, `images`,
  `machine_learning` are `Literal[False]`: they cannot be turned on by
  configuration, only by a code change.
- `supported_operations` is a closed 13-entry tuple, validated for uniqueness
  and membership.
- `runtime/execution/admission.py:103` `evaluate_admission` returns
  `REJECT` / `PLAN_ONLY` / `QUEUE`, with a typed reason code for every
  `PLAN_ONLY` (`native_execution_not_installed`,
  `workbook_patches_not_installed`, `operation_not_executable`).
- Planning success is separated from run completion: `runtime/repositories/plans.py:404`
  queues only when the capability profile says the engine exists.
- Selective early approval + mandatory final patch approval implemented via
  `ApprovalPolicy` (`plan_approval_required`, `final_patch_approval_required`,
  `auto_execute_read_only`).

### 9.2 — Plan Schema v2 and the typed expression AST · **done**

- `runtime/models/expressions.py` (405 lines) — the closed union: literal,
  column_ref, unary, binary, compare, set, between, boolean, case_when,
  coalesce, cast, date_part, date_trunc, string_transform, null_check. Every
  node is `extra="forbid"` and frozen; non-finite literals are rejected at the
  field validator.
- `safe_divide` *requires* an explicit `zero_division` policy and every other
  operator forbids one — an under-specified division cannot be constructed.
- `runtime/models/plans.py` (1,645 lines) carries both v2 steps and
  `Legacy*Step` twins (`LegacyDeriveColumnStep`, `LegacyFilterRowsStep`,
  `LegacyJoinStep`, `LegacyPivotStep`, `LegacyFillMissingStep`,
  `LegacyGenerateDatasetStep`), so v1 history still deserializes but is
  non-executable.
- `runtime/planning/canonicalization.py` + `runtime/models/canonical.py` —
  display-only fields (`label`, `output_label`) are excluded from the plan hash
  through a `display_only_fields` ClassVar, so renaming a column for the user
  does not invalidate an approval.
- Bounded repair: one deterministic-error repair call, then clarification or
  failure. No unlimited LLM loop.

### 9.3 — Durable input resolution and admission · **done**

- `runtime/execution/inputs.py` (340 lines) — `MongoNormalizedInputResolver`
  re-resolves normalized references by tenant + dataset + version after a
  restart; nothing depends on the worker's in-memory Phase 7 result.
- `runtime/execution/idempotency.py` — tenant-scoped `execution_key` over input
  content signatures + recipe hash + engine version + semantics version.
- Admission re-checks ownership, plan revision and hash, schema version,
  capability profile, input existence and versions, and cancellation state
  before allocating anything.

### 9.4 — Deterministic native execution engine · **done**

- `runtime/execution/native/engine.py` (367 lines) — Polars, pinned at
  **1.43.2**, recorded on every execution.
- **Stage fusion is real**: steps stay lazy and are evaluated in batches through
  one `collect_all`, so Polars applies common-subplan elimination across
  per-step row counts, semantic guard counters and the frame itself. A batch
  ends at a barrier (pivot, eager by API) or at the final result.
- `runtime/execution/native/subprocess_backend.py` — bounded child process; the
  recipe is handed over as `job.json`, results come back as a manifest, and the
  child imports nothing from repositories, storage or the web layer. The
  "no credentials in the child" guarantee is structural, not disciplinary.
- `InProcessNativeBackend` is retained for tests behind the same protocol.
- `runtime/execution/dag.py` — topological compile, typed failure on any
  operation outside `NATIVE_SUPPORTED_OPERATIONS`. A plan never half-executes.
- All 13 operations implemented under `native/operations/`: columns, rows,
  grouping, joining, sources.

### 9.5 — Exact operation semantics · **done**

- `runtime/execution/native/semantics.py` — `NATIVE_SEMANTICS_VERSION = "2.0"`
  covering timezone, date parsing, decimal precision and rounding mode, overflow,
  string normalization, stable ordering, null-vs-empty-string, NaN/infinity,
  locale and column-name collisions.
- The semantics version is part of the execution key, so a policy change
  invalidates the cache rather than silently reusing an older result.
- `tests/test_data_analysis_phase9_semantics.py` — 1,042 lines, 34 tests, the
  largest file in the phase: nulls, dates, decimals, duplicate keys, ordering,
  join expansion guards, pivot width caps.

### 9.6 — Seeded synthetic generation · **done**

- `runtime/execution/native/generation/` — `generator.py`, `rules.py`,
  `randomness.py`.
- Per-column seeds derived from the global seed and the stable column key, so
  adding or reordering a column does not change values already generated.
- Money in integer minor units, then decimal scale. Deterministic ID generation
  with uniqueness verification. Bounded constraint retries.
- 21 tests, including replay-hash identity and column-reorder invariance.

### 9.7 — Semantic formula compiler · **built, unit-tested, not reachable end to end**

- `runtime/formulas/` — `expressions.py` (326), `compiler.py` (283),
  `validation.py` (252), `native.py` (211), `safety.py` (203).
- Allowlisted function subset; `INDIRECT`/`OFFSET`/volatiles/external refs
  rejected. `neutralize_text` guards `=`, `+`, `-`, `@` injection and is used by
  `patches/grid.py`.
- `patches/compiler.py:102` accepts `formulas: tuple[FormulaSpec, ...]` and
  emits `fill_formula` operations from them.
- 31 tests pass.
- **The gap:** no v2 plan step or write intent carries a `FormulaSpec`, and
  `patch_service.py` never passes `formulas=`. See remaining.md item R6.

### 9.8 — Durable orchestration, fencing, replay · **mostly done**

- `runtime/models/executions.py` + `runtime/repositories/executions.py` —
  durable `AnalysisExecution` per revision, indexed by run.
- The lease attempt is the fencing token; a superseded worker's compare-and-set
  publication fails.
- `runtime/execution/publication.py` (330 lines) — bundle uploaded *before* the
  Mongo record is committed, so a crash leaves recoverable objects rather than a
  record promising a result that was never stored.
- Duplicate queue delivery produces one logical execution (cache-key hit,
  inputs resolved once) — directly asserted in
  `tests/test_data_analysis_phase9_execution.py`.
- Cancellation is honoured: `worker.py:1246` re-reads the durable flag inside
  execution and the subprocess is terminated.
- **Partial:** stage scheduling is an inline drain (no concurrent branches),
  `runtime/execution/checkpoints.py` is written and unit-tested but not wired
  into `execution/service.py`, and pause is not honoured mid-execution. See
  remaining.md items R7 and R8.

### 9.9 — Result validation, lineage, previews, Cloudinary · **done**

- `runtime/execution/results/` — `validation.py` (311), `serialization.py` (236),
  `publisher.py` (180), `reader.py` (142), `previews.py` (142), `lineage.py` (108).
- All six validation layers present: protocol, schema, assertion, resource,
  safety (formula-injection), hash.
- Durable bundle `result.csv.gz` + `result.schema.json` + `result.lineage.json`
  + `result.preview.json`; the schema manifest carries decimal scale,
  timezone and null encoding, and CSV round-trips are tested.
- MongoDB holds IDs, hashes, schemas, bounded metrics and redacted previews
  only — never full tables.
- Previews are redacted through the same privacy gateway as everything else.

### 9.10 — Workbook Patch Protocol v1 · **done (backend side)**

- `runtime/patches/` — 15 modules, 3,459 lines total.
- 8 implemented operations: `create_sheet`, `rename_sheet`, `write_range`,
  `clear_range`, `set_formula`, `fill_formula`, `set_number_format`,
  `delete_sheet` (undo path only).
- 5 reserved-but-refused: `create_table`, `attach_chart`, `attach_image`,
  `insert_rows`, `insert_columns`.
- One canonical cell-hash algorithm with **12 golden fixtures shared with
  TypeScript** — `runtime/patches/fixtures.py` ↔
  `lib/data-analysis/patch/cell-hash.fixtures.ts`, verified green by
  `npm run verify:cell-hash`.
- Chunked payloads with per-chunk checksums; the patch hash commits to the
  ordered chunk checksums. Payloads live in Cloudinary, read back through the
  authenticated API — no signed URL is ever persisted.
- Inverse patch captured for every destructive edit (`patches/inverse.py`).

### 9.11 — Placement, handshake, reservations · **done (backend side)**

- `runtime/placement/` — `selection.py` (447), `context.py` (320),
  `occupancy.py` (263), `reservations.py` (166), `naming.py` (91).
- `WorkbookPatchContext` (`placement/context.py:174`) — per-sheet occupancy
  (`used_range_a1`, `merged_ranges`, `protected_ranges`, `table_ranges`,
  `drawing_ranges`), source capture, candidate rectangles, idempotency key and a
  `context_hash` the server recomputes and rejects on mismatch.
- Adjacent-right with a two-column gap, full-rectangle collision check against
  values, formulas, merges, protection, tables, drawings and *other runs'
  reservations*; deterministic new-sheet fallback with 31-char sanitization.
- Exact rectangle reservations (not sheet-level): intersecting-lease query plus
  transactional insert, released on rejection, cancellation, application,
  supersession or expiry.
- 37 placement tests + 15 handshake tests.

### 9.12 — Approval, application, conflict, undo · **done (backend side)**

- `runtime/services/patch_service.py` (1,190 lines) — `request_context`,
  `submit_context`, `decide`, `preflight`, `record_application`,
  `read_payload_chunk`, `propose_undo`, plus `_rebase` and `_reopen_context`.
- Approval binds to patch ID + revision + patch hash + plan hash + base workbook
  revision. Any change voids it.
- `patches/receipt.py` (363 lines) — `verify_receipt` checks binding,
  per-operation results, touched ranges and pre/post hashes; a duplicate receipt
  is recognized as the same application, not a second edit.
- `patches/conflicts.py` — all six rows of the 9.12.5 matrix, with deterministic
  rebase (no LLM).
- `patches/undo.py` — durable inverse proposed as a new, separately approved,
  auditable patch.
- 37 application tests + 41 patch tests.

### 9.13 — Frontend integration and the Univer adapter · **≈ 55 % — S1–S7 done, S8–S14 not started**

Done:

| Session | What shipped |
| --- | --- |
| **S1** | `apis/analysis_executions.py` — `GET /{run_id}/execution` and `/execution/preview`, tenant-scoped, redacted; run result linkage populated on completion (`worker.py:1566 _execution_linkage`). 38 tests. |
| **S2** | Granular events on the durable stream: `execution_inputs_resolved`, `execution_step_completed`, `result_validation_started`, `result_validation_completed`, `result_materialized`. `runtime/execution/progress.py` + `services/execution_progress.py`. 26 tests. |
| **S3** | `lib/server/analysis-routes.ts` — explicit route table replacing the length-2 matcher. **19 routes reachable, 21 refusals**, including the 7-segment chunk download and a binary-safe passthrough. `npm run verify:analysis-routes`. |
| **S4** | `lib/data-analysis/execution/execution-api.ts`, `execution-events.ts` (380 lines), `execution-types.ts`. |
| **S5** | `execution-progress-card.tsx`, `result-preview-table.tsx`, `result-preview.ts`, both mounted in `ai-analyst-panel.tsx`; provider tracks `executionProgress`/`execution`/`executionPreview`; activity bar labels stages instead of stopping at "Waiting for approval". |
| **S6** | **The Univer undo spike is resolved and documented.** `univer-patch-adapter.ts` — `applyAsOneUndo` runs real Univer commands, then collapses the undo entries they pushed into one, concatenating redo forward and **undo in reverse**. The investigation found `__tempBatchingUndoRedo` is not merely deprecated but *wrong* — it appends undo mutations forward, so one undo lands on an intermediate state the user never entered, while redo looks healthy. It is rejected outright, not kept as a fallback. `univer-contract.ts` is a 14-check contract suite at `/dev/univer-contract`. |
| **S7** | `revision-coordinator.ts` — one AI patch = one logical revision = one snapshot save; wired into `univer-host.tsx:119`. 24 checks via `npm run verify:revision-coordinator`. |

Not started: **S8–S14** (context capture + hash parity, payload loader and
preflight, patch review UI, adapter operations, apply coordinator and receipt,
conflict resolution and durable undo, preview clone and large-range
performance). See remaining.md.

### 9.14 — APIs, events, persistence, lifecycle · **mostly done**

19 routes live behind the Clerk → BFF → internal-secret boundary:

```
GET  /analysis/runs            POST /analysis/runs
GET  /analysis/runs/{id}       GET  /analysis/runs/{id}/events      (SSE)
GET  /analysis/runs/{id}/plan  POST /analysis/runs/{id}/approve|reject
POST /analysis/runs/{id}/cancel|pause|resume|resume-as-new
GET  /analysis/runs/{id}/execution
GET  /analysis/runs/{id}/execution/preview
GET  /analysis/runs/{id}/patch
POST /analysis/runs/{id}/patch/context|approve|reject|preflight|receipt|undo
GET  /analysis/runs/{id}/patch/{pid}/revisions/{rev}/operations/{op}/chunks/{i}
```

Collections added: `analysis_executions`, `analysis_patch_proposals`,
`analysis_patch_contexts`, `analysis_apply_receipts`,
`workbook_write_reservations`. Index intent declared and migrated
(`scripts/migrate_phase8_indexes.py`, `verify_phase8_indexes.py`).

Lifecycle mapping (`status` coarse, `phase`/`outcome` detailed) implemented in
`runtime/services/state_machine.py`, including the three Phase 9 waits.

**Missing:** 5 of the specified events and 2 of the specified routes — see
remaining.md items R9 and R10.

### 9.15 — Security, privacy, observability · **done**; performance evidence **not gathered**

- Clerk → Next BFF → internal-secret FastAPI preserved; tenancy re-checked at
  repository queries, not only at routes.
- Checksums verified before parse/apply; no secrets in the child process;
  private temp directories with guaranteed cleanup; decompressed byte limits;
  CSV/formula injection guarded; short-lived artifact access, redacted from logs.
- `runtime/observability/` — `logging.py`, `metrics.py`, `tokens.py`. Structured
  logs carry identifiers and metrics only. Tests assert sensitive fixture values
  never reach logs or events.
- Limits are configuration (`config/settings.py`), not constants scattered
  through the engine.
- **Not done:** the small/medium/large benchmark profiles and the README
  hardware-and-timings evidence 9.15.2 asks for.

### 9.16 — Tests · **unit and contract layers strong; integration and E2E absent**

- 421 Phase 9 tests across 15 files (10,030 lines) — every unit-test bullet in
  9.16.1 is covered.
- Property/metamorphic tests present: filter subset, dedup idempotence, sort
  stability, generation constraints, replay hash identity, patch↔inverse
  round-trip, key-order-independent canonical hashing.
- Cross-language parity proven by 5 Node verification scripts, all green.
- **Not done:** the 8 Atlas-dependent concurrency tests (skipped), the 12
  end-to-end portfolio scenarios, and performance evidence.

---

## Summary

| Section | State |
| --- | --- |
| 9.1 · 9.2 · 9.3 | done |
| 9.4 · 9.5 · 9.6 | done, engine live (Gate A open) |
| 9.7 | built and unit-tested, not reachable from a plan |
| 9.8 | done except checkpoint wiring, stage concurrency, mid-execution pause |
| 9.9 · 9.10 · 9.11 · 9.12 | done on the backend, gated off pending the browser half |
| 9.13 | S1–S7 done; S8–S14 not started |
| 9.14 | routes and collections done; 5 events and 2 routes missing |
| 9.15 | security/privacy/observability done; performance evidence missing |
| 9.16 | unit + contract done; integration, E2E, benchmarks missing |

**Roughly: the backend is complete to the browser boundary and proven by 421
tests; the browser half of 9.13 is about half done, and the half that remains is
the half that actually mutates a spreadsheet.**
