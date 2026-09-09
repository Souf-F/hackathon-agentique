"""Les DEUX SEULS outils accessibles au modèle. Tous deux en lecture seule.

Aucune fonction de ce module n'écrit. Le modèle ne peut ni quarantiner, ni
déquarantiner, ni journaliser, ni modifier la politique de sécurité.
"""

import re
import unicodedata

from .db import connect, guard_database
from .models import AgentDocumentInspection, EvidenceChunk

STOPWORDS = {
    "le", "la", "les", "de", "des", "du", "un", "une", "et", "est", "que",
    "qui", "dans", "pour", "sur", "au", "aux", "en", "ce", "cette", "il",
    "elle", "a", "the", "of", "to", "is", "in", "and", "for", "on", "what",
    "quel", "quelle", "quels", "quelles", "combien", "comment", "pourquoi",
}


def _fold(text: str) -> str:
    """Minuscules sans accents : « adherents » doit matcher « adhérents »."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"\w+", _fold(text))
        if len(t) > 2 and t not in STOPWORDS
    }


def search_evidence(corpus_id: str, query: str, k: int = 5) -> list[EvidenceChunk]:
    """Passages admissibles les plus proches de la question.

    Le filtre `quarantined = 0` est appliqué en SQL. Un passage en
    quarantaine est structurellement absent du résultat, quelle que soit
    sa pertinence — et quoi qu'en dise le modèle.
    """
    with guard_database(), connect() as conn:
        # ORDER BY explicite : le contexte d'en-tete ci-dessous depend du
        # premier passage admissible de chaque document. Sans tri, cet ordre
        # serait une propriete du moteur, pas de la requete.
        rows = conn.execute(
            "SELECT c.chunk_id, c.document_id, c.text, c.page, c.position"
            " FROM chunks c"
            " WHERE c.corpus_id = ? AND c.quarantined = 0 AND c.kind = 'body'"
            " ORDER BY c.document_id, c.position",
            (corpus_id,),
        ).fetchall()

    # Contexte de document : chaque passage herite des tokens de l'en-tete de
    # son document (son premier passage admissible). Sans cela, une question
    # qui nomme a la fois une personne et une competence ne peut jamais
    # remonter le paragraphe qui ne contient que la competence : l'en-tete,
    # qui porte le nom, gagnerait toujours. L'en-tete utilise ici a deja
    # traverse l'analyse de securite — un passage quarantine n'y entre pas.
    header: dict[str, set[str]] = {}
    for row in rows:
        header.setdefault(row["document_id"], _tokens(row["text"]))

    q_tokens = _tokens(query)
    scored = []
    for row in rows:
        own = _tokens(row["text"])
        context = header.get(row["document_id"], set())
        covered = len(q_tokens & (own | context))
        if covered:
            # Depart des ex aequo par les tokens propres au passage.
            scored.append(((covered, len(q_tokens & own)), row))
    scored.sort(key=lambda p: p[0], reverse=True)

    return [
        EvidenceChunk(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            text=row["text"],
            page=row["page"],
        )
        for _, row in scored[:k]
    ]


def inspect_document(document_id: str) -> AgentDocumentInspection | None:
    """Agrégats de sécurité d'un document, pour le modèle.

    Ni texte quarantiné, ni nom de fichier : tout ce qui vient de l'auteur du
    document est exclu du plan de contrôle. La version destinée à
    l'utilisateur est `display.document_report`.
    """
    with guard_database(), connect() as conn:
        doc = conn.execute(
            "SELECT document_id, status FROM documents WHERE document_id = ?",
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

    return AgentDocumentInspection(
        document_id=doc["document_id"],
        status=doc["status"],
        chunk_count=counts["total"] or 0,
        quarantined_count=counts["flagged"] or 0,
        categories=[c["category"] for c in categories],
    )
