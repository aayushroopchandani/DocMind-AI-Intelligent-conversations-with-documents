# Phase 9 — Native execution engine and safe spreadsheet editing

Status: implementation plan only  
Audience: project owner and future contributors  
Depends on: Phase 8.1–8.13  
Primary boundary: deterministic tabular execution and approved workbook mutation  
Storage decision: Cloudinary raw assets for durable files; MongoDB for metadata only

## 1. Executive decision

Phase 8 made a user request durable, inspectable, typed, validated and approvable. It deliberately stops before executing the plan or changing a workbook. Phase 9 should complete that missing half:

```text
user prompt
  -> Phase 1–7 evidence, preparation and normalization
  -> Phase 8 typed and validated plan
  -> Phase 9 deterministic native execution
  -> validated result dataset
  -> declarative workbook patch
  -> user preview and approval
  -> atomic Univer application
  -> durable apply receipt and undo record
```

The recommended Phase 9 boundary is:

- Implement common tabular work with a deterministic Polars engine.
- Implement seeded synthetic dataset generation.
- Implement a typed expression language for derived values and filters.
- Implement safe spreadsheet formulas through a compiler, never raw generated code.
- Implement a versioned workbook patch protocol.
- Implement collision-safe output placement.
- Implement preview, final approval, apply receipts, conflict handling and undo.
- Keep every large dataset and patch payload outside MongoDB.
- Do **not** execute arbitrary Python in Phase 9.
- Do **not** claim chart, dashboard, KNN or ML execution in Phase 9.

Charts, dashboards and arbitrary Python remain important product goals. The patch protocol should reserve future operation names for them, but the Phase 9 capability registry must reject them until their executor and frontend renderer exist. This is better than exposing a plan operation which silently cannot run.

For this portfolio, “production-grade” means strong contracts, isolation, idempotency, recovery, validation, tests and an honest deployment boundary. It does not require paid infrastructure or a public deployment.

## 2. What the existing code changes about the design

This plan is based on the current implementation, not only the earlier reference outline.

### 2.1 Phase 1–7 already provides the correct input boundary

The Phase 7 adapter in `scripts/data_analysis_agent/runtime/integration/phase7.py` returns normalized dataset references rather than arbitrary data frames. The normalized dataset system already records:

- source identity and source version;
- schema and row counts;
- source-passthrough versus materialized data;
- normalization recipe and lineage;
- MongoDB or blob access information;
- profiling and preparation metadata.

Phase 9 should consume these references. It must not repeat PDF extraction, retrieval, profiling or normalization inside the native engine.

### 2.2 Phase 8 plans are typed, but two fields are still too free-form to execute

`runtime/models/plans.py` currently has a useful operation union and executor declarations. However:

- `derive_column.expression` is a free-form string;
- `generate_dataset.generation_instructions` is a free-form string.

Those were acceptable while plans were inspectable proposals. They are not an execution language. Executing either field through `eval`, SQL string interpolation, Python, or direct formula injection would break the Phase 8 safety boundary.

Phase 9 therefore needs Plan Schema v2 before enabling execution. Old v1 plans remain readable in run history, but are non-executable.

### 2.3 The current worker ends at `plan_ready`

`runtime/services/worker.py` and the plan repository currently complete a successful run after planning or plan approval. Phase 9 must change that transition:

- a plan that can execute automatically enters the execution queue;
- approving a gated plan enters the execution queue;
- approving a plan must not set `completed_at`;
- a read-only execution may complete after result validation;
- an edit execution waits for a final patch decision and frontend application.

### 2.4 Pause, cancel, leases and durable SSE should be extended, not rebuilt

The existing run lifecycle already supports:

- MongoDB-backed events with ordered sequence numbers;
- SSE replay and reconnect;
- worker leases and stale-lease recovery;
- idempotent run creation;
- pause, resume and cancellation requests;
- checkpoints at safe orchestration boundaries.

Phase 9 should add execution-stage checkpoints to this framework. It should not introduce an independent task system or a second event bus.

### 2.5 The workbook is local to the browser

`frontend/my-app/lib/data-analysis/workbook-snapshot.ts` sends bounded workbook context, while Univer remains the authoritative editor in the browser. This leads to a strict design rule:

> The backend can propose and validate a patch, but it cannot directly mutate or independently inspect the current local workbook.

The frontend must perform a final preflight against the live workbook and return a receipt with the post-application hashes.

### 2.6 The existing adjacent-placement context is not enough after execution

At planning time the backend often has only the selected range or a bounded used-range snapshot. It does not necessarily have every cell in the future output rectangle. The exact output size is also unknown until execution finishes.

Therefore placement must use a fresh, post-execution context handshake. Without this handshake, “place beside the table” can overwrite cells which were outside the original snapshot.

### 2.7 Univer must be hidden behind an adapter

The frontend currently uses Univer `0.25.1`. Bulk value, formula and sheet operations are available, but atomic multi-command undo support is version-sensitive and some batching APIs are internal or deprecated.

Phase 9 must introduce a `UniverPatchAdapter` and a contract test. Business code should never call version-specific Univer internals directly. Phase 9 is not complete until one applied AI patch behaves as one logical undo action.

## 3. Phase 9 invariants

These rules apply to every subsection:

1. Raw LLM text never becomes executable code, a Polars expression or a Univer command.
2. Every executable plan is schema-valid, semantically valid and capability-valid.
3. Every input is addressed by immutable dataset ID and version/content signature.
4. Every native output is immutable and content-hashed.
5. Replaying the same deterministic recipe over the same inputs produces the same output hash.
6. A workbook patch is declarative, versioned and independent of Univer.
7. No edit is applied without a final live-workbook preflight.
8. No non-empty source or target cell is overwritten silently.
9. All large tables, previews and inverse payloads live outside MongoDB.
10. Every state-changing endpoint is tenant-scoped and idempotent.
11. Pause and cancellation take effect only at declared safe boundaries.
12. Events and logs contain identifiers and metrics, not workbook values or formulas.
13. A failed or partial frontend application never produces a successful run receipt.
14. Legacy Phase 8 plans can be displayed but cannot accidentally enter the new executor.

## 4. Target architecture

```text
Validated Plan v2
       |
       v
Execution Admission Controller
  - ownership and version checks
  - capability and resource checks
  - idempotency/cache lookup
       |
       v
Execution DAG Compiler
  - topological validation
  - native step compilation
  - lazy-stage fusion
  - checkpoint boundaries
       |
       v
Bounded Native Worker Process
  - Polars LazyFrame
  - no LLM
  - no arbitrary Python
  - no database/cloud credentials
       |
       v
Result Validator + Artifact Publisher
  - assertions
  - schema/row metrics
  - content hash
  - Cloudinary payload + Mongo metadata
       |
       +-------------------- read-only run -> completed
       |
       v
Patch Context Handshake
  - source/target live hashes
  - candidate rectangles
  - workbook revision
       |
       v
Placement + Patch Compiler
       |
       v
Final Patch Approval
       |
       v
Frontend UniverPatchAdapter
  - preflight all operations
  - preview
  - one logical apply/undo
  - snapshot persistence
       |
       v
Apply Receipt -> completed
```

