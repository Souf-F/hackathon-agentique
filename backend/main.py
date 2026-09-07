"""API de La Taupe."""

import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from .answer import LLMUnavailable, answer_query  # noqa: E402
from .db import connect, init_db          # noqa: E402
from .pipeline import ingest_corpus       # noqa: E402
from .display import document_report, resolve_source_names  # noqa: E402
from .tools import inspect_document, search_evidence  # noqa: E402

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
    evidence = search_evidence(payload.corpus_id, payload.question, payload.k)
    try:
        answer = answer_query(payload.question, evidence)
    except LLMUnavailable as exc:
        # Une cle est configuree mais l'appel a echoue. On le dit, plutot que
        # de retomber sur l'extractif et de laisser croire que tout va bien.
        raise HTTPException(502, f"modèle indisponible : {exc}") from exc

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
    }


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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
