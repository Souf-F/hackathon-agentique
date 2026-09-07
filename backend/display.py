"""Plan d'affichage : tout ce qui n'est destiné qu'à l'utilisateur.

Séparé de `tools.py` volontairement. `tools.py` contient ce que le modèle
peut appeler ; ce module contient ce que le modèle ne doit jamais voir —
au premier rang duquel le nom des fichiers, fourni par l'auteur du document
et donc non fiable (MENACES, canal 2).

La règle tient en une ligne : un identifiant traverse la boucle LLM, un nom
lisible est résolu après elle.
"""

from .db import connect
from .models import DocumentReport


def resolve_source_names(chunk_ids: list[str]) -> dict[str, str]:
    """chunk_id → nom de fichier, pour l'affichage des citations."""
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" * len(chunk_ids))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT c.chunk_id, d.source_name FROM chunks c"
            f" JOIN documents d ON d.document_id = c.document_id"
            f" WHERE c.chunk_id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
    return {r["chunk_id"]: r["source_name"] for r in rows}


def document_report(document_id: str) -> DocumentReport | None:
    """Version utilisateur de `inspect_document`, nom de fichier compris."""
    with connect() as conn:
        doc = conn.execute(
            "SELECT document_id, source_name, status FROM documents"
            " WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        if doc is None:
            return None

        counts = conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN quarantined = 1 THEN 1 ELSE 0 END) AS flagged"
            " FROM chunks WHERE document_id = ?",
            (document_id,),
        ).fetchone()

        categories = conn.execute(
            "SELECT DISTINCT category FROM security_events WHERE document_id = ?",
            (document_id,),
        ).fetchall()

    return DocumentReport(
        document_id=doc["document_id"],
        source_name=doc["source_name"],
        status=doc["status"],
        chunk_count=counts["total"] or 0,
        quarantined_count=counts["flagged"] or 0,
        categories=[c["category"] for c in categories],
    )


def security_summary(corpus_id: str) -> dict | None:
    """Agrégats méta d'un corpus, pour les questions « système » du frontend.

    Le frontend reçoit des questions qui ne portent pas sur le contenu des
    documents (« as-tu détecté des injections ? », « combien de passages
    sont en quarantaine ? »). Lancer le retrieval sur ces questions ne
    remonte rien : les documents ne parlent pas du système qui les analyse.
    Sans cet endpoint, le frontend tombait sur « Aucun passage admissible »
    à chaque question méta.

    Surface strictement contrôlée : compteurs et catégories uniquement.
    Aucun extrait, aucun texte de chunk, aucun nom de fichier, aucun
    identifiant utilisateur. C'est un agrégat, pas une fuite.
    """
    with connect() as conn:
        corpus = conn.execute(
            "SELECT 1 FROM corpora WHERE corpus_id = ?", (corpus_id,),
        ).fetchone()
        if corpus is None:
            return None

        doc_total = conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE corpus_id = ?",
            (corpus_id,),
        ).fetchone()["n"]
        doc_suspicious = conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE corpus_id = ? AND status = 'suspicious'",
            (corpus_id,),
        ).fetchone()["n"]

        quarantined = conn.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE corpus_id = ? AND quarantined = 1",
            (corpus_id,),
        ).fetchone()["n"]

        events_total = conn.execute(
            "SELECT COUNT(*) AS n FROM security_events WHERE corpus_id = ?",
            (corpus_id,),
        ).fetchone()["n"]

        top_categories = conn.execute(
            "SELECT category, COUNT(*) AS n FROM security_events"
            " WHERE corpus_id = ? GROUP BY category ORDER BY n DESC LIMIT 5",
            (corpus_id,),
        ).fetchall()

    return {
        "documents": doc_total,
        "documents_suspicious": doc_suspicious,
        "chunks_quarantined": quarantined,
        "events": events_total,
        "categories": [{"category": r["category"], "count": r["n"]} for r in top_categories],
    }


def document_preview(document_id: str) -> list[dict] | None:
    """Corps intégral d'un document, pour l'aperçu humain du workspace.

    Plan d'affichage uniquement : le texte renvoyé ici ne doit jamais
    remonter vers le contexte du modèle. Le frontend l'affiche dans
    `renderDocumentPreview` et c'est tout — il n'est ni concaténé à un
    prompt, ni réutilisé par un outil. Le même principe que
    `document_report` et que les `excerpt` du rapport de sécurité, qui
    exposent eux aussi du contenu auteur pour la seule lecture humaine.
    """
    with connect() as conn:
        doc = conn.execute(
            "SELECT 1 FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        if doc is None:
            return None
        rows = conn.execute(
            "SELECT chunk_id, kind, text, quarantined FROM chunks"
            " WHERE document_id = ? ORDER BY position",
            (document_id,),
        ).fetchall()
    return [
        {
            "chunk_id": r["chunk_id"],
            "kind": r["kind"],
            "text": r["text"],
            "quarantined": bool(r["quarantined"]),
        }
        for r in rows
    ]
