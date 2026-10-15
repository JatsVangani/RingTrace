"""Account features + risk scores from prepared transfers.

uv run python scripts/build_features.py --data data/prepared
"""

import argparse
from pathlib import Path

import polars as pl

from ringtrace_engine.candidates import score
from ringtrace_engine.features import account_features

def main() -> None:
    parser = argparse.ArgumentParser(description= "Prepared transfers -> features.parquet")
    parser.add_argument("--data", type=Path, default=Path("data/prepared"))
    args = parser.parse_args()

    transfers = pl.read_parquet(args.data / "transfers.parquet")
    scored = score(account_features(transfers))
    scored.write_parquet(args.data / "features.parquet")

    print(f"accounts with features: {scored.height:,}")
    print(scored["risk_score"].value_counts().sort("risk_score", descending=True))

if __name__ == "__main__":
    main()