/**
 * The adapter contract suite (Phase 9.13.4).
 *
 * Univer's undo behaviour cannot be tested in Node — it needs a renderer and a
 * DOM — so this is written as a plain function that a browser page drives, and
 * `/dev/univer-contract` is the page. 9.13.4 asks for exactly this: a suite to
 * run against the pinned version before any upgrade.
 *
 * The scenarios are chosen to fail loudly if the mechanism regresses to
 * Univer's own batching helper, which merges undo mutations in forward order.
 * That is why every scenario contains a *dependent* pair of operations: a
 * forward-order undo lands on an intermediate state, and only a correct reverse
 * ordering returns the sheet to where it started.
 */

import type { FUniver } from "@univerjs/core/facade";
import type { UniverPatchAdapter } from "@/lib/data-analysis/patches/univer-patch-adapter";
import type { RevisionCoordinator } from "@/lib/data-analysis/patches/revision-coordinator";

export interface ContractCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface ContractReport {
  checks: ContractCheck[];
  passed: boolean;
  engineVersion: string;
  adapterVersion: string;
}

type Cells = Record<string, string>;

interface Harness {
  api: FUniver;
  adapter: UniverPatchAdapter;
  /**
   * A coordinator wired to a mutation listener exactly as the workbook host
   * wires it, so the composition can be measured against real Univer traffic
   * rather than a simulation of it.
   */
  coordinator: RevisionCoordinator;
  /** What the coordinator asked the host to do, in order. */
  hostCalls: string[];
  /** Create a fresh, focused workbook and return its unit id. */
  createWorkbook: (name: string) => Promise<string>;
  disposeWorkbook: (unitId: string) => void;
  settle: () => Promise<void>;
}

function read(api: FUniver, unitId: string, keys: string[]): Cells {
  const sheet = api.getWorkbook(unitId)?.getActiveSheet();
  const cells: Cells = {};
  for (const key of keys) {
    const value = sheet?.getRange(key).getValue();
    cells[key] = value === null || value === undefined ? "" : String(value);
  }
  return cells;
}

function write(api: FUniver, unitId: string, a1: string, value: string): void {
  api.getWorkbook(unitId)?.getActiveSheet()?.getRange(a1).setValue(value);
}

function same(actual: Cells, expected: Cells): boolean {
  return JSON.stringify(actual) === JSON.stringify(expected);
}

/**
 * Run the suite.
 *
 * Every scenario builds its own workbook so one failure cannot contaminate the
 * next, and disposes it afterwards.
 */
