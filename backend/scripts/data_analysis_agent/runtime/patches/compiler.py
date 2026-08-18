"""Compiling one reviewable patch from one finished result (Phase 9.11.1, step 6).

By the time this runs, three things are already settled: the result exists and
is immutable, the workbook context has been captured and hashed, and placement
has chosen exactly one rectangle. The compiler's job is to turn those into the
declarative document a user approves and an adapter applies — and to make sure
every hash in it was computed from the same single pass over the same data.

Nothing here decides anything. Placement decided where; the plan decided what.
If the compiler had a choice to make, that choice would not have been reviewed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ..formulas.compiler import FormulaPlacement, compile_formula
from ..formulas.expressions import FormulaSpec
from ..models.plans import PlanColumn
from ..models.workbook import Rect, WorkbookCellType, a1_from_bounds, column_label
from .cells import (
    CELL_HASH_VERSION,
    CellState,
    RangeHashBuilder,
    blank_range_hash,
)
from .envelope import (
    PATCH_COMPILER_VERSION,
    PatchStatus,
    WorkbookGuard,
    WorkbookPatch,
    compute_patch_hash,
    summarize_impact,
)
from .grid import MaterializedGrid, ResultGrid
from .inverse import invert_operation
from .operations import PatchOperation, PatchOperationType
from .payloads import PayloadWriter, build_payload


if TYPE_CHECKING:  # A runtime import would close the placement/patches cycle.
    from ..placement.selection import PlacementDecision


WRITE_OP_ID = "write_result"
CREATE_SHEET_OP_ID = "create_target_sheet"


class PatchCompilationError(RuntimeError):
    """The result cannot be expressed as a patch under the given limits."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class PatchIdentity:
    """Who owns this patch and what it is derived from."""

    user_id: str
    workspace_id: str
    run_id: str
    plan_id: str
    plan_hash: str
    execution_id: str
    workbook_id: str
    base_workbook_revision: int
    patch_id: str
    idempotency_key: str
    patch_revision: int = 1


@dataclass(frozen=True, slots=True)
class CompiledPatch:
    """The patch, plus what compiling it cost."""

    patch: WorkbookPatch
    after_hash: str
    payload_bytes: int
    #: The first rows of the written grid, captured while the payload was being
    #: built. The proposal preview is rendered from these rather than by
    #: fetching a chunk back out of storage.
    preview_head: tuple[tuple[CellState, ...], ...] = ()

    @property
    def affected_cells(self) -> int:
        return self.patch.affected_cells


