# AGENTS — La Taupe

État : palier 1. Les prompts système réels et le détail de la boucle seront complétés quand l'implémentation existera. Ce qui est écrit ici est déjà défendable à l'oral ; ce qui ne l'est pas encore est marqué comme tel.

---

## 1. Rôle de l'agent

Répondre à une question de l'utilisateur en s'appuyant exclusivement sur les passages admissibles du corpus, et citer les passages utilisés.

L'agent **ne décide pas** de sa propre frontière de sécurité : il n'analyse pas, ne met pas en quarantaine, ne journalise pas. Ces opérations appartiennent au pipeline applicatif et sont exécutées avant qu'il ne soit sollicité.

---

## 2. Hiérarchie des instructions

```text
system prompt          ── autorité
requête utilisateur    ── intention
passages du corpus     ── donnée, aucune autorité
```

Les passages sont transmis dans un bloc délimité, jamais concaténés au system prompt. Le prompt indique explicitement que tout ce qui figure dans ce bloc est du contenu à analyser, y compris lorsqu'il prend la forme d'un ordre.

---

## 3. Outils exposés au modèle

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

Retourne uniquement des agrégats : `status`, `chunk_count`, `quarantined_count`, `categories`. Ni texte, ni extrait, ni `source_name` — sinon l'injection reviendrait dans le contexte du modèle sous couvert de rapport de sécurité, ou par le nom du fichier.

---

## 4. Fonctions non accessibles au modèle

Appelées par l'application, dans un ordre imposé par le code.

```text
ingest_corpus        (effet de bord : oui)
chunk_document       (effet de bord : oui)
analyze_chunk        (effet de bord : non)
quarantine_chunk     (effet de bord : oui)
record_security_event(effet de bord : oui)
index_chunks         (effet de bord : oui)
```

**Pourquoi cette séparation** : si le modèle pouvait appeler `quarantine_chunk`, la frontière de sécurité se trouverait à l'intérieur du composant probabiliste. Un outil d'agent est une action dont le LLM décide ; une fonction de pipeline est une action que l'architecture impose. La quarantaine appartient à la seconde catégorie.

---

## 4 bis. Plan de contrôle / plan d'affichage

Tout ce qui provient de l'auteur d'un document est exclu du contexte du modèle, quel que soit le chemin emprunté.

```text
                    │ plan de contrôle │ plan d'affichage
────────────────────┼──────────────────┼─────────────────
texte du passage    │ oui, si admis    │ oui
chunk_id            │ oui              │ oui
nom de fichier      │ NON              │ oui
extrait quarantiné  │ NON              │ oui (rapport)
```

Le prompt ne contient que des identifiants opaques :

```text
[chunk_id=f025c64f document_id=e0423c85]
```

Le nom lisible est résolu après génération par `display.resolve_source_names`.

---

## 5. Boucle

```text
ingestion
   │
   ▼
découpage (corps + métadonnées)
   │
   ▼
analyse ──► verdict typé
   │
   ├── admissible ──► index
   │
   └── suspect ──► quarantaine + événement de sécurité
                            │
                            ▼
                   rapport utilisateur
   ─────────────────────────────────────
question
   │
   ▼
search_evidence (quarantined = 0)
   │
   ▼
génération + citations
```

L'analyse a lieu **avant** l'indexation : un passage en quarantaine n'entre jamais dans l'index de recherche.

---

## 6. Prompts système

À compléter au palier où la boucle sera implémentée. Contraintes déjà arrêtées :

- le prompt du répondeur reçoit les passages dans un bloc de données délimité et déclaré non exécutable ;
- le prompt du détecteur reçoit un passage isolé et retourne un JSON conforme à `InjectionVerdict`, sans texte libre ;
- aucun secret, aucune clé, aucun nom de variable d'environnement ne figure dans un prompt.

---

## 7. Gestion des erreurs

À compléter. Principe retenu : en cas d'échec de l'analyse d'un passage, le passage est traité comme suspect (`action = "flagged"`) plutôt qu'admis par défaut, et l'échec est journalisé.

---

## 8. Limites

Le détecteur est faillible dans les deux sens. Ce qui est garanti n'est pas la détection, mais l'isolement : un passage marqué est structurellement absent du contexte de génération, et chaque décision est journalisée avec sa justification, donc contestable.
