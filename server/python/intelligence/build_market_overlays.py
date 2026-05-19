#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.append(str(CURRENT_DIR))
if str(PARENT_DIR) not in sys.path:
    sys.path.append(str(PARENT_DIR))

from common import build_output_path, build_parser, get_connection, load_target_runners, write_json
from market_overlay_common import build_market_overlay_map_from_sp


def build_payload(target_date: str, track_filter: str | None, statement_timeout_ms: int) -> dict:
    runners = load_target_runners(target_date, track_filter)
    tracks = sorted({runner["track"] for runner in runners if runner.get("track")})
    conn = get_connection(statement_timeout_ms)
    try:
        return build_market_overlay_map_from_sp(conn, tracks, target_date)
    finally:
        conn.close()


def main() -> None:
    parser = build_parser("Build SP-based market overlay intelligence for today's racecard.")
    args = parser.parse_args()
    payload = build_payload(args.date, args.track, args.statement_timeout_ms)
    output_path = build_output_path("market_overlays", args.date, args.track, args.output)
    write_json(output_path, payload)
    print(output_path)


if __name__ == "__main__":
    main()
