"""
Phase 7: Evaluation framework CLI.

Usage:
  # Run all automated metrics (no human labels needed)
  python -m script.run_evaluation --db data/private_10b5_sample_416.db

  # Run only baselines
  python -m script.run_evaluation --db data/private_10b5_sample_416.db --baselines-only

  # Generate full Markdown report
  python -m script.run_evaluation --db data/private_10b5_sample_416.db --report

  # Run metrics that require human annotations
  python -m script.run_evaluation --db data/private_10b5_sample_416.db --human-metrics
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 7: Evaluation framework",
    )
    parser.add_argument(
        "--db", type=Path, default=Path("data/private_10b5_sample_416.db"),
        help="SQLite database path",
    )
    parser.add_argument(
        "--baselines-only", action="store_true",
        help="Run only the 5 baselines",
    )
    parser.add_argument(
        "--human-metrics", action="store_true",
        help="Run metrics requiring human annotations",
    )
    parser.add_argument(
        "--report", type=Path, default=None,
        nargs="?", const=Path("doc/Phase_7_Eval_Report.md"),
        help="Generate Markdown report (default: doc/Phase_7_Eval_Report.md)",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"ERROR: Database not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    if args.report is not None:
        from script.eval.report import generate_report
        report = generate_report(args.db, args.report)
        if args.report:
            print(f"Report written to {args.report}")
        else:
            print(report)
        return

    if args.baselines_only:
        from script.eval.baselines import print_baselines, run_all_baselines
        print_baselines(run_all_baselines(args.db))
        return

    # Default: run all automated metrics
    print(f"\n{'='*65}")
    print("  PHASE 7 EVALUATION — AUTOMATED METRICS")
    print(f"{'='*65}\n")

    # Baselines
    from script.eval.baselines import print_baselines, run_all_baselines
    print_baselines(run_all_baselines(args.db))

    # ANCO-HITS
    from script.eval.anco_holdout import compute_anco_evaluation, print_anco_evaluation
    print_anco_evaluation(compute_anco_evaluation(args.db))

    # Constraint rates
    from script.eval.constraint_rates import compute_constraint_rates, print_constraint_rates
    print_constraint_rates(compute_constraint_rates(args.db))

    if args.human_metrics:
        # Element accuracy
        from script.eval.element_accuracy import compute_element_accuracy, print_element_accuracy
        print_element_accuracy(compute_element_accuracy(args.db))

        # Outcome accuracy
        from script.eval.outcome_accuracy import compute_outcome_accuracy, print_outcome_accuracy
        print_outcome_accuracy(compute_outcome_accuracy(args.db))

        # IAA
        from script.eval.iaa import compute_iaa, print_iaa
        print_iaa(compute_iaa(args.db))


if __name__ == "__main__":
    main()
