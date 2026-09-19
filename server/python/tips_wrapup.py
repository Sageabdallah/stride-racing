#!/usr/bin/env python3
"""Render the Saturday wrap-up from a tips_<date>.json artifact.

Plain text, computed entirely from the file: nothing here is an opinion, and
nothing is added that the artifact does not contain. The layout is the one the
operator asked for on 2026-09-19 (header, READ THIS FIRST, top ten by win
probability, best bets, value plays, every race by track, timing).

    python tips_wrapup.py tips_2026-09-19.json \
        [--landed "13:31"] [--landed-iso 2026-09-19T03:31:04+00:00] \
        [--last-week tips_2026-09-12.json]
    python tips_wrapup.py --self-test

stdlib only. Every field is read with .get and tolerated when absent, because
the artifact schema has grown by accretion (run_tips_pipeline.py:3566 builds
the race entry; :3290 builds each full_field runner; backfill_tips_contract.py
adds bet_status / bet_pick / coverage_pick).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

RULE = "=" * 78
TIPS_START_SYDNEY = "08:05"   # infra/07b_fargate_schedules.sh: stride-tips-0805


# --- small helpers ----------------------------------------------------------

def _num(v, default=None):
    try:
        if v is None:
            return default
        f = float(v)
        if f != f:  # NaN
            return default
        return f
    except (TypeError, ValueError):
        return default


def _price(v) -> str:
    f = _num(v)
    if not f or f <= 1.0:
        return "no price"
    s = f"{f:.2f}"
    if s.endswith("0"):          # $5.60 -> $5.6, $4.00 -> $4.0, $2.52 stays
        s = s[:-1]
    return f"${s}"


def _pct(v, signed=False) -> str:
    f = _num(v)
    if f is None:
        return "n/a"
    return f"{f:+.1f}%" if signed else f"{f:.1f}%"


def _upper(name) -> str:
    return str(name or "Unknown").strip().upper()


def _title(name) -> str:
    return str(name or "Unknown").strip()


def _sydney():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Australia/Sydney")
    except Exception:  # noqa: BLE001 - tzdata absent; AEST fallback
        return timezone(timedelta(hours=10))


def _parse_generated_at(raw):
    """The pipeline stamps datetime.now().isoformat() on a UTC container clock."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_sydney())


def _leading_pick(race: dict):
    """The race's headline runner: the bet when there is one, else the
    coverage/primary pick, else the rank-1 tip, else the raw model leader."""
    for key in ("bet_pick", "primary_pick", "coverage_pick"):
        p = race.get(key)
        if isinstance(p, dict) and p.get("horse"):
            return p
    tops = race.get("top_picks") or []
    if tops and isinstance(tops[0], dict):
        return tops[0]
    p = race.get("raw_model_leader")
    return p if isinstance(p, dict) else None


def _bet_line(p: dict, with_tier=True) -> str:
    parts = [f"{_title(p.get('track'))} R{p.get('race_number', '?')}",
             _upper(p.get("horse")), _price(p.get("odds")),
             f"win {_pct(p.get('win_pct'))}", f"edge {_pct(p.get('edge_pct'), signed=True)}",
             str(p.get("confidence") or "n/a")]
    if with_tier:
        parts.append(str(p.get("staking") or "0u"))
        parts.append(str(p.get("convergence_tier") or p.get("selection_origin") or "n/a"))
    return " ".join(parts)


# --- the report --------------------------------------------------------------

