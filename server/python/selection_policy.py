#!/usr/bin/env python3
"""Optional selection policies: minimum-probability gate, market mix, race-type filters.

Workstream B (strike-rate lever). Every policy here is OFF unless its flag is
set, and each returns a *proposal* rather than mutating a pick, so the caller
decides whether to adopt it. With no flag set this module changes nothing.

Read this before enabling anything:

  * Market mix (place / each-way / dutching) stakes more than one runner per
    race, or stakes a market other than win. That contradicts the BET/NO_BET
    contract's one-bet-per-race rule (guardrail 10) and the standing
    prohibition on relaxing it. It is implemented because it was explicitly
    asked for; it must not be switched on without a walk-forward result and a
    deliberate decision to change that contract.
  * Race-type filters raise strike rate by declining races. That is the
    mechanism the research pass named as the single most common way a betting
    system fools itself: refusing bets raises the hit rate of what remains
    while usually lowering ROI, and it prices in whatever bias the filter was
    fitted on. Each rule is therefore individually toggleable and individually
    measurable — never ship them as a bundle.
  * A minimum-probability gate is the mildest of the three but pulls the same
    lever: it trades bet count for strike rate. Tune it on validation folds
    only, never on the fold you report.

Scales follow the live pick contract (examples/sample_race.json):
`win_pct`, `raw_model_pct` and `edge_pct` are percentages 0-100; `odds` is
decimal including stake.

Run `python selection_policy.py` for the self-test.
"""

import os
import sys
from typing import Any, Dict, List, Optional, Sequence

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _stride_flag(name: str) -> bool:
    """Default-off env flag, Variant B idiom (see run_tips_pipeline.py:591)."""
    return os.environ.get(name, "false").strip().lower() in ("true", "1", "yes")


