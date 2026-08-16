"""Entry point for the bounded native execution child process.

Invoked as:

    python -m scripts.data_analysis_agent.runtime.execution.native.worker_main \
        <job.json> <result.json>

The child reads a validated recipe, runs it, and writes a result manifest. It
never touches MongoDB, Cloudinary or any LLM client — the parent constructs a
scrubbed environment and passes file paths only, so there is nothing in scope
for this process to leak even if the engine misbehaves.

Files are used instead of stdio so that any engine chatter on stdout/stderr
cannot corrupt the manifest.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: worker_main <job.json> <result.json>", file=sys.stderr)
        return 2

    # Imported here so an argument error is reported before the (slower)
    # engine import, and so a failure to import Polars is reported as a
    # process failure the parent can classify.
    from ..contracts import NativeRecipe
    from .engine import execute_recipe

    job_path = Path(argv[1])
    result_path = Path(argv[2])
    recipe = NativeRecipe.model_validate_json(job_path.read_text(encoding="utf-8"))
    output_path = result_path.with_suffix(".arrow")
    result = execute_recipe(recipe, output_path=output_path)
    result_path.write_text(result.model_dump_json(), encoding="utf-8")
    return 0 if result.succeeded else 1


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main(sys.argv))
