#!/usr/bin/env python3
"""Shared market-probability helpers (ROI roadmap task 05).

Single source of truth for converting bookmaker odds into probabilities:

- proportional de-vig (overround-corrected "true" market probability),
  matching the semantics of run_tips_pipeline.py's true_market computation
  (``(100 / odds) / overround``);
- ``reference_price()`` — the median price across bookmakers for one runner;
- ``renormalise_probs()`` — per-race rescaling so published probabilities
  sum to 100 exactly.

Task 10 (execution & pricing) extends this module with alternative de-vig
methods (Shin/power) — keep it clean and free of pipeline-specific logic.

Probabilities are on the percent scale (0-100) to match the pipeline's
``winPercentage`` convention. All functions are pure and DB-free.
"""

from statistics import median


def valid_odds(value):
    """Coerce a value to decimal odds strictly greater than 1, else None."""
    if value is None:
        return None
    try:
        dec = float(str(value).replace("$", ""))
    except (TypeError, ValueError):
        return None
    return dec if dec > 1 else None


def implied_prob_pct(odds):
    """Raw implied probability (percent), with no overround correction."""
    dec = valid_odds(odds)
    return 100.0 / dec if dec else 0.0


def overround(odds_list):
    """Book overround as a divisor on the probability scale (1.0 = fair book).

    Matches run_tips_pipeline.calculate_overround semantics: with fewer than
    two valid quotes the book cannot be de-vigged, so return 1.0 (no-op).
    """
    total = 0.0
    valid = 0
    for o in odds_list:
        dec = valid_odds(o)
        if dec:
            total += 100.0 / dec
            valid += 1
    if valid < 2 or total <= 0:
        return 1.0
    return total / 100.0


def true_market_prob_pct(odds, overround_value):
    """Overround-corrected (proportional de-vig) market probability, percent.

    Exactly the pipeline's true_market: ``(100 / odds) / overround``.
    A missing/invalid overround degrades gracefully to raw implied.
    """
    dec = valid_odds(odds)
    if not dec:
        return 0.0
    try:
        ov = float(overround_value)
    except (TypeError, ValueError):
        ov = 1.0
    if ov <= 0:
        ov = 1.0
    return (100.0 / dec) / ov


def devig_probabilities_pct(odds_list):
    """Proportional de-vig of a whole book; the result sums to 100.0."""
    implied = [implied_prob_pct(o) for o in odds_list]
    total = sum(implied)
    if total <= 0:
        return [0.0 for _ in implied]
    return [p * 100.0 / total for p in implied]


def devig_probabilities_pct_power(odds_list, tol=1e-10, max_iter=100):
    """Power de-vig: implied probs raised to k, k solved so the book sums to 1.

    Corrects favourite-longshot bias better than proportional on typical win
    books (longshots carry proportionally more vig).
    """
    implied = [implied_prob_pct(o) / 100.0 for o in odds_list]
    if not implied or any(p <= 0 for p in implied):
        return [0.0 for _ in implied]
    lo, hi = 0.25, 4.0
    for _ in range(max_iter):
        k = (lo + hi) / 2.0
        total = sum(p ** k for p in implied)
        if abs(total - 1.0) < tol:
            break
        if total > 1.0:
            lo = k
        else:
            hi = k
    total = sum(p ** k for p in implied)
    if total <= 0:
        return [0.0 for _ in implied]
    return [(p ** k) / total * 100.0 for p in implied]


def devig_probabilities_pct_shin(odds_list, tol=1e-10, max_iter=100):
    """Shin (1992) de-vig: models the book as balanced action plus a share z
    of insider money; solving for z yields fair probabilities.

    Standard iterative solution: with pi the implied probs (normalised) and
    z the insider share, p_i = (sqrt(z^2 + 4(1-z) pi^2 / total) - z) / (2(1-z)).
    z is solved so the p_i sum to 1.
    """
    implied = [implied_prob_pct(o) / 100.0 for o in odds_list]
    total = sum(implied)
    if not implied or total <= 0 or any(p <= 0 for p in implied):
        return [0.0 for _ in implied]

    def probs_for(z):
        if z >= 1.0:
            return None
        out = []
        for pi in implied:
            val = (z * z + 4.0 * (1.0 - z) * (pi * pi) / total) ** 0.5
            out.append((val - z) / (2.0 * (1.0 - z)))
        return out

    lo, hi = 0.0, 0.5
    for _ in range(max_iter):
        z = (lo + hi) / 2.0
        ps = probs_for(z)
        s = sum(ps)
        if abs(s - 1.0) < tol:
            break
        if s > 1.0:
            lo = z
        else:
            hi = z
    ps = probs_for((lo + hi) / 2.0)
    s = sum(ps)
    if s <= 0:
        return [0.0 for _ in implied]
    return [p / s * 100.0 for p in ps]


