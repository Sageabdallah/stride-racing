#!/usr/bin/env python3
"""DM-H2 probe: is Punting Form's `last10` frozen as-of the race card, or
regenerated as-of query time? Two subcommands, split by where credentials
live:

  fetch    (CI, GitHub-hosted runner) — PF fetches ONLY. Writes the raw
           meeting_detail payLoads to pf_last10_asof_artifacts/ for upload
           as a run artifact. Key arrives via PUNTINGFORM_API_KEY and is
           never printed.
  compare  (local Mac, read-only) — DB-side work where DATABASE_URL lives:
           Measurement A (fresh last10 head vs recorded finish AT the
           meeting vs finish of the PRIOR run, from race_results_history)
           and Measurement B (drift vs the archived pf_raw_payloads of
           2026-07-31; NOT-APPLICABLE if the archive holds no payload kind
           carrying last10). Prints the evidence table and writes it to
           pf_last10_asof_evidence.json. Zero writes to any table.

    python3 scripts/pf_last10_asof_probe.py fetch  <meetingId> [...]
    python3 scripts/pf_last10_asof_probe.py compare <artifact_dir> [meetingId ...]
"""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server", "python"))

OUT_DIR = "pf_last10_asof_artifacts"


# ------------------------------------------------------------------ fetch