async def compile_patch(
    *,
    identity: PatchIdentity,
    decision: PlacementDecision,
    grid: ResultGrid,
    maximum_affected_cells: int,
    source_guard: WorkbookGuard | None = None,
    writer: PayloadWriter | None = None,
    formulas: tuple[FormulaSpec, ...] = (),
    expires_at: datetime | None = None,
) -> CompiledPatch:
    """Return the patch that writes `grid` where `decision` chose.

    Formula columns must already be present in `grid.columns` and emitted blank
    by the grid: the value write lays down the whole rectangle, and a
    `fill_formula` operation then replaces those columns. That ordering is what
    lets every guard hash be computed without a second pass over the result.
    """

    if grid.cell_count != decision.target_rect.cell_count:
        raise PatchCompilationError(
            "patch_target_size_mismatch",
            (
                f"the result is {grid.rows}x{grid.width} but the target "
                f"{decision.target_range_a1} holds "
                f"{decision.target_rect.cell_count} cells"
            ),
        )
    if grid.cell_count > maximum_affected_cells:
        raise PatchCompilationError(
            "patch_too_large",
            (
                f"writing {grid.cell_count} cells exceeds the configured "
                f"limit of {maximum_affected_cells}"
            ),
        )

    built = await build_payload(
        grid,
        range_a1=decision.target_range_a1,
        rows=grid.rows,
        columns=grid.width,
        writer=writer,
    )

    operations: list[PatchOperation] = []
    if decision.creates_sheet:
        operations.append(
            PatchOperation(
                op_id=CREATE_SHEET_OP_ID,
                operation_type=PatchOperationType.CREATE_SHEET,
                worksheet_id=decision.worksheet_id,
                sheet_name=decision.worksheet_name,
            )
        )
    write = PatchOperation(
        op_id=WRITE_OP_ID,
        operation_type=PatchOperationType.WRITE_RANGE,
        depends_on=(CREATE_SHEET_OP_ID,) if decision.creates_sheet else (),
        worksheet_id=decision.worksheet_id,
        range_a1=decision.target_range_a1,
        expected_before_hash=decision.before_hash,
        expected_after_hash=built.after_hash,
        payload=built.payload,
        affected_cells=built.cell_count,
    )
    operations.append(write)
    operations.extend(
        _formula_operations(
            formulas,
            decision=decision,
            columns=grid.columns,
            data_rows=grid.record_count,
            header_rows=1 if grid.include_header else 0,
        )
    )

    inverse = await _build_inverse(
        operations,
        decision=decision,
        writer=writer,
    )

    impact = summarize_impact(tuple(operations)).model_copy(
        update={
            "overwrites_existing_values": decision.overwrites,
            "overwrites_existing_formulas": (
                decision.overwrites and _contains_formula(decision.before_cells)
            ),
        }
    )
    if impact.total_cells > maximum_affected_cells:
        raise PatchCompilationError(
            "patch_too_large",
            (
                f"the patch affects {impact.total_cells} cells, above the "
                f"configured limit of {maximum_affected_cells}"
            ),
        )

    target_guards: tuple[WorkbookGuard, ...] = ()
    if not decision.creates_sheet:
        # A sheet that does not exist yet cannot be guarded; the create
        # operation establishes it empty inside the same patch.
        target_guards = (
            WorkbookGuard(
                worksheet_id=decision.worksheet_id,
                range_a1=decision.target_range_a1,
                expected_hash=decision.before_hash,
                role="target",
            ),
        )

    draft = WorkbookPatch(
        patch_id=identity.patch_id,
        patch_revision=identity.patch_revision,
        patch_hash="0" * 64,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        run_id=identity.run_id,
        plan_id=identity.plan_id,
        plan_hash=identity.plan_hash,
        execution_id=identity.execution_id,
        workbook_id=identity.workbook_id,
        base_workbook_revision=identity.base_workbook_revision,
        source_guards=(source_guard,) if source_guard is not None else (),
        target_guards=target_guards,
        operations=tuple(operations),
        inverse_operations=inverse,
        impact=impact,
        maximum_affected_cells=maximum_affected_cells,
        idempotency_key=identity.idempotency_key,
        compiler_version=PATCH_COMPILER_VERSION,
        cell_hash_version=CELL_HASH_VERSION,
        status=PatchStatus.DRAFT,
        expires_at=expires_at,
    )
    # The hash covers everything except itself, so it is computed from the
    # finished draft and written back without revalidation.
    patch = draft.model_copy(update={"patch_hash": compute_patch_hash(draft)})
    return CompiledPatch(
        patch=patch,
        after_hash=built.after_hash,
        payload_bytes=built.byte_count,
        preview_head=built.head,
    )


def formula_placement(
    decision: PlacementDecision,
    columns: tuple[PlanColumn, ...],
    *,
    data_rows: int,
    header_rows: int = 1,
) -> FormulaPlacement:
    """Return where each result column landed, for the Phase 9.7 compiler.

    `FormulaPlacement` documents that it is "supplied by the placement step,
    never guessed by the planner" — this is that step.
    """

    rect = decision.target_rect
    first_data_row = rect.first_row + header_rows
    return FormulaPlacement(
        columns={
            column.key: column_label(rect.first_column + index)
            for index, column in enumerate(columns)
        },
        first_data_row=first_data_row,
        last_data_row=max(first_data_row, first_data_row + data_rows - 1),
    )


