"""USD rate table implied by the dataset's own cross-currency transfers."""

import polars as pl

USD = "US Dollar"


def implied_rates(transfers: pl.DataFrame) -> pl.DataFrame:
    """One row per currency: units of that currency per 1 USD.

    Uses transfers paid in a foreign currency and received in USD; the rates in
     HI-Small are fixed, so the median is exact
    """
    rates = (
        transfers.filter((pl.col("currency_received") == USD) & (pl.col("currency_paid") != USD))
        .group_by(pl.col("currency_paid").alias("currency"))
        .agg((pl.col("amount_paid") / pl.col("amount_received")).median().alias("per_usd"))
    )
    rates = pl.concat([rates, pl.DataFrame({"currency": [USD], "per_usd": [1.0]})])

    seen = set(transfers["currency_paid"].unique()) | set(transfers["currency_received"].unique())
    missing = seen - set(rates["currency"])
    if missing:
        raise ValueError(f"no USD rate for {sorted(missing)}")
    return rates.sort("currency")


def to_usd(amount: str, currency: str, rates: pl.DataFrame) -> pl.Expr:
    """Expression converting column `amount` (in column `currency`) to USD."""
    lookup = dict(zip(rates["currency"], rates["per_usd"], strict=True))
    return pl.col(amount) / pl.col(currency).replace_strict(lookup, return_dtype=pl.Float64)
