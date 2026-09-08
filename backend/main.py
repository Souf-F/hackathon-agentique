"""API de La Taupe (Palier 4 : runs, kill switch, journal durable)."""

import inspect
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

load_dotenv()

from . import journal as journal_mod  # noqa: E402
from . import run_control as runs  # noqa: E402
from .agent import (  # noqa: E402
    aagent_events,
    agent_events,
    arun_agent,
    run_agent,
)
from .answer import LLMUnavailable  # noqa: E402
from .db import connect, init_db  # noqa: E402
from .display import (  # noqa: E402
    document_preview, document_report, resolve_source_names, security_summary,
)
from .errors import ResourceUnavailable, RunStopped  # noqa: E402
from .metrics import unavailable as unavailable_metrics  # noqa: E402
from .pipeline import ingest_corpus  # noqa: E402
from .tools import inspect_document  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
CORPUS_DEMO = ROOT / "corpus_demo"

_original_run_agent = run_agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schéma garanti au démarrage (création autorisée UNIQUEMENT ici et à
    # l'import), puis récupération : tout run journalisé sans événement
    # terminal est marqué run_interrupted (heure de détection honnête).
    try:
        init_db(create=True)
    except Exception:
        pass
    try:
        journal_mod.mark_interrupted_runs()
    except Exception:
        pass
    yield


app = FastAPI(title="La Taupe", version="0.3.0", lifespan=lifespan)

# Le schema est cree a l'import, pas seulement au demarrage du serveur :
# un premier lancement sur une machine vierge ne doit demander aucune
# etape de migration manuelle (contrainte du chrono de cinq minutes).
init_db()


MAX_QUESTION_CHARS = 2000
MAX_CORPUS_ID_CHARS = 128
MAX_SOURCE_NAME_CHARS = 255
MAX_DOCUMENTS_PER_UPLOAD = 20
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class DocumentIn(BaseModel):
    source_name: str
    text: str

    @field_validator("source_name")
    @classmethod
    def _source_name(cls, v: object) -> str:
        name = v.strip() if isinstance(v, str) else ""
        if not name or len(name) > MAX_SOURCE_NAME_CHARS:
            raise ValueError("source_name doit contenir entre 1 et 255 caractères")
        return name

    @field_validator("text")
    @classmethod
    def _text(cls, v: object) -> str:
        text = v.strip() if isinstance(v, str) else ""
        if not text:
            raise ValueError("document vide refusé")
        if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise ValueError("document trop volumineux (max 1 MiB)")
        return text


class IngestIn(BaseModel):
    documents: list[DocumentIn]

    @field_validator("documents")
    @classmethod
    def _documents(cls, v: object) -> list[DocumentIn]:
        docs = v if isinstance(v, list) else []
        if not docs:
            return docs
        if len(docs) > MAX_DOCUMENTS_PER_UPLOAD:
            raise ValueError("trop de documents (max 20 par upload)")
        total = sum(len(d.text.encode("utf-8")) for d in docs)
        if total > MAX_UPLOAD_BYTES:
            raise ValueError("upload trop volumineux (max 5 MiB)")
        return docs


class AskIn(BaseModel):
    corpus_id: str
    question: str

    @field_validator("corpus_id")
    @classmethod
    def _corpus_id(cls, v: object) -> str:
        cid = v.strip() if isinstance(v, str) else ""
        if not cid or len(cid) > MAX_CORPUS_ID_CHARS:
            raise ValueError("corpus_id doit contenir entre 1 et 128 caractères")
        return cid

    @field_validator("question")
    @classmethod
    def _question(cls, v: object) -> str:
        question = v.strip() if isinstance(v, str) else ""
        if not question or len(question) > MAX_QUESTION_CHARS:
            raise ValueError("question doit contenir entre 1 et 2000 caractères")
        return question


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/corpus")
def create_corpus(payload: IngestIn) -> dict:
    if not payload.documents:
        raise HTTPException(400, "corpus vide")
    try:
        return ingest_corpus([(d.source_name, d.text) for d in payload.documents])
    except ResourceUnavailable as exc:
        raise HTTPException(503, exc.public_message) from exc


@app.post("/api/corpus/{corpus_id}/documents")
def add_documents(corpus_id: str, payload: IngestIn) -> dict:
    """Ajoute des documents au corpus courant, sans remplacer son contenu."""
    _require_corpus(corpus_id)
    if not payload.documents:
        raise HTTPException(400, "aucun document à ajouter")
    try:
        return ingest_corpus(
            [(d.source_name, d.text) for d in payload.documents], corpus_id=corpus_id,
        )
    except ResourceUnavailable as exc:
        raise HTTPException(503, exc.public_message) from exc


@app.delete("/api/corpus/{corpus_id}/documents/{document_id}")
def delete_document(corpus_id: str, document_id: str) -> dict:
    """Retire explicitement un document et ses données dérivées du corpus.

    Cette action utilisateur n'est pas accessible au modèle. Les chunks et
    événements associés sont retirés ensemble : le rapport ne peut pas
    conserver une alerte orpheline vers un document qui n'existe plus.
    """
    _require_corpus(corpus_id)
    try:
        with connect() as conn:
            document = conn.execute(
                "SELECT 1 FROM documents WHERE document_id = ? AND corpus_id = ?",
                (document_id, corpus_id),
            ).fetchone()
            if document is None:
                raise HTTPException(404, "document inconnu dans ce corpus")

            conn.execute(
                "DELETE FROM security_events WHERE corpus_id = ? AND document_id = ?",
                (corpus_id, document_id),
            )
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            conn.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
    except ResourceUnavailable as exc:
        raise HTTPException(503, exc.public_message) from exc

    return {"deleted_document_id": document_id}


@app.post("/api/corpus/demo")
def create_demo_corpus() -> dict:
    """Charge le corpus de démonstration livré avec le dépôt.

    C'est ce que le correcteur clique en arrivant : aucune donnée à fournir.
    """
    files = sorted(CORPUS_DEMO.glob("*.txt"))
    if not files:
        raise HTTPException(500, "corpus de démonstration introuvable")
    try:
        return ingest_corpus([(f.name, f.read_text(encoding="utf-8")) for f in files])
    except ResourceUnavailable as exc:
        raise HTTPException(503, exc.public_message) from exc


def _require_corpus(corpus_id: str) -> None:
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM corpora WHERE corpus_id = ?", (corpus_id,)
            ).fetchone()
    except ResourceUnavailable as exc:
        raise HTTPException(503, exc.public_message) from exc
    if row is None:
        raise HTTPException(404, f"corpus inconnu : {corpus_id}")


