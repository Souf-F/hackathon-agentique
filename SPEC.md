# SPEC — La Taupe

## Le problème (5 lignes)

Un utilisateur dépose un corpus de documents hétérogène pour que l'agent réponde à ses questions dessus. Certains documents contiennent, dissimulées dans leur texte, des instructions adressées à l'agent lui-même (prompt injection) plutôt qu'à l'utilisateur. Si l'agent les exécute, il peut dévier de la tâche, halluciner, ou exfiltrer des informations sensibles (system prompt, contenu d'autres documents). L'agent doit répondre correctement à la question posée en ne se fondant que sur le contenu légitime, et signaler explicitement quels documents ont tenté de le manipuler, avec l'extrait qui a déclenché la détection. La confiance du système repose sur une frontière stricte entre donnée (le contenu des documents) et instruction (le system prompt) — jamais sur une liste de mots interdits.

## User stories (max 3)

1. **En tant qu'utilisateur**, je dépose un corpus de documents mixte et pose une question ; je reçois une réponse correcte citant ses sources, même si un document du corpus contient une instruction cachée qui tente de détourner la réponse.
2. **En tant qu'utilisateur**, je consulte un rapport listant chaque document mis en quarantaine, la technique d'injection détectée et l'extrait déclencheur, pour comprendre pourquoi il a été exclu.
3. **En tant qu'utilisateur**, je dépose un document parfaitement légitime qui mentionne un mot ou une tournure sensible dans une phrase normale (ex. « ignorez le paragraphe 3 du contrat précédent ») et je vérifie qu'il n'est pas mis en quarantaine à tort.

## Hors scope

1. Multi-tour conversation / mémoire persistante entre sessions — le MVP traite une session = un corpus + une question, pas un historique de conversation.
2. Authentification et multi-utilisateurs avec corpus séparés et droits d'accès — un seul corpus par session, pas de gestion de comptes.
3. **Détection d'injections dans des canaux binaires (texte blanc/caché dans un PDF, stéganographie image, macros Office, JavaScript embarqué)** — on se limite au texte brut extrait (txt/md/pdf texte simple). C'est probablement le plus gros vecteur d'attaque réel du sujet, et on choisit sciemment de ne pas le couvrir plutôt que de le traiter à moitié.
4. Corpus évolutif / ré-indexation incrémentale après le dépôt initial — le corpus est figé au moment du dépôt, pas d'ajout de documents à chaud pour le MVP.
5. Défense contre un attaquant qui parle directement à l'API en dehors du corpus (injection via l'endpoint de requête plutôt que via un document) — le modèle de menace du MVP se concentre sur le canal « document », pas sur un attaquant qui contrôle la requête elle-même.
6. Fine-tuning ou garde-fous ré-entraînés spécifiquement sur nos données — on s'appuie sur le prompting structuré (séparation données/instructions) et une passe de détection, pas sur un modèle ré-entraîné.
7. Couverture multilingue exhaustive de la détection — on optimise pour du français et de l'anglais, pas une couverture universelle.

**Carte bonus « non argumenté »** : item 3 (canaux binaires / texte caché) — c'est celui qu'on écarte alors qu'il paraît évident à couvrir, précisément parce qu'on sait qu'il existe et qu'on choisit le triage plutôt que la couverture à moitié en 5 jours.

## Happy path de la démo finale (6 étapes)

1. L'utilisateur dépose un corpus de 5 documents (4 légitimes + 1 piégé) via l'interface.
2. Le système ingère, chunk et indexe le corpus, puis lance la passe de détection sur chaque document.
3. Le document piégé est détecté, mis en quarantaine, et l'événement est journalisé avec l'extrait déclencheur et la technique identifiée.
4. L'utilisateur pose une question dont la réponse dépend du contenu des documents légitimes.
5. L'agent répond correctement, cite ses sources parmi les documents non quarantainés, et n'exécute aucune instruction issue du document piégé.
6. L'utilisateur consulte le rapport de quarantaine : document suspect, technique détectée, extrait déclencheur.

## Répartition du travail

*(à ajuster avec Erwan selon vos préférences réelles — squelette de départ, sachant que le checkpoint alterne qui répond)*

- **Personne A** : pipeline d'ingestion (chunking, embeddings, retrieval), passe de détection d'injection, prompting structuré donnée/instruction.
- **Personne B** : quarantaine, journalisation, citation des sources, API + interface (dépôt de corpus, question, rapport de quarantaine).
- Les deux : MENACES.md, revue croisée de chaque outil avant checkpoint (l'un explique le code de l'autre), démo scriptée.