---

# 9.1 — Lock the execution boundary and migrate capabilities

## Goal

Make it impossible for the planner to produce an apparently executable operation for which no safe executor exists.

## Work

### 9.1.1 Introduce capability profiles

Define a versioned capability document used by the planner, validator and executor:

```json
{
  "capability_profile": "native_spreadsheet_v1",
  "native_execution": true,
  "python_execution": false,
  "workbook_patches": true,
  "spreadsheet_formulas": true,
  "charts": false,
  "images": false,
  "machine_learning": false,
  "supported_plan_schema_versions": ["2.0"],
  "supported_patch_schema_versions": ["1.0"]
}
```

The Phase 8 planner currently knows about a Python executor even though no Python runtime exists. Change the runtime capability to false for Phase 9. A query needing KNN, model training or Python-only visualization should return an honest `capability_not_available` result or a clarification explaining that the later Python phase is required.

### 9.1.2 Separate planning success from run completion

Planning produces an executable contract; it is no longer the final output for an edit or analysis run. Change state transitions so that:

```text
plan_ready
  -> awaiting_plan_approval, when early approval is required
  -> queued_for_execution, otherwise

plan_approved
  -> queued_for_execution
```

Plan rejection remains terminal. Plan approval must be compare-and-set against the plan revision and hash.

### 9.1.3 Use selective early approval and mandatory final edit approval

Avoid approval fatigue:

- Ask/analysis operations without workbook mutation do not need patch approval.
- A cheap, non-destructive edit plan may execute to a proposal without early plan approval because execution itself is immutable.
- Ambiguous, expensive, destructive, formula-overwriting or broad-impact plans require early approval.
- Every actual workbook mutation requires final approval of the exact patch hash.

This preserves HITL where it matters without asking the user to approve the same safe intent twice.

## Acceptance criteria

- Python, charts and ML cannot be scheduled.
- A v1 plan cannot enter execution.
- An approved v2 plan is queued exactly once.
- Planning no longer marks an executable run complete.
- Approval policy tests cover low-risk, high-risk and destructive examples.

---

# 9.2 — Plan Schema v2 and a typed native expression language

## Goal

Turn every executable instruction into a closed, validated data structure.

## 9.2.1 Backward-compatible plan versioning

Keep the current plan model for history and introduce Plan Schema `2.0`. The executor dispatches by schema version and fails closed for unknown versions.

Canonicalization rules must be versioned. `plan_hash` must include:

- schema version;
- operation types and parameters;
- dependencies;
- input dataset IDs and versions;
- expected output schemas;
- workbook write intent;
- validator and capability versions.

Timestamps, display labels and approval metadata must not affect the canonical hash.

## 9.2.2 Replace free-form expressions with an AST

Use a small expression union such as:

```text
Literal
ColumnRef(column_key)
Unary(op, operand)
Binary(op, left, right)
Compare(op, left, right)
Boolean(op, operands)
CaseWhen(branches, otherwise)
Coalesce(expressions)
Cast(expression, target_type, failure_policy)
DatePart(part, expression)
StringTransform(operation, expression, options)
NullCheck(expression)
```

Initially allow only:

- arithmetic: add, subtract, multiply, safe divide, modulo;
- comparisons: equal, not equal, greater/less, in, between;
- boolean: and, or, not;
- null handling: is null, coalesce, fill policy;
- dates: year, quarter, month, day, date truncation;
- bounded strings: trim, lowercase, uppercase, length, prefix/suffix checks;
- conditional values through `case_when`;
- explicit safe casts.

Do not support:

- arbitrary Python;
- `eval`;
- raw SQL;
- arbitrary function names;
- filesystem or network references;
- user-defined functions;
- raw spreadsheet formulas inside a native expression.

Columns use stable `column_key` identifiers, not only display names. The compiler resolves a key to the current physical column exactly once.

## 9.2.3 Strengthen operation contracts

Important additions:

- `filter_rows`: typed predicate AST and null predicate policy.
- `derive_column`: typed expression AST, output type, rounding and overflow policy.
- `join`: join kind, left/right key pairs, expected cardinality, null matching, suffix policy and maximum expansion ratio.
- `aggregate`: explicit functions, output names, null policy and decimal rounding.
- `pivot`: explicit value/group/category keys, aggregation, category discovery policy and maximum output columns.
- `sort_rows`: stable-sort flag and explicit null placement.
- `fill_missing`: typed fill strategy and optional group/ordering keys.
- `deduplicate`: key set, keep-first/last policy and deterministic ordering rule.
- `generate_dataset`: typed generation schema described in 9.6.
- workbook formula intent: semantic formula specification described in 9.7.

## 9.2.4 Repair remains bounded

The existing deterministic validator should emit structured v2 errors. The planner may receive those errors for one repair call. The second deterministic failure becomes clarification or a failed plan; it must not enter an unlimited LLM loop.

## Acceptance criteria

- Every executable value is represented by a closed schema union.
- Unknown AST node/function/operator fails validation.
- No v2 native step contains free-form executable text.
- Plan hashes remain stable across serialization order.
- Golden v1 history fixtures still deserialize as non-executable records.

---

# 9.3 — Durable input resolution and execution admission

## Goal

Resolve a validated plan back to the exact normalized data after approval, restart or resume.

## 9.3.1 Durable normalized references

The current worker has Phase 7 results in memory during one graph invocation. Phase 9 must not depend on that memory. Add a repository-level lookup for normalized references by:

```text
user_id
workspace_id
dataset_id
dataset_version/content_signature
```

The version must bind to source identity and source version as well as the normalization recipe. A recipe hash by itself is not a sufficient data version because two different source tables may share the same recipe.

For source-passthrough references, admission rechecks the source Mongo/blob version. For materialized references, it verifies the immutable content hash and artifact state.

## 9.3.2 Admission checks

Before allocating execution resources, check:

- run ownership and workspace scope;
- active plan revision and hash;
- plan schema and capability profile;
- normalized input existence and versions;
- artifact checksum and reconciliation status;
- workbook revision/snapshot guard where relevant;
- row, column, cell and byte estimates;
- join expansion and pivot width estimates;
- current user and global execution quota;
- cancellation/pause state;
- existing successful execution with the same cache key.

## 9.3.3 Deterministic execution key

Create an execution key from:

```text
tenant scope
+ ordered input dataset content signatures
+ canonical native recipe hash
+ native engine version
+ semantic policy version
```

The cache is tenant-scoped even if two users happen to upload identical bytes. This avoids exposing cross-tenant cache existence or metadata.

## Acceptance criteria

- Restarting the backend after plan creation does not lose the inputs.
- A stale or missing input blocks execution before any work begins.
- Duplicate approval or queue delivery produces one logical execution.
- Cache reuse is possible only for identical immutable inputs and versions.

---

# 9.4 — Deterministic native execution engine

## Goal

Execute common tabular transformations quickly without generated Python.

## 9.4.1 Engine choice

