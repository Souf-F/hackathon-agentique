# JOURNAL — La Taupe

Journal de notre travail avec les outils d'IA. On y note ce qu'on a demandé, ce qui a été proposé, ce qu'on a gardé, ce qu'on a rejeté, et pourquoi.

Minimum requis : 5 entrées.

---

## Entrée 1 — Séparation outils d'agent / fonctions de pipeline

**Date** : 7 septembre 2026 · **Participants** : Erwan, Souf

**Objectif** : cadrer les contrats d'outils avant l'oral du palier 1.

**Ce qui a été proposé** : une première version exposait à l'agent quatre outils, dont `quarantine_chunk` et `record_security_event`, tous deux avec effet de bord.

**Problème identifié en revue** : contradiction avec notre propre argument. Nous affirmions que la sécurité devait être imposée par l'application plutôt que par le comportement du modèle, tout en laissant le modèle décider de la mise en quarantaine. La frontière de sécurité se serait retrouvée à l'intérieur du composant probabiliste.

**Décision** : deux listes distinctes. Outils exposés au modèle, tous en lecture seule (`search_evidence`, `inspect_document`). Fonctions de pipeline appelées par l'application (`analyze_chunk`, `quarantine_chunk`, `record_security_event`, `index_chunks`). L'exclusion d'un passage est un `WHERE quarantined = 0`, pas une consigne.

**Ce qu'on en retient** : un outil d'agent est une action dont le LLM décide ; une fonction de pipeline est une action que l'architecture impose. Nous confondions les deux.

---

## Entrée 2 — Deux fuites trouvées en relisant nos propres fichiers

**Date** : 7 septembre 2026 · **Participants** : Erwan, Souf

**Objectif** : vérifier la cohérence entre SPEC, MENACES et AGENTS avant de traverser la salle, puisqu'un fichier est ouvert au hasard.

**Fuite 1 — `inspect_document`.** MENACES affirmait qu'un passage en quarantaine n'apparaît que dans le rapport de sécurité, mais `inspect_document` était décrit comme exposant « les alertes du document » et il est accessible au modèle. Si `DocumentInspection` contenait l'extrait de l'alerte, le texte hostile revenait dans le prompt par la porte de service, et nos menaces T08 et T09 tombaient. Corrigé : `DocumentInspection` ne contient que des agrégats (statut, compteurs, catégories). Le rapport détaillé est pour l'utilisateur, pas pour l'agent.

**Fuite 2 — granularité.** Une version des documents mettait en quarantaine le document entier (`quarantine(doc_id, ...)`). C'est exactement le piège de l'énoncé inversé : on ne perd plus des documents à cause d'un mot-clé, on les perd à cause d'un paragraphe. Retour à la quarantaine au niveau du passage, le document ne recevant qu'un statut `suspicious`.

**Aussi corrigé** : `SecurityEvent` ne contenait ni `timestamp` ni `action`, alors que MENACES les exigeait ; trois types (`Chunk`, `QuarantineRecord`, `DocumentInspection`) étaient référencés dans des signatures sans être définis.

**Ce qu'on en retient** : nos incohérences ne se voyaient pas fichier par fichier, seulement en lecture croisée. On institue quinze minutes de relecture croisée avant chaque checkpoint, chacun lisant le fichier qu'il n'a pas écrit.

---

## Entrée 3 — À compléter

**Date** : · **Participants** :

**Objectif** :

**Outils IA utilisés** :

**Proposition** :

**Gardé / rejeté, et pourquoi** :

**Ce qu'on en retient** :

---

## Entrée 4 — À compléter

**Date** : · **Participants** :

**Objectif** :

**Outils IA utilisés** :

**Proposition** :

**Gardé / rejeté, et pourquoi** :

**Ce qu'on en retient** :

---

## Entrée 5 — À compléter

**Date** : · **Participants** :

**Objectif** :

**Outils IA utilisés** :

**Proposition** :

**Gardé / rejeté, et pourquoi** :

**Ce qu'on en retient** :
