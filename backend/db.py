"""Couche de données SQLite : schéma et connexion.

La colonne `chunks.quarantined` est le support de la propriété de sécurité du
projet. Le filtre qui s'appuie dessus est appliqué dans
`tools.search_evidence`, pas ici, et pas dans une consigne adressée au
modèle : un passage quarantiné n'est pas « ignoré par le LLM », il n'entre
jamais dans sa requête.

Palier 4 : `init_db(create=True)` crée le schéma (démarrage, tests).
`connect(create=False)` ne recrée JAMAIS silencieusement la base pendant un
run : si le fichier attendu a disparu, il lève ResourceUnavailable
(database/DATABASE_MISSING). Idem si le schéma attendu est absent.
"""

import os
import sqlite3
from contextlib import contextmanager
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

CREATE TABLE IF NOT EXISTS tool_calls (
    call_id     TEXT PRIMARY KEY,
    corpus_id   TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    tool        TEXT NOT NULL,
    arguments   TEXT NOT NULL,
    status      TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    result      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    corpus_id   TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,
    failure_code TEXT
);

CREATE INDEX IF NOT EXISTS idx_chunks_corpus ON chunks(corpus_id, quarantined);
CREATE INDEX IF NOT EXISTS idx_events_corpus ON security_events(corpus_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_corpus ON tool_calls(corpus_id);
"""


def _db_path() -> Path:
    url = os.getenv("DATABASE_URL", "sqlite:///./data/la-taupe.db")
    raw = url.replace("sqlite:///", "", 1)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
    except sqlite3.Error:
        pass
    return conn


def connect(create: bool = False) -> sqlite3.Connection:
    """Connexion normale : ne crée jamais une base manquante.

    Lève ResourceUnavailable(resource="database") si le fichier a disparu ou
    si le schéma attendu est absent/corrompu. Aucune stack trace brute ne
    sort d'ici vers l'API : l'appelant la convertit en événement journalisé.
    """
    from .errors import ResourceUnavailable

    path = _db_path()
    if not create and not path.exists():
        raise ResourceUnavailable(
            "database", "DATABASE_MISSING",
            "La base de données est indisponible.",
        )
    try:
        conn = _open(path)
    except (sqlite3.Error, OSError) as exc:
        raise ResourceUnavailable(
            "database", "DATABASE_UNAVAILABLE",
            "La base de données est indisponible.",
        ) from exc
    if not create:
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='corpora'"
            ).fetchone()
        except sqlite3.Error as exc:
            conn.close()
            raise ResourceUnavailable(
                "database", "DATABASE_UNAVAILABLE",
                "La base de données est indisponible.",
            ) from exc
        if row is None:
            conn.close()
            raise ResourceUnavailable(
                "database", "DATABASE_SCHEMA_MISSING",
                "La base de données est indisponible.",
            )
    return conn


def init_db(create: bool = True) -> None:
    """Crée le schéma. Seul point d'entrée autorisé à créer le fichier."""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _open(path) as conn:
        conn.executescript(SCHEMA)
        for statement in (
            "ALTER TABLE tool_calls ADD COLUMN run_id TEXT",
            "ALTER TABLE queries ADD COLUMN run_id TEXT",
        ):
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.Error:
            pass


@contextmanager
def guard_database():
    """Convertit toute panne SQLite en ResourceUnavailable (jamais brute)."""
    from .errors import ResourceUnavailable

    try:
        yield
    except ResourceUnavailable:
        raise
    except (sqlite3.Error, OSError) as exc:
        raise ResourceUnavailable(
            "database", "DATABASE_UNAVAILABLE",
            "La base de données est indisponible.",
        ) from exc
