"""
Focal Loss implementation for XGBoost and LightGBM, designed for imbalanced horse racing prediction (~12% win rate). Downweights easy examples and focuses training on hard-to-classify samples.

FL(p_t) = -alpha_t * (1 - p_t)^gamma * log(p_t)

Reference: Lin et al., "Focal Loss for Dense Object Detection" (2017)
"""
import numpy as np
from typing import Tuple, Optional, Callable


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -500, 500)))


def focal_loss_objective(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    gamma: float = 2.0,
    alpha: float = 0.25,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    XGBoost-compatible focal loss objective (gradient and hessian).

    Focal Loss: FL(p_t) = -alpha_t * (1 - p_t)^gamma * log(p_t)
    Where:
        p = sigmoid(z) (z is raw logit from XGBoost)
        p_t = p if y=1, (1-p) if y=0
        alpha_t = alpha if y=1, (1-alpha) if y=0
    """
    y_true = np.asarray(y_true, dtype=np.float64)
    eps = 1e-7
    p = np.clip(_sigmoid(y_pred), eps, 1.0 - eps)

    y = y_true
    alpha_t = np.where(y == 1, alpha, 1.0 - alpha)
    p_t = np.where(y == 1, p, 1.0 - p)

    log_p_t = np.log(p_t + 1e-15)

    # Gradient computation:
    # dFL/dp_t = alpha_t * (gamma * (1-p_t)^(gamma-1) * log(p_t) - (1-p_t)^gamma / p_t)
    # dp_t/dp = 1 if y=1, -1 if y=0
    # dp/dz = p*(1-p)
    # grad = dFL/dp_t * dp_t/dp * dp/dz

    dFL_dp_t = alpha_t * (
        gamma * (1 - p_t)**(gamma - 1) * log_p_t
        - (1 - p_t)**gamma / (p_t + 1e-15)
    )

    dp_t_dp = np.where(y == 1, 1.0, -1.0)
    dp_dz = p * (1.0 - p)

    grad = dFL_dp_t * dp_t_dp * dp_dz

    # Hessian: proper second-order approximation
    # Scaled version of p*(1-p) weighted by focal loss terms
    # This maintains positive definiteness and captures the focal loss curvature
    hess = alpha_t * (1 - p_t)**gamma * p * (1.0 - p)
    hess = np.maximum(hess, 1e-6)

    return grad, hess


def focal_loss_eval(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    gamma: float = 2.0,
    alpha: float = 0.25,
) -> Tuple[str, float]:
    """XGBoost-compatible focal loss evaluation metric. Returns ('focal_loss', value)."""
    y_true = np.asarray(y_true, dtype=np.float64)
    eps = 1e-7
    p = np.clip(_sigmoid(y_pred), eps, 1.0 - eps)

    alpha_t = np.where(y_true == 1, alpha, 1.0 - alpha)
    p_t = np.where(y_true == 1, p, 1.0 - p)

    focal_loss = -alpha_t * np.power(1.0 - p_t, gamma) * np.log(p_t)
    return ("focal_loss", float(np.mean(focal_loss)))


def lgb_focal_loss_objective(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    gamma: float = 2.0,
    alpha: float = 0.25,
) -> Tuple[np.ndarray, np.ndarray]:
    """LightGBM-compatible focal loss objective. Returns gradient, hessian arrays."""
    return focal_loss_objective(y_true, y_pred, gamma, alpha)


def lgb_focal_loss_eval(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    gamma: float = 2.0,
    alpha: float = 0.25,
) -> Tuple[str, float, bool]:
    """LightGBM-compatible focal loss evaluation metric. Returns (name, value, is_higher_better)."""
    name, value = focal_loss_eval(y_true, y_pred, gamma, alpha)
    return (name, value, False)


class FocalLossConfig:
    """Configuration wrapper that produces framework-compatible objective and evaluation callables for XGBoost and LightGBM."""

    def __init__(
        self,
        gamma: float = 2.0,
        alpha: float = 0.25,
        enabled: bool = True,
    ):
        """
        Args:
            gamma: focusing parameter (0 = standard cross-entropy, 2 = default focal)
            alpha: positive class weight (0.25 = default, higher = more weight on winners)
            enabled: whether to use focal loss
        """
        self.gamma = gamma
        self.alpha = alpha
        self.enabled = enabled

    def get_xgb_objective(self) -> Callable:
        """Return XGBoost-compatible objective function."""
        gamma, alpha = self.gamma, self.alpha

        def obj(y_pred, dtrain):
            y_true = dtrain.get_label()
            return focal_loss_objective(y_true, y_pred, gamma, alpha)

        return obj

    def get_xgb_eval(self) -> Callable:
        """Return XGBoost-compatible eval metric."""
        gamma, alpha = self.gamma, self.alpha

        def eval_fn(y_pred, dtrain):
            y_true = dtrain.get_label()
            return focal_loss_eval(y_true, y_pred, gamma, alpha)

        return eval_fn

    def get_lgb_objective(self) -> Callable:
        """Return LightGBM-compatible objective function."""
        gamma, alpha = self.gamma, self.alpha

        def obj(y_true, y_pred):
            return lgb_focal_loss_objective(y_true, y_pred, gamma, alpha)

        return obj

    def get_lgb_eval(self) -> Callable:
        """Return LightGBM-compatible eval metric."""
        gamma, alpha = self.gamma, self.alpha

        def eval_fn(y_true, y_pred):
            return lgb_focal_loss_eval(y_true, y_pred, gamma, alpha)

        return eval_fn

    def to_dict(self) -> dict:
        """Serialize config to dictionary."""
        return {
            "gamma": self.gamma,
            "alpha": self.alpha,
            "enabled": self.enabled,
        }

    def __repr__(self) -> str:
        return (
            f"FocalLossConfig(gamma={self.gamma}, alpha={self.alpha}, "
            f"enabled={self.enabled})"
        )


class ValueAlignedLoss:
    """Value-aligned loss that weighs misclassification errors by potential betting value. Higher-odds winners that are missed receive a larger penalty so the model learns to spot high-value opportunities."""

    def __init__(self, odds_weight: float = 0.3, edge_bonus: float = 1.5):
        """
        Args:
            odds_weight: how much to scale the odds-based penalty (0-1)
            edge_bonus: multiplier applied when model has edge vs market
        """
        self.odds_weight = odds_weight
        self.edge_bonus = edge_bonus

    def _compute_sample_weights(
        self,
        y_true: np.ndarray,
        p: np.ndarray,
        odds: Optional[np.ndarray],
    ) -> np.ndarray:
        """Compute per-sample importance weights based on odds and edge."""
        weights = np.ones_like(y_true, dtype=np.float64)

        if odds is not None:
            odds = np.asarray(odds, dtype=np.float64)
            log_odds = np.log1p(np.maximum(odds, 1.0))
            odds_factor = 1.0 + self.odds_weight * (log_odds / np.max(log_odds + 1e-9))
            weights *= odds_factor

            implied_prob = 1.0 / np.maximum(odds, 1.01)
            edge = p - implied_prob
            positive_edge = np.maximum(edge, 0.0)
            edge_factor = 1.0 + self.edge_bonus * positive_edge
            weights *= np.where(y_true == 1, edge_factor, 1.0)

        return weights

    def objective(
        self,
        y_true: np.ndarray,
        y_pred: np.ndarray,
        odds: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Compute value-weighted gradient and hessian (XGBoost-compatible). Returns gradient, hessian arrays."""
        y_true = np.asarray(y_true, dtype=np.float64)
        eps = 1e-7
        p = np.clip(_sigmoid(y_pred), eps, 1.0 - eps)

        sample_weights = self._compute_sample_weights(y_true, p, odds)

        grad = (p - y_true) * sample_weights
        hess = p * (1.0 - p) * sample_weights
        hess = np.maximum(hess, 1e-6)

        return grad, hess

    def eval_metric(
        self,
        y_true: np.ndarray,
        y_pred: np.ndarray,
        odds: Optional[np.ndarray] = None,
    ) -> Tuple[str, float]:
        """Compute value-weighted log-loss for evaluation. Returns ('value_loss', value)."""
        y_true = np.asarray(y_true, dtype=np.float64)
        eps = 1e-7
        p = np.clip(_sigmoid(y_pred), eps, 1.0 - eps)

        sample_weights = self._compute_sample_weights(y_true, p, odds)

        loss = -(y_true * np.log(p) + (1.0 - y_true) * np.log(1.0 - p))
        weighted_loss = loss * sample_weights
        return ("value_loss", float(np.mean(weighted_loss)))

    def to_dict(self) -> dict:
        return {
            "odds_weight": self.odds_weight,
            "edge_bonus": self.edge_bonus,
        }

    def __repr__(self) -> str:
        return (
            f"ValueAlignedLoss(odds_weight={self.odds_weight}, "
            f"edge_bonus={self.edge_bonus})"
        )
