"""
ANCO-HITS algorithm for scoring legal arguments and cases.

Implements Equation 1 from Gokalp et al. "Partisan Scale" (ICTAI):

  x_i^(k) = Σ_j (a_ij * y_j^(k-1)) / Σ_j |a_ij * y_j^(k-1)|
  y_j^(k) = Σ_i (a_ij * x_i^(k)) / Σ_i |a_ij * x_i^(k)|

Uses per-entity normalization (each score divided by sum of its own
absolute contributions), NOT global L-infinity normalization.

Pure numpy — no I/O, no database imports.
"""

from __future__ import annotations

import numpy as np


def anco_hits(
    sign_matrix: np.ndarray,
    case_seeds: np.ndarray,
    max_iterations: int = 200,
    epsilon: float = 1e-6,
) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """
    Run ANCO-HITS on a signed bipartite graph.

    Args:
        sign_matrix: (C, A) signed adjacency, values in {-1, 0, +1}
        case_seeds: (C,) initial case scores from outcomes (+1/-1/0)
        max_iterations: hard cap on iterations
        epsilon: convergence threshold on max absolute score change

    Returns:
        argument_scores: (A,) in [-1, +1]
        case_scores: (C,) in [-1, +1]
        convergence_history: list of max-delta values per iteration
    """
    C, A = sign_matrix.shape

    if C == 0 or A == 0:
        return np.zeros(A), np.zeros(C), []

    case_scores = case_seeds.astype(np.float64).copy()
    argument_scores = np.ones(A, dtype=np.float64)
    history: list[float] = []

    for iteration in range(max_iterations):
        # Authority step: update argument scores (Eq. 1, y_j update)
        # For each argument j: raw_j = Σ_i (a_ij * x_i)
        #                       denom_j = Σ_i |a_ij * x_i|
        weighted = sign_matrix * case_scores[:, np.newaxis]  # (C, A)
        raw_arg = weighted.sum(axis=0)                        # (A,)
        denom_arg = np.abs(weighted).sum(axis=0)              # (A,)

        safe_denom_arg = np.where(denom_arg > 0, denom_arg, 1.0)
        new_arg_scores = np.where(denom_arg > 0, raw_arg / safe_denom_arg, 0.0)

        # Hub step: update case scores (Eq. 1, x_i update)
        # For each case i: raw_i = Σ_j (a_ij * y_j)
        #                   denom_i = Σ_j |a_ij * y_j|
        weighted = sign_matrix * new_arg_scores[np.newaxis, :]  # (C, A)
        raw_case = weighted.sum(axis=1)                          # (C,)
        denom_case = np.abs(weighted).sum(axis=1)                # (C,)

        safe_denom_case = np.where(denom_case > 0, denom_case, 1.0)
        new_case_scores = np.where(denom_case > 0, raw_case / safe_denom_case, 0.0)

        # Check convergence
        delta = max(
            np.max(np.abs(new_arg_scores - argument_scores)),
            np.max(np.abs(new_case_scores - case_scores)),
        )
        history.append(float(delta))

        argument_scores = new_arg_scores
        case_scores = new_case_scores

        if delta < epsilon:
            break

    return argument_scores, case_scores, history