async def _execute_run(question: str, corpus_id: str, run_id: str):
    """Passe par backend.main.run_agent si les tests l'ont mocké, sinon arun_agent."""
    func = globals().get("run_agent", _original_run_agent)
    if func is _original_run_agent:
        return await arun_agent(question, corpus_id, run_id=run_id)
    try:
        result = func(question, corpus_id, run_id=run_id)
    except TypeError:
        result = func(question, corpus_id)
    if inspect.isawaitable(result):
        result = await result
    return result


@app.post("/api/ask")
async def ask(payload: AskIn) -> dict:
    _require_corpus(payload.corpus_id)
    run_id = uuid.uuid4().hex[:12]
    try:
        run = await _execute_run(payload.question, payload.corpus_id, run_id)
    except RunStopped as exc:
        raise HTTPException(409, f"run arrêté par l'opérateur : {exc.run_id or run_id}") from exc
    except ResourceUnavailable as exc:
        raise HTTPException(503, exc.public_message) from exc
    except LLMUnavailable as exc:
        raise HTTPException(502, f"modèle indisponible : {exc}") from exc
    answer = run.answer
    run_id = getattr(run, "run_id", "") or run_id

    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO queries (query_id, corpus_id, run_id, timestamp, question, answer)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex[:12],
                    payload.corpus_id,
                    run_id,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    payload.question,
                    answer.text,
                ),
            )
    except ResourceUnavailable:
        pass

    # Le nom lisible est ajouté ICI, apres la generation : il n'a jamais
    # traverse le contexte du modele.
    try:
        names = resolve_source_names([c.chunk_id for c in answer.citations])
    except ResourceUnavailable as exc:
        raise HTTPException(503, exc.public_message) from exc
    metrics = getattr(run, "metrics", None) or unavailable_metrics()
    if not isinstance(metrics, dict):
        metrics = unavailable_metrics()
    return {
        "run_id": run_id,
        "answer": answer.text,
        "mode": answer.mode,
        "status": answer.status,
        "confidence": answer.confidence,
        "metrics": metrics,
        "citations": [
            {**asdict(c), "source_name": names.get(c.chunk_id, "")}
            for c in answer.citations
        ],
        "tool_trace": run.traces,
    }


