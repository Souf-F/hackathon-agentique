# OUTILS — La Taupe

Liste des outils et fonctions de l'agent, chacun avec nom, signature typée et effet de bord. Référencé depuis AGENTS.md.

État : palier 6 (livraison finale). Sections 1-3 à jour avec le code réel (`backend/tool_runtime.py`, `backend/agent.py`), inchangé. Section 4 : `stop_run` (palier 4) est livré et vérifié en direct. Palier 5 : aucun nouvel outil LLM — seulement des champs supplémentaires (`status`, `confidence`, `metrics`) sur la réponse existante, livrés et vérifiés en direct contre le vrai modèle — voir AGENTS.md section 9 et DURCISSEMENT.md. Section 5 (nouvelle) : les fonctions de mesure du palier 5 (`backend/metrics.py`, `grounding_confidence` dans `backend/models.py`).

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

La réponse finale du modèle doit être un JSON strict `{"status": "answered | insufficient_evidence | out_of_scope | refused", "answer": "...", "used_chunk_ids": [...]}` (palier 5 — le champ `status` s'ajoute au contrat du palier 3 ; `out_of_scope` ajouté au palier 6, cf. JOURNAL.md entrée 12) — les citations sont validées après coup contre les `chunk_id` réellement retournés par les appels d'outils du run (`runtime.returned_chunk_ids`), pas simplement recopiées depuis ce que dit le modèle. Le `status` lui-même n'est pas non plus pris tel quel : voir `grounding_confidence` en section 5, et AGENTS.md section 9 pour le garde-fou qui peut le rétrograder. `out_of_scope` se décide en général sans appeler l'outil (question hors du domaine du corpus), `insufficient_evidence` implique d'avoir cherché sans rien trouver d'admissible sur une question qui, elle, relève du corpus.

**Boucle de réparation** : si la réponse finale du modèle n'est pas ce JSON strict (par exemple s'il répond en langage naturel), `backend/agent.py` retente **une fois** avec `REPAIR_INSTRUCTION` (demande explicite de reformuler en JSON strict, sans changer les faits). Si cette réparation échoue aussi, `_ground_final_answer` lève `LLMUnavailable("réponse finale modèle malformée")`, remontée comme une erreur technique (HTTP 502) — propre (pas de fuite, pas de stack trace) mais pas un `status="refused"` explicite. **Limite connue, testée en direct** : sur un prompt hostile élaboré, ça arrive environ une fois sur cinq observée malgré la réparation — cf. DURCISSEMENT.md H20.

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

---

## 4. Palier 4 — `stop_run` n'est pas un tool LLM

Ne pas mélanger deux catégories différentes :

- **`search_evidence`** : un tool au sens Anthropic — défini dans `tools=[...]`, le modèle voit sa description, décide de l'appeler ou non, avec quels arguments. C'est le seul de cette catégorie.
- **`stop_run`** : une **opération de contrôle opérateur**, exposée comme endpoint API classique (`POST /api/runs/{run_id}/stop`), jamais présentée au modèle, jamais dans une liste `tools`. Le modèle ignore totalement son existence ; il ne peut ni l'appeler, ni la refuser, ni la contourner, puisqu'il n'y a même pas accès.

| | `search_evidence` | `stop_run` |
|---|---|---|
| Qui décide de l'appeler | Le modèle (`tool_choice: auto`) | L'utilisateur, via l'interface |
| Visible du modèle | Oui (schéma dans `tools`) | Non |
| Effet de bord | Non (lecture seule) | Oui (change l'état du run) |

**État** : `backend/run_control.py` et l'endpoint `POST /api/runs/{run_id}/stop` sont livrés et vérifiés en direct (cycle `running → stop_requested → stopped`, aucun appel d'outil après la demande d'arrêt). Le frontend garde sa gestion défensive de l'endpoint absent — utile si un déploiement tourne temporairement sur un backend plus ancien, pas parce que l'endpoint manquerait aujourd'hui.

---

## 5. Fonctions de mesure — palier 5 (non exposées au modèle)

Ni des outils LLM, ni du pipeline d'ingestion : des fonctions pures qui transforment ce que le fournisseur (Anthropic) et la boucle agentique produisent déjà en `metrics`/`confidence` honnêtes, jamais recalculées après coup à partir de suppositions.

| Fonction / classe | Signature réelle | Effet de bord |
|---|---|---|
| Accumulateur d'usage | `UsageAccumulator(model: str \| None = None)` — méthodes `.record(response: dict) -> None`, `.set_tool_calls(n: int) -> None`, `.snapshot() -> dict` (`backend/metrics.py`) | Non — additionne en mémoire l'usage de chaque réponse fournisseur du run ; si un `response` n'a pas de bloc `usage` (client mocké), les compteurs passent à `None` et `usage_available=False` plutôt qu'un faux zéro |
| Tarif par modèle | `pricing_for(model: str \| None) -> tuple[Decimal, Decimal] \| None` (`backend/metrics.py`) | Non — `None` explicite si le modèle n'est pas dans `PRICING`, jamais un tarif appliqué par défaut à un modèle inconnu |
| Métrique indisponible | `unavailable(model: str \| None = None) -> dict` (`backend/metrics.py`) | Non — même forme que `.snapshot()`, utilisée quand aucun run réel n'a eu lieu (ex. réponse extractive) |
| Confiance de grounding | `grounding_confidence(status: str, n_refs: int, n_docs: int, had_tool_error: bool) -> dict` (`backend/models.py`) | Non — déterministe à partir du nombre de passages/documents réellement cités, jamais une auto-évaluation du modèle. `refused` → `n/a` ; 0 citation → `none` ; erreur d'outil en cours de route → `low` ; ≥2 documents distincts → `high` ; sinon `medium` |

**Pourquoi ce n'est pas dans la boucle du modèle** : la confiance et le coût affichés à l'utilisateur doivent rester vrais même si le modèle ment ou s'auto-évalue mal — donc ils sont recalculés côté application à partir de faits vérifiables (citations réellement retournées par les outils, tokens réellement facturés par le fournisseur), jamais lus depuis ce que le modèle prétend.
