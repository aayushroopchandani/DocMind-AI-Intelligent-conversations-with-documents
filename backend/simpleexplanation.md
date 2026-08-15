# Phase 9 in simple words

## What Phase 8 already does

Phase 8 can take a request such as:

> Filter the rows where revenue is above 50,000 and put the result beside my table.

It understands the request, finds the data, creates a typed plan, validates the plan, saves the run and asks for approval when needed.

It does **not** yet run the filter or edit the spreadsheet. That missing work is Phase 9.

## What Phase 9 will do

Phase 9 will:

1. Run normal data operations safely.
2. Save the result as a new versioned dataset.
3. Decide where the result can safely go in the spreadsheet.
4. Show the exact proposed spreadsheet change.
5. Wait for the user to press Apply.
6. Apply the whole change as one action.
7. Save proof that it was applied.
8. Let the user undo it.

The full flow becomes:

```text
User request
  -> validated Phase 8 plan
  -> safe data execution
  -> checked result
  -> spreadsheet preview
  -> user approval
  -> spreadsheet edit
  -> save receipt and undo information
```

## The most important rule

The LLM will never directly edit the spreadsheet and will never directly run Polars, Python or JavaScript code.

The LLM only creates a typed plan. Normal application code validates and executes that plan.

```text
LLM says what should happen
Deterministic code decides whether it is valid
Polars performs the data work
Backend creates a safe patch
Frontend applies the patch after approval
```

## What can be done after Phase 9

Examples:

- Select, rename and reorder columns.
- Filter revenue above 50,000.
- Sort rows.
- Convert a text column into dates or numbers.
- Fill or remove missing values.
- Remove duplicate customers.
- Calculate a new value column.
- Group sales by month and region.
- Join two tables.
- Pivot or unpivot a table.
- Generate repeatable sample data.
- Put a result beside the selected table.
- Put a result in a new sheet when there is no safe empty space.
- Add a spreadsheet formula and fill it down.
- Preview, apply and undo an AI spreadsheet edit.

## What Phase 9 will not do

Phase 9 will not yet run arbitrary LLM-generated Python.

It will also not yet create advanced ML graphs, KNN plots, chart dashboards or arbitrary images inside the workbook. Those features should use the same result and patch system in later phases.

The planner must clearly say that these features are unavailable. It must not pretend that a Python or chart executor exists.

## Why we need Plan Schema v2 first

The current Phase 8 plan is strongly typed in most places, but two parts are still written as normal text:

- how to calculate a derived column;
- how to generate random data.

Normal text is not safe to execute. In Phase 9, these become small typed objects.

Instead of:

```text
expression: "calculate revenue minus cost"
```

we store something like:

```text
subtract
  left: revenue column
  right: cost column
```

Only supported operations are allowed. There is no `eval` and no hidden Python.

## The native data engine

We will use Polars for normal table work.

Polars is used because it is fast, column-based and can combine several operations before reading or writing all the data.

For example, these three steps:

```text
select needed columns
filter revenue
group by region
```

can be optimized into one lazy data job instead of creating three full copies of the table.

The work should run in a separate bounded process so a large task does not freeze the FastAPI server. The process receives only the validated recipe and temporary input files. It does not receive MongoDB, Cloudinary or LLM secrets.

## Supported native operations

The first engine version should support:

- select/rename/reorder;
- filter/sort;
- safe type conversion;
- missing values;
- deduplication;
- derived columns;
- grouping and aggregation;
- joins;
- pivot/unpivot;
- seeded sample-data generation.

Every operation must have exact rules for nulls, dates, decimals, sorting and errors. We should not depend on changing library defaults.

## Repeatable random data

The LLM creates only the schema, not thousands of values.

Example:

```text
100 sales rows
seed: 91342

transaction_id: unique ID
date: between January and December 2024
region: North, South, East or West
revenue: between 1,000 and 100,000
cost: always lower than revenue
```

The normal generator creates the rows. The seed and generator version are saved, so running the same recipe again creates the same data.

If the user only says “generate random data,” the agent should ask what kind of data is needed or show a small assumed example first.

## Results and storage

Every result gets:

- a dataset ID and version;
- schema before and after;
- input/output row counts;
- rows added, changed or removed;
- warnings;
- a small preview;
- execution time;
- lineage showing how it was made;
- a replay recipe;
- a content hash.

For now, files stay in Cloudinary:

```text
compressed CSV data
schema information
lineage information
small preview
optional XLSX export
```

MongoDB stores only IDs, hashes, status, small metrics and Cloudinary references. It does not store the full table.

## What a spreadsheet patch means

A patch is a safe description of spreadsheet changes. It is not JavaScript and it is not a Univer command.

Example:

```text
Create a new sheet called Filtered Revenue
Write 73 rows into A1:F74
Add percentage formatting to column F
Expected workbook revision: 12
Expected target cells: empty
```

Each patch has:

- workbook and worksheet IDs;
- base workbook revision;
- exact source and target hashes;
- ordered operations;
- affected-cell count;
- before/after preview;
- patch hash and idempotency key;
- inverse information for undo.

Univer only receives this patch through one frontend adapter after the user approves it.

## Safely placing a result

When the result size is known, the backend asks the browser for fresh information about the possible target area.

For “place it beside my table”:

