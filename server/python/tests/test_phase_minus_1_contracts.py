"""Phase −1 acceptance contracts: identity, schema, decisions and stages."""

from __future__ import annotations

from identity_normalization import (
    DDL_FUNCTIONS,
    canonical_race_key,
    normalize_runner_key,
    normalize_track_key,
)
from decision_contract import reconcile_crowd_bet
from prediction_stages import finalise_stages, put_stage
from training_view_contract import (
    TRAINING_VIEW_REQUIRED_COLUMNS,
    TrainingViewSchemaError,
    missing_required_columns,
    validate_training_view_schema,
)


def test_canonical_identity_examples_and_circuit_separation():
    assert normalize_runner_key("Golden Crusader") == "goldencrusader"
    assert normalize_runner_key("Ocean Deep (NZ)") == "oceandeep"
    assert normalize_track_key("Sandown-Lakeside") == "sandownlakeside"
    assert normalize_track_key("Sandown Hillside") == "sandownhillside"
    assert normalize_track_key("Sandown-Lakeside") != normalize_track_key("Sandown Hillside")
    assert canonical_race_key("2026-08-10", "Royal Randwick", "3") == \
        canonical_race_key("2026-08-10", "Randwick", 3)


def test_sql_contract_lowercases_before_case_sensitive_strip():
    runner_body = DDL_FUNCTIONS.split("CREATE OR REPLACE FUNCTION stride_norm_track", 1)[0]
    assert "SELECT regexp_replace(\n    lower(regexp_replace" in runner_body
    track_body = DDL_FUNCTIONS.split("CREATE OR REPLACE FUNCTION stride_norm_track", 1)[1]
    assert "regexp_replace(lower(" in track_body
    assert "'sandownlakeside'" not in DDL_FUNCTIONS  # no circuit collapse alias


class _Cursor:
    def __init__(self, columns):
        self.columns = columns

    def execute(self, query, params):
        assert "pg_attribute" in query
        assert params == ("training_view_v2",)

    def fetchall(self):
        return [(column,) for column in self.columns]

    def close(self):
        pass


class _Connection:
    def __init__(self, columns):
        self.columns = columns

    def cursor(self):
        return _Cursor(self.columns)


def test_training_view_contract_accepts_materialized_view_columns():
    actual = validate_training_view_schema(_Connection(TRAINING_VIEW_REQUIRED_COLUMNS + ("extra",)))
    assert actual[-1] == "extra"


def test_training_view_contract_names_all_missing_consumer_fields():
    columns = tuple(c for c in TRAINING_VIEW_REQUIRED_COLUMNS if c not in {
        "tip_time_odds", "odds_source", "seconds_to_jump"})
    assert missing_required_columns(columns) == (
        "tip_time_odds", "odds_source", "seconds_to_jump")
    try:
        validate_training_view_schema(_Connection(columns))
    except TrainingViewSchemaError as exc:
        message = str(exc)
    else:
        raise AssertionError("missing training columns did not fail closed")
    assert "tip_time_odds" in message
    assert "odds_source" in message
    assert "seconds_to_jump" in message


def test_crowd_veto_reaches_final_active_bet_contract():
    original = {"horse": "Ocean Deep (NZ)", "should_bet": True, "staking": "1u"}
    gated = [{
        "horse": "Ocean Deep", "should_bet": False,
        "crowd_gate_reason": "MODEL_ONLY — no crowd support",
        "crowd_classification": "MODEL_ONLY",
    }]
    active, refused, status, reason = reconcile_crowd_bet(
        original, gated, gate_evaluated=True)
    assert active is None
    assert status == "NO_BET"
    assert refused["horse"] == "Ocean Deep (NZ)"
    assert refused["should_bet"] is False
    assert refused["staking"] == "0u"
    assert refused["refusal_reason"] == "crowd_veto"
    assert "no crowd support" in reason
    assert refused["prediction_stages"]["final_decision"]["value"] == "NO_BET"


