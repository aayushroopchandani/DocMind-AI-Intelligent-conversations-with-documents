# Data-analysis pipeline — what is remaining

Audited at `b60258c` (2026-08-24). Companion to [done.md](done.md).
Section numbers refer to `backend/phase9plan.md`; session numbers (S1–S14) to
`docs/phase9plan.html`.

Everything below was confirmed by reading the code, not inferred from the plans.

---

## The one-sentence version

The backend computes, validates, places, reserves, compiles, approves and
verifies a workbook patch — and **nothing in the browser can apply one**, so
`ANALYSIS_WORKBOOK_PATCHES_READY` is still `False` and every edit-mode plan stops
at `plan_ready`. Closing that is items **R1–R5**; everything else is smaller.

---

## Critical path — the browser half of 9.13 (sessions S8–S14)

These are ordered by dependency. Each ends green and committable on its own.

### R1 · S8 — Context capture and hash parity  *(blocks R2–R5)*

**Missing files:**
`lib/data-analysis/patches/context.ts`, `context-hash.ts`,
`context-hash.fixtures.ts`, `scripts/verify-context-hash.mjs`,
`backend/…/runtime/placement/fixtures.py`.

**What is wrong today:** `captureWorkbookContext` in `workbook-snapshot.ts`
returns values, formulas, types, formats and merges. `WorkbookPatchContext`
(`runtime/placement/context.py:174`) additionally needs, per sheet,
`used_range_a1`, `protected_ranges`, `table_ranges`, `drawing_ranges`, plus a
`protected` flag per cell — and a TypeScript `computeContextHash` that
reproduces `canonical_context_payload` **byte for byte**, because
`WorkbookPatchContext.validate_context` recomputes the hash and rejects a
mismatch outright.

**Also confirmed broken:** `workbook-snapshot.ts:216-217` hardcodes
`hidden_rows: []` and `hidden_columns: []`, so the backend's privacy accounting
always sees zero hidden rows. This is a live privacy defect *today*, independent
of patching.

**Also fold in:** `canonicalNumber` and `stableStringify` are duplicated between
`workbook-snapshot.ts` and `patch/cell-hash.ts`. Two copies of a canonicalization
routine is how hashes drift. De-duplicate while both are in one head.

**Done when:** `npm run verify:context-hash` is green against Python-generated
fixtures (the way `verify:cell-hash` already works) and the server accepts a
context posted from a real workbook.

---

### R2 · S9 — Payload loader and preflight

**Missing files:** `lib/data-analysis/patches/contracts.ts`,
`payload-loader.ts`, `preflight.ts`.

Also missing: the patch **types** were never mirrored. `analysis-types.ts` has
only the run-level fields (`current_patch_id`, `current_patch_revision`,
`current_patch_hash`, `patch_approval_status`) — there is no `PatchProposal`,
`PatchOperation`, chunk reference, preflight verdict or receipt type in
TypeScript. S4 was supposed to mirror them; it mirrored execution only.

Chunk download must verify each chunk's checksum. The route is already reachable
(`verify:analysis-routes` covers the 7-segment chunk path) and
`lib/server/backend.ts` already has the binary-safe passthrough, so this is
client work only.

**Done when:** for a real proposal the browser downloads and verifies every
chunk and receives a `may_apply` verdict from `POST /patch/preflight`.

---

### R3 · S10 — Patch review and approval UI

**Missing files:** `components/data-analysis/analyst/patch-preview-card.tsx`,
`patch-diff-table.tsx`; plus patch state in `analysis-run-provider.tsx`
(it currently knows execution events and nothing about patches).

Must show what 9.13.2 lists: plain-language summary, exact workbook/sheet/range,
input/output row counts, formulas and formats being added, the placement
explanation, bounded before/after samples, warnings. Approve/reject wired to the
full binding — patch ID, revision, patch hash, plan hash, base revision.

**Done when:** a user can see exactly what would change and approve or reject it.
Still nothing mutates — this makes 9.11's demo visible for the first time.

---

### R4 · S11 — Adapter operations

**Partially missing.** `univer-patch-adapter.ts` today exposes exactly one
thing: `applyAsOneUndo(unitId, work)`. That is the hard half (S6) and it is
done and contract-tested. What does not exist is the **operations** the `work`
callback would perform.

Needs the seven proposable types against the facade — `write_range`,
`set_formula`, `fill_formula`, `set_number_format`, `clear_range`,
`create_sheet`, `rename_sheet` — plus `delete_sheet` for the undo path only.
Bulk `setValues`/`setFormulas` throughout, never one command per cell.

**Done when:** every operation has a before/after hash check and the
`/dev/univer-contract` suite passes against Univer 0.25.1.

---

### R5 · S12 — Apply coordinator and receipt

