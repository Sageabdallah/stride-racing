# Model guide

`STRIDE-How-the-model-makes-up-its-mind.docx` is a plain-English explanation of
what the model weighs and how a rating becomes a bet, written for a reader with
no background in racing analytics or statistics.

It covers the factor weights, the three-learner blend, the two probability
blends, edge and expected value, the three-pillar convergence, the five
verdicts, the consensus and market injections, and staking. It also states what
it cannot tell you — the 25.45% of model weight that is zero-filled at serve,
and the absence of any measured performance baseline in this repository.

## It is generated, not written by hand

Every figure comes out of the repository's own records:

| Source | What it supplies |
|---|---|
| `docs/research/feature_liveness_report.json` | factor weights, trained-column count, zero-at-serve set |
| `server/python/ml_model.py` | base-learner shares, race categories, declared features |
| `server/python/consensus_blender.py` | pillar weights, thresholds, tiers, injections |
| `server/python/run_tips_pipeline.py` | probability blends, price ladder, confidence, staking |
| `server/python/market_prob.py` | de-vigged market probability |

Constants are read with `ast.literal_eval`, never regex-scraped or retyped.
`groups.py` asserts every factor lands in exactly one theme and that the theme
shares sum to the total. `verify_doc.py` reads the finished `.docx` back and
re-checks all 254 figures against the source data; it exits non-zero on any
mismatch.

## Rebuilding after a retrain

The weights describe one artifact (v2, trained 2026-04-15). A retrain changes
them and the guide goes stale. To rebuild:

    cd docs/model-guide/generate
    ./generate.sh

Requires `node` with the `docx` package and `python3` with `matplotlib`.
The hand-written plain-English descriptions live in `plain.json`; a retrain that
adds a factor needs a line added there, and `verify_doc.py` will fail until it is.
