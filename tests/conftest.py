"""Isolation Palier 4 : le journal durable des tests n'écrit jamais dans data/.

Chaque test reçoit un ORACLE_JOURNAL_PATH temporaire et des registres
(run_control, séquences journal) réinitialisés. La base applicative n'est
pas touchée ici : les modules existants gèrent déjà leur DATABASE_URL.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_FALLBACK_JOURNAL = str(Path(tempfile.mkdtemp(prefix="oracle-journal-")) / "journal.jsonl")
os.environ.setdefault("ORACLE_JOURNAL_PATH", _FALLBACK_JOURNAL)


@pytest.fixture
def journal_file(tmp_path, monkeypatch):
    path = str(tmp_path / "oracle-journal.jsonl")
    monkeypatch.setenv("ORACLE_JOURNAL_PATH", path)
    from backend import journal as journal_mod
    from backend import run_control as runs

    journal_mod._reset_for_tests()
    runs._reset_for_tests()
    yield path
    journal_mod._reset_for_tests()
    runs._reset_for_tests()


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    from backend.db import init_db

    init_db()
    return path
