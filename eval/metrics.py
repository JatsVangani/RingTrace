"""Agent vs baseline on the same candidates, using the labels the agent never sees.

Precision: of the accounts each method flags, how many are in a known ring.
Member recall: of the seed's ring, how much of it the case includes.
"""

from pathlib import Path

import polars as pl


def load_labels(labels_dir: Path = Path("data/labels")) -> dict[str, set[str]]:
    """acct_key -> the members of its ring."""
    gt = pl.read_parquet(labels_dir / "ground_truth.parquet")
    rings = {rid: set(keys) for rid, keys in gt.group_by("ring_id").agg("acct_key").iter_rows()}
    return {k: rings[rid] for k, rid in gt.select("acct_key", "ring_id").iter_rows()}


def precision(flagged: list[str], labels: dict[str, set[str]]) -> float:
    return sum(k in labels for k in flagged) / len(flagged) if flagged else 0.0


def summarise(results: list[dict], labels: dict[str, set[str]]) -> dict:
    """`results` are `investigate()` outputs for the baseline's top-N candidates, in rank order."""
    seeds = [r["seed"] for r in results]
    cases = [r for r in results if r.get("case")]
    cases.sort(key=lambda r: -r["case"]["score"])
    recalls = [
        len(set(r["case"]["members"]) & labels[r["seed"]]) / len(labels[r["seed"]])
        for r in cases
        if r["seed"] in labels
    ]
    return {
        "candidates": len(seeds),
        "baseline_precision": round(precision(seeds, labels), 2),
        "agent_cases": len(cases),
        "agent_precision": round(precision([r["seed"] for r in cases], labels), 2),
        "dismissed_rings": sum(r["seed"] in labels for r in results if not r.get("case")),
        "member_recall_mean": round(sum(recalls) / len(recalls), 2) if recalls else None,
    }
