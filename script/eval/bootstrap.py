"""
Bootstrap confidence interval computation.

Shared utility for all evaluation modules. Computes 95% CIs
via percentile bootstrap (Efron, 1979).
"""

from __future__ import annotations

from typing import Callable

import numpy as np

from .config import BOOTSTRAP_CI_LEVEL, BOOTSTRAP_N_RESAMPLES, BOOTSTRAP_SEED


def bootstrap_ci(
    values: np.ndarray,
    statistic: Callable[[np.ndarray], float] = np.mean,
    n_resamples: int = BOOTSTRAP_N_RESAMPLES,
    ci_level: float = BOOTSTRAP_CI_LEVEL,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float, float]:
    """
    Compute bootstrap confidence interval for a statistic.

    Args:
        values: 1-D array of observations
        statistic: Function to compute on each bootstrap sample
        n_resamples: Number of bootstrap iterations
        ci_level: Confidence level (e.g. 0.95)
        seed: Random seed for reproducibility

    Returns:
        (point_estimate, ci_lower, ci_upper)
    """
    rng = np.random.RandomState(seed)
    n = len(values)

    if n == 0:
        return (0.0, 0.0, 0.0)

    point = float(statistic(values))

    boot_stats = np.empty(n_resamples)
    for i in range(n_resamples):
        sample = rng.choice(values, size=n, replace=True)
        boot_stats[i] = statistic(sample)

    alpha = 1.0 - ci_level
    ci_lower = float(np.percentile(boot_stats, 100 * alpha / 2))
    ci_upper = float(np.percentile(boot_stats, 100 * (1 - alpha / 2)))

    return (point, ci_lower, ci_upper)


def balanced_accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """
    Compute balanced accuracy: mean per-class recall.

    Handles class imbalance by weighting each class equally.
    """
    classes = np.unique(y_true)
    if len(classes) == 0:
        return 0.0

    recalls = []
    for c in classes:
        mask = y_true == c
        if mask.sum() == 0:
            continue
        recalls.append((y_pred[mask] == c).mean())

    return float(np.mean(recalls)) if recalls else 0.0


def format_ci(
    point: float,
    ci_lower: float,
    ci_upper: float,
    pct: bool = True,
) -> str:
    """Format a metric with confidence interval for reporting."""
    if pct:
        return f"{point*100:.1f}% [{ci_lower*100:.1f}%, {ci_upper*100:.1f}%]"
    return f"{point:.3f} [{ci_lower:.3f}, {ci_upper:.3f}]"
