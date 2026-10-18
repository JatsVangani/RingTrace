"""Everything the graph can say about one candidate, gathered in one pass.

The rows feed `cases.py`; `facts()` turns them into plain sentences for the LLM. Each tool
call is timed and summarised into `trace`, so a case can show how it was built.
"""

import time
from dataclasses import dataclass, field

from falkordb import Graph

from ringtrace_engine import queries

HOUR_MS = queries.HOUR_MS


@dataclass
class Evidence:
    seed: str
    profile: dict
    flow: list[dict]  # money_flow, both directions, 2 hops
    cycles: list[dict]
    collectors: list[dict]  # accounts that 2+ of the seed's recipients pay
    sources: list[dict]  # accounts that paid 2+ of the seed's senders
    owners: list[dict]
    trace: list[dict] = field(default_factory=list)

    @property
    def out1(self) -> list[dict]:
        return [r for r in self.flow if r["hop"] == 1 and r["outgoing"]]

    @property
    def in1(self) -> list[dict]:
        return [r for r in self.flow if r["hop"] == 1 and not r["outgoing"]]

    @property
    def pass_on(self) -> set[str]:
        """Recipients that sent the money on again within the window."""
        keys = {r["acct_key"] for r in self.out1}
        return {r["via"] for r in self.flow if r["hop"] == 2 and r["outgoing"] and r["via"] in keys}

    @property
    def upstream(self) -> set[str]:
        """Senders that had themselves just been paid by someone else."""
        keys = {r["acct_key"] for r in self.in1}
        return {
            r["via"] for r in self.flow if r["hop"] == 2 and not r["outgoing"] and r["via"] in keys
        }


def span_h(rows: list[dict]) -> float:
    ts = [r["first_ts"] for r in rows if r["first_ts"]]
    return (max(ts) - min(ts)) / HOUR_MS if len(ts) > 1 else 0.0


def gather(graph: Graph, seed: str, window_h: int = 96) -> Evidence | None:
    trace: list[dict] = []

    def step(tool: str, args: dict, fn, summary):
        started = time.monotonic()
        result = fn()
        ms = round((time.monotonic() - started) * 1000)
        trace.append({"tool": tool, "args": args, "summary": summary(result), "ms": ms})
        return result

    profile = step(
        "account_profile",
        {"acct_key": seed},
        lambda: queries.account_profile(graph, seed),
        lambda p: (
            "no such account"
            if p is None
            else f"risk {p.get('risk_score')} {p.get('risk_codes') or []}"
        ),
    )
    if profile is None:
        return None
    flow = step(
        "money_flow",
        {"acct_key": seed, "direction": "both", "hops": 2, "window_h": window_h},
        lambda: queries.money_flow(graph, seed, "both", 2, window_h),
        lambda rows: f"{len(rows)} accounts reached over one-off links",
    )
    cycles = step(
        "find_cycles",
        {"acct_key": seed, "window_h": window_h},
        lambda: queries.find_cycles(graph, seed, window_h=window_h),
        lambda cs: (
            f"{len(cs)} cycles" + (f", shortest {min(c['hops'] for c in cs)} hops" if cs else "")
        ),
    )
    out_keys = sorted({r["acct_key"] for r in flow if r["hop"] == 1 and r["outgoing"]})
    in_keys = sorted({r["acct_key"] for r in flow if r["hop"] == 1 and not r["outgoing"]})
    collectors = sources = []
    if len(out_keys) >= 2:
        collectors = step(
            "convergence",
            {"acct_keys": out_keys, "direction": "out"},
            lambda: [
                r for r in queries.convergence(graph, out_keys, "out") if r["acct_key"] != seed
            ],
            lambda rows: (
                f"{len(rows)} shared recipients"
                + (f", top shared by {len(rows[0]['shared_by'])}" if rows else "")
            ),
        )
    if len(in_keys) >= 2:
        sources = step(
            "convergence",
            {"acct_keys": in_keys, "direction": "in"},
            lambda: [r for r in queries.convergence(graph, in_keys, "in") if r["acct_key"] != seed],
            lambda rows: (
                f"{len(rows)} shared payers"
                + (f", top shared by {len(rows[0]['shared_by'])}" if rows else "")
            ),
        )
    members = [seed, *out_keys, *in_keys]
    owners = step(
        "shared_owner",
        {"acct_keys": members},
        lambda: queries.shared_owner(graph, members),
        lambda rows: f"{len(rows)} shared owners",
    )
    return Evidence(seed, profile, flow, cycles, collectors, sources, owners, trace)


