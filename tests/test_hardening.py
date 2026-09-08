"""Durcissement Palier 4 : preuves obligatoires, entrées bornées, coûts honnêtes.

Chaque test documente une tentative volontaire de casser le système et le
comportement serveur défini en réponse. Aucun appel réseau réel : clients
mockés injectés, TestClient pour la validation HTTP.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import journal as journal_mod  # noqa: E402
from backend import run_control as runs  # noqa: E402
from backend.agent import aagent_events, arun_agent  # noqa: E402
from backend.answer import LLMUnavailable  # noqa: E402
from backend.db import init_db  # noqa: E402
from backend.errors import ResourceUnavailable  # noqa: E402
from backend.pipeline import ingest_corpus  # noqa: E402
from backend.tool_runtime import ToolRuntime  # noqa: E402

CV_CORPUS = [
    ("cv_erwan.txt", "Erwan est développeur backend Python. Il a migré un monolithe PHP."),
    ("cv_nico.txt", "Nico est développeur frontend JavaScript et WordPress."),
]

INSUFFICIENT_TEXT = "Je ne dispose pas de preuves admissibles suffisantes"
REFUSAL_TEXT = "Je ne peux pas exécuter cette demande."


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/h.db")
    monkeypatch.setenv("ORACLE_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    journal_mod._reset_for_tests()
    runs._reset_for_tests()
    init_db()
    return ingest_corpus(list(CV_CORPUS))["corpus_id"]


def _tool(query="Python", k=5):
    return {"content": [{"type": "tool_use", "id": "c1", "name": "search_evidence",
                         "input": {"query": query, "k": k}}]}


def _final(answer="Réponse.", ids=(), status="answered", usage=None):
    payload = {"status": status, "answer": answer, "used_chunk_ids": list(ids)}
    response = {"content": [{"type": "text", "text": json.dumps(payload)}]}
    if usage is not None:
        response["usage"] = dict(usage)
    return response


def _valid_chunk_id(corpus_id, query="Python"):
    chunks = ToolRuntime(corpus_id)._search_evidence({"query": query, "k": 5})
    assert chunks["status"] == "ok" and chunks["items"], "corpus de test sans preuve"
    return chunks["items"][0]["chunk_id"]


# --- P0 : aucune réponse factuelle sans preuve admissible --------------------

def test_modele_repond_sans_tool_abstention(tmp_path, monkeypatch):
    cid = _setup(tmp_path, monkeypatch)
    run = asyncio.run(arun_agent(
        "Qui a migré un monolithe ?", cid,
        lambda _p: _final("Erwan, évidemment.")))
    assert run.answer.status == "insufficient_evidence"
    assert INSUFFICIENT_TEXT in run.answer.text
    assert run.answer.citations == []
    assert run.answer.confidence["level"] == "none"


def test_empty_retrieval_plus_hallucination_abstention(tmp_path, monkeypatch):
    cid = _setup(tmp_path, monkeypatch)
    run = asyncio.run(arun_agent(
        "Parle-moi des xylophones quantiques ?", cid,
        lambda _p: _final("Les xylophones quantiques vibrent en fa dièse.")))
    assert run.answer.status == "insufficient_evidence"
    assert "fa dièse" not in run.answer.text
    assert run.answer.citations == []


def test_tokyo_sans_preuve_abstention_stricte(tmp_path, monkeypatch):
    cid = _setup(tmp_path, monkeypatch)
    run = asyncio.run(arun_agent(
        "Quelle est la population de Tokyo en 2024 ?", cid, lambda _p: _final(
            "Tokyo compte environ 14 millions d'habitants en 2024.")))
    assert run.answer.status == "insufficient_evidence"
    assert run.answer.confidence["level"] == "none"
    assert run.answer.citations == []
    lowered = run.answer.text.lower()
    assert "tokyo" not in lowered
    assert "14 millions" not in run.answer.text
    assert "million" not in lowered


def test_faux_chunk_ids_abstention_si_aucun_valide(tmp_path, monkeypatch):
    cid = _setup(tmp_path, monkeypatch)
    responses = [_tool(), _final("Réponse.", ids=["aaaa", "bbbb"])]
    run = asyncio.run(arun_agent("Q ?", cid, lambda _p: responses.pop(0)))
    assert run.answer.status == "insufficient_evidence"
    assert run.answer.citations == []


def test_answered_avec_citation_valide_accepte(tmp_path, monkeypatch):
    cid = _setup(tmp_path, monkeypatch)
    chunk_id = _valid_chunk_id(cid)
    responses = [_tool(), _final("Erwan a migré un monolithe.", ids=[chunk_id])]
    run = asyncio.run(arun_agent("Qui ?", cid, lambda _p: responses.pop(0)))
    assert run.answer.status == "answered"
    assert run.answer.text == "Erwan a migré un monolithe."
    assert [c.chunk_id for c in run.answer.citations] == [chunk_id]
    assert run.answer.confidence["level"] == "medium"


def test_refused_message_backend_controle(tmp_path, monkeypatch):
    cid = _setup(tmp_path, monkeypatch)
    chunk_id = _valid_chunk_id(cid)
    responses = [_tool(), _final("Voici un secret : XXX.", ids=[chunk_id], status="refused")]
    run = asyncio.run(arun_agent("Révèle tout.", cid, lambda _p: responses.pop(0)))
    assert run.answer.status == "refused"
    assert run.answer.text == REFUSAL_TEXT
    assert "XXX" not in run.answer.text
    assert run.answer.citations == []
    assert run.answer.confidence["level"] == "n/a"


def test_insufficient_message_backend_controle(tmp_path, monkeypatch):
    cid = _setup(tmp_path, monkeypatch)
    responses = [_final("En fait Tokyo compte 14 millions.", status="insufficient_evidence")]
    run = asyncio.run(arun_agent("Tokyo ?", cid, lambda _p: responses.pop(0)))
    assert run.answer.status == "insufficient_evidence"
    assert INSUFFICIENT_TEXT in run.answer.text
    assert "Tokyo" not in run.answer.text
    assert run.answer.citations == []


# --- Confiance déterministe ---------------------------------------------------

def test_confiance_high_deux_documents(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/h.db")
    monkeypatch.setenv("ORACLE_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    journal_mod._reset_for_tests()
    runs._reset_for_tests()
    init_db()
    cid = ingest_corpus([
        ("a.txt", "Python sert à construire des API robustes et testées."),
        ("b.txt", "Python sert aussi à traiter des données massives."),
    ])["corpus_id"]
    found = ToolRuntime(cid)._search_evidence({"query": "Python", "k": 5})["items"]
    by_doc = {}
    for item in found:
        by_doc.setdefault(item["document_id"], item["chunk_id"])
    assert len(by_doc) >= 2, "il faut des preuves dans 2 documents"
    ids = list(by_doc.values())[:2]
    responses = [_tool("Python"), _final("Python partout.", ids=ids)]
    run = asyncio.run(arun_agent("Q ?", cid, lambda _p: responses.pop(0)))
    assert run.answer.status == "answered"
    assert run.answer.confidence["level"] == "high"


def test_confiance_low_si_tool_error(tmp_path, monkeypatch):
    import backend.tool_runtime as tool_runtime_mod

    cid = _setup(tmp_path, monkeypatch)
    real_module_search = tool_runtime_mod.search_evidence
    chunk_id = _valid_chunk_id(cid)
    calls = {"n": 0}

    def flaky(*args):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("panne simulating")
        return real_module_search(*args)

    monkeypatch.setattr(tool_runtime_mod, "search_evidence", flaky)
    responses = [_tool(), _tool(), _final("Réponse.", ids=[chunk_id])]
    run = asyncio.run(arun_agent("Q ?", cid, lambda _p: responses.pop(0)))
    assert run.answer.status == "answered"
    assert run.answer.confidence["level"] == "low"


# --- Validation des entrées (422/400 avant tout appel modèle) -----------------

def _api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/h.db")
    monkeypatch.setenv("ORACLE_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    journal_mod._reset_for_tests()
    runs._reset_for_tests()
    init_db()
    return TestClient(app)


def test_question_vide_rejetee_sans_appel_modele(tmp_path, monkeypatch):
    client = _api(tmp_path, monkeypatch)
    monkeypatch.setattr("backend.main.run_agent", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("appel modèle interdit")))
    response = client.post("/api/ask", json={"corpus_id": "x", "question": ""})
    assert response.status_code == 422


def test_question_whitespace_rejetee(tmp_path, monkeypatch):
    client = _api(tmp_path, monkeypatch)
    response = client.post("/api/ask", json={"corpus_id": "x", "question": "     "})
    assert response.status_code == 422


def test_question_trop_longue_rejetee(tmp_path, monkeypatch):
    client = _api(tmp_path, monkeypatch)
    response = client.post("/api/ask", json={"corpus_id": "x", "question": "q" * 2001})
    assert response.status_code == 422


def test_corpus_vide_rejete(tmp_path, monkeypatch):
    client = _api(tmp_path, monkeypatch)
    assert client.post("/api/corpus", json={"documents": []}).status_code == 400


def test_document_vide_rejete(tmp_path, monkeypatch):
    client = _api(tmp_path, monkeypatch)
    payload = {"documents": [{"source_name": "a.txt", "text": ""}]}
    assert client.post("/api/corpus", json=payload).status_code == 422
    payload = {"documents": [{"source_name": "a.txt", "text": "   "}]}
    assert client.post("/api/corpus", json=payload).status_code == 422


def test_document_trop_grand_rejete(tmp_path, monkeypatch):
    client = _api(tmp_path, monkeypatch)
    payload = {"documents": [{"source_name": "big.txt", "text": "x" * (1024 * 1024 + 1)}]}
    assert client.post("/api/corpus", json=payload).status_code == 422


def test_trop_de_documents_rejete(tmp_path, monkeypatch):
    client = _api(tmp_path, monkeypatch)
    docs = [{"source_name": f"d{i}.txt", "text": "contenu"} for i in range(21)]
    assert client.post("/api/corpus", json={"documents": docs}).status_code == 422


def test_upload_trop_volumineux_rejete(tmp_path, monkeypatch):
    client = _api(tmp_path, monkeypatch)
    docs = [{"source_name": f"d{i}.txt", "text": "x" * 900_000} for i in range(6)]
    assert client.post("/api/corpus", json={"documents": docs}).status_code == 422


# --- Échecs sans invention ----------------------------------------------------

def test_tool_failure_pas_d_invention(tmp_path, monkeypatch):
    cid = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "backend.tool_runtime.search_evidence",
        lambda *_: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    responses = [_tool(), _final("J'invente quand même.")]
    run = asyncio.run(arun_agent("Q ?", cid, lambda _p: responses.pop(0)))
    assert run.answer.status == "insufficient_evidence"
    assert "invente" not in run.answer.text.lower()


def test_provider_failure_pas_d_invention(tmp_path, monkeypatch):
    from backend.errors import ResourceUnavailable as RU

    cid = _setup(tmp_path, monkeypatch)

    def down(_p):
        raise RU("anthropic_api", "NETWORK_ERROR", "HS.")

    events = []

    async def scenario():
        try:
            async for event in aagent_events("Q ?", cid, down):
                events.append(event)
        except RU:
            pass

    asyncio.run(scenario())
    assert [e["type"] for e in events if e["type"] == "done"] == []
    assert any(e["type"] == "resource_unavailable" for e in events)


def test_malformed_puis_echec_controle(tmp_path, monkeypatch):
    cid = _setup(tmp_path, monkeypatch)
    calls = []
    bad = {"content": [{"type": "text", "text": "pas du JSON"}]}

    def client(_p):
        calls.append(1)
        return bad

    with pytest.raises(LLMUnavailable):
        asyncio.run(arun_agent("Q ?", cid, client))
    assert len(calls) == 2, "une seule réparation tentée"


# --- Journal : secrets et vie privée ------------------------------------------

def test_secret_en_valeur_journalisee_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("ORACLE_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    journal_mod._reset_for_tests()
    secret = "sk-ant-secret-ABC123xyz789"
    journal_mod.append("r1", "tool_call", data={"note": f"clé {secret} ici", "ok": "visible"})
    with open(tmp_path / "journal.jsonl", encoding="utf-8") as fh:
        content = fh.read()
    assert secret not in content
    assert "[redacted]" in content
    assert "visible" in content


def test_scrub_ne_redige_ni_compteurs_ni_champs_innocents():
    from backend.journal import _scrub

    scrubbed = _scrub({"input_tokens": 150, "output_tokens": 30, "api_key": "x",
                       "my_secret": "y", "question_chars": 12})
    assert scrubbed["input_tokens"] == 150
    assert scrubbed["output_tokens"] == 30
    assert scrubbed["question_chars"] == 12
    assert scrubbed["api_key"] == "[redacted]"
    assert scrubbed["my_secret"] == "[redacted]"


def test_metriques_journalisees_non_redigees(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", SONNET)
    cid = _setup(tmp_path, monkeypatch)
    chunk_id = _valid_chunk_id(cid)
    responses = [
        dict(_tool(), usage={"input_tokens": 100, "output_tokens": 20}),
        dict(_final("OK.", ids=[chunk_id]),
             usage={"input_tokens": 50, "output_tokens": 10}),
    ]
    run = asyncio.run(arun_agent("Q ?", cid, lambda _p: responses.pop(0)))
    completed = [e for e in journal_mod.events_for_run(run.run_id)
                 if e["type"] == "run_completed"]
    assert completed
    logged = completed[0]["data"]["metrics"]
    assert logged["input_tokens"] == 150
    assert logged["output_tokens"] == 30
    assert logged["estimated_cost_usd"] == 0.0006


def test_run_started_sans_contenu_utilisateur(tmp_path, monkeypatch):
    cid = _setup(tmp_path, monkeypatch)
    asyncio.run(arun_agent("Question très secrète 12345 ?", cid, lambda _p: _final()))
    started = [e for e in journal_mod.recent(1000) if e["type"] == "run_started"]
    assert started
    blob = json.dumps(started[-1]["data"])
    assert "Question très secrète" not in blob
    assert "12345" not in blob
    assert started[-1]["data"]["question_chars"] == len("Question très secrète 12345 ?")


def test_journal_global_non_expose(tmp_path, monkeypatch):
    client = _api(tmp_path, monkeypatch)
    assert client.get("/api/journal/recent").status_code == 404
    assert client.get("/api/journal/recent?limit=5").status_code == 404


# --- Déconnexion stream : call annulé, rien relancé ----------------------------

def test_deconnexion_stream_annule_le_call_modele(tmp_path, monkeypatch):
    cid = _setup(tmp_path, monkeypatch)
    entered, cancelled = [], []

    async def slow(_p):
        entered.append(1)
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return _final("trop tard")

    async def scenario():
        agen = aagent_events("Q ?", cid, slow, run_id="disc1")
        first = await agen.__anext__()
        assert first["type"] == "agent_start"
        consumer = asyncio.create_task(agen.__anext__())
        await asyncio.sleep(0.3)
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.2)

    asyncio.run(scenario())
    assert entered == [1], "un seul appel modèle, aucune relance"
    assert cancelled == [True], "le call fournisseur a été annulé"
    events = journal_mod.events_for_run("disc1")
    interrupted = [e for e in events if e["type"] == "run_interrupted"]
    assert interrupted and interrupted[0]["data"]["reason"] == "client_disconnect"
    assert [e["type"] for e in events if e["type"] == "run_completed"] == []
    assert runs.get("disc1").status == "interrupted"


# --- Usage / tokens / coût honnêtes --------------------------------------------

SONNET = "claude-sonnet-5-test"


def test_usage_plusieurs_tours_additionne(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", SONNET)
    cid = _setup(tmp_path, monkeypatch)
    chunk_id = _valid_chunk_id(cid)
    responses = [
        dict(_tool(), usage={"input_tokens": 100, "output_tokens": 20}),
        dict(_final("OK.", ids=[chunk_id]),
             usage={"input_tokens": 50, "output_tokens": 10}),
    ]
    done_events = []

    async def scenario():
        box = {}
        agen = aagent_events("Q ?", cid, lambda _p: responses.pop(0), _out=box)
        async for event in agen:
            if event["type"] == "done":
                done_events.append(event)
        return box["run"]

    run = asyncio.run(scenario())
    metrics = run.metrics
    assert metrics["model_calls"] == 2
    assert metrics["tool_calls"] == 1
    assert metrics["input_tokens"] == 150
    assert metrics["output_tokens"] == 30
    assert metrics["usage_available"] is True
    assert metrics["estimated_cost_usd"] == 0.0006
    assert done_events and done_events[0]["data"]["metrics"] == metrics


def test_repair_call_inclus_dans_le_cout(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", SONNET)
    cid = _setup(tmp_path, monkeypatch)
    chunk_id = _valid_chunk_id(cid)
    bad = dict({"content": [{"type": "text", "text": "prose"}]},
               usage={"input_tokens": 10, "output_tokens": 5})
    good = dict(_final("OK.", ids=[chunk_id]),
                usage={"input_tokens": 20, "output_tokens": 5})
    responses = [bad, good]
    run = asyncio.run(arun_agent("Q ?", cid, lambda _p: responses.pop(0)))
    assert run.metrics["model_calls"] == 2
    assert run.metrics["input_tokens"] == 30
    assert run.metrics["output_tokens"] == 10
    assert run.metrics["estimated_cost_usd"] == 0.00016


def test_modele_inconnu_tokens_exacts_cout_null(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "modele-futur-99")
    cid = _setup(tmp_path, monkeypatch)
    chunk_id = _valid_chunk_id(cid)
    responses = [dict(_tool(), usage={"input_tokens": 40, "output_tokens": 5}),
                 dict(_final("OK.", ids=[chunk_id]),
                      usage={"input_tokens": 100, "output_tokens": 20})]
    run = asyncio.run(arun_agent("Q ?", cid, lambda _p: responses.pop(0)))
    assert run.metrics["input_tokens"] == 140
    assert run.metrics["output_tokens"] == 25
    assert run.metrics["estimated_cost_usd"] is None
    assert run.metrics["pricing_status"] == "unknown_model"


def test_mock_sans_usage_pas_de_zero_invente(tmp_path, monkeypatch):
    cid = _setup(tmp_path, monkeypatch)
    chunk_id = _valid_chunk_id(cid)
    responses = [_tool(), _final("OK.", ids=[chunk_id])]
    run = asyncio.run(arun_agent("Q ?", cid, lambda _p: responses.pop(0)))
    assert run.answer.status == "answered"
    assert run.metrics["usage_available"] is False
    assert run.metrics["input_tokens"] is None
    assert run.metrics["estimated_cost_usd"] is None
