"""
Benter-style second-stage conditional-logit blend (opt-in).

Within each race:   P_i  ∝  exp( alpha·ln(m_i)  +  beta·ln(q_i) )

where m_i is the model's win probability for runner i and q_i is the
overround-corrected market probability. alpha and beta are fitted by
maximum likelihood over historical races (the winner's probability is
maximised race-conditionally, not runner-by-runner).

Why this exists — the literature is unambiguous that this is the strongest
known architecture for picking winners:

- Bolton & Chapman (1986, Management Science) established the conditional /
  multinomial logit as the canonical form for race win probabilities: winning
  is relative to the field, so probabilities should be estimated within-race,
  not as independent binary events.
- Benter (1994, "Computer-Based Horse Race Handicapping and Wagering
  Systems") combined his fundamental model with the public's implied
  probabilities in exactly this two-stage conditional logit and reported the
  blend outperforming BOTH inputs (R² 0.1396 combined vs 0.1245 fundamental
  vs 0.1218 public) — the basis of the most successful betting operation on
  record.

STRIDE's current market anchor is a *linear* per-runner blend with
hand-tuned odds-band weights (run_tips_pipeline.calibrate_and_score). This
module is the fitted, log-space, field-normalised version of the same idea.
It is strictly opt-in:

    1. Fit on your stored predictions:   python conditional_logit.py --fit
       (reads training_view_v2; writes models/conditional_logit.json and
        prints holdout hit-rate / log-loss for model-only, market-only and
        the blend so you can see the evidence before enabling anything)
    2. Enable in the tips pipeline:      STRIDE_CL_BLEND=true
       (without the fitted JSON, or with the env var unset, pipeline
        behaviour is unchanged)

Self-test (synthetic, no DB needed):     python conditional_logit.py
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_PATH = SCRIPT_DIR / "models" / "conditional_logit.json"

PROB_FLOOR = 1e-4
PROB_CEIL = 0.99


def _clip_probs(p):
    p = np.asarray(p, dtype=float)
    return np.clip(p, PROB_FLOOR, PROB_CEIL)


def _softmax(u):
    u = u - np.max(u)
    e = np.exp(u)
    return e / e.sum()


class ConditionalLogitBlend:
    """Two-parameter race-conditional blend of model and market probabilities."""

    def __init__(self, model_path=None):
        self.model_path = Path(model_path) if model_path else DEFAULT_MODEL_PATH
        self.alpha = None  # weight on ln(model prob)
        self.beta = None   # weight on ln(market prob)
        self.meta = {}

    @property
    def is_fitted(self):
        return self.alpha is not None and self.beta is not None

    # ------------------------------------------------------------ inference
    def transform_race(self, model_probs, market_probs):
        """
        Blend one race's probabilities. Inputs are per-runner win
        probabilities in [0, 1] (any consistent scale — they are re-clipped
        and field-normalised here). Returns a numpy array summing to 1.
        """
        if not self.is_fitted:
            raise RuntimeError("ConditionalLogitBlend is not fitted/loaded")
        m = _clip_probs(model_probs)
        q = _clip_probs(market_probs)
        u = self.alpha * np.log(m) + self.beta * np.log(q)
        return _softmax(u)

    # ------------------------------------------------------------- fitting
    def fit(self, races, holdout_frac=0.2, verbose=True):
        """
        races: list of (model_probs, market_probs, winner_index) tuples,
        ordered oldest→newest so the holdout is strictly the most recent
        slice (temporal, no shuffling).

        Fits alpha/beta by L-BFGS-B on the training slice and reports
        top-pick hit rate and mean log-loss of model-only / market-only /
        blend on the holdout. Returns the report dict.
        """
        from scipy.optimize import minimize

        clean = []
        for m, q, w in races:
            m = _clip_probs(m)
            q = _clip_probs(q)
            if len(m) >= 2 and len(m) == len(q) and 0 <= w < len(m):
                clean.append((m, q, int(w)))
        if len(clean) < 50:
            raise ValueError(f"Need at least 50 usable races to fit (got {len(clean)})")

        n_hold = max(1, int(len(clean) * holdout_frac)) if holdout_frac > 0 else 0
        train = clean[:-n_hold] if n_hold else clean
        hold = clean[-n_hold:] if n_hold else []

        log_m = [np.log(m) for m, _, _ in train]
        log_q = [np.log(q) for _, q, _ in train]
        winners = [w for _, _, w in train]

        def nll(params):
            a, b = params
            total = 0.0
            for lm, lq, w in zip(log_m, log_q, winners):
                u = a * lm + b * lq
                u = u - u.max()
                total -= u[w] - np.log(np.exp(u).sum())
            return total / len(train)

        res = minimize(nll, x0=np.array([0.5, 0.7]), method="L-BFGS-B",
                       bounds=[(0.0, 5.0), (0.0, 5.0)])
        self.alpha, self.beta = float(res.x[0]), float(res.x[1])

        report = {
            "alpha": round(self.alpha, 4),
            "beta": round(self.beta, 4),
            "n_train_races": len(train),
            "n_holdout_races": len(hold),
            "train_nll": round(float(res.fun), 5),
        }
        if hold:
            report["holdout"] = {
                "model_only": self._evaluate(hold, mode="model"),
                "market_only": self._evaluate(hold, mode="market"),
                "blend": self._evaluate(hold, mode="blend"),
            }
        self.meta = {**report, "fitted_at": datetime.now().isoformat()}

        if verbose:
            print(f"[CL] Fitted alpha={self.alpha:.3f} (model), beta={self.beta:.3f} (market) "
                  f"on {len(train)} races")
            for mode, stats in report.get("holdout", {}).items():
                print(f"[CL]   holdout {mode:12s}: top-pick hit "
                      f"{stats['hit_rate']:.3f} | log-loss {stats['log_loss']:.4f}")
        return report

    def _evaluate(self, races, mode):
        hits, ll = 0, 0.0
        for m, q, w in races:
            if mode == "model":
                p = _clip_probs(m) / _clip_probs(m).sum()
            elif mode == "market":
                p = _clip_probs(q) / _clip_probs(q).sum()
            else:
                p = self.transform_race(m, q)
            hits += int(np.argmax(p) == w)
            ll -= float(np.log(max(p[w], PROB_FLOOR)))
        n = len(races)
        return {"hit_rate": round(hits / n, 4), "log_loss": round(ll / n, 4), "n": n}

    # --------------------------------------------------------- persistence
    def save(self):
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"alpha": self.alpha, "beta": self.beta, "meta": self.meta,
                   "version": 1}
        with open(self.model_path, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"[CL] Saved to {self.model_path}")

    def load(self):
        try:
            with open(self.model_path) as f:
                payload = json.load(f)
            self.alpha = float(payload["alpha"])
            self.beta = float(payload["beta"])
            self.meta = payload.get("meta", {})
            return True
        except Exception:
            return False


# ------------------------------------------------------------------ fit CLI
def _load_db_url():
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    env_path = SCRIPT_DIR.parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.strip().startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"')
    return ""


_FIT_SQL = """
    SELECT race_date::text AS race_date, track, race_number::int AS race_number,
           predicted_win_prob::float8 AS predicted_win_prob,
           COALESCE(market_odds, sp_odds)::float8 AS odds,
           is_winner::int AS is_winner
    FROM training_view_v2
    WHERE is_winner IS NOT NULL
      AND predicted_win_prob IS NOT NULL
      AND COALESCE(market_odds, sp_odds) > 1
    ORDER BY race_date, track, race_number