def shape_reading(ev: Evidence) -> tuple[str, str]:
    """The engine's own reading of the shape around the seed: (pattern, why)."""
    n_out, n_in = len(ev.out1), len(ev.in1)
    if ev.cycles:
        return "cycle", f"money returns to the account in {min(c['hops'] for c in ev.cycles)} hops"
    if ev.collectors and len(ev.collectors[0]["shared_by"]) >= 3:
        return (
            "scatter_gather",
            f"{len(ev.collectors[0]['shared_by'])} recipients pay the same collector",
        )
    if n_in >= 3 and n_out >= 3:
        return "gather_scatter", f"{n_in} senders in, {n_out} recipients out through this account"
    if n_in >= 3 and n_out <= 2:
        if len(ev.upstream) >= 2 or ev.sources and len(ev.sources[0]["shared_by"]) >= 2:
            return "stack", f"{n_in} senders were themselves fed by earlier accounts"
        return "fan_in", f"{n_in} senders, little goes out"
    if n_out >= 3 and n_in <= 2:
        if len(ev.pass_on) >= 2:
            return "stack", f"{len(ev.pass_on)} of {n_out} recipients pass the money on"
        return "fan_out", f"{n_out} recipients, no convergence found"
    if ev.pass_on or ev.upstream:
        return "random", "a thin chain with no clear shape"
    return "none", "no one-off flow of note"


def facts(ev: Evidence) -> str:
    """The evidence as plain sentences; what the LLM reads."""
    p = ev.profile
    num = lambda v: f"{v:.2f}" if v is not None else "-"  # noqa: E731
    usd = lambda rows: f"${sum(r['usd'] for r in rows):,.0f}"  # noqa: E731
    lines = [
        f"Flagged account: {ev.seed}.",
        f"Activity: {p.get('n_txn')} transfers with {p.get('degree')} counterparties across "
        f"{p.get('bank_spread')} banks; median time money stays before moving on: "
        f"{num(p.get('dwell_median_min'))} min; currency switched on "
        f"{num(p.get('currency_switch_share'))} of pass-throughs; busiest day: "
        f"{p.get('fan_in_max_day')} payments in, {p.get('fan_out_max_day')} out.",
        f"Fresh links out: pays {len(ev.out1)} accounts, {usd(ev.out1)} total, spread over "
        f"{span_h(ev.out1):.0f} h.",
        f"Fresh links in: paid by {len(ev.in1)} accounts, {usd(ev.in1)} total, spread over "
        f"{span_h(ev.in1):.0f} h.",
        f"Onward flow: {len(ev.pass_on)} of the {len(ev.out1)} recipients pass money on again "
        f"within the window.",
        f"Upstream flow: {len(ev.upstream)} of the {len(ev.in1)} senders had themselves just "
        f"received money from another account.",
    ]
    if ev.collectors:
        c = ev.collectors[0]
        lines.append(
            f"Convergence: {len(c['shared_by'])} of the recipients later pay the same account "
            f"{c['acct_key']} (collector)."
        )
    else:
        lines.append("Convergence: the recipients do not pay any common account.")
    if ev.sources:
        s = ev.sources[0]
        lines.append(
            f"Common source: {len(s['shared_by'])} of the senders were all paid by the same "
            f"account {s['acct_key']}."
        )
    else:
        lines.append("Common source: the senders share no common payer.")
    lines.append(
        "Cycle back to the flagged account: "
        + (
            f"yes, shortest {min(c['hops'] for c in ev.cycles)} hops."
            if ev.cycles
            else "none found within 12 hops."
        )
    )
    low = sum((r["degree"] or 0) <= 5 for r in ev.flow)
    n = max(len(ev.flow), 1)
    lines.append(
        f"Neighbourhood: {len(ev.flow)} accounts reached within 2 hops; {low / n:.0%} of them have "
        f"5 or fewer counterparties (single-purpose); average risk score of reached accounts "
        f"{sum(r['risk_score'] or 0 for r in ev.flow) / n:.2f} "
        f"(flagged account: {p.get('risk_score')})."
    )
    members = {ev.seed, *(r["acct_key"] for r in ev.flow if r["hop"] == 1)}
    shared = [o for o in ev.owners if len(set(o["accounts"]) & members) >= 2]
    low = sum((r["degree"] or 0) <= 5 for r in ev.flow)
    n = max(len(ev.flow), 1)
    lines.append(
        f"Neighbourhood: {len(ev.flow)} accounts reached within 2 hops; {low / n:.0%} of them have "
        f"5 or fewer counterparties (single-purpose); average risk score of reached accounts "
        f"{sum(r['risk_score'] or 0 for r in ev.flow) / n:.2f} "
        f"(flagged account: {p.get('risk_score')})."
    )
    members = {ev.seed, *(r["acct_key"] for r in ev.flow if r["hop"] == 1)}
    shared = [o for o in ev.owners if len(set(o["accounts"]) & members) >= 2]
    if shared:
        lines.append(f"Ownership: {len(shared)} owner(s) hold two or more of these accounts.")
    pattern, why = shape_reading(ev)
    lines.append(f"Engine's shape reading: {pattern} ({why}).")
    return "\n".join(lines)
