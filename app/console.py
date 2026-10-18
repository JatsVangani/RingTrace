"""Analyst console: candidate queue, account explorer, live investigations, cases, verdicts.

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
TIER_COLOUR = {"freeze": "red", "hold_txn": "orange", "step_up": "blue", "monitor": "gray"}
STATUS = {"open": "open", "closed_tp": "confirmed ring", "closed_fp": "false positive"}
STATUS_COLOUR = {"open": "blue", "closed_tp": "green", "closed_fp": "gray"}
CHIP = '<span style="background:{c};color:#212529;padding:2px 8px;border-radius:6px">{r}</span>'


def when(ms: int | None) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M") if ms else "-"


def usd(amount: float | None) -> str:
    return f"${amount:,.0f}" if amount else "-"


def badge(text: str, colour: str) -> str:
    return f":{colour}-badge[{text}]"


@st.cache_resource
def graph():
    return get_graph()


def candidates(n: int = 30) -> list[dict]:
    """Riskiest accounts first, with the case each one already landed in, if any."""
    rows = (
        graph()
        .ro_query(
            """
        MATCH (a:Account) WHERE a.risk_score IS NOT NULL
        WITH a ORDER BY a.risk_score DESC, a.currency_switch_share DESC, a.degree DESC
        LIMIT $n
        OPTIONAL MATCH (c:Case)-[:INCLUDES]->(a)
        RETURN a.acct_key, a.risk_score, a.risk_codes, a.degree, a.usd_out, collect(c.case_id)[0]
        """,
            {"n": n},
        )
        .result_set
    )
    return [
        {
            "account": k,
            "risk": r,
            "codes": ", ".join(codes or []),
            "counterparties": d,
            "usd out": out or 0,
            "case": case or "",
        }
        for k, r, codes, d, out, case in rows
    ]


def ring_dot(case: dict) -> str:
    """Members coloured by role, seed outlined, edges from the transfers among them."""
    members = {m["acct_key"]: m["role"] for m in case["members"]}
    flows: dict[tuple[str, str], list[float]] = {}
    for t in queries.timeline(graph(), list(members)):
        flows.setdefault((t["src"], t["dst"]), []).append(t["usd"])
    lines = [
        "digraph ring {",
        'rankdir=LR; bgcolor="transparent";',
        'node [shape=box style="filled,rounded" fontname=Helvetica fontsize=11];',
        'edge [fontname=Helvetica fontsize=9 color="#6c757d"];',
    ]
    for key, role in members.items():
        width = 3 if key == case["seed"] else 1
        lines.append(
            f'"{key}" [fillcolor="{ROLE_COLOUR[role]}" penwidth={width} label="{key}\\n{role}"];'
        )
    for (src, dst), amounts in flows.items():
        label = usd(sum(amounts)) + (f" x{len(amounts)}" if len(amounts) > 1 else "")
        lines.append(f'"{src}" -> "{dst}" [label="{label}"];')
    lines.append("}")
    return "\n".join(lines)


def run_investigation(seed: str) -> None:
    """Run the investigator with every tool step shown as it lands."""
    with st.status(f"Investigating {seed}", expanded=True) as status:

        def on_step(t: dict) -> None:
            st.write(f"`{t['tool']}`  {t['summary']}  ({t['ms']} ms)")

        try:
            result = investigate(graph(), seed, on_step=on_step)
        except Exception as e:  # show the analyst what went wrong; keep the app up
            status.update(label=f"Investigation failed: {e}", state="error")
            return
        if result.get("outcome") == "no_such_account":
            status.update(label=f"No account {seed}", state="error")
            return
        case = result.get("case")
        if case:
            verb = "merged into" if result.get("merged") else "opened"
            label = (
                f"Ring found: {case['pattern']}, {case['tier']}; {verb} case {result['case_id']}"
            )
        else:
            label = f"Dismissed: {result['decision']['analysis']}"
        status.update(label=label, state="complete", expanded=False)
    st.session_state["last_run"] = result
    if case:
        st.session_state["case_id"] = result["case_id"]
        st.session_state["account"] = None
    st.rerun()


def show_header() -> None:
    cases = list_cases(graph(), limit=500)
    todo = sum(1 for c in candidates() if not c["case"])
    frozen = sum(c["value_at_risk_usd"] for c in cases if c["tier"] == "freeze")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Candidates to look at", todo)
    m2.metric("Open cases", sum(c["status"] == "open" for c in cases))
    m3.metric("Confirmed rings", sum(c["status"] == "closed_tp" for c in cases))
    m4.metric("Value in freeze tier", usd(frozen))


def show_queue() -> None:
    tab_c, tab_k = st.tabs(["Candidates", "Cases"])
    with tab_c:
        st.caption("Riskiest accounts by rule score. Select one to explore it.")
        rows = candidates()
        picked = st.dataframe(
            rows,
            hide_index=True,
            width="stretch",
            on_select="rerun",
            selection_mode="single-row",
            key="candidate_table",
            column_config={
                "risk": st.column_config.ProgressColumn(min_value=0, max_value=7, format="%.0f"),
                "usd out": st.column_config.NumberColumn(format="dollar"),
            },
        )
        if picked.selection.rows:
            row = rows[picked.selection.rows[0]]
            st.session_state["account"] = row["account"]
            if row["case"]:
                st.session_state["case_id"] = row["case"]
    with tab_k:
        st.caption("Most value at risk first. Select one to open it.")
        rows = [
            {
                "case": c["case_id"],
                "pattern": c["pattern"],
                "tier": c["tier"],
                "score": c["score"],
                "value at risk": c["value_at_risk_usd"],
                "members": c["member_count"],
                "status": STATUS.get(c["status"], c["status"]),
                "seed": c["seed"],
            }
            for c in list_cases(graph())
        ]
        picked = st.dataframe(
            rows,
            hide_index=True,
            width="stretch",
            on_select="rerun",
            selection_mode="single-row",
            key="case_table",
            column_config={
                "score": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f"),
                "value at risk": st.column_config.NumberColumn(format="dollar"),
            },
        )
        if picked.selection.rows:
            st.session_state["case_id"] = rows[picked.selection.rows[0]]["case"]
            st.session_state["account"] = None


def show_account(acct_key: str) -> None:
    p = queries.account_profile(graph(), acct_key)
    if p is None:
        st.error(f"No account {acct_key}")
        return
    st.subheader(f"Account {acct_key}")
    st.markdown(
        f"{p['bank']} ({p['bank_country']}), owner {p['entity_id']} ({p['entity_kind']})  \n"
        + " ".join(badge(code, "orange") for code in p.get("risk_codes") or [])
    )
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Transfers", p.get("n_txn") or 0)
    m2.metric("Counterparties", p.get("degree") or 0)
    m3.metric("In", usd(p.get("usd_in")))
    m4.metric("Out", usd(p.get("usd_out")))
    b1, b2, _ = st.columns([1, 1, 2])
    if b1.button("Investigate this account", type="primary", key="inv-account"):
        run_investigation(acct_key)
    if st.session_state.get("case_id") and b2.button("Open its case", key="open-case"):
        st.session_state["account"] = None
        st.rerun()
    st.markdown("**Counterparties** (one-off links first)")
    st.dataframe(
        [
            {
                "direction": "pays" if c["outgoing"] else "paid by",
                "account": c["acct_key"],
                "transfers": c["n_txn"],
                "usd": c["usd"],
                "first": when(c["first_ts"]),
                "last": when(c["last_ts"]),
            }
            for c in p["counterparties"]
        ],
        hide_index=True,
        width="stretch",
        column_config={"usd": st.column_config.NumberColumn(format="dollar")},
    )


def show_last_run(case_id: str) -> None:
    run = st.session_state.get("last_run")
    if not run or run.get("case_id") != case_id:
        return
    with st.expander("What the model read, and what it answered"):
        st.code(run["facts"], language=None)
        st.json(run["decision"])


def show_trace(trace: list[dict]) -> None:
    by_inv: dict[str, list[dict]] = {}
    for t in trace:
        by_inv.setdefault(t["inv_id"], []).append(t)
    for steps in by_inv.values():
        head = steps[0]
        st.markdown(
            f"**{when(head['finished_at'])}**  seed `{head['seed']}`  model {head['model']}"
        )
        st.write(head["analysis"])
        st.dataframe(
            [
                {
                    "step": s["n"],
                    "tool": s["tool"],
                    "args": ", ".join(f"{k}={v}" for k, v in json.loads(s["args_json"]).items()),
                    "result": s["summary"],
                    "ms": s["ms"],
                }
                for s in steps
            ],
            hide_index=True,
            width="stretch",
            column_config={
                "ms": st.column_config.ProgressColumn(
                    min_value=0, max_value=max(s["ms"] for s in steps) or 1, format="%d"
                )
            },
        )


def show_case(case_id: str) -> None:
    case = get_case(graph(), case_id)
    if case is None:
        st.error(f"No case {case_id}")
        return
    status = case["status"]
    st.subheader(f"Case {case_id}")
    st.markdown(
        " ".join(
            [
                badge(case["pattern"], "violet"),
                badge(case["tier"], TIER_COLOUR.get(case["tier"], "gray")),
                badge(STATUS.get(status, status), STATUS_COLOUR.get(status, "gray")),
                f"seed `{case['seed']}`",
            ]
        )
    )
    m1, m2, m3 = st.columns(3)
    m1.metric("Score", f"{case['score']:.2f}")
    m2.metric("Value at risk", usd(case["value_at_risk_usd"]))
    m3.metric("Members", len(case["members"]))
    st.progress(min(case["score"], 1.0))
    st.info(case.get("summary", ""))
    show_last_run(case_id)

    tab_ring, tab_findings, tab_members, tab_trace = st.tabs(
        ["Ring", "Findings", "Members", "Trace"]
    )
    with tab_ring:
        legend = " ".join(CHIP.format(c=c, r=r) for r, c in ROLE_COLOUR.items())
        st.markdown(f"{legend}  bold outline = seed", unsafe_allow_html=True)
        st.graphviz_chart(ring_dot(case), width="stretch")
    with tab_findings:
        st.dataframe(
            [
                {"code": f["code"], "weight": f["weight"], "evidence": f["text"]}
                for f in case["findings"]
            ],
            hide_index=True,
            width="stretch",
            column_config={
                "weight": st.column_config.ProgressColumn(min_value=0, max_value=0.3, format="%.2f")
            },
        )
    with tab_members:
        st.dataframe(case["members"], hide_index=True, width="stretch")
    with tab_trace:
        show_trace(case["trace"])

    st.markdown("**Verdict**")
    if case["verdicts"]:
        for v in case["verdicts"]:
            label = badge(
                STATUS.get(v["verdict"], v["verdict"]), STATUS_COLOUR.get(v["verdict"], "gray")
            )
            st.markdown(f"{label} {when(v['ts'])}  {v['notes']}")
        st.caption("Later investigations touching these accounts will recall this verdict.")
    else:
        notes = st.text_input("Notes", key="notes", placeholder="what you checked")
        b1, b2, _ = st.columns([1, 1, 2])
        if b1.button("Confirm ring", type="primary"):
            record_verdict(graph(), case_id, "closed_tp", notes)
            st.toast(f"Case {case_id} confirmed")
            st.rerun()
        if b2.button("False positive"):
            record_verdict(graph(), case_id, "closed_fp", notes)
            st.toast(f"Case {case_id} closed as false positive")
            st.rerun()


def main() -> None:
    st.set_page_config(page_title="RingTrace", page_icon="🕸️", layout="wide")
    st.title("RingTrace")
    st.caption("Laundering rings investigated over a FalkorDB transaction graph by a local model")
    show_header()

    with st.sidebar:
        st.markdown("**Investigate any account**")
        seed = st.text_input(
            "Account key", placeholder="28:8000B8AB0", label_visibility="collapsed"
        )
        if st.button("Run investigation", type="primary") and seed:
            run_investigation(seed.strip())
        if st.button("Explore first") and seed:
            st.session_state["account"] = seed.strip()
        st.divider()
        st.markdown(
            "**How a case is built**  \n"
            "1. Graph tools gather the evidence: profile, money flow, cycles, convergence, "
            "memory, owners.  \n"
            "2. The local model reads the facts and decides ring or not, and the pattern.  \n"
            "3. Code picks members, roles, findings and the tier."
        )

    left, right = st.columns([1, 2], gap="large")
    with left:
        show_queue()
    with right:
        if account := st.session_state.get("account"):
            show_account(account)
        elif case_id := st.session_state.get("case_id"):
            show_case(case_id)
        else:
            st.info("Select a candidate to explore it, or a case to open it.")


main()
