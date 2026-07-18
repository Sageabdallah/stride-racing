#!/usr/bin/env python3
"""
Day-level aggregate blocks of the tips document — the single source of truth
for `best_bets`, `value_plays`, `bankers`, `summary`, `selection_contract`
and `convergence_summary`, shared by the full-day build and the per-track
merge in run_tips_pipeline.

Why this module exists: the canonical `racecards/tips_<date>.json` is
assembled by two code paths — a full-day run, and `merge_tips_into_canonical`
when a single track is re-run (late scratchings, a failed meeting).
Historically the merge path rebuilt only `summary` and `best_bets`, so a
per-track re-run shipped a canonical file whose `selection_contract` counted
the re-run track only (the exact mismatch `validate_tips.py` flags as an
ERROR) and whose `value_plays` / `bankers` / `convergence_summary` silently
dropped every other track's entries. The two paths also disagreed on what a
best bet is (docs/09 promises rank-1 picks; only the merge path enforced it)
and the merge path lost the race-predictability ordering weights. Building
every block here, from the race entries alone, makes the paths unable to
drift again.

Stdlib-only by design: CI imports and self-tests this module without the
pipeline's database/scientific dependencies (`python tips_day_aggregates.py`).
"""

import json
import os
import sys

CONTRACT_VERSION = "v2-explicit-bet-coverage"


def best_bet_sort_score(pick):
    """
    Ordering key for best_bets. Default: the historical pure selection-score
    order. With STRIDE_PREDICTABILITY_GATE=true, the score is weighted by
    the race's predictability confidence modifier (0.5–1.2) so best bets
    prefer races where the top pick is most likely to be right. Bet/NO_BET
    decisions, edges and stakes are never affected — ordering only.
    """
    score = pick.get("selection_score", 0) or 0
    if os.environ.get("STRIDE_PREDICTABILITY_GATE", "false").strip().lower() in ("true", "1", "yes"):
        score = score * (pick.get("_predictability_mod", 1.0) or 1.0)
    return score


def collect_day_picks(race_tips):
    """
    Flatten every race's top_picks into day-level pick copies, each carrying
    its race context: track, race_number, and the race's predictability
    fields (score, category, and the `_predictability_mod` that
    best_bet_sort_score reads). Reading the modifier from the race entry —
    not from pipeline-local state — is what lets merged files keep the
    predictability ordering for races loaded back from disk.
    """
    all_picks = []
    for rt in race_tips:
        _rp = rt.get("predictability") or {}
        for p in rt.get("top_picks", []) or []:
            all_picks.append({
                **p,
                "track": rt.get("track"),
                "race_number": rt.get("race_number"),
                "race_predictability": _rp.get("score"),
                "race_predictability_category": _rp.get("category"),
                "_predictability_mod": _rp.get("confidence_modifier", 1.0) or 1.0,
            })
    return all_picks


def select_best_bets(all_picks):
    """
    Top 3 high-confidence rank-1 picks by best_bet_sort_score; if there are
    none, top 2 medium-confidence positive-edge rank-1 picks. Rank-1 only:
    a best bet is always its race's top pick — a rank-2/3 pick promoted
    above its own race leader would contradict the race card it links to.
    """
    ranked = sorted(
        [p for p in all_picks if p.get("rank") == 1],
        key=best_bet_sort_score,
        reverse=True,
    )
    best = [p for p in ranked if p.get("confidence") == "high"][:3]
    if not best:
        best = [
            p for p in ranked
            if p.get("confidence") == "medium" and (p.get("edge_pct") or 0) > 0
        ][:2]
    return best


def select_value_plays(all_picks):
    """Rank-1 picks with edge > 3% at $4–$15, top 5 by edge."""
    return sorted(
        [
            p for p in all_picks
            if (p.get("edge_pct") or 0) > 3
            and 4 <= (p.get("odds") or 0) <= 15
            and p.get("rank") == 1
        ],
        key=lambda x: x.get("edge_pct") or 0,
        reverse=True,
    )[:5]


def select_bankers(all_picks):
    """High-confidence picks at odds ≤ $4 (any rank, historical behaviour)."""
    return [
        p for p in all_picks
        if p.get("confidence") == "high" and (p.get("odds") or 999) <= 4
    ]