Use pinned Polars as the primary engine and Arrow only as an in-process or temporary interchange format. Polars is a good fit because it provides:

- columnar execution;
- lazy query plans;
- projection and predicate pushdown;
- efficient joins and aggregations;
- fewer Python objects than row-by-row pandas code;
- deterministic expression compilation from a typed AST.

Pin the Polars minor version and record it in every execution. Do not treat engine upgrades as invisible because type coercion, ordering and expression semantics can change.

## 9.4.2 Module boundaries

Recommended backend structure:

```text
runtime/execution/
  contracts.py              # execution, stage and result models
  admission.py              # ownership, version and quota checks
  dag.py                    # dependency and stage compiler
  service.py                # orchestration entry point
  checkpoints.py            # durable checkpoint policy
  idempotency.py            # execution keys and result reuse
  native/
    backend.py              # NativeExecutionBackend protocol
    polars_backend.py       # concrete implementation
    expression_compiler.py  # AST -> Polars Expr
    operation_compiler.py   # PlanStep -> native operation
    schema.py               # logical/Polars type mapping
    metrics.py              # row/schema/change metrics
    worker_protocol.py      # parent/child messages
    worker_main.py          # bounded child-process entry point
  results/
    validation.py
    lineage.py
    publisher.py
    previews.py
```

Patch code belongs in a separate `runtime/patches/` package. Univer code remains entirely in the frontend.

## 9.4.3 Lazy stages, not one data frame per step

Compile the dependency DAG into native stages:

- Fuse compatible linear steps into one `LazyFrame` query.
- Push selections and filters toward the source.
- Materialize only at a branch, join boundary, checkpoint, preview or final output.
- Reuse a materialized branch if several downstream steps consume it.
- Avoid converting full tables to Pydantic models or Python dictionaries.

Logical step records still show each user-visible step even if several steps run in one optimized stage.

## 9.4.4 Bounded worker process

Polars operations are trusted application code, so they do not require the arbitrary-code sandbox discussed later. They should still run outside the async web event loop.

Recommended portfolio architecture:

- launch a bounded child worker process using a spawn context;
- pass only a validated canonical recipe and staged immutable input paths;
- do not pass MongoDB, Cloudinary or LLM credentials to the child;
- give it a private temporary input/output directory;
- enforce wall-clock timeout and output-size limits in the parent;
- enforce memory/CPU limits where the host supports them;
- terminate the process on timeout or cancellation;
- validate all returned manifests and hashes before publishing.

This is more killable and isolated than `asyncio.to_thread`, while remaining free and easy to demonstrate locally. Keep it behind `NativeExecutionBackend` so a future deployment can replace it with a queue/container worker.

## 9.4.5 Supported operations

Phase 9 native v1 should support:

- select, rename and reorder columns;
- filter and stable sort;
- explicit type conversion;
- fill/drop missing values;
- deduplication;
- typed derived columns;
- group-by and aggregation;
- bounded joins;
- bounded pivot and unpivot;
- seeded generated datasets;
- data-result composition and previews.

Statistical tests, model training and Python-only visualizations remain disabled.

## Acceptance criteria

- No native request blocks the FastAPI event loop.
- A fused recipe yields the same result as step-by-step execution.
- Cancellation kills or safely stops the current stage.
- Memory, time, output-size and row/cell limits fail with typed errors.
- The child process cannot access application credentials.

---

# 9.5 — Define exact semantics for every native operation

## Goal

Make replay deterministic across machines and upgrades.

## Global semantic policy

Persist a `native_semantics_version` covering:

- timezone, initially UTC;
- date parsing formats and ambiguity policy;
- decimal precision and rounding mode;
- integer overflow policy;
- string normalization and case sensitivity;
- stable row ordering rules;
- null versus empty-string behavior;
- NaN/infinity policy;
- locale, initially `en-US`;
- column-name collision rules.

Never rely on engine defaults where a default affects the result.

## Key operation rules

### Filter

- Compile only a typed predicate AST.
- Treat a null predicate result as false unless the plan explicitly selects another supported policy.
- Report input rows, output rows and removed rows.

### Sort

- Use stable sorting.
- Declare ascending/descending and null-first/null-last per key.
- Add a hidden deterministic input-row ordinal when a later operation needs tie stability.

### Type conversion

- Declare strict failure, null-on-failure or bounded repair policy.
- Return invalid-value count without logging the values.
- Currency/financial values use fixed-scale decimal or integer minor units, not binary float.

### Missing values

- Distinguish null, empty string and string literals such as `N/A`.
- Phase 7 normalization determines recognized missing markers; Phase 9 does not rediscover them ad hoc.
- Ordered fill operations must declare their ordering keys.

### Deduplication

- Declare keys and keep-first/keep-last/error policy.
- “First” and “last” refer to a declared deterministic ordering.
- Report duplicate groups and removed rows.

### Aggregate

- Allow a closed list: count, non-null count, sum, min, max, mean, median and bounded quantiles.
- Require output names and types.
- Define null and decimal rounding behavior.

### Join

- Require explicit left/right keys and join kind.
- Validate compatible logical types and units.
- Default null-key matching to false.
- Require an expected cardinality such as one-to-one or many-to-one when it can be inferred.
- Estimate and then enforce maximum output rows and expansion ratio.
- Fail on unexpected column collisions unless a suffix/rename policy exists.

### Pivot

- Do not allow unbounded category expansion.
- Either receive explicit categories in the plan or run a bounded discovery preflight.
- Sort discovered categories with the semantic policy and persist them in the executable recipe.
- Enforce maximum output columns before materialization.

### Unpivot

- Keep identifier columns explicit.
- Define the output variable/value names and value coercion policy.

## Acceptance criteria

- Golden fixtures cover nulls, dates, decimals, duplicate keys and ordering.
- A semantic policy change produces a new execution key.
- Join bombs and pivot-width explosions fail before publishing output.
- Operation metrics are consistent and reproducible.

---

# 9.6 — Seeded synthetic-data generation

## Goal

Generate repeatable datasets from a typed schema rather than asking an LLM for thousands of cell values.

## 9.6.1 Generator specification

The LLM proposes only a schema:

```json
{
  "dataset_name": "sample_sales",
  "row_count": 100,
  "seed": 91342,
  "generator_version": "1.0",
  "columns": [
    {"key": "transaction_id", "type": "string", "rule": {"kind": "unique_id", "prefix": "TX"}},
    {"key": "date", "type": "date", "rule": {"kind": "date_range", "start": "2024-01-01", "end": "2024-12-31"}},
    {"key": "region", "type": "string", "rule": {"kind": "categorical", "values": ["North", "South", "East", "West"]}},
    {"key": "revenue", "type": "decimal(12,2)", "rule": {"kind": "integer_minor_units", "min": 100000, "max": 10000000}},
    {"key": "cost", "type": "decimal(12,2)", "rule": {"kind": "dependent_fraction", "of": "revenue", "min": 0.35, "max": 0.85}}
  ],
  "constraints": [
    {"kind": "unique", "columns": ["transaction_id"]},
    {"kind": "compare", "left": "cost", "operator": "less_than", "right": "revenue"}
  ]
}
```

