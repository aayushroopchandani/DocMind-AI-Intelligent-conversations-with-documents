import {
  PDF_DB_NAME,
  PDF_DB_STORE,
  PDF_DB_VERSION,
  PDF_RECORD_SCHEMA_VERSION,
} from "@/lib/data-analysis/constants";

/**
 * Browser-only blob storage for uploaded PDFs.
 *
 * PDFs are far too large for localStorage (and must never be base64'd), so
 * the bytes live in IndexedDB keyed by artifact id while the lightweight
 * metadata stays in the existing workspace persistence layer.
 *
 * Every operation is defensive: a browser with IndexedDB disabled, a blocked
 * upgrade, or a corrupted row resolves to `null`/`false` instead of throwing,
 * so one unavailable PDF can never take down the workspace.
 */

interface PdfBlobRecord {
  schemaVersion: number;
  blob: Blob;
  fileName: string;
  fileSize: number;
  mimeType: string;
  savedAt: number;
}

/** Raised for genuine storage failures so callers can show a real reason. */
export class PdfStorageError extends Error {
  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options);
    this.name = "PdfStorageError";
  }
}

function isSupported(): boolean {
  return typeof window !== "undefined" && "indexedDB" in window;
}

/**
 * Single shared connection. Cached because `open()` is asynchronous and the
 * workspace may load several PDFs at once; a failed open is not cached so a
 * later attempt can retry.
 */
let dbPromise: Promise<IDBDatabase> | null = null;

function openDatabase(): Promise<IDBDatabase> {
  if (!isSupported()) {
    return Promise.reject(
      new PdfStorageError("This browser cannot store PDFs locally."),
    );
  }
  if (dbPromise) return dbPromise;

  dbPromise = new Promise<IDBDatabase>((resolve, reject) => {
    let request: IDBOpenDBRequest;
    try {
      request = window.indexedDB.open(PDF_DB_NAME, PDF_DB_VERSION);
    } catch (error) {
      reject(new PdfStorageError("Local PDF storage is unavailable.", { cause: error }));
      return;
    }

    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(PDF_DB_STORE)) {
        db.createObjectStore(PDF_DB_STORE);
      }
    };
    request.onsuccess = () => {
      const db = request.result;
      // A version change from another tab invalidates this handle.
      db.onversionchange = () => {
        db.close();
        dbPromise = null;
      };
      resolve(db);
    };
    request.onerror = () =>
      reject(
        new PdfStorageError("Local PDF storage could not be opened.", {
          cause: request.error,
        }),
      );
    request.onblocked = () =>
      reject(
        new PdfStorageError(
          "Local PDF storage is blocked by another tab of this app.",
        ),
      );
  }).catch((error) => {
    dbPromise = null;
    throw error;
  });

  return dbPromise;
}

function runTransaction<T>(
  mode: IDBTransactionMode,
  work: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  return openDatabase().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        let request: IDBRequest<T>;
        try {
          const transaction = db.transaction(PDF_DB_STORE, mode);
          request = work(transaction.objectStore(PDF_DB_STORE));
          transaction.onabort = () =>
            reject(
              new PdfStorageError("Local PDF storage rejected the write.", {
                cause: transaction.error,
              }),
            );
        } catch (error) {
          reject(
            new PdfStorageError("Local PDF storage is unavailable.", {
              cause: error,
            }),
          );
          return;
        }
        request.onsuccess = () => resolve(request.result);
        request.onerror = () =>
          reject(
            new PdfStorageError("Local PDF storage request failed.", {
              cause: request.error,
            }),
          );
      }),
  );
}

/* ------------------------------------------------------------------ */
/* Public API                                                          */
/* ------------------------------------------------------------------ */

/** Persists a picked file. Throws `PdfStorageError` when it cannot be saved. */
export async function savePdfBlob(
  artifactId: string,
  file: File,
): Promise<void> {
  const record: PdfBlobRecord = {
    schemaVersion: PDF_RECORD_SCHEMA_VERSION,
    // Storing the File itself is fine (a File *is* a Blob) and keeps the
    // structured clone cheap — no copy into an ArrayBuffer here.
    blob: file,
    fileName: file.name,
    fileSize: file.size,
    mimeType: file.type,
    savedAt: Date.now(),
  };
  await runTransaction("readwrite", (store) => store.put(record, artifactId));
}

/**
 * Reads a stored PDF as a transferable buffer for the engine.
 *
 * Returns `null` when the row is absent or unreadable — the caller shows the
 * "no longer available" state rather than treating it as a hard failure.
 */
export async function loadPdfBuffer(
  artifactId: string,
): Promise<ArrayBuffer | null> {
  let record: PdfBlobRecord | undefined;
  try {
    record = await runTransaction<PdfBlobRecord | undefined>(
      "readonly",
      (store) => store.get(artifactId) as IDBRequest<PdfBlobRecord | undefined>,
    );
  } catch {
    return null;
  }

  if (
    !record ||
    record.schemaVersion !== PDF_RECORD_SCHEMA_VERSION ||
    !(record.blob instanceof Blob)
  ) {
    return null;
  }

  try {
    return await record.blob.arrayBuffer();
  } catch {
    return null;
  }
}

/** Removes a stored PDF. Best-effort: deletion must never block the UI. */
export async function deletePdfBlob(artifactId: string): Promise<void> {
  try {
    await runTransaction("readwrite", (store) => store.delete(artifactId));
  } catch {
    // The artifact is gone from the workspace either way.
  }
}

/**
 * Drops blobs that no longer belong to any artifact — e.g. left behind when
 * the tab closed between the blob write and the metadata write. Called once
 * per session after hydration.
 */
export async function cleanupOrphanPdfBlobs(
  validIds: readonly string[],
): Promise<void> {
  try {
    const keys = await runTransaction<IDBValidKey[]>("readonly", (store) =>
      store.getAllKeys(),
    );
    const valid = new Set<string>(validIds);
    const orphans = keys.filter(
      (key) => typeof key === "string" && !valid.has(key),
    );
    await Promise.all(orphans.map((key) => deletePdfBlob(String(key))));
  } catch {
    // Best-effort cleanup; never break the page over storage access.
  }
}