def test_crowd_confirm_preserves_bet_and_copies_gate_fields():
    original = {
        "horse": "Ocean Deep (NZ)", "should_bet": True,
        "selection_origin_reason": "Model-backed bet.",
    }
    gated = [{
        "horse": "Ocean Deep", "should_bet": True,
        "crowd_score": 0.82, "crowd_classification": "CONSENSUS",
        "crowd_gate_reason": "Crowd and model agree.",
    }]
    active, refused, status, reason = reconcile_crowd_bet(
        original, gated, gate_evaluated=True)
    assert refused is None
    assert status == "BET"
    assert active["should_bet"] is True
    assert active["crowd_score"] == 0.82
    assert active["crowd_classification"] == "CONSENSUS"
    assert active["crowd_gate_reason"] == "Crowd and model agree."
    assert active["prediction_stages"]["final_decision"]["value"] == "BET"
    assert reason == "Model-backed bet."


def test_crowd_gate_inactive_preserves_legacy_bet():
    original = {
        "horse": "A", "should_bet": True,
        "selection_origin_reason": "Legacy model-backed bet.",
    }
    active, refused, status, reason = reconcile_crowd_bet(
        original, [], gate_evaluated=False)
    assert active is original
    assert refused is None
    assert status == "BET"
    assert reason == "Legacy model-backed bet."
    assert active["prediction_stages"]["final_decision"]["value"] == "BET"


def test_crowd_gate_missing_decision_fails_closed():
    original = {"horse": "A", "should_bet": True, "staking": "1u"}
    active, refused, status, reason = reconcile_crowd_bet(
        original, [{"horse": "B", "should_bet": True}], gate_evaluated=True)
    assert active is None
    assert status == "NO_BET"
    assert refused["should_bet"] is False
    assert refused["staking"] == "0u"
    assert refused["refusal_reason"] == "crowd_gate_missing_decision"
    assert refused["prediction_stages"]["final_decision"]["value"] == "NO_BET"
    assert "no matching runner decision" in reason.lower()


def test_crowd_veto_is_the_refused_ledger_candidate(monkeypatch):
    import run_tips_pipeline as pipeline
    import selection_ledger

    captured = []

    class _Conn:
        def close(self):
            pass

    monkeypatch.setenv("STRIDE_LEDGER_WRITE", "true")
    monkeypatch.setattr(pipeline, "db_connect", lambda: _Conn())
    monkeypatch.setattr(selection_ledger, "persist_rows",
                        lambda _conn, rows: captured.extend(rows))
    refused = {
        "horse": "A", "should_bet": False, "staking": "0u",
        "win_pct": 25.0, "raw_model_pct": 28.0, "odds": 4.0,
        "selection_origin": "crowd_gated", "refusal_reason": "crowd_veto",
    }
    pipeline.persist_selection_ledger([{
        "track": "Randwick", "race_number": 1,
        "bet_pick": None, "refused_bet_pick": refused,
        "raw_model_leader": {"horse": "Wrong fallback"}, "top_picks": [],
    }], "2026-08-10")
    assert len(captured) == 1
    assert captured[0]["horse"] == "A"
    assert captured[0]["refused"] is True
    assert captured[0]["selection_origin"] == "crowd_gated"


def test_prediction_quantities_are_typed_and_scores_are_not_probabilities():
    stages = {}
    put_stage(stages, "ensemble", 0.27)
    put_stage(stages, "combined_adjustment", 1.08)
    put_stage(stages, "selection_score", 17.4)
    assert stages["ensemble"]["quantity_type"] == "probability"
    assert stages["combined_adjustment"]["quantity_type"] == "multiplier"
    assert stages["selection_score"]["quantity_type"] == "score"
    final = finalise_stages({"winPercentage": 22.0, "selectionScore": 19.0,
                             "predictionStages": stages})
    assert final["market_context_probability"]["value"] == 0.22
    assert final["selection_score"]["value"] == 19.0
