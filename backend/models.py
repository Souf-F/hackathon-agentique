"""Types du domaine. Miroir exact de la section 6 de SPEC.md."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

ChunkKind = Literal["body", "metadata"]
Action = Literal["quarantined", "flagged"]
DocStatus = Literal["clean", "suspicious"]


@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    corpus_id: str
    text: str
    kind: ChunkKind
    position: int
    page: int | None = None


@dataclass
class EvidenceChunk:
    """Passage transmis au modèle. PLAN DE CONTRÔLE.

    Ne contient aucune métadonnée d'origine documentaire. `source_name` est
    fourni par l'auteur du fichier : le mettre ici le ferait entrer dans le
    prompt, alors même que le chunk `kind="metadata"` correspondant a pu être
    mis en quarantaine. Le nom est résolu après génération, pour l'affichage
    (cf. display.py).
    """

    chunk_id: str
    document_id: str
    text: str
    page: int | None = None


@dataclass
class InjectionVerdict:
    suspicious: bool
    category: str | None
    excerpt: str | None
    reason: str
    confidence: float


@dataclass
class QuarantineRecord:
    chunk_id: str
    quarantined: bool
    category: str | None
    reason: str


@dataclass
class SecurityEvent:
    event_id: str
    timestamp: datetime
    document_id: str
    chunk_id: str
    source_name: str
    category: str
    excerpt: str
    reason: str
    confidence: float
    action: Action


@dataclass
class AgentDocumentInspection:
    """Retourné par `inspect_document`, outil accessible au modèle.

    PLAN DE CONTRÔLE : ni texte, ni extrait, ni `source_name`. Tout ce qui
    provient de l'auteur du document est exclu ; il ne reste que des
    agrégats produits par notre pipeline.
    """

    document_id: str
    status: DocStatus
    chunk_count: int
    quarantined_count: int
    categories: list[str] = field(default_factory=list)


@dataclass
class DocumentReport:
    """Même contenu, plus le nom du fichier. PLAN D'AFFICHAGE.

    Destiné à l'interface utilisateur uniquement. N'est jamais sérialisé
    dans un prompt.
    """

    document_id: str
    source_name: str
    status: DocStatus
    chunk_count: int
    quarantined_count: int
    categories: list[str] = field(default_factory=list)


@dataclass
class SourceRef:
    """Référence produite par la génération. Pas de nom de fichier ici :
    il est ajouté au moment de l'affichage."""

    document_id: str
    chunk_id: str


@dataclass
class Answer:
    text: str
    citations: list[SourceRef]
    mode: str  # "llm" | "extractive"
    status: str = "answered"  # "answered" | "insufficient_evidence" | "refused"
    confidence: dict = field(default_factory=lambda: {"level": "n/a", "reason": "non évalué"})


def grounding_confidence(status: str, n_refs: int, n_docs: int, had_tool_error: bool) -> dict:
    """Confiance déterministe issue du niveau de preuve, jamais du LLM.

    Ce score représente le niveau de preuve validée structurellement, pas la
    probabilité que le monde réel soit vrai. Conservateur par construction :
    aucune confiance auto-déclarée par le modèle n'est utilisée.
    """
    if status == "refused":
        return {"level": "n/a", "reason": "demande refusée, aucune affirmation produite"}
    if status == "insufficient_evidence" or n_refs <= 0:
        return {"level": "none", "reason": "aucune preuve admissible validée"}
    passage = "1 passage admissible validé" if n_refs == 1 else f"{n_refs} passages admissibles validés"
    if had_tool_error:
        return {"level": "low",
                "reason": f"{passage} mais un appel de recherche a échoué"}
    if n_docs >= 2:
        return {"level": "high",
                "reason": f"{passage} dans {n_docs} documents"}
    return {"level": "medium", "reason": f"{passage} dans 1 document"}
