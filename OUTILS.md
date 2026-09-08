# OUTILS — La Taupe

Liste des outils et fonctions de l'agent, chacun avec nom, signature typée et effet de bord. Référencé depuis AGENTS.md.

État : palier 3, à jour avec le code réel (`backend/tool_runtime.py`, `backend/agent.py`).

---

## 1. Outils exposés au modèle

**Un seul outil**, en lecture seule, appelé par le modèle lui-même via le tool-calling Anthropic (`tool_choice: auto` — c'est Claude qui décide de l'appeler, pas le code).

### `search_evidence`

```python
search_evidence(
    corpus_id: str,   # injecté par le runtime, jamais fourni par le modèle
    query: str,       # 1 à 2000 caractères
    k: int = 5,       # borné entre 1 et 12
) -> list[EvidenceChunk]
```

Effet de bord : **non**.

Exposé au modèle via `ToolRuntime.definitions()` (schéma JSON avec bornes explicites sur `query` et `k`) et invoqué via `ToolRuntime.execute("search_evidence", arguments)`. Le `corpus_id` n'est jamais un argument que le modèle choisit — il est lié au runtime à la création (`ToolRuntime(corpus_id)`), donc un appel ne peut pas sortir du corpus de la conversation en cours.

Le filtre de quarantaine reste appliqué en SQL, jamais confié au modèle :

```sql
SELECT ... FROM chunks WHERE corpus_id = ? AND quarantined = 0
```

**Validation stricte avant exécution** — trois erreurs typées possibles, retournées au modèle comme résultat d'outil (pas une exception qui casse la boucle) :

| Code | Déclencheur |
|---|---|
| `INVALID_ARGUMENTS` | `query` vide, trop long (> 2000), ou `k` hors bornes [1, 12] |
| `UNKNOWN_TOOL` | Le modèle demande un outil qui n'existe pas dans le registre |
| `TOOL_EXECUTION_ERROR` | Exception inattendue pendant l'exécution |

### `inspect_document` — existe, mais pas exposé à l'agent

```python
inspect_document(document_id: str) -> AgentDocumentInspection | None
```

Cette fonction existe dans `backend/tools.py` mais **n'est pas enregistrée** dans `ToolRuntime._registry` — le modèle ne peut pas l'appeler. Utilisée uniquement côté serveur, par l'endpoint `/api/document/{id}`, en dehors de la boucle agentique.

---

## 2. La boucle d'appel d'outils (`backend/agent.py`)

```python
agent_events(question: str, corpus_id: str) -> Generator[dict, None, AgentRun]
run_agent(question: str, corpus_id: str) -> AgentRun
```

Effet de bord : indirect (déclenche `search_evidence` et l'appel Anthropic, journalise chaque appel en base).

Génère les événements `agent_start`, `tool_call`, `tool_result`, `text_delta`, `done` (consommés par `/api/ask` en mode bloquant et `/api/ask/stream` en SSE — même générateur, deux façons de le lire). Bornée à `MAX_TOOL_ROUNDS = 4` tours d'outils ; au-delà, lève `LLMUnavailable("limite de tours outils atteinte")`.

La réponse finale du modèle doit être un JSON strict `{"answer": "...", "used_chunk_ids": [...]}` — les citations sont validées après coup contre les `chunk_id` réellement retournés par les appels d'outils du run (`runtime.returned_chunk_ids`), pas simplement recopiées depuis ce que dit le modèle.

**Limite connue** : si la réponse finale du modèle n'est pas ce JSON strict (par exemple s'il répond en langage naturel qu'il ne peut pas exécuter une demande), `_final_answer` lève `LLMUnavailable("réponse finale modèle malformée")`, remontée comme une erreur technique (502) plutôt que comme une réponse affichée à l'utilisateur. À vérifier / durcir avant le checkpoint.

---

## 3. Fonctions du pipeline (non exposées au modèle)

Appelées par l'application, jamais par le LLM.

| Fonction | Signature réelle | Effet de bord |
|---|---|---|
| Ingestion | `ingest_corpus(files: list[tuple[str, str]], corpus_id: str \| None = None) -> dict` | Oui — persiste documents, passages, corpus |
| Analyse | `analyze_chunk(text: str, threshold: float) -> InjectionVerdict` | Non — classification déterministe (signaux + regex) |
| Quarantaine | `quarantine_chunk(conn, chunk_id: str, verdict: InjectionVerdict) -> QuarantineRecord` | Oui — marque le passage comme exclu |
| Rapport de sécurité | `document_report(document_id: str) -> DocumentReport \| None` | Non — agrégats pour l'utilisateur |
| Résumé sécurité | `security_summary(corpus_id: str) -> dict \| None` | Non — état injecté dans le contexte de l'agent |
| Aperçu humain | `document_preview(document_id: str) -> list[dict] \| None` | Non — texte complet, jamais renvoyé au modèle |
| Résolution des noms | `resolve_source_names(chunk_ids: list[str]) -> dict[str, str]` | Non — appliqué après génération, jamais avant |

**Pourquoi cette séparation** : si le modèle pouvait appeler `quarantine_chunk` ou lire `document_preview`, la frontière de sécurité se trouverait à l'intérieur du composant probabiliste. Un outil d'agent est une action dont le LLM décide ; une fonction de pipeline est une action que l'architecture impose. La quarantaine et l'affichage humain appartiennent à la seconde catégorie.
