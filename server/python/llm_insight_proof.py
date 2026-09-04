#!/usr/bin/env python3
"""Prove that the configured LLM produces insight text, in the runtime that will use it.

Why this exists. The tips pipeline treats the LLM as optional: a provider that
cannot be reached is caught, logged once as a warning, and every pick then
carries ai_insight "" under a fresh ai_insight_generated_at timestamp. Nothing
downstream distinguishes that from a run whose insights were merely short. On
2026-09-02 the real run scored 90 top picks, produced 0 with text and 0 with an
AI score, spent 1.1 seconds in the LLM in total, and exited 0. That is the
silent no-op class this repository keeps finding, applied to the one output a
reader most wants to see.

The check is the thing itself, not a proxy for it: construct the provider the
environment names, make one trivial call so the raw error (a bad key, a retired
model id, a host that is not there) is printed rather than swallowed, then run
generate_rich_insight — the same function, the same system prompt, the same
fallback behaviour the pipeline uses — on two synthetic picks and print what
comes back. Exit 1 if either insight is empty. The picks are fixtures, not
today's card: this proves the path, not the form.

Markers, one per line, parsed by infra/jobs/handler.job_llm_proof:
    LLM_PROOF provider=<class> model=<id>      or  provider=UNAVAILABLE error=...
    LLM_PROOF ping=<repr of the one-word reply>
    LLM_PROOF pick=<horse, spaces as _> chars=<n>
    LLM_PROOF result=PASS  |  LLM_PROOF result=FAIL <reason>

Run it wherever the pipeline runs: `python llm_insight_proof.py` from
server/python with the same environment the pipeline gets.
"""

import argparse
import logging
import sys

from llm_provider import LLMProviderError, get_provider
from llm_post_scorer import generate_rich_insight

TRACK = "Randwick"
RACE_NUMBER = 4
DISTANCE = "1400m"
GOING = "Good 4"
RACE_CLASS = "BM78"