def _staking_units(pick):
    staking = pick.get("staking") or "0u"
    if staking == "0u":
        return 0
    try:
        return int(str(staking).replace("u", ""))
    except (TypeError, ValueError):
        return 0


def build_summary(race_tips, all_picks, total_races=None, mc_time=0, db_time=0, llm_time=0):
    """The day-level counters block. Rank-1 picks only, as always."""
    rank1 = [p for p in all_picks if p.get("rank") == 1]
    return {
        "total_races": len(race_tips) if total_races is None else total_races,
        "total_selections": len(rank1),
        "positive_edge": sum(1 for p in rank1 if (p.get("edge_pct") or 0) > 0),
        "high_confidence": sum(1 for p in rank1 if p.get("confidence") == "high"),
        "total_units": sum(_staking_units(p) for p in rank1),
        "mc_time_seconds": mc_time,
        "db_time_seconds": db_time,
        "llm_time_seconds": llm_time,
    }


def build_selection_contract(race_tips):
    """
    BET/NO_BET race counts over exactly the races in the document — the same
    counting rule validate_tips.py asserts, so a file built here satisfies
    the invariant by construction. ERROR races have no bet_pick and count as
    NO_BET, matching the validator.
    """
    bet_races = sum(1 for r in race_tips if r.get("bet_pick"))
    return {
        "version": CONTRACT_VERSION,
        "bet_races": bet_races,
        "no_bet_races": len(race_tips) - bet_races,
    }


def build_convergence_summary(all_picks, pillars_available, crowd_overrides_tracked):
    """
    Pick-derived convergence counters. `crowd_overrides_tracked` is passed in
    because the full-day run counts it over the per-runner convergence rows,
    which are not serialized into the tips file — a merged file approximates
    it at pick level; the authoritative row-level record is the
    convergence_output table.
    """
    return {
        "confirmed": sum(1 for p in all_picks if p.get("crowd_classification") == "CONFIRMED"),
        "crowd_only": sum(1 for p in all_picks if p.get("crowd_classification") in ("CROWD_ONLY", "CROWD_ONLY_WEAK")),
        "model_only": sum(1 for p in all_picks if p.get("crowd_classification") == "MODEL_ONLY"),
        "rejected": sum(1 for p in all_picks if p.get("crowd_classification") == "REJECTED"),
        "crowd_overrides_tracked": crowd_overrides_tracked,
        "gated_no_bet": sum(1 for p in all_picks if p.get("convergence_gate")),
        "pillars_available": pillars_available,
        "stake_distribution": {
            "full": sum(1 for p in all_picks if p.get("stake_recommendation") == "FULL"),
            "standard": sum(1 for p in all_picks if p.get("stake_recommendation") == "STANDARD"),
            "reduced": sum(1 for p in all_picks if p.get("stake_recommendation") == "REDUCED"),
        },
    }


