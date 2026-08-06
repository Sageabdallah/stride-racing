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

Exit: 0 all reachable, 1 some unreachable, 2 none reachable or no panel.
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


def probe(url: str) -> tuple[bool, str]:
    """(reachable, detail). HEAD first; some servers 405 it, so fall back."""
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--panel", default=str(Path(__file__).with_name(
        "tipster_panel.json")))
    ap.add_argument("--json", action="store_true", help="machine-readable")
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

    rows = []
    for s in sources:
        url = s.get("tip_page_url") or s.get("base_url", "")
        alive, detail = probe(url) if url else (False, "no url")
        rows.append({"id": s.get("id"), "name": s.get("name"),
                     "bucket": s.get("type"), "url": url,
                     "alive": alive, "detail": detail})

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in sorted(rows, key=lambda x: (x["alive"], x["name"])):
            print(f"  {'OK  ' if r['alive'] else 'DEAD'} "
                  f"[{r['bucket'] or '?':15}] {r['name'][:32]:34} "
                  f"{r['detail']:22} {r['url'][:56]}")

    live = [r for r in rows if r["alive"]]
    dead = [r for r in rows if not r["alive"]]
    print(f"\n[LIVENESS] {len(live)}/{len(rows)} reachable", file=sys.stderr)

    # Which WEIGHTING buckets survive, not just how many sources. Losing the
    # only stable_watcher costs more than losing a fourth form_analyst:
    # bucket_spread drives a 0.8x-1.5x multiplier on the consensus injection
    # (consensus_blender.py:123), so diversity is the thing to protect.
    lost = sorted({r["bucket"] for r in dead} - {r["bucket"] for r in live})
    if lost:
        print(f"[LIVENESS] buckets with NO reachable source: "
              f"{', '.join(lost)}", file=sys.stderr)
    if dead:
        print(f"[LIVENESS] replace or deactivate: "
              f"{', '.join(r['name'] for r in dead)}", file=sys.stderr)

    if not live:
        return 2
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