def build_field() -> list:
    """Six runners in the shape mc_api hands to the post-scorer.

    The first two are the picks. The first carries a full recent_runs history,
    the block _build_horse_summary turns into RECENT RACE HISTORY; the second
    has none, which is what a first-starter or an interstate import looks like
    to the prompt. The log therefore shows both behaviours side by side, and a
    reader can check the first insight against the runs it was given.
    """
    return [
        {
            "horse": "Ledger Line",
            "barrier": 4, "jockey": "T. Berry", "trainer": "C. Waller",
            "form": "", "runningStyle": "on_pace",
            "winPercentage": 24.5, "placePercentage": 58.0,
            "marketOdds": 6.5, "fairOdds": 4.1, "modelEdge": 9.1,
            "frankingElo": 1572, "fieldStrengthAvg": 1540, "formQualityTrend": 0.04,
            "prior_z200": 1.3, "prior_z400": 0.9,
            "classMovementDesc": "Drops from BM88 to BM78", "isClassDrop": True,
            "days_since_run": 20,
            "recent_runs": [
                {"track": "Rosehill", "date": "2026-08-15", "distance_m": 1400,
                 "race_class": "BM88", "position": 2, "margin": 0.8, "sp": 9.0,
                 "going": "Soft 5", "field_size": 11, "jockey": "T. Berry",
                 "barrier": 7, "weight": 56.5},
                {"track": "Randwick", "date": "2026-07-25", "distance_m": 1300,
                 "race_class": "BM78", "position": 4, "margin": 2.1, "sp": 7.5,
                 "going": "Good 4", "field_size": 12, "jockey": "T. Berry",
                 "barrier": 3, "weight": 57.0},
                {"track": "Warwick Farm", "date": "2026-07-04", "distance_m": 1200,
                 "race_class": "BM78", "position": 6, "margin": 3.4, "sp": 12.0,
                 "going": "Heavy 8", "field_size": 10, "jockey": "R. King",
                 "barrier": 10, "weight": 57.5},
            ],
        },
        {
            "horse": "Blank Docket",
            "barrier": 9, "jockey": "J. McDonald", "trainer": "G. Waterhouse & A. Bott",
            "form": "", "runningStyle": "backmarker",
            "winPercentage": 15.2, "placePercentage": 44.0,
            "marketOdds": 11.0, "fairOdds": 6.6, "modelEdge": 6.1,
            "frankingElo": 0, "prior_z200": 0.4, "prior_z400": 0.2,
            "days_since_run": 999,
            "recent_runs": [],
        },
        {
            "horse": "Market Leader",
            "barrier": 2, "jockey": "N. Rawiller", "trainer": "J. Pride",
            "form": "", "runningStyle": "leader",
            "winPercentage": 30.1, "placePercentage": 62.0,
            "marketOdds": 2.8, "fairOdds": 3.3, "modelEdge": -5.6,
            "frankingElo": 1590,
        },
        {
            "horse": "Steady Eddy",
            "barrier": 6, "jockey": "K. McEvoy", "trainer": "B. Baker",
            "form": "", "runningStyle": "midfield",
            "winPercentage": 12.0, "placePercentage": 38.0,
            "marketOdds": 8.0, "fairOdds": 8.3, "modelEdge": -0.5,
        },
        {
            "horse": "Wide Boy",
            "barrier": 12, "jockey": "T. Clark", "trainer": "M. Freedman",
            "form": "", "runningStyle": "midfield",
            "winPercentage": 9.0, "placePercentage": 30.0,
            "marketOdds": 15.0, "fairOdds": 11.1, "modelEdge": 2.3,
        },
        {
            "horse": "Late Mail",
            "barrier": 1, "jockey": "A. Hyeronimus", "trainer": "K. Lees",
            "form": "", "runningStyle": "backmarker",
            "winPercentage": 5.5, "placePercentage": 22.0,
            "marketOdds": 21.0, "fairOdds": 18.2, "modelEdge": 0.7,
        },
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Prove the configured LLM produces insight text before a run depends on it.")
    parser.add_argument("--max-chars", type=int, default=1500,
                        help="how much of each insight to print (default 1500)")
    args = parser.parse_args(argv)
    # llm_post_scorer reports a failed call through logger.warning and then
    # returns "". Without a handler that reason never reaches the log.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")

    try:
        llm = get_provider()
    except LLMProviderError as e:
        print(f"LLM_PROOF provider=UNAVAILABLE error={e}")
        print("LLM_PROOF result=FAIL the provider could not be constructed; "
              "the pipeline would run without insights")
        return 1
    print(f"LLM_PROOF provider={type(llm).__name__} model={llm.model}")

    try:
        reply = llm.generate("Reply with the single word READY.", max_tokens=16)
    except LLMProviderError as e:
        print(f"LLM_PROOF ping=ERROR {e}")
        print("LLM_PROOF result=FAIL the provider cannot complete a one-word call")
        return 1
    print(f"LLM_PROOF ping={reply[:40]!r}")

    field = build_field()
    empties = []
    for horse in field[:2]:
        text = generate_rich_insight(
            horse=horse, track=TRACK, race_number=RACE_NUMBER,
            distance=DISTANCE, going=GOING, race_class=RACE_CLASS,
            all_horses=field,
        ) or ""
        label = horse["horse"].replace(" ", "_")
        print(f"LLM_PROOF pick={label} chars={len(text.strip())}")
        print(f"----- insight for {horse['horse']} (first {args.max_chars} chars) -----")
        print(text[:args.max_chars] if text.strip() else "(empty)")
        print("-----")
        if not text.strip():
            empties.append(label)

    if empties:
        print(f"LLM_PROOF result=FAIL empty insight for {', '.join(empties)} — "
              f"the pipeline would publish blank ai_insight here")
        return 1
    print("LLM_PROOF result=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
