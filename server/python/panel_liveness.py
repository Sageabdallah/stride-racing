#!/usr/bin/env python3
"""Is every panel source still reachable? READ-ONLY, no API keys, no cost.

`"verified": true` in tipster_panel.json is a claim someone made once. It was
made on or before 2026-04-10 and by 2026-08-06 it was wrong for 12 of the 16
sources carrying it — Racenet and Breednet were plain 404s, the rest refused
the fetch. Nothing caught that for four months, because nothing ever asked.
The panel does not crash when a source dies; it just quietly contributes less,
which is the failure shape this repo keeps finding one layer further out.

So: a flag that decays needs something that re-checks it. This is the cheap
version — HEAD (falling back to GET for the servers that reject HEAD) against
each active+verified URL.

Read the two checks together; NEITHER is a superset of the other. Measured
2026-08-06: punters.com.au returns 403 to this script and yielded 7,587 chars
to Tavily on the same day, because Tavily's fetcher gets past anti-bot where a
plain request does not. So DEAD here does not prove a source is useless — and
`--panel-only` extracting nothing does not prove the page is gone.

What this is actually good for is the DISTINCTION the extract cannot draw:
404 means moved and needs a new URL, while 403/406/429 means the page is alive
and refusing this client. Those need opposite responses, and
`consensus_agent.py --panel-only` reports both as the same bare failure.

Run standalone, or as the pre-flight in infra/09c_upload_panel.sh so a panel
whose sources have rotted cannot be uploaded without the operator seeing it.

Three verdicts, not two, because two cannot describe what was measured — see
ALIVE/FLAKY/DEAD below. Exit: 0 every source ALIVE, 1 some DEAD or FLAKY,
2 nothing that could ever contribute (or no panel). Only 2 stops an upload.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

TIMEOUT = 15
# Servers increasingly reject a bare programmatic UA outright, which would
# report a live page as dead and send someone hunting a replacement for a
# source that was fine. Identify as a browser; this only ever reads.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
}


ATTEMPTS = 3

# Three outcomes, because two cannot describe what was measured. Back A Winner
# returned EMPTY after 26,296ms on one probe and 122,750 chars on the next, so
# every single-shot count stated about this panel — including the 4-vs-3 that
# started this — was one observation read as a fact.
#
# FLAKY is "succeeded at least once and failed at least once", NOT a 2-of-3
# majority. A majority rule would relabel a source that fails one morning in
# three as healthy, which is the specific thing worth seeing: the panel is
# fetched once a day, so a 1-in-3 failure rate is a third of race days with
# that source contributing nothing. Any disagreement is the signal. This is a
# report, not a gate, so strictness costs nothing and hides nothing.
ALIVE, FLAKY, DEAD = "ALIVE", "FLAKY", "DEAD"


def _attempt(url: str) -> tuple[bool, str]:
    """One try. HEAD first; some servers 405 it, so fall back to GET."""
    for method in ("HEAD", "GET"):
        try:
            r = requests.request(method, url, timeout=TIMEOUT,
                                 allow_redirects=True, headers=HEADERS,
                                 stream=(method == "GET"))
            if method == "GET":
                r.close()          # status is all we need; do not read a body
            if r.status_code == 405 and method == "HEAD":
                continue
            return r.status_code < 400, f"HTTP {r.status_code}"
        except requests.RequestException as e:
            if method == "GET":
                return False, type(e).__name__
    return False, "no response"


def probe(url: str, attempts: int = ATTEMPTS) -> tuple[str, str]:
    """(verdict, detail) over `attempts` tries.

    Detail names the disagreement when there is one, because "2/3 HTTP 200,
    1/3 ConnectTimeout" and "3/3 HTTP 404" need opposite responses and a
    single-shot probe reports them identically.
    """
    results = [_attempt(url) for _ in range(attempts)]
    oks = [ok for ok, _ in results]
    details = [d for _, d in results]
    if all(oks):
        return ALIVE, f"{attempts}/{attempts} {details[0]}"
    if not any(oks):
        # Distinct reasons matter: an intermittent 429 among 404s is a
        # different diagnosis from three consistent 404s.
        uniq = sorted(set(details))
        return DEAD, f"{attempts}/{attempts} failed ({', '.join(uniq)})"
    good = sum(oks)
    bad = sorted({d for ok, d in results if not ok})
    return FLAKY, f"{good}/{attempts} ok, failed: {', '.join(bad)}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--panel", default=str(Path(__file__).with_name(
        "tipster_panel.json")))
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--attempts", type=int, default=ATTEMPTS,
                    help="tries per source; >1 separates FLAKY from DEAD")
    args = ap.parse_args()

    path = Path(args.panel)
    if not path.exists():
        print(f"[LIVENESS] FATAL: {path} not found", file=sys.stderr)
        return 2

    sources = [s for s in json.loads(path.read_text()).get("sources", [])
               if s.get("active") and s.get("verified")]
    if not sources:
        print("[LIVENESS] FATAL: no active+verified sources", file=sys.stderr)
        return 2

    # Concurrent across sources, sequential within one. 16 sources x 3
    # attempts x a 15s timeout is 12 minutes serially, which is long enough
    # that an operator skips the pre-flight — and a check people skip is worse
    # than no check. Fanned out it is bounded by the slowest single source.
    from concurrent.futures import ThreadPoolExecutor
    def _one(s):
        url = s.get("tip_page_url") or s.get("base_url", "")
        verdict, detail = probe(url, args.attempts) if url else (DEAD, "no url")
        return {"id": s.get("id"), "name": s.get("name"),
                "bucket": s.get("type"), "url": url,
                "verdict": verdict, "detail": detail}
    # One batch, not two: at 8 workers a 16-source panel took 3m00s, and a
    # 3-minute pre-flight is one an operator learns to skip. Bounded now by the
    # slowest single source (2 methods x 3 attempts x TIMEOUT), not by batches.
    with ThreadPoolExecutor(max_workers=max(8, len(sources))) as pool:
        rows = list(pool.map(_one, sources))

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        order = {ALIVE: 0, FLAKY: 1, DEAD: 2}
        for r in sorted(rows, key=lambda x: (order[x["verdict"]], x["name"])):
            print(f"  {r['verdict']:5} [{r['bucket'] or '?':15}] "
                  f"{r['name'][:32]:34} {r['detail']:36} {r['url'][:52]}")

    live = [r for r in rows if r["verdict"] == ALIVE]
    flaky = [r for r in rows if r["verdict"] == FLAKY]
    dead = [r for r in rows if r["verdict"] == DEAD]
    # Every count says which predicate it counts. Two reports of "the panel"
    # disagreeing about what they were counting is what produced 4-vs-3.
    print(f"\n[LIVENESS] REACHABLE over {args.attempts} attempts: "
          f"{len(live)} ALIVE, {len(flaky)} FLAKY, {len(dead)} DEAD "
          f"(of {len(rows)} active+verified)", file=sys.stderr)
    print(f"[LIVENESS] this measures REACHABILITY by plain request. What "
          f"consensus actually receives is EXTRACTABILITY via Tavily — "
          f"`consensus_agent.py <date> --panel-only`. The two disagree in "
          f"BOTH directions; neither is a superset.", file=sys.stderr)
    if flaky:
        print(f"[LIVENESS] FLAKY sources contribute on some race days and not "
              f"others: {', '.join(r['name'] for r in flaky)}", file=sys.stderr)

    # Which WEIGHTING buckets survive, not just how many sources. Losing the
    # only stable_watcher costs more than losing a fourth form_analyst:
    # bucket_spread drives a 0.8x-1.5x multiplier on the consensus injection
    # (consensus_blender.py:123), so diversity is the thing to protect.
    lost = sorted({r["bucket"] for r in dead}
                  - {r["bucket"] for r in live + flaky})
    if lost:
        print(f"[LIVENESS] buckets with NO reachable source: "
              f"{', '.join(lost)}", file=sys.stderr)
    if dead:
        print(f"[LIVENESS] replace or deactivate: "
              f"{', '.join(r['name'] for r in dead)}", file=sys.stderr)

    # Exit contract, and what 09c_upload_panel.sh does with each:
    #   0  every source ALIVE                      -> upload, no comment
    #   1  some DEAD or FLAKY                      -> upload, report loudly
    #   2  nothing that can EVER contribute        -> refuse the upload
    #
    # FLAKY counts as contributing for the go/no-go, because it does: a source
    # that fetches two mornings in three is worth more than no panel. It is
    # never counted as ALIVE, is always named, and is a replacement candidate
    # for the URL pass — not an emergency. Excluding it from the go/no-go
    # would throw away real signal to make a number look tidier.
    if not live and not flaky:
        return 2
    return 1 if (dead or flaky) else 0


if __name__ == "__main__":
    sys.exit(main())
