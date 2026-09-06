# The Monte Carlo Engines

Monte Carlo simulation is how STRIDE turns per-runner strength estimates into
win/place probabilities: sample thousands of plausible race outcomes, count how often
each horse wins. The repo contains **three separate MC engines** — they do not share
a core, and only two of them run in production.

Related docs: [Architecture](01-architecture.md) · [Daily pipeline](02-daily-pipeline.md) ·
[ML training & calibration](05-ml-training-and-calibration.md)

---

## 1. Engine map

| Engine | File | Sampling method | Role |
|---|---|---|---|
| `MonteCarloEngine` | `monte_carlo.py` (repo root) | skew-normal performance scores → argsort | Standalone CLI showcase — **not imported by anything** |
| Plackett-Luce MC | `racing_system_v8.3_mc.py` → `simulate_race_monte_carlo` (line 1781) | Dirichlet-perturbed probabilities + Gumbel-max | **Production base engine** |
| Realistic / sectional MC | `server/python/realistic_simulate.py` | mixture noise + multi-phase energy + sectional profiles | **Production overlay engine** |

`server/python/mc_api.py` (7,782 lines — the biggest file in the repo) orchestrates the
two production engines. Despite the name it is **not an HTTP server**: it's a
stdin→stdout JSON child process (for the excluded Node/Express frontend) that the
Python pipeline also loads **in-process** via importlib
(`run_tips_pipeline.run_mc_simulation`, `run_tips_pipeline.py:384`) so model caches
survive across a full race card.

(`adaptive_mc.py`, an adaptive sim-count sampler that nothing imported, was removed
in the 2026-09 cleanup.)

---

## 2. The production simulation, step by step (`mc_api.run_simulation`, mc_api.py:7014)

```
run_tips_pipeline / Express subprocess
        └─ mc_api.run_simulation(race, runners, mc_sims=10000, seed=42)
             ├─ 1. get_or_build_base_model()        → RacingModel (v8.2 factor model)
             ├─ 2. RacingModel.analyze(race)        → model_prob per runner (17 factors)
             ├─ 3. simulate_race_monte_carlo(...)   → base win/place probs (Plackett-Luce)
             ├─ 4. apply llm_mu_adjustment          → LLM pre-analysis nudges (cap 0.60 after renorm)
             ├─ 5. MCRecalibrator.transform_race    → isotonic recalibration (if model file exists)
             ├─ 6. simulate_race_with_sectional_profiles → realistic/sectional MC
             ├─ 7. blend: base × 0.70 + sectional × 0.30
             ├─ 8. feature adjustment: ml 0.55 + sophisticated 0.22 + enhanced 0.13 + fitness 0.10
             │      (win prob capped 60%, floored 1%; place capped 90%)
             ├─ 9. field-normalize to 100%, compute edge / EV / fair odds
             ├─ 10. log_feature_snapshots / log_prediction_audit / log_race_schedule (DB)
             └─ 11. banker_detector.detect_bankers  → banker_flag / banker_score
```

Notable mechanics inside `mc_api.py`:

- **Model cache.** The `RacingModel` is trained once from racecard directories and
  cached in module globals, keyed on directory mtimes (`_build_model_cache_key`,
  `mc_api.py:482`). This is why the pipeline loads mc_api in-process.
- **~25 optional feature modules** are imported behind `try/except` with
  `*_AVAILABLE` flags (`mc_api.py:57-280`) — the engine degrades gracefully when a
  module or its data is missing. Env toggles: `MC_ENABLE_SECTIONAL_FRANKING`,
  `MC_ENABLE_JOCKEY_EFFICIENCY` (the tips pipeline defaults both to `false` for
  full-card runs, `run_tips_pipeline.py:79-98`).
- **DB connection sharing.** One hot psycopg2 connection per DSN, installed by
  monkey-patching `psycopg2.connect` (`_SharedDbConnection` /
  `_shared_psycopg2_connect` in `mc_api.py`), 15 s statement timeout. The pooled
  connection is **autocommit** and ignores `close()` — right for the per-runner
  enrichment reads, wrong for a writer that needs a transaction. Because the
  patch is process-wide, every `psycopg2.connect` call made after mc_api loads
  gets the pool, including the tips pipeline's own `db_connect()`. The wrapper
  therefore publishes the real driver function as `__wrapped__`, and
  `run_tips_pipeline.db_connect(transactional=True)` peels the pool off for
  `store_selections_in_db`, which runs deactivate-then-insert as one
  transaction with a savepoint per pick (audit 2026-09-06, H2).
- All logging goes to **stderr**; stdout is reserved for the JSON result.
- Top pick is flagged `isConfidentSelection` when win% ≥ 18, margin over 2nd ≥ 5 pts
  and EV ≥ 0 (`mc_api.py:7634`).

