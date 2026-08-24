/**
 * Check how a published result sample is presented.
 *
 * The sample arrives already bounded and redacted, so nothing here decides what
 * a user may see — only how it reads. That still matters: a currency column
 * rendered with six decimals, a withheld column rendered as its raw payload, or
 * a truncated result described as complete would each be wrong in a way no type
 * checker catches.
 *
 * The fixture mirrors what `build_preview` in
 * `backend/.../execution/results/previews.py` actually emits.
 *
 * Run with:  npm run verify:result-preview
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, cpSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const SCHEMA = [
  { key: "region", label: "Region", data_type: "string", unit: null, nullable: false },
  { key: "orders", label: "Orders", data_type: "integer", unit: null, nullable: false },
  { key: "revenue", label: "Revenue", data_type: "currency", unit: "USD", nullable: false },
  { key: "margin", label: "Margin", data_type: "percentage", unit: null, nullable: true },
  { key: "opened_on", label: "Opened", data_type: "date", unit: null, nullable: true },
  { key: "contact", label: "Contact", data_type: "string", unit: null, nullable: true },
];

const PREVIEW = {
  row_count: 3420,
  preview_row_count: 3,
  truncated: true,
  privacy_mode: "standard",
  redacted_column_keys: ["contact"],
  columns: ["region", "orders", "revenue", "margin", "opened_on", "contact"],
  rows: [
    { region: "North", orders: 1204, revenue: 98211.5, margin: 12.5, opened_on: "2026-01-15T00:00:00", contact: "[redacted]" },
    // Deliberately carries a real value in a withheld column: the backend
    // redacts before publishing, and the browser must withhold anyway rather
    // than trust that it did.
    { region: "South", orders: 87, revenue: 1200, margin: null, opened_on: "2026-02-01T00:00:00", contact: "ana@example.com" },
    // A sparse row: the publisher omits a key when a column had no value.
    { region: "", orders: 0, revenue: 0, opened_on: null, contact: "[redacted]" },
  ],
};

/* ------------------------------------------------------------------ */

const workspace = mkdtempSync(join(tmpdir(), "result-preview-"));
cpSync("lib/data-analysis/execution/result-preview.ts", join(workspace, "result-preview.ts"));
// The module formats through the shared, locale-pinned helpers.
cpSync("lib/data-analysis/format.ts", join(workspace, "format.ts"));
writeFileSync(
  join(workspace, "execution-types.ts"),
  `export type PlanDataType =
  | "string" | "integer" | "decimal" | "currency" | "percentage"
  | "date" | "datetime" | "boolean" | "category";
export interface PlanColumn {
  key: string; label: string; data_type: PlanDataType;
  unit: string | null; nullable: boolean;
}
export interface ResultPreview {
  row_count: number; preview_row_count: number; truncated: boolean;
  privacy_mode: string; redacted_column_keys: string[]; columns: string[];
  rows: Array<Record<string, string | number | boolean | null>>;
}
`,
  "utf8",
);

const source = join(workspace, "result-preview.ts");
// tsc runs here without the app's tsconfig paths, so aliases become relative.
writeFileSync(
  source,
  readFileSync(source, "utf8")
    .replace("@/lib/data-analysis/execution/execution-types", "./execution-types")
    .replace("@/lib/data-analysis/format", "./format"),
  "utf8",
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
    source,
    join(workspace, "execution-types.ts"),
    join(workspace, "format.ts"),
  ],
  { stdio: "inherit" },
);

// tsc emits extensionless relative imports; Node's ESM loader requires them.
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

const { buildPreviewTable, columnHeading, describeBytes, describeResultShape } =
  await import(pathToFileURL(join(workspace, "out", "result-preview.js")).href);

const failures = [];
let checks = 0;

function check(label, actual, expected) {
  checks += 1;
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) failures.push(`${label}\n  expected: ${e}\n  actual:   ${a}`);
}

const table = buildPreviewTable(PREVIEW, SCHEMA);
const cellAt = (row, key) =>
  table.rows[row][table.columns.findIndex((column) => column.key === key)];