def _stride_float(name: str, default: float) -> float:
    """Numeric knob; an unparseable value falls back to the default, loudly."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        print(f"  [POLICY] {name}={raw!r} is not a number — using {default}",
              file=sys.stderr)
        return default


# ---------------------------------------------------------------------------
# B2 — minimum probability gate
# ---------------------------------------------------------------------------

def min_probability_gate(picks: Sequence[Dict[str, Any]],
                         min_win_pct: Optional[float] = None,
                         prob_key: str = "win_pct") -> Dict[str, Any]:
    """Propose declining picks whose calibrated win probability is too low.

    Returns {'enabled', 'min_win_pct', 'kept', 'declined'} where kept/declined
    are the pick dicts themselves. Disabled ⇒ everything is kept, so the caller
    can apply the result unconditionally.
    """
    enabled = _stride_flag("STRIDE_MIN_PROB_GATE")
    if min_win_pct is None:
        min_win_pct = _stride_float("STRIDE_MIN_PROB_PCT", 10.0)

    if not enabled:
        return {"enabled": False, "min_win_pct": min_win_pct,
                "kept": list(picks), "declined": []}

    kept, declined = [], []
    for p in picks:
        value = p.get(prob_key)
        # A pick with no probability is kept: absence of evidence is not a
        # reason to decline, and silently dropping it would hide a data fault.
        if value is None or value >= min_win_pct:
            kept.append(p)
        else:
            declined.append(p)

    return {"enabled": True, "min_win_pct": min_win_pct,
            "kept": kept, "declined": declined}


# ---------------------------------------------------------------------------
# B3 — market mix
# ---------------------------------------------------------------------------

def dutch_stakes(picks: Sequence[Dict[str, Any]], total_stake: float = 100.0,
                 odds_key: str = "odds") -> Dict[str, Any]:
    """Split `total_stake` across runners so the return is equal whichever wins.

    Stake_i is proportional to 1/odds_i. The book only pays if one of the
    selected runners wins, so `guaranteed_return` is the payout in that case and
    `worst_case` is -total_stake otherwise. A positive `profit_if_any_wins` does
    NOT mean a positive-EV bet: it means the combined implied probability is
    under 100%, which says nothing about whether the winner is among them.
    """
    usable = []
    for p in picks:
        o = p.get(odds_key)
        try:
            o = float(o)
        except (TypeError, ValueError):
            continue
        if o > 1.0:
            usable.append((p, o))

    if not usable:
        return {"enabled": False, "reason": "no runner with usable odds",
                "legs": [], "total_stake": 0.0}

    inv_sum = sum(1.0 / o for _, o in usable)
    legs = []
    for p, o in usable:
        stake = total_stake * (1.0 / o) / inv_sum
        legs.append({
            "horse": p.get("horse"),
            "odds": round(o, 2),
            "stake": round(stake, 2),
            "return_if_wins": round(stake * o, 2),
        })

    guaranteed = total_stake / inv_sum
    return {
        "enabled": True,
        "legs": legs,
        "total_stake": round(total_stake, 2),
        "combined_implied_pct": round(inv_sum * 100.0, 2),
        "guaranteed_return": round(guaranteed, 2),
        "profit_if_any_wins": round(guaranteed - total_stake, 2),
        "worst_case": round(-total_stake, 2),
    }


def each_way_split(pick: Dict[str, Any], total_stake: float = 100.0,
                   place_fraction: float = 0.25, place_terms: int = 3,
                   odds_key: str = "odds") -> Dict[str, Any]:
    """Half the stake on the win, half on the place at `place_fraction` odds.

    AU place terms vary by field size; `place_terms` and `place_fraction` are
    inputs rather than constants because a 5-runner field pays differently from
    a 16-runner one and the caller knows the field.
    """
    try:
        o = float(pick.get(odds_key))
    except (TypeError, ValueError):
        return {"enabled": False, "reason": "no usable odds"}
    if o <= 1.0:
        return {"enabled": False, "reason": "odds must exceed 1.0"}

    half = total_stake / 2.0
    place_odds = 1.0 + (o - 1.0) * place_fraction
    return {
        "enabled": True,
        "horse": pick.get("horse"),
        "win_stake": round(half, 2),
        "place_stake": round(half, 2),
        "win_odds": round(o, 2),
        "place_odds": round(place_odds, 2),
        "place_terms": place_terms,
        "return_if_wins": round(half * o + half * place_odds, 2),
        "return_if_places": round(half * place_odds, 2),
        "return_if_unplaced": 0.0,
        "total_stake": round(total_stake, 2),
    }


def place_only(pick: Dict[str, Any], total_stake: float = 100.0,
               place_fraction: float = 0.25, odds_key: str = "odds") -> Dict[str, Any]:
    """Stake the place market alone at `place_fraction` of win odds."""
    ew = each_way_split(pick, total_stake=total_stake * 2.0,
                        place_fraction=place_fraction, odds_key=odds_key)
    if not ew.get("enabled"):
        return ew
    return {
        "enabled": True,
        "horse": ew["horse"],
        "place_stake": round(total_stake, 2),
        "place_odds": ew["place_odds"],
        "return_if_places": round(total_stake * ew["place_odds"], 2),
        "return_if_unplaced": 0.0,
        "total_stake": round(total_stake, 2),
    }


def propose_market_mix(picks: Sequence[Dict[str, Any]], total_stake: float = 100.0,
                       bunched_within_pct: Optional[float] = None) -> Dict[str, Any]:
    """Suggest a market other than straight win, when enabled.

    Dutching is proposed only when the top probabilities are *bunched* — the
    case where a single-runner bet is least defensible. `bunched_within_pct` is
    the maximum gap between the top two `win_pct` values for that to apply.
    """
    if not _stride_flag("STRIDE_MARKET_MIX"):
        return {"enabled": False, "mode": "win", "reason": "flag off"}

    mode = os.environ.get("STRIDE_MARKET_MIX_MODE", "dutch").strip().lower()
    if bunched_within_pct is None:
        bunched_within_pct = _stride_float("STRIDE_MARKET_MIX_BUNCHED_PCT", 3.0)

    ranked = [p for p in picks if p.get("win_pct") is not None]
    ranked.sort(key=lambda p: p["win_pct"], reverse=True)
    if not ranked:
        return {"enabled": False, "mode": "win", "reason": "no probabilities"}

    if mode == "place":
        return {"enabled": True, "mode": "place",
                "proposal": place_only(ranked[0], total_stake=total_stake)}
    if mode == "each_way":
        return {"enabled": True, "mode": "each_way",
                "proposal": each_way_split(ranked[0], total_stake=total_stake)}

    if len(ranked) < 2:
        return {"enabled": False, "mode": "win", "reason": "only one runner with a probability"}
    gap = ranked[0]["win_pct"] - ranked[1]["win_pct"]
    if gap > bunched_within_pct:
        return {"enabled": False, "mode": "win",
                "reason": f"top two not bunched (gap {gap:.1f}pp > {bunched_within_pct:.1f}pp)"}

    return {"enabled": True, "mode": "dutch", "gap_pp": round(gap, 2),
            "proposal": dutch_stakes(ranked[:2], total_stake=total_stake)}


# ---------------------------------------------------------------------------
# B4 — race-type filters
# ---------------------------------------------------------------------------

RACE_FILTER_RULES = {
    # flag suffix -> (description, predicate on the race dict)
    "MAIDEN": ("decline maiden races",
               lambda r: "maiden" in str(r.get("race_class", "")).lower()),
    "BIG_FIELD": ("decline fields larger than STRIDE_RACE_FILTER_MAX_FIELD",
                  lambda r: (r.get("field_size") or 0) >
                            _stride_float("STRIDE_RACE_FILTER_MAX_FIELD", 14)),
    "SMALL_FIELD": ("decline fields smaller than STRIDE_RACE_FILTER_MIN_FIELD",
                    lambda r: 0 < (r.get("field_size") or 0) <
                              _stride_float("STRIDE_RACE_FILTER_MIN_FIELD", 6)),
    "HEAVY_GOING": ("decline heavy tracks",
                    lambda r: "heavy" in str(r.get("going", "")).lower()),
}


def race_type_filter(race: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate each race-type rule independently.

    Every rule has its own flag so it can be measured on its own. Returns
    {'enabled', 'declined', 'triggered': [...]}. Nothing is declined unless at
    least one rule flag is set AND its predicate fires.
    """
    triggered = []
    for suffix, (description, predicate) in RACE_FILTER_RULES.items():
        flag = f"STRIDE_RACE_FILTER_{suffix}"
        if not _stride_flag(flag):
            continue
        try:
            if predicate(race):
                triggered.append({"rule": suffix, "flag": flag, "why": description})
        except Exception as err:
            print(f"  [POLICY] rule {suffix} errored ({err}) — not applied",
                  file=sys.stderr)

    return {"enabled": bool(triggered) or any(
                _stride_flag(f"STRIDE_RACE_FILTER_{s}") for s in RACE_FILTER_RULES),
            "declined": bool(triggered),
            "triggered": triggered}


