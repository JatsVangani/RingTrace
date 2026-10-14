"""Raw labels + patterns file -> ground truth for evaluation.

The only module that reads `Is Laundering` or the patterns file. engine/ and agent/
never import from eval/.

    uv run python -m eval.ground_truth --raw data/ibm-aml --out data/labels
"""

import argparse
import io
import re
from pathlib import Path

import polars as pl

from ringtrace_core.config import get_settings
from ringtrace_engine.prepare import ID_COLUMNS, TRANSACTION_COLUMNS, acct_key, read_transactions

BEGIN = re.compile(r"^BEGIN LAUNDERING ATTEMPT - ([A-Z-]+)")
MATCH_ON = [
    "ts",
    "src",
    "dst",
    "amount_received",
    "currency_received",
    "amount_paid",
    "currency_paid",
    "format",
]

def with_keys(txns: pl.DataFrame) -> pl.DataFrame:
    return txns.with_columns(
        acct_key("src_bank", "src_account").alias("src"),
        acct_key("dst_bank", "dst_account").alias("dst"),
    )

def read_patterns(raw_dir: Path, dataset: str) -> pl.DataFrame:
    """One row per transaction line in the patterns file, tagged with ring_id and pattern."""
    ring_ids, patterns, lines = [], [], []
    ring_id, pattern = -1, None
    for line in (raw_dir / f"{dataset}_Patterns.txt").read_text().splitlines():
        if begin := BEGIN.match(line):
            ring_id, pattern = ring_id + 1, begin.group(1)
        elif line.startswith("END"):
            pattern = None
        elif line.strip() and pattern:
            ring_ids.append(ring_id)
            patterns.append(pattern)
            lines.append(line)

    return pl.read_csv(
        io.StringIO("\n".join(lines)),
        has_header=False,
        new_columns=TRANSACTION_COLUMNS,
        schema_overrides=dict.fromkeys(ID_COLUMNS, pl.String),
    ).with_columns(
        pl.Series("ring_id", ring_ids, pl.UInt32),
        pl.Series("pattern", patterns),
    )

def ground_truth(raw_dir: Path, out_dir: Path, dataset: str = "HI-Small") -> dict[str, int]:
    flagged = with_keys(read_transactions(raw_dir, dataset).filter(pl.col("is_laundering") == 1))
    ring_txns = with_keys(read_patterns(raw_dir, dataset))

    matched = ring_txns.join(
        flagged.select(*MATCH_ON, "txn_id"), on=MATCH_ON, how="left", validate="m:1"
    )
    unmatched = matched["txn_id"].null_count()
    if unmatched:
        raise ValueError(f"{unmatched} pattern transactions not found among flagged transactions")

    laundering_txns = flagged.select("txn_id", "src", "dst").join(
        matched.select("txn_id", "ring_id", "pattern"), on="txn_id", how="left"
    )

    members = (
        matched.select("ring_id", "pattern", pl.concat_list("src", "dst")
                       .alias("acct_key"))
                       .explode("acct_key")
                       .unique()
                       .sort("ring_id", "acct_key")
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    members.write_parquet(out_dir / "ground_truth.parquet")
    laundering_txns.write_parquet(out_dir / "laundering_txns.parquet")
    return {
        "rings": members["ring_id"].n_unique(),
        "ring_accounts": members["acct_key"].n_unique(),
        "laundering_txns": laundering_txns.height,
        "outside_rings": laundering_txns["ring_id"].null_count(),
    }

def main() -> None:
    parser = argparse.ArgumentParser(description="Patterns file -> ground truth Parquet")
    parser.add_argument("--raw", type=Path, default=Path("data/ibm-aml"))
    parser.add_argument("--out", type=Path, default=Path("data/labels"))
    parser.add_argument("--dataset", default=get_settings().dataset)
    args = parser.parse_args()

    for name, count in ground_truth(args.raw, args.out, args.dataset).items():
        print(f"{name}: {count:,}")

if __name__ == "__main__":
    main()