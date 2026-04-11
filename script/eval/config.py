"""
Evaluation configuration: test cases, exclusions, seeds, thresholds.

All evaluation parameters in one place. No test-set tuning —
these are fixed before any evaluation runs.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Random seeds (reproducibility)
# ---------------------------------------------------------------------------

RANDOM_SEED = 42
BOOTSTRAP_SEED = 2026
ANNOTATION_ORDER_SEED = 7  # Randomize case presentation for Emre

# ---------------------------------------------------------------------------
# Bootstrap parameters
# ---------------------------------------------------------------------------

BOOTSTRAP_N_RESAMPLES = 10_000
BOOTSTRAP_CI_LEVEL = 0.95

# ---------------------------------------------------------------------------
# Development cases (EXCLUDED from all evaluation metrics)
# These were used to debug and develop the pipeline during Phase 5.
# ---------------------------------------------------------------------------

DEV_EXCLUDED_DOCKETS: set[int] = {
    6135547,  # Ketan Patel v. Portfolio Diversification — Phase 5 LLM test
    87229,    # Ashland v. Oppenheimer — Phase 5 LLM test
}

# ---------------------------------------------------------------------------
# Test split: 14 cases from label_and_split.py
# ---------------------------------------------------------------------------

TEST_SPLIT_DOCKETS: list[int] = [
    19225,      # Smith v. Ayres — DEF_WINS, ca5
    28160,      # Golding v. Barr — DEF_WINS, ca2  (actually PLT_WINS per ANCO)
    37974,      # Theoharous v. Fong — DEF_WINS, ca11
    87229,      # Ashland v. Oppenheimer — DEF_WINS, ca6 (DEV EXCLUDED)
    4426187,    # In re Vivendi — DEF_WINS, ca2
    6328090,    # Webb v. Solarcity — DEF_WINS, ca9
    16566489,   # Gamm v. Sanderson Farms — DEF_WINS, ca2
    17356912,   # Carpenters v. Allstate — DEF_WINS, ca7
    66821987,   # In re Overstock — DEF_WINS, ca10
    70353466,   # Lee v. McDowell — DEF_WINS, ncbizct
    54674,      # Streber v. Hunter — MIXED, ca5
    48095,      # Barrie v. Intervoice-Brite — PLT_WINS, ca5
    6076389,    # JP Morgan v. Geveran — PLT_WINS, fladistctapp
    15582770,   # Masel v. Villarreal — PLT_WINS, ca5
]

# ---------------------------------------------------------------------------
# Hard negatives: 10 additional cases for annotation
# Selection criteria documented in data/annotation_cases.json
# ---------------------------------------------------------------------------

HARD_NEGATIVE_DOCKETS: list[int] = [
    # 3 MIXED outcomes (rarest, hardest class)
    1903179,    # WPP v. Spot Runner — MIXED, ca9, conf=0.85
    67983529,   # Alavi v. Genius Brands — MIXED, ca9, conf=0.85
    66798773,   # Amalgamated v. Facebook — MIXED, ca9, conf=0.68
    # 2 underrepresented circuits (ca1, ca8 — not in test split)
    1870471,    # FirstBank Puerto Rico — DEF_WINS, ca1, conf=0.85
    160053,     # Minneapolis Firefighters v. MEMC — DEF_WINS, ca8, conf=0.85
    # 2 low-confidence labels (ambiguous regex labeling)
    8175,       # Cory v. Stewart — MIXED, ca5, conf=0.50
    98386,      # Trust Company v. NNP — DEF_WINS, ca5, conf=0.50
    # 3 CONTESTED elements (pipeline expected to struggle)
    6136502,    # Lighting Science v. Geveran — 4 contested elements
    6136505,    # JP Morgan v. Geveran — 4 contested elements
    17357763,   # Carpenters v. Allstate — 3 contested elements
]

# ---------------------------------------------------------------------------
# Full annotation set (24 cases, minus dev exclusions for metrics)
# ---------------------------------------------------------------------------

ANNOTATION_DOCKETS: list[int] = TEST_SPLIT_DOCKETS + HARD_NEGATIVE_DOCKETS

# For evaluation metrics: exclude dev cases
EVAL_DOCKETS: list[int] = [
    d for d in ANNOTATION_DOCKETS if d not in DEV_EXCLUDED_DOCKETS
]

# ---------------------------------------------------------------------------
# Baseline thresholds
# ---------------------------------------------------------------------------

# B3: ANCO-HITS threshold for outcome prediction
ANCO_THRESHOLD_PLT = 0.3   # score > 0.3 → PLAINTIFF_WINS
ANCO_THRESHOLD_DEF = -0.3  # score < -0.3 → DEFENDANT_WINS
# Between thresholds → MIXED

# ---------------------------------------------------------------------------
# Metric thresholds (quality gates)
# ---------------------------------------------------------------------------

MIN_KAPPA = 0.70            # Inter/intra-annotator agreement gate
MIN_BOOTSTRAP_SAMPLES = 24  # Minimum test cases for reporting
