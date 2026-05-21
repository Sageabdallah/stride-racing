#!/usr/bin/env python3
"""Render a calibration curve (predicted vs actual win rate) from a backtest summary JSON.

Requires: matplotlib  (e.g. `pip install matplotlib`)

Usage:
    python3 scripts/plot_calibration.py examples/backtest_summary.json examples/calibration.png
"""
import json
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main(in_path: str, out_path: str) -> None:
    data = json.load(open(in_path))
    calib = data["calibration"]
    bins = [b for b in calib["bins"] if b["n_runners"] > 0]
    if not bins:
        print("no calibration bins to plot", file=sys.stderr)
        sys.exit(1)

    pred = [b["predicted_mean"] for b in bins]
    obs = [b["observed_mean"] for b in bins]
    n = [b["n_runners"] for b in bins]
    sizes = [max(40, min(900, 4 * cnt)) for cnt in n]

    fig, ax = plt.subplots(figsize=(7.5, 6.5), dpi=130)
    lim = max(0.6, max(pred + obs) + 0.05)
    ax.plot([0, lim], [0, lim], color="#888", linestyle="--", linewidth=1, label="perfect calibration")
    sc = ax.scatter(pred, obs, s=sizes, c="#1f77b4", alpha=0.75, edgecolor="white", linewidth=1.2, zorder=3)
    for p, o, c in zip(pred, obs, n):
        ax.annotate(f"n={c}", (p, o), textcoords="offset points", xytext=(8, 8), fontsize=9, color="#333")

    ax.set_xlim(0, lim)
    ax.set_ylim(0, lim)
    ax.set_xlabel("Predicted win probability (calibrated ensemble, race-normalised)")
    ax.set_ylabel("Observed win rate")
    period = f'{data["date_from"]} → {data["date_to"]}'
    brier = calib.get("brier_score")
    sub = f"{data['races']} races, {data['runners']} runners"
    if brier is not None:
        sub += f", Brier = {brier:.4f}"
    ax.set_title(f"STRIDE calibration  ({period})\n{sub}", fontsize=11)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left")

    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
