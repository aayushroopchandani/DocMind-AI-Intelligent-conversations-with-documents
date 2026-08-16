"""Operations that produce a table instead of transforming one.

`generate_dataset` is the only zero-input operation in the plan contract. It is
a barrier because it produces real data rather than a lazy query, and it reads
no frames at all — the engine passes it an empty mapping.
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from ....models.plans import GenerateDatasetStep
from ..generation import GenerationError, generate_dataset
from .base import NativeExecutionSemanticError, Operation, register


def _apply_generate(
    step: GenerateDatasetStep,
    frames: Mapping[str, pl.DataFrame],
) -> pl.DataFrame:
    try:
        return generate_dataset(step.generation, columns=step.expected_schema)
    except GenerationError as error:
        # Surfaces as a typed semantic violation like every other declared
        # policy the data failed to satisfy.
        raise NativeExecutionSemanticError(
            f"step '{step.step_id}' could not generate its dataset: {error}"
        ) from error


register(Operation(kind="generate_dataset", apply=_apply_generate, barrier=True))


__all__: list[str] = []
