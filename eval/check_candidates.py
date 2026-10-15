"""How many of the top-N candidates are really laundering?

uv run python -m eval.check_candidates --top 50
"""

import argparse
from pathlib import Path

import polars as pl

from ringtrace_engine.candidates import top_candidates

def main() -> None:
    parser = argparse.ArgumentParser(description= "Top-N candidates vs ground truth")
    parser.add_argument("--data", type=Path, default=Path("data/prepared"))
    parser.add_argument("--labels", type=Path, default=Path("data/labels"))
    parser.add_argument("--top", type=int, default=50)
    args = parser.parse_args()

    top = top_candidates(pl.read_parquet(args.data / "features.parquet"), args.top)
    members = pl.read_parquet(args.labels / "ground_truth.parquet")
    flagged = pl.read_parquet(args.labels / "laundering_txns.parquet")
    illicit = pl.concat([flagged["src"], flagged["dst"]]).unique()

    hits = members.join(top.select("acct_key"), on="acct_key")
    print(f"top{args.top}:")
    print(f" in a ring:          {hits['acct_key'].n_unique()}")
    print(f"  any laundering:    {top['acct_key'].is_in(illicit.implode()).sum()}")
    print(f"  distinct rings:    {hits['ring_id'].n_unique()}")
    print(hits.group_by("pattern").agg(pl.col("ring_id").n_unique().alias("rings")).sort("pattern"))
    

if __name__ == "__main__":
    main()