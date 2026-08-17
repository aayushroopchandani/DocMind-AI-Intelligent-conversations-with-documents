/**
 * Golden fixtures shared by the Python and TypeScript cell-hash implementations
 * (Phase 9.10.4 — "both must share golden fixtures").
 *
 * The expected digests are NOT hand-written. They are produced by the Python
 * implementation and pasted here, so this file is the assertion that the two
 * agree rather than a second opinion about what the answer should be.
 *
 * Regenerate with:
 *   backend/.venv/bin/python -m scripts.data_analysis_agent.runtime.patches.fixtures
 */

import type { CellState } from "./cell-hash";

export interface CellHashFixture {
  name: string;
  cell: CellState;
  expected: string;
}

export interface RangeHashFixture {
  name: string;
  rangeA1: string;
  cells: CellState[][];
  expected: string;
}

function cell(overrides: Partial<CellState> = {}): CellState {
  return {
    value: null,
    formula: null,
    cellType: null,
    numberFormat: null,
    merged: false,
    protected: false,
    ...overrides,
  };
}

export const CELL_HASH_FIXTURES: CellHashFixture[] = [
  {
    name: "blank",
    cell: cell(),
    expected: "dc785d1932c3aaf9c5f3030688592ca646fd6b70eabf41b51c64932e91feb0d9",
  },
  {
    name: "text",
    cell: cell({ value: "North", cellType: "string" }),
    expected: "f085c47376bba802296d362c142a7ff1ec49fb2e3faa68edaea887cbf51a3f51",
  },
  {
    name: "integer",
    cell: cell({ value: 42, cellType: "number" }),
    expected: "f9505af71e146f9625f27a951af65fda2619bb3618823ce2bbbad200473850c3",
  },
  {
    name: "float with binary representation",
    cell: cell({ value: 1.005, cellType: "number", numberFormat: "0.00" }),
    expected: "d4820bd9b06fb77ea603c8f9d8f8fc53fddb6ff40e0660189712fd374bf7fa66",
  },
  {
    name: "negative zero normalizes",
    cell: cell({ value: -0, cellType: "number" }),
    expected: "d03123f9515afd16ac9fd1c166a4489d5932ac51c3a8974c2f2a0a934452f7fd",
  },
  {
    name: "boolean",
    cell: cell({ value: true, cellType: "boolean" }),
    expected: "3f78ad2a45f0c20ce36dea3e336fe86f3a1c0cdf225e4ca27161c523837b2f37",
  },
  {
    name: "formula",
    cell: cell({ value: 0.6, formula: "=(C2-D2)/C2", cellType: "number", numberFormat: "0.00%" }),
    expected: "c9522fd01581976cd54666d514a9e0fa9935bcd5ccdb5e6cad431fd4f293ea32",
  },
  {
    name: "merged and protected",
    cell: cell({ value: "Header", cellType: "string", merged: true, protected: true }),
    expected: "ad4f0b8abef8fd33e702a92f7b4ac012e99b80384a2d50e7b829c2f536292980",
  },
  {
    name: "empty string is not null",
    cell: cell({ value: "", cellType: "string" }),
    expected: "f1bee0f30310e76e1860e913ad389254d7e97d5e3ea6078eb326955dfb0e1305",
  },
];

export const RANGE_HASH_FIXTURES: RangeHashFixture[] = [
  {
    name: "blank 2x2",
    rangeA1: "Sheet1!A1:B2",
    cells: [
      [cell(), cell()],
      [cell(), cell()],
    ],
    expected: "13d5c114e3e573debaa713867ce8d3aa414a2d0be0d0c759232b593e9aca73e3",
  },
  {
    name: "mixed 2x2",
    rangeA1: "Sheet1!A1:B2",
    cells: [
      [cell({ value: "Region", cellType: "string" }), cell({ value: "Revenue", cellType: "string" })],
      [cell({ value: "North", cellType: "string" }), cell({ value: 1234.5, cellType: "number" })],
    ],
    expected: "6ed7fe022a9c245b8256062989ef23ca56f43c9982431fd221a5aff2dcd0df40",
  },
  {
    name: "same content at a different address",
    rangeA1: "Sheet1!D1:E2",
    cells: [
      [cell({ value: "Region", cellType: "string" }), cell({ value: "Revenue", cellType: "string" })],
      [cell({ value: "North", cellType: "string" }), cell({ value: 1234.5, cellType: "number" })],
    ],
    expected: "4eadb024c5a603893e1390b633604d91634558acd31d91dc620755bcb1761d98",
  },
];
