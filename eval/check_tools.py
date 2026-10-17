"""
from one ring member, do the tools reach the rest of the ring?
Recall is measured against the seed's own connected piece of the ring:
STACK and BIPARTITE attempts are often
several unconnected chains, unreachable from each other

uv run python -m eval.check_tools --rings 15
"""

import argparse
import random
import time
from pathlib import Path

import polars as pl

from ringtrace_core.clients import get_graph
from ringtrace_engine.queries import find_cycles, money_flow


def ring_component(edges: pl.DataFrame, seed: str) -> set[str]:
    neighbours: dict[str, set[str]] = {}
    for src, dst in edges.select("src", "dst").iter_rows():
        neighbours.setdefault(src, set()).add(dst)
        neighbours.setdefault(dst, set()).add(src)
    component, stack = {seed}, [seed]
    while stack:
        for n in neighbours[stack.pop()] - component:
            component.add(n)
            stack.append(n)
    return component


def main() -> None:
    parser = argparse.ArgumentParser(description="Ring recall of the graph tools")
    parser.add_argument("--labels", type=Path, default=Path("data/labels"))
    parser.add_argument("--rings", type=int, default=15, help="rings smapled per pattern")
    parser.add_argument("--hops", type=int, default=3)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    graph = get_graph()
    rng = random.Random(args.seed)
    ring_txns = pl.read_parquet(args.labels / "laundering_txns.parquet").drop_nulls("ring_id")

    rings: dict[str, list] = {}
    for (ring_id, pattern), edges in ring_txns.sort("ring_id").group_by(
        "ring_id", "pattern", maintain_order=True
    ):
        rings.setdefault(pattern, []).append((ring_id, edges))

        rows = []
        for pattern, sample in sorted(rings.items()):
            for ring_id, edges in rng.sample(sample, min(args.rings, len(sample))):
                members = sorted(set(edges["src"]) | set(edges["dst"]))
                seed = rng.choice(members)
                target = ring_component(edges, seed)
                started = time.monotonic()
                reached = {seed} | {r["acct_key"] for r in money_flow(graph, seed, hops=args.hops)}
                if pattern == "CYCLE":
                    for cycle in find_cycles(graph, seed):
                        reached |= set(cycle["path"])
                rows.append(
                    {
                        "pattern": pattern,
                        "ring_id": ring_id,
                        "recall": len(target & reached) / len(target),
                        "returned": len(reached),
                        "ms": (time.monotonic() - started) * 1000,
                    }
                )

        df = pl.DataFrame(rows)
        summary = df.group_by("pattern").agg(
            pl.col("recall").mean().round(2),
            (pl.col("recall") >= 0.8).mean().round(2).alias("rings_ge_80pct"),
            pl.col("returned").median().alias("returned_median"),
            pl.col("ms").max().round(0).alias("ms_max"),
        )
        print(summary.sort("pattern"))
        print(f"overall recall {df['recall'].mean():.2f} over {df.height} rings")


if __name__ == "__main__":
    main()
