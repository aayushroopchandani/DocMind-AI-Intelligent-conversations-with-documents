/**
 * Pin the Univer version the patch adapter was verified against.
 *
 * The adapter depends on undo/redo behaviour that is not a stable public API:
 * it collapses the entries Univer's own commands push, and it does so because
 * `__tempBatchingUndoRedo` merges undo mutations in the wrong order. None of
 * that is guaranteed across versions.
 *
 * So an upgrade must not be silent. This fails when the installed version
 * moves away from the one recorded in the adapter, and the fix is deliberate:
 * run the contract suite at `/dev/univer-contract` against the new version,
 * then update `VERIFIED_UNIVER_VERSION`.
 *
 * Run with:  npm run verify:univer-version
 */

import { existsSync, readdirSync, readFileSync } from "node:fs";

const ADAPTER = "lib/data-analysis/patches/univer-patch-adapter.ts";
const CONTRACT_PAGE = "/dev/univer-contract";

function fail(message) {
  console.error(message);
  process.exit(1);
}

const adapterSource = readFileSync(ADAPTER, "utf8");
const declared = /VERIFIED_UNIVER_VERSION\s*=\s*"([^"]+)"/.exec(adapterSource);
if (!declared) {
  fail(`Could not find VERIFIED_UNIVER_VERSION in ${ADAPTER}.`);
}

const manifest = JSON.parse(readFileSync("package.json", "utf8"));
const pinned = manifest.dependencies?.["@univerjs/core"];
if (!pinned) fail("package.json does not depend on @univerjs/core.");

// The dependency is pinned exactly (no ^ or ~) so the adapter's guarantee is
// about one version rather than a range. Flag a range as its own problem.
if (!/^\d+\.\d+\.\d+$/.test(pinned)) {
  fail(
    `@univerjs/core is declared as "${pinned}". The patch adapter relies on ` +
      "version-specific undo behaviour, so this dependency must be pinned to " +
      "an exact version.",
  );
}

const installed = JSON.parse(
  readFileSync("node_modules/@univerjs/core/package.json", "utf8"),
).version;

const problems = [];
if (declared[1] !== pinned) {
  problems.push(
    `  adapter says ${declared[1]}, package.json pins ${pinned}`,
  );
}
if (installed !== pinned) {
  problems.push(`  package.json pins ${pinned}, node_modules has ${installed}`);
}

if (problems.length > 0) {
  fail(
    "Univer version mismatch:\n" +
      problems.join("\n") +
      `\n\nThe patch adapter's one-undo guarantee was verified against ` +
      `${declared[1]}. Run the contract suite at ${CONTRACT_PAGE} against the ` +
      `installed version, then update VERIFIED_UNIVER_VERSION in ${ADAPTER}.`,
  );
}

/*
 * The adapter must stay the only place that reaches for undo/redo internals.
 *
 * 9.13's acceptance criteria require all Univer-specific behaviour to live
 * behind one adapter. That is easy to state and easy to erode — one component
 * reaching for `IUndoRedoService` to "just check something" is all it takes —
 * so it is checked rather than trusted.
 */
const OWNED_SYMBOLS = [
  "IUndoRedoService",
  "pitchTopUndoElement",
  "popUndoToRedo",
  "pushUndoRedo",
  "__tempBatchingUndoRedo",
  "__getInjector",
];

/** Files allowed to name them, and why. */
const ALLOWED = new Map([
  [ADAPTER, "owns the mechanism"],
  ["scripts/verify-univer-version.mjs", "this check"],
  // Constructing the adapter needs the injector, and only these two do that.
  ["components/data-analysis/dev/univer-contract-runner.tsx", "boots the contract suite"],
  ["components/data-analysis/workspace/spreadsheet/univer-host.tsx", "owns the app's instance"],
]);

const SEARCH_ROOTS = ["lib", "components", "app", "scripts"];
const SOURCE = /\.(ts|tsx|mjs)$/;

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = `${dir}/${entry.name}`;
    if (entry.isDirectory()) out.push(...walk(path));
    else if (SOURCE.test(entry.name)) out.push(path);
  }
  return out;
}

const leaks = [];
for (const root of SEARCH_ROOTS) {
  if (!existsSync(root)) continue;
  for (const file of walk(root)) {
    if (ALLOWED.has(file)) continue;
    const source = readFileSync(file, "utf8");
    const found = OWNED_SYMBOLS.filter((symbol) => source.includes(symbol));
    if (found.length > 0) leaks.push(`  ${file}: ${found.join(", ")}`);
  }
}

if (leaks.length > 0) {
  fail(
    "Univer undo/redo internals are referenced outside the patch adapter:\n" +
      leaks.join("\n") +
      `\n\nMove the behaviour into ${ADAPTER}, which is the one file the ` +
      "contract suite verifies after an upgrade.",
  );
}

console.log(
  `Univer ${installed} matches the version the patch adapter was verified ` +
    `against, and is the only file naming its ${OWNED_SYMBOLS.length} ` +
    "undo/redo entry points.",
);