"""


def _fetch_rows_pg(db_url):
    import psycopg2
    conn = psycopg2.connect(db_url, connect_timeout=10)
    cur = conn.cursor()
    cur.execute(_FIT_SQL)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def _fetch_rows_neon_http(db_url, page_size=50000):
    """
    Fallback for networks where Postgres TCP (5432) is blocked but HTTPS is
    open: Neon's SQL-over-HTTPS endpoint (the serverless driver protocol) —
    POST https://<endpoint-host>/sql with the connection string in the
    Neon-Connection-String header. Paginates to respect response limits.
    """
    import requests

    host = db_url.split("@", 1)[1].split("/", 1)[0].split("?", 1)[0]
    rows, offset = [], 0
    while True:
        page_sql = f"{_FIT_SQL} LIMIT {int(page_size)} OFFSET {int(offset)}"
        resp = requests.post(
            f"https://{host}/sql",
            headers={"Neon-Connection-String": db_url,
                     "Content-Type": "application/json"},
            json={"query": page_sql, "params": []},
            timeout=120,
        )
        resp.raise_for_status()
        payload = resp.json()
        fields = [f.get("name") for f in payload.get("fields", [])]
        batch = payload.get("rows", [])
        for r in batch:
            if isinstance(r, dict):
                rows.append((r["race_date"], r["track"], r["race_number"],
                             r["predicted_win_prob"], r["odds"], r["is_winner"]))
            else:  # arrayMode-style row
                d = dict(zip(fields, r))
                rows.append((d["race_date"], d["track"], d["race_number"],
                             d["predicted_win_prob"], d["odds"], d["is_winner"]))
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def _rows_to_races(rows, min_field=4):
    """
    Group ordered (race_date, track, race_number, prob, odds, won) rows into
    (model_probs, market_probs, winner_idx) fitting tuples. Transport-agnostic
    value coercion: numerics may arrive as strings (HTTP text format / CSV),
    and the winner flag as 1/0, t/f or true/false.
    """
    races, current_key, bucket = [], None, []

    def _as_bool(v):
        return str(v).strip().lower() in ("1", "t", "true")

    def flush(bucket):
        if len(bucket) < min_field:
            return
        winners = [i for i, r in enumerate(bucket) if r[2]]
        if len(winners) != 1:
            return
        m = np.array([max(float(r[0]), 1e-4) for r in bucket])
        m = m / 100.0 if m.max() > 1.5 else m  # stored as % or fraction
        implied = np.array([1.0 / float(r[1]) for r in bucket])
        q = implied / implied.sum()
        races.append((m, q, winners[0]))

    for rd, track, rn, prob, odds, won in rows:
        key = (str(rd), str(track).strip().lower(), int(float(rn or 0)))
        if key != current_key:
            if bucket:
                flush(bucket)
            current_key, bucket = key, []
        bucket.append((prob, odds, _as_bool(won)))
    if bucket:
        flush(bucket)
    return races


def fit_from_database(holdout_frac=0.2, min_field=4):
    """
    Fit alpha/beta from stored predictions in training_view_v2:
    the model's pre-race predicted_win_prob vs the SP-implied market
    probability, grouped per race, ordered by race_date.

    Uses a direct Postgres connection when possible, falling back to Neon's
    SQL-over-HTTPS endpoint when port 5432 is unreachable (e.g. sandboxed /
    proxy-only networks).
    """
    db_url = _load_db_url()
    if not db_url:
        print("[CL] DATABASE_URL not set — cannot fit from database", file=sys.stderr)
        return None

    try:
        rows = _fetch_rows_pg(db_url)
    except Exception as pg_err:
        print(f"[CL] Direct Postgres connection failed ({type(pg_err).__name__}) — "
              f"trying Neon SQL-over-HTTPS", file=sys.stderr)
        rows = _fetch_rows_neon_http(db_url)

    races = _rows_to_races(rows, min_field=min_field)

    print(f"[CL] Usable races: {len(races)}")
    blend = ConditionalLogitBlend()
    report = blend.fit(races, holdout_frac=holdout_frac)
    blend.save()
    return report


def fit_from_csv(csv_path, holdout_frac=0.2, min_field=4):
    """
    Fit from a CSV export (e.g. the Neon console SQL editor) instead of a
    live connection. Required columns, matching the _FIT_SQL aliases:
    race_date, track, race_number, predicted_win_prob, odds, is_winner.
    """
    import csv as _csv

    with open(csv_path, newline="") as f:
        reader = _csv.DictReader(f)
        missing = {"race_date", "track", "race_number", "predicted_win_prob",
                   "odds", "is_winner"} - set(reader.fieldnames or [])
        if missing:
            print(f"[CL] CSV missing columns: {sorted(missing)}", file=sys.stderr)
            return None
        rows = [(r["race_date"], r["track"], r["race_number"],
                 r["predicted_win_prob"], r["odds"], r["is_winner"])
                for r in reader]

    rows.sort(key=lambda r: (str(r[0]), str(r[1]).strip().lower(),
                             int(float(r[2] or 0))))
    races = _rows_to_races(rows, min_field=min_field)
    print(f"[CL] Usable races from CSV: {len(races)}")
    blend = ConditionalLogitBlend()
    report = blend.fit(races, holdout_frac=holdout_frac)
    blend.save()
    return report


# ---------------------------------------------------------------- self-test
def _self_test():
    print("=" * 60)
    print("conditional_logit self-test (synthetic)")
    print("=" * 60)
    rng = np.random.default_rng(11)
    races = []
    for _ in range(1200):
        n = int(rng.integers(6, 15))
        strength = rng.normal(0, 1, n)
        true_p = _softmax(1.6 * strength)
        winner = int(rng.choice(n, p=true_p))
        # model and market see the same latent truth through INDEPENDENT noise
        model_p = _softmax(1.6 * strength + rng.normal(0, 0.9, n))
        market_p = _softmax(1.6 * strength + rng.normal(0, 0.55, n))
        races.append((model_p, market_p, winner))

    blend = ConditionalLogitBlend(model_path="/tmp/cl_selftest.json")
    report = blend.fit(races, holdout_frac=1 / 3)
    hold = report["holdout"]

    assert blend.alpha > 0 and blend.beta > 0
    assert blend.beta > blend.alpha, "market (lower noise) should earn more weight"
    assert hold["blend"]["log_loss"] < hold["model_only"]["log_loss"]
    assert hold["blend"]["log_loss"] < hold["market_only"]["log_loss"]
    assert hold["blend"]["hit_rate"] >= hold["market_only"]["hit_rate"]

    p = blend.transform_race([0.4, 0.3, 0.2, 0.1], [0.5, 0.25, 0.15, 0.10])
    assert abs(p.sum() - 1.0) < 1e-9 and p[0] == p.max()

    blend.save()
    reloaded = ConditionalLogitBlend(model_path="/tmp/cl_selftest.json")
    assert reloaded.load() and abs(reloaded.alpha - blend.alpha) < 1e-9
    p2 = reloaded.transform_race([0.4, 0.3, 0.2, 0.1], [0.5, 0.25, 0.15, 0.10])
    assert np.allclose(p, p2)

    print("All conditional_logit self-tests passed "
          f"(blend beat both inputs on {hold['blend']['n']} holdout races).")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Conditional-logit blend fitter")
    parser.add_argument("--fit", action="store_true",
                        help="Fit alpha/beta from training_view_v2 and save the model")
    parser.add_argument("--csv", metavar="PATH",
                        help="Fit from a CSV export instead of a live DB "
                             "(columns: race_date,track,race_number,"
                             "predicted_win_prob,odds,is_winner)")
    parser.add_argument("--holdout", type=float, default=0.2,
                        help="Temporal holdout fraction for the fit report")
    args = parser.parse_args()

    if args.csv:
        fit_from_csv(args.csv, holdout_frac=args.holdout)
    elif args.fit:
        fit_from_database(holdout_frac=args.holdout)
    else:
        _self_test()
