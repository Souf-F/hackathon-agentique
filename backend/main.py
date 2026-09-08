"""API de La Taupe."""

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from .answer import LLMUnavailable  # noqa: E402
from .agent import agent_events, run_agent  # noqa: E402
from .db import connect, init_db          # noqa: E402
from .pipeline import ingest_corpus       # noqa: E402
from .display import (  # noqa: E402
    document_preview, document_report, resolve_source_names, security_summary,
)
from .tools import inspect_document  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
CORPUS_DEMO = ROOT / "corpus_demo"

app = FastAPI(title="La Taupe", version="0.2.0")

# Le schema est cree a l'import, pas seulement au demarrage du serveur :
# un premier lancement sur une machine vierge ne doit demander aucune
# etape de migration manuelle (contrainte du chrono de cinq minutes).
init_db()


class DocumentIn(BaseModel):
    source_name: str
    text: str


class IngestIn(BaseModel):
    documents: list[DocumentIn]


class AskIn(BaseModel):
    corpus_id: str
    question: str
    k: int = 5


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/corpus")
def create_corpus(payload: IngestIn) -> dict:
    if not payload.documents:
        raise HTTPException(400, "corpus vide")
    return ingest_corpus([(d.source_name, d.text) for d in payload.documents])


@app.post("/api/corpus/{corpus_id}/documents")
def add_documents(corpus_id: str, payload: IngestIn) -> dict:
    """Ajoute des documents au corpus courant, sans remplacer son contenu."""
    _require_corpus(corpus_id)
    if not payload.documents:
        raise HTTPException(400, "aucun document à ajouter")
    return ingest_corpus(
        [(d.source_name, d.text) for d in payload.documents], corpus_id=corpus_id,
    )


@app.delete("/api/corpus/{corpus_id}/documents/{document_id}")
def delete_document(corpus_id: str, document_id: str) -> dict:
    """Retire explicitement un document et ses données dérivées du corpus.

    Cette action utilisateur n'est pas accessible au modèle. Les chunks et
    événements associés sont retirés ensemble : le rapport ne peut pas
    conserver une alerte orpheline vers un document qui n'existe plus.
    """
    _require_corpus(corpus_id)
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

    return {"deleted_document_id": document_id}


@app.post("/api/corpus/demo")
def create_demo_corpus() -> dict:
    """Charge le corpus de démonstration livré avec le dépôt.

    C'est ce que le correcteur clique en arrivant : aucune donnée à fournir.
    """
    files = sorted(CORPUS_DEMO.glob("*.txt"))
    if not files:
        raise HTTPException(500, "corpus de démonstration introuvable")
    return ingest_corpus([(f.name, f.read_text(encoding="utf-8")) for f in files])


def _require_corpus(corpus_id: str) -> None:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM corpora WHERE corpus_id = ?", (corpus_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, f"corpus inconnu : {corpus_id}")


@app.post("/api/ask")
def ask(payload: AskIn) -> dict:
    _require_corpus(payload.corpus_id)
    try:
        run = run_agent(payload.question, payload.corpus_id)
    except LLMUnavailable as exc:
        raise HTTPException(502, f"modèle indisponible : {exc}") from exc
    answer = run.answer

    with connect() as conn:
        conn.execute(
            "INSERT INTO queries (query_id, corpus_id, timestamp, question, answer)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex[:12],
                payload.corpus_id,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                payload.question,
                answer.text,
            ),
        )

    # Le nom lisible est ajouté ICI, apres la generation : il n'a jamais
    # traverse le contexte du modele.
    names = resolve_source_names([c.chunk_id for c in answer.citations])
    return {
        "answer": answer.text,
        "mode": answer.mode,
        "citations": [
            {**asdict(c), "source_name": names.get(c.chunk_id, "")}
            for c in answer.citations
        ],
        "tool_trace": run.traces,
    }


@app.post("/api/ask/stream")
def ask_stream(payload: AskIn) -> StreamingResponse:
    _require_corpus(payload.corpus_id)

    def stream():
        try:
            for event in agent_events(payload.question, payload.corpus_id):
                yield f"data: {json.dumps(event)}\n\n"
        except LLMUnavailable as exc:
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': 'Le modèle ne peut pas répondre.'}})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/corpus/{corpus_id}/report")
def report(corpus_id: str) -> dict:
    """Rapport de sécurité — destiné à l'utilisateur, pas à l'agent.

    C'est le seul endroit où l'extrait hostile réapparaît.
    """
    _require_corpus(corpus_id)
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
    summary = security_summary(corpus_id)
    if summary is None:
        raise HTTPException(404, f"corpus inconnu : {corpus_id}")
    return summary


@app.get("/api/document/{document_id}")
def document(document_id: str) -> dict:
    """Expose exactement ce que verrait l'agent via `inspect_document`.

    Sans nom de fichier, donc : c'est le point de l'endpoint. Le rapport
    utilisateur, lui, passe par /api/corpus/{id}/report.
    """
    insp = inspect_document(document_id)
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
    chunks = document_preview(document_id)
    if chunks is None:
        raise HTTPException(404, "document inconnu")
    return {"chunks": chunks}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
