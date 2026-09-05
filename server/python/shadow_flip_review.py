#!/usr/bin/env python3
"""Gate-3 flip review: the registered shadow-flip criteria, computed.

docs/roi-roadmap/shadow-flip-criteria.md fixes, per dark-launched flag, the
evidence required to flip it. gate_status.py counted the evidence DAYS, but
nothing computed the criteria themselves — the per-day delta distribution,
the top-3 flip rate against the 15% bar, dirty-day detection, the pooled
Brier comparison, the tier-transition rate and the single-race sign-off list.
A flip review therefore meant reading every day file by hand (23 of each on
2026-09-04). This module reads the durable evidence store (evidence_store:
S3 plus the local logs/ cache) and prints one verdict per registered
criterion.

Read-only by default. --emit-evidence writes the review record
(flip_review_<flag>_<date>.json) to the store, where gate_status gate 3
requires an auto_verdict of PASS before a flipped flag counts as flipped ON
EVIDENCE rather than merely flipped.

Every threshold is the registered one, quoted from the criteria document
next to its constant. Nothing here is tuned. A criterion the document leaves
to human judgement (delta stability, the transition matrix, the largest-delta
races) is reported as REVIEW with the numbers, never auto-passed: the human
flip is the approval act, and this tool only makes sure it is an informed one.

Statuses:
  PASS    the registered bar is measured and met
  FAIL    the registered bar is measured and missed
  WAIT    not enough evidence to measure (absence, not defeat)
  REVIEW  the document assigns this call to a human; the numbers are printed

Usage:
    python shadow_flip_review.py                   # both flags, read-only
    python shadow_flip_review.py --flag serve      # STRIDE_SERVE_LIVE_FEATURES
    python shadow_flip_review.py --flag renorm     # STRIDE_RENORMALISE_FIELD
    python shadow_flip_review.py --emit-evidence   # also write review records
    python shadow_flip_review.py --json
    python shadow_flip_review.py --self-test       # synthetic; no store, no DB
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ---------------------------------------------------------------------------
# Registered thresholds (docs/roi-roadmap/shadow-flip-criteria.md). Quoted.
# ---------------------------------------------------------------------------

# "The common floor: >= 5 race days of shadow data" (both flags).
MIN_CLEAN_DAYS = 5
# STRIDE_SERVE_LIVE_FEATURES #4: "Top-3 flip rate <= 15%".
TOP3_FLIP_RATE_MAX = 0.15
# STRIDE_SERVE_LIVE_FEATURES #3: "no single race whose deltas are an order of
# magnitude off the window's". An order of magnitude is a factor of ten.
OUTLIER_FACTOR = 10.0
# STRIDE_RENORMALISE_FIELD #3: "<= 5% of shadow runners transitioning in
# aggregate, with any single race above 25% requiring explicit sign-off".
TRANSITION_RATE_MAX = 0.05
SINGLE_RACE_TRANSITION_MAX = 0.25
# STRIDE_RENORMALISE_FIELD #2: "Field sums = 1.0 +/- 1e-6 on every published
# race" — the same constant shadow_calibrator_compare enforces.
SUM_TOLERANCE = 1e-6
# How many largest-delta races to hand the reviewer ("Sage reviews the
# largest-delta races by name before flipping").
N_LARGEST_DELTA_RACES = 10

SERVE_STEM = "serve_liveness_shadow"
RENORM_STEM = "calibrator_shadow"
RENORM_POOLED = "calibrator_compare_pooled.json"
RENORM_PAIR = "current__current_renormalised"

REVIEW_STEMS = {
    "serve": "flip_review_serve_live_features",
    "renorm": "flip_review_renormalise_field",
}
FLAG_NAMES = {
    "serve": "STRIDE_SERVE_LIVE_FEATURES",
    "renorm": "STRIDE_RENORMALISE_FIELD",
}

PASS, FAIL, WAIT, REVIEW = "PASS", "FAIL", "WAIT", "REVIEW"


def _c(name: str, status: str, detail: str, **extra: Any) -> Dict[str, Any]:
    row = {"criterion": name, "status": status, "detail": detail}
    row.update(extra)
    return row


def _streak(per_day: List[Dict[str, Any]]) -> int:
    """Consecutive clean days ending at the most recent day. A dirty day
    restarts the count (criteria doc #2: 'restarts the 5-day count')."""
    n = 0
    for d in reversed(per_day):
        if d["dirty"]:
            break
        n += 1
    return n


def _days_criterion(per_day: List[Dict[str, Any]]) -> Dict[str, Any]:
    streak = _streak(per_day)
    dirty = [d["date"] for d in per_day if d["dirty"]]
    if streak >= MIN_CLEAN_DAYS:
        status = PASS
    elif per_day and per_day[-1]["dirty"]:
        status = FAIL   # the most recent day is dirty: the count stands at zero
    else:
        status = WAIT   # still accruing (a dirty day may have restarted the count)
    return _c(
        "clean_days",
        status,
        f"{streak} consecutive clean day(s) ending {per_day[-1]['date'] if per_day else 'n/a'} "
        f"(need {MIN_CLEAN_DAYS}); {len(per_day)} day file(s) in store; "
        f"dirty days: {dirty or 'none'}",
        streak=streak, n_days=len(per_day), dirty_days=dirty,
    )


def auto_verdict(criteria: List[Dict[str, Any]]) -> str:
    statuses = {c["status"] for c in criteria}
    if FAIL in statuses:
        return FAIL
    if WAIT in statuses:
        return WAIT
    return PASS


# ---------------------------------------------------------------------------
# STRIDE_SERVE_LIVE_FEATURES
# ---------------------------------------------------------------------------

def summarise_serve_day(d: str, blocks: Any) -> Dict[str, Any]:
    """One serve_liveness_shadow_<date>.json: a list of race blocks, each
    {track, race_number, runners: [{horse, legacy_prob_pct, live_prob_pct,
    delta_pp, legacy_rank, live_rank, tier_change}]} (run_tips_pipeline.
    _write_serve_liveness_shadow)."""
    info: Dict[str, Any] = {"date": d, "dirty": False, "reason": None,
                            "n_races": 0, "n_runners": 0, "tier_changes": 0,
                            "mean_delta_pp": None, "std_delta_pp": None,
                            "max_abs_delta_pp": None, "races": []}
    if blocks is None:
        info.update(dirty=True, reason="day file missing or unreadable")
        return info
    if not isinstance(blocks, list):
        info.update(dirty=True, reason="day file is not a list of race blocks")
        return info
    deltas: List[float] = []
    for b in blocks:
        runners = b.get("runners") if isinstance(b, dict) else None
        if not runners:
            continue
        race_deltas: List[float] = []
        race_changes = 0
        for r in runners:
            try:
                dp = float(r["delta_pp"])
            except (KeyError, TypeError, ValueError):
                continue
            race_deltas.append(dp)
            race_changes += int(bool(r.get("tier_change")))
        if not race_deltas:
            continue
        info["n_races"] += 1
        info["n_runners"] += len(race_deltas)
        info["tier_changes"] += race_changes
        deltas.extend(race_deltas)
        info["races"].append({
            "date": d,
            "track": str(b.get("track", "")),
            "race_number": b.get("race_number"),
            "n_runners": len(race_deltas),
            "max_abs_delta_pp": round(max(abs(x) for x in race_deltas), 2),
            "tier_changes": race_changes,
        })
    if info["n_runners"] == 0:
        info.update(dirty=True, reason="no scored runners in the day file")
        return info
    arr = np.asarray(deltas, dtype=float)
    info.update(mean_delta_pp=round(float(arr.mean()), 3),
                std_delta_pp=round(float(arr.std()), 3),
                max_abs_delta_pp=round(float(np.abs(arr).max()), 2))
    return info


def review_serve_liveness(days: Dict[str, Any]) -> Dict[str, Any]:
    per_day = [summarise_serve_day(d, days[d]) for d in sorted(days)]
    clean = [d for d in per_day if not d["dirty"]]
    criteria = [_days_criterion(per_day)]

    # #2 "No day errored to legacy." The store can show a missing, empty or
    # unreadable day. It cannot show a single race whose live path raised and
    # fell back — that is a stderr line ("[FEATURES] shadow log write failed")
    # on the task, and the race is simply absent from the file. Said here so
    # the PASS is read for what it is.
    dirty = [d for d in per_day if d["dirty"]]
    criteria.append(_c(
        "no_errored_day",
        PASS if not dirty else (FAIL if per_day and per_day[-1]["dirty"] else REVIEW),
        (f"{len(dirty)} dirty day(s): "
         + "; ".join(f"{d['date']}: {d['reason']}" for d in dirty)
         if dirty else "every day file in the store parsed and carried scored runners"),
        limitation=("a race whose live path raised is absent from its day file "
                    "and visible only in that task's stderr; check the "
                    "'[FEATURES] shadow log write failed' line count per day"),
        dirty_days=[d["date"] for d in dirty],
    ))

    # #3 stability: per-day table plus the document's one quantitative
    # clause — no race an order of magnitude off the window. Trend and regime
    # shift are the reviewer's read (REVIEW), never auto-passed.
    races = [r for d in clean for r in d["races"]]
    outliers: List[Dict[str, Any]] = []
    window_median = None
    if len(races) >= 3:
        per_race_max = np.asarray([r["max_abs_delta_pp"] for r in races], dtype=float)
        window_median = float(np.median(per_race_max))
        if window_median > 0:
            outliers = [r for r in races
                        if r["max_abs_delta_pp"] > OUTLIER_FACTOR * window_median]
    largest = sorted(races, key=lambda r: -r["max_abs_delta_pp"])[:N_LARGEST_DELTA_RACES]
    day_table = [{k: d[k] for k in ("date", "n_races", "n_runners", "mean_delta_pp",
                                    "std_delta_pp", "max_abs_delta_pp", "tier_changes")}
                 for d in clean]
    if not clean:
        st3, det3 = WAIT, "no clean day to summarise"
    elif outliers:
        st3 = FAIL
        det3 = (f"{len(outliers)} race(s) with max |delta| > {OUTLIER_FACTOR:g}x the "
                f"window median ({window_median:.2f} pp): "
                + ", ".join(f"{o['date']} {o['track']} R{o['race_number']} "
                            f"{o['max_abs_delta_pp']} pp" for o in outliers[:5]))
    else:
        st3 = REVIEW
        det3 = (f"no order-of-magnitude race outlier (window median per-race max "
                f"|delta| {window_median:.2f} pp)" if window_median is not None
                else "fewer than 3 races — outlier rule not applicable")
        det3 += "; trend / regime shift across the per-day table is the reviewer's call"
    criteria.append(_c("delta_stability", st3, det3, per_day=day_table,
                       outlier_races=outliers, largest_delta_races=largest,
                       window_median_race_max_abs_delta_pp=window_median))

    # #4 top-3 flip rate over the clean days.
    n_runners = sum(d["n_runners"] for d in clean)
    n_changes = sum(d["tier_changes"] for d in clean)
    if n_runners == 0:
        criteria.append(_c("top3_flip_rate", WAIT, "no scored runners", rate=None))
    else:
        rate = n_changes / n_runners
        criteria.append(_c(
            "top3_flip_rate",
            PASS if rate <= TOP3_FLIP_RATE_MAX else FAIL,
            f"{n_changes}/{n_runners} runners changed top-3 membership = "
            f"{rate:.3%} (bar <= {TOP3_FLIP_RATE_MAX:.0%})",
            rate=rate, n_changes=n_changes, n_runners=n_runners,
        ))

    return {
        "flag": FLAG_NAMES["serve"],
        "criteria_source": "docs/roi-roadmap/shadow-flip-criteria.md#STRIDE_SERVE_LIVE_FEATURES",
        "n_days": len(per_day),
        "n_clean_days": len(clean),
        "criteria": criteria,
        "auto_verdict": auto_verdict(criteria),
        "review_items": [
            "delta_stability: per-day mean/spread/max show no trend or regime shift",
            f"largest-delta races reviewed by name ({len(largest)} listed)",
            "deploy together with STRIDE_SERVE_NAN_CONTRACT=true",
        ],
    }


# ---------------------------------------------------------------------------
# STRIDE_RENORMALISE_FIELD
# ---------------------------------------------------------------------------

def _variant(report: Optional[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    if not isinstance(report, dict):
        return None
    v = report.get("variants")
    if not isinstance(v, dict):
        return None
    return v.get(name)


def summarise_renorm_day(d: str, payload: Any) -> Dict[str, Any]:
    """One calibrator_shadow_<date>.json: {date, day: compare-report,
    window_interpretation, pooled_window} (shadow_calibrator_compare.
    emit_evidence)."""
    info: Dict[str, Any] = {"date": d, "dirty": False, "reason": None,
                            "n_rows": 0, "n_races": 0, "brier_current": None,
                            "brier_renormalised": None, "sums_within_tolerance": None,
                            "n_compared": 0, "n_transitions": 0}
    day = payload.get("day") if isinstance(payload, dict) else None
    if not isinstance(day, dict):
        info.update(dirty=True, reason="day file missing, unreadable or without a 'day' report")
        return info
    if day.get("status") != "ok" or not day.get("n_rows"):
        info.update(dirty=True, reason=f"day report status={day.get('status')!r}, n_rows={day.get('n_rows')}")
        return info
    cur, ren = _variant(day, "current"), _variant(day, "current_renormalised")
    if not cur or not ren:
        info.update(dirty=True, reason="day report lacks the current/current_renormalised variants")
        return info
    pair = ((day.get("tier_transitions") or {}).get("pairs") or {}).get(RENORM_PAIR) or {}
    info.update(n_rows=int(day["n_rows"]), n_races=int(day.get("n_races") or 0),
                brier_current=cur.get("brier"), brier_renormalised=ren.get("brier"),
                sums_within_tolerance=(ren.get("race_sums") or {}).get("within_tolerance"),
                n_compared=int(pair.get("n_compared") or 0),
                n_transitions=int(pair.get("n_transitions") or 0))
    return info


def review_renormalisation(days: Dict[str, Any], pooled: Any) -> Dict[str, Any]:
    per_day = [summarise_renorm_day(d, days[d]) for d in sorted(days)]
    clean = [d for d in per_day if not d["dirty"]]
    criteria = [_days_criterion(per_day)]

    # #2 pooled Brier and field sums. The pooled report is the registered
    # quantity ("pooled, not best-day"). Brier is a mean over rows, so the
    # row-weighted mean of day Briers is the same number when the pooled
    # file is absent; the source is named either way.
    cur_p, ren_p = _variant(pooled, "current"), _variant(pooled, "current_renormalised")
    source = "calibrator_compare_pooled.json"
    if not (cur_p and ren_p):
        rows = sum(d["n_rows"] for d in clean)
        if rows:
            b_cur = sum(d["brier_current"] * d["n_rows"] for d in clean) / rows
            b_ren = sum(d["brier_renormalised"] * d["n_rows"] for d in clean) / rows
            within = all(bool(d["sums_within_tolerance"]) for d in clean)
            source = "row-weighted aggregate of day files (pooled file absent)"
        else:
            b_cur = b_ren = None
            within = None
    else:
        b_cur, b_ren = float(cur_p["brier"]), float(ren_p["brier"])
        rs = ren_p.get("race_sums") or {}
        within = rs.get("within_tolerance")
    if b_cur is None:
        criteria.append(_c("pooled_brier_not_worse", WAIT, "no pooled or day Brier available"))
        criteria.append(_c("field_sums_unity", WAIT, "no race-sum data available"))
    else:
        criteria.append(_c(
            "pooled_brier_not_worse",
            PASS if b_ren <= b_cur else FAIL,
            f"renormalised Brier {b_ren:.6f} vs current {b_cur:.6f} ({source})",
            brier_current=b_cur, brier_renormalised=b_ren, source=source,
        ))
        criteria.append(_c(
            "field_sums_unity",
            PASS if within else FAIL,
            (f"every renormalised race sums to 1.0 +/- {SUM_TOLERANCE:g}" if within
             else f"a renormalised race sum is outside 1.0 +/- {SUM_TOLERANCE:g} ({source})"),
            within_tolerance=bool(within), source=source,
        ))

    # #3 tier transitions: aggregate rate, then the single-race rule.
    pair = ((pooled or {}).get("tier_transitions") or {}).get("pairs", {}).get(RENORM_PAIR) \
        if isinstance(pooled, dict) else None
    if pair and pair.get("n_compared"):
        n_cmp, n_tr = int(pair["n_compared"]), int(pair["n_transitions"])
        tr_source = "pooled"
        races_detail = pair.get("races") or []
    else:
        n_cmp = sum(d["n_compared"] for d in clean)
        n_tr = sum(d["n_transitions"] for d in clean)
        tr_source = "sum of day files"
        races_detail = []
    if n_cmp == 0:
        criteria.append(_c("transition_rate", WAIT, "no runner had a computable tier in both variants"))
    else:
        rate = n_tr / n_cmp
        criteria.append(_c(
            "transition_rate",
            PASS if rate <= TRANSITION_RATE_MAX else FAIL,
            f"{n_tr}/{n_cmp} compared runners changed confidence tier = {rate:.3%} "
            f"(bar <= {TRANSITION_RATE_MAX:.0%}, {tr_source})",
            rate=rate, n_transitions=n_tr, n_compared=n_cmp,
        ))
    signoff: List[Dict[str, Any]] = []
    proxy_used = False
    for r in races_detail:
        n_t = len(r.get("transitions") or [])
        n_r = r.get("n_compared") or r.get("n_runners")
        if n_r:
            frac = n_t / float(n_r)
            if frac > SINGLE_RACE_TRANSITION_MAX:
                signoff.append({"race_key": r.get("race_key"), "n_transitions": n_t,
                                "n_runners": n_r, "fraction": round(frac, 3)})
        else:
            # Evidence emitted before per-race field sizes were recorded
            # (shadow_calibrator_compare.tier_transitions, 2026-09-05).
            # Three moved runners is the conservative stand-in: it exceeds
            # 25% of any field of 11 or fewer.
            proxy_used = True
            if n_t >= 3:
                signoff.append({"race_key": r.get("race_key"), "n_transitions": n_t,
                                "n_runners": None, "fraction": None})
    criteria.append(_c(
        "single_race_signoff",
        REVIEW if signoff else (PASS if races_detail or n_cmp else WAIT),
        (f"{len(signoff)} race(s) above {SINGLE_RACE_TRANSITION_MAX:.0%} of runners "
         f"transitioning require explicit sign-off" if signoff
         else "no race above the single-race bar")
        + ("; per-race field sizes absent from this evidence — >=3 moved runners "
           "used as the conservative proxy; the daily calibrator-coverage job "
           "rewrites the files with n_runners" if proxy_used else ""),
        races=signoff, proxy_used=proxy_used,
    ))
    criteria.append(_c(
        "transition_matrix_reviewed", REVIEW,
        "Sage reviews the matrix for pathology: mass demotions, one direction "
        "dominating, clustering in one track/class",
        matrix=(pair or {}).get("matrix") if pair else None,
    ))

    return {
        "flag": FLAG_NAMES["renorm"],
        "criteria_source": "docs/roi-roadmap/shadow-flip-criteria.md#STRIDE_RENORMALISE_FIELD",
        "n_days": len(per_day),
        "n_clean_days": len(clean),
        "criteria": criteria,
        "auto_verdict": auto_verdict(criteria),
        "review_items": [
            "transition matrix reviewed for pathology",
            "single-race sign-off list cleared",
        ],
    }


# ---------------------------------------------------------------------------
# Store access (injectable so the review functions stay pure)
# ---------------------------------------------------------------------------

def _load_json(text: Optional[str]) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def load_days(stem: str,
              list_dates: Optional[Callable[[str], List[str]]] = None,
              fetch: Optional[Callable[[str], Optional[str]]] = None) -> Dict[str, Any]:
    from evidence_store import fetch_evidence, list_evidence_dates
    list_dates = list_dates or list_evidence_dates
    fetch = fetch or fetch_evidence
    return {d: _load_json(fetch(f"{stem}_{d}.json")) for d in list_dates(stem)}


def load_pooled(fetch: Optional[Callable[[str], Optional[str]]] = None) -> Any:
    from evidence_store import fetch_evidence
    return _load_json((fetch or fetch_evidence)(RENORM_POOLED))


def run_review(which: str, list_dates=None, fetch=None) -> Dict[str, Any]:
    if which == "serve":
        report = review_serve_liveness(load_days(SERVE_STEM, list_dates, fetch))
    elif which == "renorm":
        report = review_renormalisation(load_days(RENORM_STEM, list_dates, fetch),
                                        load_pooled(fetch))
    else:
        raise ValueError(which)
    report["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return report


def emit_review(which: str, report: Dict[str, Any], on: Optional[date] = None) -> Dict[str, Any]:
    """Write flip_review_<flag>_<date>.json — the strict stem_date pattern
    evidence_store.list_evidence_dates counts, so gate 3 can find the latest."""
    from evidence_store import put_evidence
    d = (on or datetime.now(timezone.utc).date()).isoformat()
    return put_evidence(f"{REVIEW_STEMS[which]}_{d}.json",
                        json.dumps(report, indent=2, default=str))


def latest_review(which: str, list_dates=None, fetch=None) -> Optional[Dict[str, Any]]:
    """The most recent review record for a flag, or None. Used by gate 3."""
    from evidence_store import fetch_evidence, list_evidence_dates
    list_dates = list_dates or list_evidence_dates
    fetch = fetch or fetch_evidence
    stem = REVIEW_STEMS[which]
    dates = list_dates(stem)
    if not dates:
        return None
    return _load_json(fetch(f"{stem}_{dates[-1]}.json"))


def render(report: Dict[str, Any]) -> str:
    lines = ["", f"=== {report['flag']} — flip review ({report['criteria_source']}) ===",
             f"days in store: {report['n_days']}  clean: {report['n_clean_days']}"]
    for c in report["criteria"]:
        lines.append(f"  [{c['status']:<6}] {c['criterion']:<26} {c['detail']}")
        if c["criterion"] == "delta_stability":
            for d in c.get("per_day") or []:
                lines.append(f"           {d['date']}  races {d['n_races']:>3}  runners {d['n_runners']:>4}  "
                             f"mean {d['mean_delta_pp']:>7}  sd {d['std_delta_pp']:>7}  "
                             f"max|d| {d['max_abs_delta_pp']:>7}  tier changes {d['tier_changes']}")
            for r in c.get("largest_delta_races") or []:
                lines.append(f"           largest: {r['date']} {r['track']} R{r['race_number']} "
                             f"max|d| {r['max_abs_delta_pp']} pp, {r['tier_changes']} tier change(s)")
        if c["criterion"] == "transition_matrix_reviewed" and c.get("matrix"):
            lines.append(f"           matrix: {c['matrix']}")
        if c["criterion"] == "single_race_signoff":
            for r in c.get("races") or []:
                lines.append(f"           sign-off: {r}")
    lines.append(f"AUTO VERDICT: {report['auto_verdict']}  "
                 f"(REVIEW items are the human's: {'; '.join(report['review_items'])})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Self-test (synthetic; no store, no DB)
# ---------------------------------------------------------------------------

def _synthetic_serve_day(rng, d: str, n_races: int = 8, scale: float = 1.0,
                         flip_every: int = 40) -> List[Dict[str, Any]]:
    blocks = []
    k = 0
    for rn in range(1, n_races + 1):
        runners = []
        n = int(rng.integers(8, 13))
        for i in range(n):
            legacy = float(rng.uniform(3, 30))
            delta = float(rng.normal(0, 2.0 * scale))
            k += 1
            runners.append({"horse": f"H{rn}_{i}", "legacy_prob_pct": round(legacy, 2),
                            "live_prob_pct": round(legacy + delta, 2),
                            "delta_pp": round(delta, 2), "legacy_rank": i + 1,
                            "live_rank": i + 1, "tier_change": (k % flip_every == 0)})
        blocks.append({"track": "Synthetic", "race_number": rn, "runners": runners})
    return blocks


def _synthetic_renorm_day(d: str, brier_cur: float, brier_ren: float, n_rows: int,
                          n_compared: int, n_transitions: int) -> Dict[str, Any]:
    day = {"status": "ok", "n_rows": n_rows, "n_races": max(1, n_rows // 10),
           "variants": {
               "current": {"brier": brier_cur, "race_sums": {"within_tolerance": False}},
               "current_renormalised": {"brier": brier_ren,
                                        "race_sums": {"within_tolerance": True,
                                                      "max_abs_dev": 1e-9}}},
           "tier_transitions": {"available": True, "base": "current",
                                "pairs": {RENORM_PAIR: {"n_compared": n_compared,
                                                        "n_transitions": n_transitions,
                                                        "matrix": {"medium>low": n_transitions}}}}}
    return {"date": d, "day": day}


def _self_test() -> None:
    print("=" * 60)
    print("shadow_flip_review self-test (synthetic)")
    print("=" * 60)
    rng = np.random.default_rng(7)
    dates = [f"2026-08-{d:02d}" for d in range(10, 19)]  # 9 days

    # --- serve: 9 clean days, ~2.5% flips -> PASS on the auto criteria
    days = {d: _synthetic_serve_day(rng, d) for d in dates}
    rep = review_serve_liveness(days)
    by = {c["criterion"]: c for c in rep["criteria"]}
    assert by["clean_days"]["status"] == PASS and by["clean_days"]["streak"] == 9, by["clean_days"]
    assert by["no_errored_day"]["status"] == PASS
    assert by["delta_stability"]["status"] == REVIEW, by["delta_stability"]["detail"]
    assert by["top3_flip_rate"]["status"] == PASS and by["top3_flip_rate"]["rate"] <= TOP3_FLIP_RATE_MAX
    assert rep["auto_verdict"] == PASS, rep["auto_verdict"]
    print(f"  serve clean window: verdict {rep['auto_verdict']}, flip rate {by['top3_flip_rate']['rate']:.3%}")

    # --- serve: a dirty day (empty file) three days from the end cuts the streak to 2 -> WAIT
    days2 = dict(days)
    days2[dates[-3]] = []
    rep2 = review_serve_liveness(days2)
    by2 = {c["criterion"]: c for c in rep2["criteria"]}
    assert by2["clean_days"]["streak"] == 2 and by2["clean_days"]["status"] == WAIT, by2["clean_days"]
    assert by2["no_errored_day"]["dirty_days"] == [dates[-3]]
    assert rep2["auto_verdict"] == WAIT
    print(f"  serve dirty day restarts the count: streak {by2['clean_days']['streak']}, verdict {rep2['auto_verdict']}")

    # --- serve: an unreadable most-recent day -> FAIL on no_errored_day
    days3 = dict(days)
    days3[dates[-1]] = None
    rep3 = review_serve_liveness(days3)
    assert {c["criterion"]: c["status"] for c in rep3["criteria"]}["no_errored_day"] == FAIL
    assert rep3["auto_verdict"] == FAIL

    # --- serve: flip rate exactly at the bar passes, just above fails
    runners = [{"horse": str(i), "delta_pp": 1.0, "tier_change": i < 3}
               for i in range(20)]             # 3/20 = 15.0%
    at_bar = {dates[0]: [{"track": "T", "race_number": 1, "runners": runners}]}
    r_at = review_serve_liveness(at_bar)
    assert {c["criterion"]: c for c in r_at["criteria"]}["top3_flip_rate"]["status"] == PASS
    runners[3]["tier_change"] = True      # 4/20 = 20%
    r_over = review_serve_liveness(at_bar)
    assert {c["criterion"]: c for c in r_over["criteria"]}["top3_flip_rate"]["status"] == FAIL
    print("  serve top-3 flip bar: 15.0% passes, 20.0% fails")

    # --- serve: one race an order of magnitude off the window -> FAIL on stability
    days4 = json.loads(json.dumps(days))   # deep copy: `days` is reused below
    bad = days4[dates[4]][0]["runners"]
    for r in bad:
        r["delta_pp"] = round(r["delta_pp"] * 60.0, 2)
    rep4 = review_serve_liveness(days4)
    st = {c["criterion"]: c for c in rep4["criteria"]}["delta_stability"]
    assert st["status"] == FAIL and st["outlier_races"], st["detail"]
    print(f"  serve outlier race detected: {st['outlier_races'][0]['max_abs_delta_pp']} pp vs median {st['window_median_race_max_abs_delta_pp']:.2f}")

    # --- renorm: pooled Brier better, sums within tolerance, 3% transitions -> PASS
    rdays = {d: _synthetic_renorm_day(d, 0.0850, 0.0845, 300, 280, 8) for d in dates}
    pooled = {"status": "ok", "n_rows": 2700,
              "variants": {"current": {"brier": 0.0850, "race_sums": {"within_tolerance": False}},
                           "current_renormalised": {"brier": 0.0845,
                                                    "race_sums": {"within_tolerance": True, "max_abs_dev": 3e-9}}},
              "tier_transitions": {"available": True, "base": "current",
                                   "pairs": {RENORM_PAIR: {"n_compared": 2520, "n_transitions": 76,
                                                           "matrix": {"medium>low": 50, "low>medium": 26},
                                                           "races": [
                                                               {"race_key": ["2026-08-12", "randwick", "5"],
                                                                "n_runners": 8, "n_compared": 8,
                                                                "transitions": [{}, {}, {}]},   # 37.5% -> sign-off
                                                               {"race_key": ["2026-08-13", "flemington", "2"],
                                                                "n_runners": 12, "n_compared": 12,
                                                                "transitions": [{}, {}]},       # 16.7% -> fine
                                                           ]}}}}
    rrep = review_renormalisation(rdays, pooled)
    rby = {c["criterion"]: c for c in rrep["criteria"]}
    assert rby["clean_days"]["status"] == PASS
    assert rby["pooled_brier_not_worse"]["status"] == PASS and rby["pooled_brier_not_worse"]["source"].startswith("calibrator_compare_pooled")
    assert rby["field_sums_unity"]["status"] == PASS
    assert rby["transition_rate"]["status"] == PASS and abs(rby["transition_rate"]["rate"] - 76 / 2520) < 1e-9
    assert rby["single_race_signoff"]["status"] == REVIEW and len(rby["single_race_signoff"]["races"]) == 1
    assert rby["single_race_signoff"]["races"][0]["fraction"] == 0.375
    assert not rby["single_race_signoff"]["proxy_used"]
    assert rrep["auto_verdict"] == PASS, rrep["auto_verdict"]
    print(f"  renorm: verdict {rrep['auto_verdict']}, transition rate {rby['transition_rate']['rate']:.3%}, 1 race to sign off")

    # --- renorm: pooled file absent -> row-weighted aggregate of day files
    rrep2 = review_renormalisation(rdays, None)
    rby2 = {c["criterion"]: c for c in rrep2["criteria"]}
    assert rby2["pooled_brier_not_worse"]["source"].startswith("row-weighted")
    assert abs(rby2["pooled_brier_not_worse"]["brier_renormalised"] - 0.0845) < 1e-12
    assert rby2["transition_rate"]["n_compared"] == 280 * 9

    # --- renorm: worse Brier fails; legacy race detail without n_runners uses the proxy
    pooled_bad = json.loads(json.dumps(pooled))
    pooled_bad["variants"]["current_renormalised"]["brier"] = 0.0860
    for r in pooled_bad["tier_transitions"]["pairs"][RENORM_PAIR]["races"]:
        r.pop("n_runners"); r.pop("n_compared")
    rrep3 = review_renormalisation(rdays, pooled_bad)
    rby3 = {c["criterion"]: c for c in rrep3["criteria"]}
    assert rby3["pooled_brier_not_worse"]["status"] == FAIL
    assert rby3["single_race_signoff"]["proxy_used"] and len(rby3["single_race_signoff"]["races"]) == 1
    assert rrep3["auto_verdict"] == FAIL
    print("  renorm: worse pooled Brier fails; pre-2026-09-05 evidence falls back to the >=3 proxy")

    # --- store roundtrip through a temp local dir (no bucket)
    import tempfile
    from pathlib import Path
    import evidence_store
    with tempfile.TemporaryDirectory() as tmp:
        saved_bucket = os.environ.pop("STRIDE_EVIDENCE_BUCKET", None)
        saved_local = evidence_store.local_dir
        evidence_store.local_dir = lambda: Path(tmp)
        try:
            for d in dates:
                evidence_store.put_evidence(f"{SERVE_STEM}_{d}.json", json.dumps(days[d]))
            loaded = load_days(SERVE_STEM)
            assert sorted(loaded) == dates and all(isinstance(v, list) for v in loaded.values())
            rep_s = run_review("serve")
            out = emit_review("serve", rep_s, on=date(2026, 9, 5))
            assert out["local"] and out["local"].endswith("flip_review_serve_live_features_2026-09-05.json")
            assert evidence_store.list_evidence_dates(REVIEW_STEMS["serve"]) == ["2026-09-05"]
            got = latest_review("serve")
            assert got and got["auto_verdict"] == PASS and got["flag"] == FLAG_NAMES["serve"]
            # a pooled/summary file must never inflate the day count
            evidence_store.put_evidence("flip_review_serve_live_features_summary.json", "{}")
            assert evidence_store.list_evidence_dates(REVIEW_STEMS["serve"]) == ["2026-09-05"]
        finally:
            evidence_store.local_dir = saved_local
            if saved_bucket is not None:
                os.environ["STRIDE_EVIDENCE_BUCKET"] = saved_bucket
    print("  store roundtrip: review record written and found as the latest")
    print(render(rep))
    print("All shadow_flip_review self-tests passed.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Gate-3 shadow-flip criteria review (read-only by default).")
    ap.add_argument("--flag", choices=("serve", "renorm", "both"), default="both")
    ap.add_argument("--emit-evidence", action="store_true",
                    help="write flip_review_<flag>_<date>.json to the evidence store")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        _self_test()
        return 0
    which = ("serve", "renorm") if args.flag == "both" else (args.flag,)
    reports = {}
    for w in which:
        try:
            reports[w] = run_review(w)
        except Exception as e:  # an unreadable store is a loud WAIT, never a silent zero
            reports[w] = {"flag": FLAG_NAMES[w], "error": f"{type(e).__name__}: {e}",
                          "auto_verdict": WAIT, "criteria": [], "review_items": [],
                          "n_days": 0, "n_clean_days": 0,
                          "criteria_source": "docs/roi-roadmap/shadow-flip-criteria.md"}
        if args.emit_evidence and "error" not in reports[w]:
            out = emit_review(w, reports[w])
            reports[w]["emitted"] = out
    if args.json:
        print(json.dumps(reports, indent=2, default=str))
    else:
        for w in which:
            r = reports[w]
            if "error" in r:
                print(f"\n=== {r['flag']} — EVIDENCE STORE UNREADABLE: {r['error']}")
            else:
                print(render(r))
                if r.get("emitted"):
                    print(f"  review record: {r['emitted'].get('s3') or r['emitted'].get('local')}")
    return 0 if all(r["auto_verdict"] == PASS for r in reports.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
