"""Kill switch Palier 4 : arrêt réel, idempotence, aucune reprise."""

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault(
    "ORACLE_JOURNAL_PATH",
    str(Path(tempfile.mkdtemp(prefix="oracle-journal-test-")) / "journal.jsonl"),
)

from backend.agent import aagent_events, arun_agent  # noqa: E402
from backend.db import init_db  # noqa: E402
from backend.errors import RunStopped  # noqa: E402
from backend import journal as journal_mod  # noqa: E402
from backend import run_control as runs  # noqa: E402
from backend.pipeline import ingest_corpus  # noqa: E402


def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/r.db")
    monkeypatch.setenv("ORACLE_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    journal_mod._reset_for_tests()
    runs._reset_for_tests()
    init_db()
    return ingest_corpus([("safe.txt", "Python FastAPI pour des API.")])["corpus_id"]


def _final(text="OK"):
    return {"content": [{"type": "text", "text": json.dumps(
        {"answer": text, "used_chunk_ids": []})}]}


def test_stop_run_actif_produit_stop_puis_stopped(tmp_path, monkeypatch):
    cid = _isolated(tmp_path, monkeypatch)
    calls = []

    async def slow(_payload):
        calls.append(1)
        await asyncio.sleep(30)
        return _final()

    async def scenario():
        task = asyncio.create_task(arun_agent("Longue ?", cid, slow, run_id="stop1"))
        await asyncio.sleep(0.3)
        record, _, _ = runs.request_stop("stop1")
        assert record.status == "stop_requested"
        try:
            await task
            stopped = None
        except RunStopped as exc:
            stopped = exc
        return stopped

    start = time.perf_counter()
    stopped = asyncio.run(scenario())
    elapsed_stop = time.perf_counter() - start
    assert stopped is not None
    assert elapsed_stop < 5, "l'annulation doit être rapide, pas 30 s"
    assert calls == [1], "un seul appel modèle, aucune relance silencieuse"
    types = [e["type"] for e in journal_mod.events_for_run("stop1")]
    assert "stop_requested" in types and "run_stopped" in types
    assert "run_completed" not in types
    assert types.index("stop_requested") < types.index("run_stopped")


def test_aucun_appel_apres_stop_requested(tmp_path, monkeypatch):
    cid = _isolated(tmp_path, monkeypatch)
    calls = []
    record = runs.create_run(cid, run_id="prestop")
    runs.request_stop("prestop")
    assert record.status == "stop_requested"

    def client(_payload):
        calls.append(1)
        return _final()

    try:
        asyncio.run(arun_agent("Q ?", cid, client, run_id="prestop"))
        assert False, "aurait dû lever RunStopped"
    except RunStopped:
        pass
    assert calls == [], "aucun appel modèle après stop_requested"
    tool_calls = [e for e in journal_mod.events_for_run("prestop") if e["type"] == "tool_call"]
    assert tool_calls == []


def test_stop_idempotent(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    runs.create_run("cid", run_id="idem")
    first, _, _ = runs.request_stop("idem")
    second, _, _ = runs.request_stop("idem")
    assert first.status == "stop_requested" and second.status == "stop_requested"
    stops = [e for e in journal_mod.events_for_run("idem") if e["type"] == "stop_requested"]
    assert len(stops) == 1, "un second stop ne rejournalise pas"


def test_stop_run_termine_ne_casse_rien(tmp_path, monkeypatch):
    cid = _isolated(tmp_path, monkeypatch)
    run = asyncio.run(arun_agent("Q ?", cid, lambda _p: _final("Fini.")))
    before = len(journal_mod.events_for_run(run.run_id))
    record, already, _ = runs.request_stop(run.run_id)
    assert already is True and record.status == "completed"
    after = journal_mod.events_for_run(run.run_id)
    assert len(after) == before
    assert "run_completed" in [e["type"] for e in after]


def test_streaming_termine_proprement_apres_stop(tmp_path, monkeypatch):
    cid = _isolated(tmp_path, monkeypatch)

    async def slow(_payload):
        await asyncio.sleep(30)
        return _final()

    async def scenario():
        agen = aagent_events("Longue ?", cid, slow, run_id="streamstop")
        events = []

        async def consume():
            try:
                async for event in agen:
                    events.append(event)
            except RunStopped:
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.3)
        runs.request_stop("streamstop")
        await asyncio.wait_for(task, timeout=5)
        return events

    events = asyncio.run(scenario())
    types = [e["type"] for e in events]
    assert "agent_start" in types
    assert "stop_requested" in types and types[-1] == "stopped"
    assert "done" not in types
