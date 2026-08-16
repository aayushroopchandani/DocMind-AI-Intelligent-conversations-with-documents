"""Pinned randomness for seeded generation (Phase 9.6.2).

Two decisions make a generated dataset reproducible, and both are made here.

*Per-column seeds.* A column's values are drawn from a stream seeded by
`SHA-256(global_seed, generator_version, column_key)`, not by position. Adding a
column, removing one, or reordering them therefore leaves every other column
byte-identical — which is the property 9.6.2 asks for and the one a positional
seed silently breaks.

*A pinned algorithm.* The stream is NumPy's PCG64, chosen because its output is
documented as stable for a given seed across NumPy versions. Nothing here calls
a module-level `random.*` function, whose stream is a global shared with the
rest of the process.
"""

from __future__ import annotations

import hashlib

import numpy as np


RANDOM_ALGORITHM = "pcg64"
"""Recorded alongside the generator version so a future change is visible."""

_SEED_BITS = 128


def column_seed(
    *,
    global_seed: int,
    generator_version: str,
    column_key: str,
) -> int:
    """Return the independent seed for one column."""

    digest = hashlib.sha256()
    digest.update(str(global_seed).encode("ascii"))
    digest.update(b"\x1e")
    digest.update(generator_version.encode("ascii"))
    digest.update(b"\x1e")
    digest.update(column_key.encode("utf-8"))
    return int.from_bytes(digest.digest()[: _SEED_BITS // 8], "big")


def column_stream(
    *,
    global_seed: int,
    generator_version: str,
    column_key: str,
    attempt: int = 0,
) -> np.random.Generator:
    """Return the pinned generator for one column.

    `attempt` advances the stream deterministically when a constraint retry is
    needed, so a retry is reproducible rather than merely different.
    """

    seed = column_seed(
        global_seed=global_seed,
        generator_version=generator_version,
        column_key=column_key,
    )
    return np.random.Generator(np.random.PCG64(seed + attempt))


__all__ = ["RANDOM_ALGORITHM", "column_seed", "column_stream"]
