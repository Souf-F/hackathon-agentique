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
class Confidence:
    """Confiance de grounding de la réponse — jamais celle déclarée par le
    modèle sur lui-même (pas de calibration probabiliste réelle disponible).
    Calculée par le code à partir du nombre de passages/documents réellement
    cités, cf. `agent._confidence_for`. À ne jamais confondre avec le score
    du détecteur d'injection (`InjectionVerdict.confidence`), qui porte sur
    un passage, pas sur la véracité de la réponse."""

    level: Literal["high", "medium", "low", "none", "n/a"]
    reason: str


@dataclass
class Metrics:
    """Coût et volume d'un run. `estimated_cost_usd` vaut `None` si le
    modèle n'a pas de tarif connu — jamais arrondi à 0."""

    model: str
    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    duration_ms: int
    estimated_cost_usd: float | None


@dataclass
class Answer:
    text: str
    citations: list[SourceRef]
    mode: str  # "llm" | "extractive"
    status: Literal["answered", "insufficient_evidence", "refused"] = "answered"
    confidence: Confidence | None = None
    metrics: Metrics | None = None