1. Start two empty columns to the right.
2. Check the complete result rectangle.
3. Check values, formulas, merged cells, protection and other occupied objects.
4. Use the space only when the whole rectangle is safe.
5. Otherwise create a new sheet.

This extra check is necessary because the original Phase 8 snapshot may not contain every future target cell.

The system must never assume that an unseen area is empty.

## Adding spreadsheet formulas

The LLM creates a meaning-based formula description, for example:

```text
profit margin
  = (revenue - cost) / revenue
on division by zero: 0
format: percentage
fill down all result rows
```

The backend checks the columns and compiles the description into safe spreadsheet formulas after the final target range is known.

Unknown functions, network functions, external workbook links and dangerous dynamic references are blocked.

The backend calculates a few example results, and the frontend checks a few evaluated cells during preview.

## Preview and approval

The user sees:

- the exact sheet and range;
- rows and cells affected;
- formulas and formats being added;
- before/after samples;
- warnings;
- why a new sheet was selected, if needed.

Preview does not edit the saved workbook. It can use a temporary workbook copy or an overlay.

The real change only happens after the user presses Apply.

## Applying the patch

Before editing anything, the frontend checks:

- the correct workbook and sheet are open;
- the workbook revision is still valid;
- source and target hashes still match;
- all payload pieces downloaded correctly;
- the patch was not already applied;
- every operation is supported.

Then it applies the whole patch as one logical action, saves the workbook once and increases the workbook revision once.

If sending the receipt fails after the workbook was saved, the frontend retries the receipt. It does not apply the patch again.

## Undo

There are two undo types:

1. Normal Ctrl/Cmd+Z should undo the complete AI edit in one action.
2. A saved inverse patch can undo the edit later after checking for conflicts.

The second undo is stored as a new auditable action instead of erasing history.

## What happens if the workbook changed

The old patch is not blindly applied.

```text
Only revision changed, cells are the same
  -> safely rebase the patch

Source data changed
  -> create a new linked run and plan again

Target area became occupied
  -> move the result or use a new sheet

Patch is already present
  -> recover the apply receipt without editing again

Part of the patch is present
  -> stop and recover/undo; never continue partly
```

## Pause, cancel and recovery

The engine groups work into safe stages.

It checks Pause and Cancel between stages. Expensive completed stages can be saved as checkpoints.

- Pause keeps the same run and resumes from the last valid checkpoint.
- Cancel makes the run terminal.
- Resume Cancelled creates a new linked run.
- A backend crash can recover a valid checkpoint.
- A stale worker is not allowed to publish over a newer worker.

## Frontend work

The frontend needs:

- an execution progress card;
- a patch preview card;
- a small before/after table;
- conflict choices;
- Apply/Reject/Undo actions;
- a `UniverPatchAdapter`;
- a coordinator that treats many cell commands as one revision and one undo action;
- retry logic for apply receipts;
- virtualized previews and chunked bulk writes for large data.

All direct Univer-specific code stays inside the adapter. This makes future Univer upgrades safer.

## New backend records

Use the existing run, event, plan, artifact and patch collections. Add records for:

- executions;
- step/stage execution summaries;
- patch placement contexts;
- apply receipts;
- exact workbook write-range reservations.

The write reservation prevents two runs from writing into overlapping rectangles at the same time.

## The sandbox decision

Phase 9 does not run arbitrary Python, so its trusted Polars engine does not need the Python sandbox.

For the later Python/ML phase, use a separate sandbox interface.

Best local portfolio choice:

```text
Docker Desktop hardened container
```

It is free for personal and educational use, easy to show in a portfolio and works well on macOS. Podman rootless is the best fully open-source alternative.

The future container should have:

- no network;
- no secrets;
- non-root user;
- read-only system files;
- dropped capabilities;
- CPU, memory, process, file and time limits;
- read-only input data;
- one small output directory;
- pinned packages;
- deletion after the run.

On a Linux host, gVisor can later give stronger isolation. Firecracker is powerful but needs Linux/KVM and is too much infrastructure for this portfolio.

A Python virtual environment, subprocess or Jupyter kernel by itself is not a security sandbox.

## Best implementation order

### Part 1 — Execute one safe read-only operation

- Capability changes.
- Plan Schema v2.
- Durable input lookup.
- Polars engine boundary.
- Filter/select execution.
- Execution events and recovery.

### Part 2 — Complete table operations

- All normal operations.
- Seeded data generation.
- Result validation.
- Cloudinary result files.
- Lineage and replay.

### Part 3 — Create safe patches

- Patch protocol.
- Matching backend/frontend hashes.
- Fresh placement context.
- Collision checks and range reservations.

### Part 4 — Edit the spreadsheet

- Preview UI.
- Univer adapter.
- Apply receipt.
- Conflict handling.
- Formula compiler.
- One-step undo.

### Part 5 — Test and prove it

- Concurrency tests.
- Crash/pause/resume tests.
- Large-data tests.
- Cross-user security tests.
- End-to-end demos.
- Performance measurements from the real local machine.

## How to know Phase 9 is finished

Phase 9 is finished when a user can ask for a normal table operation, watch it execute, preview the exact spreadsheet change, apply it safely, reload/reconnect, and undo it—and the same input and recipe always produce the same result.

Charts, dashboards, KNN graphs and arbitrary Python should then be built on top of these same result and patch contracts in later phases.

For the detailed technical design, read [phase9plan.md](./phase9plan.md).
