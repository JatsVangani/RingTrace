"""Analyst console: candidate queue, cases with their ring, findings and trace, verdicts.

uv run streamlit run app/console.py
"""

import json
from datetime import UTC, datetime

import streamlit as st

from ringtrace_agent.investigate import investigate
from ringtrace_core.clients import get_graph
from ringtrace_engine import queries
from ringtrace_engine.cases import get_case, list_cases, record_verdict

ROLE_COLOUR = {
    "source": "#8ecae6",
    "distributor": "#ffb703",
    "intermediary": "#e9ecef",
    "collector": "#fb8500",
}
STATUS = {"open": "open", "closed_tp": "confirmed ring", "closed_fp": "false positive"}


def when(ms: int | None) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M") if ms else "-"


@st.cache_resource
def graph():
    return get_graph()


def candidates(n: int = 20) -> list[dict]:
    """Riskiest accounts first, with the case each one already landed in, if any."""
    rows = (
        graph()
        .ro_query(
            """
        MATCH (a:Account) WHERE a.risk_score IS NOT NULL
        WITH a ORDER BY a.risk_score DESC, a.currency_switch_share DESC, a.degree DESC
        LIMIT $n
        OPTIONAL MATCH (c:Case)-[:INCLUDES]->(a)
        RETURN a.acct_key, a.risk_score, a.risk_codes, a.degree, collect(c.case_id)[0]
        """,
            {"n": n},
        )
        .result_set
    )
    keys = ("acct_key", "risk_score", "risk_codes", "degree", "case_id")
    return [dict(zip(keys, r, strict=True)) for r in rows]


def ring_dot(case: dict) -> str:
    """Members coloured by role, edges from the transfers among them."""
    members = {m["acct_key"]: m["role"] for m in case["members"]}
    flows: dict[tuple[str, str], list[float]] = {}
    for t in queries.timeline(graph(), list(members)):
        flows.setdefault((t["src"], t["dst"]), []).append(t["usd"])
    lines = ["digraph ring {", "rankdir=LR; node [shape=box style=filled fontname=Helvetica];"]
    for key, role in members.items():
        lines.append(f'"{key}" [fillcolor="{ROLE_COLOUR[role]}" label="{key}\\n{role}"];')
    for (src, dst), amounts in flows.items():
        label = f"${sum(amounts):,.0f}" + (f" x{len(amounts)}" if len(amounts) > 1 else "")
        lines.append(f'"{src}" -> "{dst}" [label="{label}"];')
    lines.append("}")
    return "\n".join(lines)


def run_investigation(seed: str) -> None:
    with st.spinner(f"Investigating {seed} (graph tools, then the local model)..."):
        result = investigate(graph(), seed)
    if result.get("outcome") == "no_such_account":
        st.error(f"No account {seed}")
        return
    case = result.get("case")
    if case:
        st.session_state["case_id"] = result["case_id"]
        verb = "merged into" if result.get("merged") else "opened"
        st.success(
            f"Ring found: {case['pattern']}, {case['tier']}; {verb} case {result['case_id']}"
        )
    else:
        st.info(f"Dismissed: {result['decision']['analysis']}")


def show_queue() -> None:
    st.subheader("Candidates")
    st.caption("Riskiest accounts by rule score. Investigate one, or open the case it is in.")
    for c in candidates():
        col1, col2 = st.columns([3, 1])
        codes = ", ".join(c["risk_codes"] or [])
        col1.markdown(f"**{c['acct_key']}**  risk {c['risk_score']:.0f}  \n{codes}")
        if c["case_id"]:
            if col2.button("case", key=f"open-{c['acct_key']}"):
                st.session_state["case_id"] = c["case_id"]
        elif col2.button("investigate", key=f"inv-{c['acct_key']}"):
            run_investigation(c["acct_key"])

    st.subheader("Cases")
    for c in list_cases(graph()):
        label = (
            f"{c['case_id']}  {c['pattern']}  {c['tier']}  "
            f"${c['value_at_risk_usd']:,.0f}  ({STATUS.get(c['status'], c['status'])})"
        )
        if st.button(label, key=f"case-{c['case_id']}"):
            st.session_state["case_id"] = c["case_id"]


def show_case(case_id: str) -> None:
    case = get_case(graph(), case_id)
    if case is None:
        st.error(f"No case {case_id}")
        return
    st.subheader(f"Case {case_id}: {case['pattern']} from seed {case['seed']}")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Score", f"{case['score']:.2f}")
    m2.metric("Tier", case["tier"])
    m3.metric("Value at risk", f"${case['value_at_risk_usd']:,.0f}")
    m4.metric("Status", STATUS.get(case["status"], case["status"]))
    st.write(case.get("summary", ""))

    st.markdown("**Findings**")
    st.table(
        [
            {"code": f["code"], "weight": f["weight"], "evidence": f["text"]}
            for f in case["findings"]
        ]
    )

    st.markdown(f"**Ring ({len(case['members'])} members)**")
    st.graphviz_chart(ring_dot(case), width="stretch")
    with st.expander("Members"):
        st.dataframe(case["members"], width="stretch", hide_index=True)

    st.markdown("**Investigation trace**")
    by_inv: dict[str, list[dict]] = {}
    for t in case["trace"]:
        by_inv.setdefault(t["inv_id"], []).append(t)
    for steps in by_inv.values():
        head = steps[0]
        with st.expander(
            f"{when(head['finished_at'])}  seed {head['seed']}  model {head['model']}",
            expanded=len(by_inv) == 1,
        ):
            st.write(head["analysis"])
            st.table(
                [
                    {
                        "step": s["n"],
                        "tool": s["tool"],
                        "args": ", ".join(
                            f"{k}={v}" for k, v in json.loads(s["args_json"]).items()
                        ),
                        "result": s["summary"],
                        "ms": s["ms"],
                    }
                    for s in steps
                ]
            )

    if case["verdicts"]:
        st.markdown("**Verdicts**")
        for v in case["verdicts"]:
            st.write(f"{when(v['ts'])}  {STATUS.get(v['verdict'], v['verdict'])}: {v['notes']}")
    elif case["status"] == "open":
        st.markdown("**Verdict**")
        notes = st.text_input("Notes", key="notes", placeholder="what you checked")
        b1, b2 = st.columns(2)
        if b1.button("Confirm ring", type="primary"):
            record_verdict(graph(), case_id, "closed_tp", notes)
            st.rerun()
        if b2.button("False positive"):
            record_verdict(graph(), case_id, "closed_fp", notes)
            st.rerun()


def main() -> None:
    st.set_page_config(page_title="RingTrace", layout="wide")
    st.title("RingTrace")
    st.caption("Laundering rings investigated over a FalkorDB transaction graph")

    with st.sidebar:
        seed = st.text_input("Investigate an account", placeholder="28:8000B8AB0")
        if st.button("Run") and seed:
            run_investigation(seed.strip())

    left, right = st.columns([1, 2])
    with left:
        show_queue()
    with right:
        if case_id := st.session_state.get("case_id"):
            show_case(case_id)
        else:
            st.info("Pick a case, or investigate a candidate.")


main()
