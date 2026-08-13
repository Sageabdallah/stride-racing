"""Final-action helpers shared by gates, risk controls, export and ledger."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Tuple

from identity_normalization import normalize_runner_key
from prediction_stages import put_stage


_GATE_FIELDS = (
    "should_bet", "selection_origin", "selection_origin_reason",
    "refusal_reason", "crowd_score", "crowd_classification",
    "crowd_gate_reason", "convergence_tier", "convergence_score",
    "convergence_gate", "stake_recommendation",
)


def _matching_pick(active: Dict[str, Any], picks: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    key = normalize_runner_key(active.get("horse"))
    if not key:
        return None
    return next(
        (pick for pick in picks if normalize_runner_key(pick.get("horse")) == key),
        None,
    )


def reconcile_crowd_bet(
    bet_pick: Optional[Dict[str, Any]],
    evaluated_picks: Iterable[Dict[str, Any]],
    *,
    gate_evaluated: bool,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str, str]:
    """Apply the crowd verdict to the one active bet contract.

    Returns ``(active, refused, status, reason)``. A required gate without a
    matching decision fails closed so an unevaluated copy cannot escape to
    risk, export, or the ledger.
    """
    if not isinstance(bet_pick, dict):
        return None, None, "NO_BET", "No model-backed candidate cleared the bet guardrail."
    if not gate_evaluated:
        stages = dict(bet_pick.get("prediction_stages") or {})
        put_stage(stages, "final_decision", "BET", note="Bet guardrail accepted; crowd gate inactive.")
        bet_pick["prediction_stages"] = stages
        return bet_pick, None, "BET", str(bet_pick.get("selection_origin_reason") or "Model-backed bet.")

    matched = _matching_pick(bet_pick, evaluated_picks)
    active = dict(bet_pick)
    if matched is None:
        refused = dict(active)
        refused.update({
            "should_bet": False,
            "staking": "0u",
            "selection_origin": "crowd_gated",
            "selection_origin_reason": "Crowd gate produced no matching runner decision.",
            "refusal_reason": "crowd_gate_missing_decision",
        })
        stages = dict(refused.get("prediction_stages") or {})
        put_stage(stages, "final_decision", "NO_BET", note=refused["selection_origin_reason"])
        refused["prediction_stages"] = stages
        return None, refused, "NO_BET", refused["selection_origin_reason"]

    for field in _GATE_FIELDS:
        if field in matched:
            active[field] = matched[field]
    if active.get("should_bet"):
        stages = dict(active.get("prediction_stages") or {})
        put_stage(stages, "final_decision", "BET", note="Crowd gate confirmed active bet.")
        active["prediction_stages"] = stages
        return active, None, "BET", str(
            active.get("selection_origin_reason") or active.get("crowd_gate_reason") or "Crowd gate confirmed."
        )

    reason = str(
        active.get("crowd_gate_reason")
        or active.get("selection_origin_reason")
        or "Crowd gate vetoed the model-backed candidate."
    )
    active.update({
        "should_bet": False,
        "staking": "0u",
        "selection_origin": "crowd_gated",
        "selection_origin_reason": reason,
        "refusal_reason": "crowd_veto",
    })
    stages = dict(active.get("prediction_stages") or {})
    put_stage(stages, "final_decision", "NO_BET", note=reason)
    active["prediction_stages"] = stages
    return None, active, "NO_BET", reason


def demote_active_bet(
    race: Dict[str, Any],
    *,
    refusal_reason: str,
    selection_origin: str,
    explanation: str,
) -> Optional[Dict[str, Any]]:
    """Atomically turn an active race bet into its durable refused contract."""
    pick = race.get("bet_pick")
    if not isinstance(pick, dict):
        return None
    refused = dict(pick)
    refused.update({
        "should_bet": False,
        "staking": "0u",
        "refusal_reason": refusal_reason,
        "selection_origin": selection_origin,
        "selection_origin_reason": explanation,
    })
    stages = dict(refused.get("prediction_stages") or {})
    put_stage(stages, "final_decision", "NO_BET", note=explanation)
    refused["prediction_stages"] = stages
    key = normalize_runner_key(refused.get("horse"))
    for collection_name in ("top_picks", "full_field"):
        for runner in race.get(collection_name) or []:
            if normalize_runner_key(runner.get("horse")) != key:
                continue
            runner.update({field: refused[field] for field in (
                "should_bet", "staking", "refusal_reason",
                "selection_origin", "selection_origin_reason",
                "prediction_stages",
            )})
    race["refused_bet_pick"] = refused
    race["bet_pick"] = None
    race["bet_status"] = "NO_BET"
    race["bet_status_reason"] = explanation
    return refused
