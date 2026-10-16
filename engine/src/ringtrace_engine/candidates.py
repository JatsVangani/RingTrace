"""Rule codes over account features -> risk score -> candidate queue for the agent."""

import polars as pl

# code: (rule over feature columns, weight). A null feature never triggers a rule.
ACCOUNT_CODES: dict[str, tuple[pl.Expr, float]] = {
    "CURRENCY_HOPPING": (pl.col("currency_switch_share") > 0, 3.0),
    "RAPID_PASSTHROUGH": (
        pl.col("passthrough_ratio").is_between(0.8, 1.2) & (pl.col("dwell_median_min") <= 4320),
        1.0,
    ),
    "FAN_OUT_BURST": (pl.col("fan_out_max_day") >= 8, 1.0),
    "FAN_IN_BURST": (pl.col("fan_in_max_day") >= 5, 1.0),
    "CROSS_BANK_HOPS": (pl.col("bank_spread") >= 8, 1.0),
}


def score(features: pl.DataFrame) -> pl.DataFrame:
    """Adds 'risk_codes (names of triggered codes) and 'risk_score' (sum of their weights)."""
    names, weights = [], []
    for code, (rule, weight) in ACCOUNT_CODES.items():
        hit = rule.fill_null(False)
        names.append(pl.when(hit).then(pl.lit(code)))
        weights.append(pl.when(hit).then(weight).otherwise(0.0))

    return features.with_columns(
        pl.concat_list(names).list.drop_nulls().alias("risk_codes"),
        pl.sum_horizontal(weights).alias("risk_score"),
    )


def top_candidates(scored: pl.DataFrame, n: int, exclude: list[str] | None = None) -> pl.DataFrame:
    """Highest risk first; ties broken by currency switching, then degrree."""
    if exclude:
        scored = scored.filter(~pl.col("acct_key").is_in(pl.Series(exclude).implode()))
    return scored.sort(
        "risk_score", "currency_switch_share", "degree", descending=True, nulls_last=True
    ).head(n)
