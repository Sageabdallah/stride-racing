"""Regression tests for the post-audit Phase -1 correction pass."""

from __future__ import annotations

from backfill_tips_contract import backfill_race_contract


class _SelectionCursor:
    def __init__(self):
        self.calls = []
        self.closed = False

    def execute(self, query, params=None):
        self.calls.append((query, params))

    def close(self):
        self.closed = True


class _SelectionConnection:
    def __init__(self):
        self.cursor_instance = _SelectionCursor()
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _bettable_pick(horse="A"):
    return {
        "horse": horse,
        "rank": 1,
        "odds": 4.0,
        "has_real_market_odds": True,
        "edge_pct": 4.0,
        "raw_model_pct": 20.0,
        "win_pct": 20.0,
        "confidence": "high",
        "should_bet": True,
        "staking": "1u",
    }


def test_no_bet_status_cannot_insert_primary_pick_fallback(monkeypatch):
    import run_tips_pipeline as pipeline

    connection = _SelectionConnection()
    monkeypatch.setattr(pipeline, "db_connect", lambda: connection)
    pipeline.store_selections_in_db([{
        "track": "Randwick",
        "race_number": 1,
        "bet_status": "NO_BET",
        "bet_pick": None,
        "refused_bet_pick": {**_bettable_pick(), "should_bet": False},
        "primary_pick": _bettable_pick(),
    }], "2026-08-10")

    insert_calls = [
        query for query, _params in connection.cursor_instance.calls
        if "INSERT INTO selections" in query
    ]
    assert insert_calls == []
    assert connection.committed is True


def test_backfill_preserves_reconciled_crowd_veto():
    refused = {
        **_bettable_pick(),
        "should_bet": False,
        "staking": "0u",
        "refusal_reason": "crowd_veto",
        "selection_origin_reason": "MODEL_ONLY — no crowd support",
    }
    race = {
        "raw_model_leader": _bettable_pick(),
        "full_field": [_bettable_pick()],
        "top_picks": [_bettable_pick()],
        "bet_pick": None,
        "refused_bet_pick": refused,
        "bet_status": "NO_BET",
        "bet_status_reason": "MODEL_ONLY — no crowd support",
    }
    updated = backfill_race_contract(race)
    assert updated["bet_pick"] is None
    assert updated["bet_status"] == "NO_BET"
    assert updated["refused_bet_pick"]["refusal_reason"] == "crowd_veto"


def test_backfill_still_upgrades_legacy_unreconciled_race():
    leader = _bettable_pick()
    race = {
        "full_field": [leader],
        "top_picks": [leader],
    }
    updated = backfill_race_contract(race)
    assert "refused_bet_pick" not in updated
    assert updated["bet_status"] == "BET"
    assert updated["bet_pick"]["horse"] == "A"
    assert updated["bet_pick"]["should_bet"] is True


def test_out_of_range_stage_is_marked_without_losing_race(capsys):
    from mc_api import _attach_prediction_stages

    result = {"horse": "A", "selectionScore": 17.0}
    survived = _attach_prediction_stages(
        result,
        {"xgb": 0.2, "lightgbm": 0.2, "catboost": 0.2, "ensemble": 0.2},
        {"win_prob_sim_raw": 1.0000001, "win_prob_sim": 0.25},
        25.0,
        1.0,
        1.0,
        25.0,
    )
    assert survived is result
    assert survived["horse"] == "A"
    violation = survived["predictionStages"]["mc_raw"]
    assert violation["value"] == 1.0000001
    assert violation["range_violation"] is True
    assert "probability must be in [0, 1]" in violation["recording_error"]
    stderr = capsys.readouterr().err
    assert stderr.count("[PREDICTION_STAGE_RECORDING_VIOLATION]") == 1
    assert "stage=mc_raw" in stderr
