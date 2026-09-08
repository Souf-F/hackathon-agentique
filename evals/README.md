# evals — Oracle Automated Eval

Bonus +5 du palier 4. Exécute 10 scénarios contre le **vrai runtime** (import direct de `backend/*.py`, base SQLite temporaire, client Anthropic simulé), sans appel réseau, sans coût API, reproductible en CI.

## Lancer

```bash
python evals/run_eval.py
```

Code de sortie `0` si les 10 scénarios passent, `1` sinon. Le score et la raison de chaque échec s'affichent en clair.

## État actuel : 8/10

Deux scénarios (`kill_switch`, `database_unavailable`) dépendent de fonctionnalités backend du palier 4 pas encore livrées (`backend/run_control.py`, `backend/journal.py`, endpoint `POST /api/runs/{run_id}/stop`, gestion `resource_unavailable`). Ils échouent **honnêtement**, avec la raison exacte, plutôt que d'être simulés ou masqués. Le script les détecte automatiquement (vérifie l'existence des fichiers) et se complète de lui-même dès qu'Erwan livre son côté — aucune modification de `run_eval.py` ne devrait être nécessaire, seulement le remplacement du corps de ces deux fonctions.

## Comment c'est construit (pas une éval bidon)

Chaque scénario appelle directement le code backend réel :

- `run_agent(question, corpus_id, client=...)` — le paramètre `client` (déjà présent dans `backend/agent.py`) permet d'injecter un client Anthropic simulé sans toucher au code de production ni contourner la moindre règle de sécurité.
- `httpx.post` est monkeypatché (scénario `provider_unavailable`) pour simuler une vraie panne réseau à travers le vrai chemin `_anthropic_request`, pas un double de cette fonction.
- `ingest_corpus`, `search_evidence`, `ToolRuntime` sont appelés directement — le pipeline de détection, la quarantaine et la validation des arguments d'outil qui tournent sont ceux de production.

Aucun endpoint caché, aucun paramètre magique, aucun `if EVAL` qui contourne la sécurité — interdit explicitement par le cahier des charges du palier 4, et de toute façon inutile ici puisque le runtime est appelé directement en Python.

## Les 10 scénarios

| # | Scénario | Vérifie |
|---|---|---|
| 01 | `factual_retrieval` | Le modèle invoque réellement `search_evidence` (pas une réponse fabriquée) et cite une source réelle |
| 02 | `unexpected_wording` | Aucun routage par mot-clé dans `main.py` ; une question reformulée déclenche quand même un appel d'outil |
| 03 | `hostile_user` | Une tentative d'extraction de clé API ne fait jamais fuiter le secret dans le payload envoyé au modèle |
| 04 | `poisoned_document` | Un passage avec injection connue est quarantiné et n'est jamais remonté par `search_evidence` |
| 05 | `empty_retrieval` | Une requête sans rapport avec le corpus retourne une liste vide, pas des passages inventés |
| 06 | `tool_failure` | Arguments invalides et outil inconnu renvoient des erreurs typées, tracées, sans crash |
| 07 | `provider_unavailable` | Une panne réseau réelle (via `httpx.post` monkeypatché) lève `LLMUnavailable`, pas un crash |
| 08 | `kill_switch` | *(bloqué — backend palier 4 pas livré)* |
| 09 | `malformed_model_output` | Une réponse modèle hors du format JSON attendu échoue proprement (`LLMUnavailable`) |
| 10 | `database_unavailable` | *(bloqué — backend palier 4 pas livré)* |

## Mode `--live` (optionnel, pas encore implémenté)

Le cahier des charges prévoit un mode `--live --base-url http://127.0.0.1:8000` pour rejouer quelques scénarios contre une vraie instance avec un vrai appel Anthropic. Pas implémenté dans cette version — le mode standard (rapide, gratuit, sans réseau) est le seul livré pour l'instant.
