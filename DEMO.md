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