def _self_test():
    print("selection_policy self-test")

    picks = [
        {"horse": "Alpha", "win_pct": 22.0, "edge_pct": 4.0, "odds": 4.0},
        {"horse": "Bravo", "win_pct": 20.5, "edge_pct": 2.0, "odds": 4.5},
        {"horse": "Charlie", "win_pct": 6.0, "edge_pct": 1.0, "odds": 12.0},
        {"horse": "Delta", "win_pct": None, "edge_pct": None, "odds": 9.0},
    ]

    saved = {k: os.environ.pop(k, None) for k in list(os.environ) if k.startswith("STRIDE_")}
    try:
        # --- all policies inert by default ---
        g = min_probability_gate(picks)
        assert g["enabled"] is False and len(g["kept"]) == 4 and not g["declined"]
        assert propose_market_mix(picks)["enabled"] is False
        assert race_type_filter({"race_class": "Maiden", "field_size": 20,
                                 "going": "Heavy"})["declined"] is False
        print("  defaults: all three policies inert, nothing declined")

        # --- B2 min probability gate ---
        os.environ["STRIDE_MIN_PROB_GATE"] = "true"
        os.environ["STRIDE_MIN_PROB_PCT"] = "10"
        g = min_probability_gate(picks)
        assert [p["horse"] for p in g["declined"]] == ["Charlie"], g["declined"]
        assert "Delta" in [p["horse"] for p in g["kept"]], "missing probability must not be declined"
        os.environ["STRIDE_MIN_PROB_PCT"] = "not-a-number"
        assert min_probability_gate(picks)["min_win_pct"] == 10.0, "bad value ⇒ default"
        print(f"  min_prob_gate: declined {[p['horse'] for p in g['declined']]}, "
              f"kept unknown-probability pick, unparseable threshold falls back")

        # --- B3 dutching maths ---
        d = dutch_stakes(picks[:2], total_stake=100.0)
        assert abs(sum(l["stake"] for l in d["legs"]) - 100.0) < 0.05
        returns = [l["return_if_wins"] for l in d["legs"]]
        assert max(returns) - min(returns) < 0.05, f"returns must be equal: {returns}"
        assert d["worst_case"] == -100.0
        print(f"  dutch: equal return {returns[0]:.2f} either way, "
              f"implied {d['combined_implied_pct']:.1f}%, worst case {d['worst_case']:.0f}")

        ew = each_way_split(picks[0], total_stake=100.0)
        assert ew["place_odds"] == 1.75, ew["place_odds"]   # 1 + (4-1)*0.25
        assert ew["return_if_wins"] > ew["return_if_places"] > ew["return_if_unplaced"]
        po = place_only(picks[0], total_stake=100.0)
        assert po["place_odds"] == 1.75, po
        assert each_way_split({"odds": None})["enabled"] is False
        assert each_way_split({"odds": 1.0})["enabled"] is False
        print(f"  each_way: win {ew['return_if_wins']:.0f} / place "
              f"{ew['return_if_places']:.0f} / unplaced 0; degenerate odds rejected")

        # --- B3 mix only fires when the top two are bunched ---
        os.environ["STRIDE_MARKET_MIX"] = "true"
        os.environ["STRIDE_MARKET_MIX_MODE"] = "dutch"
        m = propose_market_mix(picks, total_stake=100.0)
        assert m["enabled"] is True and m["mode"] == "dutch", m
        spread = [dict(p) for p in picks]
        spread[1]["win_pct"] = 8.0          # top two now 14pp apart
        m2 = propose_market_mix(spread)
        assert m2["enabled"] is False and "not bunched" in m2["reason"], m2
        print(f"  market_mix: dutches a bunched race (gap {m['gap_pp']}pp), "
              f"declines a spread one")

        # --- B4 race filters, independently toggleable ---
        race = {"race_class": "Maiden Plate", "field_size": 18, "going": "Good"}
        os.environ["STRIDE_RACE_FILTER_MAIDEN"] = "true"
        r = race_type_filter(race)
        assert r["declined"] is True and r["triggered"][0]["rule"] == "MAIDEN"
        assert len(r["triggered"]) == 1, "only the enabled rule may fire"
        os.environ["STRIDE_RACE_FILTER_BIG_FIELD"] = "true"
        r2 = race_type_filter(race)
        assert {t["rule"] for t in r2["triggered"]} == {"MAIDEN", "BIG_FIELD"}, r2
        assert race_type_filter({"race_class": "Group 1", "field_size": 10,
                                 "going": "Good"})["declined"] is False
        print(f"  race_filter: rules fire independently "
              f"({[t['rule'] for t in r2['triggered']]}), clean race passes")
    finally:
        for k in list(os.environ):
            if k.startswith("STRIDE_"):
                os.environ.pop(k, None)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

    assert min_probability_gate(picks)["enabled"] is False, "env must be restored"
    print("  env restored: policies inert again")
    print("All tests completed successfully.")


if __name__ == "__main__":
    _self_test()
