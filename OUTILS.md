# OUTILS — La Taupe

Liste des outils et fonctions de l'agent, chacun avec nom, signature typée et effet de bord. Référencé depuis AGENTS.md.

---

## 1. Outils exposés au modèle

Deux outils, tous deux en lecture seule.

### `search_evidence`

```python
search_evidence(
    corpus_id: str,
    query: str,
    k: int = 5,
) -> list[EvidenceChunk]
```

Effet de bord : **non**.

Retourne les passages admissibles les plus proches de la question. Le filtre de quarantaine est appliqué dans la requête de données, pas confié au modèle :

```sql
SELECT ... FROM chunks WHERE corpus_id = ? AND quarantined = 0
```

### `inspect_document`

```python
inspect_document(
    document_id: str,
) -> DocumentInspection
```

Effet de bord : **non**.

Retourne uniquement des agrégats : `status`, `chunk_count`, `quarantined_count`, `categories`. Ne retourne jamais le texte ni l'extrait d'un passage en quarantaine — sinon l'injection reviendrait dans le contexte du modèle sous couvert de rapport de sécurité.

---

## 2. Fonctions non accessibles au modèle

Appelées par l'application, dans un ordre imposé par le code.

| Fonction | Signature | Effet de bord |
|---|---|---|
| Ingestion | `ingest_corpus(files: list[UploadedFile]) -> Corpus` | Oui — persiste documents et métadonnées |
| Découpage | `chunk_document(doc: Document) -> list[Chunk]` | Oui — persiste les passages |
| Analyse | `analyze_chunk(chunk: Chunk) -> InjectionVerdict` | Non — classification pure |
| Quarantaine | `quarantine_chunk(chunk: Chunk, verdict: InjectionVerdict) -> QuarantineRecord` | Oui — marque le passage comme exclu |
| Journalisation | `record_security_event(chunk: Chunk, verdict: InjectionVerdict) -> SecurityEvent` | Oui — append dans le journal |
| Indexation | `index_chunks(chunks: list[Chunk]) -> int` | Oui — écrit l'index de recherche |

Types (`InjectionVerdict`, `Chunk`, `SecurityEvent`, etc.) détaillés dans SPEC.md section 6.

**Pourquoi cette séparation** : si le modèle pouvait appeler `quarantine_chunk`, la frontière de sécurité se trouverait à l'intérieur du composant probabiliste. Un outil d'agent est une action dont le LLM décide ; une fonction de pipeline est une action que l'architecture impose. La quarantaine appartient à la seconde catégorie.