def devig_method() -> str:
    """STRIDE_DEVIG: proportional (default) | power | shin. The default only
    changes after the task 09 pre-registered comparison selects a winner on
    settled data; never flip it ad hoc (task 10 guardrail)."""
    import os
    method = os.environ.get("STRIDE_DEVIG", "proportional").strip().lower()
    return method if method in ("proportional", "power", "shin") else "proportional"


def devig(odds_list, method=None):
    """De-vig a whole book with the configured method; sums to 100.0."""
    method = method or devig_method()
    if method == "power":
        return devig_probabilities_pct_power(odds_list)
    if method == "shin":
        return devig_probabilities_pct_shin(odds_list)
    return devig_probabilities_pct(odds_list)


def reference_price(runner_odds):
    """Median decimal price across bookmakers for one runner.

    Accepts a list of bookmaker entries (dicts carrying a decimal/win_odds/
    odds/price key, or plain numbers/strings), a dict of {bookmaker: price},
    or a single price. Returns None when no valid quote exists.
    """
    if isinstance(runner_odds, dict):
        entries = list(runner_odds.values())
    elif isinstance(runner_odds, (list, tuple)):
        entries = list(runner_odds)
    else:
        entries = [runner_odds]

    quotes = []
    for entry in entries:
        if isinstance(entry, dict):
            for key in ("decimal", "win_odds", "odds", "price"):
                dec = valid_odds(entry.get(key))
                if dec:
                    quotes.append(dec)
                    break
        else:
            dec = valid_odds(entry)
            if dec:
                quotes.append(dec)
    if not quotes:
        return None
    return float(median(quotes))


def renormalise_probs(values, target=100.0, ndigits=2):
    """Rescale probabilities so they sum to ``target`` exactly (percent scale).

    Rounds to ``ndigits`` then assigns the rounding residual to the largest
    probability (largest-remainder correction), so the returned values sum to
    ``target`` within float epsilon despite rounding. A non-positive total
    (e.g. all scratched runners) is returned unchanged as zeros.
    """
    vals = [max(0.0, float(v or 0.0)) for v in values]
    total = sum(vals)
    if total <= 0:
        return vals
    scaled = [round(v * target / total, ndigits) for v in vals]
    residual = round(target - sum(scaled), ndigits)
    if residual and scaled:
        idx = max(range(len(scaled)), key=lambda i: scaled[i])
        scaled[idx] = round(scaled[idx] + residual, ndigits)
    return scaled


def _self_test():
    print("market_prob self-test")

    probs = devig_probabilities_pct([2.0, 3.0, 4.0, 6.0, 10.0])
    assert abs(sum(probs) - 100.0) < 1e-9, sum(probs)
    print(f"  de-vig sums to 100: {[round(p, 2) for p in probs]}")

    ov = overround([2.0, 3.0, 4.0, 6.0, 10.0])
    assert ov > 1.0, ov
    assert true_market_prob_pct(2.0, ov) == (100.0 / 2.0) / ov
    assert overround([5.0]) == 1.0 and overround([]) == 1.0
    print(f"  overround {ov:.4f}; single-runner book degrades to 1.0")

    assert reference_price([{"bookmaker": "a", "decimal": 3.0},
                            {"bookmaker": "b", "decimal": 3.4},
                            {"bookmaker": "c", "decimal": 3.2}]) == 3.2
    assert reference_price({"a": 3.0, "b": 3.4, "c": 3.2}) == 3.2
    assert reference_price([3.0, "3.40", "$3.20"]) == 3.2
    assert reference_price([3.0, 3.4, 3.2, 3.3]) == 3.25
    assert reference_price(None) is None and reference_price([0.5, "x"]) is None
    print("  reference_price: median across bookmakers (dict/list/scalar)")

    renorm = renormalise_probs([33.33, 27.17, 15.9, 12.3, 11.3])
    assert abs(sum(renorm) - 100.0) < 1e-9, sum(renorm)
    assert renormalise_probs([0, 0, 0]) == [0.0, 0.0, 0.0]
    print(f"  renormalise: {renorm} sums to {sum(renorm):.6f}")

    print("All tests completed successfully.")


if __name__ == "__main__":
    _self_test()
