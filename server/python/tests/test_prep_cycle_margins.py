"""The prep-cycle path the pipeline runs must read a winner's margin as none.

#157 (2026-09-03) introduced result_margins.beaten_margin and applied it to
every reader it found, including server/python/intelligence/
build_prep_cycles.py. That file is not the prep-cycle builder the pipeline
runs: nothing imports it, and stride_build.py runs stride_agent_form.py,
whose build_prep_cycles reads race history through
_batch_prefetch_fitness_data and fitness_peak._fetch_race_history. Both
still read margin_lengths raw. Under the Punting Form convention, which
stores the winning margin for the winner, a three-length win entered
margins_this_prep as three lengths lost, and analyze_form_trajectory's
margin trend counted the horse's best run as its worst.

These pin both loaders, the duplicate-row fill, and the consumer.
"""

import fitness_peak
import stride_agent_form

RACE_DATE = "2026-09-04"

# One preparation: every run within 60 days of the next and of RACE_DATE.
OLD_WIN = ("2026-06-01", 1, None, 1200, "BM64")   # pre-June importer: NULL for the winner
PF_WIN = ("2026-07-20", 1, 3.0, 1200, "BM64")     # Punting Form: the winning margin
LOSS = ("2026-08-10", 4, 2.5, 1400, "BM70")
PLACE = ("2026-08-28", 2, 0.5, 1400, "BM70")


class _Cursor:
    """Answers the two history queries by the table each one names."""

    def __init__(self, training_rows, history_rows):
        self._training, self._history = training_rows, history_rows
        self._last = ""

    def execute(self, sql, params=None):
        self._last = sql

    def fetchall(self):
        if "FROM training_data" in self._last:
            return self._training
        if "FROM race_results_history" in self._last:
            return self._history
        return []

    def close(self):
        pass


class _Conn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur


def test_fitness_peak_loader_reads_a_win_as_no_margin_under_both_conventions():
    conn = _Conn(_Cursor([], [OLD_WIN, PF_WIN, LOSS, PLACE]))
    runs = fitness_peak._fetch_race_history("Horse", RACE_DATE, conn)
    assert [r["position"] for r in runs] == [1, 1, 4, 2]
    assert [r["margin"] for r in runs] == [None, None, 2.5, 0.5]


def test_fitness_peak_loader_does_not_refill_a_winner_from_a_duplicate_row():
    # training_data carries the date with no margin; the race_results_history
    # duplicate used to fill it with whatever was stored, winning margin included.
    training = [("2026-07-20", 1, None, 1200, "BM64"),
                ("2026-08-10", 4, None, 1400, "BM70")]
    conn = _Conn(_Cursor(training, [PF_WIN, LOSS]))
    runs = fitness_peak._fetch_race_history("Horse", RACE_DATE, conn)
    assert [r["margin"] for r in runs] == [None, 2.5]


def test_prep_margins_leave_the_win_out_so_the_trend_is_not_poisoned():
    conn = _Conn(_Cursor([], [PF_WIN, LOSS, PLACE]))
    prep = fitness_peak.detect_current_prep("Horse", RACE_DATE, conn)
    assert prep["positions_this_prep"] == [1, 4, 2]
    assert prep["margins_this_prep"] == [2.5, 0.5]


def test_batch_prefetch_applies_the_same_rule_to_what_it_serves():
    training = [("Horse", "2026-07-20", 1, None, 1200, "BM64")]
    history = [("horse", "2026-07-20", 1, 3.0, 1200, "BM64"),
               ("horse", "2026-08-10", 4, 2.5, 1400, "BM70"),
               ("horse", "2026-08-28", 2, 0.5, 1400, "BM70")]
    conn = _Conn(_Cursor(training, history))
    original = fitness_peak._fetch_race_history
    fitness_peak.clear_cache()
    try:
        stride_agent_form._batch_prefetch_fitness_data(["Horse"], RACE_DATE, conn)
        runs = fitness_peak._fetch_race_history("Horse", RACE_DATE, conn)  # served from the batch
        assert [r["margin"] for r in runs] == [None, 2.5, 0.5]
        prep = fitness_peak.detect_current_prep("Horse", RACE_DATE, conn)
        assert prep["margins_this_prep"] == [2.5, 0.5]
    finally:
        fitness_peak._fetch_race_history = original
        fitness_peak.clear_cache()
