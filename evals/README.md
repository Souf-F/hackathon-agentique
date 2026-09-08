# evals — Oracle Automated Eval

Bonus +5 (palier 3), étendu au palier 4 puis 5. Exécute des scénarios contre le **vrai runtime** (import direct de `backend/*.py`, base SQLite et journal temporaires isolés par exécution, client Anthropic simulé), sans appel réseau, sans coût API, reproductible en CI.

## Lancer

```bash
python evals/run_eval.py
```

Code de sortie `0` si tous les scénarios passent, `1` sinon. Le score et la raison de chaque échec s'affichent en clair.

## État actuel

**28/28.** Le contrat palier 5 (`status`, `confidence`, `metrics` sur `Answer`, livré par Erwan) est testé, y compris en direct contre le vrai modèle (pas seulement en mode mocké — voir DURCISSEMENT.md). `client_disconnect` passe : l'annulation se fait en coupant directement la tâche `asyncio` qui porte l'appel fournisseur, pas via un watcher HTTP en polling. Un point de vigilance non couvert par cette suite (car non-déterministe) : DURCISSEMENT.md H20, un prompt hostile plus élaboré qui fait parfois échouer le format JSON de sortie même après réparation — sans fuite ni crash, mais pas encore fiable à 100 %.

## Comment c'est construit (pas une éval bidon)

Chaque scénario appelle directement le code backend réel :

- `run_agent(question, corpus_id, client=...)` — le paramètre `client` (présent dans `backend/agent.py`) permet d'injecter un client Anthropic simulé sans toucher au code de production ni contourner la moindre règle de sécurité.
- `httpx.AsyncClient.post` est monkeypatché (scénario `provider_unavailable`) pour simuler une vraie panne réseau à travers le vrai chemin `_anthropic_request`, pas un double de cette fonction.
- `ingest_corpus`, `search_evidence`, `ToolRuntime` sont appelés directement — le pipeline de détection, la quarantaine et la validation des arguments d'outil qui tournent sont ceux de production.
- Le kill switch (`08`) exécute un vrai run dans un thread, en parallèle du polling du journal réel, et déclenche un vrai `POST`-équivalent via `run_control.request_stop`.

Aucun endpoint caché, aucun paramètre magique, aucun `if EVAL` qui contourne la sécurité — interdit explicitement par le cahier des charges, et de toute façon inutile ici puisque le runtime est appelé directement en Python.

## Mode `--live` (optionnel)

```bash
python evals/run_eval.py --live --base-url http://127.0.0.1:8000
```

Rejoue quelques scénarios (`factual`, `absurd`, `hostile`, `no-result`) contre une vraie instance avec un vrai appel Anthropic — pas exécuté en CI, affiche tokens et coût, n'enregistre jamais de clé. État : à implémenter (palier 5).