def merge_tips_into_canonical(new_output, canonical_path, track_filter):
    """
    Merge new track-specific results into an existing canonical tips file.

    Replaces only races whose track matches the filter; preserves all others.
    Every day-level block — summary, best_bets, value_plays, bankers,
    selection_contract, convergence_summary — is then rebuilt over the
    merged race list, so a per-track re-run can never ship contract counts
    or headline lists that cover only the re-run track. The new run's
    engine timings are carried through. Returns the merged output dict.
    """
    if not canonical_path.exists() or not track_filter:
        return new_output

    try:
        with canonical_path.open("r", encoding="utf-8") as f:
            existing = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [MERGE] Could not read existing file, overwriting: {e}", file=sys.stderr)
        return new_output

    tf_lower = {t.lower() for t in track_filter}

    def _track_matches_filter(track_name):
        tn = (track_name or "").lower()
        return any(tf in tn or tn in tf for tf in tf_lower)

    kept_races = [
        r for r in existing.get("races", [])
        if not _track_matches_filter(r.get("track", ""))
    ]

    merged_races = kept_races + new_output.get("races", [])
    new_output["races"] = merged_races

    all_picks = collect_day_picks(merged_races)

    prior_summary = new_output.get("summary", {}) or {}
    new_output["summary"] = build_summary(
        merged_races, all_picks,
        mc_time=prior_summary.get("mc_time_seconds", 0),
        db_time=prior_summary.get("db_time_seconds", 0),
        llm_time=prior_summary.get("llm_time_seconds", 0),
    )
    new_output["best_bets"] = select_best_bets(all_picks)
    new_output["value_plays"] = select_value_plays(all_picks)
    new_output["bankers"] = select_bankers(all_picks)
    new_output["selection_contract"] = build_selection_contract(merged_races)

    new_conv = new_output.get("convergence_summary")
    old_conv = existing.get("convergence_summary")
    if new_conv or old_conv:
        pillars = (
            (new_conv or {}).get("pillars_available")
            or (old_conv or {}).get("pillars_available")
            or {}
        )
        overrides = sum(1 for p in all_picks if p.get("convergence_tier") == "CROWD_ONLY_WEAK")
        new_output["convergence_summary"] = build_convergence_summary(all_picks, pillars, overrides)

    print(
        f"  [MERGE] {len(merged_races) - len(kept_races)} new + {len(kept_races)} existing = {len(merged_races)} total races",
        file=sys.stderr,
    )
    return new_output


# ---------------------------------------------------------------------------
# Self-test (stdlib only — runs in CI)
# ---------------------------------------------------------------------------

def _mk_pick(horse, rank, confidence, edge, odds, score, staking="0u", **extra):
    p = {
        "horse": horse, "rank": rank, "confidence": confidence,
        "edge_pct": edge, "odds": odds, "selection_score": score,
        "staking": staking, "should_bet": False,
    }
    p.update(extra)
    return p


def _mk_race(track, rn, picks, bet=True, predictability=None):
    leader = picks[0] if picks else None
    race = {
        "track": track, "race_number": rn, "race_name": f"Race {rn}",
        "top_picks": picks,
        "raw_model_leader": leader,
        "coverage_pick": leader,
        "bet_pick": leader if (bet and leader) else None,
        "bet_status": "BET" if (bet and leader) else "NO_BET",
        "full_field": [],
    }
    if predictability:
        race["predictability"] = predictability
    return race


