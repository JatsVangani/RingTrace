"""Record an analyst's verdict on a case. Later investigations recall it.

uv run python scripts/verdict.py <case_id> closed_tp --notes "confirmed by SAR review"
"""

import argparse

from ringtrace_core.clients import get_graph
from ringtrace_engine.cases import record_verdict


def main() -> None:
    parser = argparse.ArgumentParser(description="Close a case with a verdict")
    parser.add_argument("case_id")
    parser.add_argument("verdict", choices=["closed_tp", "closed_fp"])
    parser.add_argument("--notes", default="")
    args = parser.parse_args()
    if record_verdict(get_graph(), args.case_id, args.verdict, args.notes):
        print(f"{args.case_id} -> {args.verdict}")
    else:
        raise SystemExit(f"no case {args.case_id}")


if __name__ == "__main__":
    main()
