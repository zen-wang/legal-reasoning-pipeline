"""
Neo4j schema constants: node labels, edge types, constraints, and indexes.

All Cypher DDL uses IF NOT EXISTS for idempotent re-runs.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Node labels
# ---------------------------------------------------------------------------
CASE = "Case"
OPINION = "Opinion"
STATUTE = "Statute"
LEGAL_ARGUMENT = "LegalArgument"
JUDGE = "Judge"
COMPANY = "Company"
LAW_FIRM = "LawFirm"

# ---------------------------------------------------------------------------
# Edge (relationship) types
# ---------------------------------------------------------------------------
HAS_OPINION = "HAS_OPINION"
CITES = "CITES"
CHARGED_UNDER = "CHARGED_UNDER"
INVOLVES = "INVOLVES"
DECIDED_BY = "DECIDED_BY"
DEFENDANT_IS = "DEFENDANT_IS"
REPRESENTED_BY = "REPRESENTED_BY"

# ---------------------------------------------------------------------------
# Uniqueness constraints (created first for MERGE idempotency)
# ---------------------------------------------------------------------------
CONSTRAINTS: list[str] = [
    f"CREATE CONSTRAINT case_docket_id IF NOT EXISTS FOR (c:{CASE}) REQUIRE c.docket_id IS UNIQUE",
    f"CREATE CONSTRAINT opinion_id IF NOT EXISTS FOR (o:{OPINION}) REQUIRE o.opinion_id IS UNIQUE",
    f"CREATE CONSTRAINT statute_citation IF NOT EXISTS FOR (s:{STATUTE}) REQUIRE s.citation IS UNIQUE",
    f"CREATE CONSTRAINT argument_hash IF NOT EXISTS FOR (a:{LEGAL_ARGUMENT}) REQUIRE a.text_hash IS UNIQUE",
    f"CREATE CONSTRAINT judge_name IF NOT EXISTS FOR (j:{JUDGE}) REQUIRE j.name_normalized IS UNIQUE",
    f"CREATE CONSTRAINT company_name IF NOT EXISTS FOR (co:{COMPANY}) REQUIRE co.name_normalized IS UNIQUE",
    f"CREATE CONSTRAINT firm_name IF NOT EXISTS FOR (f:{LAW_FIRM}) REQUIRE f.name_normalized IS UNIQUE",
]

# ---------------------------------------------------------------------------
# Indexes (for downstream query performance)
# ---------------------------------------------------------------------------
INDEXES: list[str] = [
    f"CREATE INDEX case_court IF NOT EXISTS FOR (c:{CASE}) ON (c.court_id)",
    f"CREATE INDEX case_outcome IF NOT EXISTS FOR (c:{CASE}) ON (c.outcome_label)",
    f"CREATE INDEX case_date IF NOT EXISTS FOR (c:{CASE}) ON (c.date_filed)",
    f"CREATE INDEX case_has_irac IF NOT EXISTS FOR (c:{CASE}) ON (c.has_irac)",
    f"CREATE INDEX opinion_docket IF NOT EXISTS FOR (o:{OPINION}) ON (o.docket_id)",
    f"CREATE INDEX opinion_internal IF NOT EXISTS FOR (o:{OPINION}) ON (o.internal)",
    f"CREATE INDEX argument_side IF NOT EXISTS FOR (a:{LEGAL_ARGUMENT}) ON (a.side)",
]
