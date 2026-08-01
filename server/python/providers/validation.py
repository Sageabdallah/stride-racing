"""Ingest contract validation.

Every payload crossing the provider boundary is checked here before it can
reach disk or the database. The failure philosophy is loud-not-silent: a
provider change that breaks the schema must stop the pipeline with a named
problem, never flow through as nulls that quietly degrade model edges.

Hard problems  -> data is rejected (racecard not saved / results not applied)
Warnings       -> data is accepted but the anomaly is printed, so early
                  declarations (no barriers yet, no odds yet) still work.
"""

import sys
from typing import Dict, List

# Early-morning cards legitimately lack barriers/jockeys for some runners;
# beyond these rates the shape of the feed itself has likely changed.
MAX_NAMELESS_RUNNER_RATE = 0.20
WARN_MISSING_FIELD_RATE = 0.50

_NAME_KEYS = ("horse", "horse_name", "name", "runner_name")
_POSITION_KEYS = ("position", "finishing_position", "finish_position")


class IngestValidationError(Exception):
    def __init__(self, problems: List[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


class ValidationReport:
    def __init__(self, context: str):
        self.context = context
        self.problems: List[str] = []
        self.warnings: List[str] = []

    @property
    def ok(self) -> bool:
        return not self.problems

    def raise_if_failed(self):
        if self.problems:
            raise IngestValidationError(self.problems)

    def print_report(self, stream=sys.stderr):
        for p in self.problems:
            print(f"  [INGEST FAIL] {self.context}: {p}", file=stream)
        for w in self.warnings:
            print(f"  [ingest warn] {self.context}: {w}", file=stream)


def _runner_name(runner: Dict) -> str:
    for key in _NAME_KEYS:
        value = runner.get(key)
        if value and str(value).strip():
            return str(value).strip()
    return ""


def validate_meet_cards(meet_cards: List[Dict], date_str: str) -> ValidationReport:
    report = ValidationReport(f"racecards {date_str}")

    if not isinstance(meet_cards, list) or not meet_cards:
        report.problems.append("no meet cards returned")
        return report

    total_races = 0
    empty_races = 0
    total_runners = 0
    nameless_runners = 0
    missing_draw = 0
    missing_jockey = 0

    for meet in meet_cards:
        course = meet.get("course") or "?"
        if not meet.get("meet_id"):
            report.problems.append(f"{course}: meet missing meet_id")
        if not meet.get("course"):
            report.problems.append("meet missing course name")

        races = meet.get("races")
        if not isinstance(races, list) or not races:
            report.problems.append(f"{course}: meet has no races list")
            continue

        for race in races:
            total_races += 1
            if race.get("race_number") is None:
                report.problems.append(f"{course}: race missing race_number")
            if not race.get("course"):
                report.problems.append(f"{course}: race missing course")
            if not race.get("date"):
                report.problems.append(f"{course}: race missing date")

            runners = race.get("runners")
            if not isinstance(runners, list) or not runners:
                empty_races += 1
                continue

            for runner in runners:
                total_runners += 1
                if not _runner_name(runner):
                    nameless_runners += 1
                if runner.get("draw") in (None, "", 0):
                    missing_draw += 1
                if not runner.get("jockey"):
                    missing_jockey += 1

    if total_races and empty_races == total_races:
        report.problems.append(
            f"all {total_races} races have zero runners — feed shape likely changed"
        )
    elif empty_races:
        report.warnings.append(f"{empty_races}/{total_races} races have no declared runners yet")

    if total_runners:
        nameless_rate = nameless_runners / total_runners
        if nameless_rate > MAX_NAMELESS_RUNNER_RATE:
            report.problems.append(
                f"{nameless_runners}/{total_runners} runners have no horse name "
                f"({nameless_rate:.0%} > {MAX_NAMELESS_RUNNER_RATE:.0%} limit)"
            )
        elif nameless_runners:
            report.warnings.append(f"{nameless_runners}/{total_runners} runners have no horse name")

        if missing_draw / total_runners > WARN_MISSING_FIELD_RATE:
            report.warnings.append(f"{missing_draw}/{total_runners} runners missing barrier draw")
        if missing_jockey / total_runners > WARN_MISSING_FIELD_RATE:
            report.warnings.append(f"{missing_jockey}/{total_runners} runners missing jockey")

    return report


def validate_results(results: List[Dict], date_str: str) -> ValidationReport:
    report = ValidationReport(f"results {date_str}")

    if not isinstance(results, list):
        report.problems.append("results payload is not a list")
        return report
    if not results:
        # No completed races is normal early in the day — callers retry later.
        report.warnings.append("no completed races returned")
        return report

    for result in results:
        track = result.get("course") or result.get("track") or ""
        label = track or "?"
        if not track:
            report.problems.append("result missing track/course name")

        try:
            int(result.get("race_number"))
        except (TypeError, ValueError):
            report.problems.append(f"{label}: result race_number not numeric: {result.get('race_number')!r}")

        runners = result.get("runners")
        if not isinstance(runners, list) or not runners:
            report.problems.append(f"{label}: result has no runners")
            continue

        placed = sum(1 for r in runners if any(r.get(k) for k in _POSITION_KEYS))
        if placed == 0:
            report.problems.append(f"{label} R{result.get('race_number')}: no runner has a finishing position")
        nameless = sum(1 for r in runners if not _runner_name(r))
        if nameless:
            report.warnings.append(f"{label} R{result.get('race_number')}: {nameless} result runners unnamed")

    return report