## 9.6.2 Determinism rules

- Persist seed, generator version and complete schema.
- Derive a separate column seed from the global seed and stable column key. Adding or reordering another column then does not change existing columns.
- Pin the random algorithm. Do not depend on the unspecified default RNG of a library.
- Generate money in integer minor units, then apply decimal scale.
- Generate IDs deterministically and verify uniqueness.
- Store the output content hash and compare it in replay tests.

## 9.6.3 Ambiguous requests

For “generate random data” with no domain, columns or purpose:

- ask one concise clarification, or
- offer a small preview with clearly stated assumptions and do not write it until accepted.

Do not let the planner invent a large, expensive dataset silently.

## 9.6.4 Limits

Enforce:

- maximum rows and columns;
- maximum unique-category count;
- bounded string lengths;
- no generated secrets, real credentials or realistic personal identifiers;
- deterministic constraint retries with a maximum attempt count;
- post-generation schema and constraint validation.

## Acceptance criteria

- Same schema, seed and version produces the same content hash.
- Column reordering does not change generated values.
- Revenue/cost constraints always hold.
- Ambiguous generation reaches clarification rather than arbitrary execution.

---

# 9.7 — Semantic spreadsheet formulas and formula compiler

## Goal

Allow requests such as “add profit margin” without letting the LLM inject formula text.

## 9.7.1 Formula specification

Use a typed semantic formula AST separate from native derived expressions:

```json
{
  "output_column": "profit_margin",
  "expression": {
    "kind": "safe_divide",
    "numerator": {
      "kind": "subtract",
      "left": {"kind": "column_ref", "column_key": "revenue"},
      "right": {"kind": "column_ref", "column_key": "cost"}
    },
    "denominator": {"kind": "column_ref", "column_key": "revenue"},
    "on_zero": 0,
    "on_error": 0
  },
  "fill": "down",
  "number_format": "0.00%"
}
```

The compiler resolves stable column keys to coordinates only after final placement is known.

## 9.7.2 Safe formula subset

Initially support arithmetic, comparisons, `IF`, `IFERROR`, `AND`, `OR`, selected date functions and bounded aggregation functions needed by approved product examples.

Reject:

- external workbook references;
- URLs and network-capable functions;
- unknown function names;
- `INDIRECT`, `OFFSET` and similar dynamic-reference functions;
- volatile functions such as `RAND`, `RANDBETWEEN`, `NOW` and `TODAY` unless a later explicit capability permits them;
- formulas exceeding length, dependency or range limits;
- formula text supplied directly by the LLM.

Literal strings beginning with `=`, `+`, `-` or `@` must stay typed as strings where appropriate so imported/generated data cannot become formula injection.

## 9.7.3 Compilation and preview

- Support `en-US` formula syntax and locale in Phase 9 v1; fail clearly for unsupported locales.
- Compile a seed formula with relative references and use bounded fill semantics, or compile a validated formula matrix.
- Apply number formats separately from formula values.
- Evaluate representative rows with the equivalent native semantic AST before patch proposal.
- Compare a bounded sample with Univer’s evaluated results during frontend preview.
- Store the semantic formula specification, compiler version and coordinate mapping in lineage.

## Acceptance criteria

- Column renames and movement are resolved through keys, not guessed names.
- Relative formula references fill correctly from first to last row.
- Division by zero and null behavior matches the specification.
- Unsafe and unknown functions fail before a patch is created.
- Formula preview and post-apply sample checks agree.

---

# 9.8 — Durable execution orchestration, checkpoints and replay

## Goal

Make native execution recoverable, pausable and idempotent.

## 9.8.1 Execution records

Create a durable `AnalysisExecution` for each execution revision:

```text
execution_id
run_id, plan_id, plan_hash
input signatures
recipe hash
engine and semantic versions
status and current stage
attempt/fencing token
resource estimates and actual usage
result dataset references
checkpoint references
warnings/errors/timing
created/started/finished timestamps
```

Store logical step attempts separately or as bounded summaries. Large diagnostics and sample values must not live in these documents.

## 9.8.2 DAG and stage scheduling

- Validate acyclicity and dependency references again at execution admission.
- Topologically compile logical steps into native stages.
- Run independent branches concurrently only within a global CPU/memory budget.
- Avoid parallelizing small stages whose scheduling cost exceeds their work.
- Assign stable stage IDs from the canonical stage recipe.

## 9.8.3 Checkpoints

Checkpoint at:

- expensive materialization boundaries;
- fan-out branches used by multiple descendants;
- completed joins/pivots above a size threshold;
- final native result;
- explicit pause boundaries.

Do not upload a checkpoint after every tiny filter or rename. That would add more storage and latency than it saves.

Each checkpoint includes input signatures, stage recipe hash, content hash, schema, row count and artifact reference. A recovered worker reuses only a checkpoint whose complete key still matches.

## 9.8.4 Pause and cancellation

- Check pause/cancel before a stage, after a stage and during bounded source/output streaming.
- A running native child may finish a very small stage before pausing.
- A long child stage can be terminated on cancellation; its incomplete output directory is discarded.
- Pause persists the last valid checkpoint and moves the existing run to `paused`.
- Resume reacquires a lease and continues the same non-terminal run from that checkpoint.
- Cancel remains terminal. “Resume cancelled” creates a linked new run, preserving audit history.

## 9.8.5 Fencing and publication

Only the current worker lease/fencing token may publish a stage or final result. A stale recovered worker may finish computation, but its compare-and-set publication must fail.

## Acceptance criteria

- Duplicate queue delivery does not duplicate outputs.
- Crash after artifact upload but before Mongo commit is reconciled.
- Crash after Mongo reservation but before upload expires or reconciles safely.
- Pause/resume skips valid completed stages.
- Stale workers cannot overwrite a newer attempt.

---

# 9.9 — Result validation, lineage, previews and Cloudinary artifacts

## Goal

Publish an immutable, inspectable result only after deterministic validation.

## 9.9.1 Result contract

Every native execution result includes:

- output dataset ID and version/content hash;
- schema before and after;
- input and output row/column counts;
- rows removed, added or changed where meaningful;
- warnings and validation assertions;
- bounded preview rows with redaction policy applied;
- execution and stage durations;
- canonical replay recipe;
- input-to-output lineage;
- engine, semantics and compiler versions;
- durable artifact references.

## 9.9.2 Validation layers

1. **Protocol validation** — worker response and files match their manifests.
2. **Schema validation** — actual output matches the declared output schema.
3. **Assertion validation** — row counts, uniqueness, constraints, null bounds and operation-specific assertions.
4. **Resource validation** — output remains inside cell/byte limits.
5. **Safety validation** — no formula injection or unsafe types in workbook-bound results.
6. **Hash validation** — computed content hashes match before publication.

## 9.9.3 Durable file format while using Cloudinary

Cloudinary is the storage provider for this portfolio phase. Use its raw-resource mode and immutable versioned object names.