**Missing files:** `lib/data-analysis/patches/apply-coordinator.ts`,
`receipt.ts`, `components/data-analysis/analyst/application-status.tsx`.

The seven-step protocol in 9.12.3, applied as one logical command through the
S6 mechanism, wrapped by the S7 coordinator in that order
(`coordinator.runAsOneRevision(… adapter.applyAsOneUndo(…) …)`). Then receipt
assembly — per-operation results, touched ranges, pre/post hashes — and a local
application marker keyed by patch hash so a lost receipt is **retried, never
re-applied**.

The backend side is finished and waiting: `verify_receipt`
(`runtime/patches/receipt.py`) already rejects any receipt whose claims
disagree with what the server computed, and already recognizes a duplicate.

**Done when:** approve → apply → receipt accepted → run completes; then kill the
network before the receipt and reload — it recovers from the local marker
without applying twice.

---

### Then: Gate B

Flip `ANALYSIS_WORKBOOK_PATCHES_READY=true`.

> Do **not** flip it before R5. `evaluate_admission`
> (`runtime/execution/admission.py:126`) currently stops edit plans at
> `plan_ready`; with the flag on and no browser adapter, every edit run parks at
> `WAITING/proposal` on a handshake nothing answers, **holding a live rectangle
> reservation** until its lease expires.

---

## Also in 9.13, after Gate B

### R6 · S13 — Conflict resolution and durable undo (frontend)

**Missing:** `lib/data-analysis/patches/conflict-resolution.ts`,
`components/data-analysis/analyst/patch-conflict-card.tsx`.

All six rows of the 9.12.5 matrix, including the rebase path — where the server
issues a new patch revision and the old approval is deliberately void, so the
user must approve again. Plus the durable undo proposal as a new, separately
approved, auditable patch. The backend halves (`patches/conflicts.py`,
`patches/undo.py`, `_rebase`, `propose_undo`) all exist.

### R7 · S14 — Preview clone and large-range performance

**Missing:** `lib/data-analysis/patches/preview-clone.ts`; virtualization in
`patch-diff-table.tsx`; chunked application in `apply-coordinator.ts`.

A throwaway Univer unit with its own unit ID that never writes `localStorage` and
is disposed after preview. Then 9.13.5: virtualized diffs, chunked application
that yields between chunks while staying inside one undo transaction,
incremental hashing.

---

## Backend gaps

### R8 · 9.7 formula compiler is built but unreachable  *(real, and not in the session plan)*

`runtime/formulas/` is complete and 31 tests pass. `patches/compiler.py:102`
accepts `formulas: tuple[FormulaSpec, ...]`. But:

- no v2 plan step carries a `FormulaSpec` — `DeriveColumnStep`
  (`runtime/models/plans.py:518`) takes only the native `Expression` AST, and
  the `expression_language: Literal["native", "python", "spreadsheet_formula"]`
  discriminator survives only on `LegacyDeriveColumnStep`;
- `WorkbookWriteIntent` (`plans.py:926`) has no formula field at all;
- `patch_service.py` never passes `formulas=` when it calls `compile_patch`.

So "add profit margin as a live spreadsheet formula" — 9.16.4 scenario 6, one of
the headline product examples — **cannot currently be planned**. Needs a typed
formula intent on the plan, validation for it, and the plumbing through
`patch_service` into `compile_patch`.

### R9 · 9.8 checkpoints are written but not wired

`runtime/execution/checkpoints.py` (123 lines) exists and is exercised directly
by `tests/test_data_analysis_phase9_durability.py`, but **nothing imports it**
outside its own tests — `execution/service.py` does not call
`should_checkpoint` or `stage_recipe_hash`. Consequences:

- `execution_checkpoint_created` is never emitted;
- pause/resume cannot skip a completed expensive stage;
- a crashed multi-stage run recomputes from the beginning.

### R10 · 9.8 stage scheduling and mid-execution pause

- `execution/dag.py` produces an ordered tuple of *steps*, not a graph of
  *stages*. Independent branches are never run concurrently (9.8.2).
- Pause is not honoured during native execution. `worker.py:1246`
  `cancellation_requested()` reads only `latest.cancellation_requested`;
  `pause_requested` is checked at the Phase 1–7 boundary
  (`worker.py:595`) but not inside `_execute_plan`. So pausing a long execution
  takes effect only after it finishes.

### R11 · 9.14.2 — five specified events are missing

Present and correct: `execution_queued`, `execution_started`,
`execution_step_completed`, `result_materialized`,
`result_validation_started/completed`, all seven patch events, `patch_rebased`,
`run_completed`.

Missing from `runtime/models/events.py`:

