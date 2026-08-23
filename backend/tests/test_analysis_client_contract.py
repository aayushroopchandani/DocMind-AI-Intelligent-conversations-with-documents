"""The browser's mirrored types, pinned against what this API actually sends.

`analysis-types.ts` and `execution/execution-types.ts` are hand-written mirrors
of Pydantic response models, in another language, in another directory. Nothing
in either toolchain notices when they fall behind — and they had: the run type
was missing fifteen fields this app returns, including every patch field, by the
time this test was written. A missing field is not a compile error in
TypeScript, it is a silently `undefined` value rendered as a blank.

So this reads the TypeScript and compares it to the OpenAPI schema, which is
generated from the models themselves. There is no third list to maintain: the
Pydantic model is the source, the interface is the mirror, and this asserts
they agree in both directions.

The parse deliberately only recognises top-level fields at two-space indent.
The mirrored interfaces are written without inline nested objects for exactly
that reason, and a reformat that breaks the parse fails loudly here rather than
quietly checking nothing.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import main


FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "my-app"
ANALYSIS_TYPES = FRONTEND / "lib" / "data-analysis" / "analysis-types.ts"
EXECUTION_TYPES = (
    FRONTEND / "lib" / "data-analysis" / "execution" / "execution-types.ts"
)

#: (TypeScript file, interface, OpenAPI schema) triples that must agree.
MIRRORED_TYPES = (
    (ANALYSIS_TYPES, "AnalysisRun", "AnalysisRunView"),
    (EXECUTION_TYPES, "ExecutionView", "ExecutionView"),
    (EXECUTION_TYPES, "ExecutionStageView", "ExecutionStageView"),
    (EXECUTION_TYPES, "ExecutionMetrics", "ExecutionMetrics"),
    (EXECUTION_TYPES, "ResultPreview", "ResultPreview"),
    (EXECUTION_TYPES, "PlanColumn", "PlanColumn"),
    (EXECUTION_TYPES, "ExecutionResponse", "ExecutionResponse"),
    (EXECUTION_TYPES, "ExecutionPreviewResponse", "ExecutionPreviewResponse"),
)

_FIELD = re.compile(r"^ {2}([a-z_][a-z0-9_]*)\??:", re.MULTILINE)


class TypeScriptSourceError(AssertionError):
    """The mirror could not be parsed, so it could not be checked."""


def _interface_fields(path: Path, name: str) -> set[str]:
    """Return the top-level field names of one exported interface."""

    source = path.read_text(encoding="utf-8")
    opening = f"export interface {name} {{"
    if opening not in source:
        raise TypeScriptSourceError(
            f"{path.name} no longer exports an interface named {name}"
        )
    body = source.split(opening, 1)[1].split("\n}", 1)[0]
    fields = set(_FIELD.findall(body))
    if not fields:
        raise TypeScriptSourceError(
            f"no fields parsed from {name} in {path.name}; the interface "
            "formatting changed and this check is no longer reading it"
        )
    return fields


def _schema_fields(name: str) -> set[str]:
    schemas = main.app.openapi()["components"]["schemas"]
    if name not in schemas:
        raise AssertionError(f"the API no longer publishes a {name} schema")
    return set(schemas[name].get("properties", {}))


@unittest.skipUnless(
    ANALYSIS_TYPES.exists(),
    "frontend sources are not present in this checkout",
)
class ClientContractTests(unittest.TestCase):
    def test_mirrored_interfaces_match_the_published_schema(self) -> None:
        for path, interface, schema in MIRRORED_TYPES:
            with self.subTest(interface=interface):
                mirrored = _interface_fields(path, interface)
                published = _schema_fields(schema)

                missing = published - mirrored
                self.assertFalse(
                    missing,
                    f"{interface} in {path.name} is missing fields this API "
                    f"sends, so they read as undefined in the browser: "
                    + ", ".join(sorted(missing)),
                )

                invented = mirrored - published
                self.assertFalse(
                    invented,
                    f"{interface} in {path.name} declares fields this API "
                    f"never sends: " + ", ".join(sorted(invented)),
                )

    def test_the_execution_view_withholds_addressing_internals(self) -> None:
        """The browser addresses a run. How an execution is keyed, resumed and
        stored stays server-side, and the mirror must not reintroduce it."""

        mirrored = _interface_fields(EXECUTION_TYPES, "ExecutionView")
        for internal in (
            "execution_key",
            "recipe_hash",
            "input_signatures",
            "fencing_token",
            "worker_id",
            "artifacts",
            "user_id",
            "workspace_id",
        ):
            self.assertNotIn(internal, mirrored)
            self.assertNotIn(internal, _schema_fields("ExecutionView"))

    def test_a_broken_mirror_fails_loudly(self) -> None:
        """The parse must not silently succeed against nothing."""

        with self.assertRaises(TypeScriptSourceError):
            _interface_fields(EXECUTION_TYPES, "NoSuchInterface")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