Recommended durable bundle:

```text
result.csv.gz              # canonical row data for the current Cloudinary phase
result.schema.json         # logical types, units, scale, timezone, null encoding
result.lineage.json        # canonical recipe and source mapping
result.preview.json        # small redacted preview only
result.xlsx                # optional user export, not the canonical cache key
```

CSV alone is not a complete typed interchange format. The schema manifest must define decimal scale, dates/timezones, empty-string versus null encoding, escaping and column keys. Use a reserved null encoding with an explicit escape convention, and test round trips.

Arrow IPC may be used in private temporary directories between native stages, but it is not required as a durable cloud object. When the project later moves to R2, Parquet can become the canonical analytical artifact without changing dataset handles.

## 9.9.4 MongoDB storage rule

MongoDB stores only:

- IDs, ownership and versions;
- content hashes;
- schemas and bounded metrics;
- artifact references and reconciliation state;
- bounded redacted previews;
- lineage graph references.

It must not store full output tables, full workbook patches or inverse cell payloads.

## Acceptance criteria

- CSV/schema round-trip preserves every supported logical type.
- Large output rows never enter MongoDB or SSE events.
- Corrupt or mismatched Cloudinary assets are never marked ready.
- Replaying a recipe produces the same canonical content hash.

---

# 9.10 — Workbook Patch Protocol v1

## Goal

Represent all spreadsheet changes as declarative, reviewable data independent of Univer.

## 9.10.1 Patch envelope

```text
patch_schema_version
patch_id, patch_revision, patch_hash
run_id, plan_id, plan_hash, execution_id
user_id/workspace ownership metadata
workbook_id and base_workbook_revision
source guards and target guards
ordered operations
impact summary
before/after preview references
payload and inverse-payload references
maximum affected cells
idempotency key
compiler versions
approval status and expiry
```

Ownership fields are persisted for querying but omitted from the canonical patch hash only if the hash is already cryptographically bound to a tenant-scoped record. Prefer including stable tenant/workspace identifiers to avoid accidental reuse.

## 9.10.2 Operation envelope

Every operation has:

```text
op_id
operation_type
dependencies
target sheet ID and range
expected_before_hash
expected_after_hash
payload reference or bounded inline payload
affected cell count
inverse operation reference
```

Implement only operations whose adapter support is verified:

- `create_sheet`;
- `rename_sheet`;
- `write_range`;
- `clear_range`;
- `set_formula`;
- `fill_formula`;
- `set_number_format`;
- required bounded row/column insertion if Univer contract tests pass.

`create_table`, `attach_chart` and `attach_image` may be reserved in the protocol registry, but must return `unsupported_patch_operation` until both backend validation and frontend application exist.

## 9.10.3 Payload design

- Small previews can be inline and redacted.
- Full values/formulas use immutable Cloudinary raw payloads for non-trivial ranges.
- Chunk payloads by bounded row blocks so the browser does not build multiple full copies in memory.
- Each chunk has an index, byte length, row bounds and checksum.
- The patch hash commits to the ordered chunk checksums.
- Signed delivery URLs are short-lived and are not persisted in the patch.

## 9.10.4 Cell hashes

Define one canonical cell hash algorithm covering:

- typed literal value;
- formula text, when present;
- cell type;
- relevant number format;
- merged/protected state where it affects safe application.

Both Python and TypeScript implementations must share golden fixtures. A blank rectangle has a canonical hash; do not treat missing cells inconsistently with explicit blank cells.

## 9.10.5 Inverse patch

- Writing into verified blank cells has a clear-range inverse.
- Creating a sheet has a delete-sheet inverse, even if delete-sheet is exposed only through the controlled undo path.
- Any allowed destructive edit captures previous values, formulas, formats and structure in an immutable encrypted/private payload.
- Inverse patches have the same guards and must be conflict-checked when used later.

## Acceptance criteria

- Backend and frontend calculate identical patch and cell hashes.
- Duplicate patch application is detectable before mutation.
- A patch never contains raw JavaScript or Univer commands.
- Large payloads and inverse data remain outside MongoDB.

---

# 9.11 — Post-execution placement and write reservations

## Goal

Place output beside a table when safe, otherwise choose a new sheet without silent overwrite.

## 9.11.1 Patch-context handshake

Once exact output dimensions are known:

1. Backend emits `patch_context_required` with output rows/columns and intended placement.
2. Frontend captures the current workbook revision, source guard and bounded candidate target rectangles.
3. The context includes values/formulas/types, merged cells, protected cells, tables and structural occupancy relevant to collision checks.
4. Frontend posts this context with its hash and idempotency key.
5. Backend validates it and selects the target.
6. Backend compiles the final patch against that exact context.

If the browser is disconnected, the run waits durably. Reconnection through existing SSE replay restores the request. The backend must not guess that an uncaptured rectangle is empty.

## 9.11.2 Adjacent-right algorithm

For `adjacent_right`:

1. Determine the source rectangle from stable worksheet/range metadata.
2. Start two columns after its right edge.
3. Construct the complete result rectangle including headers.
4. Check workbook row/column limits.
5. Reject collision with values, formulas, merges, protection, structured tables, drawings or reserved rectangles.
6. If safe, select it and explain the placement.
7. Otherwise choose a deterministic new-sheet name.

Never scan cell-by-cell over a whole sheet when used-range metadata or interval rectangles can answer the question.

## 9.11.3 New-sheet naming

- Sanitize unsupported characters.
- Respect the spreadsheet’s 31-character limit.
- Use a meaningful base such as `Filtered Revenue`.
- Resolve collisions with deterministic numeric suffixes.
- Bind later operations to the created sheet ID, not only its display name.

## 9.11.4 Exact-range writes

An exact-range target may overwrite data only if:

- the user explicitly requested replacement;
- deterministic validation identified the exact impact;
- before content is captured for inverse patch;
- early destructive approval and final patch approval are both satisfied;
- the live preflight hash still matches.

## 9.11.5 Spatial reservations

The current sheet-level write reservation is conservative. Phase 9 should add exact rectangle reservations:

```text
workbook_id, worksheet_id
start_row, end_row, start_col, end_col
run_id, patch_id
base_revision
status
lease_owner and expiry
```

MongoDB cannot enforce arbitrary rectangle non-overlap with a normal unique index. The reservation service must query intersecting active rectangles and insert inside a transaction. Sheet-level locking is an acceptable fallback only during the migration, not the long-term behavior.

Release or expire reservations on rejection, cancellation, application, patch supersession and lease expiry.

## Acceptance criteria

- A full output rectangle is checked after output size is known.
- Two concurrent patches cannot reserve overlapping rectangles.
- Non-overlapping patches on one sheet may proceed.
- A collision causes relocation/new-sheet proposal, never silent overwrite.

---

# 9.12 — Preview, approval, application, conflict handling and undo

## Goal

Make spreadsheet editing visibly safe and recoverable.

## 9.12.1 Final patch HITL

The patch proposal contains the exact target, affected cells, formulas, hashes and inverse reference. Final approval binds to:

```text
patch_id
patch_revision
patch_hash
plan_hash
base workbook revision
```

If any of these changes, the old approval cannot be reused.

## 9.12.2 Preview is non-authoritative

Preview must not mutate the saved workbook. Recommended modes:

- a temporary cloned Univer workbook for a realistic visual preview;
- a sampled before/after diff for very large outputs;
- an overlay highlighting the target range in the real editor without changing its cells.

The preview clone has its own unit ID, does not write localStorage, and is destroyed after preview.

## 9.12.3 Apply protocol

Before the first mutation, the frontend:

1. verifies authenticated run/patch ownership through the BFF;
2. downloads all payload chunks and verifies their checksums;
3. confirms workbook and worksheet IDs;
4. rechecks revision, source guards, target guards and structural guards;
5. checks that the patch has not already been applied;
6. prepares a complete inverse in memory/durable storage;
7. validates all operations through `UniverPatchAdapter`.

Only then does it apply the patch as one logical command. It persists the workbook snapshot and increments the logical workbook revision once.

## 9.12.4 Apply receipt

```text
application_id/idempotency_key
patch_id, patch_revision, patch_hash
plan_hash and execution_id
base revision and applied revision
adapter/Univer version
per-operation result summary
pre- and post-application hashes
touched-range snapshot hash
local persistence confirmation
applied_at
```

The backend validates ownership, active patch, expected hashes and revision transition before marking the run applied/completed. Because the workbook is local, the server is validating a signed/authenticated client receipt and bounded touched-range evidence, not independently reading the whole workbook.

If local apply succeeds but receipt delivery fails, store a local application marker keyed by patch hash and retry the same receipt. Do not apply again.

## 9.12.5 Conflict matrix

| Conflict | Safe response |
|---|---|
| Revision changed, but source/target/structure hashes still match | Deterministically rebase to the new revision and issue a new patch revision |
| Source range changed | Re-plan/re-execute in a new linked run |
| Only target became occupied | Relocate beside data or to a new sheet, then re-propose |
| Workbook/sheet was removed | Ask for a new target or cancel |
| Expected after-hash already exists | Treat as already applied and recover the receipt |
| Partial/mismatched state | Do not continue; roll back or present durable inverse recovery |

Rebase is deterministic and does not need an LLM when semantics and data guards are unchanged.

## 9.12.6 Undo

Provide two levels:

- **Immediate editor undo:** one Ctrl/Cmd+Z reverses the entire AI patch as one logical action.
- **Durable AI undo:** the stored inverse patch can be proposed later, conflict-checked and applied as a new auditable action.

Undoing later is not an invisible database rollback. It is a new application record linked to the original patch.

## Acceptance criteria

- No real workbook cell changes during Preview.
- One patch increments the logical revision once.
- One editor undo restores the exact before state.
- Lost receipt delivery does not duplicate edits.
- Every conflict follows the matrix and never partially applies remaining operations.

---

# 9.13 — Frontend integration and the Univer adapter

## Goal

Turn durable backend execution into a polished spreadsheet experience without coupling backend contracts to Univer.

## 9.13.1 Frontend modules

Recommended structure:

```text
lib/data-analysis/execution/
  execution-api.ts
  execution-events.ts
  result-preview.ts

lib/data-analysis/patches/
  contracts.ts
  hash.ts
  payload-loader.ts
  preflight.ts
  apply-coordinator.ts
  receipt.ts
  conflict-resolution.ts
  univer-patch-adapter.ts

components/data-analysis/analyst/
  execution-progress-card.tsx
  patch-preview-card.tsx
  patch-diff-table.tsx
  patch-conflict-card.tsx
  application-status.tsx
```

Keep `analysis-run-provider.tsx` as the durable run/session coordinator, but move operation-specific work out of the provider.

## 9.13.2 User experience

The activity stream should show meaningful stages:

```text
Preparing 2 datasets
Filtering 3,420 rows
Grouped into 24 region/month rows
Validating result
Waiting for live workbook context
Patch ready: Sheet1!H1:M25
Waiting for your approval
Applying 150 cells
Saved at workbook revision 13
```

The Patch Preview card should show:

- operation summary in plain language;
- exact workbook, sheet and range;
- input/output row counts;
- formulas and formats being added;
- collision/placement explanation;
- bounded before/after samples;
- warnings;
- Preview, Apply, Reject and conflict-resolution actions.

## 9.13.3 Logical revision coordinator

The current workbook host observes Univer mutation commands. Applying several patch operations could otherwise increment the workbook revision several times and trigger repeated saves.

Introduce a coordinator that:

- enters an AI patch transaction;
- suppresses intermediate revision increments/autosaves;
- applies validated operations;
- commits one revision and one snapshot save;
- rolls back with the in-memory inverse on failure;
- emits one application result.

Normal user edits outside the transaction keep their existing behavior.

## 9.13.4 One-undo strategy

Before implementing every operation, perform a short Univer capability spike against the pinned version. Preferred order:

1. use a stable public command/undo API if the installed version provides one;
2. register one DocMind patch command with complete undo/redo state;
3. use an encapsulated version-specific batching adapter only as a documented fallback.

Never spread deprecated internal calls across components. Pin the Univer version and run the adapter contract suite before upgrades.

## 9.13.5 Large-range performance

- Stream/decode bounded chunks.
- Use bulk `setValues`/formula methods, never one command per cell.
- Avoid rendering the entire diff.
- Virtualize previews.
- Yield between frontend chunks to keep the UI responsive while keeping one logical undo transaction.
- Calculate hashes incrementally.
- Hold at most the current chunk plus bounded inverse buffer when possible.

## Acceptance criteria

- SSE reconnect restores execution and patch UI.
- Reload after apply-but-before-receipt recovers from the local marker.
- Large patches do not freeze the browser or render every row.
- All Univer-specific behavior is behind one adapter.

---

# 9.14 — APIs, events, persistence and lifecycle

## Goal

Expose the Phase 9 workflow through durable, tenant-scoped contracts.

## 9.14.1 API additions

Recommended routes under the existing BFF/FastAPI trust boundary:

```http
GET  /analysis/runs/{run_id}/execution
POST /analysis/runs/{run_id}/patch-context
GET  /analysis/runs/{run_id}/patch
POST /analysis/runs/{run_id}/patch/approve
POST /analysis/runs/{run_id}/patch/reject
POST /analysis/runs/{run_id}/apply-receipt
POST /analysis/runs/{run_id}/apply-failure
POST /analysis/runs/{run_id}/resolve-conflict
POST /analysis/runs/{run_id}/undo-proposal
```

Payload download should go through an authorized artifact endpoint that issues short-lived Cloudinary access, not permanent signed URLs saved in MongoDB.

All POST endpoints require idempotency keys and compare-and-set expected revisions/hashes.

## 9.14.2 Events

Extend the existing durable event stream with:

```text
execution_queued
execution_started
execution_stage_started
execution_step_completed
execution_checkpoint_created
result_materialized
result_validation_started
result_validation_completed
patch_context_required
patch_context_received
patch_proposed
patch_approval_required
patch_approved
patch_rejected
application_required
patch_applied
patch_apply_failed
workbook_revision_conflict
patch_rebased
run_completed
```

Events contain IDs, counts, status, safe warnings and durations. They must not contain raw rows, formulas or full patches.

## 9.14.3 Lifecycle mapping

Keep `status` coarse and `phase/outcome` detailed to avoid contradictory state fields:

```text
ACTIVE / execution
WAITING / plan_approval
PAUSED / execution
ACTIVE / result_validation
WAITING / patch_context
WAITING / patch_approval
WAITING / application
SUCCEEDED / completed
FAILED | CANCELLED | EXPIRED / terminal phase
```

Run state, event append and queue visibility changes should share a MongoDB transaction where atomicity matters.

## 9.14.4 Collections

Retain existing Phase 8 collections and extend `analysis_patch_proposals`. Add:

- `analysis_executions` — one record per execution revision/attempt family;
- `analysis_step_executions` — bounded logical step/stage summaries;
- `analysis_patch_contexts` — bounded hashed placement contexts;
- `analysis_apply_receipts` — immutable application and undo receipts;
- `workbook_write_reservations` — active rectangle reservations.

Large artifacts remain in Cloudinary.

## 9.14.5 Index intent

Examples, with user/workspace prefixes for user-facing access:

- unique logical execution revision within a run;
- unique tenant-scoped execution key for successful-cache lookup;
- active execution queue/lease recovery index;
- unique patch revision and patch hash within a run;
- unique application idempotency key and patch application;
- workbook history by user/workspace/workbook and applied time;
- active reservation lookup by workbook/sheet/status/expiry;
- cleanup lookup for abandoned staged artifacts.

Rectangle overlap still requires transactional service logic; indexes only narrow the candidate set.

## Acceptance criteria

- Cross-user reads and decisions return not-found/forbidden consistently.
- Every mutation is safe under duplicate requests and concurrent decisions.
- SSE replay alone can reconstruct the visible run state.
- No terminal run is accidentally returned to execution.

---

# 9.15 — Security, privacy, performance and observability

## 9.15.1 Security

- Continue the Clerk -> Next.js BFF -> internal-secret FastAPI boundary.
- Recheck tenant ownership at repository queries, not only routes.
- Verify every Cloudinary payload checksum before parse/apply.
- Never pass application secrets to the native child process.
- Use private temporary directories, restrictive permissions and guaranteed cleanup.
- Reject path traversal, URLs and unsupported encodings in worker manifests.
- Enforce decompressed byte limits to prevent compressed payload bombs.
- Guard CSV/spreadsheet formula injection.
- Keep patch approvals immutable and hash-bound.
- Use short-lived artifact access and redact it from logs.
- Rate-limit execution, context submission and repeated conflict/rebase requests.

## 9.15.2 Performance policy

Define local defaults in configuration, not hard-coded throughout the engine:

```text
maximum input/output rows
maximum columns and cells
maximum join expansion ratio
maximum pivot categories
maximum execution seconds
maximum native-worker memory
maximum concurrent native workers
maximum preview rows/columns
maximum patch cells and payload bytes
frontend chunk rows/bytes
checkpoint size/benefit thresholds
```

Recommended optimizations:

- Polars lazy scan and predicate/projection pushdown;
- stage fusion;
- streaming CSV read/write;
- content-addressed, tenant-scoped result reuse;
- incremental hashing;
- column-key integer maps rather than repeated name scans;
- bounded previews and virtualized diffs;
- bulk workbook operations;
- materialize once at fan-out boundaries;
- backpressure when CPU/memory slots are occupied.

Do not promise a fixed large row count before measuring the actual student machine. Provide small/medium/large test profiles and report the tested hardware in the README.

## 9.15.3 Privacy

The Phase 8 privacy gateway continues to control LLM payloads. The native engine may process local dataset values deterministically, but:

- previews follow workspace privacy mode and redaction;
- hidden rows/sheets remain excluded unless explicitly selected;
- raw values/formulas never enter logs/events;
- artifact access is tenant-scoped;
- local-only mode must not route values back into later LLM response composition.

## 9.15.4 Observability

Use LangSmith for LLM traces already in the project. Keep native execution observability separate and lightweight:

- execution/stage duration;
- rows/bytes read and written;
- peak memory if available;
- cache/checkpoint hits;
- output size and validation failures;
- patch cell counts;
- context wait time;
- conflict, rebase, apply and undo counts;
- typed error codes.

Structured logs contain only safe identifiers and metrics.

## Acceptance criteria

- Sensitive fixture values never appear in logs/events.
- Quota and resource failures are typed and user-readable.
- Backend remains responsive during a maximum-profile native job.
- Diagnostics explain a slow stage without exposing row content.

---

# 9.16 — Test strategy and completion criteria

## 9.16.1 Unit tests

- Plan v2 parsing, canonicalization and hashes.
- Every AST operator and rejection case.
- Logical-to-Polars type mapping.
- Operation semantics for nulls, dates, decimals and ordering.
- Synthetic generator determinism and constraints.
- Formula compiler allowlist and coordinate mapping.
- Patch/cell hash golden fixtures shared with TypeScript.
- Placement and new-sheet naming.
- Conflict classification and deterministic rebase.
- State-machine transitions and approval policy.

## 9.16.2 Property and metamorphic tests

- Filter output is a subset of input row identities.
- Deduplication is idempotent.
- Sorting twice is stable/idempotent.
- Select/reorder does not alter values.
- Generated data always satisfies declared constraints.
- Same recipe/input yields the same output hash.
- Patch then inverse patch restores the original cell hash.
- Reordering JSON object keys does not alter canonical hashes.

## 9.16.3 Integration tests

- Phase 7 normalized reference -> native result -> Cloudinary metadata.
- Worker crash, lease recovery and fencing.
- Pause/resume at every checkpoint boundary.
- Cancellation during a long stage.
- Duplicate approval and duplicate receipt races.
- Concurrent overlapping/non-overlapping write reservations on Atlas replica-set semantics.
- SSE disconnect/reconnect during execution, context wait and application wait.
- Cloudinary upload/Mongo commit partial failures.
- Backend/frontend patch-hash parity.
- Univer preview, apply, snapshot save and one-step undo.

## 9.16.4 End-to-end portfolio scenarios

1. Generate 100 seeded sales rows and write to a new sheet.
2. Filter revenue above 50,000 and place it two columns right of the source.
3. Force an adjacent collision and verify new-sheet fallback.
4. Deduplicate customers and show removed-row metrics.
5. Derive profit margin as native values.
6. Add profit margin as spreadsheet formulas with percentage formatting.
7. Group revenue by region and month.
8. Join two workbook/PDF-derived tables with a many-to-one guard.
9. Pause a multi-stage run, restart the worker and resume without repeating a valid checkpoint.
10. Modify the workbook after proposal and demonstrate rebase, relocation and re-plan paths.
11. Lose the network after local apply and recover the apply receipt without applying twice.
12. Undo the entire AI edit with one editor action and demonstrate durable undo later.

