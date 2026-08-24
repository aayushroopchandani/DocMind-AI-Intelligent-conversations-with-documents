/**
 * One AI patch, one undo (Phase 9.13.4).
 *
 * This module is the only place in the codebase allowed to touch Univer's
 * command and undo/redo services. Everything else goes through
 * `applyAsOneUndo`, so a Univer upgrade has exactly one file to re-verify.
 *
 * ## How this mechanism was chosen
 *
 * 9.13.4 prescribes trying, in order: a stable public batching API; a
 * registered command carrying complete undo state; and only then a
 * version-specific fallback. Univer 0.25.1 has no stable public batching API,
 * so the candidates were measured against three dependent writes — `A1="A"`,
 * then `A1="B"`, then `B1="C"` — where undoing in the wrong order is visible:
 *
 * | mechanism                  | undo items | after one undo   |
 * |----------------------------|-----------:|------------------|
 * | no batching                |          3 | `A1="B"` — only the last write reverted |
 * | `__tempBatchingUndoRedo`   |          1 | `A1="A"` — **a value that never existed** |
 * | collapsing the stack       |          1 | `A1=""`  — correct |
 *
 * `__tempBatchingUndoRedo` is not merely deprecated, it is wrong. Its merge
 * (`_tryBatchingElements`) appends undo mutations in *forward* order, and undo
 * replays them in array order, so undoing overlapping or dependent operations
 * lands on an intermediate state. It fails silently and in the worst possible
 * way: the sheet is left holding data the user never entered. Worse, its redo
 * path is correct, so an undo-then-redo round trip looks healthy while a bare
 * undo corrupts. It must not be used, not even as a fallback.
 *
 * ## What this does instead
 *
 * It runs the *real* Univer commands — `setValues`, `setFormulas`, sheet
 * creation — because those commands do far more than write cells. Each one
 * gathers contributions from `SheetInterceptorService` and generates row
 * auto-height mutations, which is how formulas recalculate and number formats
 * follow a write. Hand-rolling mutations would silently drop all of that.
 *
 * Each of those commands pushes its own undo item. Afterwards this collapses
 * the items they pushed into a single one, concatenating the redo mutations in
 * order and the undo mutations **in reverse** — the last thing done is the
 * first thing undone. That is the one line `_tryBatchingElements` gets wrong.
 *
 * ## Constraints this imposes on callers
 *
 * Univer's `pitchTopUndoElement` and `popUndoToRedo` operate on the *focused*
 * unit, not on a unit you name. So the target workbook must be the current one
 * before applying, and nothing else may write to the workbook while a patch is
 * in flight. Both are checked below rather than assumed.
 */

import type { Injector } from "@univerjs/core";
import { IUndoRedoService, type IMutationInfo } from "@univerjs/core";
import type { FUniver } from "@univerjs/core/facade";

/**
 * The Univer version this adapter was verified against.
 *
 * `scripts/verify-univer-version.mjs` asserts this matches the installed
 * package, so an upgrade cannot land without someone re-running the contract
 * suite at `/dev/univer-contract`.
 */
export const VERIFIED_UNIVER_VERSION = "0.25.1";

/** Bumped whenever the transaction mechanism changes; travels on the receipt. */
export const PATCH_ADAPTER_VERSION = "1.0.0";

export class UniverPatchAdapterError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "UniverPatchAdapterError";
    this.code = code;
  }
}

/** One collapsed undo entry, in Univer's own shape. */
interface UndoRedoItem {
  unitID: string;
  undoMutations: IMutationInfo[];
  redoMutations: IMutationInfo[];
}

export interface OneUndoResult<T> {
  value: T;
  /** How many undo entries the underlying commands produced. */
  collapsedItems: number;
  /** Total mutations the single entry will replay on redo. */
  redoMutationCount: number;
}

export interface UniverPatchAdapter {
  readonly adapterVersion: string;
  readonly engineVersion: string;
  /**
   * Undo entries currently available for the focused workbook.
   *
   * Exposed so callers — including the contract suite — never need to reach
   * for Univer's undo service themselves.
   */
  undoDepth(): number;
  /**
   * Run `work` and leave the workbook with exactly one new undo entry.
   *
   * `work` performs ordinary facade operations. Whatever undo entries those
   * produce are merged into one before this resolves, so a single undo reverses
   * the whole patch and a single redo restores it.
   *
   * Throws `UniverPatchAdapterError` without merging if the workbook is not
   * current, if another apply is in flight, or if the entries produced cannot
   * be accounted for — in which case the caller must treat the apply as failed
   * and roll back, because the undo stack is not in the shape it expects.
   */
  applyAsOneUndo<T>(
    unitId: string,
    work: () => T | Promise<T>,
  ): Promise<OneUndoResult<T>>;
}