def _self_test():
    import tempfile
    from pathlib import Path

    print("Running tips_day_aggregates self-tests...")

    # --- collect_day_picks attaches race context and predictability ---
    r = _mk_race(
        "Randwick", 1,
        [_mk_pick("Alpha", 1, "high", 4.0, 5.0, 80, "2u")],
        predictability={"score": 55, "category": "normal", "confidence_modifier": 0.6},
    )
    picks = collect_day_picks([r])
    assert picks[0]["track"] == "Randwick" and picks[0]["race_number"] == 1
    assert picks[0]["race_predictability"] == 55
    assert picks[0]["_predictability_mod"] == 0.6
    no_pred = collect_day_picks([_mk_race("Sale", 2, [_mk_pick("Beta", 1, "low", 0, 10.0, 40)])])
    assert no_pred[0]["_predictability_mod"] == 1.0
    print("  collect_day_picks: race context + predictability attached")

    # --- best bets: rank-1 only, gate flag flips ordering ---
    day = [
        _mk_race("Randwick", 1,
                 [_mk_pick("Alpha", 1, "high", 4.0, 5.0, 80, "2u"),
                  _mk_pick("Sneaky", 2, "high", 2.0, 6.0, 95, "2u")],
                 predictability={"score": 30, "category": "chaotic", "confidence_modifier": 0.6}),
        _mk_race("Flemington", 1,
                 [_mk_pick("Bravo", 1, "high", 3.0, 4.5, 60, "2u")],
                 predictability={"score": 80, "category": "highly_predictable", "confidence_modifier": 1.2}),
    ]
    all_picks = collect_day_picks(day)

    prev_flag = os.environ.pop("STRIDE_PREDICTABILITY_GATE", None)
    try:
        best = select_best_bets(all_picks)
        assert [b["horse"] for b in best] == ["Alpha", "Bravo"], best
        assert all(b["rank"] == 1 for b in best), "rank-2 pick leaked into best_bets"

        os.environ["STRIDE_PREDICTABILITY_GATE"] = "true"
        best_gated = select_best_bets(all_picks)
        # 80×0.6=48 vs 60×1.2=72 — the predictable race's pick leads
        assert [b["horse"] for b in best_gated] == ["Bravo", "Alpha"], best_gated
    finally:
        if prev_flag is None:
            os.environ.pop("STRIDE_PREDICTABILITY_GATE", None)
        else:
            os.environ["STRIDE_PREDICTABILITY_GATE"] = prev_flag
    print("  select_best_bets: rank-1 only; predictability gate reorders when enabled")

    # --- fallback: no high picks → top 2 medium positive-edge rank-1 ---
    med_day = [
        _mk_race("Sale", 1, [_mk_pick("M1", 1, "medium", 2.0, 6.0, 70, "1u")]),
        _mk_race("Sale", 2, [_mk_pick("M2", 1, "medium", 1.0, 7.0, 60, "1u")]),
        _mk_race("Sale", 3, [_mk_pick("M3", 1, "medium", -1.0, 8.0, 90, "1u")]),
        _mk_race("Sale", 4, [_mk_pick("M4", 1, "low", 5.0, 9.0, 95)]),
    ]
    fallback = select_best_bets(collect_day_picks(med_day))
    assert [b["horse"] for b in fallback] == ["M1", "M2"], fallback
    print("  select_best_bets: medium fallback excludes negative edge and low confidence")

    # --- summary counters ---
    summary = build_summary(day, all_picks, mc_time=9.1, db_time=2.2, llm_time=3.3)
    assert summary["total_races"] == 2 and summary["total_selections"] == 2
    assert summary["high_confidence"] == 2 and summary["positive_edge"] == 2
    assert summary["total_units"] == 4  # rank-1 stakes only: 2u + 2u
    assert summary["db_time_seconds"] == 2.2 and summary["llm_time_seconds"] == 3.3
    assert _staking_units({"staking": "junk"}) == 0
    print("  build_summary: rank-1 counters and safe staking parse")

    # --- selection contract matches the validator's counting rule ---
    error_race = {"track": "Sale", "race_number": 9, "race_name": "Race 9",
                  "error": "boom", "top_picks": [], "full_field": [],
                  "bet_status": "ERROR",
                  "raw_model_leader": None, "coverage_pick": None}
    contract_day = day + [_mk_race("Sale", 5, [_mk_pick("NB", 1, "low", 0.5, 12.0, 50)], bet=False), error_race]
    contract = build_selection_contract(contract_day)
    assert contract["version"] == CONTRACT_VERSION
    assert contract["bet_races"] == 2 and contract["no_bet_races"] == 2
    assert contract["bet_races"] + contract["no_bet_races"] == len(contract_day)
    print("  build_selection_contract: BET/NO_BET partition covers every race incl. errors")

    # --- convergence summary ---
    conv_picks = [
        _mk_pick("C1", 1, "high", 4.0, 5.0, 80, "2u",
                 crowd_classification="CONFIRMED", stake_recommendation="FULL",
                 convergence_tier="CONFIRMED"),
        _mk_pick("C2", 1, "medium", 2.0, 6.0, 60, "1u",
                 crowd_classification="CROWD_ONLY_WEAK", stake_recommendation="REDUCED",
                 convergence_tier="CROWD_ONLY_WEAK", convergence_gate="crowd weak"),
    ]
    conv = build_convergence_summary(conv_picks, {"stride": True}, crowd_overrides_tracked=7)
    assert conv["confirmed"] == 1 and conv["crowd_only"] == 1
    assert conv["gated_no_bet"] == 1 and conv["crowd_overrides_tracked"] == 7
    assert conv["stake_distribution"] == {"full": 1, "standard": 0, "reduced": 1}
    print("  build_convergence_summary: pick-derived counters")

    # --- merge: the regression this module exists to prevent ---
    with tempfile.TemporaryDirectory() as td:
        canonical_path = Path(td) / "tips_2026-07-18.json"

        rand_pick = _mk_pick("KeptValue", 1, "high", 5.0, 6.0, 85, "2u",
                             crowd_classification="CONFIRMED", stake_recommendation="FULL",
                             convergence_tier="CONFIRMED")
        rand_banker = _mk_pick("KeptBanker", 1, "high", 2.0, 3.5, 75, "2u")
        flem_old = _mk_pick("StaleFlem", 1, "high", 4.0, 7.0, 90, "2u")
        day1_races = [
            _mk_race("Randwick", 1, [rand_pick]),
            _mk_race("Randwick", 2, [rand_banker]),
            _mk_race("Flemington", 1, [flem_old]),
        ]
        day1_picks = collect_day_picks(day1_races)
        canonical = {
            "date": "2026-07-18", "generated_at": "t0",
            "races": day1_races,
            "best_bets": select_best_bets(day1_picks),
            "value_plays": select_value_plays(day1_picks),
            "bankers": select_bankers(day1_picks),
            "summary": build_summary(day1_races, day1_picks, mc_time=100),
            "selection_contract": build_selection_contract(day1_races),
            "convergence_summary": build_convergence_summary(
                day1_picks, {"stride": True, "consensus": True, "market": False}, 0),
        }
        canonical_path.write_text(json.dumps(canonical), encoding="utf-8")

        flem_new = _mk_pick("FreshFlem", 1, "medium", 3.5, 5.0, 65, "1u")
        new_races = [_mk_race("Flemington", 1, [flem_new], bet=False)]
        new_picks = collect_day_picks(new_races)
        new_output = {
            "date": "2026-07-18", "generated_at": "t1",
            "races": new_races,
            "best_bets": select_best_bets(new_picks),
            "value_plays": select_value_plays(new_picks),
            "bankers": select_bankers(new_picks),
            "summary": build_summary(new_races, new_picks, mc_time=10.0, db_time=2.0, llm_time=3.0),
            "selection_contract": build_selection_contract(new_races),
            "convergence_summary": build_convergence_summary(new_picks, {"stride": True}, 0),
        }

        merged = merge_tips_into_canonical(new_output, canonical_path, ["flemington"])

        tracks = sorted({r["track"] for r in merged["races"]})
        assert tracks == ["Flemington", "Randwick"] and len(merged["races"]) == 3
        assert not any(p["horse"] == "StaleFlem" for r in merged["races"] for p in r["top_picks"])

        # Contract covers the MERGED set (pre-fix: new track only → validator ERROR)
        assert merged["selection_contract"]["bet_races"] == 2
        assert merged["selection_contract"]["no_bet_races"] == 1

        # Kept tracks' value plays and bankers survive (pre-fix: dropped)
        assert any(p["horse"] == "KeptValue" for p in merged["value_plays"])
        assert any(p["horse"] == "KeptBanker" for p in merged["bankers"])

        # Best bets: rank-1, merged-wide; new-run timings carried through
        assert [p["horse"] for p in merged["best_bets"]] == ["KeptValue", "KeptBanker"]
        assert merged["summary"]["total_races"] == 3 and merged["summary"]["total_selections"] == 3
        assert merged["summary"]["db_time_seconds"] == 2.0 and merged["summary"]["llm_time_seconds"] == 3.0

        # Convergence summary rebuilt over merged picks
        assert merged["convergence_summary"]["confirmed"] == 1
        assert merged["convergence_summary"]["pillars_available"] == {"stride": True}

        # The merged file passes the repo's own validator with zero errors/warnings
        merged_path = Path(td) / "tips_merged.json"
        merged_path.write_text(json.dumps(merged, default=str), encoding="utf-8")
        from validate_tips import validate_file
        result = validate_file(merged_path)
        assert result["errors"] == [], result["errors"]
        assert result["warnings"] == [], result["warnings"]
        print("  merge: contract/value_plays/bankers/summary rebuilt over merged races; validator PASS")

        # Missing canonical or empty filter → new output returned untouched
        assert merge_tips_into_canonical(new_output, Path(td) / "nope.json", ["flemington"]) is new_output
        assert merge_tips_into_canonical(new_output, canonical_path, None) is new_output
        print("  merge: no-canonical and no-filter passthroughs intact")

    print("All tips_day_aggregates self-tests passed.")


if __name__ == "__main__":
    _self_test()
