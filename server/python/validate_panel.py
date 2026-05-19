#!/usr/bin/env python3
"""
Validate tipster panel — check all active URLs are reachable.
Run manually before going live with consensus agent.
"""

import json
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)


def main():
    panel_path = Path(__file__).parent / "tipster_panel.json"
    if not panel_path.exists():
        print(f"ERROR: {panel_path} not found")
        sys.exit(1)

    panel = json.loads(panel_path.read_text())

    print(f"Tipster Panel v{panel.get('panel_version', '?')} — "
          f"{panel.get('current_size', '?')} sources")
    print(f"Last updated: {panel.get('last_updated', '?')}")
    print(f"{'=' * 80}\n")

    active_count = 0
    ok_count = 0
    fail_count = 0

    for source in panel["sources"]:
        sid = source["id"]
        name = source["name"]
        bucket = source["type"]
        url = source.get("tip_page_url") or source.get("base_url", "")

        if not source.get("active"):
            print(f"  INACTIVE  [{bucket:15}] {name}")
            continue

        active_count += 1

        if not source.get("verified"):
            print(f"  UNVERIF   [{bucket:15}] {name} — active but not verified!")
            continue

        try:
            r = requests.get(
                url,
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
                allow_redirects=True,
            )
            if r.status_code == 200:
                content_len = len(r.text)
                print(f"  OK        [{bucket:15}] {name} ({content_len:,} chars)")
                ok_count += 1
            else:
                print(f"  WARN {r.status_code}  [{bucket:15}] {name}: {url}")
                fail_count += 1
        except requests.exceptions.Timeout:
            print(f"  TIMEOUT   [{bucket:15}] {name}: {url}")
            fail_count += 1
        except requests.exceptions.ConnectionError as e:
            print(f"  CONN ERR  [{bucket:15}] {name}: {e}")
            fail_count += 1
        except Exception as e:
            print(f"  FAIL      [{bucket:15}] {name}: {e}")
            fail_count += 1

    print(f"\n{'=' * 80}")
    print(f"  Active: {active_count} | OK: {ok_count} | Failed: {fail_count}")

    if fail_count > 0:
        print(f"\n  WARNING: {fail_count} panel member(s) unreachable.")
        print(f"  Fix URLs or set active=false before running consensus agent.")

    if active_count == 0:
        print(f"\n  ERROR: No active panel members. Consensus agent will produce empty data.")


if __name__ == "__main__":
    main()
