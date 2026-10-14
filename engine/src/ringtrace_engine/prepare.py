"""IBM AML CSVs -> prepared Parquet for the graph and features.

Outputs never contain the `Is Laundering` label; only eval/ reads labels.
"""

from pathlib import Path

import polars as pl

from ringtrace_engine.fx import implied_rates, to_usd

TRANSACTION_COLUMNS = [
    "ts",
    "src_bank",
    "src_account",
    "dst_bank",
    "dst_account",
    "amount_received",
    "currency_received",
    "amount_paid",
    "currency_paid",
    "format",
    "is_laundering",
]
ID_COLUMNS = ("src_bank", "src_account", "dst_bank", "dst_account")


def read_transactions(raw_dir: Path, dataset: str) -> pl.DataFrame:
    """Raw transactions with a stable `txn_id` (CSV row order). Includes the label.

    The header repeats `Account`, so columns are named by position. eval/ uses this
    same reader, so txn ids line up between prepared data and ground truth.
    """

    return pl.read_csv(
        raw_dir / f"{dataset}_Trans.csv",
        has_header=False,
        skip_rows=1,
        new_columns=TRANSACTION_COLUMNS,
        schema_overrides=dict.fromkeys(ID_COLUMNS, pl.String),
    ).with_row_index("txn_id")


def acct_key(bank: str, account: str) -> pl.Expr:
    """`<bank_id>:<account_no>`. The transactions file zero-pads bank ids
    inconsistently (010 vs 10); the accounts file never does, so strip them."""
    return pl.col(bank).str.strip_chars_start("0") + ":" + pl.col(account)


def prepare_transfers(txns: pl.DataFrame, rates: pl.DataFrame) -> pl.DataFrame:
    return txns.select(
        "txn_id",
        pl.col("ts")
        .str.to_datetime("%Y/%m/%d %H:%M", time_zone="UTC")
        .dt.epoch("ms")
        .alias("ts_ms"),
        acct_key("src_bank", "src_account").alias("src"),
        acct_key("dst_bank", "dst_account").alias("dst"),
        to_usd("amount_paid", "currency_paid", rates).round(2).alias("amount_usd"),
        "amount_paid",
        "currency_paid",
        "amount_received",
        "currency_received",
        "format",
    )


def bank_country(name: str) -> pl.Expr:
    """`Portugal Bank #4507` -> Portugal. Named banks (`First Bank of Topeka`) are US."""
    return (
        pl.col(name).str.extract(r"^(.+?) Bank #\d+$").replace({"Crytpo": "Crypto"}).fill_null("US")
    )


def prepare_accounts(raw_dir: Path, dataset: str, self_transfers: pl.DataFrame) -> pl.DataFrame:
    accounts = pl.read_csv(
        raw_dir / f"{dataset}_accounts.csv",
        schema_overrides=dict.fromkeys(("Bank ID", "Account Number", "Entity ID"), pl.String),
    ).select(
        acct_key("Bank ID", "Account Number").alias("acct_key"),
        pl.col("Bank ID").str.strip_chars_start("0").alias("bank_id"),
        pl.col("Account Number").alias("account_no"),
        pl.col("Bank Name").alias("bank_name"),
        bank_country("Bank Name").alias("bank_country"),
        pl.col("Entity ID").alias("entity_id"),
        pl.col("Entity Name").str.replace(r" #\d+$", "").alias("entity_kind"),
    )

    self_totals = self_transfers.group_by(pl.col("src").alias("acct_key")).agg(
        pl.len().alias("self_txn_count"),
        pl.col("amount_usd").sum().round(2).alias("self_txn_usd"),
    )
    return accounts.join(self_totals, on="acct_key", how="left").with_columns(
        pl.col("self_txn_count").fill_null(0),
        pl.col("self_txn_usd").fill_null(0.0),
    )


def prepare(raw_dir: Path, out_dir: Path, dataset: str = "HI-Small") -> dict[str, int]:
    txns = read_transactions(raw_dir, dataset).drop("is_laundering")
    rates = implied_rates(txns)
    transfers = prepare_transfers(txns, rates)

    is_self = pl.col("src") == pl.col("dst")
    accounts = prepare_accounts(raw_dir, dataset, transfers.filter(is_self))
    transfers = transfers.filter(~is_self).sort("ts_ms", "txn_id")

    endpoints = pl.concat([transfers["src"], transfers["dst"]]).unique().alias("acct_key")
    unknown = endpoints.to_frame().join(accounts, on="acct_key", how="anti")
    if unknown.height:
        raise ValueError(f"{unknown.height} transfer endpoints missing from accounts file")

    out_dir.mkdir(parents=True, exist_ok=True)
    transfers.write_parquet(out_dir / "transfers.parquet")
    accounts.write_parquet(out_dir / "accounts.parquet")
    rates.write_parquet(out_dir / "fx_rates.parquet")
    return {"transfers": transfers.height, "accounts": accounts.height, "currencies": rates.height}