export async function runUniverContract(
  harness: Harness,
): Promise<ContractReport> {
  const { api, adapter, settle } = harness;
  const checks: ContractCheck[] = [];

  const record = (name: string, passed: boolean, detail: string) => {
    checks.push({ name, passed, detail });
  };

  /* -------- dependent writes collapse into one reversible undo -------- */
  {
    const unitId = await harness.createWorkbook("contract-dependent");
    const keys = ["A1", "B1"];
    const before = read(api, unitId, keys);
    const depthBefore = adapter.undoDepth();

    const result = await adapter.applyAsOneUndo(unitId, () => {
      // A1 is written twice: the undo order is the whole question.
      write(api, unitId, "A1", "A");
      write(api, unitId, "A1", "B");
      write(api, unitId, "B1", "C");
      return 3;
    });
    await settle();

    const applied = read(api, unitId, keys);
    record(
      "three operations apply",
      same(applied, { A1: "B", B1: "C" }),
      JSON.stringify(applied),
    );
    record(
      "three operations leave one undo entry",
      adapter.undoDepth() - depthBefore === 1,
      `depth delta ${adapter.undoDepth() - depthBefore}, collapsed ${result.collapsedItems}`,
    );

    await api.undo();
    await settle();
    const undone = read(api, unitId, keys);
    record(
      "one undo reverses all three, in reverse order",
      same(undone, before),
      `${JSON.stringify(undone)} (a forward-order merge leaves A1="A")`,
    );

    await api.redo();
    await settle();
    const redone = read(api, unitId, keys);
    record(
      "one redo restores all three",
      same(redone, { A1: "B", B1: "C" }),
      JSON.stringify(redone),
    );

    harness.disposeWorkbook(unitId);
  }

  /* -------- a patch over existing content restores that content -------- */
  {
    const unitId = await harness.createWorkbook("contract-overwrite");
    const keys = ["A1", "A2"];
    write(api, unitId, "A1", "original");
    write(api, unitId, "A2", "keep");
    await settle();
    const before = read(api, unitId, keys);

    await adapter.applyAsOneUndo(unitId, () => {
      write(api, unitId, "A1", "patched");
      write(api, unitId, "A1", "patched twice");
    });
    await settle();

    await api.undo();
    await settle();
    const undone = read(api, unitId, keys);
    record(
      "undo restores pre-existing values rather than an intermediate",
      same(undone, before),
      JSON.stringify(undone),
    );

    harness.disposeWorkbook(unitId);
  }

  /* -------- work that changes nothing collapses nothing -------- */
  {
    const unitId = await harness.createWorkbook("contract-noop");
    const depthBefore = adapter.undoDepth();
    const result = await adapter.applyAsOneUndo(unitId, () => "nothing");
    await settle();
    record(
      "a patch that writes nothing adds no undo entry",
      result.collapsedItems === 0 && adapter.undoDepth() === depthBefore,
      `collapsed ${result.collapsedItems}, depth delta ${adapter.undoDepth() - depthBefore}`,
    );
    harness.disposeWorkbook(unitId);
  }

  /* -------- the adapter refuses to touch an unfocused workbook -------- */
  {
    const target = await harness.createWorkbook("contract-target");
    const other = await harness.createWorkbook("contract-other");
    await settle();
    // `other` was created last, so it is current; `target` is not.
    let refused = false;
    let code = "";
    try {
      await adapter.applyAsOneUndo(target, () => {
        write(api, target, "A1", "should not happen");
      });
    } catch (error) {
      refused = true;
      code = (error as { code?: string }).code ?? "";
    }
    record(
      "applying to an unfocused workbook is refused",
      refused && code === "workbook_not_current",
      refused ? `refused with ${code}` : "the adapter applied it anyway",
    );
    harness.disposeWorkbook(other);
    harness.disposeWorkbook(target);
  }

  /* -------- two applies cannot interleave -------- */
  {
    const unitId = await harness.createWorkbook("contract-single-flight");
    let concurrentCode = "";
    await adapter.applyAsOneUndo(unitId, async () => {
      write(api, unitId, "A1", "first");
      try {
        await adapter.applyAsOneUndo(unitId, () => {
          write(api, unitId, "B1", "second");
        });
      } catch (error) {
        concurrentCode = (error as { code?: string }).code ?? "";
      }
    });
    await settle();
    record(
      "a second apply during one in flight is refused",
      concurrentCode === "apply_already_in_flight",
      concurrentCode || "the adapter allowed a nested apply",
    );
    harness.disposeWorkbook(unitId);
  }

  /* -------- a failed patch leaves one undo entry, not several -------- */
  {
    const unitId = await harness.createWorkbook("contract-partial");
    const depthBefore = adapter.undoDepth();
    let raised = "";

    try {
      await adapter.applyAsOneUndo(unitId, () => {
        write(api, unitId, "A1", "one");
        write(api, unitId, "B1", "two");
        throw new Error("operation three failed");
      });
    } catch (error) {
      raised = (error as Error).message;
    }
    await settle();

    record(
      "a failing patch surfaces its own error",
      raised === "operation three failed",
      raised || "nothing was thrown",
    );
    record(
      "a partly applied patch still collapses to one undo entry",
      adapter.undoDepth() - depthBefore === 1,
      `depth delta ${adapter.undoDepth() - depthBefore}`,
    );

    await api.undo();
    await settle();
    const undone = read(api, unitId, ["A1", "B1"]);
    record(
      "one undo clears the whole partial application",
      same(undone, { A1: "", B1: "" }),
      JSON.stringify(undone),
    );

    harness.disposeWorkbook(unitId);
  }

  /* -------- coordinator and adapter, composed over real mutations -------- */
  {
    const unitId = await harness.createWorkbook("contract-composed");
    harness.hostCalls.length = 0;

    const result = await harness.coordinator.runAsOneRevision(unitId, () =>
      adapter.applyAsOneUndo(unitId, () => {
        write(api, unitId, "A1", "one");
        write(api, unitId, "A1", "two");
        write(api, unitId, "B1", "three");
      }),
    );
    await settle();

    record(
      "a real patch commits exactly one revision",
      JSON.stringify(harness.hostCalls) ===
        JSON.stringify([`settle:${unitId}`, `commit:${unitId}`]),
      JSON.stringify(harness.hostCalls),
    );
    record(
      "and absorbs every mutation those operations produced",
      // At least one per operation, and possibly many more: a write to a sheet
      // with formulas, number formats or auto-height rows drags interceptor
      // and auto-height mutations along with it. The count is not knowable in
      // advance, which is exactly why the host cannot tally them itself.
      result.absorbedMutations >= 3,
      `${result.absorbedMutations} mutations absorbed for 3 operations`,
    );
    record(
      "while still leaving one undo entry",
      result.value.collapsedItems === 3,
      `collapsed ${result.value.collapsedItems}`,
    );

    harness.disposeWorkbook(unitId);
  }

  return {
    checks,
    passed: checks.every((check) => check.passed),
    engineVersion: adapter.engineVersion,
    adapterVersion: adapter.adapterVersion,
  };
}
