#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[2]


def run_cmd(cmd: list[str], cwd: Path) -> None:
    print(f"[Backfill] Running: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(cwd), check=True)


def candidate_betfair_dirs() -> list[Path]:
    env_dir = os.environ.get("BETFAIR_HISTORICAL_DIR")
    candidates = []
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.extend(
        [
            ROOT / "historical" / "betfair_historical",
            ROOT / "historical" / "betfair",
            ROOT / "historical_data" / "betfair_historical",
            Path.home() / "Desktop" / "BETFAIR" / "betfair_historical",
        ]
    )
    return candidates


def discover_betfair_dir() -> Path | None:
    for candidate in candidate_betfair_dirs():
        if candidate.exists() and any(candidate.rglob("*.bz2")):
            return candidate
    return None


def snapshot_counts(db_url: str) -> dict:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        counts = conn.execute(
            text(
                """
                SELECT
                  (SELECT COUNT(*) FROM training_data) AS training_data_rows,
                  (SELECT COUNT(*) FROM training_data WHERE selection_id IS NOT NULL) AS training_data_live_rows,
                  (SELECT COUNT(*) FROM betfair_market_runner_map) AS betfair_map_rows,
                  (SELECT COUNT(*) FROM betfair_labeled_training_view) AS betfair_labeled_rows,
                  (SELECT COUNT(*) FROM betfair_labeled_training_view WHERE has_label = 1) AS betfair_labeled_has_label_rows,
                  (SELECT COUNT(*) FROM training_view_v2) AS training_view_rows,
                  (SELECT COUNT(model_probability) FROM training_view_v2) AS training_view_model_probability_rows,
                  (SELECT COUNT(expected_value) FROM training_view_v2) AS training_view_expected_value_rows,
                  (SELECT COUNT(market_odds) FROM training_view_v2) AS training_view_market_odds_rows
                """
            )
        ).mappings().one()
    return dict(counts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill historical research sources for winner-pattern analysis.")
    parser.add_argument("--skip-track-import", action="store_true")
    parser.add_argument("--skip-betfair", action="store_true")
    parser.add_argument("--skip-view-refresh", action="store_true")
    parser.add_argument("--timestamp-from", default="2024-01-01")
    parser.add_argument("--timestamp-to", default=None)
    return parser.parse_args()


def main() -> int:
    load_dotenv(ROOT / ".env")
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL not set")

    before = snapshot_counts(db_url)
    print("[Backfill] Before:", before)

    if not args.skip_track_import:
        track_import_dir = ROOT / "historical_data" / "track_imports"
        if track_import_dir.exists():
            run_cmd([sys.executable, "server/python/import_track_json_fast.py", "--dir", str(track_import_dir)], ROOT)
        else:
            print(f"[Backfill] Track import dir not found: {track_import_dir}")

    if not args.skip_betfair:
        betfair_dir = discover_betfair_dir()
        if betfair_dir:
            cmd = [
                sys.executable,
                "server/python/build_betfair_mapping.py",
                "--data-dir",
                str(betfair_dir),
                "--timestamp-from",
                args.timestamp_from,
            ]
            if args.timestamp_to:
                cmd.extend(["--timestamp-to", args.timestamp_to])
            run_cmd(cmd, ROOT)
        else:
            print("[Backfill] No local Betfair BASIC archive directory found. Refreshing Betfair labeled view only.")
            run_cmd([sys.executable, "server/python/build_betfair_mapping.py", "--refresh-view-only"], ROOT)

    if not args.skip_view_refresh:
        run_cmd([sys.executable, "server/python/refresh_training_view_v2.py"], ROOT)

    after = snapshot_counts(db_url)
    print("[Backfill] After:", after)
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main())