def _formula_operations(
    formulas: tuple[FormulaSpec, ...],
    *,
    decision: PlacementDecision,
    columns: tuple[PlanColumn, ...],
    data_rows: int,
    header_rows: int,
) -> tuple[PatchOperation, ...]:
    if not formulas:
        return ()
    if data_rows < 1:
        raise PatchCompilationError(
            "formula_without_rows",
            "a filled formula needs at least one data row",
        )
    placement = formula_placement(
        decision,
        columns,
        data_rows=data_rows,
        header_rows=header_rows,
    )
    index_by_key = {column.key: index for index, column in enumerate(columns)}
    rect = decision.target_rect
    operations: list[PatchOperation] = []
    for spec in formulas:
        if spec.output_column_key not in index_by_key:
            raise PatchCompilationError(
                "formula_column_not_placed",
                (
                    f"formula targets column '{spec.output_column_key}', which "
                    "is not part of the result"
                ),
            )
        compiled = compile_formula(spec, placement)
        column_index = rect.first_column + index_by_key[spec.output_column_key]
        target = Rect(
            first_row=compiled.seed_row,
            first_column=column_index,
            last_row=compiled.fill_through_row,
            last_column=column_index,
        )
        range_a1 = a1_from_bounds(
            target.first_row,
            target.first_column,
            target.last_row,
            target.last_column,
            sheet_name=decision.worksheet_name,
        )
        operations.append(
            PatchOperation(
                op_id=f"formula_{spec.output_column_key}",
                operation_type=(
                    PatchOperationType.FILL_FORMULA
                    if compiled.fill_row_count > 1
                    else PatchOperationType.SET_FORMULA
                ),
                depends_on=(WRITE_OP_ID,),
                worksheet_id=decision.worksheet_id,
                range_a1=range_a1,
                # The value write left this column blank, so the state before
                # the fill is known exactly without re-reading the result.
                expected_before_hash=blank_range_hash(range_a1),
                expected_after_hash=_formula_range_hash(
                    range_a1,
                    formula=compiled.formula,
                    number_format=compiled.number_format,
                    rows=target.rows,
                ),
                formula=compiled.formula,
                number_format=compiled.number_format,
                affected_cells=target.cell_count,
            )
        )
    return tuple(operations)


def _formula_range_hash(
    range_a1: str,
    *,
    formula: str,
    number_format: str | None,
    rows: int,
) -> str:
    """Hash a single-column range of identical filled formulas.

    A fill writes the same seed formula down the column, so the whole rectangle
    is one repeated cell and costs one cell of memory to hash.
    """

    cell = CellState(
        formula=formula,
        cell_type=WorkbookCellType.FORMULA,
        number_format=number_format,
    )
    builder = RangeHashBuilder(range_a1)
    for _ in range(rows):
        builder.add_row((cell,))
    return builder.digest()


async def _build_inverse(
    operations: list[PatchOperation],
    *,
    decision: PlacementDecision,
    writer: PayloadWriter | None,
) -> tuple[PatchOperation, ...]:
    """Return the undo for `operations`, in reverse application order.

    Only the sheet creation and the value write are inverted. A formula fill
    lands inside the rectangle the write covers, so the write's own inverse —
    clear it, or restore what was there — already takes the formulas back with
    it. A second, narrower clear would only inflate the undo's impact.
    """

    inverse: list[PatchOperation] = []
    for operation in operations:
        if operation.operation_type is PatchOperationType.CREATE_SHEET:
            inverse.append(invert_operation(operation))
        elif operation.op_id == WRITE_OP_ID:
            inverse.append(
                await _invert_write(operation, decision=decision, writer=writer)
            )
    return tuple(reversed(inverse))


async def _invert_write(
    operation: PatchOperation,
    *,
    decision: PlacementDecision,
    writer: PayloadWriter | None,
) -> PatchOperation:
    before = decision.before_cells
    if before is None:
        # The target was proven empty, so clearing it is a complete undo and no
        # previous state has to be stored at all.
        return invert_operation(operation)
    assert operation.range_a1 is not None
    grid = MaterializedGrid(cells=before)
    restored = await build_payload(
        grid,
        range_a1=operation.range_a1,
        rows=grid.rows,
        columns=grid.width,
        writer=writer,
    )
    return PatchOperation(
        op_id=f"{operation.op_id}__inverse",
        operation_type=PatchOperationType.WRITE_RANGE,
        worksheet_id=operation.worksheet_id,
        range_a1=operation.range_a1,
        # Guards swap: the undo expects what the patch produced and leaves what
        # the patch found.
        expected_before_hash=operation.expected_after_hash,
        expected_after_hash=restored.after_hash,
        payload=restored.payload,
        affected_cells=restored.cell_count,
        inverse_op_id=operation.op_id,
    )


def _contains_formula(
    cells: tuple[tuple[CellState, ...], ...] | None,
) -> bool:
    if cells is None:
        return False
    return any(cell.formula for row in cells for cell in row)


__all__ = [
    "CREATE_SHEET_OP_ID",
    "WRITE_OP_ID",
    "CompiledPatch",
    "PatchCompilationError",
    "PatchIdentity",
    "compile_patch",
    "formula_placement",
]