interface AdapterDependencies {
  api: FUniver;
  injector: Injector;
}

export function createUniverPatchAdapter(
  deps: AdapterDependencies,
): UniverPatchAdapter {
  const { api, injector } = deps;
  // One apply at a time. Two concurrent collapses would pop each other's
  // entries off the shared stack.
  let inFlight = false;

  const undoRedo = () => injector.get(IUndoRedoService);

  /** Current undo depth. `undoRedoStatus$` is a BehaviorSubject, so this is sync. */
  const depth = (): number => {
    let undos = 0;
    undoRedo()
      .undoRedoStatus$.subscribe((status) => {
        undos = status.undos;
      })
      .unsubscribe();
    return undos;
  };

  const requireCurrent = (unitId: string): void => {
    const active = api.getActiveWorkbook()?.getId();
    if (active !== unitId) {
      throw new UniverPatchAdapterError(
        "workbook_not_current",
        `The patch targets workbook ${unitId}, but ${active ?? "no workbook"} ` +
          "is current. Univer's undo stack is addressed by focus, so the " +
          "target must be focused before applying.",
      );
    }
  };

  /**
   * Take the `expected` most recent undo entries, newest first.
   *
   * Each is checked to belong to `unitId` before it is taken. A foreign entry
   * means the focused unit is not the one we think it is, so collection stops
   * rather than popping another workbook's history.
   */
  const takeRecentEntries = (unitId: string, expected: number): UndoRedoItem[] => {
    const service = undoRedo();
    const taken: UndoRedoItem[] = [];
    for (let index = 0; index < expected; index += 1) {
      const top = service.pitchTopUndoElement() as UndoRedoItem | null;
      if (!top || top.unitID !== unitId) break;
      // Oldest-first, so the merge below reads in application order.
      taken.unshift(top);
      service.popUndoToRedo();
    }
    return taken;
  };

  /**
   * Merge the `produced` most recent undo entries into one.
   *
   * Returns zero counts when nothing was produced — a patch that changed
   * nothing leaves no entry rather than an empty one.
   */
  const collapse = (
    unitId: string,
    produced: number,
  ): { entryCount: number; redoMutationCount: number } => {
    if (produced <= 0) return { entryCount: 0, redoMutationCount: 0 };

    const entries = takeRecentEntries(unitId, produced);
    if (entries.length !== produced) {
      throw new UniverPatchAdapterError(
        "undo_stack_unexpected",
        `Expected ${produced} undo entries for workbook ${unitId} but could ` +
          `only account for ${entries.length}. The workbook changed, but its ` +
          "undo history is not in the expected shape; roll back.",
      );
    }

    const redoMutations = entries.flatMap((entry) => entry.redoMutations);
    const undoMutations = [...entries]
      // The last operation applied is the first one undone. Univer's own
      // batching helper omits this reversal, which is why it is wrong.
      .reverse()
      .flatMap((entry) => entry.undoMutations);

    // `pushUndoRedo` clears the redo stack, so the entries moved there by
    // `popUndoToRedo` above are discarded here rather than left dangling.
    undoRedo().pushUndoRedo({ unitID: unitId, undoMutations, redoMutations });
    return { entryCount: entries.length, redoMutationCount: redoMutations.length };
  };

  return {
    adapterVersion: PATCH_ADAPTER_VERSION,
    engineVersion: VERIFIED_UNIVER_VERSION,
    undoDepth: depth,

    async applyAsOneUndo<T>(
      unitId: string,
      work: () => T | Promise<T>,
    ): Promise<OneUndoResult<T>> {
      if (inFlight) {
        throw new UniverPatchAdapterError(
          "apply_already_in_flight",
          "Another patch is being applied to this workbook.",
        );
      }
      requireCurrent(unitId);
      inFlight = true;
      const before = depth();
      try {
        const value = await work();
        const collapsed = collapse(unitId, depth() - before);
        return {
          value,
          collapsedItems: collapsed.entryCount,
          redoMutationCount: collapsed.redoMutationCount,
        };
      } catch (error) {
        // The patch failed part-way, so some operations have landed. Collapse
        // them anyway: one undo then reverses the partial application, where
        // leaving the entries separate would make the user press undo once per
        // operation and guess when to stop.
        //
        // This is cleanup, not recovery. Restoring the workbook is the caller's
        // job, using the durable inverse the backend compiled (9.12.3); a
        // best-effort undo here could not be verified against it.
        try {
          collapse(unitId, depth() - before);
        } catch {
          // Never let cleanup replace the failure the caller needs to see.
        }
        throw error;
      } finally {
        inFlight = false;
      }
    },
  };
}
