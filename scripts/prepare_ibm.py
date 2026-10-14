"""Prepare the IBM AML CSVs for RingTrace.

uv run python scripts/prepare_ibm.py --raw data/ibm-aml --out data/prepared
"""

import argparse
from pathlib import Path

from ringtrace_core.config import get_settings
from ringtrace_engine.prepare import prepare


def main() -> None:
    parser = argparse.ArgumentParser(description="IBM AML CSVs -> prepared Parquet")
    parser.add_argument("--raw", type=Path, default=Path("data/ibm-aml"))
    parser.add_argument("--out", type=Path, default=("data/prepared"))
    parser.add_argument("--dataset", default=get_settings().dataset)
    args = parser.parse_args()

    for name, count in prepare(args.raw, args.out, args.dataset).items():
        print(f"{name}: {count:,}")


if __name__ == "__main__":
    main()