## 9.16.5 Performance evidence

Publish reproducible benchmark results for small, medium and large fixtures on the actual local machine:

- cold/warm execution time;
- peak memory;
- source/output bytes;
- stage count before/after fusion;
- browser preview and apply duration;
- workbook responsiveness;
- result hash.

This is stronger portfolio evidence than claiming untested “millions of rows.”

## Phase 9 is complete when

- Plan Schema v2 has no free-form executable fields.
- Native select/filter/sort/cast/missing/deduplicate/derive/aggregate/join/pivot/unpivot work.
- Seeded generation is repeatable and constraint-checked.
- Execution is durable, pausable, cancellable, idempotent and recoverable.
- Result data is validated, lineage-tracked and stored outside MongoDB.
- Adjacent placement checks the full live output rectangle.
- Formula patches compile from semantic specifications and fill correctly.
- Every workbook edit has preview, exact final approval, atomic apply receipt and inverse patch.
- One patch is one logical workbook revision and one editor undo action.
- Stale workbook state cannot receive an old patch.
- Duplicate receipt/retry cannot apply twice.
- Unsupported Python/chart/ML operations fail honestly at capability validation.
- The complete end-to-end and concurrency matrix passes.

---

# 9.17 — Free sandbox options for the later Python/ML phase

## Important boundary

The Phase 9 native engine executes application-owned, validated Polars operations. It does not need an arbitrary-code sandbox.

When the later phase permits LLM-generated Python for ML or special plots, that code is untrusted even when the user asked for it. A virtual environment, Python subprocess, Jupyter kernel or `RestrictedPython` alone is not a security boundary.

## Options

| Option | Cost for this portfolio | Isolation | Local macOS fit | Recommendation |
|---|---:|---|---|---|
| Docker Desktop Linux container | Free for personal/education use under Docker’s current terms | Good when hardened; still shares a VM/kernel boundary | Very good | Best default for a recognizable portfolio implementation |
| Podman rootless container | Open source/free | Good; daemonless/rootless model | Good, uses a Podman-managed Linux VM | Best fully open-source alternative |
| Docker/Containerd with gVisor `runsc` | Open source/free | Stronger syscall isolation than a normal container | Awkward on a Mac; best on a Linux host | Optional hardened adapter later |
| Firecracker microVM | Open source/free software | Strong microVM isolation | Poor locally because it requires Linux/KVM | Overkill for this student portfolio |
| Browser Pyodide | Free | Browser-origin boundary, but limited packages/memory | Easy demo | Useful only for small calculations, not the main ML executor |
| Plain subprocess/venv/Jupyter kernel | Free | Weak | Easy | Never treat as a sandbox for generated code |

Official references:

- Docker documents that Desktop is free for personal use and education: <https://docs.docker.com/subscription/desktop-license/>
- Docker Desktop's current macOS installation requirements are documented here: <https://docs.docker.com/desktop/setup/install/mac-install/>
- Docker documents CPU and memory constraints: <https://docs.docker.com/engine/containers/resource_constraints/>
- Docker run supports non-root users, read-only filesystems, temporary filesystems, PID limits, dropped privileges and `no-new-privileges`: <https://docs.docker.com/reference/cli/docker/container/run>
- Podman documents its daemonless and rootless operation: <https://docs.podman.io/en/latest/markdown/podman.1.html>
- Podman on macOS uses a managed Linux VM: <https://docs.podman.io/en/stable/markdown/podman-machine-start.1.html>
- gVisor provides the OCI `runsc` sandbox runtime: <https://gvisor.dev/docs/>
- Firecracker requires Linux/KVM and `/dev/kvm`: <https://github.com/firecracker-microvm/firecracker/blob/main/docs/getting-started.md>

## Recommended later design

Create a `PythonSandboxBackend` interface and initially implement a hardened local Docker profile. A Podman implementation can satisfy the same interface. Use a pinned, prebuilt image; never install packages requested by the generated script at runtime.

Minimum execution profile:

```text
network disabled
non-root UID/GID
read-only root filesystem
all Linux capabilities dropped
no-new-privileges
default or stricter seccomp profile
bounded CPU, memory, PIDs, file size and wall time
small tmpfs scratch directory
immutable read-only input mount
single bounded output mount
no Docker socket, host paths, secrets or cloud credentials
stdout/stderr and result-size caps
pinned package allowlist
container destroyed after execution
```

Generated code should receive data handles resolved to staged files, not database credentials or Cloudinary URLs. The parent verifies output MIME type, size, schema and checksum before publishing it as an artifact.

On a future Linux deployment, the same adapter can select gVisor for stronger isolation. Firecracker is valuable technology, but implementing its image, kernel, jailer, networking and KVM control plane would distract from the portfolio’s actual data-analysis features.

## What to show in the portfolio later

- the generated code before execution;
- the approved package/environment manifest;
- sandbox resource limits;
- live stdout/stderr with truncation;
- generated chart/model artifacts;
- timeout and cancellation behavior;
- deterministic seed and environment image digest;
- a clear “runs locally in an isolated container” deployment note.

---

# 9.18 — Recommended implementation order

Implement in vertical slices so each slice ends in a testable user outcome.

## Slice A — Safe execution foundation

1. 9.1 capability and lifecycle migration.
2. 9.2 Plan Schema v2 and expression AST.
3. 9.3 durable input resolution/admission.
4. 9.4 engine boundary and one filter/select path.
5. 9.8 execution record, lease, pause/cancel and checkpoint wiring.

Demo: a query executes a read-only filter and returns a durable result preview.

## Slice B — Complete native tabular engine

1. 9.5 operation semantics.
2. Remaining native operations.
3. 9.6 deterministic generation.
4. 9.9 validation, lineage and Cloudinary publishing.

Demo: filtering, cleaning, aggregation, joining and generated data replay with identical hashes.

## Slice C — Patch safety boundary

1. 9.10 patch schema and cross-language hashes.
2. 9.11 context handshake, placement and reservations.
3. Patch APIs/events/persistence from 9.14.

Demo: backend proposes an exact collision-safe patch but does not mutate the workbook.

## Slice D — Real spreadsheet editing

1. 9.13 Univer adapter and preview clone.
2. 9.12 atomic apply, receipts, conflict matrix and undo.
3. 9.7 semantic formula compiler.
4. Frontend performance and reconnection polish.

Demo: preview, approve, apply, reconnect, conflict recovery and one-action undo.

## Slice E — Certification

1. Security/privacy tests.
2. Atlas concurrency tests.
3. End-to-end portfolio scenarios.
4. Performance benchmarks and README evidence.

Only after Slice E should the capability profile advertise Phase 9 as complete.

## Final recommendation

Phase 9 should be implemented before arbitrary Python or charts. It creates the durable execution, result, patch and application contracts that every later Python-generated table, ML plot, chart or dashboard will also use. Once this boundary is correct, future executors produce the same kind of immutable artifact and declarative patch instead of inventing another unsafe path into the spreadsheet.
