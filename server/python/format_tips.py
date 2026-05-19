#!/usr/bin/env python3
"""Format tips JSON for display."""
import json, sys

with open(sys.argv[1]) as f:
    data = json.load(f)

races = data.get("races", [])
tracks = {}
for race in races:
    t = race.get("track", "?")
    if t not in tracks:
        tracks[t] = []
    tracks[t].append(race)

for track in sorted(tracks.keys()):
    rlist = tracks[track]
    print(f"\n=== {track} ({len(rlist)} races) ===")
    rlist.sort(key=lambda x: x.get("race_number", 0))
    for race in rlist:
        rn = race.get("race_number", "?")
        rname = race.get("race_name", "")
        dist = race.get("distance", "")
        going = race.get("going", "")
        fs = race.get("field_size", "")
        rc = race.get("race_class", "")
        picks = race.get("top_picks", [])
        print(f"\n  R{rn} {rname} {dist}m ({going}) {rc} | {fs} runners")
        for h in picks[:3]:
            name = h.get("horse", "?")
            odds = h.get("odds", 0)
            win_pct = h.get("win_pct", 0)
            edge = h.get("edge_pct", 0)
            conf = h.get("confidence", "?")
            score = h.get("selection_score", 0)
            raw = h.get("raw_model_pct", 0)
            staking = h.get("staking", "")
            vr = h.get("value_rating", "")
            barrier = h.get("barrier", "")
            jockey = h.get("jockey", "")
            print(f"    {h.get('rank','-')}. {name:22s} ${odds:<6} Win {win_pct:5.1f}%  Edge {edge:+5.1f}%  {conf.upper():6s}  Score {score}  [{staking}]")
            print(f"       Barrier {barrier} | {jockey} | Raw MC {raw:.1f}% | {vr}")

print("\n" + "=" * 60)
print("BEST BETS")
print("=" * 60)
for b in data.get("best_bets", []):
    print(f"  {b.get('track','')} R{b.get('race_number','')} {b.get('horse','').upper()} ${b.get('odds',0)} Win {b.get('win_pct',0):.1f}% Edge {b.get('edge_pct',0):+.1f}% [{b.get('confidence','')}] {b.get('staking','')}")
if not data.get("best_bets"):
    print("  No strong best bets today")

print("\n" + "=" * 60)
print("VALUE PLAYS")
print("=" * 60)
for v in data.get("value_plays", []):
    print(f"  {v.get('track','')} R{v.get('race_number','')} {v.get('horse','').upper()} ${v.get('odds',0)} Win {v.get('win_pct',0):.1f}% Edge {v.get('edge_pct',0):+.1f}%")
if not data.get("value_plays"):
    print("  No value plays identified")

print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
s = data.get("summary", {})
for k, v in s.items():
    print(f"  {k}: {v}")
