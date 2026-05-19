from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from winner_pattern_gap.agent1 import run_agent1_analysis
    from winner_pattern_gap.agent2 import run_agent2_analysis
    from winner_pattern_gap.config import METRO_TRACK_ORDER, resolve_tracks
    from winner_pattern_gap.coverage import run_coverage_audit
    from winner_pattern_gap.db import ResearchDB, ensure_directory, json_write
    from winner_pattern_gap.synthesis import run_synthesis
else:
    from .agent1 import run_agent1_analysis
    from .agent2 import run_agent2_analysis
    from .config import METRO_TRACK_ORDER, resolve_tracks
    from .coverage import run_coverage_audit
    from .db import ResearchDB, ensure_directory, json_write
    from .synthesis import run_synthesis


def _default_export_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[4]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return repo_root / "data" / "research" / "winner_pattern_gap" / stamp


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the reusable STRIDE winner-pattern gap analysis pipeline.")
    parser.add_argument("--date-from", required=True, help="Inclusive start date in YYYY-MM-DD format.")
    parser.add_argument("--date-to", required=True, help="Inclusive end date in YYYY-MM-DD format.")
    parser.add_argument("--scope", choices=["metro", "all"], default="metro")
    parser.add_argument("--agent", choices=["agent1", "agent2", "both", "synthesis"], default="both")
    parser.add_argument("--tracks", default="", help='Optional comma-separated track override, e.g. "Rosehill,Flemington,Doomben".')
    parser.add_argument("--export-dir", default="", help="Optional export directory. Defaults to data/research/winner_pattern_gap/<timestamp>/")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    export_dir = Path(args.export_dir).expanduser().resolve() if args.export_dir else _default_export_dir()
    ensure_directory(export_dir)

    track_override = [part.strip() for part in args.tracks.split(",") if part.strip()] if args.tracks else []
    tracks = resolve_tracks(args.scope, track_override if track_override else None)

    db = ResearchDB(export_dir=export_dir)
    try:
        coverage = run_coverage_audit(
            db,
            date_from=args.date_from,
            date_to=args.date_to,
            tracks=tracks,
            export_dir=export_dir,
        )

        outputs: Dict[str, Any] = {
            "config": {
                "date_from": args.date_from,
                "date_to": args.date_to,
                "scope": args.scope,
                "agent": args.agent,
                "tracks": tracks,
                "export_dir": str(export_dir),
            },
            "coverage_summary": coverage.summary,
        }

        agent1_result = None
        agent2_result = None
        if args.agent in {"agent1", "both", "synthesis"}:
            agent1_result = run_agent1_analysis(coverage, export_dir=export_dir)
            outputs["agent1_summary"] = agent1_result.get("summary", {})
        if args.agent in {"agent2", "both", "synthesis"}:
            agent2_result = run_agent2_analysis(
                db,
                coverage,
                date_from=args.date_from,
                date_to=args.date_to,
                export_dir=export_dir,
            )
            outputs["agent2_summary"] = agent2_result.get("summary", {})
        if args.agent in {"both", "synthesis"}:
            if agent1_result is None:
                agent1_result = run_agent1_analysis(coverage, export_dir=export_dir)
            if agent2_result is None:
                agent2_result = run_agent2_analysis(
                    db,
                    coverage,
                    date_from=args.date_from,
                    date_to=args.date_to,
                    export_dir=export_dir,
                )
            synthesis_result = run_synthesis(coverage, agent1_result, agent2_result, export_dir=export_dir)
            outputs["synthesis_summary"] = {
                "cross_match_count": len(synthesis_result.get("cross_matches", [])),
                "feature_count": len(synthesis_result.get("feature_roadmap", [])),
                "underreaction_sample_size": synthesis_result.get("sectional_market_underreaction", {}).get("sample_size", 0),
            }

        json_write(outputs, export_dir / "run_summary.json")
        print(json.dumps({"status": "ok", **outputs["config"]}, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
