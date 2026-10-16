"""Load prepared Parquet into FalkorDB (drops and rebuilds the graph).
uv run python scripts/load_graph.py --data/prepared
"""

import argparse
import time
from pathlib import Path

from ringtrace_core.clients import get_db
from ringtrace_core.config import get_settings
from ringtrace_engine.load import load


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepared Parquet -> FalkorDB")
    parser.add_argument("--data", type=Path, default=Path("data/prepared"))
    parser.add_argument("--batch-size", type=int, default=10_000)
    args = parser.parse_args()

    started = time.monotonic()
    load(get_db(), get_settings().graph_name, args.data, args.batch_size)
    print(f"done in {time.monotonic() - started:.0f}s")


if __name__ == "__main__":
    main()
