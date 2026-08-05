#!/usr/bin/env python3
"""STRIDE deploy preflight + liveness verifier.

One command, two boards, green/red, exit-non-zero on any RED so it can gate a
deploy session. It *reports* state — it never blindly re-applies a migration
(prediction_audit_unique_key.sql and final_prob_audit.sql are already applied in
prod; re-running them is noise, not signal).

  DEPLOYMENT board — are the running bytes built from current main?
  SCHEMA board     — do the objects the writers require exist yet?
  LIVENESS board   — is each pipeline actually PRODUCING rows, or frozen?

The liveness board is the one that matters. Schema can be all-green while
nothing has tipped for months: selections / stride_tip_results / race_schedule
all stop at 2026-04-19 and do not resume until 2026-08-02. That particular stop
was the operator pausing the pipeline deliberately, not a fault — recorded in
infra/EXECUTION_STATUS.md, and not worth re-investigating — but it is the exact
shape a real freeze would have, which is the point: only a liveness check can
tell the two apart, and only by asking a human. The single highest-value probe
here is "did tipping run for the latest race day?" — max(race_date) on
selections vs the race calendar. A schema-only check reads green throughout.

The deployment board answers a different question that used to have no owner:
main can move without AWS moving, because deploy-infra is dispatch-only. See
check_deployment for what it compares and why it is path-scoped.

Usage:
  python deploy_preflight.py                  # full board; exit 1 if any RED
  python deploy_preflight.py --stale-days 3   # freshness tolerance (default 3)
  python deploy_preflight.py --json           # machine-readable board
  python deploy_preflight.py --skip-deployment  # offline; skips the github call
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

GREEN, RED, AMBER, UNKNOWN = "GREEN", "RED", "AMBER", "UNKNOWN"
SYM = {GREEN: "OK  ", RED: "FAIL", AMBER: "PEND", UNKNOWN: "??  "}

# Exactly the paths infra/Dockerfile COPYs into the image, plus the Dockerfile
# itself. Anything outside this set can move on main without changing a single
# byte of what the jobs run, and must not raise an alarm — CLAUDE.md-only
# commits are routine and a SHA-equality check would fire on every one of them.
# Keep in step with infra/Dockerfile; test_deploy_freshness asserts they match.
IMAGE_PATHS = (
    "requirements.txt",
    "server/python/",
    "migrations/",
    "infra/jobs/",
    "infra/entrypoint.sh",
    "infra/Dockerfile",
    "racing_system_v8.3_mc.py",
)

DEFAULT_REPO = "Sageabdallah/stride-racing"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# The compare API caps its file list. Past the cap we cannot prove the drift is
# irrelevant, so the answer is RED, not a guess.
_COMPARE_FILE_CAP = 300


def _connect():
    import psycopg2

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        env = Path(__file__).resolve().parents[2] / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip()
                    break
    if not url:
        raise RuntimeError("DATABASE_URL not set (env or project .env)")
    conn = psycopg2.connect(url)
    conn.autocommit = True  # read-only checks; never leave a tx open across probes
    return conn


def _scalar(conn, sql, params=None):
    cur = conn.cursor()
    try:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()


def _row(name, status, detail=""):
    return {"name": name, "status": status, "detail": detail}


# ---------------------------------------------------------------------------
# DEPLOYMENT board — do the running bytes match main?
# ---------------------------------------------------------------------------
#
# deploy-infra is dispatch-only and deliberately stays that way, so a merge
# that changes job behaviour does not reach AWS until someone runs it. Nothing
# noticed the gap. On 2026-08-03 the quiet-day work sat on main for an hour
# while Fargate ran the previous image: the GitHub crons had the fix and the
# Fargate jobs did not, so the two monitors disagreed about the same morning
# and neither was wrong. This is the check that would have said so.
#
# It runs inside the container, against the image's own stamp, and asks GitHub
# what changed between that commit and main. Two deliberate choices:
#
#   Path-scoped, not SHA-equality. Only the Dockerfile COPY set can change
#   behaviour. A docs commit must not alarm, or the alarm gets ignored.
#
#   Unreachable is AMBER, unstamped is RED. A network blip is not evidence of
#   staleness and must not cry wolf; an image with no stamp cannot be checked
#   at all, which is itself a deployment defect and self-clears on redeploy.

def _image_relevant(filename: str) -> bool:
    return any(filename == p or filename.startswith(p) for p in IMAGE_PATHS)


def _fetch_compare(repo, base, head, timeout):
    """GitHub's compare API. Public repo, so unauthenticated is fine — one
    call a day against a 60/hour limit. GITHUB_TOKEN is used when present so
    the same code works if the repo is ever made private."""
    url = f"https://api.github.com/repos/{repo}/compare/{base}...{head}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "stride-deploy-preflight",
    })
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def check_deployment(branch="main", timeout=15, fetch=None):
    """Is the image these bytes are running from built from current main?"""
    name = "deployed image carries current main (image-relevant paths)"
    sha = (os.environ.get("STRIDE_IMAGE_SHA") or "").strip()
    repo = (os.environ.get("STRIDE_REPO") or DEFAULT_REPO).strip()
    fetch = fetch or _fetch_compare

    if not sha:
        return [_row(name, RED,
                     "STRIDE_IMAGE_SHA is unset — this image was built before "
                     "the freshness stamp existed, so it cannot be checked at "
                     "all. Run deploy-infra.")]
    if not _SHA_RE.match(sha):
        return [_row(name, AMBER,
                     f"image stamped {sha!r}, which is not a clean commit "
                     "(a dirty tree, or a hand build). Cannot compare — "
                     "redeploy from a clean checkout to restore the check.")]

    try:
        data = fetch(repo, sha, branch, timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return [_row(name, AMBER,
                         f"GitHub cannot compare {sha[:7]}...{branch} (404) — "
                         "the commit is not on this repo, likely a force-push "
                         "or a build from a fork. Redeploy to resolve.")]
        return [_row(name, AMBER, f"GitHub compare failed: HTTP {e.code} — "
                                  "staleness unknown, not assumed")]
    except Exception as e:
        return [_row(name, AMBER, f"GitHub compare unreachable ({type(e).__name__}) — "
                                  "staleness unknown, not assumed")]

    files = data.get("files") or []
    behind = data.get("ahead_by") or 0        # main ahead of the image
    if len(files) >= _COMPARE_FILE_CAP:
        return [_row(name, RED,
                     f"{behind} commit(s) behind main and the compare hit its "
                     f"{_COMPARE_FILE_CAP}-file cap, so the drift cannot be "
                     "shown to be irrelevant. Run deploy-infra.")]

    changed = sorted({f.get("filename", "") for f in files if _image_relevant(f.get("filename", ""))})
    if not changed:
        detail = (f"{sha[:7]}, {behind} commit(s) behind main, none touching "
                  "the image") if behind else f"{sha[:7]}, current with main"
        return [_row(name, GREEN, detail)]

    shown = ", ".join(changed[:4]) + (f" (+{len(changed) - 4} more)" if len(changed) > 4 else "")
    return [_row(name, RED,
                 f"image is {sha[:7]}; main has changed {len(changed)} "
                 f"image-relevant file(s) since — {shown}. The jobs are "
                 "running different code from main. Run deploy-infra.")]


# ---------------------------------------------------------------------------
# SCHEMA board — presence probes; PENDING (amber) is expected, not a failure
# ---------------------------------------------------------------------------

def check_schema(conn):
    rows = []

    def probe(name, exists_sql, absent_status=RED, absent_detail="", present_detail="present"):
        try:
            ok = _scalar(conn, exists_sql) is not None
        except Exception as e:
            rows.append(_row(name, UNKNOWN, str(e)[:90]))
            return
        rows.append(_row(name, GREEN if ok else absent_status,
                         present_detail if ok else absent_detail))

    probe("prediction_audit unique index",
          "SELECT 1 FROM pg_indexes WHERE indexname = 'uq_prediction_audit_race_horse'",
          absent_detail="MISSING — apply migrations/prediction_audit_unique_key.sql")
    probe("prediction_audit.final_win_prob column",
          "SELECT 1 FROM information_schema.columns "
          "WHERE table_name='prediction_audit' AND column_name='final_win_prob'",
          absent_detail="MISSING — apply migrations/final_prob_audit.sql")
    probe("selection_ledger table",
          "SELECT to_regclass('public.selection_ledger')",
          absent_detail="MISSING — apply migrations/selection_ledger.sql")
    probe("selection_ledger net-settlement cols",
          "SELECT 1 FROM information_schema.columns "
          "WHERE table_name='selection_ledger' AND column_name='settled_at_sp_fallback'",
          absent_status=AMBER,
          absent_detail="pending: merge PR #1 + apply selection_ledger_net_settlement.sql")
    probe("runner_odds_snapshots table",
          "SELECT to_regclass('public.runner_odds_snapshots')",
          absent_status=AMBER,
          absent_detail="pending: merge PR #2 + apply runner_odds_snapshots.sql")
    return rows


# ---------------------------------------------------------------------------
# LIVENESS board — is each pipeline producing rows? (the board that matters)
# ---------------------------------------------------------------------------

def check_liveness(conn, stale_days):
    rows = []
    today = _scalar(conn, "SELECT CURRENT_DATE")
    sel_max = {"v": None}

    def freshness(name, table, red_if_stale=True, keep=None):
        try:
            mx = _scalar(conn, f"SELECT max(race_date::date) FROM {table}")
        except Exception as e:
            rows.append(_row(name, UNKNOWN, str(e)[:90]))
            return None
        if mx is None:
            rows.append(_row(name, RED if red_if_stale else AMBER, "no rows"))
            return None
        age = (today - mx).days
        status = GREEN if age <= stale_days else (RED if red_if_stale else AMBER)
        rows.append(_row(name, status, f"latest {mx} ({age}d behind today)"))
        if keep:
            keep["v"] = mx
        return mx

    # THE gate: did tips actually run?
    freshness("selections (tips pipeline)", "selections", keep=sel_max)
    freshness("stride_tip_results", "stride_tip_results")
    freshness("prediction_audit", "prediction_audit")
    freshness("race_results_history (results side)", "race_results_history", red_if_stale=False)

    # prediction_audit.final_win_prob recent non-null (downstream of tips + store_final_probs)
    window = max(stale_days, 7)
    try:
        n = _scalar(conn,
                    "SELECT count(*) FROM prediction_audit "
                    "WHERE final_win_prob IS NOT NULL AND race_date::date >= CURRENT_DATE - %s",
                    (window,))
        rows.append(_row("prediction_audit.final_win_prob (recent non-null)",
                         GREEN if (n or 0) > 0 else AMBER, f"{n or 0} rows in last {window}d"))
    except Exception as e:
        rows.append(_row("prediction_audit.final_win_prob", UNKNOWN, str(e)[:90]))

    # runner_odds_snapshots coverage (delegated to odds_snapshot_coverage.py for the 95% gate)
    try:
        if _scalar(conn, "SELECT to_regclass('public.runner_odds_snapshots')") is None:
            rows.append(_row("runner_odds_snapshots tip_time", AMBER, "table pending (PR #2)"))
        else:
            d = _scalar(conn, "SELECT max(race_date::date) FROM runner_odds_snapshots "
                              "WHERE snapshot_kind='tip_time'")
            if d is None:
                rows.append(_row("runner_odds_snapshots tip_time", AMBER, "no tip_time rows yet"))
            else:
                cnt = _scalar(conn, "SELECT count(*) FROM runner_odds_snapshots "
                                    "WHERE snapshot_kind='tip_time' AND race_date::date = %s", (d,))
                rows.append(_row("runner_odds_snapshots tip_time", GREEN if (cnt or 0) > 0 else AMBER,
                                 f"{cnt or 0} rows for {d} — run odds_snapshot_coverage.py for the >=95% gate"))
    except Exception as e:
        rows.append(_row("runner_odds_snapshots tip_time", UNKNOWN, str(e)[:90]))

    # selection_ledger settled (0 until the writer is enabled — expected)
    try:
        if _scalar(conn, "SELECT to_regclass('public.selection_ledger')") is None:
            rows.append(_row("selection_ledger settled", AMBER, "table pending"))
        else:
            s = _scalar(conn, "SELECT count(*) FROM selection_ledger WHERE settled = TRUE")
            rows.append(_row("selection_ledger settled", GREEN if (s or 0) > 0 else AMBER,
                             f"{s or 0} settled rows (0 until STRIDE_LEDGER_WRITE + settle pass)"))
    except Exception as e:
        rows.append(_row("selection_ledger settled", UNKNOWN, str(e)[:90]))

    # THE single highest-value assertion: tips ran for the latest race day
    rows.append(_tips_ran_for_latest_race_day(conn, today, stale_days, sel_max["v"]))
    return rows


def _tips_ran_for_latest_race_day(conn, today, stale_days, sel_max):
    name = "tips ran for the latest scheduled race day"
    try:
        if _scalar(conn, "SELECT to_regclass('public.race_schedule')") is not None:
            expected = _scalar(conn, "SELECT max(race_date::date) FROM race_schedule "
                                     "WHERE race_date::date <= CURRENT_DATE")
            ref = "race_schedule"
        else:
            expected = today
            ref = "today (no race_schedule)"
    except Exception as e:
        return _row(name, UNKNOWN, str(e)[:90])
    if sel_max is None:
        return _row(name, RED, "selections is empty — tips have never run / are frozen")
    if expected is None:
        return _row(name, UNKNOWN, "no reference race day")
    gap = (expected - sel_max).days
    status = GREEN if gap <= stale_days else RED
    return _row(name, status,
                f"selections latest {sel_max} vs expected {expected} [{ref}] ({gap}d gap)")


def _print_board(title, rows):
    print(f"\n=== {title} ===")
    for r in rows:
        print(f"  [{SYM[r['status']]}] {r['name']:<44} {r['detail']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="STRIDE deploy preflight + liveness verifier.")
    ap.add_argument("--stale-days", type=int, default=3,
                    help="Max days a pipeline may be behind before it counts as frozen (default 3)")
    ap.add_argument("--json", action="store_true", help="Machine-readable output")
    ap.add_argument("--skip-deployment", action="store_true",
                    help="Omit the image-freshness board (offline or local runs; "
                         "it needs github.com)")
    args = ap.parse_args()

    # Before the DB, and reported even when the DB is down: a stale image is a
    # finding in its own right, and letting a Neon outage swallow it is exactly
    # how the two-monitor disagreement went unnoticed in the first place.
    deployment = [] if args.skip_deployment else check_deployment()

    try:
        conn = _connect()
    except Exception as e:
        if deployment:
            _print_board("DEPLOYMENT (do the running bytes match main?)", deployment)
        print(f"PREFLIGHT: cannot connect — {e}", file=sys.stderr)
        return 2

    try:
        schema = check_schema(conn)
        liveness = check_liveness(conn, args.stale_days)
    finally:
        conn.close()

    reds = [r for r in deployment + schema + liveness if r["status"] == RED]
    verdict = "RED" if reds else "GREEN"

    if args.json:
        print(json.dumps({"verdict": verdict, "deployment": deployment,
                          "schema": schema, "liveness": liveness}, default=str, indent=2))
    else:
        if deployment:
            _print_board("DEPLOYMENT (do the running bytes match main?)", deployment)
        _print_board("SCHEMA (presence — PEND is expected)", schema)
        _print_board("LIVENESS (is it producing rows?)", liveness)
        print(f"\nVERDICT: {verdict}" + (f" — {len(reds)} blocking: "
              + "; ".join(r["name"] for r in reds) if reds else " — all clear"))
        if not reds:
            print("(PEND items are expected until PR #1/#2 merge + their migrations apply.)")
    return 1 if reds else 0


if __name__ == "__main__":
    raise SystemExit(main())
