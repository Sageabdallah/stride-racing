# Market-baseline analyses have no negative control

**Type:** governance / analysis integrity · **Raised:** 2026-08-02 · **Status:** finding, no code written yet

This came out of the consensus rebuild but is not about consensus. It applies to
every analysis in the repo that measures something *conditional on the market*,
and none of them currently has the check that would catch it.

---

## The finding

`market_prob.devig_method()` defaults to `proportional`:

```python
def devig_method() -> str:
    """STRIDE_DEVIG: proportional (default) | power | shin. The default only
    changes after the task 09 pre-registered comparison selects a winner on
    settled data; never flip it ad hoc (task 10 guardrail)."""
```

Proportional de-vig divides out the overround uniformly. It therefore leaves
**favourite–longshot bias** in the resulting probabilities: longshots stay
over-priced relative to their true chance. Any analysis that uses those
probabilities as a baseline inherits that residual, and any covariate correlated
with market rank will pick it up as apparent signal.

Measured on ~11,000 real AU races (`race_results_history`, SP present, field
5–24, median overround 1.217), scoring covariates that carry **zero information
by construction**:

| Covariate (zero information) | proportional | power | shin |
|---|---|---|---|
| `1{market rank == 1}` | **Z = +7.63** | +1.10 | +2.23 |
| `log p_market` | **Z = +11.36** | +2.01 | +0.38 |
| zero-skill crowd, q ∝ p^1.0 | **Z = +5.97** | +0.42 | +0.38 |
| zero-skill crowd, q ∝ p^1.5 | **Z = +7.77** | +1.23 | +0.66 |
| zero-skill crowd, q ∝ p^2.0 | **Z = +7.61** | +0.01 | +2.31 |

Z scales as √n, so at a 1,000-race window — roughly 40 race days — a covariate
that is *provably pure noise* still lands at **Z ≈ +2.3, p ≈ 0.02**. That is a
publishable-looking result produced entirely by the baseline.

The failure mode is not a null result. It is a **confident wrong answer**, and
nothing in the current protocol would catch it.

## What is exposed

Every analysis that conditions on a market baseline. None has a negative control:

| Analysis | How it touches the baseline |
|---|---|
| `validate_forward.py` | The registered rule's `edge >= 3pp vs de-vigged market prob` term — this is the live one; **VR-001 and VR-002 both inherit it** |
| `shadow_calibrator_compare.py` | Calibration bins scored against market-anchored probabilities |
| `tier_pnl_attribution.py` | Per-tier counterfactual ROI at SP |
| `walk_forward_backtest.py` | ROI-at-thresholds over de-vigged edge |
| the proposed consensus marginal-contribution test | Market as offset |

## What is *not* proposed

**Do not set `STRIDE_DEVIG=power`.** The docstring above registers a task-10
guardrail against ad-hoc flipping, and the default is itself the subject of a
pre-registered task-09 comparison. Flipping a global default to fix an analysis
would breach that guardrail and change the live pricing path at the same time.

## What is proposed

1. **A shared `negative_control.py`.** Given a window's races and a de-vig
   method, simulate a zero-skill covariate (a market-mirroring crowd at several
   γ, plus `rank==1` and `log p`) and return the score-test Z for each. ~30 lines
   of real work.
2. **Run it as a hard pre-flight**, before the real covariate is looked at. If
   `|Z| > 1` on a zero-information covariate, the baseline is unfit and no result
   from that analysis is interpretable. Fail the run; do not report.
3. **Analysis-time de-vig selection**, never a global flip: a caller that needs a
   bias-corrected baseline passes `devig(odds, method="power")` explicitly in its
   own code path and declares that choice in its registration. The global default
   and the live path stay untouched.
4. **Market-rank splines as free covariates** alongside the offset wherever a
   model is fitted, so residual bias is absorbed rather than attributed.
5. **A precondition in `09-forward-validation-protocol.md`**: no entry may be
   registered without (a) a system-health pre-flight on the system under test and
   (b) a passing negative control on the analysis method.

## Why (5) is the durable part

VR-001 was invalidated because pre-registration protected the *rule* and said
nothing about whether the *system under test* was working
([VR-001-invalidation.md](VR-001-invalidation.md)). This finding is the same
class of gap one level down: the protocol also says nothing about whether the
*analysis method* can distinguish signal from its own baseline. Both are cheap to
check and neither is currently required.

A registration that survives both checks means something. One that survives
neither can be defeated by a retired model id or a de-vig default.
