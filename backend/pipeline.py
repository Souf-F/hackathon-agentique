"""Pipeline applicatif : ingestion → découpage → analyse → quarantaine → index.

Ces fonctions NE SONT PAS des outils de l'agent. Le LLM ne peut pas les
appeler. L'ordre est imposé par le code : l'analyse a lieu AVANT que le
passage n'entre dans l'index consultable.
"""

import os
import re
import uuid
from datetime import datetime, timezone

from .db import connect, guard_database
from .detector import analyze_chunk
from .models import InjectionVerdict, QuarantineRecord

MAX_CHUNK_CHARS = 700


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _threshold() -> float:
    return float(os.getenv("INJECTION_CONFIDENCE_THRESHOLD", "0.5"))


def split_text(text: str) -> list[str]:
    """Un chunk = un paragraphe.

    Choix delibere : le chunk est l'unite de quarantaine. Regrouper des
    paragraphes ferait tomber le contenu legitime voisin avec l'injection,
    ce qui reintroduirait exactement le probleme que la granularite au
    passage doit resoudre (cf. MENACES T06). Un paragraphe trop long est
    scinde sur les phrases.
    """
    chunks: list[str] = []
    for para in (p.strip() for p in text.split("\n\n")):
        if not para:
            continue
        if len(para) <= MAX_CHUNK_CHARS:
            chunks.append(para)
            continue
        buffer = ""
        for sentence in re.split(r"(?<=[.!?])\s+", para):
            if buffer and len(buffer) + len(sentence) + 1 > MAX_CHUNK_CHARS:
                chunks.append(buffer)
                buffer = sentence
            else:
                buffer = f"{buffer} {sentence}".strip()
        if buffer:
            chunks.append(buffer)
    # Une phrase unique plus longue que la limite est coupee durement :
    # sans cela MAX_CHUNK_CHARS n'est pas reellement garanti.
    bounded: list[str] = []
    for c in chunks:
        while len(c) > MAX_CHUNK_CHARS:
            bounded.append(c[:MAX_CHUNK_CHARS])
            c = c[MAX_CHUNK_CHARS:]
        if c:
            bounded.append(c)
    return bounded or [text.strip()]


def ingest_corpus(
    files: list[tuple[str, str]], corpus_id: str | None = None,
) -> dict:
    """files = [(source_name, text)]. Retourne un résumé d'ingestion.

    Le nom de fichier est ingéré comme un passage `kind="metadata"` et
    traverse la même analyse que le corps du texte (cf. MENACES T07).
    """
    is_new_corpus = corpus_id is None
    corpus_id = corpus_id or uuid.uuid4().hex[:12]
    threshold = _threshold()
    summary = {"corpus_id": corpus_id, "documents": 0, "chunks": 0, "quarantined": 0}

    with guard_database(), connect() as conn:
        if is_new_corpus:
            conn.execute(
                "INSERT INTO corpora (corpus_id, created_at) VALUES (?, ?)",
                (corpus_id, _now()),
            )

        for source_name, text in files:
            document_id = uuid.uuid4().hex[:12]
            conn.execute(
                "INSERT INTO documents (document_id, corpus_id, source_name, status)"
                " VALUES (?, ?, ?, 'clean')",
                (document_id, corpus_id, source_name),
            )
            summary["documents"] += 1

            pieces = [("metadata", source_name)] + [
                ("body", p) for p in split_text(text)
            ]

            doc_flagged = False
            for position, (kind, piece) in enumerate(pieces):
                chunk_id = uuid.uuid4().hex[:12]
                verdict = analyze_chunk(piece, threshold)

                conn.execute(
                    "INSERT INTO chunks (chunk_id, document_id, corpus_id, text,"
                    " kind, position, page, quarantined)"
                    " VALUES (?, ?, ?, ?, ?, ?, NULL, 0)",
                    (chunk_id, document_id, corpus_id, piece, kind, position),
                )
                summary["chunks"] += 1

                if verdict.suspicious:
                    doc_flagged = True
                    summary["quarantined"] += 1
                    quarantine_chunk(conn, chunk_id, verdict)
                    _record_event(
                        conn, corpus_id, document_id, source_name, chunk_id, verdict,
                        action="quarantined",
                    )

            if doc_flagged:
                conn.execute(
                    "UPDATE documents SET status = 'suspicious' WHERE document_id = ?",
                    (document_id,),
                )

    return summary


def quarantine_chunk(conn, chunk_id: str, verdict: InjectionVerdict) -> QuarantineRecord:
    """Retire un passage du périmètre consultable. Effet de bord : oui.

    Fonction du pipeline, jamais un outil de l'agent : le modèle ne dispose
    d'aucun moyen d'appeler ceci, ni de l'annuler.
    """
    conn.execute(
        "UPDATE chunks SET quarantined = 1 WHERE chunk_id = ?", (chunk_id,)
    )
    return QuarantineRecord(
        chunk_id=chunk_id,
        quarantined=True,
        category=verdict.category,
        reason=verdict.reason,
    )


def _record_event(conn, corpus_id, document_id, source_name, chunk_id,
                  verdict: InjectionVerdict, action: str) -> None:
    conn.execute(
        "INSERT INTO security_events (event_id, timestamp, corpus_id, document_id,"
        " chunk_id, source_name, category, excerpt, reason, confidence, action)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            uuid.uuid4().hex[:12],
            _now(),
            corpus_id,
            document_id,
            chunk_id,
            source_name,
            verdict.category or "unknown",
            verdict.excerpt or "",
            verdict.reason,
            verdict.confidence,
            action,
        ),
    )
