"""Investigate one account, or the top-N candidates, and write the cases to the graph.

uv run python scripts/investigate.py --seed 28:8000B8AB0
uv run python scripts/investigate.py --top 20            # adds agent-vs-baseline numbers
"""

import argparse
import sys
import time
from pathlib import Path

import polars as pl

from ringtrace_agent.investigate import investigate
from ringtrace_core.clients import get_graph
from ringtrace_engine.candidates import score, top_candidates

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval.metrics import load_labels, summarise  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the investigator")
    parser.add_argument("--seed", help="one account key")
    parser.add_argument("--top", type=int, help="investigate the top-N candidates")
    parser.add_argument("--data", type=Path, default=Path("data/prepared"))
    parser.add_argument("--labels", type=Path, default=Path("data/labels"))
    parser.add_argument("--dry-run", action="store_true", help="don't write to the graph")
    args = parser.parse_args()

    if args.seed:
        seeds = [args.seed]
    elif args.top:
        features = pl.read_parquet(args.data / "features.parquet")
        seeds = list(top_candidates(score(features), args.top)["acct_key"])
    else:
        parser.error("give --seed or --top")

    graph = get_graph()
    results = []
    for seed in seeds:
        started = time.monotonic()
        r = investigate(graph, seed, write=not args.dry_run)
        results.append(r)
        d, c = r.get("decision", {}), r.get("case")
        verdict = (
            f"CASE {c['pattern']} score {c['score']} {c['tier']} members {len(c['members'])}"
            if c
            else f"dismissed ({d.get('pattern')})"
        )
        secs = time.monotonic() - started
        print(f"{seed:18} {verdict:48} {secs:4.0f}s  {d.get('analysis', '')[:90]}")

    if args.top and (args.labels / "ground_truth.parquet").exists():
        print(summarise(results, load_labels(args.labels)))


if __name__ == "__main__":
    main()
