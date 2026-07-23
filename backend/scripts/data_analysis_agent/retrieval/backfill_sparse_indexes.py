from __future__ import annotations

import argparse
import json
from dataclasses import asdict

import qdrant_manager

from .utils.sparse_backfill import (
    DEFAULT_BACKFILL_BATCH_SIZE,
    backfill_sparse_collection,
    data_analysis_backfill_specs,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill data-analysis lexical indexes from existing dense "
            "Qdrant payloads without re-extracting PDFs"
        )
    )
    parser.add_argument(
        "--target",
        choices=("all", "text", "tables"),
        default="all",
        help="Select which sparse companion index to backfill",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BACKFILL_BATCH_SIZE,
        help="Maximum dense payloads held in memory per batch",
    )
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    text_spec, table_spec = data_analysis_backfill_specs()
    selected_specs = {
        "all": (text_spec, table_spec),
        "text": (text_spec,),
        "tables": (table_spec,),
    }[args.target]

    client = qdrant_manager.get_client()
    try:
        results = [
            backfill_sparse_collection(
                spec,
                client=client,
                batch_size=args.batch_size,
            )
            for spec in selected_specs
        ]
        print(
            json.dumps(
                [asdict(result) for result in results],
                indent=2,
            )
        )
    finally:
        qdrant_manager.close_sync_client()


if __name__ == "__main__":
    main()
