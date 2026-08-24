/**
 * One AI patch, one logical revision and one save (Phase 9.13.3).
 *
 * The workbook host treats every Univer mutation as a user edit: it advances
 * the artifact's logical revision and schedules a snapshot save. That is right
 * for typing, and wrong for a patch. A patch is many mutations — at least one
 * per operation, and more wherever a write drags interceptor or auto-height
 * mutations behind it — so applying one would advance the revision once per
 * mutation and write a snapshot for each.
 *
 * The revision is not cosmetic. It travels to the backend as `client_revision`
 * and is what a patch's guards are bound to, so a patch that advanced it
 * several times would invalidate its own approval.
 *
 * This coordinator makes the host's counting pause for the duration of one
 * transaction and then commit exactly once. It deliberately knows nothing about
 * Univer or about patches: the host supplies the two operations it needs, and
 * the caller composes it with the patch adapter. Undo semantics live in
 * `univer-patch-adapter.ts`; persistence semantics live here.
 *
 * ## Ordering
 *
 * The coordinator wraps the adapter, never the reverse:
 *
 *     coordinator.runAsOneRevision(unitId, () =>
 *       adapter.applyAsOneUndo(unitId, () => …operations…))
 *
 * so that anything the adapter does while recovering from a failure is absorbed
 * by the same transaction rather than committing a revision of its own.
 */

/** What the workbook host must be able to do for a transaction to work. */
export interface RevisionCommitHandler {
  /**
   * Persist any debounced save immediately.
   *
   * Called once as a transaction opens, so it starts from a state that is
   * already on disk. Without it, a save scheduled before the patch could fire
   * midway through and persist a half-applied workbook.
   */
  settle(unitId: string): void;
  /** Advance the logical revision by one and persist exactly one snapshot. */
  commit(unitId: string): void;
}

export class RevisionTransactionError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "RevisionTransactionError";
    this.code = code;
  }
}

export interface OneRevisionResult<T> {
  value: T;
  /** Mutations the host would otherwise have counted one by one. */
  absorbedMutations: number;
  /** Whether a revision was committed. False when nothing changed. */
  committed: boolean;
}

export interface RevisionCoordinator {
  /**
   * Offer a mutation to the open transaction.
   *
   * Returns `true` when the transaction has taken responsibility for it and the
   * host should do nothing. Returns `false` — for any mutation outside a
   * transaction, or belonging to a different workbook — so ordinary edits keep
   * their existing behaviour while a patch is in flight elsewhere.
   */
  absorbMutation(unitId: string): boolean;

  /** The workbook currently inside a transaction, if any. */
  activeUnitId(): string | null;

  /**
   * Run `work` as a single logical revision.
   *
   * Commits once on success, and only if something actually changed. On
   * failure it commits nothing — the caller is expected to have restored the
   * workbook — and always releases the transaction.
   */
  runAsOneRevision<T>(
    unitId: string,
    work: () => T | Promise<T>,
  ): Promise<OneRevisionResult<T>>;
}

interface OpenTransaction {
  unitId: string;
  mutations: number;
}

export function createRevisionCoordinator(
  handler: RevisionCommitHandler,
): RevisionCoordinator {
  // One transaction at a time, across all workbooks. A patch applies to the
  // focused workbook and the adapter refuses any other, so there is no case
  // for two at once — and one flag is easier to reason about than a map.
  let open: OpenTransaction | null = null;

  return {
    absorbMutation(unitId: string): boolean {
      if (open === null || open.unitId !== unitId) return false;
      open.mutations += 1;
      return true;
    },

    activeUnitId(): string | null {
      return open?.unitId ?? null;
    },

    async runAsOneRevision<T>(
      unitId: string,
      work: () => T | Promise<T>,
    ): Promise<OneRevisionResult<T>> {
      if (open !== null) {
        throw new RevisionTransactionError(
          "revision_transaction_open",
          `A revision transaction is already open for ${open.unitId}.`,
        );
      }

      // Flush before suppressing, so a save scheduled a moment ago cannot fire
      // mid-patch and persist a half-applied workbook.
      handler.settle(unitId);
      const transaction: OpenTransaction = { unitId, mutations: 0 };
      open = transaction;

      let value: T;
      try {
        value = await work();
      } finally {
        // Released before committing: `commit` writes a snapshot, and that
        // write must be counted the ordinary way if it produces mutations.
        open = null;
      }

      if (transaction.mutations === 0) {
        // Nothing touched the workbook, so there is no new revision to
        // announce and nothing new to save.
        return { value, absorbedMutations: 0, committed: false };
      }

      handler.commit(unitId);
      return {
        value,
        absorbedMutations: transaction.mutations,
        committed: true,
      };
    },
  };
}

/* ------------------------------------------------------------------ */
/* The instance the application shares                                  */
/* ------------------------------------------------------------------ */

/**
 * Until the workbook host mounts there is nowhere to persist to, so a
 * transaction opened against this refuses rather than silently dropping a
 * commit. Tests build their own coordinator instead.
 */
const UNMOUNTED: RevisionCommitHandler = {
  settle() {
    throw new RevisionTransactionError(
      "workbook_host_unmounted",
      "No workbook host is mounted, so a revision cannot be committed.",
    );
  },
  commit() {
    throw new RevisionTransactionError(
      "workbook_host_unmounted",
      "No workbook host is mounted, so a revision cannot be committed.",
    );
  },
};

let handler: RevisionCommitHandler = UNMOUNTED;
const shared = createRevisionCoordinator({
  settle: (unitId) => handler.settle(unitId),
  commit: (unitId) => handler.commit(unitId),
});

/** Called by the workbook host on mount, and with `null` on unmount. */
export function setRevisionCommitHandler(
  next: RevisionCommitHandler | null,
): void {
  handler = next ?? UNMOUNTED;
}

/** The coordinator the workbook host and the apply path share. */
export function getRevisionCoordinator(): RevisionCoordinator {
  return shared;
}
