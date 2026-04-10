"""
Neo4j driver connection management.

Reads connection details from environment variables (loaded from .env).
Provides context manager for sessions and idempotent schema setup.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator

from dotenv import load_dotenv
from neo4j import Driver, GraphDatabase, Session

from .schema import CONSTRAINTS, INDEXES

logger = logging.getLogger(__name__)

load_dotenv()

DEFAULT_URI = "bolt://localhost:7687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "legal_graph_2026"


def get_driver(
    uri: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> Driver:
    """Create a Neo4j driver from env vars or explicit args."""
    return GraphDatabase.driver(
        uri or os.getenv("NEO4J_URI", DEFAULT_URI),
        auth=(
            user or os.getenv("NEO4J_USER", DEFAULT_USER),
            password or os.getenv("NEO4J_PASSWORD", DEFAULT_PASSWORD),
        ),
    )


@contextmanager
def neo4j_session(driver: Driver) -> Generator[Session, None, None]:
    """Yield a Neo4j session that auto-closes."""
    session = driver.session()
    try:
        yield session
    finally:
        session.close()


def ensure_constraints(session: Session) -> None:
    """Create all uniqueness constraints and indexes (idempotent)."""
    for cypher in CONSTRAINTS:
        session.run(cypher)
        logger.debug(f"Constraint: {cypher[:60]}...")

    for cypher in INDEXES:
        session.run(cypher)
        logger.debug(f"Index: {cypher[:60]}...")

    logger.info(
        f"Schema ready: {len(CONSTRAINTS)} constraints, {len(INDEXES)} indexes"
    )


def clear_graph(session: Session) -> int:
    """
    Delete all nodes and edges. Returns count of deleted nodes.

    Uses CALL IN TRANSACTIONS for large graphs.
    """
    result = session.run(
        "MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS"
    )
    summary = result.consume()
    deleted = summary.counters.nodes_deleted
    logger.warning(f"Cleared graph: {deleted} nodes deleted")
    return deleted
