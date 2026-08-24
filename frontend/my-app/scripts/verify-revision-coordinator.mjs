/**
 * Check that one AI patch becomes one logical revision and one save.
 *
 * The coordinator deliberately depends on nothing but a two-method handler, so
 * unlike the Univer adapter it can be exercised without a DOM. That is the
 * point of the seam: the part that must be right — how many revisions a patch
 * announces, and when — is testable on its own.
 *
 * The revision is not cosmetic. It travels to the backend as `client_revision`
 * and a patch's guards are bound to it, so a patch that advanced it more than
 * once would invalidate its own approval.
 *
 * Run with:  npm run verify:revision-coordinator
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, cpSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const workspace = mkdtempSync(join(tmpdir(), "revision-coordinator-"));
cpSync(
  "lib/data-analysis/patches/revision-coordinator.ts",
  join(workspace, "revision-coordinator.ts"),
);

execFileSync(
  "./node_modules/.bin/tsc",
  [
    "--target", "es2022",
    "--module", "es2022",
    "--moduleResolution", "bundler",
    "--lib", "es2022,dom",
    "--strict",
    "--outDir", join(workspace, "out"),
    join(workspace, "revision-coordinator.ts"),
  ],
  { stdio: "inherit" },
);

for (const name of readdirSync(join(workspace, "out")).filter((f) => f.endsWith(".js"))) {
  const file = join(workspace, "out", name);
  writeFileSync(
    file,
    readFileSync(file, "utf8").replace(/from "(\.\/[\w.-]+)"/g, (whole, path) =>
      path.endsWith(".js") ? whole : `from "${path}.js"`,
    ),
    "utf8",
  );
}

const {
  createRevisionCoordinator,
  getRevisionCoordinator,
  setRevisionCommitHandler,
} = await import(
  pathToFileURL(join(workspace, "out", "revision-coordinator.js")).href
);

const failures = [];
let checks = 0;

function check(label, actual, expected) {
  checks += 1;
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) failures.push(`${label}\n  expected: ${e}\n  actual:   ${a}`);
}

/** A handler that records what the host would have been asked to do. */
function recorder(overrides = {}) {
  const calls = [];
  return {
    calls,
    settle: overrides.settle ?? ((unitId) => calls.push(`settle:${unitId}`)),
    commit: overrides.commit ?? ((unitId) => calls.push(`commit:${unitId}`)),
  };
}

const BOOK = "workbook-1";
const OTHER = "workbook-2";

/* -------- many mutations, one revision -------- */
{
  const host = recorder();
  const coordinator = createRevisionCoordinator(host);

  const result = await coordinator.runAsOneRevision(BOOK, () => {
    // A real patch emits several mutations per operation: the write itself,
    // plus interceptor and auto-height mutations.
    for (let i = 0; i < 17; i += 1) coordinator.absorbMutation(BOOK);
    return "applied";
  });

  check("seventeen mutations commit once", host.calls, [
    `settle:${BOOK}`,
    `commit:${BOOK}`,
  ]);
  check("the absorbed count is reported", result.absorbedMutations, 17);
  check("committed is true", result.committed, true);
  check("the work's value is returned", result.value, "applied");
}

/* -------- settle happens before any mutation -------- */
{
  const order = [];
  const coordinator = createRevisionCoordinator({
    settle: () => order.push("settle"),
    commit: () => order.push("commit"),
  });
  await coordinator.runAsOneRevision(BOOK, () => {
    order.push("mutation");
    coordinator.absorbMutation(BOOK);
  });
  check(
    "a pending save is flushed before the patch touches anything",
    order,
    ["settle", "mutation", "commit"],
  );
}

/* -------- outside a transaction nothing is absorbed -------- */
{
  const coordinator = createRevisionCoordinator(recorder());
  check("no transaction absorbs nothing", coordinator.absorbMutation(BOOK), false);
  check("no active unit", coordinator.activeUnitId(), null);
}

/* -------- another workbook keeps saving normally -------- */
{
  const host = recorder();
  const coordinator = createRevisionCoordinator(host);
  let otherAbsorbed = null;
  let activeDuring = null;

  await coordinator.runAsOneRevision(BOOK, () => {
    coordinator.absorbMutation(BOOK);
    // A user typing in a second open workbook must not be swallowed by a
    // patch running against the first.
    otherAbsorbed = coordinator.absorbMutation(OTHER);
    activeDuring = coordinator.activeUnitId();
  });

  check("a different workbook's edit is not absorbed", otherAbsorbed, false);
  check("the active unit is the one under patch", activeDuring, BOOK);
  check("only the patched workbook commits", host.calls, [
    `settle:${BOOK}`,
    `commit:${BOOK}`,
  ]);
}