def cmd_fetch(meeting_ids):
    from pf_client import meeting_detail
    os.makedirs(OUT_DIR, exist_ok=True)
    manifest = {"fetched_at": datetime.datetime.now(datetime.timezone.utc)
                .isoformat(timespec="seconds"),
                "endpoint": "meeting_detail (/form/meeting)",
                "meetings": {}}
    for mid in meeting_ids:
        payload = meeting_detail(mid)
        path = os.path.join(OUT_DIR, f"meeting_{mid}.json")
        with open(path, "w") as f:
            json.dump(payload, f, indent=2, default=str)
        races = (payload or {}).get("races") or []
        runners = sum(len(r.get("runners") or []) for r in races)
        manifest["meetings"][mid] = {"races": len(races), "runners": runners,
                                     "file": f"meeting_{mid}.json"}
        print(f"{mid}: {len(races)} races, {runners} runners -> {path}")
    with open(os.path.join(OUT_DIR, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    return 0


# ---------------------------------------------------------------- compare

def norm_name(name):
    from pf_backfill_results import norm_name as _n
    return _n(name)


def digits_only(form):
    """last10 -> finishing-position digit sequence, dropping spell/letter
    codes (x, f, p, ...). '0' is kept: PF encodes placings >9 as 0
    (verified against recorded finishes during decoding)."""
    return [c for c in str(form or "") if c.isdigit()]


def cmd_compare(artifact_dir, only_ids=()):
    import psycopg2

    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        print("ERROR: DATABASE_URL is required (prod READ approval covers "
              "this read-only compare)", file=sys.stderr)
        return 1

    files = sorted(f for f in os.listdir(artifact_dir)
                   if re.fullmatch(r"meeting_\d+\.json", f))
    if only_ids:
        files = [f for f in files if f[8:-5] in set(only_ids)]
    if not files:
        print(f"ERROR: no meeting_<id>.json payloads in {artifact_dir}",
              file=sys.stderr)
        return 1

    conn = psycopg2.connect(db_url)
    try:
        cur = conn.cursor()
        evidence, b_notes = [], []
        for fn in files:
            mid = fn[8:-5]
            with open(os.path.join(artifact_dir, fn)) as f:
                payload = json.load(f)
            races = payload.get("races") or []
            mdate = (payload.get("meetingDate") or "")[:10]
            track = (payload.get("track") or {}).get("name") or ""
            # recorded finishes AT this meeting (PF-written rows)
            cur.execute(
                "SELECT race_number, horse_name, position, horse_id "
                "FROM race_results_history "
                "WHERE race_id LIKE %s ORDER BY race_number, horse_name",
                (f"pf{mid}_%",))
            at_meeting = {}
            for race_no, hname, pos, hid in cur.fetchall():
                at_meeting[norm_name(hname)] = {
                    "race_number": race_no, "position": pos, "horse_id": hid,
                    "horse_name": hname}
            # Measurement B: archived payloads for this meetingId
            cur.execute("SELECT DISTINCT kind FROM pf_raw_payloads")
            kinds = [r[0] for r in cur.fetchall()]
            cur.execute(
                "SELECT kind, payload FROM pf_raw_payloads "
                "WHERE meeting_id = %s", (int(mid),))
            archived = cur.fetchall()
            b_notes.append({"meetingId": mid, "archive_kinds_in_table": kinds,
                            "archived_payloads_matched": len(archived),
                            "archived_carries_last10": any(
                                "last10" in json.dumps(p) for _, p in archived)})
            for race in races:
                rno = race.get("raceNumber")
                for runner in race.get("runners") or []:
                    name = runner.get("name")
                    last10 = runner.get("last10")
                    rec = at_meeting.get(norm_name(name))
                    if not rec or rec["position"] is None:
                        continue  # scratched / no recorded finish
                    # the run BEFORE this meeting (same horse_id)
                    cur.execute(
                        "SELECT position, race_date::text FROM "
                        "race_results_history WHERE horse_id = %s "
                        "AND race_date < %s ORDER BY race_date DESC LIMIT 1",
                        (rec["horse_id"], mdate))
                    prior = cur.fetchone()
                    evidence.append({
                        "meetingId": mid, "track": track, "date": mdate,
                        "race": rno, "runner": name,
                        "last10": last10,
                        "finish_at_meeting": rec["position"],
                        "prior_finish": prior[0] if prior else None,
                        "prior_date": prior[1] if prior else None,
                    })
        # --- decode & classify
        # Decoding established on this dataset (recorded in PUNTINGFORM_MIGRATION.md
        # F-FORM-ASOF): the RIGHTMOST character is the most recent run (94.7%
        # of decided discriminating runners vs 54.7% for leftmost); '0' means
        # a finish of 10th or worse; 'x' marks a spell; strings are
        # right-aligned, shorter strings = fewer runs on record.
        def enc(f):
            return None if f is None else ("0" if f >= 10 else str(f))
        for e in evidence:
            seq = digits_only(e["last10"])
            head = seq[-1] if seq else None
            m, p = enc(e["finish_at_meeting"]), enc(e["prior_finish"])
            e["head_rightmost"] = head
            e["discriminating"] = bool(
                m is not None and p is not None and m != p)
            if head is None:
                e["call"] = "no-form"
            elif e["discriminating"] and head == m:
                e["call"] = "ADVERSE"   # head == the meeting's own finish
            elif p is not None and head == p:
                e["call"] = "CLEAN"     # head == the prior run's finish
            elif m is not None and head == m:
                e["call"] = "consistent-both"  # m == p after 0-encoding
            else:
                e["call"] = "neither"
        out = {"evidence": evidence, "measurement_b": b_notes}
        with open("pf_last10_asof_evidence.json", "w") as f:
            json.dump(out, f, indent=2)
        # --- print the table + split
        hdr = f"{'runner':28} {'meet':>4} {'prior':>5} {'last10':>12} {'head':>4} {'call':>16}"
        print(hdr)
        for e in evidence:
            print(f"{e['runner'][:28]:28} {str(e['finish_at_meeting']):>4} "
                  f"{str(e['prior_finish']):>5} {str(e['last10'])[:12]:>12} "
                  f"{str(e['head_rightmost']):>4} {e['call']:>16}")
        disc = [e for e in evidence if e["discriminating"]]
        d_adv = sum(1 for e in disc if e["call"] == "ADVERSE")
        d_cln = sum(1 for e in disc if e["call"] == "CLEAN")
        d_nei = sum(1 for e in disc if e["call"] == "neither")
        n = len(disc) or 1
        print(f"\ndiscriminating runners (meet != prior after 0-encoding): "
              f"{len(disc)} of {len(evidence)}")
        print(f"  CLEAN   {d_cln} ({100.0*d_cln/n:.1f}%)  <- head == prior finish")
        print(f"  ADVERSE {d_adv} ({100.0*d_adv/n:.1f}%)  <- head == meeting finish")
        print(f"  neither {d_nei}")
        calls = [e["call"] for e in evidence]
        print(f"all runners: {calls.count('CLEAN')} CLEAN, "
              f"{calls.count('ADVERSE')} ADVERSE, "
              f"{calls.count('consistent-both')} consistent-both, "
              f"{calls.count('neither')} neither, "
              f"{calls.count('no-form')} no-form of {len(calls)}")
        for b in b_notes:
            print(f"measurement B {b['meetingId']}: archive kinds="
                  f"{b['archive_kinds_in_table']} matched="
                  f"{b['archived_payloads_matched']} carries_last10="
                  f"{b['archived_carries_last10']}")
        print(f"\n{len(evidence)} runners classified; evidence written to "
              f"pf_last10_asof_evidence.json")
        return 0
    finally:
        conn.close()


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd, rest = argv[0], argv[1:]
    ids = [m.strip() for a in rest for m in a.split(",") if m.strip()]
    if cmd == "fetch":
        if not ids:
            print("usage: pf_last10_asof_probe.py fetch <meetingId> [...]")
            return 1
        return cmd_fetch(ids)
    if cmd == "compare":
        if not rest:
            print("usage: pf_last10_asof_probe.py compare <artifact_dir> "
                  "[meetingId ...]")
            return 1
        return cmd_compare(rest[0], ids[1:] if len(rest) > 1 else ())
    print(f"unknown subcommand {cmd!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
