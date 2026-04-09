# Legal Reasoning Pipeline

ASU CIPS Lab -- Neuro-symbolic legal reasoning system for securities fraud (Rule 10b-5) with symbolic pattern lifting, ANCO-HITS argument scaling, and explainable IRAC output.

Originally forked from [SaurabhDusane/sec-litigation-scraper](https://github.com/SaurabhDusane/sec-litigation-scraper).

## Overview

A neuro-symbolic "thinking RAG" system that reasons about legal cases using interpretable symbolic patterns. The system shows **WHY** it reaches a conclusion through traceable, editable rules -- not black-box prediction.

- **Domain**: Private 10b-5 securities fraud litigation (investor sues company)
- **Data source**: CourtListener API v4 (48 fields, 6 endpoints)
- **Core rule**: Rule 10b-5 -- 6 elements must ALL be satisfied for plaintiff to win
- **Architecture**: Based on lab papers -- Beyond the Black Box (lifting/lowering), NARRA-SCALE (ANCO-HITS), Hybrids (human oversight)

## The Rule-Based Pattern

```
PlaintiffWins(case) <- MaterialMisrep(case) ^ Scienter(case) ^ Connection(case)
                     ^ Reliance(case) ^ EconomicLoss(case) ^ LossCausation(case)

Sub-rules:
  Scienter(case)       <- MotiveAndOpportunity | ConsciousMisbehavior | RecklessDisregard
  LossCausation(case)  <- CorrectiveDisclosurePriceDrop | MaterializationOfConcealedRisk
  Reliance(case)       <- FraudOnTheMarket | DirectReliance | AffiliateOmission
  MaterialMisrep(case) <- FalseStatements | MisleadingOmissions | SchemeToDefraud
```

## Pipeline

| Phase | Name | What it produces |
|-------|------|-----------------|
| 0 | **Data Preparation** | ~10,000 Private 10b-5 cases scraped from CourtListener |
| 1 | **Symbolic Lifting** | Structured element-level assessments per case (IRAC format) |
| 2 | **Knowledge Graph** | Citation network + signed argument-case edges (KuzuDB) |
| 3 | **ANCO-HITS Scaling** | Argument strength scores on [-1, +1] scale |
| 4 | **GraphSAGE** | Structural predictions from citation neighborhoods |
| 5 | **Constrained RAG** | Retrieval with hard rules -- zero hallucination |
| 6 | **IRAC Output** | Explainable legal analysis (Issue, Rule, Application, Conclusion) |
| 7 | **Evaluation** | Citation accuracy, element extraction, outcome prediction |

## Dataset

Private 10b-5 cases scraped from CourtListener API:

| Metric | Count |
|--------|------:|
| Total dockets | ~10,200 |
| Opinions with full text | ~1,500-1,700 |
| Avg opinion text length | 47,000 chars |
| Citation edges | ~50,000+ |
| Outcome variety | ~50/50 (MTD granted vs denied) |

Download the dataset: see `data/DATASET_DOWNLOAD.txt`

## Technology Stack

| Tool | Purpose |
|------|---------|
| CourtListener API v4 | Data source (48 fields, 6 endpoints) |
| SQLite | Raw scraped data with checkpoint/resume |
| DuckDB | Structured IRAC objects (embedded, no server) |
| KuzuDB | Knowledge graph (embedded, Cypher + vector search) |
| Llama 3.3 70B Instruct | Structured lifting + IRAC generation (on Gaudi 2) |
| PyTorch Geometric | GraphSAGE (on Sol A100) |
| LlamaIndex | RAG orchestration |
| Pydantic v2 | Schema enforcement on all LLM outputs |

## Setup

```bash
# Clone
git clone https://github.com/zen-wang/legal-reasoning-pipeline.git
cd legal-reasoning-pipeline

# Environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# API token
cp .env.example .env
# Edit .env with your CourtListener token

# Download dataset
# See data/DATASET_DOWNLOAD.txt for Google Drive link

# Run scraper (or use pre-scraped dataset)
python script/scraper_private_10b5.py --tier golden    # 200 cases for testing
python script/scraper_private_10b5.py --tier opinions  # ~3,400 opinion cases
python script/scraper_private_10b5.py --tier all       # all ~10,200 cases
```

## Project Structure

```
legal-reasoning-pipeline/
├── Project-Background/         # Reference papers and project proposals
├── data/
│   ├── DATASET_DOWNLOAD.txt    # Google Drive link for dataset
│   └── sources-researching/    # Data source exploration scripts
├── doc/
│   ├── Pipeline_Plan_Private_10b5.md
│   ├── CourtListener_API_Manual.md
│   └── SEC_EDGAR_vs_CourtListener_vs_IA_RECAP_data_field.md
├── script/
│   └── scraper_private_10b5.py # CourtListener scraper
├── .env.example                # API token template
├── README.md
└── requirements.txt
```

## Research Foundation

| Component | Paper |
|-----------|-------|
| ANCO-HITS scaling | NARRA-SCALE, ICTAI 2025 -- 91% accuracy on political scaling |
| Lifting & lowering | Beyond the Black Box, IEEE TCSS 2026 -- 90% human agreement on pattern matching |
| Human oversight | Hybrids, 2025 -- practical wisdom as structural requirement |

## Full Pipeline Plan

See [doc/Pipeline_Plan_Private_10b5.md](doc/Pipeline_Plan_Private_10b5.md) for the complete implementation plan with phases, tools, compute strategy, and evaluation metrics.