def render(tips: dict, *, path: str = "", landed: str = "", landed_iso: str = "",
           last_week: dict | None = None) -> str:
    date = str(tips.get("date") or "unknown date")
    races = [r for r in (tips.get("races") or []) if isinstance(r, dict)]
    summary = tips.get("summary") or {}
    tracks = sorted({_title(r.get("track")) for r in races if r.get("track")})
    runners = sum(len(r.get("full_field") or []) or int(_num(r.get("field_size"), 0) or 0)
                  for r in races)
    bet_races = [r for r in races if r.get("bet_status") == "BET"]
    no_bet = [r for r in races if r.get("bet_status") == "NO_BET"]
    errors = [r for r in races if r.get("error") or r.get("bet_status") == "ERROR"]
    n, b = len(races), len(bet_races)

    out: list[str] = []
    out.append(f"STRIDE tips for {date}")
    out.append(f"{n} races across {len(tracks)} tracks: {', '.join(tracks) if tracks else 'none'}")
    out.append(f"{runners} runners scored. {b} bet races, {len(no_bet)} gated to NO_BET."
               + (f" {len(errors)} race(s) errored." if errors else ""))
    out.append("")

    # READ THIS FIRST: computed statements only.
    if b == 0:
        first = (f"READ THIS FIRST. The system backed nothing today. Every one of the {n} races "
                 f"gated to NO_BET, so nothing below is a bet instruction.")
    else:
        first = (f"READ THIS FIRST. The system backed {b} of {n} races today. Only the lines "
                 f"under BET RACES are bet instructions; the other {n - b} races gated to NO_BET.")
    first += (" The top ten is the ten runners with the highest win probability on the card, "
              "which is a different question from what is worth backing.")
    if last_week:
        lw_races = [r for r in (last_week.get("races") or []) if isinstance(r, dict)]
        lw_bets = sum(1 for r in lw_races if r.get("bet_status") == "BET")
        same = " was the same" if (lw_bets == b == 0) else ""
        first += (f" Last Saturday{same}: {len(lw_races)} races, "
                  f"{lw_bets} bet{'s' if lw_bets != 1 else ''}.")
    out.append(first)
    out.append("")

    if bet_races:
        out += [RULE, "BET RACES", RULE]
        for r in bet_races:
            p = r.get("bet_pick") or _leading_pick(r) or {}
            p = dict(p, track=p.get("track") or r.get("track"),
                     race_number=p.get("race_number") or r.get("race_number"))
            out.append(_bet_line(p))
            if r.get("bet_status_reason"):
                out.append(f"    {r.get('bet_status_reason')}")
        out.append("")

    # TOP TEN by published win probability across every runner on the card.
    field: list[dict] = []
    for r in races:
        for h in r.get("full_field") or []:
            if isinstance(h, dict) and _num(h.get("win_pct")) is not None:
                field.append(dict(h, _track=_title(r.get("track")),
                                  _race=r.get("race_number", "?"),
                                  _status=r.get("bet_status") or "n/a"))
    field.sort(key=lambda h: _num(h.get("win_pct"), 0.0), reverse=True)
    top = field[:10]
    out += [RULE, "TOP TEN OF THE DAY, BEST CHANCE FIRST", RULE]
    out.append("Ranked on the published win probability for every runner on the card.")
    out.append("The edge column is the value read: positive means the model rates the")
    out.append("horse better than the market prices it. Negative means the opposite.")
    out.append("")
    if not top:
        out.append("No runner-level data in the file.")
    for i, h in enumerate(top, 1):
        out.append(f"{i}. {_upper(h.get('horse'))} {h['_track']} R{h['_race']}")
        out.append(f"win {_pct(h.get('win_pct'))} place {_pct(h.get('place_pct'))} "
                   f"{_price(h.get('odds'))} fair {_price(h.get('fair_odds'))} "
                   f"edge {_pct(h.get('edge_pct'), signed=True)}")
        out.append(f"{_title(h.get('jockey')) or 'jockey n/a'}, barrier {h.get('barrier') or '?'}, "
                   f"confidence {h.get('confidence') or 'n/a'}, race is {h['_status']}")
        out.append("")
    if top:
        neg = [h for h in top if (_num(h.get("edge_pct"), 0.0) or 0.0) < 0]
        pos = [h for h in top if (_num(h.get("edge_pct"), 0.0) or 0.0) > 0]
        line = f"{len(neg)} of those {len(top)} carry negative edge."
        if pos:
            names = ", ".join(f"{_title(h.get('horse'))} (#{top.index(h) + 1})" for h in pos)
            line += f" Positive edge: {names}."
        out.append(line)
        out.append("")

    # BEST BETS / VALUE PLAYS / BANKERS: the file's own day-level blocks.
    out += [RULE, "BEST BETS, THE SYSTEM'S OWN RANKING BY VALUE", RULE]
    best = [p for p in (tips.get("best_bets") or []) if isinstance(p, dict)]
    if not best:
        out.append("None today")
    for p in best:
        out.append(_bet_line(p))
    if best:
        tiers: dict[str, int] = {}
        for p in best:
            t = str(p.get("convergence_tier") or p.get("selection_origin") or "n/a")
            tiers[t] = tiers.get(t, 0) + 1
        out.append("")
        out.append("Tiers: " + ", ".join(f"{k} x{v}" for k, v in sorted(tiers.items())) + ".")
        backed = sum(1 for p in best if p.get("should_bet") is True)
        out.append(f"{backed} of these {len(best)} carry should_bet=true in the file.")
    out.append("")
    out.append("VALUE PLAYS")
    plays = [p for p in (tips.get("value_plays") or []) if isinstance(p, dict)]
    if not plays:
        out.append("None today")
    for p in plays:
        out.append(_bet_line(p, with_tier=False))
    out.append("")
    out.append("BANKERS")
    bankers = [p for p in (tips.get("bankers") or []) if isinstance(p, dict)]
    if not bankers:
        out.append("None today")
    for p in bankers:
        out.append(_bet_line(p))
    out.append("")

    # EVERY RACE, grouped by track.
    out += [RULE, "EVERY RACE ON THE CARD", RULE]
    out.append("Each race, its status, and its leading pick.")
    out.append("")
    by_track: dict[str, list[dict]] = {}
    for r in races:
        by_track.setdefault(_title(r.get("track")), []).append(r)
    for track in sorted(by_track):
        out.append(f"-- {track} ".ljust(74, "-"))
        for r in sorted(by_track[track], key=lambda x: int(_num(x.get("race_number"), 0) or 0)):
            p = _leading_pick(r) or {}
            size = r.get("field_size") or len(r.get("full_field") or []) or "?"
            dist = str(r.get("distance") or "?").replace(" ", "")
            status = r.get("bet_status") or "n/a"
            if p:
                pick = (f"{_title(p.get('horse'))} {_price(p.get('odds'))} win {_pct(p.get('win_pct'))} "
                        f"edge {_pct(p.get('edge_pct'), signed=True)} {p.get('confidence') or 'n/a'}")
            else:
                pick = "no pick"
            out.append(f"R{r.get('race_number', '?')} {dist} {size}rnr {status} {pick}")
        out.append("")

    # TIMING and FLAGS: computed, never guessed.
    out += [RULE, "TIMING", RULE]
    gen = _parse_generated_at(tips.get("generated_at"))
    if gen:
        line = f"The pipeline is scheduled to start at {TIPS_START_SYDNEY} Sydney and wrote this file at {gen.strftime('%H:%M')} Sydney"
        try:
            start = gen.replace(hour=int(TIPS_START_SYDNEY[:2]), minute=int(TIPS_START_SYDNEY[3:]),
                                second=0, microsecond=0)
            secs = (gen - start).total_seconds()
            if secs > 0:
                h, m = divmod(int(secs // 60), 60)
                line += f", so {h}h {m:02d}m"
                if runners:
                    line += f" for {runners} runners, {secs / runners:.1f} seconds per runner"
        except ValueError:
            pass
        out.append(line + ".")
    else:
        out.append(f"generated_at could not be parsed: {tips.get('generated_at')!r}.")
    if landed:
        out.append(f"The file appeared in S3 at {landed} Sydney.")
    if path and os.path.exists(path):
        out.append(f"Source: {os.path.basename(path)}, {os.path.getsize(path) / 1e6:.2f} MB.")
    out.append("")

    flags = []
    if not races:
        flags.append("no races in file")
    if races and b == 0:
        flags.append("zero BET races")
    if not best:
        flags.append("best_bets empty")
    if _num(summary.get("positive_edge")) == 0:
        flags.append("positive_edge is 0")
    if gen and gen.strftime("%Y-%m-%d") != date:
        flags.append(f"generated_at date {gen.strftime('%Y-%m-%d')} differs from file date {date}")
    if landed_iso:
        try:
            li = datetime.fromisoformat(landed_iso).astimezone(_sydney())
            if li.strftime("%Y-%m-%d") == date and li.strftime("%H:%M") < "08:00":
                flags.append("file landed before 08:00 Sydney (possible stale file)")
            if li.strftime("%Y-%m-%d") != date:
                flags.append(f"file landed on {li.strftime('%Y-%m-%d')}, not on {date}")
        except ValueError:
            flags.append(f"landed time unparseable: {landed_iso}")
    if path and os.path.exists(path) and os.path.getsize(path) < 20_000:
        flags.append("file smaller than 20 KB")
    if errors:
        flags.append(f"{len(errors)} race(s) errored: " + ", ".join(
            f"{_title(r.get('track'))} R{r.get('race_number', '?')}" for r in errors[:10]))
    out.append("FLAGS: " + ("; ".join(flags) if flags else "none"))
    return "\n".join(out)


# --- self-test ----------------------------------------------------------------

def _synthetic() -> dict:
    def runner(name, win, place, odds, fair, edge, conf, jockey="J. Smith", barrier=3):
        return {"horse": name, "win_pct": win, "place_pct": place, "odds": odds,
                "fair_odds": fair, "edge_pct": edge, "confidence": conf,
                "jockey": jockey, "barrier": barrier}

    def pick(track, rn, name, odds, win, edge, conf, staking, tier, should_bet):
        return {"track": track, "race_number": rn, "horse": name, "odds": odds,
                "fair_odds": round(100 / win, 2), "win_pct": win, "edge_pct": edge,
                "confidence": conf, "staking": staking, "convergence_tier": tier,
                "should_bet": should_bet, "rank": 1}

    r1 = {"track": "Rosehill", "race_number": 1, "distance": "1200m", "field_size": 3,
          "bet_status": "BET", "bet_status_reason": "BET — crowd confirmed",
          "bet_pick": pick("Rosehill", 1, "Alpha", 4.0, 30.0, 5.0, "high", "2u", "CONFIRMED", True),
          "full_field": [runner("Alpha", 30.0, 60.0, 4.0, 3.33, 5.0, "high"),
                         runner("Bravo", 20.0, 50.0, 6.0, 5.0, -3.0, "low"),
                         runner("Charlie", 10.0, 30.0, 12.0, 10.0, 1.0, "")]}
    r2 = {"track": "Rosehill", "race_number": 2, "distance": "1400m", "field_size": 2,
          "bet_status": "NO_BET", "bet_status_reason": "MODEL_ONLY — no crowd support",
          "bet_pick": None,
          "primary_pick": pick("Rosehill", 2, "Delta", 8.0, 15.0, 2.5, "medium", "1u", "MODEL_ONLY", False),
          "full_field": [runner("Delta", 15.0, 40.0, 8.0, 6.67, 2.5, "medium"),
                         runner("Echo", 40.0, 70.0, 1.8, 2.5, -20.0, "low", "K. Lee", 1)]}
    r3 = {"track": "Doomben", "race_number": 5, "distance": "1615m", "field_size": 1,
          "bet_status": "NO_BET", "bet_status_reason": "archetype trap",
          "top_picks": [pick("Doomben", 5, "Foxtrot", 0, 12.0, 0.0, "low", "0u", "MODEL_ONLY", False)],
          "full_field": [runner("Foxtrot", 12.0, 35.0, 0, None, 0.0, "low")]}
    return {"date": "2026-09-19", "generated_at": "2026-09-19T03:31:04.123456",
            "summary": {"total_races": 3, "total_selections": 3, "positive_edge": 2,
                        "high_confidence": 1, "total_units": 3, "mc_time_seconds": 900.0},
            "best_bets": [r1["bet_pick"], r2["primary_pick"]],
            "value_plays": [r2["primary_pick"]],
            "bankers": [],
            "races": [r1, r2, r3]}


def _self_test() -> None:
    text = render(_synthetic(), landed="13:31", landed_iso="2026-09-19T03:31:30+00:00",
                  last_week={"races": [{"bet_status": "NO_BET"}] * 55})
    must = [
        "STRIDE tips for 2026-09-19",
        "3 races across 2 tracks: Doomben, Rosehill",
        "6 runners scored. 1 bet races, 2 gated to NO_BET.",
        "READ THIS FIRST. The system backed 1 of 3 races today.",
        "Last Saturday: 55 races, 0 bets.",
        "BET RACES",
        "Rosehill R1 ALPHA $4.0 win 30.0% edge +5.0% high 2u CONFIRMED",
        "1. ECHO Rosehill R2",                       # highest win% on the card, not a bet
        "win 40.0% place 70.0% $1.8 fair $2.5 edge -20.0%",
        "K. Lee, barrier 1, confidence low, race is NO_BET",
        "2. ALPHA Rosehill R1",
        "2 of those 6 carry negative edge.",
        "Tiers: CONFIRMED x1, MODEL_ONLY x1.",
        "1 of these 2 carry should_bet=true in the file.",
        "VALUE PLAYS",
        "Rosehill R2 DELTA $8.0 win 15.0% edge +2.5% medium",
        "BANKERS",
        "-- Doomben ",
        "R5 1615m 1rnr NO_BET Foxtrot no price win 12.0% edge +0.0% low",
        "-- Rosehill ",
        "R1 1200m 3rnr BET Alpha $4.0 win 30.0% edge +5.0% high",
        "R2 1400m 2rnr NO_BET Delta $8.0 win 15.0% edge +2.5% medium",
        "wrote this file at 13:31 Sydney, so 5h 26m for 6 runners",
        "The file appeared in S3 at 13:31 Sydney.",
        "FLAGS: none",
    ]
    for m in must:
        assert m in text, f"missing line: {m!r}\n---\n{text}"
    # An empty artifact renders without raising and says so.
    empty = render({"date": "2026-09-19", "races": [], "best_bets": []})
    assert "0 races across 0 tracks" in empty and "no races in file" in empty
    assert "No runner-level data in the file." in empty
    # A zero-bet card gets the "backed nothing" wording and the flag.
    zero = _synthetic()
    zero["races"][0]["bet_status"] = "NO_BET"
    zero["races"][0]["bet_pick"] = None
    z = render(zero)
    assert "The system backed nothing today. Every one of the 3 races gated to NO_BET" in z
    assert "zero BET races" in z
    print("tips_wrapup self-test: header, READ THIS FIRST, top ten, best bets, value plays, "
          "every race, timing, flags, empty and zero-bet cards PASS")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path", nargs="?", help="tips_<date>.json")
    ap.add_argument("--landed", default="", help="HH:MM Sydney the file appeared in S3")
    ap.add_argument("--landed-iso", default="", help="ISO timestamp of the S3 LastModified")
    ap.add_argument("--last-week", default="", help="last Saturday's tips json, for the comparison line")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        _self_test()
        return 0
    if not args.path:
        ap.error("path is required unless --self-test")
    with open(args.path, encoding="utf-8") as fh:
        tips = json.load(fh)
    last = None
    if args.last_week and os.path.exists(args.last_week):
        try:
            with open(args.last_week, encoding="utf-8") as fh:
                last = json.load(fh)
        except (OSError, ValueError):
            last = None
    print(render(tips, path=args.path, landed=args.landed, landed_iso=args.landed_iso,
                 last_week=last))
    return 0


if __name__ == "__main__":
    sys.exit(main())