Sim counts: the tips pipeline passes 5,000 iterations for fields ≤ 10, 3,000 for ≤ 14,
2,000 above that (`run_tips_pipeline.get_iterations`, `:374-381`); mc_api's own
default is 10,000; the base engine's standalone default is 20,000.

---

## 3. The base engine — Plackett-Luce (`racing_system_v8.3_mc.py:1781`)

`racing_system_v8.3_mc.py` (3,286 lines) is dual-role: an importable library (this is
what production uses) *and* a standalone CLI system (`--mc_tips6`, `--backtest`).
The name means "v8.2 factor model + MC layer".

1. **Priors.** `RacingModel.analyze()` produces `model_prob` per runner from 17
   weighted factor scores (`FEATURE_WEIGHTS`, line 46 — form, class, distance,
   course, jockey, barrier, weight, pace…). Probabilities are soft-clipped and
   renormalized.
2. **Uncertainty.** Per simulation, probabilities are drawn from a **Dirichlet**
   distribution whose concentration scales with evidence:
   `conc = max(6.0, 12.0 + 1.3 × n_historical_runs)` — more data ⇒ tighter samples.
   Going multiplies concentration: heavy ×0.72, soft ×0.82, synthetic ×1.06, firm ×1.04.
3. **Pace scenario.** Each sim samples a leader-rate `Normal(track_base, 0.07)` and
   pace pressure; the regime (slow/even/fast/meltdown) shifts logits via
   `scenario_adjustments` (line 347): leaders +0.04 baseline but −0.05 in a meltdown,
   backmarkers the mirror image.
4. **Finishing order.** Plackett-Luce sampling via the **Gumbel-max trick**:
   `order = argsort(−(log p + adjustments + Gumbel noise))` (lines 1852-1857).
5. **Outputs.** Win/top-2/top-3 frequencies, expected position, **Wilson** confidence
   intervals (α = 0.10), a stability score, scenario sensitivity, and per-regime pace
   splits. Probability clamps: min 0.001, max 0.70 (`MC_SIM_LIMITS`, line 145).

The same file supplies production staking maths: fractional Kelly
(`KELLY_FRACTION_DEFAULT = 0.25`, `MAX_KELLY_STAKE = 0.05`), selection scoring
(`mc_selection_score`, line 1908), playability gates and a 12-unit per-track cap.

---

## 4. The overlay engine — realistic simulation (`realistic_simulate.py`)

Called with the base engine's win probabilities as latent strength
(`mu = log(win_prob)`), then re-simulates the race with more physical realism.
Entry point: `simulate_race_with_sectional_profiles` (line 966), falling back to
`simulate_race_realistic` (line 698) when no sectional history exists.

What "realistic" means here (it is race physics, **not** bankroll simulation):

- **Mixture noise** (`generate_mixture_noise`, line 533): base Normal, plus a
  *downside* Normal component (probability 0.30 on good going → 0.40 on heavy;
  shift 0.8–1.08σ), plus a **Student-t (df 4.5) fat tail** (8% → 16% on heavy).
  Captures the empirical fact that horses fail more often than they over-deliver.
- **Per-runner sigma** (`calculate_runner_sigma`, line 467): base 1.0 scaled by form
  consistency, sample size, market position (favourite ×0.9, longshot ×1.2), elite
  jockey ×0.95, fatigue/collapse/λ-decay, clamped [0.6, 2.0]; then a race-type
  multiplier (sprint ×1.30 … staying ×0.88; heavy ×1.22; maiden ×1.25) annotated as
  fit on 26,769 results.
- **Multi-phase energy model** (`simulate_multi_phase`, line 368): 4 race phases,
  style-dependent advantage per phase (leaders +0.06–0.08 early, −0.08 in the sprint)
  and energy depletion modulated by the sampled pace (hot 1.3× … slow 0.6×).
- **Sectional profiles**: early/home medians and stds per horse are sampled per sim;
  the field's speed-horse count derives a pace scenario distribution
  (HOT ≥ 3 speed horses / GENUINE / SOFT / SLOW).
- **Tactical layers**: jockey position-dependent bonuses
  (`JOCKEY_TACTICAL_PROFILES`), tight-turn track effects, an RSI race-shape
  correction (±0.08), and five quant-engine mu shifts from `sectional_quant.py`
  (SASR upgrade, pace collapse, race-shape fit, closing rank, trip efficiency —
  combined clip ±0.25).
- **Collapse events**: horses with collapse probability > 0.05 can draw an
  exponential penalty mid-sim.

Outputs mirror the base engine (win/place from counts, Wilson CIs, exactas/trifectas)
plus `pace_scenario_distribution` and a per-race confidence flag (top prob ≥ 0.18).

