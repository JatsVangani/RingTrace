"""Per-account behaviour features from prepared transfers (polars, no graph needed).

No entity kind, bank country or payment format: those would score who someone is,
not what their money does.
"""

import polars as pl

DAY_MS = 86_400_000


def legs(transfers: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Each transfer seen from both ends: (outgoing, incoming), keyed by 'acct'"""
    cols = ("ts_ms", "amount_usd")
    outgoing = transfers.select(*cols, acct="src", cp="dst", currency="currency_paid")
    incoming = transfers.select(*cols, acct="dst", cp="src", currency="currency_received")
    return outgoing, incoming


def max_daily_counterparties(leg_df: pl.DataFrame, name: str) -> pl.DataFrame:
    """Most distinct counterparties on my single (UTC) day."""
    per_day = leg_df.group_by("acct", pl.col("ts_ms") // DAY_MS).agg(pl.col("cp").n_unique())
    return per_day.group_by("acct").agg(pl.col("cp").max().alias(name))


def forwarding(outgoing: pl.DataFrame, incoming: pl.DataFrame) -> pl.DataFrame:
    """
    Match each debit to the latest credit before it: how long money sat, and
    whether it left in a different currency from the one it arrived in.
    """
    credits = incoming.select("acct", in_ts="ts_ms", in_currency="currency").sort("acct", "in_ts")
    debits = outgoing.select("acct", "ts_ms", "currency").sort("acct", "ts_ms")
    matched = debits.join_asof(
        credits, left_on="ts_ms", right_on="in_ts", by="acct", check_sortedness=False
    )
    return matched.group_by("acct").agg(
        ((pl.col("ts_ms") - pl.col("in_ts")) / 60_000).median().alias("dwell_median_min"),
        (pl.col("currency") != pl.col("in_currency")).mean().alias("currency_switch_share"),
    )


def account_features(transfers: pl.DataFrame) -> pl.DataFrame:
    outgoing, incoming = legs(transfers)
    both = pl.concat([outgoing, incoming])

    activity = both.group_by("acct").agg(
        pl.len().alias("n_txn"),
        pl.col("cp").n_unique().alias("degree"),
        pl.col("cp").str.split(":").list.first().n_unique().alias("bank_spread"),
    )
    usd_in = incoming.group_by("acct").agg(pl.col("amount_usd").sum().alias("usd_in"))
    usd_out = outgoing.group_by("acct").agg(pl.col("amount_usd").sum().alias("usd_out"))

    return (
        activity.join(usd_in, on="acct", how="left")
        .join(usd_out, on="acct", how="left")
        .join(max_daily_counterparties(incoming, "fan_in_max_day"), on="acct", how="left")
        .join(max_daily_counterparties(outgoing, "fan_out_max_day"), on="acct", how="left")
        .join(forwarding(outgoing, incoming), on="acct", how="left")
        .with_columns(
            (pl.col("usd_out") / pl.col("usd_in")).clip(0, 2).alias("passthrough_ratio"),
        )
        .rename({"acct": "acct_key"})
        .sort("acct_key")
    )
