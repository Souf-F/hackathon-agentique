"""Couche de données SQLite : schéma et connexion.

La colonne `chunks.quarantined` est le support de la propriété de sécurité du
projet. Le filtre qui s'appuie dessus est appliqué dans
`tools.search_evidence`, pas ici, et pas dans une consigne adressée au
modèle : un passage quarantiné n'est pas « ignoré par le LLM », il n'entre
jamais dans sa requête.
"""

import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS corpora (
    corpus_id   TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    corpus_id   TEXT NOT NULL,
    source_name TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'clean',
    FOREIGN KEY (corpus_id) REFERENCES corpora(corpus_id)
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    corpus_id   TEXT NOT NULL,
    text        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'body',
    position    INTEGER NOT NULL,
    page        INTEGER,
    quarantined INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (document_id) REFERENCES documents(document_id)
);

CREATE TABLE IF NOT EXISTS security_events (
    event_id    TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    corpus_id   TEXT NOT NULL,
    document_id TEXT NOT NULL,
    chunk_id    TEXT NOT NULL,
    source_name TEXT NOT NULL,
    category    TEXT NOT NULL,
    excerpt     TEXT NOT NULL,
    reason      TEXT NOT NULL,
    confidence  REAL NOT NULL,
    action      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS queries (
    query_id    TEXT PRIMARY KEY,
    corpus_id   TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_corpus ON chunks(corpus_id, quarantined);
CREATE INDEX IF NOT EXISTS idx_events_corpus ON security_events(corpus_id);
"""


def _db_path() -> Path:
    url = os.getenv("DATABASE_URL", "sqlite:///./data/la-taupe.db")
    raw = url.replace("sqlite:///", "", 1)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
