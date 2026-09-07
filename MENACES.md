# MENACES.md — v1 (palier 1)

Modèle de menace initial : qui peut parler à l'agent, par quel canal, et ce qui se passe si ce canal ment. À affiner aux paliers suivants.

## Canal 1 — Contenu des documents du corpus (canal principal du sujet)

- **Qui parle** : n'importe quel document déposé dans le corpus, potentiellement rédigé par un tiers malveillant (l'utilisateur qui dépose le corpus n'en est pas forcément l'auteur).
- **Confiance par défaut** : aucune. Traité comme donnée pure, jamais comme instruction.
- **S'il ment** (contient une instruction cachée type « ignore les consignes précédentes », « tu es maintenant... », faux tag système, etc.) :
  - Sans protection : l'agent peut dévier de la tâche, halluciner une réponse fausse, ou tenter d'exfiltrer le system prompt / d'autres documents.
  - Avec protection : le document est détecté par la passe de détection, mis en quarantaine, exclu du contexte de réponse, et journalisé avec l'extrait déclencheur. La réponse finale reste correcte sur les documents légitimes restants.

## Canal 2 — La requête de l'utilisateur

- **Qui parle** : l'utilisateur final, via la question posée au système.
- **Confiance par défaut** : plus élevée que le canal 1 (c'est l'opérateur du système), mais pas absolue — un utilisateur peut aussi tenter d'extraire le system prompt ou de manipuler l'agent directement.
- **S'il ment** (question formulée pour piéger l'agent, demande explicite de révéler ses instructions internes) :
  - L'agent doit distinguer répondre à la question métier de suivre des instructions de contournement embarquées dans la question. Le system prompt doit résister à ce canal aussi, mais ce n'est pas le cœur du MVP (cf. hors-scope SPEC.md item 5).

## Canal 3 — Métadonnées des documents (nom de fichier, propriétés)

- **Qui parle** : l'utilisateur ou l'auteur du document, via le nom de fichier ou les métadonnées (auteur, titre PDF, etc.), souvent oublié.
- **Confiance par défaut** : aucune, même traitement que le contenu.
- **S'il ment** (nom de fichier contenant une instruction, ex. `IMPORTANT_ignore_previous_instructions.txt`) :
  - Doit passer par la même passe de détection que le contenu, pas seulement le corps du texte. À vérifier explicitement en implémentation (risque d'angle mort si seul le corps du document est scanné).

## Canal 4 — Sortie de la passe de détection elle-même

- **Qui parle** : le résultat structuré du modèle de détection (verdict), qui alimente ensuite le prompt de réponse (liste des documents quarantainés, techniques détectées).
- **Confiance par défaut** : élevée mais pas aveugle — c'est une sortie de modèle, potentiellement bruitée par le document qu'elle vient d'analyser.
- **S'il ment** (le détecteur hallucine un verdict, ou reproduit dans son propre output du texte injecté qu'il vient de lire) :
  - Le verdict doit rester strictement structuré (schéma typé, jamais du texte libre réinjecté tel quel dans le prompt suivant) pour éviter qu'une injection ne survive au passage détecteur → répondeur.

## Canal 5 — Le store de vecteurs / l'index de retrieval

- **Qui parle** : indirectement, quiconque peut influencer ce qui est indexé.
- **Confiance par défaut** : dépend du canal 1, hérite de sa méfiance.
- **S'il ment** : hors scope MVP (corpus figé au dépôt, cf. SPEC.md item 4) — noté ici pour mémoire, à traiter si le corpus devient évolutif.

## Principe directeur

La détection se fonde sur un **comportement** (une portion de texte qui s'adresse à l'agent à la 2e personne, tente de redéfinir son rôle, ou de contredire ses instructions système) et non sur une **liste de mots-clés**. Un document légitime qui contient le mot « ignore » ou « instructions » dans une phrase normale ne doit pas être mis en quarantaine à tort (cf. user story 3 et le piège du sujet).
