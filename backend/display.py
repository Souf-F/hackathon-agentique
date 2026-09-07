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
