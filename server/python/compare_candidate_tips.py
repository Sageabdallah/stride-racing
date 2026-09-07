#!/usr/bin/env python3
"""Candidate-vs-live comparison for the parallel-scoring week.

docs/project_retrain_gate.md promises "racing_ensemble_v3.pkl beside v2, one
week parallel scoring". The mechanism (2026-09-05): the tips-proof job with
STRIDE_ENSEMBLE_ARTIFACT set scores the same card with the candidate, writes
nothing to the database, and relays tips_<date>_candidate.json. This module
puts that file next to the real tips_<date>.json and reports what the
candidate would have changed, race by race:

  top-1 agreement       same first pick (top_picks[0].horse)
  top-3 overlap         Jaccard of the two top-3 sets
  bet-pick agreement    same bet_pick horse (None == None counts as agreement)
  bet-status changes    BET -> NO_BET and back
  win% deltas           per shared runner in full_field: mean / max |delta|
  largest-delta races   for the reviewer, by name

CAVEAT, stated in every report: the real run scores at ~08:05 and a proof
runs when it is dispatched. Odds, scratchings and the market anchor move in
between, so a delta conflates the candidate with time-of-day drift unless the
proof is dispatched close to the real run. The comparison is a review aid for
the human promotion decision under 12-preregistration.md NEW-BEATS-OLD; it is
not itself a criterion and no threshold in here is registered.

Usage:
    python compare_candidate_tips.py racecards/tips_2026-09-20.json racecards/tips_2026-09-20_candidate.json
    python compare_candidate_tips.py LIVE CANDIDATE --json
    python compare_candidate_tips.py --self-test
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Tuple


def _norm(name: Any) -> str:
    return str(name or "").strip().lower()


def _race_key(race: Dict[str, Any]) -> Tuple[str, int]:
    try:
        rn = int(race.get("race_number") or 0)
    except (TypeError, ValueError):
        rn = 0
    return (_norm(race.get("track")), rn)


def _top(picks: Any, n: int) -> List[str]:
    if not isinstance(picks, list):
        return []
    ordered = sorted((p for p in picks if isinstance(p, dict)),
                     key=lambda p: (p.get("rank") is None, p.get("rank") or 0))
    return [_norm(p.get("horse")) for p in ordered[:n] if p.get("horse")]


def _bet_horse(race: Dict[str, Any]) -> Optional[str]:
    bp = race.get("bet_pick")
    return _norm(bp.get("horse")) if isinstance(bp, dict) and bp.get("horse") else None


def compare_race(live: Dict[str, Any], cand: Dict[str, Any]) -> Dict[str, Any]:
    l1, c1 = _top(live.get("top_picks"), 1), _top(cand.get("top_picks"), 1)
    l3, c3 = set(_top(live.get("top_picks"), 3)), set(_top(cand.get("top_picks"), 3))
    lb, cb = _bet_horse(live), _bet_horse(cand)
    live_ff = {_norm(r.get("horse")): r for r in (live.get("full_field") or []) if isinstance(r, dict)}
    cand_ff = {_norm(r.get("horse")): r for r in (cand.get("full_field") or []) if isinstance(r, dict)}
    deltas = []
    for h, lr in live_ff.items():
        cr = cand_ff.get(h)
        if cr is None:
            continue
        try:
            deltas.append((h, float(cr.get("win_pct", 0)) - float(lr.get("win_pct", 0))))
        except (TypeError, ValueError):
            continue
    abs_d = [abs(d) for _, d in deltas]
    return {
        "track": live.get("track"),
        "race_number": live.get("race_number"),
        "top1_live": l1[0] if l1 else None,
        "top1_candidate": c1[0] if c1 else None,
        "top1_agree": bool(l1 and c1 and l1[0] == c1[0]),
        "top3_jaccard": (len(l3 & c3) / len(l3 | c3)) if (l3 | c3) else None,
        "bet_live": lb,
        "bet_candidate": cb,
        "bet_agree": lb == cb,
        "bet_status_live": live.get("bet_status"),
        "bet_status_candidate": cand.get("bet_status"),
        "bet_status_changed": (live.get("bet_status") != cand.get("bet_status")),
        "n_shared_runners": len(deltas),
        "mean_abs_delta_win_pct": (sum(abs_d) / len(abs_d)) if abs_d else None,
        "max_abs_delta_win_pct": max(abs_d) if abs_d else None,
        "max_delta_runner": (max(deltas, key=lambda t: abs(t[1]))[0] if deltas else None),
    }


def compare(live: Dict[str, Any], cand: Dict[str, Any]) -> Dict[str, Any]:
    live_races = {_race_key(r): r for r in (live.get("races") or []) if isinstance(r, dict)}
    cand_races = {_race_key(r): r for r in (cand.get("races") or []) if isinstance(r, dict)}
    matched = sorted(set(live_races) & set(cand_races))
    rows = [compare_race(live_races[k], cand_races[k]) for k in matched]
    scored = [r for r in rows if r["top1_live"] and r["top1_candidate"]]
    jac = [r["top3_jaccard"] for r in rows if r["top3_jaccard"] is not None]
    mad = [r["mean_abs_delta_win_pct"] for r in rows if r["mean_abs_delta_win_pct"] is not None]
    largest = sorted((r for r in rows if r["max_abs_delta_win_pct"] is not None),
                     key=lambda r: -r["max_abs_delta_win_pct"])[:10]
    return {
        "date_live": live.get("date"),
        "date_candidate": cand.get("date"),
        "n_races_live": len(live_races),
        "n_races_candidate": len(cand_races),
        "n_races_matched": len(matched),
        "only_in_live": sorted(set(live_races) - set(cand_races)),
        "only_in_candidate": sorted(set(cand_races) - set(live_races)),
        "top1_agreement": (sum(r["top1_agree"] for r in scored) / len(scored)) if scored else None,
        "top3_mean_jaccard": (sum(jac) / len(jac)) if jac else None,
        "bet_pick_agreement": (sum(r["bet_agree"] for r in rows) / len(rows)) if rows else None,
        "bet_status_changes": sum(r["bet_status_changed"] for r in rows),
        "mean_abs_delta_win_pct": (sum(mad) / len(mad)) if mad else None,
        "largest_delta_races": largest,
        "races": rows,
        "caveat": ("the real run and the proof score at different times of day; odds, "
                   "scratchings and the market anchor move in between, so deltas "
                   "conflate the candidate with drift unless the proof was dispatched "
                   "close to the real run. Review aid only — not a registered criterion."),
    }


def render(rep: Dict[str, Any]) -> str:
    def f(v, pct=False):
        if v is None:
            return "n/a"
        return f"{v:.1%}" if pct else f"{v:.2f}"

    lines = [f"=== candidate vs live — {rep['date_live']} ===",
             f"races: live {rep['n_races_live']}, candidate {rep['n_races_candidate']}, "
             f"matched {rep['n_races_matched']}"
             + (f", only in live {rep['only_in_live']}" if rep["only_in_live"] else "")
             + (f", only in candidate {rep['only_in_candidate']}" if rep["only_in_candidate"] else ""),
             f"top-1 agreement {f(rep['top1_agreement'], True)} | top-3 mean Jaccard "
             f"{f(rep['top3_mean_jaccard'])} | bet-pick agreement {f(rep['bet_pick_agreement'], True)}"
             f" | bet-status changes {rep['bet_status_changes']} | mean |Δ win%| "
             f"{f(rep['mean_abs_delta_win_pct'])}"]
    for r in rep["largest_delta_races"]:
        lines.append(f"  {r['track']} R{r['race_number']}: max |Δ| {r['max_abs_delta_win_pct']:.1f}pp "
                     f"({r['max_delta_runner']}); top-1 {r['top1_live']} -> {r['top1_candidate']}"
                     f"{'' if r['top1_agree'] else ' CHANGED'}; bet {r['bet_status_live']} -> "
                     f"{r['bet_status_candidate']}")
    lines.append(f"CAVEAT: {rep['caveat']}")
    return "\n".join(lines)


def _self_test() -> None:
    print("compare_candidate_tips self-test")

    def race(track, rn, picks, bet, status, field):
        return {"track": track, "race_number": rn,
                "top_picks": [{"rank": i + 1, "horse": h} for i, h in enumerate(picks)],
                "bet_pick": ({"horse": bet} if bet else None), "bet_status": status,
                "full_field": [{"horse": h, "win_pct": w} for h, w in field]}

    live = {"date": "2026-09-20", "races": [
        race("Randwick", 1, ["A", "B", "C"], "A", "BET", [("A", 30.0), ("B", 20.0), ("C", 10.0), ("D", 5.0)]),
        race("Randwick", 2, ["E", "F", "G"], None, "NO_BET", [("E", 25.0), ("F", 20.0), ("G", 15.0)]),
        race("Flemington", 3, ["X", "Y", "Z"], "X", "BET", [("X", 40.0), ("Y", 10.0)]),
    ]}
    cand = {"date": "2026-09-20", "races": [
        race("randwick ", 1, ["B", "A", "C"], "B", "BET", [("A", 24.0), ("B", 26.0), ("C", 10.0), ("D", 5.0)]),
        race("Randwick", 2, ["E", "G", "F"], None, "NO_BET", [("E", 25.5), ("F", 19.5), ("G", 15.0)]),
        race("Caulfield", 7, ["Q"], None, "NO_BET", [("Q", 50.0)]),
    ]}
    rep = compare(live, cand)
    assert rep["n_races_matched"] == 2
    assert rep["only_in_live"] == [("flemington", 3)] and rep["only_in_candidate"] == [("caulfield", 7)]
    assert rep["top1_agreement"] == 0.5
    r1 = rep["races"][0]
    assert r1["top1_live"] == "a" and r1["top1_candidate"] == "b" and not r1["top1_agree"]
    assert r1["top3_jaccard"] == 1.0, "same three horses, different order"
    assert not r1["bet_agree"] and not r1["bet_status_changed"]
    assert r1["n_shared_runners"] == 4 and abs(r1["mean_abs_delta_win_pct"] - 3.0) < 1e-9
    assert r1["max_abs_delta_win_pct"] == 6.0 and r1["max_delta_runner"] == "a"
    r2 = rep["races"][1]
    assert r2["top1_agree"] and r2["bet_agree"] and r2["bet_status_live"] == "NO_BET"
    assert rep["bet_pick_agreement"] == 0.5 and rep["bet_status_changes"] == 0
    assert rep["largest_delta_races"][0]["race_number"] == 1
    assert "drift" in rep["caveat"]
    print(render(rep))

    empty = compare({"races": []}, {"races": []})
    assert empty["n_races_matched"] == 0 and empty["top1_agreement"] is None
    print("All compare_candidate_tips self-tests passed.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare a candidate proof's tips file with the live tips file.")
    ap.add_argument("live", nargs="?", help="racecards/tips_<date>.json")
    ap.add_argument("candidate", nargs="?", help="racecards/tips_<date>_candidate.json")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        _self_test()
        return 0
    if not (args.live and args.candidate):
        ap.error("pass LIVE and CANDIDATE tips files, or --self-test")
    with open(args.live, encoding="utf-8") as fh:
        live = json.load(fh)
    with open(args.candidate, encoding="utf-8") as fh:
        cand = json.load(fh)
    rep = compare(live, cand)
    print(json.dumps(rep, indent=2, default=str) if args.json else render(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
