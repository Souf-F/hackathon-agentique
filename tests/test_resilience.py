"""Résilience Palier 4 : ressources perdues, recovery, API journal."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault(
    "ORACLE_JOURNAL_PATH",
    str(Path(tempfile.mkdtemp(prefix="oracle-journal-test-")) / "journal.jsonl"),
)

from backend.agent import aagent_events, arun_agent  # noqa: E402
from backend.db import connect, init_db  # noqa: E402
from backend.errors import ResourceUnavailable  # noqa: E402
from backend import journal as journal_mod  # noqa: E402
from backend import run_control as runs  # noqa: E402
from backend.pipeline import ingest_corpus  # noqa: E402

SAFE_TEXT = "Erwan a migre un monolithe PHP vers des microservices Python."


def _isolated(tmp_path, monkeypatch):
    db_path = tmp_path / "s.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("ORACLE_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    journal_mod._reset_for_tests()
    runs._reset_for_tests()
    init_db()
    cid = ingest_corpus([("safe.txt", SAFE_TEXT)])["corpus_id"]
    return cid, str(db_path)


def _final(text="OK"):
    return {"content": [{"type": "text", "text": json.dumps(
        {"answer": text, "used_chunk_ids": []})}]}


def test_panne_modele_produit_resource_unavailable_et_run_failed(tmp_path, monkeypatch):
    cid, _ = _isolated(tmp_path, monkeypatch)
    calls = []

    def client(_payload):
        calls.append(1)
        raise ResourceUnavailable("anthropic_api", "NETWORK_ERROR", "HS.", retryable=True)

    events = []

    async def scenario():
        try:
            async for event in aagent_events("Q ?", cid, client):
                events.append(event)
        except ResourceUnavailable:
            pass

    asyncio.run(scenario())
    assert calls == [1], "pas de restart silencieux"
    terminal = [e for e in events if e["type"] == "resource_unavailable"]
    assert terminal and terminal[0]["data"]["resource"] == "anthropic_api"
    assert terminal[0]["data"]["code"] == "NETWORK_ERROR"
    journal_types = [e["type"] for e in journal_mod.recent(1000)]
    assert "resource_unavailable" in journal_types
    assert "run_failed" in journal_types
    assert "run_completed" not in journal_types


def test_timeout_reseau_mappe_timeout(tmp_path, monkeypatch):
    cid, _ = _isolated(tmp_path, monkeypatch)

    def client(_payload):
        raise httpx.TimeoutException("trop lent")

    with pytest.raises(ResourceUnavailable) as info:
        asyncio.run(arun_agent("Q ?", cid, client))
    assert info.value.code == "TIMEOUT"
    assert info.value.resource == "anthropic_api"


def test_connexion_refusee_mappee_network_error(tmp_path, monkeypatch):
    cid, _ = _isolated(tmp_path, monkeypatch)

    def client(_payload):
        raise httpx.ConnectError("refusé")

    with pytest.raises(ResourceUnavailable) as info:
        asyncio.run(arun_agent("Q ?", cid, client))
    assert info.value.code == "NETWORK_ERROR"


def test_cle_absente_trace_explicite(tmp_path, monkeypatch):
    cid, _ = _isolated(tmp_path, monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ResourceUnavailable) as info:
        asyncio.run(arun_agent("Q ?", cid, None))
    assert info.value.resource == "anthropic_api_key"
    assert info.value.code == "MISSING_CREDENTIAL"
    journal_types = [e["type"] for e in journal_mod.recent(1000)]
    assert "resource_unavailable" in journal_types and "run_failed" in journal_types


def test_exception_tool_journalisee_sans_stack(tmp_path, monkeypatch):
    cid, _ = _isolated(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "backend.tool_runtime.search_evidence",
        lambda *_: (_ for _ in ()).throw(RuntimeError("stack interne tres secrete")),
    )
    tool = {"content": [{"type": "tool_use", "id": "c1", "name": "search_evidence",
                         "input": {"query": "Python"}}]}
    responses = [tool, _final("Degrade.")]
    run = asyncio.run(arun_agent("Q ?", cid, lambda _p: responses.pop(0)))
    assert run.answer.text == "Degrade."
    results = [e for e in journal_mod.events_for_run(run.run_id) if e["type"] == "tool_result"]
    assert results and results[0]["data"]["trace"]["status"] == "error"
    with open(os.environ["ORACLE_JOURNAL_PATH"], encoding="utf-8") as fh:
        assert "stack interne tres secrete" not in fh.read()


def test_db_supprimee_pendant_run(tmp_path, monkeypatch):
    cid, db_file = _isolated(tmp_path, monkeypatch)
    os.remove(db_file)
    assert not Path(db_file).exists()
    with pytest.raises(ResourceUnavailable) as info:
        asyncio.run(arun_agent("Q ?", cid, lambda _p: _final()))
    assert info.value.resource == "database"
    assert not Path(db_file).exists(), "pas de recreation silencieuse"
    journal_types = [e["type"] for e in journal_mod.recent(1000)]
    assert "resource_unavailable" in journal_types and "run_failed" in journal_types
    with pytest.raises(ResourceUnavailable):
        connect()


def test_run_incomplet_puis_restart_marche_interrupted(tmp_path, monkeypatch):
    monkeypatch.setenv("ORACLE_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    journal_mod._reset_for_tests()
    journal_mod.append("run-orphelin", "run_started", status="running", data={"corpus_id": "c"})
    journal_mod.append("run-orphelin", "model_request_started", status="running", data={})
    marked = journal_mod.mark_interrupted_runs()
    assert len(marked) == 1
    event = marked[0]
    assert event["type"] == "run_interrupted"
    assert event["data"]["reason"] == "process_restart_detected"
    assert event["data"]["last_event_timestamp"]
    assert event["data"]["restart_detection_timestamp"]
    assert journal_mod.mark_interrupted_runs() == [], "idempotent après marquage"


def test_api_journal_ordonne_et_recent_borne(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app

    cid, _ = _isolated(tmp_path, monkeypatch)
    run = asyncio.run(arun_agent("Q ?", cid, lambda _p: _final("Fini.")))
    client = TestClient(app)
    response = client.get(f"/api/runs/{run.run_id}/journal")
    assert response.status_code == 200
    events = response.json()["events"]
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    assert events[0]["type"] == "run_started"
    assert events[-1]["type"] == "run_completed"
    assert client.get("/api/runs/inconnu/journal").status_code == 404
    response = client.get("/api/journal/recent", params={"limit": 100000})
    assert response.status_code == 200
    assert response.json()["count"] <= 500


def test_api_stop_idempotent_et_404(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app

    cid, _ = _isolated(tmp_path, monkeypatch)
    run = asyncio.run(arun_agent("Q ?", cid, lambda _p: _final("Fini.")))
    client = TestClient(app)
    first = client.post(f"/api/runs/{run.run_id}/stop").json()
    second = client.post(f"/api/runs/{run.run_id}/stop").json()
    assert first["status"] == "completed" and second["status"] == "completed"
    assert client.post("/api/runs/inconnu/stop").status_code == 404
