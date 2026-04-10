"""
Validation and reporting for ANCO-HITS scores.

Computes AUC, prints score summaries, and generates matplotlib plots.
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend for headless compatibility
import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import roc_auc_score

from .bipartite import BipartiteGraph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AUC
# ---------------------------------------------------------------------------

def compute_auc(
    case_scores: np.ndarray,
    case_outcomes: np.ndarray,
) -> float | None:
    """
    Compute ROC AUC for separating PLAINTIFF_WINS (+1) vs DEFENDANT_WINS (-1).

    Excludes MIXED (0) cases. Returns None if insufficient data.
    """
    mask = case_outcomes != 0
    if mask.sum() < 2:
        return None

    scores = case_scores[mask]
    labels = (case_outcomes[mask] > 0).astype(int)  # 1 = PLT_WINS, 0 = DEF_WINS

    # Need at least one of each class
    if labels.sum() == 0 or labels.sum() == len(labels):
        return None

    return float(roc_auc_score(labels, scores))


# ---------------------------------------------------------------------------
# Score summary
# ---------------------------------------------------------------------------

def print_score_summary(
    argument_scores: np.ndarray,
    case_scores: np.ndarray,
    bipartite: BipartiteGraph,
    convergence_history: list[float],
    argument_texts: dict[str, str] | None = None,
) -> None:
    """Print comprehensive score summary to stdout."""
    outcomes = bipartite.case_outcomes

    print(f"\n{'='*60}")
    print("  ANCO-HITS SCORING RESULTS")
    print(f"{'='*60}")

    # Convergence
    print(f"\n  Convergence:")
    print(f"    Iterations:     {len(convergence_history)}")
    if convergence_history:
        print(f"    Final delta:    {convergence_history[-1]:.2e}")

    # Case scores by outcome
    print(f"\n  Case scores by outcome:")
    for label, name in [(1.0, "PLAINTIFF_WINS"), (-1.0, "DEFENDANT_WINS"), (0.0, "MIXED")]:
        mask = outcomes == label
        count = int(mask.sum())
        if count > 0:
            scores = case_scores[mask]
            print(
                f"    {name:18s} (n={count:3d}): "
                f"mean={scores.mean():+.3f}  std={scores.std():.3f}  "
                f"[{scores.min():+.3f}, {scores.max():+.3f}]"
            )

    # AUC
    auc = compute_auc(case_scores, outcomes)
    if auc is not None:
        plt_count = int((outcomes > 0).sum())
        def_count = int((outcomes < 0).sum())
        print(f"\n  AUC (PLT vs DEF): {auc:.4f}  ({plt_count} PLT, {def_count} DEF)")
    else:
        print(f"\n  AUC: insufficient data")

    # Argument score distribution
    print(f"\n  Argument score distribution ({len(argument_scores)} arguments):")
    bands = [
        ("+0.5 to +1.0 (strong plaintiff)", 0.5, 1.01),
        ("+0.1 to +0.5 (moderate plaintiff)", 0.1, 0.5),
        ("-0.1 to +0.1 (contested)", -0.1, 0.1),
        ("-0.5 to -0.1 (moderate defendant)", -0.5, -0.1),
        ("-1.0 to -0.5 (strong defendant)", -1.01, -0.5),
    ]
    for name, lo, hi in bands:
        count = int(((argument_scores >= lo) & (argument_scores < hi)).sum())
        print(f"    {name:40s} {count:4d}")

    # Top/bottom arguments
    sorted_idx = np.argsort(argument_scores)

    print(f"\n  Top 10 arguments (plaintiff-favorable):")
    for rank, idx in enumerate(reversed(sorted_idx[-10:]), 1):
        h = bipartite.argument_hashes[idx]
        score = argument_scores[idx]
        text = argument_texts.get(h, h[:20] + "...") if argument_texts else h[:20] + "..."
        print(f"    {rank:2d}. [{score:+.3f}] {text[:80]}")

    print(f"\n  Bottom 10 arguments (defendant-favorable):")
    for rank, idx in enumerate(sorted_idx[:10], 1):
        h = bipartite.argument_hashes[idx]
        score = argument_scores[idx]
        text = argument_texts.get(h, h[:20] + "...") if argument_texts else h[:20] + "..."
        print(f"    {rank:2d}. [{score:+.3f}] {text[:80]}")

    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def plot_case_scores(
    case_scores: np.ndarray,
    case_outcomes: np.ndarray,
    output_path: Path,
) -> None:
    """Histogram of case scores colored by outcome label."""
    fig, ax = plt.subplots(figsize=(10, 6))

    for label, name, color in [
        (1.0, "PLAINTIFF_WINS", "#2ecc71"),
        (-1.0, "DEFENDANT_WINS", "#e74c3c"),
        (0.0, "MIXED", "#95a5a6"),
    ]:
        mask = case_outcomes == label
        if mask.sum() > 0:
            ax.hist(
                case_scores[mask],
                bins=20,
                range=(-1.1, 1.1),
                alpha=0.6,
                label=f"{name} (n={int(mask.sum())})",
                color=color,
            )

    ax.set_xlabel("ANCO-HITS Case Score")
    ax.set_ylabel("Count")
    ax.set_title("Case Score Distribution by Outcome")
    ax.legend()
    ax.axvline(x=0, color="black", linestyle="--", alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    logger.info(f"Saved case score plot: {output_path}")


def plot_argument_distribution(
    argument_scores: np.ndarray,
    output_path: Path,
) -> None:
    """Histogram of all argument scores."""
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.hist(argument_scores, bins=30, range=(-1.1, 1.1), color="#3498db", alpha=0.7)
    ax.set_xlabel("ANCO-HITS Argument Score")
    ax.set_ylabel("Count")
    ax.set_title(f"Argument Score Distribution (n={len(argument_scores)})")
    ax.axvline(x=0, color="black", linestyle="--", alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    logger.info(f"Saved argument distribution plot: {output_path}")


def plot_convergence(
    history: list[float],
    output_path: Path,
) -> None:
    """Max-delta per iteration convergence curve."""
    if not history:
        return

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(range(1, len(history) + 1), history, "o-", markersize=4, color="#e67e22")
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Max |Δ score|")
    ax.set_title("ANCO-HITS Convergence")
    ax.set_yscale("log")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    logger.info(f"Saved convergence plot: {output_path}")