/* -------- a patch that changes nothing commits nothing -------- */
{
  const host = recorder();
  const coordinator = createRevisionCoordinator(host);
  const result = await coordinator.runAsOneRevision(BOOK, () => "no writes");

  check("no mutations means no commit", host.calls, [`settle:${BOOK}`]);
  check("committed is false", result.committed, false);
  check("absorbed is zero", result.absorbedMutations, 0);
}

/* -------- a failed patch commits no revision -------- */
{
  const host = recorder();
  const coordinator = createRevisionCoordinator(host);
  let raised = null;

  try {
    await coordinator.runAsOneRevision(BOOK, () => {
      coordinator.absorbMutation(BOOK);
      coordinator.absorbMutation(BOOK);
      throw new Error("operation three failed");
    });
  } catch (error) {
    raised = error.message;
  }

  check("the failure reaches the caller", raised, "operation three failed");
  check("a failed patch announces no revision", host.calls, [`settle:${BOOK}`]);
  check("the transaction is released", coordinator.activeUnitId(), null);
  checks += 1;
  if (coordinator.absorbMutation(BOOK) !== false) {
    failures.push("mutations were still absorbed after a failure");
  }
}

/* -------- transactions cannot nest -------- */
{
  const coordinator = createRevisionCoordinator(recorder());
  let code = "";
  await coordinator.runAsOneRevision(BOOK, async () => {
    try {
      await coordinator.runAsOneRevision(BOOK, () => undefined);
    } catch (error) {
      code = error.code;
    }
  });
  check("a nested transaction is refused", code, "revision_transaction_open");
}

/* -------- a second transaction is possible after the first -------- */
{
  const host = recorder();
  const coordinator = createRevisionCoordinator(host);
  await coordinator.runAsOneRevision(BOOK, () => coordinator.absorbMutation(BOOK));
  await coordinator.runAsOneRevision(BOOK, () => coordinator.absorbMutation(BOOK));
  check("two patches commit twice", host.calls.filter((c) => c.startsWith("commit")).length, 2);
}

/* -------- a failing commit still releases the transaction -------- */
{
  const coordinator = createRevisionCoordinator({
    settle: () => {},
    commit: () => {
      throw new Error("localStorage is full");
    },
  });
  let raised = "";
  try {
    await coordinator.runAsOneRevision(BOOK, () => coordinator.absorbMutation(BOOK));
  } catch (error) {
    raised = error.message;
  }
  check("a failing commit surfaces", raised, "localStorage is full");
  check("and does not wedge the coordinator", coordinator.activeUnitId(), null);
}

/* -------- async work is awaited before committing -------- */
{
  const host = recorder();
  const coordinator = createRevisionCoordinator(host);
  await coordinator.runAsOneRevision(BOOK, async () => {
    await new Promise((resolve) => setTimeout(resolve, 10));
    coordinator.absorbMutation(BOOK);
    await new Promise((resolve) => setTimeout(resolve, 10));
    coordinator.absorbMutation(BOOK);
  });
  check("async operations are all absorbed before the commit", host.calls, [
    `settle:${BOOK}`,
    `commit:${BOOK}`,
  ]);
}

/* -------- the shared instance refuses without a mounted host -------- */
{
  setRevisionCommitHandler(null);
  let code = "";
  try {
    await getRevisionCoordinator().runAsOneRevision(BOOK, () => undefined);
  } catch (error) {
    code = error.code;
  }
  check("no host means no silent commit", code, "workbook_host_unmounted");

  // And it works once a host registers.
  const host = recorder();
  setRevisionCommitHandler(host);
  await getRevisionCoordinator().runAsOneRevision(BOOK, () =>
    getRevisionCoordinator().absorbMutation(BOOK),
  );
  check("a registered host commits", host.calls, [`settle:${BOOK}`, `commit:${BOOK}`]);
  setRevisionCommitHandler(null);
}

for (const message of failures) console.error(message);

if (failures.length > 0) {
  console.error(`\n${failures.length} of ${checks} revision-coordinator checks failed.`);
  process.exit(1);
}

console.log(`All ${checks} revision-coordinator checks passed.`);