The production blend is **70% base + 30% sectional overlay** (`mc_api.py:7342`) —
`convert_mc_results_to_realistic` documents this as a deliberate gradual-migration
strategy.

---

## 5. MC recalibration (`mc_recalibration.py`)

Corrects the favourite-longshot bias of raw simulation output (documented in-file:
50–100% predicted band actually won ~38%; 0–5% band won ~4%).

- `fit_from_database` re-simulates historical races (5,000 sims, seed 42) and pairs
  predicted vs actual outcomes.
- A custom **Pool-Adjacent-Violators** isotonic fit (`:157`) produces a monotone
  mapping stored as JSON (`calibration_model.json` — git-ignored, so recalibration is
  wired but **inert in this published tree**).
- `transform` clips to [0.005, 0.95]; `transform_race` renormalizes each race to
  sum 1.

This is one of five distinct calibration layers in the system — see
[ML training & calibration](05-ml-training-and-calibration.md#5-the-calibration-story--five-distinct-layers)
for how they fit together.

---

## 6. The standalone showcase engine (`monte_carlo.py`)

A complete, self-contained engine used from the CLI only
(`python monte_carlo.py --input race.json -n 20000 --seed 42 --with_exotics`):

- Strength: `base_strength` (0–100) → mus; optional blend with market implied
  probability (35% market weight); market drift nudges mu (cap ±0.15).
- Noise: **skew-normal** with α = −2.0 (underperformance more likely than over).
- Pace regimes sampled per sim: slow 25% / even 45% / fast 22% / meltdown 8%;
  barrier and tight-turn effects added.
- Outputs: win/place with **bootstrap** CIs (1,000 resamples, P5–P95), fair odds vs
  market edge, quarter-Kelly stakes (cap 5%), exactas/trifectas, a
  stat-weighted composite ranking, and meeting-level bet-slip formatting.
- `--integrated` mode dynamically loads `racing_system_v8.3_mc.py` and blends its
  factor model at 40% weight (`IntegratedAnalyzer`, line 1139).

It duplicates several concepts of the production stack (pace regimes, track
leader-bias tables, tight-turn lists, Kelly) with slightly different numbers — see
§8. Verified working end-to-end in this environment (see
[docs/README](README.md#what-was-verified-by-running-code)).

---

## 7. Interface contracts

**Single-race** (stdin JSON or in-process call):

```jsonc
// input
{ "track": "...", "race_number": 5, "distance": "1400m", "going": "Good",
  "raceClass": "BM78", "raceDate": "YYYY-MM-DD",
  "runners": [ { "horse": "...", "barrier": 4, "odds": [...], "form": "1213",
                 "llm_mu_adjustment": 0.03, ... } ],
  "mc_sims": 10000, "seed": 42 }
// output
{ "success": true, "results": [ { "horse": "...", "winPercentage": 23.4,
    "placePercentage": 58.1, "expectedValue": 0.12, "edge": 3.1, "fairOdds": 4.3,
    "ciLower": 21.9, "ciUpper": 24.8, "kellyStake": 1.2, "selectionScore": 14.2,
    "rawWinProb": ..., "calibratedWinProb": ..., "sectionalMcWinProb": ...,
    "trackBiasPoints": ..., "fitnessData": {...}, "mlAdjustmentBreakdown": {...},
    "banker_flag": false, ... } ],
  "paceScenarioDistribution": {...}, "sectionalMcExactas": [...], "isConfidentRace": true }
```

**Batch mode** (`{"mode": "batch", "races": [...]}`) drives
`TipGenerator.generate_mc_tips_for_date` and returns up to 6 MC tips per track.

---

## 8. Duplications & dead code to be aware of

- `TIGHT_TURN_TRACKS` is defined three times (monte_carlo.py:82,
  realistic_simulate.py:213, referenced from pace_modeling) with matching values.
- Track leader-bias tables exist twice with *slightly different* numbers
  (`TRACK_LEADER_BIAS` monte_carlo.py:73 vs `TRACK_PROFILES`
  racing_system_v8.3_mc.py:84 — e.g. Flemington 0.20 vs 0.18).
- Elite-jockey lists exist in two representations (weighted dict in racing_system;
  tactical profiles in realistic_simulate).
- CI method differs by engine: bootstrap (monte_carlo.py) vs Wilson (everything else).
- `monte_carlo.py`'s `CalibrationEngine` is unused (`adaptive_mc.py`, also unused, was
  removed in the 2026-09 cleanup).
- `DEFAULT_CONFIG['race_shock_sigma']` in monte_carlo.py is dead — the race-level
  shared shock was removed ("audit fix #4", lines 639-642) because it inflated CIs.
