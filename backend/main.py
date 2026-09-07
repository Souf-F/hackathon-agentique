"""API de La Taupe."""

import uuid
import re
import unicodedata
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
from .models import Answer  # noqa: E402
from .display import (  # noqa: E402
    document_preview, document_report, resolve_source_names, security_summary,
)
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


def _normalize_question(question: str) -> str:
    normalized = unicodedata.normalize("NFD", question.lower())
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")


def _is_security_question(question: str) -> bool:
    """Détermine si une question porte sur l'état de sécurité du corpus.

    Cette décision ne lit que la question utilisateur, jamais les documents.
    Une réponse de sécurité est construite depuis des agrégats locaux et ne
    peut donc pas réintroduire un passage quarantiné dans un prompt.
    """
    text = _normalize_question(question)
    security_terms = (
        "quarantain", "injection", "detect", "isole", "suspect",
        "menace", "piege", "exfilt", "securite", "alerte",
    )
    if any(term in text for term in security_terms):
        return True

    discovery_terms = ("trouv", "detect", "repere", "identifi", "constat")
    issue_terms = ("probleme", "anomal", "risque", "souci")
    return any(term in text for term in discovery_terms) and any(
        term in text for term in issue_terms
    )


def _security_answer(summary: dict) -> Answer:
    """Formate une réponse de sécurité sans appel LLM ni donnée auteur."""
    quarantined = summary["chunks_quarantined"]
    suspicious = summary["documents_suspicious"]
    if not quarantined:
        text = (
            "Aucune tentative de manipulation n'a été détectée : "
            f"les {summary['documents']} documents sont admissibles."
        )
    else:
        categories = ", ".join(
            f"{item['category']} ({item['count']})"
            for item in summary["categories"]
        )
        text = (
            f"J'ai détecté {quarantined} passage"
            f"{'s' if quarantined > 1 else ''} en quarantaine dans "
            f"{suspicious} document{'s' if suspicious > 1 else ''} suspect"
            f"{'s' if suspicious > 1 else ''}."
        )
        if categories:
            text += f" Catégorie{'s' if ',' in categories else ''} : {categories}."

    return Answer(text=text, citations=[], mode="security_summary")


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
    if _is_security_question(payload.question):
        summary = security_summary(payload.corpus_id)
        if summary is None:
            raise HTTPException(404, f"corpus inconnu : {payload.corpus_id}")
        answer = _security_answer(summary)
    else:
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
