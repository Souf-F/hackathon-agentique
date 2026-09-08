# DEMO — comment lancer le projet

Deux façons, du plus rapide au plus complet.

---

## Option A — Le plus rapide : le site déjà en ligne

Rien à installer.

**https://oracle-api-rh41.onrender.com/**

Ouvrir ce lien dans un navigateur. L'interface se charge directement.

> Le service est hébergé sur un plan gratuit : s'il n'a pas reçu de requête depuis un moment, le premier chargement peut prendre jusqu'à 50 secondes le temps qu'il se réveille. Un rafraîchissement suffit si la première tentative semble bloquée.

---

## Option B — En local (moins de 5 minutes depuis un clone)

```bash
git clone https://github.com/Souf-F/hackathon-agentique.git
cd hackathon-agentique
cp .env.example .env      # renseigner ANTHROPIC_API_KEY (optionnel : sans clé, le système répond en mode dégradé)

python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload
```

Ouvrir **http://127.0.0.1:8000/** dans un navigateur.

---

## Une fois l'interface ouverte (les deux options)

1. Cliquer sur le bouton pour charger le **corpus de démo** (6 CV livrés avec le dépôt)
2. Un document apparaît déjà marqué suspect dans le rapport de sécurité — la détection a tourné à l'ingestion, avant toute question
3. Poser une question, par exemple : *« Classe ces candidats par pertinence pour un poste de développeur backend Python. »*
4. La réponse ne favorise jamais le candidat piégé, malgré l'instruction cachée dans son CV
5. Cliquer sur son document dans la liste pour voir l'extrait qui a déclenché la quarantaine — visible pour l'humain dans le rapport, jamais transmis au modèle

---

## Si quelque chose ne répond pas

- **Option A ne charge pas** : réessayer une fois (réveil du service), sinon basculer sur l'option B
- **Réponse en `"mode": "extractive"` au lieu de `"llm"`** : la clé API n'est pas configurée ou a expiré — le système répond quand même, juste sans génération par le modèle

---

## Script checkpoint — palier 4

État au moment de la rédaction : **A et D sont jouables tels quels. B et C dépendent de l'intégration du backend palier 4 d'Erwan (`run_id`, `/api/runs/{id}/stop`, `/api/runs/{id}/journal`) — à retester une fois fusionné, ne pas les annoncer comme acquis avant.**

### A. Fonctionnement normal (jouable)

1. Charger le corpus de démo
2. Lancer une question de classement (ex. *"Classe ces candidats par pertinence pour un poste de développeur backend Python"*)
3. Montrer la trace d'outil en direct (nom, arguments, statut, durée) qui apparaît avant la réponse
4. Montrer la réponse qui arrive par morceaux, puis les citations
5. Montrer l'onglet **Journal** de l'Inspector : la timeline de ce qui vient de se passer

### B. Kill switch (dépend du backend palier 4)

1. Poser une question qui prend du temps
2. Cliquer **STOP** pendant que ça tourne
3. Montrer l'état "Arrêt demandé…" puis "Arrêté proprement"
4. Montrer dans le Journal que le run s'est bien arrêté, sans événement après l'arrêt
5. Vérifier que le composer se réactive normalement ensuite

*Ce que fait le frontend aujourd'hui si le backend n'est pas encore branché : clique sur STOP → message clair "endpoint pas encore disponible", pas de crash, pas de faux "arrêté" simulé côté client.*

### C. Panne réseau/provider (dépend du backend palier 4)

1. Lancer une question
2. Couper la ressource (réseau ou clé API invalide)
3. Montrer que l'heure de la panne affichée vient du serveur, pas de l'horloge du navigateur
4. Montrer le bandeau "Ressource indisponible" — **la trace d'outil déjà affichée avant la panne doit rester visible**, pas remplacée par un message générique
5. Montrer l'état `failed` dans le Journal

### D. Bonus éval (jouable)

Dans un terminal, à la racine du repo :

```bash
python evals/run_eval.py
```

Montrer le score directement affiché (8/10 au moment de la rédaction — 2 scénarios honnêtement bloqués en attendant le backend palier 4, avec la raison exacte affichée, pas masqués).