@app.post("/api/ask/stream")
async def ask_stream(payload: AskIn) -> StreamingResponse:
    _require_corpus(payload.corpus_id)

    async def stream():
        terminal_yielded = False

        def sse(event: dict) -> str:
            return f"data: {json.dumps(event)}\n\n"

        try:
            async for event in aagent_events(payload.question, payload.corpus_id):
                if event.get("type") in (
                    "done", "stopped", "resource_unavailable", "error",
                ):
                    terminal_yielded = True
                yield sse(event)
        except RunStopped:
            if not terminal_yielded:
                yield sse({"type": "stopped", "data": {}})
        except ResourceUnavailable as exc:
            if not terminal_yielded:
                yield sse({"type": "resource_unavailable", "data": {
                    "resource": exc.resource, "code": exc.code,
                    "message": exc.public_message,
                    "timestamp": journal_mod._now(),
                }})
        except LLMUnavailable:
            if not terminal_yielded:
                yield sse({"type": "error", "data": {"message": "Le modèle ne peut pas répondre."}})
        except Exception:
            if not terminal_yielded:
                yield sse({"type": "error", "data": {"message": "Le modèle ne peut pas répondre."}})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str) -> dict:
    """Kill switch opérateur : idempotent, ne tue jamais le serveur."""
    record = runs.get(run_id)
    if record is None:
        events = journal_mod.events_for_run(run_id)
        if not events:
            raise HTTPException(404, f"run inconnu : {run_id}")
        terminal = [e for e in events if e.get("type") in journal_mod.TERMINAL_EVENTS]
        if terminal:
            last = terminal[-1]
            return {"run_id": run_id, "status": last.get("status") or "stopped",
                    "timestamp": last.get("timestamp")}
        record, _, timestamp = runs.request_stop(run_id)
        return {"run_id": run_id, "status": record.status, "timestamp": timestamp}
    record, already_terminal, timestamp = runs.request_stop(run_id)
    return {"run_id": run_id, "status": record.status, "timestamp": timestamp}


@app.get("/api/runs/{run_id}/journal")
def run_journal(run_id: str) -> dict:
    """Événements d'un run, dans l'ordre seq. Lecture seule."""
    events = journal_mod.events_for_run(run_id)
    if not events:
        raise HTTPException(404, f"run inconnu : {run_id}")
    return {"run_id": run_id, "events": events}


@app.get("/api/corpus/{corpus_id}/report")
def report(corpus_id: str) -> dict:
    """Rapport de sécurité — destiné à l'utilisateur, pas à l'agent.

    C'est le seul endroit où l'extrait hostile réapparaît.
    """
    _require_corpus(corpus_id)
    try:
        with connect() as conn:
            events = conn.execute(
                "SELECT timestamp, source_name, document_id, chunk_id, category,"
                " excerpt, reason, confidence, action FROM security_events"
                " WHERE corpus_id = ? ORDER BY timestamp",
                (corpus_id,),
            ).fetchall()
            docs = conn.execute(
                "SELECT document_id FROM documents WHERE corpus_id = ?",
                (corpus_id,),
            ).fetchall()
    except ResourceUnavailable as exc:
        raise HTTPException(503, exc.public_message) from exc

    return {
        "events": [dict(e) for e in events],
        "documents": [
            asdict(rep) for d in docs
            if (rep := document_report(d["document_id"])) is not None
        ],
    }


@app.get("/api/corpus/{corpus_id}/security/summary")
def corpus_security_summary(corpus_id: str) -> dict:
    """Agrégats méta d'un corpus, pour les questions « système » du frontend.

    Le frontend reçoit des questions qui ne portent pas sur le contenu des
    documents (« as-tu détecté des injections ? »). Lancer le retrieval sur
    ces questions ne remonte rien. Sans cet endpoint, le frontend tombait
    sur « Aucun passage admissible » à chaque question méta.

    Surface strictement contrôlée : compteurs et catégories, plan
    d'affichage uniquement. Aucun extrait, aucun texte de chunk, aucun nom
    de fichier, aucun identifiant d'auteur.
    """
    _require_corpus(corpus_id)
    try:
        summary = security_summary(corpus_id)
    except ResourceUnavailable as exc:
        raise HTTPException(503, exc.public_message) from exc
    if summary is None:
        raise HTTPException(404, f"corpus inconnu : {corpus_id}")
    return summary


@app.get("/api/document/{document_id}")
def document(document_id: str) -> dict:
    """Expose exactement ce que verrait l'agent via `inspect_document`.

    Sans nom de fichier, donc : c'est le point de l'endpoint. Le rapport
    utilisateur, lui, passe par /api/corpus/{id}/report.
    """
    try:
        insp = inspect_document(document_id)
    except ResourceUnavailable as exc:
        raise HTTPException(503, exc.public_message) from exc
    if insp is None:
        raise HTTPException(404, "document inconnu")
    return asdict(insp)


@app.get("/api/document/{document_id}/preview")
def document_preview_route(document_id: str) -> dict:
    """Corps intégral d'un document, pour l'aperçu humain du workspace.

    Volontairement séparé de `/api/document/{id}` : ce dernier ne renvoie
    que des agrégats (ce que l'agent a le droit de voir), alors que cet
    endpoint expose le texte des passages — y compris ceux qui sont
    marqués en quarantaine. Le frontend l'utilise uniquement pour
    `renderDocumentPreview` ; rien de ce qu'il renvoie ne doit être
    réinjecté dans un prompt.
    """
    try:
        chunks = document_preview(document_id)
    except ResourceUnavailable as exc:
        raise HTTPException(503, exc.public_message) from exc
    if chunks is None:
        raise HTTPException(404, "document inconnu")
    return {"chunks": chunks}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
