"""Building the undo for a patch (Phase 9.10.5).

Every applied patch must be reversible, and the inverse has to be built *before*
application — once the cells are overwritten the previous values are gone.

The rules follow what is actually recoverable:

* writing into cells verified blank inverts to clearing them, and needs no
  captured payload at all;
* writing over existing content inverts to restoring it, which means the
  previous values, formulas and formats must be captured first;
* creating a sheet inverts to deleting it, which is why `delete_sheet` exists in
  the registry but is never proposable by a plan — it is reachable only here.

An inverse carries the same guards as the patch it undoes, with before and after
swapped, so applying it later is conflict-checked exactly like any other patch.
"""

from __future__ import annotations

from ..models.workbook import a1_dimensions
from .cells import CellState, blank_range_hash, is_blank_grid, range_hash
from .operations import (
    InlinePayload,
    MAX_INLINE_CELLS,
    PatchOperation,
    PatchOperationType,
)


class InverseNotAvailableError(ValueError):
    """A patch operation cannot be reversed with the information captured."""


def invert_operation(
    operation: PatchOperation,
    *,
    before: tuple[tuple[CellState, ...], ...] | None = None,
) -> PatchOperation:
    """Return the operation that undoes `operation`.

    `before` is the target's captured prior state. It may be omitted only when
    the operation's own `expected_before_hash` already proves the target was
    blank, in which case clearing is a complete undo.
    """

    kind = operation.operation_type
    inverse_id = f"{operation.op_id}__inverse"

    if kind is PatchOperationType.CREATE_SHEET:
        return PatchOperation(
            op_id=inverse_id,
            operation_type=PatchOperationType.DELETE_SHEET,
            worksheet_id=operation.worksheet_id,
            sheet_name=operation.sheet_name,
            inverse_op_id=operation.op_id,
        )

    if kind is PatchOperationType.RENAME_SHEET:
        # The undo restores the name the sheet had, which the compiler records
        # in `range_a1`-free form as the operation's prior name.
        raise InverseNotAvailableError(
            "rename_sheet inverses require the previous name; the compiler "
            "builds them directly rather than deriving them here"
        )

    if kind in _RESTORABLE:
        if operation.range_a1 is None:  # pragma: no cover - model guarantees it
            raise InverseNotAvailableError(
                f"'{kind.value}' has no range to restore"
            )
        if before is None:
            if operation.expected_before_hash != blank_range_hash(operation.range_a1):
                raise InverseNotAvailableError(
                    f"'{kind.value}' overwrites existing content, so its "
                    "previous state must be captured before it is applied"
                )
            return _clear(inverse_id, operation)
        if is_blank_grid(before):
            return _clear(inverse_id, operation)
        return _restore(inverse_id, operation, before)

    raise InverseNotAvailableError(
        f"'{kind.value}' has no defined inverse in patch protocol v1"
    )


def _clear(inverse_id: str, operation: PatchOperation) -> PatchOperation:
    assert operation.range_a1 is not None
    rows, columns = a1_dimensions(operation.range_a1)
    return PatchOperation(
        op_id=inverse_id,
        operation_type=PatchOperationType.CLEAR_RANGE,
        worksheet_id=operation.worksheet_id,
        range_a1=operation.range_a1,
        # Guards swap: the inverse expects what the patch produced, and leaves
        # what the patch found.
        expected_before_hash=operation.expected_after_hash,
        expected_after_hash=operation.expected_before_hash,
        affected_cells=rows * columns,
        inverse_op_id=operation.op_id,
    )


def _restore(
    inverse_id: str,
    operation: PatchOperation,
    before: tuple[tuple[CellState, ...], ...],
) -> PatchOperation:
    assert operation.range_a1 is not None
    rows, columns = a1_dimensions(operation.range_a1)
    if rows * columns > MAX_INLINE_CELLS:
        raise InverseNotAvailableError(
            "a destructive edit larger than the inline limit needs a stored "
            "inverse payload; the compiler uploads it rather than inlining it"
        )
    return PatchOperation(
        op_id=inverse_id,
        operation_type=PatchOperationType.WRITE_RANGE,
        worksheet_id=operation.worksheet_id,
        range_a1=operation.range_a1,
        expected_before_hash=operation.expected_after_hash,
        expected_after_hash=range_hash(operation.range_a1, before),
        payload=InlinePayload(cells=before),
        affected_cells=rows * columns,
        inverse_op_id=operation.op_id,
    )


_RESTORABLE = frozenset(
    {
        PatchOperationType.WRITE_RANGE,
        PatchOperationType.CLEAR_RANGE,
        PatchOperationType.SET_FORMULA,
        PatchOperationType.FILL_FORMULA,
        PatchOperationType.SET_NUMBER_FORMAT,
    }
)


def build_inverse(
    operations: tuple[PatchOperation, ...],
    *,
    captured: dict[str, tuple[tuple[CellState, ...], ...]] | None = None,
) -> tuple[PatchOperation, ...]:
    """Return the inverse of a whole patch, in reverse application order."""

    prior = captured or {}
    inverted = [
        invert_operation(operation, before=prior.get(operation.op_id))
        for operation in operations
    ]
    # Undo runs backwards: the last change made is the first one taken back.
    return tuple(reversed(inverted))


__all__ = [
    "InverseNotAvailableError",
    "build_inverse",
    "invert_operation",
]