| Specified | Status |
| --- | --- |
| `execution_stage_started` | absent (`execution_inputs_resolved` exists instead) |
| `execution_checkpoint_created` | absent — blocked on R9 |
| `application_required` | absent |
| `patch_apply_failed` | absent |
| `workbook_revision_conflict` | approximated by `patch_conflict_detected` |

### R12 · 9.14.1 — two specified routes are missing

| Specified | Status |
| --- | --- |
| `POST /analysis/runs/{id}/apply-failure` | **absent.** `PatchApplicationReceipt` models success only; there is no way for the browser to report a failed apply, so a partial application cannot be reported durably. |
| `POST /analysis/runs/{id}/resolve-conflict` | folded into `POST /patch/preflight`, which returns the verdict and a rebased proposal. Arguably satisfied — decide deliberately rather than by accident. |

### R13 · README states a boundary the code does not produce

`README.md:686` says that until the adapter ships, "a workbook-writing run
**completes at its published result** rather than waiting on a handshake".
That is not what happens: `evaluate_admission` returns `PLAN_ONLY` with
`workbook_patches_not_installed`, so the run stops at `plan_ready` and never
executes at all. Rewrite that paragraph.

---

## Certification — Slice E / 9.16 (after Gate B)

### R14 · Integration tests (9.16.3) — none exist

Every one of the 421 Phase 9 tests is a unit or contract test. Missing:

- Phase 7 normalized reference → native result → Cloudinary metadata, as one flow
- worker crash, lease recovery and fencing under a real restart
- pause/resume at every checkpoint boundary (blocked on R9/R10)
- cancellation during a long stage
- duplicate approval and duplicate receipt races
- SSE disconnect/reconnect during execution, context wait and application wait
- Cloudinary upload / Mongo commit partial failures

### R15 · Atlas concurrency (9.16.3) — 8 tests skipped

`tests/test_phase8_mongo_replica_integration.py` skips all 8 tests with
*"MONGODB_TEST_URI is not configured"*. These are the only tests that prove
rectangle-overlap reservations behave correctly under real replica-set
transactions — exactly the property 9.11.5 says a normal unique index cannot
enforce. Point `MONGODB_TEST_URI` at a replica set and run them, then add the
two-patches-racing-for-overlapping-rectangles case.

### R16 · End-to-end portfolio scenarios (9.16.4) — 0 of 12 done

All twelve need the browser half. Scenario 6 additionally needs R8.

### R17 · Performance evidence (9.15.2 / 9.16.5) — not gathered

No benchmark fixtures, no small/medium/large profiles, no recorded timings.
9.15.2 explicitly asks for the tested hardware named in the README. Needed:
cold/warm execution time, peak memory, source/output bytes, stage count before
and after fusion, browser preview and apply duration, workbook responsiveness,
result hash.

### R18 · Security and privacy certification pass (9.15)

The controls are implemented and unit-tested. What has not happened is the
deliberate adversarial pass over them as a set — the thing that makes the
capability profile safe to advertise as complete.

---

## Suggested order

| # | Work | Why here |
| --- | --- | --- |
| 1 | **R1** (S8) | Blocks everything else in the browser. Also fixes the live `hidden_rows` privacy defect. |
| 2 | **R2** (S9) | Pure client work; the routes and payloads already exist. |
| 3 | **R3** (S10) | Makes the finished backend visible with zero mutation risk. |
| 4 | **R4** (S11) | The hard part (S6 undo) is already solved and contract-tested. |
| 5 | **R5** (S12) | Where the invariants live. |
| 6 | **Gate B** | `ANALYSIS_WORKBOOK_PATCHES_READY=true`. Not before R5. |
| 7 | **R6, R7** (S13, S14) | Conflict, durable undo, preview clone, performance. |
| 8 | **R8** | Formula intent — unlocks scenario 6 and a headline product claim. |
| 9 | **R9, R10** | Checkpoints, stage concurrency, mid-execution pause. |
| 10 | **R11, R12, R13** | Event/route/doc completeness. |
| 11 | **R14–R18** | Slice E certification. |

Items 8–11 are independent of 1–7 and can be interleaved when a session has room.

---

## Practice notes carried forward from `docs/phase9plan.html`

- **One session, one surface.** R1 is the only deliberate backend↔frontend
  crossing, and only because hash parity needs both sides in one head.
- **Open with the baseline, not the goal.** Run
  `.venv/bin/python -m unittest discover -s tests -t . -q` (873 expected) and
  `npm run typecheck && npm run verify` first, so a pre-existing failure is never
  mistaken for something you caused.
- **Hand over the card, not the plan.** Paste one item's goal, file list and
  done-when. The 1,655-line phase plan is reference material for a specific
  subsection, not context to load wholesale.
- **End green, end committed.** The flags mean an unfinished frontend never
  breaks the product.
