"""Seeded synthetic-data generation (Phase 9.6).

Kept in its own package because generation is not a table transformation: it has
no input frame, draws from a pinned random stream, and enforces its own
constraints. The engine treats it as a source rather than a stage.
"""

from .generator import (
    MAXIMUM_CONSTRAINT_ATTEMPTS,
    GenerationError,
    GenerationLimits,
    generate_dataset,
)
from .randomness import RANDOM_ALGORITHM, column_seed, column_stream

__all__ = [
    "MAXIMUM_CONSTRAINT_ATTEMPTS",
    "RANDOM_ALGORITHM",
    "GenerationError",
    "GenerationLimits",
    "column_seed",
    "column_stream",
    "generate_dataset",
]
