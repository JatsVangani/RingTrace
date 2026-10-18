"""Ring-level reason codes: what the agent may cite as evidence and what each is worth"""

REASON_CODES: dict[str, tuple[float, str]] = {
    "FAN_IN_BURST": (0.15, "Many senders into one account in a short window"),
    "FAN_OUT_BURST": (0.15, "One account pays many receivers in a short window"),
    "CYCLE": (0.30, "Funds return to the originating account through 2+ hops"),
    "SCATTER_GATHER": (0.30, "One source reaches one collector through several intermediaries"),
    "GATHER_SCATTER": (0.30, "Several sources converge on one account that then pays out widely"),
    "LAYERED_FLOW": (0.20, "Money passes through 2+ intermediate layers in time order"),
    "RAPID_PASSTHROUGH": (0.15, "Intermediaries forward most of what they receive within hours"),
    "CROSS_BANK_HOPS": (0.10, "The flow crosses many banks"),
    "CURRENCY_HOPPING": (0.15, "The flow changes currency along the way"),
    "SYNCHRONISED_TIMING": (0.10, "Members active in a tight window"),
    "SHARED_OWNER": (0.10, "Members share an owning entity"),
    "PRIOR_CONFIRMED_LINK": (0.25, "A member is in a ring an analyst confirmed"),
}
