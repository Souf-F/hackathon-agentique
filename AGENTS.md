# AGENTS.md — v1 (palier 1)

Ce fichier grandira aux paliers suivants (system prompts complets, schéma de la boucle). Pour l'instant : la liste des outils de l'agent, avec nom, signature typée et effet de bord.

## Outils

| Outil | Signature | Effet de bord |
|---|---|---|
| Ingestion | `ingest_corpus(files: list[UploadedFile]) -> CorpusId` | Oui — écrit le corpus en stockage |
| Chunking + embedding | `chunk_and_embed(corpus_id: CorpusId) -> list[ChunkId]` | Oui — écrit les embeddings dans le vector store |
| Retrieval | `retrieve(corpus_id: CorpusId, query: str, k: int) -> list[Chunk]` | Non — lecture seule |
| Détection d'injection | `detect_injection(chunk: Chunk) -> InjectionVerdict` | Non — appel de classification pur |
| Quarantaine | `quarantine(doc_id: DocId, verdict: InjectionVerdict) -> None` | Oui — écrit dans le store de quarantaine |
| Réponse | `answer_query(query: str, clean_chunks: list[Chunk]) -> Answer` | Non — appel LLM, aucune mutation externe |
| Journalisation | `log_event(event: DetectionEvent) -> None` | Oui — append dans le journal |

## Types

```
InjectionVerdict = {
  is_suspicious: bool,
  technique: str | None,      # ex: "instruction override", "role-play jailbreak", "fake system tag"
  excerpt: str | None,        # extrait exact qui a déclenché la détection
  confidence: float,          # 0.0 - 1.0
}

Answer = {
  text: str,
  citations: list[SourceRef],  # SourceRef = { doc_id: str, chunk_id: str }
}

DetectionEvent = {
  timestamp: datetime,
  doc_id: DocId,
  verdict: InjectionVerdict,
}
```

## Principe de frontière donnée/instruction

Le system prompt de `answer_query` doit expliciter que tout contenu venant des documents (`clean_chunks`) est délimité et non-exécutable — jamais interpolé comme instruction, quel que soit son contenu. À détailler avec le prompt système complet au palier 3 (la boucle).

## À venir (paliers suivants)
- System prompt complet de l'agent répondeur et du détecteur.
- Schéma de la boucle (ingestion → détection → quarantaine → retrieval → réponse → citation).