/* Headers read as labels, with the unit stated once. */
check("labels", table.columns.map((c) => c.label), [
  "Region", "Orders", "Revenue", "Margin", "Opened", "Contact",
]);
check("revenue heading carries its unit", columnHeading(table.columns[2]), "Revenue (USD)");
check("plain heading has no unit", columnHeading(table.columns[0]), "Region");

/* Numbers align right; text aligns left. */
check("alignment", table.columns.map((c) => c.align), [
  "start", "end", "end", "end", "start", "start",
]);

/* Type-directed formatting. */
check("integer has no decimals", cellAt(0, "orders").text, "1,204");
check("currency keeps two", cellAt(0, "revenue").text, "98,211.50");
check("percentage is suffixed", cellAt(0, "margin").text, "12.5%");
check("units stay out of cells", cellAt(0, "revenue").text.includes("USD"), false);

/* Absent values read as absent, not as zero or blank. */
check("null is em-dashed", cellAt(1, "margin").text, "—");
check("null is muted", cellAt(1, "margin").muted, true);
check("a missing key is em-dashed", cellAt(2, "margin").text, "—");
check("empty text is em-dashed", cellAt(2, "region").text, "—");
check("a real zero is not muted", cellAt(2, "orders").muted, false);
check("a real zero shows as zero", cellAt(2, "orders").text, "0");

/* Withheld columns stay withheld, and say so. */
check("redacted column flagged", table.columns[5].redacted, true);
check("redacted cell text", cellAt(0, "contact").text, "[redacted]");
check("redacted cell muted", cellAt(0, "contact").muted, true);
check("a withheld column never renders a value that slipped through",
  cellAt(1, "contact").text, "[redacted]");
checks += 1;
if (JSON.stringify(table.rows).includes("ana@example.com")) {
  failures.push("a value in a withheld column reached the rendered table");
}
check("redacted count", table.redactedColumnCount, 1);

/* Dates are reformatted, not echoed as ISO. */
checks += 1;
if (cellAt(0, "opened_on").text.includes("T00:00:00")) {
  failures.push("a date cell was echoed as a raw ISO timestamp");
}

/* The summary must not imply the whole result is on screen. */
check("truncated summary", table.summary, "Showing 3 of 3,420 rows");
check("shownRows", table.shownRows, 3);
check("totalRows", table.totalRows, 3420);

const whole = buildPreviewTable(
  { ...PREVIEW, row_count: 3, truncated: false },
  SCHEMA,
);
check("complete summary", whole.summary, "3 rows");
check("empty summary", buildPreviewTable({ ...PREVIEW, row_count: 0, preview_row_count: 0, truncated: false, rows: [] }, SCHEMA).summary, "No rows");

/* Without a schema the table still renders, using raw keys. */
const bare = buildPreviewTable(PREVIEW);
check("bare labels fall back to keys", bare.columns.map((c) => c.label), PREVIEW.columns);
check("bare alignment is textual", new Set(bare.columns.map((c) => c.align)).size, 1);
check("bare redaction still applies", bare.columns[5].redacted, true);
check("bare numbers still render", cellAtOf(bare, 0, "orders").text, "1,204");

function cellAtOf(t, row, key) {
  return t.rows[row][t.columns.findIndex((c) => c.key === key)];
}

/* Shape and size helpers. */
check("shape", describeResultShape(24, 4), "24 rows × 4 columns");
check("singular shape", describeResultShape(1, 1), "1 row × 1 column");
check("shape without columns", describeResultShape(24, null), "24 rows");
check("no shape without rows", describeResultShape(null, 4), null);
check("bytes under a kilobyte", describeBytes(512), "512 B");
check("bytes in kilobytes", describeBytes(4308), "4.2 KB");
check("bytes in megabytes", describeBytes(5 * 1024 * 1024), "5.0 MB");
check("no size when unknown", describeBytes(null), null);

/* Every row must be as wide as the header. */
checks += 1;
if (table.rows.some((row) => row.length !== table.columns.length)) {
  failures.push("a row does not line up with the header");
}

for (const message of failures) console.error(message);

if (failures.length > 0) {
  console.error(`\n${failures.length} of ${checks} result-preview checks failed.`);
  process.exit(1);
}

console.log(`All ${checks} result-preview checks passed.`);
