#!/usr/bin/env python3
"""STRIDE Intelligence Builder — orchestrates Agent 1 (track/market) and Agent 2 (form/horse), then writes a build_log.txt."""

import argparse
import hashlib
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
INTEL_DIR = SCRIPT_DIR / "intelligence"

REQUIRED_FILES = [
    "barrier_map.json",
    "flemington_straight.json",
    "class_distance_patterns.json",
    "form_franking.json",
    "prep_cycles.json",
    "sectional_trends.json",
    "trainer_patterns.json",
    "market_overlays.json",
]


def main():
    parser = argparse.ArgumentParser(description="STRIDE Intelligence Builder")
    parser.add_argument("date", help="Race date YYYY-MM-DD")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--parallel",
        dest="parallel",
        action="store_true",
        help="Run agents in parallel (default)",
    )
    mode_group.add_argument(
        "--sequential",
        dest="parallel",
        action="store_false",
        help="Run agents sequentially",
    )
    parser.set_defaults(parallel=True)
    args = parser.parse_args()

    INTEL_DIR.mkdir(parents=True, exist_ok=True)

    agent1 = str(SCRIPT_DIR / "stride_agent_track.py")
    agent2 = str(SCRIPT_DIR / "stride_agent_form.py")

    print(f"STRIDE Intelligence Builder — {args.date}")
    print(f"Output: {INTEL_DIR}")
    print(f"Mode: {'parallel' if args.parallel else 'sequential'}")
    print("=" * 60)

    start = time.time()

    if args.parallel:
        print("\nLaunching Agent 1 (track/market) and Agent 2 (form/horse) in parallel...")
        p1 = subprocess.Popen(
            [sys.executable, agent1, args.date],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        p2 = subprocess.Popen(
            [sys.executable, agent2, args.date],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        _, err1 = p1.communicate()
        _, err2 = p2.communicate()
        r1 = p1.returncode
        r2 = p2.returncode
        if err1:
            sys.stderr.write(err1.decode("utf-8", errors="replace"))
        if err2:
            sys.stderr.write(err2.decode("utf-8", errors="replace"))
    else:
        print("\nRunning Agent 1 (track/market)...")
        r1 = subprocess.call([sys.executable, agent1, args.date])

        print("\nRunning Agent 2 (form/horse)...")
        r2 = subprocess.call([sys.executable, agent2, args.date])

    elapsed = time.time() - start

    log_lines = [
        "STRIDE Intelligence Build Log",
        f"Date: {args.date}",
        f"Built: {datetime.now().isoformat()}",
        f"Elapsed: {elapsed:.1f}s",
        f"Mode: {'parallel' if args.parallel else 'sequential'}",
        "",
        f"Agent 1 (track/market): {'OK' if r1 == 0 else f'FAILED (exit code {r1})'}",
        f"Agent 2 (form/horse):   {'OK' if r2 == 0 else f'FAILED (exit code {r2})'}",
        "",
        "Files:",
    ]

    all_ok = (r1 == 0 and r2 == 0)
    missing = []
    for fname in REQUIRED_FILES:
        fpath = INTEL_DIR / fname
        if fpath.exists():
            md5 = hashlib.md5(fpath.read_bytes()).hexdigest()
            size_kb = fpath.stat().st_size / 1024
            log_lines.append(f"  {fname}: {md5} ({size_kb:.1f} KB)")
        else:
            log_lines.append(f"  {fname}: MISSING")
            missing.append(fname)

    if missing:
        all_ok = False
        log_lines.append(f"\nMISSING FILES: {', '.join(missing)}")

    log_lines.append(f"\nStatus: {'ALL OK' if all_ok else 'INCOMPLETE'}")

    log_text = "\n".join(log_lines) + "\n"
    (INTEL_DIR / "build_log.txt").write_text(log_text)

    print("\n" + "=" * 60)
    print(log_text)

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
